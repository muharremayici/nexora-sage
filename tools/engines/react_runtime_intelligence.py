from __future__ import annotations

import re
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from tools.core.config import CONFIG_DIR, DYNAMIC_CONFIG, RAW_DIR, REPORTS_DIR, ROOT, save_json_atomic, save_text_atomic
from tools.core.atlas_io import load_atlas_data
from tools.core.runtime_project_scope import project_runtime_atlas
from tools.core.json_io import load_json_file, load_json_object_strict
from tools.core.logger import logger
from tools.core.react_evidence import atlas_evidence_kinds, attach_react_evidence_contract
from tools.core.report_surface_limits import report_surface_limit
from tools.core.source_evidence import read_atlas_bound_source


REACT_EXTENSIONS = (".tsx", ".jsx", ".ts", ".js")
CLIENT_DIRECTIVE_RE = re.compile(r"^\s*['\"]use client['\"]", re.MULTILINE)
IMPORT_RE = re.compile(r"import\s+(?:type\s+)?(?:[^'\";]+?\s+from\s+)?['\"]([^'\"]+)['\"]")
DEFAULT_HEAVY_CLIENT_IMPORTS = ()
DEFAULT_SERVER_ONLY_IMPORTS = ()
MUTATION_RE = re.compile(r"\buseMutation\s*\((.*?)\)\s*;?", re.DOTALL)
QUERY_RE = re.compile(r"\buse(?:Suspense)?Query\s*\(")
INVALIDATE_RE = re.compile(r"\binvalidateQueries\s*\(")
OPTIMISTIC_RE = re.compile(r"\b(?:onMutate|setQueryData|optimistic)\b")
AUTH_ROUTE_RE = re.compile(r"(?:^|/)(?:admin|dashboard|settings|account|billing|private|auth)/", re.IGNORECASE)
AUTH_GUARD_RE = re.compile(r"\b(?:requireAuth|auth\(|getServerSession|getSession|useSession|withAuth|currentUser|requireRole|hasPermission|canAccess)\b")
CLIENT_AUTH_ONLY_RE = re.compile(r"\b(?:useSession|localStorage|sessionStorage)\b")
ERROR_BOUNDARY_RE = re.compile(r"\b(?:ErrorBoundary|error\.tsx|not-found\.tsx|try\s*{|catch\s*\(|onError|QueryErrorResetBoundary)\b")
ASYNC_SURFACE_RE = re.compile(r"\b(?:await\s+|fetch\s*\(|useQuery\s*\(|useSuspenseQuery\s*\()")
FOCUS_JOURNEY_RE = re.compile(r"\b(?:autoFocus|focus\(|tabIndex|onKeyDown|onEscapeKeyDown|FocusScope|initialFocus)\b")
DIALOG_RE = re.compile(r"<(?:Dialog|Modal|Sheet|Popover|AlertDialog)\b")
ROUTE_FILE_RE = re.compile(r"(?:^|/)(?:app|pages)/.*(?:page|layout|route)\.(?:tsx|jsx|ts|js)$")
CSS_CLASS_RE = re.compile(r"className\s*=\s*(?:\"([^\"]+)\"|'([^']+)')")
RESPONSIVE_PREFIX_RE = re.compile(r"\b(?:sm|md|lg|xl|2xl):")
CLASS_COMPOSITION_DEFAULTS = ()
STRING_LITERAL_RE = re.compile(r"['\"]([^'\"]+)['\"]")
MEMO_RE = re.compile(r"\buse(?:Memo|Callback)\s*\(")
INLINE_FACTORY_RE = re.compile(r"\b(?:const|let)\s+[A-Za-z_$][\w$]*\s*=\s*(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>")
BROWSER_PERMISSION_API_RE = re.compile(
    r"\b(?:navigator\.clipboard|navigator\.geolocation|navigator\.mediaDevices|getUserMedia|Notification\.requestPermission|navigator\.permissions\.query|navigator\.share|showOpenFilePicker|navigator\.wakeLock)",
    re.IGNORECASE,
)
PERMISSION_FALLBACK_RE = re.compile(r"\b(?:try\s*{|catch\s*\(|\.catch\s*\(|onError|permission|denied|fallback|toast|alert\s*\()", re.IGNORECASE)
DEFAULT_REFERENCE_PATH_MARKERS = ()
DEFAULT_FIXTURE_PATH_MARKERS = ()
DEFAULT_FIXTURE_FILE_SUFFIXES = ()

CONFIDENCE_RANK = {
    "needs_runtime_proof": 0,
    "probable": 1,
    "likely": 2,
    "confirmed": 3,
}

DEFAULT_ACTIONABLE_DIMENSIONS = ()

DEFAULT_CALIBRATION_LANES = {
    "act_now": {"min_score": 8, "min_confidence": "likely"},
    "review_next": {"min_score": 4, "min_confidence": "probable"},
    "needs_runtime_probe": {"confidence": "needs_runtime_proof"},
}

POLICY_PATH = CONFIG_DIR / "react_runtime_policy.json"
_POLICY_CACHE: dict[str, Any] | None = None


def _project_root(project: str) -> Path:
    rel = (DYNAMIC_CONFIG.get("variations") or {}).get(project, "")
    return (ROOT / rel).resolve()


def _merge_confidence_map(defaults: dict[str, str], override: Any) -> dict[str, str]:
    merged = dict(defaults)
    if isinstance(override, dict):
        for key, value in override.items():
            key_s = str(key)
            value_s = str(value)
            if key_s in CONFIDENCE_RANK and value_s in {"low", "medium", "high"}:
                merged[key_s] = value_s
    return merged


