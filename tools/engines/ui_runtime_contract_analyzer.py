from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable

from tools.core.atlas_io import load_atlas_data
from tools.core.analysis_snapshot_lineage import write_current_atlas_lineage
from tools.core.config import CONFIG_DIR, RAW_DIR, REPORTS_DIR, ROOT, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file, load_json_object_strict
from tools.core.logger import logger
from tools.core.projects_registry import canonical_project_name, resolve_runtime_projects
from tools.core.source_evidence import read_atlas_bound_source
from tools.core.language_registry import apply_legacy_path_shims


UI_EXTENSIONS = (".tsx", ".jsx")
UI_PATH_HINTS = (
    "/components/",
    "/features/",
    "/pages/",
    "/widgets/",
    "/layouts/",
    "/layout/",
    "/ui/",
)
UI_TARGET_LAYERS = {"features", "widgets", "pages", "components", "ui_surface"}
UI_NAME_SUFFIXES = (
    "Page",
    "View",
    "Panel",
    "Modal",
    "Dialog",
    "Drawer",
    "Card",
    "List",
    "Form",
    "Editor",
    "Studio",
    "Widget",
    "Layout",
)

PROVIDER_TAG_RE = re.compile(r"<([A-Z][A-Za-z0-9_]*Provider)\b")
CONTEXT_HOOK_RE = re.compile(r"\b(use[A-Z][A-Za-z0-9_]*Context)\s*\(")
STORE_HOOK_RE = re.compile(r"\b(use[A-Z][A-Za-z0-9_]*Store)\s*\(")
ROUTER_HOOK_RE = re.compile(r"\b(useNavigate|useParams|useSearchParams|useLocation|useLoaderData|useRouteLoaderData)\s*\(")
I18N_TRANSLATION_ARG_RE = re.compile(r"\buseTranslation\s*\(\s*(\[[^\]]+\]|['\"][^'\"]+['\"])?")
I18N_STRING_RE = re.compile(r"['\"]([^'\"]+)['\"]")
I18N_KEY_RE = re.compile(r"\b(?:t|i18n\.t)\s*\(\s*['\"]([^'\"]+)['\"]")
I18N_CALL_RE = re.compile(r"\b(?:t|i18n\.t)\s*\(\s*['\"]([^'\"]+)['\"]\s*(?:,\s*([\s\S]*?))?\)", re.DOTALL)
CLASS_NAME_RE = re.compile(r"\bclassName\s*=\s*(?:['\"]([^'\"]+)['\"]|\{`([^`]+)`\})")
CLASS_COMPOSITION_DEFAULTS = ("cn", "clsx", "classNames", "classnames", "twMerge", "cva", "tv")
STRING_LITERAL_RE = re.compile(r"['\"]([^'\"]+)['\"]")
CSS_VAR_RE = re.compile(r"var\(\s*(--[A-Za-z0-9_-]+)")
CSS_IMPORT_RE = re.compile(r"import\s+['\"]([^'\"]+\.css)['\"]")
RELATIVE_IMPORT_RE = re.compile(r"(?:import|export)\s+(?:type\s+)?[\s\S]*?from\s+['\"](\.{1,2}/[^'\"]+)['\"]")
SERVICE_IMPORT_RE = re.compile(r"from\s+['\"]([^'\"]*(?:service|Service|api|Api|repository|Repository|eventBus|store|Store)[^'\"]*)['\"]")
BROWSER_API_RE = re.compile(r"\b(window|document|localStorage|sessionStorage|indexedDB|FileReader|ResizeObserver|IntersectionObserver)\b")
ROUTE_COMPONENT_RE = re.compile(r"<(?:Route|Link|NavLink|Navigate)\b")


def _is_ui_file(rel_path: str) -> bool:
    normalized_path = str(rel_path or "").replace("\\", "/").lower()
    normalized = f"/{normalized_path}"
    return normalized.endswith(UI_EXTENSIONS) or any(hint in normalized for hint in UI_PATH_HINTS)


