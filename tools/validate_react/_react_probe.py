import json
from pathlib import Path
from typing import Iterable, Any
import sys
from tools.core.json_io import load_json_file
from tools.core.config import CONFIG_DIR, ROOT, DYNAMIC_CONFIG, REPORTS_DIR, RAW_DIR, ensure_output_dir, save_json_atomic, save_text_atomic
from tools.core.projects_registry import project_display_name
from tools.core.source_files import is_analysis_source_file
from tools.core.workspace_mode import get_workspace_mode
from tools.validate_react._react_capabilities import CAPABILITIES


class SizeBoundedDict(dict):
    def __init__(self, max_size=500, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_size = max_size
        self._keys_order = list(self.keys())

    def __setitem__(self, key, value):
        if key in self:
            self._keys_order.remove(key)
        self._keys_order.append(key)
        super().__setitem__(key, value)
        if len(self) > self.max_size:
            oldest = self._keys_order.pop(0)
            super().pop(oldest, None)

    def pop(self, key, default=None):
        if key in self._keys_order:
            self._keys_order.remove(key)
        return super().pop(key, default)


def _load_package_json() -> dict:
    package_json = ROOT / "package.json"
    if not package_json.exists():
        return {}
    try:
        return json.loads(package_json.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _load_discovery() -> dict:
    return load_json_file(CONFIG_DIR / "codemaps.discovery.json")


def _iter_config_files(discovery_payload: dict) -> Iterable[Path]:
    if not ROOT.exists():
        return []
    interesting_names = (
        "package.json",
        "pnpm-workspace.yaml",
        "pnpm-workspace.yml",
        "turbo.json",
        "vite.config",
        "next.config",
        "webpack.config",
        "vitest.config",
        "jest.config",
        "cypress.config",
        "playwright.config",
        "tailwind.config",
    )
    candidate_dirs = {ROOT}
    variations = discovery_payload.get("variations", {}) if isinstance(discovery_payload, dict) else {}
    for meta in variations.values():
        if not isinstance(meta, dict):
            continue
        raw_path = meta.get("path")
        if not raw_path:
            continue
        candidate_dirs.add((ROOT / str(raw_path)).resolve())

    for directory in candidate_dirs:
        if not directory.exists() or not directory.is_dir():
            continue
        for child in directory.iterdir():
            if not child.is_file():
                continue
            name = child.name.lower()
            if any(token in name for token in interesting_names):
                yield child


def _project_role_map() -> dict[str, str]:
    project_roles = DYNAMIC_CONFIG.get("project_roles", {}) or {}
    variations = DYNAMIC_CONFIG.get("variations", {}) or {}
    roles: dict[str, str] = {}
    for key in variations.keys():
        canonical = str(key)
        roles[canonical] = str(project_roles.get(canonical, "host" if canonical == "MAIN" else "companion"))
    return roles


def _project_root_map() -> dict[str, Path]:
    variations = DYNAMIC_CONFIG.get("variations", {}) or {}
    roots: dict[str, Path] = {}
    for key, raw in variations.items():
        canonical = str(key)
        if isinstance(raw, list):
            candidate = raw[0] if raw else "."
        else:
            candidate = raw
        candidate_text = str(candidate or ".")
        roots[canonical] = (ROOT / candidate_text).resolve()
    return roots


def _iter_config_files_for_root(project_root: Path) -> Iterable[Path]:
    interesting_names = (
        "package.json",
        "pnpm-workspace.yaml",
        "pnpm-workspace.yml",
        "turbo.json",
        "vite.config",
        "next.config",
        "webpack.config",
        "vitest.config",
        "jest.config",
        "cypress.config",
        "playwright.config",
        "tailwind.config",
    )
    if not project_root.exists() or not project_root.is_dir():
        return []
    for child in project_root.iterdir():
        if not child.is_file():
            continue
        lowered = child.name.lower()
        if any(token in lowered for token in interesting_names):
            yield child


def _load_package_json_for_root(project_root: Path) -> dict:
    package_json = project_root / "package.json"
    if not package_json.exists():
        return {}
    try:
        return json.loads(package_json.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _merge_package_deps(primary: dict, fallback: dict) -> dict:
    primary = primary if isinstance(primary, dict) else {}
    fallback = fallback if isinstance(fallback, dict) else {}
    return {
        **(fallback.get("dependencies") or {}),
        **(fallback.get("devDependencies") or {}),
        **(primary.get("dependencies") or {}),
        **(primary.get("devDependencies") or {}),
    }


def _collect_atlas_source_index(atlas_payload: dict) -> tuple[list[str], list[tuple[str, Path]]]:
    relative_paths: list[str] = []
    source_entries: list[tuple[str, Path]] = []

    if not isinstance(atlas_payload, dict):
        return relative_paths, source_entries

    for project_payload in atlas_payload.values():
        if not isinstance(project_payload, dict):
            continue
        files = project_payload.get("files", {}) or {}
        project_meta = project_payload.get("project", {}) or {}
        project_root = project_meta.get("root")
        if not isinstance(files, dict) or not project_root:
            continue
        project_root_path = Path(str(project_root))
        for rel_path in files.keys():
            rel_text = str(rel_path).replace("\\", "/")
            if not is_analysis_source_file(rel_text):
                continue
            relative_paths.append(rel_text)
            source_entries.append((rel_text, project_root_path / rel_path))

    return relative_paths, source_entries


def _collect_workspace_index(atlas_payload: dict, discovery_payload: dict) -> tuple[list[str], list[tuple[str, Path]], list[str]]:
    source_paths, source_entries = _collect_atlas_source_index(atlas_payload)
    config_paths: list[str] = []
    for path in _iter_config_files(discovery_payload):
        try:
            config_paths.append(str(path.relative_to(ROOT)).replace("\\", "/"))
        except Exception:
            continue
    return source_paths, source_entries, config_paths


def _collect_project_index(project_name: str, atlas_payload: dict, project_root: Path) -> tuple[list[str], list[tuple[str, Path]], list[str]]:
    source_paths: list[str] = []
    source_entries: list[tuple[str, Path]] = []
    config_paths: list[str] = []

    project_payload = atlas_payload.get(project_name, {}) if isinstance(atlas_payload, dict) else {}
    files = project_payload.get("files", {}) if isinstance(project_payload, dict) else {}
    if isinstance(files, dict):
        for rel_path in files.keys():
            rel_text = str(rel_path).replace("\\", "/")
            if not is_analysis_source_file(rel_text):
                continue
            source_paths.append(rel_text)
            source_entries.append((rel_text, project_root / rel_path))

    for path in _iter_config_files_for_root(project_root):
        try:
            config_paths.append(str(path.relative_to(project_root)).replace("\\", "/"))
        except Exception:
            config_paths.append(path.name)

    return source_paths, source_entries, config_paths


def _find_matches(paths: list[str], patterns: tuple[str, ...]) -> list[str]:
    matches: list[str] = []
    lowered_paths = [path.lower() for path in paths]
    for pattern in patterns:
        token = pattern.lower()
        if any(token in path for path in lowered_paths):
            matches.append(pattern)
    return matches


def _scan_source_content(
    entries: list[tuple[str, Path]],
    patterns: tuple[str, ...],
    limit: int = 4,
    text_cache: dict[Path, str] | None = None,
) -> list[str]:
    remaining = {pattern.lower(): pattern for pattern in patterns}
    found: list[str] = []
    if not remaining:
        return found

    cache = text_cache if text_cache is not None else SizeBoundedDict(max_size=500)
    for rel_path, path in entries:
        if not is_analysis_source_file(rel_path):
            continue
        text = cache.get(path)
        if text is None:
            try:
                text = path.read_text(encoding="utf-8", errors="ignore").lower()
                cache[path] = text
            except Exception:
                continue
        matched_now = [original for lowered, original in remaining.items() if lowered in text]
        for original in matched_now:
            found.append(original)
            remaining.pop(original.lower(), None)
            if len(found) >= limit:
                return found
        if not remaining:
            return found
    return found


def _evaluate_capabilities(
    deps: dict,
    source_files: list[str],
    source_entries: list[tuple[str, Path]],
    config_files: list[str],
    atlas_features: set[str],
    zustand_store_count: int,
    tanstack_query_count: int,
    tanstack_mutation_count: int,
    text_cache: dict[Path, str],
) -> tuple[list[dict], dict]:
    matrix = []
    summary = {
        "repo_present": 0,
        "declared_detected": 0,
        "partial_missing": 0,
        "action_required": 0,
    }

    for capability in CAPABILITIES:
        search_space = list(config_files)
        if capability.key == "bundler_workspace":
            search_space.append("package.json")
        package_hits = [name for name in capability.package_names if name in deps]
        config_hits = _find_matches(search_space, capability.code_patterns)
        
        feature_hits = []
        file_hits = _find_matches(source_files, capability.code_patterns)
        code_hits = _scan_source_content(source_entries, capability.code_patterns, limit=4, text_cache=text_cache)

        if capability.key == "state_management" and zustand_store_count > 0:
            code_hits.append("zustand")
        if capability.key == "data_fetching":
            if tanstack_query_count > 0: code_hits.append("useQuery")
            if tanstack_mutation_count > 0: code_hits.append("useMutation")

        present_in_repo = bool(package_hits or config_hits)
        is_detected = bool(file_hits or code_hits or feature_hits)

        support = "not_applicable"
        if present_in_repo and is_detected:
            support = "declared_detected"
            summary["repo_present"] += 1
            summary["declared_detected"] += 1
        elif present_in_repo and not is_detected:
            support = "partial_missing"
            summary["repo_present"] += 1
            summary["partial_missing"] += 1
            summary["action_required"] += 1
        elif not present_in_repo and is_detected:
            support = "partial_missing"
            summary["partial_missing"] += 1
            summary["action_required"] += 1

        matrix.append({
            "key": capability.key,
            "category": capability.category,
            "label": capability.label,
            "present_in_repo": present_in_repo,
            "support": support,
            "evidence": {
                "package_hits": package_hits[:4],
                "config_hits": config_hits[:4],
                "feature_hits": feature_hits[:4],
                "file_hits": file_hits[:4],
                "code_hits": code_hits[:4],
            }
        })
    return matrix, summary


def run_probe() -> dict:
    ensure_output_dir()
    package_json = _load_package_json()
    discovery_payload = _load_discovery()
    deps = {
        **(package_json.get("dependencies") or {}),
        **(package_json.get("devDependencies") or {}),
    } if isinstance(package_json, dict) else {}
    _, _, config_files = _collect_workspace_index({}, discovery_payload)
    workspace_mode = get_workspace_mode()
    project_roots = _project_root_map()
    project_roles = _project_role_map()

    capabilities = []
    summary = {"repo_present": 0, "declared_detected": 0}
    for capability in CAPABILITIES:
        search_space = list(config_files)
        if capability.key == "bundler_workspace":
            search_space.append("package.json")
        package_hits = [name for name in capability.package_names if name in deps]
        config_hits = _find_matches(search_space, capability.code_patterns)
        present_in_repo = bool(package_hits or config_hits)
        if present_in_repo:
            summary["repo_present"] += 1
            summary["declared_detected"] += 1
        capabilities.append(
            {
                "key": capability.key,
                "category": capability.category,
                "label": capability.label,
                "present_in_repo": present_in_repo,
                "support": "declared_detected" if present_in_repo else "not_applicable",
                "evidence": {
                    "package_hits": package_hits,
                    "config_hits": config_hits,
                },
            }
        )

    by_project: dict[str, dict] = {}
    for project_name, project_root in sorted(project_roots.items()):
        project_role = project_roles.get(project_name, "host" if project_name == "MAIN" else "companion")
        project_package_json = _load_package_json_for_root(project_root)
        if project_role == "host" and project_root != ROOT:
            project_deps = _merge_package_deps(project_package_json, package_json)
            project_config_files = sorted({*([str(path.name) for path in _iter_config_files_for_root(project_root)]), *([str(path.name) for path in _iter_config_files_for_root(ROOT)])})
        else:
            project_deps = _merge_package_deps(project_package_json, {})
            project_config_files = [str(path.name) for path in _iter_config_files_for_root(project_root)]
        project_capabilities = []
        project_summary = {"repo_present": 0, "declared_detected": 0}
        for capability in CAPABILITIES:
            search_space = list(project_config_files)
            if capability.key == "bundler_workspace":
                search_space.append("package.json")
            package_hits = [name for name in capability.package_names if name in project_deps]
            config_hits = _find_matches(search_space, capability.code_patterns)
            present_in_repo = bool(package_hits or config_hits)
            if present_in_repo:
                project_summary["repo_present"] += 1
                project_summary["declared_detected"] += 1
            project_capabilities.append(
                {
                    "key": capability.key,
                    "category": capability.category,
                    "label": capability.label,
                    "present_in_repo": present_in_repo,
                    "support": "declared_detected" if present_in_repo else "not_applicable",
                    "evidence": {
                        "package_hits": package_hits,
                        "config_hits": config_hits,
                    },
                }
            )

        by_project[project_name] = {
            "role": project_role,
            "display_name": project_display_name(project_name),
            "project_root": str(project_root),
            "summary": project_summary,
            "capabilities": project_capabilities,
        }

    payload = {
        "workspace_root": str(ROOT),
        "workspace_mode": workspace_mode,
        "summary": summary,
        "capabilities": capabilities,
        "by_project": by_project,
    }
    save_json_atomic(RAW_DIR / "react_capability_probe.json", payload)

    lines = [
        "# React Capability Probe",
        "",
        "> Early declared/config-driven probe that sets React expectations before deep structural validation.",
        "",
        f"Workspace mode: **{workspace_mode.get('mode', 'unknown')}**",
        f"Projects: **{workspace_mode.get('project_count', 0)}**",
        "",
        "## Projects",
        "",
    ]
    for project_name, project_payload in by_project.items():
        project_summary = project_payload["summary"]
        lines.append(f"### {project_payload['display_name']} (`{project_name}` / `{project_payload['role']}`)")
        lines.append(f"- Present in repo: `{project_summary['repo_present']}`")
        lines.append(f"- Declared detected: `{project_summary['declared_detected']}`")
        lines.append("")
        lines.append("| Capability | Present | Evidence |")
        lines.append("|---|---|---|")
        for item in project_payload["capabilities"]:
            evidence_bits = []
            if item["evidence"]["package_hits"]:
                evidence_bits.append("pkg:" + ", ".join(item["evidence"]["package_hits"][:4]))
            if item["evidence"]["config_hits"]:
                evidence_bits.append("config:" + ", ".join(item["evidence"]["config_hits"][:4]))
            lines.append(
                f"| {item['label']} | {'YES' if item['present_in_repo'] else 'NO'} | {' ; '.join(evidence_bits) or '-'} |"
            )
        lines.append("")
    save_text_atomic(REPORTS_DIR / "react_capability_probe.md", "\n".join(lines))
    return payload