def _policy_string_list(configured: dict[str, Any], section: str, key: str, defaults: tuple[str, ...] = ()) -> list[str]:
    section_data = configured.get(section, {})
    if not isinstance(section_data, dict):
        section_data = {}
    values = section_data.get(key, defaults)
    if not isinstance(values, list | tuple):
        values = defaults
    return sorted({str(item).replace("\\", "/").strip() for item in values if str(item or "").strip()})


def _load_runtime_policy(force: bool = False) -> dict[str, Any]:
    global _POLICY_CACHE
    if _POLICY_CACHE is not None and not force:
        return _POLICY_CACHE

    configured = load_json_object_strict(POLICY_PATH, label="React runtime policy")

    lanes = dict(DEFAULT_CALIBRATION_LANES)
    lane_override = configured.get("calibration_lanes", {})
    if isinstance(lane_override, dict):
        for lane, value in lane_override.items():
            if lane in lanes and isinstance(value, dict):
                merged_lane = dict(lanes[lane])
                if "min_score" in value:
                    try:
                        merged_lane["min_score"] = int(value["min_score"])
                    except (TypeError, ValueError):
                        pass
                if str(value.get("min_confidence", "")) in CONFIDENCE_RANK:
                    merged_lane["min_confidence"] = str(value["min_confidence"])
                if str(value.get("confidence", "")) in CONFIDENCE_RANK:
                    merged_lane["confidence"] = str(value["confidence"])
                lanes[lane] = merged_lane

    actionable = set(DEFAULT_ACTIONABLE_DIMENSIONS)
    if isinstance(configured.get("actionable_dimensions"), list):
        actionable = {str(item) for item in configured["actionable_dimensions"] if str(item).strip()}

    fp_config = configured.get("false_positive_policy", {})
    if not isinstance(fp_config, dict):
        fp_config = {}
    conservative_fallback = {confidence: "high" for confidence in CONFIDENCE_RANK}
    dimension_overrides: dict[str, dict[str, str]] = {}
    configured_dimensions = fp_config.get("dimension_overrides", {})
    if isinstance(configured_dimensions, dict):
        for dimension, mapping in configured_dimensions.items():
            base = dimension_overrides.get(str(dimension), {})
            dimension_overrides[str(dimension)] = _merge_confidence_map(base, mapping)

    policy = {
        "meta": configured.get("meta", {"kind": "react_runtime_policy", "version": "default"}),
        "policy_source": str(POLICY_PATH),
        "calibration_lanes": lanes,
        "actionable_dimensions": sorted(actionable),
        "runtime_import_surfaces": {
            "heavy_client_imports": _policy_string_list(
                configured,
                "runtime_import_surfaces",
                "heavy_client_imports",
                DEFAULT_HEAVY_CLIENT_IMPORTS,
            ),
            "server_only_imports": _policy_string_list(
                configured,
                "runtime_import_surfaces",
                "server_only_imports",
                DEFAULT_SERVER_ONLY_IMPORTS,
            ),
        },
        "reference_surfaces": {
            "path_markers": _policy_string_list(
                configured,
                "reference_surfaces",
                "path_markers",
                DEFAULT_REFERENCE_PATH_MARKERS,
            ),
            "fixture_path_markers": _policy_string_list(
                configured,
                "reference_surfaces",
                "fixture_path_markers",
                DEFAULT_FIXTURE_PATH_MARKERS,
            ),
            "fixture_file_suffixes": _policy_string_list(
                configured,
                "reference_surfaces",
                "fixture_file_suffixes",
                DEFAULT_FIXTURE_FILE_SUFFIXES,
            ),
        },
        "class_composition_functions": [
            str(item)
            for item in configured.get("class_composition_functions", CLASS_COMPOSITION_DEFAULTS)
            if str(item or "").strip()
        ],
        "false_positive_policy": {
            "corroborated_risk": _merge_confidence_map(
                conservative_fallback,
                fp_config.get("corroborated_risk", {}),
            ),
            "dimension_overrides": dimension_overrides,
            "default_by_confidence": _merge_confidence_map(
                conservative_fallback,
                fp_config.get("default_by_confidence", {}),
            ),
        },
    }
    _POLICY_CACHE = policy
    return policy


def _read_project_file(
    project: str,
    rel_path: str,
    atlas_entry: dict[str, Any] | None = None,
    cache: dict[str, str] | None = None,
) -> str:
    normalized = str(rel_path).replace("\\", "/")
    key = f"{project}::{normalized}"
    if cache is not None and key in cache:
        return cache[key]
    content = read_atlas_bound_source(
        component="react_runtime_intelligence",
        project=project,
        project_root=_project_root(project),
        rel_path=rel_path,
        atlas_entry=atlas_entry if isinstance(atlas_entry, dict) else {},
        reason="React runtime static contract scan",
    )
    value = content or ""
    if cache is not None:
        cache[key] = value
    return value


def _is_react_source(rel_path: str, content: str) -> bool:
    normalized = rel_path.replace("\\", "/")
    return normalized.endswith(REACT_EXTENSIONS) and (
        normalized.endswith((".tsx", ".jsx"))
        or "react" in content
        or "use client" in content
        or "useQuery(" in content
        or "useMutation(" in content
        or "<" in content and ">" in content
    )


def _tier(score: int) -> str:
    if score >= 8:
        return "high"
    if score >= 4:
        return "medium"
    return "low"