def _resolve_file_path(project_root: Path, rel_path: str) -> Path | None:
    rel = Path(str(rel_path or "").replace("\\", "/"))
    candidates = [
        project_root / rel,
        project_root / "src" / rel,
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def _unique_sorted(values: Iterable[str]) -> list[str]:
    return sorted({str(value) for value in values if str(value or "").strip()})


def _extract_class_tokens(content: str) -> list[str]:
    tokens: list[str] = []
    for match in CLASS_NAME_RE.finditer(content):
        raw = match.group(1) or match.group(2) or ""
        # Template expressions are not statically reliable; keep only literal-ish tokens.
        raw = re.sub(r"\$\{.*?\}", " ", raw)
        tokens.extend(part for part in re.split(r"\s+", raw.strip()) if part)
    names = [re.escape(name) for name in _class_composition_functions()]
    if names:
        call_re = re.compile(rf"\b(?:{'|'.join(names)})\s*\((?P<args>[\s\S]{{0,1600}}?)\)", re.MULTILINE)
        for match in call_re.finditer(content):
            for raw in STRING_LITERAL_RE.findall(match.group("args") or ""):
                tokens.extend(part for part in re.split(r"\s+", raw.strip()) if part)
    return _unique_sorted(tokens)


def _class_composition_functions() -> list[str]:
    policy = load_json_object_strict(CONFIG_DIR / "react_runtime_policy.json", label="React runtime policy")
    configured = policy.get("class_composition_functions") if isinstance(policy, dict) else None
    if isinstance(configured, list):
        values = [str(item) for item in configured if str(item or "").strip()]
        if values:
            return values
    return list(CLASS_COMPOSITION_DEFAULTS)


def _load_locale_keys(atlas: dict, projects: dict[str, Path]) -> set[str]:
    keys: set[str] = set()

    def visit(prefix: str, value):
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                next_prefix = f"{prefix}.{child_key}" if prefix else str(child_key)
                visit(next_prefix, child_value)
        elif prefix:
            keys.add(prefix)

    for project, project_data in atlas.items():
        if project == "symbols" or project not in projects or not isinstance(project_data, dict):
            continue
        for rel_path, atlas_entry in (project_data.get("files", {}) or {}).items():
            rel = str(rel_path).replace("\\", "/")
            if Path(rel).suffix.lower() != ".json" or "locales" not in Path(rel).parts:
                continue
            content = read_atlas_bound_source(
                component="ui_runtime_contract_analyzer",
                project=project,
                project_root=projects[project],
                rel_path=rel,
                atlas_entry=atlas_entry,
                reason="locale key contract extraction",
            )
            try:
                data = json.loads(content) if content else {}
            except (TypeError, ValueError):
                data = {}
            namespace = Path(rel).stem
            if isinstance(data, dict):
                visit(namespace, data)
                visit("", data)
    return keys


def _extract_i18n_namespaces(content: str) -> list[str]:
    namespaces: list[str] = []
    for match in I18N_TRANSLATION_ARG_RE.finditer(content):
        raw = match.group(1) or ""
        namespaces.extend(I18N_STRING_RE.findall(raw))
    return _unique_sorted(namespaces)


def _key_has_suffix_match(key: str, namespaces: list[str], known_locale_keys: set[str]) -> bool:
    plain_key = key.split(":", 1)[-1]
    candidates = [plain_key]
    candidates.extend(f"{namespace}.{plain_key}" for namespace in namespaces)
    return any(
        known_key == candidate or known_key.endswith(f".{candidate}")
        for candidate in candidates
        for known_key in known_locale_keys
    )


def _classify_i18n_keys(
    keys: list[str],
    namespaces: list[str],
    known_locale_keys: set[str],
) -> tuple[list[str], list[str]]:
    missing = []
    unresolved = []
    for key in keys:
        if not key or key in known_locale_keys:
            continue
        namespace_key = key if ":" not in key else key.replace(":", ".", 1)
        plain_key = key.split(":", 1)[-1]
        namespaced_candidates = [namespace_key, plain_key]
        namespaced_candidates.extend(f"{namespace}.{plain_key}" for namespace in namespaces)
        if any(candidate in known_locale_keys for candidate in namespaced_candidates):
            continue
        if _key_has_suffix_match(key, namespaces, known_locale_keys):
            unresolved.append(key)
            continue
        missing.append(key)
    return _unique_sorted(missing), _unique_sorted(unresolved)


def _extract_i18n_keys(content: str) -> tuple[list[str], list[str]]:
    keys: list[str] = []
    defaulted: list[str] = []
    for match in I18N_CALL_RE.finditer(content):
        key = match.group(1)
        args = match.group(2) or ""
        keys.append(key)
        if "defaultValue" in args:
            defaulted.append(key)
    if not keys:
        keys = I18N_KEY_RE.findall(content)
    return _unique_sorted(keys), _unique_sorted(defaulted)


def _smoke_route_for_target(target_path: str, studio: str | None = None) -> str:
    return "/"


def _smoke_plan(candidate: dict, tier: str, reasons: list[str], route_contract: dict | None = None) -> dict:
    target_path = str(candidate.get("target_path") or "")
    studio = str(candidate.get("studio") or "")
    route = (route_contract or {}).get("smoke_path") or _smoke_route_for_target(target_path, studio)
    assertions = [
        "route_renders_without_uncaught_error",
        "no_missing_provider_or_context_error",
        "no_console_error_on_initial_render",
    ]
    if "missing_i18n_keys" in reasons:
        assertions.append("no_visible_raw_i18n_keys")
    if "style_token_contract" in reasons:
        assertions.append("critical_layout_elements_have_nonzero_size")
    if "store_shape_contract" in reasons:
        assertions.append("store_selectors_resolve_expected_shape")

    return {
        "required": tier in {"high", "medium"} and bool(candidate.get("ui_surface")),
        "suggested_route": route,
        "route_source": (route_contract or {}).get("source", "root_fallback_not_target_route"),
        "route_template": (route_contract or {}).get("route"),
        "route_framework": (route_contract or {}).get("framework"),
        "setup": [
            "mount_main_app_shell",
            "seed_minimal_project_fixture",
            "enable_error_boundary_capture",
        ],
        "assertions": assertions,
    }


def _is_candidate_ui_surface(candidate: dict, normalized_target: str) -> bool:
    target_layer = str(candidate.get("target_layer") or "")
    name = str(candidate.get("name") or "")
    lower_target = f"/{normalized_target.lower()}"
    if name.startswith("use") or lower_target.endswith((".ts", ".mts", ".cts")):
        return False
    if lower_target.endswith((".tsx", ".jsx")):
        return True
    if target_layer in {"pages", "widgets", "components", "ui_surface"}:
        return True
    return target_layer in UI_TARGET_LAYERS and name.endswith(UI_NAME_SUFFIXES)


def _dependency_closure_plan(contract: dict | None) -> dict:
    contract = contract or {}
    required = []
    if contract.get("provider_tags") or contract.get("context_hooks"):
        required.append("provider_tree")
    if contract.get("store_hooks"):
        required.append("store_shape")
    if contract.get("router_hooks") or contract.get("route_components"):
        required.append("route_contract")
    if contract.get("missing_i18n_keys"):
        required.append("i18n_keys")
    if contract.get("css_imports") or contract.get("css_variables"):
        required.append("style_tokens")
    if contract.get("service_imports"):
        required.append("service_ports")
    if contract.get("browser_apis"):
        required.append("browser_api_mocks")

    return {
        "required_contracts": required,
        "relative_imports": contract.get("relative_imports", [])[:40],
        "service_imports": contract.get("service_imports", [])[:40],
        "providers": contract.get("provider_tags", [])[:40],
        "context_hooks": contract.get("context_hooks", [])[:40],
        "store_hooks": contract.get("store_hooks", [])[:40],
        "i18n_keys": contract.get("i18n_keys", [])[:80],
        "missing_i18n_keys": contract.get("missing_i18n_keys", [])[:80],
        "css_variables": contract.get("css_variables", [])[:40],
        "css_imports": contract.get("css_imports", [])[:40],
    }


def analyze_ui_runtime_contract(content: str, known_locale_keys: set[str] | None = None) -> dict:
    known_locale_keys = known_locale_keys or set()
    provider_tags = _unique_sorted(PROVIDER_TAG_RE.findall(content))
    context_hooks = _unique_sorted(CONTEXT_HOOK_RE.findall(content))
    store_hooks = _unique_sorted(STORE_HOOK_RE.findall(content))
    router_hooks = _unique_sorted(ROUTER_HOOK_RE.findall(content))
    i18n_namespaces = _extract_i18n_namespaces(content)
    i18n_keys, i18n_defaulted_keys = _extract_i18n_keys(content)
    css_imports = _unique_sorted(CSS_IMPORT_RE.findall(content))
    css_vars = _unique_sorted(CSS_VAR_RE.findall(content))
    class_tokens = _extract_class_tokens(content)
    relative_imports = _unique_sorted(RELATIVE_IMPORT_RE.findall(content))
    service_imports = _unique_sorted(SERVICE_IMPORT_RE.findall(content))
    browser_apis = _unique_sorted(BROWSER_API_RE.findall(content))
    route_components = bool(ROUTE_COMPONENT_RE.search(content))
    missing_i18n_raw, unresolved_i18n_raw = _classify_i18n_keys(i18n_keys, i18n_namespaces, known_locale_keys)
    defaulted_key_set = set(i18n_defaulted_keys)
    missing_i18n = [key for key in missing_i18n_raw if key not in defaulted_key_set]
    unresolved_i18n = [key for key in unresolved_i18n_raw if key not in defaulted_key_set]

    risk_points = 0
    risk_reasons: list[str] = []
    if provider_tags or context_hooks:
        risk_points += min(20, (len(provider_tags) + len(context_hooks)) * 4)
        risk_reasons.append("provider_or_context_contract")
    if store_hooks:
        risk_points += min(18, len(store_hooks) * 6)
        risk_reasons.append("store_shape_contract")
    if router_hooks or route_components:
        risk_points += 12
        risk_reasons.append("route_contract")
    if missing_i18n:
        risk_points += min(20, len(missing_i18n) * 5)
        risk_reasons.append("missing_i18n_keys")
    if unresolved_i18n:
        risk_points += min(8, len(unresolved_i18n) * 2)
        risk_reasons.append("i18n_key_unresolved_by_static_lookup")
    if css_imports or css_vars:
        risk_points += min(12, (len(css_imports) + len(css_vars)) * 3)
        risk_reasons.append("style_token_contract")
    if service_imports:
        risk_points += min(12, len(service_imports) * 3)
        risk_reasons.append("service_runtime_contract")
    if browser_apis:
        risk_points += min(10, len(browser_apis) * 2)
        risk_reasons.append("browser_api_contract")
    if relative_imports:
        risk_points += min(8, len(relative_imports))
        risk_reasons.append("relative_dependency_closure")

    if risk_points >= 45:
        risk_tier = "high"
    elif risk_points >= 22:
        risk_tier = "medium"
    elif risk_points > 0:
        risk_tier = "low"
    else:
        risk_tier = "minimal"

    return {
        "provider_tags": provider_tags,
        "context_hooks": context_hooks,
        "store_hooks": store_hooks,
        "router_hooks": router_hooks,
        "route_components": route_components,
        "i18n_namespaces": i18n_namespaces,
        "i18n_keys": i18n_keys[:120],
        "i18n_defaulted_keys": i18n_defaulted_keys[:120],
        "missing_i18n_keys": missing_i18n[:120],
        "unresolved_i18n_keys": unresolved_i18n[:120],
        "css_imports": css_imports,
        "css_variables": css_vars,
        "class_tokens_sample": class_tokens[:80],
        "relative_imports": relative_imports[:80],
        "service_imports": service_imports[:80],
        "browser_apis": browser_apis,
        "risk_points": risk_points,
        "risk_tier": risk_tier,
        "risk_reasons": _unique_sorted(risk_reasons),
    }


def _iter_ui_files(atlas: dict, projects: dict[str, Path]):
    for project, project_root in projects.items():
        atlas_files = (atlas.get(project, {}) or {}).get("files", {}) or {}
        for rel_path, file_data in atlas_files.items():
            if not _is_ui_file(rel_path):
                continue
            path = _resolve_file_path(project_root, rel_path)
            if path is None:
                continue
            yield project, rel_path, path, file_data


def _candidate_rows(host_merge: dict) -> list[dict]:
    rows: list[dict] = []
    studios = host_merge.get("studios", {}) if isinstance(host_merge, dict) else {}
    for studio, payload in studios.items():
        if not isinstance(payload, dict):
            continue
        for bucket in ("top_merge_candidates", "top_review_candidates"):
            for candidate in payload.get(bucket, []) or []:
                if isinstance(candidate, dict):
                    row = dict(candidate)
                    row["studio"] = studio
                    row["bucket"] = bucket
                    rows.append(row)
    return rows


def _build_symbol_source_index(atlas: dict, file_contracts: dict[str, dict]) -> dict[tuple[str, str], dict]:
    candidates: dict[tuple[str, str], list[dict]] = {}
    if not isinstance(atlas, dict):
        return {}

    for project, project_payload in atlas.items():
        if not isinstance(project_payload, dict):
            continue
        symbols = project_payload.get("symbols", []) or []
        if not isinstance(symbols, list):
            continue
        canonical_project = canonical_project_name(str(project))
        for symbol_payload in symbols:
            if not isinstance(symbol_payload, dict):
                continue
            symbol_name = str(symbol_payload.get("name") or "")
            if not symbol_name:
                continue
            rel_path = str(symbol_payload.get("file") or "").replace("\\", "/")
            if not rel_path:
                continue
            scoped_file = f"{canonical_project}::{rel_path}"
            contract = file_contracts.get(scoped_file)
            if not contract:
                continue
            candidates.setdefault((canonical_project, symbol_name), []).append({
                "project": canonical_project,
                "file": rel_path,
                "scoped_file": scoped_file,
                "contract": contract,
            })
    return {key: rows[0] for key, rows in candidates.items() if len(rows) == 1}


def _candidate_ui_risk(
    candidate: dict,
    file_contracts: dict[str, dict],
    symbol_source_index: dict[tuple[str, str], dict] | None = None,
    route_lookup: dict[str, dict] | None = None,
) -> dict:
    target_layer = str(candidate.get("target_layer") or "")
    target_path = str(candidate.get("target_path") or "")
    normalized_target = apply_legacy_path_shims(target_path.replace("\\", "/"))
    is_ui_surface = _is_candidate_ui_surface(candidate, normalized_target)
    matching_contract = None
    matching_contract_origin = "none"
    source_match = None
    source_project = canonical_project_name(str(candidate.get("source") or ""))
    candidate_name = str(candidate.get("name") or "")

    for scoped_file, contract in file_contracts.items():
        if normalized_target and scoped_file.endswith(normalized_target.replace("src/", "", 1)):
            matching_contract = contract
            matching_contract_origin = "target_path"
            break

    if symbol_source_index and source_project and candidate_name:
        source_match = symbol_source_index.get((source_project, candidate_name))
        if source_match and not matching_contract:
            matching_contract = source_match.get("contract")
            matching_contract_origin = "source_symbol"
    route_contract = None
    if route_lookup:
        if source_match and source_match.get("scoped_file") in route_lookup:
            route_contract = route_lookup.get(source_match.get("scoped_file"))
        elif normalized_target:
            for scoped_file, route_item in route_lookup.items():
                if scoped_file.endswith(normalized_target.replace("src/", "", 1)):
                    route_contract = route_item
                    break

    risk_points = 0
    reasons: list[str] = []
    if is_ui_surface:
        risk_points += 20
        reasons.append("ui_surface_candidate")
    if candidate.get("merge_readiness") == "manual_review":
        risk_points += 18
        reasons.append("manual_review_candidate")
    if str(candidate.get("risk") or "").upper() == "MEDIUM":
        risk_points += 10
        reasons.append("candidate_medium_risk")
    if matching_contract:
        risk_points += int(matching_contract.get("risk_points", 0) or 0) // 2
        reasons.extend(matching_contract.get("risk_reasons", []))
        if matching_contract_origin == "source_symbol":
            reasons.append("source_runtime_contract")

    if risk_points >= 45:
        tier = "high"
    elif risk_points >= 22:
        tier = "medium"
    elif risk_points > 0:
        tier = "low"
    else:
        tier = "minimal"

    reasons = _unique_sorted(reasons)
    candidate_payload = {
        "name": candidate.get("name"),
        "studio": candidate.get("studio"),
        "source": candidate.get("source"),
        "target_path": target_path,
        "target_layer": target_layer,
        "merge_readiness": candidate.get("merge_readiness"),
        "candidate_risk": candidate.get("risk"),
        "ui_surface": is_ui_surface,
        "risk_points": risk_points,
        "risk_tier": tier,
        "risk_reasons": reasons,
        "recommended_gate": "browser_smoke_required" if tier in {"high", "medium"} and is_ui_surface else "static_gate_sufficient",
        "contract_origin": matching_contract_origin,
        "source_contract_file": (source_match or {}).get("scoped_file"),
        "source_contract_risk_tier": ((source_match or {}).get("contract") or {}).get("risk_tier"),
    }
    candidate_payload["dependency_closure_plan"] = _dependency_closure_plan(matching_contract)
    candidate_payload["smoke_plan"] = _smoke_plan(candidate_payload, tier, reasons, route_contract)
    return candidate_payload


def run_ui_runtime_contract_analyzer():
    logger.info("Analyzing UI runtime contracts and merge compatibility...")
    atlas = load_atlas_data()
    projects = resolve_runtime_projects(ROOT)
    locale_keys = _load_locale_keys(atlas, projects)

    files: list[dict] = []
    file_contracts: dict[str, dict] = {}
    for project, rel_path, path, file_data in _iter_ui_files(atlas, projects):
        content = read_atlas_bound_source(
            component="ui_runtime_contract_analyzer",
            project=project,
            project_root=projects[project],
            rel_path=rel_path,
            atlas_entry=file_data,
            reason="UI runtime contract extraction",
        )
        if content is None:
            continue
        contract = analyze_ui_runtime_contract(content, locale_keys)
        scoped_file = f"{project}::{rel_path}"
        entry = {
            "project": project,
            "file": rel_path,
            "scoped_file": scoped_file,
            "loc": file_data.get("loc", 0) if isinstance(file_data, dict) else 0,
            **contract,
        }
        files.append(entry)
        file_contracts[scoped_file] = contract

    host_merge = load_json_file(RAW_DIR / "host_merge_intelligence.json", {})
    framework_routes = load_json_file(RAW_DIR / "framework_routes.json", {})
    route_lookup = {
        str(route.get("scoped_file")): route
        for route in (framework_routes.get("routes", []) if isinstance(framework_routes, dict) else [])
        if isinstance(route, dict) and route.get("scoped_file")
    }
    symbol_source_index = _build_symbol_source_index(atlas, file_contracts)
    candidates = [_candidate_ui_risk(row, file_contracts, symbol_source_index, route_lookup) for row in _candidate_rows(host_merge)]
    candidates = sorted(candidates, key=lambda item: (item["risk_points"], item.get("name") or ""), reverse=True)

    risk_counter = Counter(item["risk_tier"] for item in files)
    candidate_counter = Counter(item["risk_tier"] for item in candidates)
    reason_counter = Counter(reason for item in files for reason in item.get("risk_reasons", []))
    by_project = defaultdict(lambda: Counter())
    for item in files:
        by_project[item["project"]][item["risk_tier"]] += 1

    payload = {
        "meta": {"kind": "ui_runtime_contracts", "version": "v1"},
        "summary": {
            "files_analyzed": len(files),
            "risk_tiers": dict(risk_counter),
            "top_risk_reasons": dict(reason_counter.most_common(12)),
            "candidate_risk_tiers": dict(candidate_counter),
            "by_project": {project: dict(counter) for project, counter in sorted(by_project.items())},
        },
        "files": sorted(files, key=lambda item: (item["risk_points"], item["loc"]), reverse=True),
        "merge_candidates": candidates,
    }

    save_json_atomic(RAW_DIR / "ui_runtime_contracts.json", payload)
    write_current_atlas_lineage(
        artifact_id="ui_runtime_contracts",
        producer="tools.engines.ui_runtime_contract_analyzer",
        artifact_payload=payload,
        atlas=atlas,
        dependency_payloads={
            "framework_routes": framework_routes,
            "host_merge_intelligence": host_merge,
        },
    )

    lines = [
        "# UI Runtime Contract Analyzer",
        "",
        "Static preflight for UI merge safety: provider/context, route, i18n, CSS token, store, service, and browser API contracts.",
        "",
        "## Summary",
        f"- files analyzed: `{len(files)}`",
        f"- file risk tiers: `{json.dumps(dict(risk_counter), ensure_ascii=False)}`",
        f"- merge candidate tiers: `{json.dumps(dict(candidate_counter), ensure_ascii=False)}`",
        "",
        "## Highest-Risk UI Files",
    ]
    for item in payload["files"][:25]:
        lines.append(
            f"- `{item['scoped_file']}` | `{item['risk_tier']}` | points `{item['risk_points']}` | "
            f"reasons `{', '.join(item.get('risk_reasons', [])) or 'none'}`"
        )
    lines += ["", "## UI Merge Candidate Gates"]
    for item in candidates[:35]:
        lines.append(
            f"- `{item['name']}` <- {item['source']} | `{item['studio']}` | `{item['risk_tier']}` | "
            f"`{item['recommended_gate']}` | `{item['target_path']}` | contract `{item.get('contract_origin', 'none')}`"
        )
        if item.get("recommended_gate") == "browser_smoke_required":
            smoke = item.get("smoke_plan", {})
            lines.append(
                f"  - smoke: route `{smoke.get('suggested_route', '/')}` | "
                f"assertions `{', '.join(smoke.get('assertions', [])[:4])}`"
            )
        if item.get("source_contract_file"):
            lines.append(f"  - source contract: `{item['source_contract_file']}`")

    save_text_atomic(REPORTS_DIR / "ui_runtime_contracts.md", "\n".join(lines))
    logger.info("UI runtime contract artifacts written.")
    return payload


if __name__ == "__main__":
    run_ui_runtime_contract_analyzer()
