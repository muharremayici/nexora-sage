from __future__ import annotations

from collections import Counter, deque
from pathlib import Path
from pathlib import PurePosixPath
import re
import time

from tools.core.atlas_io import load_atlas_data
from tools.core.analysis_snapshot_lineage import write_current_atlas_lineage
from tools.core.config import RAW_DIR, REPORTS_DIR, ROOT, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file
from tools.core.logger import logger
from tools.core.merge_review_hardening_policy import merge_dependency_packager_policy, policy_int, policy_string
from tools.core.projects_registry import canonical_project_name, resolve_runtime_projects


MAX_CLOSURE_FILES = 80
PACKAGE_CANDIDATE_LIMIT = 120
DISK_FILE_SCAN_LIMIT = 20000
DISK_FILE_SCAN_PRUNE_DIRS = {
    ".git",
    ".next",
    ".turbo",
    ".vite",
    ".venv",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "output",
}
BROWSER_API_TOKENS = {
    "clipboard": "navigator.clipboard",
    "geolocation": "navigator.geolocation",
    "mediaDevices": "navigator.mediaDevices",
    "Notification": "Notification",
    "localStorage": "localStorage",
    "sessionStorage": "sessionStorage",
    "ResizeObserver": "ResizeObserver",
    "IntersectionObserver": "IntersectionObserver",
    "matchMedia": "window.matchMedia",
}


def _normalize_rel(path: str) -> str:
    parts = []
    for part in PurePosixPath(str(path or "").replace("\\", "/")).parts:
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def _split_scoped_file(scoped_file: str | None) -> tuple[str, str] | None:
    if not scoped_file or "::" not in scoped_file:
        return None
    project, rel_path = scoped_file.split("::", 1)
    return canonical_project_name(project), _normalize_rel(rel_path)