def _confidence(score: int, evidence_kinds: set[str], runtime_confirmed: bool = False) -> str:
    if runtime_confirmed:
        return "confirmed"
    if score >= 8 and len(evidence_kinds) >= 2:
        return "likely"
    if score >= 4:
        return "probable"
    return "needs_runtime_proof"


def _confidence_rank(value: str) -> int:
    return CONFIDENCE_RANK.get(str(value), 0)


def _calibration_lane(item: dict[str, Any]) -> str:
    policy = _load_runtime_policy()
    lanes = policy["calibration_lanes"]
    actionable_dimensions = set(policy["actionable_dimensions"])
    confidence = str(item.get("confidence") or "needs_runtime_proof")
    score = int(item.get("score", 0) or 0)
    dimension = str(item.get("dimension") or "")

    if confidence == str(lanes["needs_runtime_probe"].get("confidence", "needs_runtime_proof")):
        return "needs_runtime_probe"
    if (
        score >= int(lanes["act_now"]["min_score"])
        and _confidence_rank(confidence) >= _confidence_rank(str(lanes["act_now"]["min_confidence"]))
        and dimension in actionable_dimensions
    ):
        return "act_now"
    if (
        score >= int(lanes["review_next"]["min_score"])
        and _confidence_rank(confidence) >= _confidence_rank(str(lanes["review_next"]["min_confidence"]))
    ):
        return "review_next"
    return "observe"


def _false_positive_risk(item: dict[str, Any]) -> str:
    fp_policy = _load_runtime_policy()["false_positive_policy"]
    confidence = str(item.get("confidence") or "needs_runtime_proof")
    dimension = str(item.get("dimension") or "")
    evidence_kinds = set(item.get("evidence_kinds", []) or [])

    if "react_ecosystem_corroboration" in evidence_kinds:
        return str(fp_policy.get("corroborated_risk", {}).get(confidence, "medium"))

    dimension_policy = fp_policy.get("dimension_overrides", {}).get(dimension, {})
    if isinstance(dimension_policy, dict) and confidence in dimension_policy:
        return str(dimension_policy[confidence])
    return str(fp_policy.get("default_by_confidence", {}).get(confidence, "medium"))


def _source_surface_context(rel_path: str) -> str:
    normalized = rel_path.replace("\\", "/")
    policy = _load_runtime_policy()
    reference = policy.get("reference_surfaces", {})
    path_markers = set(reference.get("path_markers", []) or [])
    fixture_markers = set(reference.get("fixture_path_markers", []) or [])
    fixture_suffixes = tuple(reference.get("fixture_file_suffixes", []) or ())

    def has_marker(markers: set[str]) -> bool:
        parts = [part for part in normalized.split("/") if part]
        normalized_with_edges = f"/{normalized.strip('/')}/"
        for marker in markers:
            marker = marker.strip("/")
            if not marker:
                continue
            if "/" in marker and f"/{marker}/" in normalized_with_edges:
                return True
            if marker in parts:
                return True
        return False

    if has_marker(path_markers):
        return "docs_demo"
    if has_marker(fixture_markers) or normalized.endswith(fixture_suffixes):
        return "test_or_fixture"
    return "production_source"


def _calibrate_surface_context(item: dict[str, Any]) -> dict[str, Any]:
    calibrated = dict(item)
    context = _source_surface_context(str(calibrated.get("file") or ""))
    calibrated["source_context"] = context
    calibrated["actionability"] = "production_actionable"

    if context in {"docs_demo", "test_or_fixture"}:
        calibrated["actionability"] = "reference_only"
        calibrated["production_relevance"] = "reference_or_fixture_surface"
        if calibrated.get("calibration_lane") == "review_next" and int(calibrated.get("score", 0) or 0) < 8:
            calibrated["calibration_lane"] = "observe"
        if str(calibrated.get("false_positive_risk") or "") == "low":
            calibrated["false_positive_risk"] = "medium"
    return calibrated


def _line_for(content: str, needle: str) -> int:
    idx = content.find(needle)
    if idx < 0:
        return 1
    return content[:idx].count("\n") + 1


def _match_lines(content: str, pattern: re.Pattern[str]) -> list[int]:
    return [content[: match.start()].count("\n") + 1 for match in pattern.finditer(content)]


def _first_line(content: str, *patterns: re.Pattern[str]) -> int:
    for pattern in patterns:
        lines = _match_lines(content, pattern)
        if lines:
            return lines[0]
    return 1


def _import_line_for(content: str, imports: list[str]) -> int:
    wanted = set(imports)
    for match in IMPORT_RE.finditer(content):
        if match.group(1) in wanted:
            return content[: match.start()].count("\n") + 1
    return _first_line(content, IMPORT_RE)


def _react_mutation_contexts(atlas_features: set[str]) -> dict[str, list[int]]:
    contexts = {key: [] for key in ("render", "effect", "event", "module", "unresolved")}
    for feature in atlas_features:
        match = re.fullmatch(
            r"React:MutableAssignment:(render|effect|event|module|unresolved):(\d+)",
            str(feature),
        )
        if match:
            contexts[match.group(1)].append(int(match.group(2)))
    return {key: sorted(set(lines)) for key, lines in contexts.items()}


def _finding(
    project: str,
    rel_path: str,
    dimension: str,
    risk: str,
    evidence: str,
    score: int,
    action: str,
    evidence_kinds: set[str] | None = None,
    line: int = 1,
    runtime_confirmed: bool = False,
    evidence_spans: list[dict[str, Any]] | None = None,
    evidence_scope: str | None = None,
) -> dict[str, Any]:
    kinds = evidence_kinds or {"static"}
    item = {
        "project": project,
        "file": rel_path.replace("\\", "/"),
        "line": line,
        "dimension": dimension,
        "risk": risk,
        "risk_tier": _tier(score),
        "confidence": _confidence(score, kinds, runtime_confirmed),
        "score": score,
        "evidence_kinds": sorted(kinds),
        "evidence": evidence,
        "recommended_action": action,
    }
    if evidence_scope:
        item["evidence_scope"] = evidence_scope
    if evidence_spans:
        item["evidence_spans"] = evidence_spans
    item = attach_react_evidence_contract(
        item,
        evidence_kinds=kinds,
        line=line,
        current_confidence=item["confidence"],
    )
    item["calibration_lane"] = _calibration_lane(item)
    item["false_positive_risk"] = _false_positive_risk(item)
    return item


def _class_count(content: str) -> tuple[int, int]:
    total = 0
    responsive = 0
    for match in CSS_CLASS_RE.finditer(content):
        classes = (match.group(1) or match.group(2) or "").split()
        total += len(classes)
        responsive += sum(1 for cls in classes if RESPONSIVE_PREFIX_RE.search(cls))
    policy = _load_runtime_policy()
    names = [re.escape(name) for name in policy.get("class_composition_functions", CLASS_COMPOSITION_DEFAULTS)]
    if names:
        call_re = re.compile(rf"\b(?:{'|'.join(names)})\s*\((?P<args>[\s\S]{{0,1600}}?)\)", re.MULTILINE)
        for match in call_re.finditer(content):
            for literal in STRING_LITERAL_RE.findall(match.group("args") or ""):
                classes = literal.split()
                total += len(classes)
                responsive += sum(1 for cls in classes if RESPONSIVE_PREFIX_RE.search(cls))
    return total, responsive


def _has_nearby_test(project: str, rel_path: str, atlas_file: dict[str, Any] | None = None) -> bool:
    if isinstance(atlas_file, dict) and atlas_file.get("test_link"):
        return True
    path = (_project_root(project) / rel_path).resolve()
    stem = path.stem
    candidates = [
        path.with_name(f"{stem}.test{path.suffix}"),
        path.with_name(f"{stem}.spec{path.suffix}"),
        path.parent / "__tests__" / f"{stem}.test{path.suffix}",
        path.parent / "__tests__" / f"{stem}.spec{path.suffix}",
    ]
    return any(candidate.exists() for candidate in candidates)


def _ui_smoke_execution_index() -> dict[tuple[str, str], list[dict[str, Any]]]:
    payload = load_json_file(RAW_DIR / "ui_smoke_execution.json", {})
    runs = payload.get("runs", []) if isinstance(payload, dict) else []
    index: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    if not isinstance(runs, list):
        return index
    for run in runs:
        if not isinstance(run, dict):
            continue
        source = str(run.get("source") or "")
        target = str(run.get("target_path") or "").replace("\\", "/")
        if source and target:
            index[(source, target)].append(run)
        source_contract = str(run.get("source_contract_file") or "")
        if "::" in source_contract:
            contract_project, contract_file = source_contract.split("::", 1)
            contract_file = contract_file.replace("\\", "/")
            if contract_project and contract_file:
                index[(contract_project, contract_file)].append(run)
    return index


def _smoke_evidence_for(
    project: str,
    rel_path: str,
    smoke_index: dict[tuple[str, str], list[dict[str, Any]]],
) -> tuple[set[str], list[dict[str, Any]]]:
    normalized = rel_path.replace("\\", "/")
    matches = smoke_index.get((project, normalized), [])
    if not matches:
        return set(), []
    # The current UI smoke artifact proves template/readiness availability only.
    # It does not bind an executed assertion to a specific finding identity, so
    # even a caller-supplied "passed" status must not confirm every finding in
    # the same source file. A future execution receipt may promote only the
    # exact assertion/finding pair it proves.
    kinds = {"runtime_smoke_ready"}
    spans = [
        {
            "file": match.get("spec_path"),
            "line": 1,
            "source": "ui_smoke_execution",
            "status": match.get("status"),
            "route": match.get("route"),
            "execution_command": match.get("execution_command"),
        }
        for match in matches[:5]
    ]
    return kinds, spans