def _is_source_like(path: str) -> bool:
    return _normalize_rel(path).endswith((".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".css", ".json"))


def _is_external_dep(path: str) -> bool:
    normalized = _normalize_rel(path)
    if not normalized:
        return False
    if normalized.startswith(("@", "~")) and not normalized.startswith(("@/", "~/")):
        return True
    return not normalized.startswith(("src/", "app/", "pages/", "components/", "features/", "hooks/", "services/", "lib/", "types/", "contexts/", "entities/", "stores/", "utils/", "styles/"))


def _project_dependencies(atlas: dict, project: str) -> dict[str, list[str]]:
    payload = atlas.get(project, {}) if isinstance(atlas, dict) else {}
    deps = payload.get("dependencies", {}) if isinstance(payload, dict) else {}
    if not isinstance(deps, dict):
        return {}
    return {
        _normalize_rel(path): [_normalize_rel(dep) for dep in values if isinstance(dep, str)]
        for path, values in deps.items()
        if isinstance(values, list)
    }


def _project_known_files(atlas: dict, project: str) -> set[str]:
    payload = atlas.get(project, {}) if isinstance(atlas, dict) else {}
    files = payload.get("files", {}) if isinstance(payload, dict) else {}
    if not isinstance(files, dict):
        return set()
    known: set[str] = set()
    for file_key, info in files.items():
        if not isinstance(info, dict):
            info = {}
        rel = _normalize_rel(str(info.get("atlas_rel_path") or info.get("file") or file_key))
        if rel and _is_source_like(rel):
            known.add(rel)
    return known


def _project_disk_source_files(project_root: Path | None) -> set[str]:
    if not project_root:
        return set()
    root = project_root.resolve()
    known: set[str] = set()
    stack = [root]
    while stack and len(known) < DISK_FILE_SCAN_LIMIT:
        current = stack.pop()
        try:
            for child in current.iterdir():
                if child.name in DISK_FILE_SCAN_PRUNE_DIRS:
                    continue
                if child.is_dir():
                    stack.append(child)
                    continue
                if not child.is_file():
                    continue
                rel = _normalize_rel(child.relative_to(root).as_posix())
                if rel and _is_source_like(rel):
                    known.add(rel)
                    if len(known) >= DISK_FILE_SCAN_LIMIT:
                        break
        except (OSError, ValueError):
            continue
    return known


def _dependency_variants(dep: str) -> list[str]:
    normalized = _normalize_rel(dep)
    variants = [normalized]
    changed = True
    while changed:
        changed = False
        for value in list(variants):
            next_values = []
            if value.startswith("src/app/src/"):
                next_values.append("src/" + value[len("src/app/src/"):])
            if value.startswith("src/src/"):
                next_values.append("src/" + value[len("src/src/"):])
            if "/shared/types/" in value:
                next_values.append(value.replace("/shared/types/", "/types/"))
            if value.startswith("@/"):
                next_values.append("src/" + value[2:])
            if value.startswith("~/"):
                next_values.append("src/" + value[2:])
            for next_value in next_values:
                if next_value not in variants:
                    variants.append(next_value)
                    changed = True
    return variants


def _resolve_dependency(dep: str, known_graph_files: set[str], known_disk_files: set[str]) -> tuple[str | None, bool]:
    for variant in _dependency_variants(dep):
        if variant in known_graph_files:
            return variant, True
        if variant in known_disk_files:
            return variant, False
    return None, False


def _read_closure_text(project_root: Path | None, files: list[str], max_files: int = 18) -> str:
    if not project_root:
        return ""
    chunks: list[str] = []
    for rel_path in files[:max_files]:
        path = (project_root / rel_path).resolve()
        try:
            if path.exists() and path.is_file() and path.is_relative_to(project_root.resolve()):
                chunks.append(path.read_text(encoding="utf-8", errors="replace")[:24000])
        except (OSError, ValueError):
            continue
    return "\n".join(chunks)


def _browser_api_mocks(content: str) -> list[dict[str, str]]:
    mocks = []
    for token, api_name in BROWSER_API_TOKENS.items():
        if token in content:
            mocks.append({"api": api_name, "mock": f"provide_{token.lower()}_mock"})
    return mocks


def _harness_plan(candidate: dict, files: list[str], project_root: Path | None) -> dict:
    closure_plan = candidate.get("dependency_closure_plan") or {}
    smoke_plan = candidate.get("smoke_plan") or {}
    providers = list(closure_plan.get("providers") or [])
    context_hooks = list(closure_plan.get("context_hooks") or [])
    store_hooks = list(closure_plan.get("store_hooks") or [])
    service_imports = list(closure_plan.get("service_imports") or [])
    i18n_keys = list(closure_plan.get("i18n_keys") or [])
    missing_i18n_keys = list(closure_plan.get("missing_i18n_keys") or [])
    css_variables = list(closure_plan.get("css_variables") or [])
    css_imports = list(closure_plan.get("css_imports") or [])
    content = _read_closure_text(project_root, files)

    provider_tree = ["AppShell"]
    provider_tree.extend(providers)
    if context_hooks and not providers:
        provider_tree.append("ContextHarness")
    if service_imports:
        provider_tree.append("ServicePortMocks")

    router_mocks = {
        "route": smoke_plan.get("suggested_route"),
        "route_template": smoke_plan.get("route_template"),
        "framework": smoke_plan.get("route_framework"),
        "params_required": "__" in str(smoke_plan.get("suggested_route") or ""),
    }
    store_shape = {
        "hooks": store_hooks,
        "context_hooks": context_hooks,
        "service_ports": service_imports,
        "seed_minimal_project_fixture": "seed_minimal_project_fixture" in list(smoke_plan.get("setup") or []),
    }
    return {
        "provider_tree": provider_tree,
        "store_shape": store_shape,
        "router_mocks": router_mocks,
        "i18n_keys": {
            "required": i18n_keys,
            "missing": missing_i18n_keys,
            "assert_no_raw_keys": bool(i18n_keys or missing_i18n_keys),
        },
        "style_tokens": {
            "css_variables": css_variables,
            "css_imports": css_imports,
        },
        "browser_api_mocks": _browser_api_mocks(content),
        "smoke_assertions": list(smoke_plan.get("assertions") or []),
    }


def build_dependency_package(
    candidate: dict,
    atlas: dict,
    max_files: int = MAX_CLOSURE_FILES,
    project_root: Path | None = None,
    known_project_files: set[str] | None = None,
) -> dict:
    scoped = _split_scoped_file(candidate.get("source_contract_file"))
    name = candidate.get("name")
    source = canonical_project_name(str(candidate.get("source") or ""))
    if scoped:
        project, entry_file = scoped
    else:
        project, entry_file = source, ""

    dependencies = _project_dependencies(atlas, project)
    known_graph_files = set(dependencies)
    known_project_files = set(known_project_files or _project_known_files(atlas, project))
    known_project_files.update(_project_disk_source_files(project_root))
    files: list[str] = []
    external_deps: set[str] = set()
    unresolved_deps: set[str] = set()
    resolved_file_only_deps: set[str] = set()
    truncated = False

    if entry_file:
        queue = deque([entry_file])
        seen = set()
        while queue and len(files) < max_files:
            current = queue.popleft()
            if current in seen:
                continue
            seen.add(current)
            files.append(current)
            for dep in dependencies.get(current, []):
                if _is_external_dep(dep):
                    external_deps.add(dep)
                    continue
                resolved_dep, has_graph = _resolve_dependency(dep, known_graph_files, known_project_files)
                if resolved_dep and resolved_dep in seen:
                    continue
                if resolved_dep and has_graph:
                    queue.append(resolved_dep)
                elif resolved_dep:
                    queue.append(resolved_dep)
                    resolved_file_only_deps.add(resolved_dep)
                elif _is_source_like(dep):
                    unresolved_deps.add(dep)
        truncated = bool(queue)

    closure_size = len(files)
    risk_points = int(candidate.get("risk_points", 0) or 0)
    if truncated:
        risk_points += 12
    if unresolved_deps:
        risk_points += min(18, len(unresolved_deps) * 3)
    if external_deps:
        risk_points += min(10, len(external_deps))

    policy = merge_dependency_packager_policy()
    dynamic_import_policy = policy.get("dynamic_import", {}) if isinstance(policy.get("dynamic_import"), dict) else {}
    has_dynamic_imports = False
    if project_root and files:
        dynamic_import_pattern = policy_string(dynamic_import_policy, "pattern")
        dynamic_import_re = re.compile(dynamic_import_pattern) if dynamic_import_pattern else None
        scan_limit = max(0, policy_int(dynamic_import_policy, "scan_file_limit"))
        scan_bytes = max(0, policy_int(dynamic_import_policy, "scan_bytes"))
        for rel in files[:scan_limit]:
            try:
                abs_path = (project_root / rel).resolve()
                if dynamic_import_re and abs_path.exists() and abs_path.is_file():
                    sample = abs_path.read_text(encoding="utf-8", errors="ignore")[:scan_bytes]
                    if dynamic_import_re.search(sample):
                        has_dynamic_imports = True
                        break
            except (OSError, ValueError):
                continue
    if has_dynamic_imports:
        risk_points += policy_int(dynamic_import_policy, "risk_points")

    dynamic_route_policy = policy.get("dynamic_route", {}) if isinstance(policy.get("dynamic_route"), dict) else {}
    smoke_route = str((candidate.get("smoke_plan") or {}).get("suggested_route") or "")
    dynamic_route_pattern = policy_string(dynamic_route_policy, "pattern")
    has_dynamic_route = bool(dynamic_route_pattern and re.search(dynamic_route_pattern, smoke_route))
    if has_dynamic_route:
        risk_points += policy_int(dynamic_route_policy, "risk_points")

    if risk_points >= 70 or truncated:
        package_tier = "manual_package_review"
    elif risk_points >= 45 or unresolved_deps:
        package_tier = "assisted_package"
    else:
        package_tier = "direct_package"

    harness_plan = _harness_plan(candidate, files, project_root)

    return {
        "candidate": name,
        "source": source,
        "studio": candidate.get("studio"),
        "target_path": _normalize_rel(str(candidate.get("target_path") or "")),
        "source_contract_file": candidate.get("source_contract_file"),
        "entry_file": entry_file,
        "recommended_gate": candidate.get("recommended_gate"),
        "candidate_risk_tier": candidate.get("risk_tier"),
        "package_tier": package_tier,
        "closure_size": closure_size,
        "closure_truncated": truncated,
        "files": files,
        "external_deps": sorted(external_deps),
        "unresolved_internal_deps": sorted(unresolved_deps),
        "resolved_file_only_deps": sorted(resolved_file_only_deps),
        "required_contracts": (candidate.get("dependency_closure_plan") or {}).get("required_contracts", []),
        "smoke_route": (candidate.get("smoke_plan") or {}).get("suggested_route"),
        "harness_plan": harness_plan,
        "transfer_plan": {
            "copy_from_project": source,
            "copy_files": files,
            "rewrite_imports": bool(files),
            "run_static_gate": True,
            "run_browser_smoke": candidate.get("recommended_gate") == "browser_smoke_required",
            "harness_required": bool(
                harness_plan["provider_tree"]
                or harness_plan["i18n_keys"]["required"]
                or harness_plan["store_shape"]["service_ports"]
                or harness_plan["browser_api_mocks"]
            ),
        },
    }


def run_merge_dependency_packager() -> dict:
    started = time.perf_counter()
    logger.info("Building merge dependency packages from Atlas and UI runtime contracts...")
    atlas = load_atlas_data()
    atlas_loaded_at = time.perf_counter()
    projects = resolve_runtime_projects(ROOT)
    ui_runtime = load_json_file(RAW_DIR / "ui_runtime_contracts.json", {})
    candidates = ui_runtime.get("merge_candidates", []) if isinstance(ui_runtime, dict) else []
    candidates = [
        item for item in candidates
        if isinstance(item, dict) and item.get("source_contract_file")
    ][:PACKAGE_CANDIDATE_LIMIT]
    logger.info(
        "[MERGE_DEPENDENCY_PROFILE] atlas_load_seconds=%.2f projects=%d candidates=%d limit=%d",
        atlas_loaded_at - started,
        len(projects),
        len(candidates),
        PACKAGE_CANDIDATE_LIMIT,
    )

    project_file_cache = {
        project_key: _project_known_files(atlas, project_key) | _project_disk_source_files(project_root)
        for project_key, project_root in projects.items()
    }
    cache_ready_at = time.perf_counter()
    logger.info(
        "[MERGE_DEPENDENCY_PROFILE] project_file_cache_seconds=%.2f cached_projects=%d",
        cache_ready_at - atlas_loaded_at,
        len(project_file_cache),
    )

    packages = []
    for index, candidate in enumerate(candidates, start=1):
        source_project = canonical_project_name(str(candidate.get("source") or ""))
        packages.append(build_dependency_package(
            candidate,
            atlas,
            project_root=projects.get(source_project),
            known_project_files=project_file_cache.get(source_project, set()),
        ))
        if index == len(candidates) or index % 25 == 0:
            logger.info(
                "[MERGE_DEPENDENCY_PROFILE] packaged=%d/%d elapsed_seconds=%.2f current_source=%s",
                index,
                len(candidates),
                time.perf_counter() - cache_ready_at,
                source_project,
            )

    tier_counter = Counter(item["package_tier"] for item in packages)
    project_counter = Counter(item["source"] for item in packages)

    payload = {
        "meta": {"kind": "merge_dependency_packages", "version": "v1"},
        "summary": {
            "packages": len(packages),
            "package_tiers": dict(tier_counter),
            "by_source": dict(project_counter),
            "max_closure_files": MAX_CLOSURE_FILES,
        },
        "packages": packages,
    }
    save_json_atomic(RAW_DIR / "merge_dependency_packages.json", payload)
    write_current_atlas_lineage(
        artifact_id="merge_dependency_packages",
        producer="tools.engines.merge_dependency_packager",
        artifact_payload=payload,
        atlas=atlas,
        dependency_payloads={"ui_runtime_contracts": ui_runtime},
    )

    lines = [
        "# Merge Dependency Packager",
        "",
        "Atlas-backed transfer manifests for merge candidates with source contracts.",
        "",
        f"- packages: `{len(packages)}`",
        f"- package tiers: `{dict(tier_counter)}`",
        "",
        "## Top Packages",
    ]
    for item in sorted(packages, key=lambda row: (row["package_tier"], row["closure_size"]), reverse=True)[:60]:
        lines.append(
            f"- `{item['candidate']}` <- {item['source']} | `{item['package_tier']}` | "
            f"files `{item['closure_size']}` | unresolved `{len(item['unresolved_internal_deps'])}` | "
            f"harness providers `{len((item.get('harness_plan') or {}).get('provider_tree') or [])}` | "
            f"target `{item.get('target_path') or '-'}`"
        )
    save_text_atomic(REPORTS_DIR / "merge_dependency_packages.md", "\n".join(lines))
    logger.info(
        "[MERGE_DEPENDENCY_PROFILE] total_seconds=%.2f packages=%d tiers=%s by_source=%s",
        time.perf_counter() - started,
        len(packages),
        dict(tier_counter),
        dict(project_counter),
    )
    logger.info("Merge dependency package artifacts written.")
    return payload


if __name__ == "__main__":
    run_merge_dependency_packager()