def analyze_runtime_intelligence_file(
    project: str,
    rel_path: str,
    content: str,
    atlas_file: dict[str, Any] | None = None,
    smoke_index: dict[tuple[str, str], list[dict[str, Any]]] | None = None,
) -> dict[str, Any] | None:
    if not _is_react_source(rel_path, content):
        return None

    normalized = rel_path.replace("\\", "/")
    imports = set(IMPORT_RE.findall(content))
    is_client = bool(CLIENT_DIRECTIVE_RE.search(content)) or normalized.endswith((".tsx", ".jsx")) and "useState(" in content
    findings: list[dict[str, Any]] = []
    file_evidence_kinds = atlas_evidence_kinds(atlas_file)
    smoke_kinds, smoke_spans = _smoke_evidence_for(project, normalized, smoke_index or {})
    runtime_surfaces = _load_runtime_policy().get("runtime_import_surfaces", {})
    heavy_policy = set(runtime_surfaces.get("heavy_client_imports", []) or [])
    server_policy = set(runtime_surfaces.get("server_only_imports", []) or [])

    heavy_imports = sorted(imp for imp in imports if imp in heavy_policy or any(imp.startswith(f"{heavy}/") for heavy in heavy_policy))
    server_imports = sorted(imp for imp in imports if imp in server_policy or any(imp.startswith(f"{server}/") for server in server_policy))
    if is_client and heavy_imports:
        score = min(10, 4 + len(heavy_imports) * 2)
        findings.append(
            _finding(
                project,
                normalized,
                "bundle_boundary",
                "client_boundary_pulls_heavy_dependency",
                f"client surface imports {', '.join(heavy_imports[:6])}",
                score,
                "Split the heavy dependency behind dynamic import, server component boundary, or lazy route-level chunk.",
                {"static", "dependency_graph"},
                line=_import_line_for(content, heavy_imports),
            )
        )
    if is_client and server_imports:
        findings.append(
            _finding(
                project,
                normalized,
                "bundle_boundary",
                "client_boundary_imports_server_only_module",
                f"client surface imports server-side modules: {', '.join(server_imports)}",
                10,
                "Move server-side code behind route handlers/server components and pass serialized data to the client.",
                {"static", "dependency_graph"},
                line=_import_line_for(content, server_imports),
            )
        )

    memo_count = len(MEMO_RE.findall(content))
    inline_factory_count = len(INLINE_FACTORY_RE.findall(content))
    compiler_risks: list[str] = []
    compiler_score = 0
    
    # Phase 1.3: Hook CFG integration
    atlas_features = set()
    if atlas_file:
        for s in atlas_file.get("symbols", []):
            if s.get("name") == "__file_meta__":
                atlas_features.update(s.get("features", []))
                break

    missing_deps = [f for f in atlas_features if f.startswith("Hook:MissingDeps:")]
    dynamic_deps = [f for f in atlas_features if f.startswith("Hook:DynamicDeps:")]

    if missing_deps:
        compiler_risks.append(f"hooks missing dependencies ({', '.join([f.split(':')[-1] for f in missing_deps])})")
        compiler_score += 5
    if dynamic_deps:
        compiler_risks.append(f"hooks with dynamic/non-literal dependencies ({', '.join([f.split(':')[-1] for f in dynamic_deps])})")
        compiler_score += 4

    mutation_contexts = _react_mutation_contexts(atlas_features)
    mutation_lines = mutation_contexts["render"]
    if mutation_lines:
        compiler_risks.append("syntax-AST observed mutable assignment in React render context")
        compiler_score += 6
    if mutation_contexts["unresolved"]:
        compiler_risks.append("syntax-AST observed mutable assignment with unresolved execution context")
        compiler_score += 1
    if compiler_risks:
        evidence_lines = (
            mutation_lines
            or mutation_contexts["unresolved"]
            or _match_lines(content, HOOK_RE)[:3]
            or [1]
        )
        evidence_source = (
            "typescript_syntax_ast"
            if mutation_lines or mutation_contexts["unresolved"]
            else "static_source_location"
        )
        evidence_spans = [
            {"file": normalized, "line": line_no, "source": evidence_source}
            for line_no in evidence_lines
        ]
        if mutation_lines:
            compiler_action = "Fix hook dependencies, remove render-time mutation, and prefer compiler-friendly pure components."
        elif missing_deps or dynamic_deps:
            compiler_action = "Fix hook dependency contracts and keep memoization boundaries compiler-friendly."
        else:
            compiler_action = "Resolve the assignment execution context before making a React Compiler readiness claim."
        item = _finding(
            project,
            normalized,
            "react_compiler_readiness",
            "compiler_static_contract_risk",
            "; ".join(compiler_risks),
            min(10, compiler_score),
            compiler_action,
            {"static", "atlas_feature"},
            line=evidence_lines[0],
            evidence_spans=evidence_spans,
        )
        item["confidence"] = (
            "confirmed"
            if atlas_file and (missing_deps or dynamic_deps or mutation_lines)
            else "probable"
        )
        findings.append(item)

    density_risks: list[str] = []
    density_score = 0
    if memo_count >= 6:
        density_risks.append(f"{memo_count} memoization hooks")
        density_score += 3
    if inline_factory_count >= 8 and memo_count == 0:
        density_risks.append(f"{inline_factory_count} render-local function factories")
        density_score += 3
    if density_risks:
        density_lines = _match_lines(content, MEMO_RE)[:3] or _match_lines(content, INLINE_FACTORY_RE)[:3] or [1]
        findings.append(_finding(
            project,
            normalized,
            "memoization_density",
            "manual_memoization_or_render_factory_density",
            "; ".join(density_risks),
            density_score,
            "Review memoization and render-local factory density as maintainability evidence; it is not independently a React Compiler blocker.",
            {"static"},
            line=density_lines[0],
            evidence_spans=[
                {"file": normalized, "line": line_no, "source": "static_density"}
                for line_no in density_lines
            ],
        ))

    class_count, responsive_count = _class_count(content)
    css_risks: list[str] = []
    css_score = 0
    if class_count >= 80:
        css_risks.append(f"{class_count} utility classes in one file")
        css_score += 4
    if class_count >= 35 and responsive_count == 0:
        css_risks.append("dense utility styling without responsive breakpoint contract")
        css_score += 3
    if re.search(r"(?:dark:|data-\[theme|theme\.)", content) and "light" not in content.lower():
        css_risks.append("theme-specific styling without visible opposite-mode contract")
        css_score += 2
    if css_risks:
        findings.append(
            _finding(
                project,
                normalized,
                "css_token_intelligence",
                "responsive_or_theme_drift_risk",
                "; ".join(css_risks),
                css_score,
                "Extract repeat visual recipes into variants/tokens and add responsive/theme contract before reuse.",
                {"static", "design_system"},
                line=_first_line(content, CSS_CLASS_RE),
                evidence_scope="file_level_css_token_aggregate",
            )
        )

    dialog_count = len(DIALOG_RE.findall(content))
    if dialog_count and not FOCUS_JOURNEY_RE.search(content):
        findings.append(
            _finding(
                project,
                normalized,
                "accessibility_journey",
                "overlay_without_keyboard_focus_journey",
                f"{dialog_count} overlay/dialog components without visible focus lifecycle",
                min(9, 4 + dialog_count * 2),
                "Add focus entry, escape handling, tab cycle, and route-return focus expectations to the smoke spec.",
                {"static", "a11y_journey"},
                line=_first_line(content, DIALOG_RE),
            )
        )

    mutation_matches = MUTATION_RE.findall(content)
    if mutation_matches:
        mutation_score = 0
        mutation_risks: list[str] = []
        if not INVALIDATE_RE.search(content):
            mutation_score += 4
            mutation_risks.append("mutation has no visible query invalidation")
        if QUERY_RE.search(content) and not OPTIMISTIC_RE.search(content):
            mutation_score += 3
            mutation_risks.append("same surface reads queries but has no optimistic/cache reconciliation contract")
        if "toast" not in content.lower() and "error" not in content.lower():
            mutation_score += 2
            mutation_risks.append("mutation lacks visible user error/success feedback")
        if mutation_risks:
            findings.append(
                _finding(
                    project,
                    normalized,
                    "data_mutation_blast_radius",
                    "mutation_cache_or_feedback_gap",
                    "; ".join(mutation_risks),
                    mutation_score,
                    "Map mutation to affected query keys, optimistic state, rollback, and user feedback before import.",
                    {"static", "state_data_graph"},
                    line=_first_line(content, MUTATION_RE),
                )
            )

    route_like = bool(ROUTE_FILE_RE.search(normalized))
    protected_like = bool(AUTH_ROUTE_RE.search(normalized)) or any(word in normalized.lower() for word in ("admin", "billing", "settings", "account"))
    if protected_like:
        if not AUTH_GUARD_RE.search(content):
            findings.append(
                _finding(
                    project,
                    normalized,
                    "auth_permission_boundary",
                    "protected_surface_without_visible_auth_guard",
                    "path/name suggests protected surface but no auth/permission guard was detected",
                    8 if route_like else 5,
                    "Require server-side auth/role verification and mirror it with UI-level affordance checks.",
                    {"static", "route_contract"},
                    line=1 if route_like else _first_line(content, AUTH_ROUTE_RE),
                    evidence_scope="file_level_path_contract",
                )
            )
        elif CLIENT_AUTH_ONLY_RE.search(content) and not re.search(r"\b(?:getServerSession|auth\(|requireAuth|currentUser)\b", content):
            findings.append(
                _finding(
                    project,
                    normalized,
                    "auth_permission_boundary",
                    "protected_surface_appears_client_guarded_only",
                    "auth signal is client/session-storage oriented without visible server-side guard",
                    6,
                    "Add server-side guard at route/API/action boundary; client guard is UX only.",
                    {"static", "route_contract"},
                    line=_first_line(content, CLIENT_AUTH_ONLY_RE),
                )
            )

    if route_like and ASYNC_SURFACE_RE.search(content) and not ERROR_BOUNDARY_RE.search(content):
        findings.append(
            _finding(
                project,
                normalized,
                "error_recovery_map",
                "async_route_without_visible_recovery_boundary",
                "route-level async/data surface lacks visible error boundary, catch path, or reset contract",
                6,
                "Pair route with error/loading/retry UX and include it in browser smoke coverage.",
                {"static", "route_contract"},
                line=_first_line(content, ASYNC_SURFACE_RE),
            )
        )

    permission_hits = sorted(set(match.group(0) for match in BROWSER_PERMISSION_API_RE.finditer(content)))
    if permission_hits and not PERMISSION_FALLBACK_RE.search(content):
        findings.append(
            _finding(
                project,
                normalized,
                "browser_permission_contract",
                "browser_permission_api_without_visible_denial_fallback",
                f"browser APIs without visible denial/error UX: {', '.join(permission_hits[:6])}",
                min(9, 4 + len(permission_hits) * 2),
                "Add permission-denied/error fallback UX and browser capability checks before relying on this interaction.",
                {"static", "browser_api"},
                line=_first_line(content, BROWSER_PERMISSION_API_RE),
            )
        )

    has_test = _has_nearby_test(project, normalized, atlas_file)
    risk_surface = any(item["risk_tier"] in {"high", "medium"} for item in findings)
    if risk_surface and not has_test:
        dims = sorted({item["dimension"] for item in findings})
        findings.append(
            _finding(
                project,
                normalized,
                "test_coverage_intent",
                "risk_surface_without_nearby_test_intent",
                f"risk dimensions without nearby test: {', '.join(dims)}",
                4,
                "Add the smallest matching test: contract/unit for logic, integration for data flow, browser smoke for UI journey.",
                {"static", "test_intent"},
                line=int(findings[0].get("line") or 1) if findings else 1,
                evidence_scope="derived_from_prior_finding",
            )
        )

    extra_evidence_kinds = file_evidence_kinds | smoke_kinds
    if extra_evidence_kinds:
        findings = [
            attach_react_evidence_contract(
                {
                    **dict(item),
                    "smoke_execution": smoke_spans if smoke_spans else item.get("smoke_execution"),
                    "evidence_spans": (item.get("evidence_spans") or []) + smoke_spans,
                },
                evidence_kinds=set(item.get("evidence_kinds", [])) | extra_evidence_kinds,
                line=int(item.get("line", 1) or 1),
                current_confidence=str(item.get("confidence") or ""),
            )
            for item in findings
        ]

    return {
        "project": project,
        "file": normalized,
        "signals": {
            "client_boundary": is_client,
            "imports": sorted(imports)[:80],
            "class_count": class_count,
            "responsive_class_count": responsive_count,
            "has_nearby_test": has_test,
            "route_like": route_like,
        },
        "findings": findings,
    }


def run_react_runtime_intelligence() -> dict[str, Any]:
    logger.info("Analyzing React runtime intelligence and calibration contracts...")
    started = time.perf_counter()
    atlas, _execution_scope = project_runtime_atlas(load_atlas_data())
    atlas_loaded_at = time.perf_counter()
    ecosystem = load_json_file(RAW_DIR / "react_ecosystem_analysis.json", {})
    smoke_index = _ui_smoke_execution_index()
    rows: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    source_cache: dict[str, str] = {}
    project_profile: dict[str, dict[str, Any]] = {}

    if isinstance(atlas, dict):
        for project, pdata in atlas.items():
            project_started = time.perf_counter()
            project_rows_before = len(rows)
            project_findings_before = len(findings)
            project_files = (pdata or {}).get("files", {}) if isinstance(pdata, dict) else {}
            for rel_path, fdata in project_files.items():
                content = _read_project_file(
                    project,
                    str(rel_path),
                    fdata if isinstance(fdata, dict) else {},
                    source_cache,
                )
                row = analyze_runtime_intelligence_file(
                    project,
                    str(rel_path),
                    content,
                    fdata if isinstance(fdata, dict) else {},
                    smoke_index,
                )
                if not row:
                    continue
                rows.append({key: value for key, value in row.items() if key != "findings"})
                findings.extend(row["findings"])
            project_profile[str(project)] = {
                "candidate_files": len(project_files),
                "runtime_files": len(rows) - project_rows_before,
                "findings": len(findings) - project_findings_before,
                "seconds": round(time.perf_counter() - project_started, 3),
            }
            logger.info(
                "[REACT_RUNTIME_PROFILE] project=%s %s",
                project,
                project_profile[str(project)],
            )

    ecosystem_findings = ecosystem.get("findings", []) if isinstance(ecosystem, dict) else []
    calibrated = _calibrate_with_ecosystem(findings, ecosystem_findings)

    dimension_counts = Counter(str(item.get("dimension")) for item in calibrated)
    confidence_counts = Counter(str(item.get("confidence")) for item in calibrated)
    calibration_counts = Counter(str(item.get("calibration_lane")) for item in calibrated)
    false_positive_counts = Counter(str(item.get("false_positive_risk")) for item in calibrated)
    tier_counts = Counter(str(item.get("risk_tier")) for item in calibrated)
    by_project = Counter(str(item.get("project")) for item in calibrated)

    sorted_findings = sorted(calibrated, key=lambda item: (-int(item.get("score", 0)), item.get("confidence", ""), item.get("project", ""), item.get("file", "")))
    primary_limit = report_surface_limit("react_runtime_intelligence.primary_findings")
    summary = {
        "files_analyzed": len(rows),
        "findings": len(calibrated),
        "dimension_counts": dict(dimension_counts),
        "confidence_counts": dict(confidence_counts),
        "calibration_counts": dict(calibration_counts),
        "false_positive_risk_counts": dict(false_positive_counts),
        "risk_tiers": dict(tier_counts),
        "high_risk": tier_counts.get("high", 0),
        "medium_risk": tier_counts.get("medium", 0),
        "confirmed_or_likely": confidence_counts.get("confirmed", 0) + confidence_counts.get("likely", 0),
        "act_now": calibration_counts.get("act_now", 0),
        "review_next": calibration_counts.get("review_next", 0),
        "needs_runtime_probe": calibration_counts.get("needs_runtime_probe", 0),
        "by_project": dict(sorted(by_project.items())),
        "primary_report_finding_limit": primary_limit,
        "truncated_in_primary_report": max(0, len(sorted_findings) - primary_limit),
        "full_artifact": "react_runtime_intelligence_full.json",
    }
    payload = {
        "meta": {"kind": "react_runtime_intelligence", "version": "v1"},
        "summary": summary,
        "calibration": _calibration_summary(calibrated),
        "files": rows,
        "findings": sorted_findings[:primary_limit],
    }
    full_payload = {
        **payload,
        "meta": {"kind": "react_runtime_intelligence_full", "version": "v1", "source": "react_runtime_intelligence"},
        "summary": {
            **summary,
            "primary_report_finding_limit": len(sorted_findings),
            "truncated_in_primary_report": 0,
        },
        "findings": sorted_findings,
    }
    save_json_atomic(RAW_DIR / "react_runtime_intelligence.json", payload)
    save_json_atomic(RAW_DIR / "react_runtime_intelligence_full.json", full_payload)
    save_text_atomic(REPORTS_DIR / "react_runtime_intelligence.md", _render_markdown(payload))
    logger.info(
        "[REACT_RUNTIME_PROFILE] total=%s",
        {
            "atlas_load_seconds": round(atlas_loaded_at - started, 3),
            "scan_seconds": round(time.perf_counter() - atlas_loaded_at, 3),
            "files_analyzed": len(rows),
            "findings": len(calibrated),
            "projects": project_profile,
        },
    )
    return payload


def _calibration_summary(findings: list[dict[str, Any]]) -> dict[str, Any]:
    policy = _load_runtime_policy()
    lanes: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in findings:
        lanes[str(item.get("calibration_lane") or "observe")].append(item)

    return {
        "policy": {
            "source": policy.get("policy_source"),
            "meta": policy.get("meta", {}),
            "calibration_lanes": policy.get("calibration_lanes", {}),
            "actionable_dimensions": policy.get("actionable_dimensions", []),
            "false_positive_policy": policy.get("false_positive_policy", {}),
        },
        "lane_counts": {lane: len(items) for lane, items in sorted(lanes.items())},
        "top_act_now": [
            _calibration_brief(item)
            for item in sorted(lanes.get("act_now", []), key=lambda row: (-int(row.get("score", 0)), row.get("project", ""), row.get("file", "")))[:25]
        ],
        "top_runtime_probe": [
            _calibration_brief(item)
            for item in sorted(lanes.get("needs_runtime_probe", []), key=lambda row: (-int(row.get("score", 0)), row.get("project", ""), row.get("file", "")))[:25]
        ],
    }


def _calibration_brief(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "project": item.get("project"),
        "file": item.get("file"),
        "dimension": item.get("dimension"),
        "risk": item.get("risk"),
        "score": item.get("score"),
        "confidence": item.get("confidence"),
        "false_positive_risk": item.get("false_positive_risk"),
        "recommended_action": item.get("recommended_action"),
    }


def _calibrate_with_ecosystem(runtime_findings: list[dict[str, Any]], ecosystem_findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ecosystem_by_file: dict[tuple[str, str], set[str]] = defaultdict(set)
    for item in ecosystem_findings:
        if not isinstance(item, dict):
            continue
        ecosystem_by_file[(str(item.get("project")), str(item.get("file")))].add(str(item.get("dimension")))

    calibrated: list[dict[str, Any]] = []
    for item in runtime_findings:
        key = (str(item.get("project")), str(item.get("file")))
        dimensions = ecosystem_by_file.get(key, set())
        if dimensions:
            evidence = set(item.get("evidence_kinds", []))
            evidence.add("react_ecosystem_corroboration")
            item = dict(item)
            item["evidence_kinds"] = sorted(evidence)
            if item.get("confidence") == "probable" and item.get("risk_tier") in {"medium", "high"}:
                item["confidence"] = "likely"
            item["corroborating_dimensions"] = sorted(dimensions)
        item = dict(item)
        item["calibration_lane"] = _calibration_lane(item)
        item["false_positive_risk"] = _false_positive_risk(item)
        item = _calibrate_surface_context(item)
        calibrated.append(item)
    return calibrated


def _render_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    calibration = payload.get("calibration", {}) if isinstance(payload.get("calibration"), dict) else {}
    lines = [
        "# React Runtime Intelligence",
        "",
        f"- Files analyzed: `{summary.get('files_analyzed', 0)}`",
        f"- Findings: `{summary.get('findings', 0)}`",
        f"- High risk: `{summary.get('high_risk', 0)}`",
        f"- Medium risk: `{summary.get('medium_risk', 0)}`",
        f"- Confirmed or likely: `{summary.get('confirmed_or_likely', 0)}`",
        f"- Act now: `{summary.get('act_now', 0)}`",
        f"- Review next: `{summary.get('review_next', 0)}`",
        f"- Needs runtime probe: `{summary.get('needs_runtime_probe', 0)}`",
        "",
        "## Dimensions",
        "",
        "| Dimension | Count |",
        "|---|---:|",
    ]
    for dimension, count in Counter(summary.get("dimension_counts", {}) or {}).most_common():
        lines.append(f"| `{dimension}` | {count} |")

    lines.extend(["", "## Confidence", "", "| Confidence | Count |", "|---|---:|"])
    for confidence, count in Counter(summary.get("confidence_counts", {}) or {}).most_common():
        lines.append(f"| `{confidence}` | {count} |")

    lines.extend(["", "## Calibration Lanes", "", "| Lane | Count |", "|---|---:|"])
    for lane, count in Counter(summary.get("calibration_counts", {}) or {}).most_common():
        lines.append(f"| `{lane}` | {count} |")

    lines.extend(["", "## False Positive Risk", "", "| Risk | Count |", "|---|---:|"])
    for risk, count in Counter(summary.get("false_positive_risk_counts", {}) or {}).most_common():
        lines.append(f"| `{risk}` | {count} |")

    lines.extend(["", "## Act Now", "", "| Project | File | Confidence | Dimension | Risk | Action |", "|---|---|---|---|---|---|"])
    for item in calibration.get("top_act_now", [])[:25]:
        lines.append(
            f"| `{item.get('project')}` | `{item.get('file')}` | `{item.get('confidence')}` | "
            f"`{item.get('dimension')}` | `{item.get('risk')}` | {item.get('recommended_action', '-')} |"
        )

    lines.extend(["", "## Top Findings", "", "| Project | File | Tier | Confidence | Lane | FP Risk | Dimension | Risk | Evidence | Action |", "|---|---|---|---|---|---|---|---|---|---|"])
    for item in payload.get("findings", [])[:100]:
        lines.append(
            f"| `{item.get('project')}` | `{item.get('file')}` | `{item.get('risk_tier')}` | `{item.get('confidence')}` | "
            f"`{item.get('calibration_lane')}` | `{item.get('false_positive_risk')}` | `{item.get('dimension')}` | "
            f"`{item.get('risk')}` | {item.get('evidence', '-')} | {item.get('recommended_action', '-')} |"
        )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    run_react_runtime_intelligence()
