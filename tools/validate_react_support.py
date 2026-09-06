from __future__ import annotations

import hashlib
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Any

TOOLS_DIR = Path(__file__).resolve().parent
CODE_MAPS_DIR = TOOLS_DIR.parent
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))

from tools.core.atlas_io import load_atlas_data
from tools.core.json_io import load_json_file
from tools.core.config import CONFIG_FILE
CONFIG_PATH = CONFIG_FILE

from tools.core.config import (
    CONFIG_DIR,
    DYNAMIC_CONFIG,
    ROOT,
    REPORTS_DIR,
    RAW_DIR,
    ensure_output_dir,
    save_json_atomic,
    save_text_atomic,
)
from tools.core.projects_registry import project_display_name
from tools.core.source_files import is_analysis_source_file
from tools.core.workspace_mode import get_workspace_mode
from tools.core.stdio import best_effort_print


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


from tools.validate_react._react_capabilities import CAPABILITIES, CAPABILITY_BACKLOG
from tools.validate_react._react_probe import (
    run_probe,
    _collect_workspace_index,
    _find_matches,
    _scan_source_content,
    _project_root_map,
    _project_role_map,
    _load_package_json_for_root,
    _merge_package_deps,
    _iter_config_files_for_root,
    _collect_project_index,
)
import tools.core.config as runtime_config

PROGRESS_INTERVAL_SECONDS = 10.0


def _log(message: str) -> None:
    best_effort_print(f"[react-support] {message}", flush=True)


def _runtime_project_roots(project_roots: dict[str, Path]) -> tuple[dict[str, Path], list[str]]:
    requested = sorted(
        {
            str(item).upper()
            for item in (runtime_config.PROJECT_FILTER or [])
            if str(item).strip()
        }
    )
    if not requested:
        return dict(project_roots), []
    allowed = set(requested)
    return (
        {key: value for key, value in project_roots.items() if key.upper() in allowed},
        requested,
    )


def _runtime_atlas_payload(atlas_payload: dict, requested_projects: list[str]) -> dict:
    """Keep workspace-level React claims inside the same runtime project scope."""
    if not requested_projects or not isinstance(atlas_payload, dict):
        return atlas_payload
    allowed = {str(item).upper() for item in requested_projects}
    return {
        key: value
        for key, value in atlas_payload.items()
        if str(key).upper() in allowed
    }


def _stable_hash(payload: Any) -> str:
    try:
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    except TypeError:
        encoded = repr(payload).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _without_volatile_artifact_fields(payload: Any, volatile_keys: set[str]) -> Any:
    if isinstance(payload, dict):
        return {
            key: _without_volatile_artifact_fields(value, volatile_keys)
            for key, value in payload.items()
            if str(key) not in volatile_keys
        }
    if isinstance(payload, list):
        return [_without_volatile_artifact_fields(item, volatile_keys) for item in payload]
    return payload


def _react_support_cache_key(
    package_json: dict,
    discovery_payload: dict,
    atlas_payload: dict,
    state_flow_payload: dict,
    project_dna_payload: dict,
) -> dict[str, Any]:
    atlas_commit = load_json_file(RAW_DIR / "atlas_commit.json", {}) or {}
    capability_signature = [
        {
            "key": capability.key,
            "category": capability.category,
            "support": capability.support,
            "package_names": list(capability.package_names),
            "code_patterns": list(capability.code_patterns),
        }
        for capability in CAPABILITIES
    ]
    key_inputs = {
        "atlas_snapshot_id": atlas_commit.get("snapshot_id") if isinstance(atlas_commit, dict) else None,
        "atlas_sha256": atlas_commit.get("atlas_sha256") if isinstance(atlas_commit, dict) else None,
        "atlas_source_fingerprint": atlas_commit.get("source_fingerprint") if isinstance(atlas_commit, dict) else None,
        "atlas_configuration_fingerprint": atlas_commit.get("configuration_fingerprint") if isinstance(atlas_commit, dict) else None,
        "package_json_sha256": _stable_hash(package_json if isinstance(package_json, dict) else {}),
        "discovery_sha256": _stable_hash(discovery_payload if isinstance(discovery_payload, dict) else {}),
        "state_flow_sha256": _stable_hash(
            _without_volatile_artifact_fields(
                state_flow_payload if isinstance(state_flow_payload, dict) else {},
                {"run_meta"},
            )
        ),
        "project_dna_sha256": _stable_hash(project_dna_payload if isinstance(project_dna_payload, dict) else {}),
        "capability_signature_sha256": _stable_hash(capability_signature),
        "runtime_project_filter": sorted(str(item) for item in (runtime_config.PROJECT_FILTER or [])),
    }
    return {
        "algorithm": "react_support_snapshot_v1",
        "fingerprint": _stable_hash(key_inputs),
        "inputs": key_inputs,
    }


def _project_dna_react_frameworks(payload: dict[str, Any]) -> set[str]:
    policy = load_json_file(CONFIG_DIR / "project_dna_profile_policy.json", {}) or {}
    activation_intents = policy.get("activation_intent", {}) if isinstance(policy, dict) else {}
    react_framework_ids = {
        str(item).lower()
        for item in activation_intents.get("react_surgical_intelligence", [])
        if str(item).strip()
    } if isinstance(activation_intents, dict) else set()
    frameworks: set[str] = set()
    for project in payload.get("projects", []) if isinstance(payload, dict) else []:
        if not isinstance(project, dict):
            continue
        for framework in project.get("frameworks", []) or []:
            value = framework.get("id") if isinstance(framework, dict) else framework
            if value:
                frameworks.add(str(value).lower())
    return frameworks & react_framework_ids


def _not_applicable_payload(project_dna_payload: dict[str, Any]) -> dict[str, Any]:
    frameworks = _project_dna_react_frameworks(project_dna_payload)
    reason = {
        "status": "NOT_APPLICABLE",
        "rule": "project_dna_react_framework_signal",
        "project_dna_frameworks": sorted(frameworks),
        "reason": "Project DNA contains no React runtime framework evidence; source vocabulary is not evaluated as ecosystem proof.",
    }
    matrix = [
        {
            "key": capability.key,
            "category": capability.category,
            "label": capability.label,
            "present_in_repo": False,
            "support": "not_applicable",
            "intrinsic_support": capability.support,
            "support_basis": reason["reason"],
            "gap_note": "Not applicable until Project DNA identifies a React runtime framework.",
            "evidence": {"package_hits": [], "code_hits": [], "artifact_hits": ["project_dna:no_react_runtime"]},
        }
        for capability in CAPABILITIES
    ]
    summary = {"repo_present": 0, "detected": 0, "partial": 0, "missing": 0}
    by_project = {}
    for project in project_dna_payload.get("projects", []) if isinstance(project_dna_payload, dict) else []:
        if not isinstance(project, dict):
            continue
        project_id = str(project.get("project") or "MAIN")
        by_project[project_id] = {
            "role": "host" if project_id == "MAIN" else "companion",
            "display_name": str(project.get("display_name") or project_id),
            "project_root": ",".join(str(root) for root in project.get("roots", []) or []),
            "source_file_count": int(project.get("source_file_count") or 0),
            "summary": dict(summary),
            "confidence": _compute_support_confidence(summary, matrix),
            "capabilities": matrix,
            "backlog": [],
        }
    return {
        "workspace_root": str(ROOT),
        "workspace_mode": get_workspace_mode(),
        "source_file_count": sum(int(project.get("source_file_count") or 0) for project in project_dna_payload.get("projects", []) if isinstance(project, dict)),
        "summary": summary,
        "confidence": _compute_support_confidence(summary, matrix),
        "cache": {"status": "not_applicable", "algorithm": "react_support_snapshot_v1", "fingerprint": _stable_hash(reason), "inputs": {"project_dna_frameworks": sorted(frameworks)}},
        "applicability": reason,
        "capabilities": matrix,
        "by_project": by_project or {"MAIN": {"role": "host", "display_name": "MAIN", "project_root": ".", "source_file_count": 0, "summary": summary, "confidence": _compute_support_confidence(summary, matrix), "capabilities": matrix, "backlog": []}},
        "backlog": [],
    }

def _cached_react_support_payload(cache_key: dict[str, Any]) -> dict[str, Any] | None:
    cached = load_json_file(RAW_DIR / "react_support_matrix.json", {}) or {}
    if not isinstance(cached, dict):
        return None
    cache = cached.get("cache") if isinstance(cached.get("cache"), dict) else {}
    if cache.get("fingerprint") != cache_key.get("fingerprint"):
        return None
    if not isinstance(cached.get("capabilities"), list) or not isinstance(cached.get("by_project"), dict):
        return None
    cached["cache"] = {
        **cache,
        "status": "hit",
        "algorithm": cache_key.get("algorithm"),
        "fingerprint": cache_key.get("fingerprint"),
        "inputs": cache_key.get("inputs", {}),
    }
    return cached



def _collect_atlas_features(atlas_payload: dict) -> set[str]:
    features: set[str] = set()
    if not isinstance(atlas_payload, dict):
        return features

    project_payloads = []
    if "files" in atlas_payload:
        project_payloads.append(atlas_payload)
    else:
        project_payloads.extend(
            payload
            for payload in atlas_payload.values()
            if isinstance(payload, dict) and isinstance(payload.get("files"), dict)
        )

    for project_payload in project_payloads:
        files = project_payload.get("files")
        if not isinstance(files, dict):
            continue

        for file_payload in files.values():
            if not isinstance(file_payload, dict):
                continue
            file_features = file_payload.get("features") or []
            if isinstance(file_features, list):
                for feature in file_features:
                    if isinstance(feature, str):
                        features.add(feature)

            for symbol in file_payload.get("symbols") or []:
                if not isinstance(symbol, dict):
                    continue
                for feature in symbol.get("features") or []:
                    if isinstance(feature, str):
                        features.add(feature)
    return features


def _collect_project_atlas_features(atlas_payload: dict, project_name: str) -> set[str]:
    if not isinstance(atlas_payload, dict):
        return set()
    project_payload = atlas_payload.get(project_name, {})
    if not isinstance(project_payload, dict):
        return set()
    return _collect_atlas_features(project_payload)


def _count_state_flow_entries_for_project(state_flow_payload: dict, key: str, project_name: str) -> int:
    if not isinstance(state_flow_payload, dict):
        return 0
    payload = state_flow_payload.get(key, {}) or {}
    if not isinstance(payload, dict):
        return 0
    prefix = f"{project_name}::"
    return sum(1 for entry_key in payload.keys() if str(entry_key).startswith(prefix))


def _index_state_flow_entries_by_project(state_flow_payload: dict, key: str) -> dict[str, int]:
    if not isinstance(state_flow_payload, dict):
        return {}
    payload = state_flow_payload.get(key, {}) or {}
    if not isinstance(payload, dict):
        return {}

    counts: dict[str, int] = {}
    for entry_key in payload.keys():
        project_name = str(entry_key).split("::", 1)[0]
        if not project_name:
            continue
        counts[project_name] = counts.get(project_name, 0) + 1
    return counts


def _collect_feature_hits(atlas_features: set[str], wanted: tuple[str, ...], prefix: str = "atlas") -> list[str]:
    hits = sorted(feature for feature in wanted if feature in atlas_features)
    return [f"{prefix}:{feature}" for feature in hits]


def _evaluate_capabilities(
    deps: dict,
    source_files: list[str],
    source_entries: list[tuple[str, Path]],
    config_files: list[str],
    atlas_features: set[str],
    zustand_store_count: int,
    tanstack_query_count: int,
    tanstack_mutation_count: int,
    text_cache: dict[Path, str] | None = None,
    scope_label: str = "workspace",
) -> tuple[list[dict], dict]:
    matrix = []
    summary = {"repo_present": 0, "detected": 0, "partial": 0, "missing": 0}
    shared_text_cache = text_cache if text_cache is not None else {}
    started = time.perf_counter()
    last_progress = started
    total_capabilities = len(CAPABILITIES)
    _log(
        f"START evaluate scope={scope_label} capabilities={total_capabilities} "
        f"source_entries={len(source_entries)} config_files={len(config_files)}"
    )

    for index, capability in enumerate(CAPABILITIES, start=1):
        now = time.perf_counter()
        if index == 1 or now - last_progress >= PROGRESS_INTERVAL_SECONDS:
            _log(
                f"RUNNING scope={scope_label} capability={index}/{total_capabilities} "
                f"key={capability.key} elapsed={now - started:.0f}s"
            )
            last_progress = now
        package_hits = [name for name in capability.package_names if name in deps]
        search_space = list(source_files)
        if capability.key in {"bundler_workspace", "testing_stack", "styling_systems"}:
            search_space.extend(config_files)
        support = capability.support
        support_basis = capability.support_basis
        gap_note = capability.gap_note

        artifact_hits: list[str] = []
        code_hits: list[str] = []
        require_artifact_bias = False

        if capability.key == "react_router_runtime":
            artifact_hits.extend(
                _collect_feature_hits(
                    atlas_features,
                    ("RouterConfig", "RouteLoader", "RouteAction", "RouteLazy", "RouteRedirect"),
                )
            )
            present_in_repo = bool(package_hits or code_hits or artifact_hits)
        elif capability.key == "tanstack_query":
            if tanstack_query_count:
                artifact_hits.append(f"state_flow:tanstack_queries={tanstack_query_count}")
            if tanstack_mutation_count:
                artifact_hits.append(f"state_flow:tanstack_mutations={tanstack_mutation_count}")
            present_in_repo = bool(package_hits or code_hits or artifact_hits)
        elif capability.key == "redux_toolkit":
            artifact_hits.extend(
                _collect_feature_hits(
                    atlas_features,
                    ("ReduxStore", "ReduxSlice", "ReduxAsyncThunk"),
                )
            )
            present_in_repo = bool(package_hits or code_hits or artifact_hits)
        elif capability.key == "zustand":
            if zustand_store_count:
                artifact_hits.append(f"state_flow:zustand_stores={zustand_store_count}")
            present_in_repo = bool(package_hits or code_hits or artifact_hits)
        elif capability.key == "context_error_suspense":
            require_artifact_bias = True
            artifact_hits.extend(
                _collect_feature_hits(
                    atlas_features,
                    ("ReactContext", "ReactProvider", "SuspenseBoundary", "ErrorBoundary"),
                )
            )
            present_in_repo = bool(package_hits or code_hits or artifact_hits)
        elif capability.key == "forms_validation":
            require_artifact_bias = True
            artifact_hits.extend(
                _collect_feature_hits(
                    atlas_features,
                    ("ReactForm", "FormResolver", "ValidationSchema", "ZodSchema", "FormFieldRegister", "FormSubmitHandler"),
                )
            )
            present_in_repo = bool(package_hits or code_hits or artifact_hits)
        elif capability.key == "api_clients_network":
            require_artifact_bias = True
            artifact_hits.extend(
                _collect_feature_hits(
                    atlas_features,
                    ("ApiClient", "ApiContract", "ApiInterceptor", "ApiTransport", "ApiResponseContract"),
                )
            )
            present_in_repo = bool(package_hits or code_hits or artifact_hits)
        elif capability.key == "testing_stack":
            artifact_hits.extend(
                _collect_feature_hits(
                    atlas_features,
                    ("TestRuntime", "JestRuntime", "CypressRuntime", "PlaywrightRuntime", "VitestRuntime", "TestingLibrary", "UnitTest", "E2ETest"),
                )
            )
            present_in_repo = bool(package_hits or code_hits or artifact_hits)
            if not artifact_hits:
                support = "partial"
                support_basis = "Discovery sees test dependencies and file patterns, but the dedicated test capability contract is not yet present in artifacts."
                gap_note = "Needs explicit TestRuntime/JestRuntime/CypressRuntime artifact signals."
        elif capability.key == "styling_systems":
            require_artifact_bias = True
            artifact_hits.extend(
                _collect_feature_hits(
                    atlas_features,
                    ("StyleSystem", "CssModule", "SassModule", "UiKit", "DesignToken"),
                )
            )
            present_in_repo = bool(package_hits or code_hits or artifact_hits)
        else:
            present_in_repo = bool(package_hits)

        if package_hits or artifact_hits:
            path_hits = _find_matches(search_space, capability.code_patterns)
            code_hits = (
                path_hits
                if path_hits
                else (
                    _scan_source_content(source_entries, capability.code_patterns, text_cache=shared_text_cache)
                    if package_hits and not artifact_hits
                    else []
                )
            )
        else:
            path_hits = _find_matches(search_space, capability.code_patterns)
            code_hits = (
                path_hits
                if path_hits
                else _scan_source_content(source_entries, capability.code_patterns, text_cache=shared_text_cache)
            )
            present_in_repo = bool(present_in_repo or code_hits)

        if capability.key == "react_router_runtime":
            strict_router_features = {"atlas:RouterConfig", "atlas:RouteLazy", "atlas:RouteRedirect"}
            strict_router_patterns = (
                "react-router-dom",
                "from 'react-router",
                'from "react-router',
                "createBrowserRouter",
                "createMemoryRouter",
                "RouterProvider",
            )
            strict_router_code_hits = (
                _find_matches(search_space, strict_router_patterns)
                if artifact_hits
                else _scan_source_content(source_entries, strict_router_patterns, text_cache=shared_text_cache)
            )
            strict_artifact_hits = [hit for hit in artifact_hits if hit in strict_router_features]
            code_hits = sorted(set(strict_router_code_hits))
            artifact_hits = sorted(set(strict_artifact_hits + [hit for hit in artifact_hits if hit in strict_router_features]))
            present_in_repo = bool(package_hits or code_hits or artifact_hits)

        if capability.key == "tanstack_query":
            strict_tanstack_patterns = (
                "@tanstack/react-query",
                "queryKey:",
                "mutationKey:",
                "invalidateQueries",
                "prefetchQuery",
                "getQueryData",
                "getQueryState",
                "useSuspenseQuery",
                "QueryClient",
            )
            strict_tanstack_code_hits = (
                _find_matches(search_space, strict_tanstack_patterns)
                if artifact_hits
                else _scan_source_content(source_entries, strict_tanstack_patterns, text_cache=shared_text_cache)
            )
            code_hits = sorted(set(strict_tanstack_code_hits))
            if not package_hits and not code_hits:
                artifact_hits = []
            present_in_repo = bool(package_hits or code_hits or artifact_hits)

        if capability.key == "forms_validation":
            schema_only_packages = {"zod", "yup"}
            runtime_form_packages = {"react-hook-form", "@hookform/resolvers", "formik"}
            has_runtime_form_package = any(pkg in runtime_form_packages for pkg in package_hits)
            has_schema_only_package = any(pkg in schema_only_packages for pkg in package_hits)
            if has_schema_only_package and not has_runtime_form_package and not code_hits and not artifact_hits:
                present_in_repo = False

        if require_artifact_bias and present_in_repo and not artifact_hits:
            deterministic_capability_keys = {"forms_validation", "api_clients_network", "context_error_suspense", "styling_systems"}
            has_strong_deterministic_signal = bool(
                (package_hits and code_hits)
                or len(code_hits) >= 2
                or (capability.key in deterministic_capability_keys and bool(code_hits))
            )
            if has_strong_deterministic_signal:
                support = "detected"
                support_basis = (
                    "Deterministic package+code evidence is strong enough for detected support "
                    "even when AST artifact features are not emitted yet."
                )
                gap_note = (
                    "Artifact-level signals can still be enriched, but capability detection is "
                    "already reliable from deterministic evidence."
                )
            else:
                support = "partial"
                if capability.key == "forms_validation":
                    support_basis = (
                        "Discovery and imports show form/schema usage, but the dedicated form-handling "
                        "capability contract is not yet present in artifacts."
                    )
                    gap_note = "Needs explicit ReactForm/FormResolver/ValidationSchema artifact signals."
                elif capability.key == "api_clients_network":
                    support_basis = (
                        "Side-effect markers and imports show HTTP client usage, but the dedicated API "
                        "boundary contract is not yet present in artifacts."
                    )
                    gap_note = "Needs explicit ApiClient/ApiContract/ApiInterceptor artifact signals."
                elif capability.key == "context_error_suspense":
                    support_basis = (
                        "Nexora SAGE sees dependency/import signals, but the dedicated React runtime feature "
                        "contract is not yet present in artifacts."
                    )
                    gap_note = "Needs explicit ReactContext/ReactProvider/SuspenseBoundary/ErrorBoundary artifact signals."
                elif capability.key == "styling_systems":
                    support_basis = (
                        "Discovery sees styling dependencies and file patterns, but the dedicated styling "
                        "capability contract is not yet present in artifacts."
                    )
                    gap_note = "Needs explicit StyleSystem/CssModule/SassModule artifact signals."

        if present_in_repo:
            summary["repo_present"] += 1
            summary[support] += 1
        reported_support = support if present_in_repo else "not_applicable"
        matrix.append(
            {
                "key": capability.key,
                "category": capability.category,
                "label": capability.label,
                "present_in_repo": present_in_repo,
                "support": reported_support,
                "intrinsic_support": support,
                "support_basis": support_basis,
                "gap_note": gap_note,
                "evidence": {
                    "package_hits": package_hits,
                    "code_hits": code_hits,
                    "artifact_hits": artifact_hits,
                },
            }
        )

    _log(
        f"PASS evaluate scope={scope_label} capabilities={total_capabilities} "
        f"elapsed={time.perf_counter() - started:.0f}s summary={summary}"
    )
    return matrix, summary


def _compute_support_confidence(summary: dict[str, Any], matrix: list[dict[str, Any]]) -> dict[str, Any]:
    def as_int(value: Any, default: int = 0) -> int:
        try:
            if value is None:
                return default
            return int(value)
        except (TypeError, ValueError):
            return default

    present = max(0, as_int(summary.get("repo_present"), 0))
    detected = max(0, as_int(summary.get("detected"), 0))
    partial = max(0, as_int(summary.get("partial"), 0))
    missing = max(0, as_int(summary.get("missing"), 0))
    if present == 0:
        return {
            "score": 1.0,
            "tier": "not_applicable",
            "signals": {
                "present": 0,
                "detected_ratio": 1.0,
                "partial_ratio": 0.0,
                "missing_ratio": 0.0,
                "evidence_density": 1.0,
            },
        }

    detected_ratio = detected / present
    partial_ratio = partial / present
    missing_ratio = missing / present

    evidence_density_acc = 0.0
    evidence_density_count = 0
    for item in matrix:
        if not isinstance(item, dict) or not item.get("present_in_repo"):
            continue
        evidence = item.get("evidence", {})
        if not isinstance(evidence, dict):
            continue
        channels = 0
        if evidence.get("package_hits"):
            channels += 1
        if evidence.get("code_hits"):
            channels += 1
        if evidence.get("artifact_hits"):
            channels += 1
        evidence_density_acc += (channels / 3.0)
        evidence_density_count += 1

    evidence_density = (
        evidence_density_acc / evidence_density_count
        if evidence_density_count > 0
        else max(0.0, min(1.0, detected_ratio))
    )
    score = (0.65 * detected_ratio) + (0.20 * evidence_density) + (0.15 * (1.0 - missing_ratio))
    score = max(0.0, min(1.0, round(score, 3)))
    if score >= 0.85:
        tier = "high"
    elif score >= 0.65:
        tier = "medium"
    else:
        tier = "low"

    return {
        "score": score,
        "tier": tier,
        "signals": {
            "present": present,
            "detected_ratio": round(detected_ratio, 3),
            "partial_ratio": round(partial_ratio, 3),
            "missing_ratio": round(missing_ratio, 3),
            "evidence_density": round(evidence_density, 3),
        },
    }


def _write_matrix_report(payload: dict[str, Any]) -> None:
    workspace_mode = payload.get("workspace_mode", {}) if isinstance(payload, dict) else {}
    by_project = payload.get("by_project", {}) if isinstance(payload, dict) else {}
    summary = payload.get("summary", {}) if isinstance(payload, dict) else {}
    workspace_confidence = payload.get("confidence", {}) if isinstance(payload, dict) else {}
    cache = payload.get("cache", {}) if isinstance(payload, dict) else {}

    lines = [
        "# React Support Matrix",
        "",
        "> Project-first React capability contract. Workspace totals are secondary context only.",
        "",
        f"Workspace mode: **{workspace_mode.get('mode', 'unknown')}**",
        f"Projects: **{workspace_mode.get('project_count', 0)}**",
    ]
    if isinstance(cache, dict) and cache:
        lines.extend([
            f"Cache status: **{cache.get('status', 'unknown')}**",
            f"Cache algorithm: `{cache.get('algorithm', 'unknown')}`",
        ])
    lines.extend([
        "",
        "## Projects",
        "",
    ])
    for project_name, project_payload in by_project.items():
        project_summary = project_payload["summary"]
        lines.append(f"### {project_payload['display_name']} (`{project_name}` / `{project_payload['role']}`)")
        lines.append(f"- Source files scanned: `{project_payload['source_file_count']}`")
        lines.append(f"- Present: `{project_summary['repo_present']}` | Detected: `{project_summary['detected']}` | Partial: `{project_summary['partial']}` | Missing: `{project_summary['missing']}`")
        confidence = project_payload.get("confidence", {}) if isinstance(project_payload, dict) else {}
        confidence_score = confidence.get("score", 0.0) if isinstance(confidence, dict) else 0.0
        confidence_tier = confidence.get("tier", "unknown") if isinstance(confidence, dict) else "unknown"
        lines.append(f"- Confidence: `{confidence_score}` (`{confidence_tier}`)")
        lines.append("")
        lines.append("| Capability | Category | Present | Support | Evidence |")
        lines.append("|---|---|---|---|---|")
        for item in project_payload["capabilities"]:
            evidence_bits = []
            if item["evidence"]["package_hits"]:
                evidence_bits.append("pkg:" + ", ".join(item["evidence"]["package_hits"][:4]))
            if item["evidence"]["code_hits"]:
                evidence_bits.append("code:" + ", ".join(item["evidence"]["code_hits"][:4]))
            if item["evidence"].get("artifact_hits"):
                evidence_bits.append("artifact:" + ", ".join(item["evidence"]["artifact_hits"][:4]))
            lines.append(
                f"| {item['label']} | {item['category']} | {'YES' if item['present_in_repo'] else 'NO'} | {item['support']} | {' ; '.join(evidence_bits) or '-'} |"
            )
        project_backlog = project_payload["backlog"]
        if project_backlog:
            lines.extend(["", f"#### {project_payload['display_name']} gaps", ""])
            for item in project_payload["capabilities"]:
                if item["present_in_repo"] and item["support"] != "detected":
                    lines.append(f"- **{item['label']}**: {item['gap_note']}")
            lines.append("")
            lines.append(f"#### {project_payload['display_name']} backlog")
            lines.append("")
            for item in sorted(project_backlog, key=lambda x: x["priority"]):
                lines.append(f"- P{item['priority']} `{item['focus']}` [{item['focus']}]")
        lines.append("")

    lines.extend([
        "## Workspace Totals",
        "",
        f"- Present: `{summary['repo_present']}`",
        f"- Detected: `{summary['detected']}`",
        f"- Partial: `{summary['partial']}`",
        f"- Missing: `{summary['missing']}`",
        f"- Confidence: `{workspace_confidence.get('score', 0.0)}` (`{workspace_confidence.get('tier', 'unknown')}`)",
    ])

    save_text_atomic(REPORTS_DIR / "react_support_matrix.md", "\n".join(lines))


def run_validation() -> dict:
    started = time.perf_counter()
    ensure_output_dir()
    _log("START load-inputs")
    package_json = load_json_file(ROOT / "package.json") or {}
    discovery_payload = load_json_file(RAW_DIR / "discovery.json") or {}
    atlas_payload = load_atlas_data()
    state_flow_payload = load_json_file(RAW_DIR / "state_flow.json")
    project_dna_payload = load_json_file(RAW_DIR / "project_dna_profile.json") or {}
    _log(f"PASS load-inputs elapsed={time.perf_counter() - started:.0f}s")
    cache_key = _react_support_cache_key(package_json, discovery_payload, atlas_payload, state_flow_payload, project_dna_payload)
    if not _project_dna_react_frameworks(project_dna_payload):
        payload = _not_applicable_payload(project_dna_payload)
        save_json_atomic(RAW_DIR / "react_support_matrix.json", payload)
        _write_matrix_report(payload)
        _log("NOT_APPLICABLE project_dna_has_no_react_runtime")
        return payload
    cached_payload = _cached_react_support_payload(cache_key)
    if cached_payload is not None:
        _log("CACHE hit react_support_matrix")
        save_json_atomic(RAW_DIR / "react_support_matrix.json", cached_payload)
        _write_matrix_report(cached_payload)
        return cached_payload
    _log("CACHE miss react_support_matrix; recomputing")

    registered_project_roots = _project_root_map()
    project_roots, requested_project_filter = _runtime_project_roots(registered_project_roots)
    scoped_atlas_payload = _runtime_atlas_payload(atlas_payload, requested_project_filter)

    deps = {
        **(package_json.get("dependencies") or {}),
        **(package_json.get("devDependencies") or {}),
    } if isinstance(package_json, dict) else {}
    atlas_features = _collect_atlas_features(scoped_atlas_payload)
    state_flow_zustand_by_project = _index_state_flow_entries_by_project(state_flow_payload, "zustand_stores")
    state_flow_tanstack_queries_by_project = _index_state_flow_entries_by_project(state_flow_payload, "tanstack_queries")
    state_flow_tanstack_mutations_by_project = _index_state_flow_entries_by_project(state_flow_payload, "tanstack_mutations")
    if requested_project_filter:
        selected_projects = set(project_roots)
        zustand_store_count = sum(state_flow_zustand_by_project.get(key, 0) for key in selected_projects)
        tanstack_query_count = sum(state_flow_tanstack_queries_by_project.get(key, 0) for key in selected_projects)
        tanstack_mutation_count = sum(state_flow_tanstack_mutations_by_project.get(key, 0) for key in selected_projects)
    else:
        zustand_store_count = len((state_flow_payload.get("zustand_stores") or {})) if isinstance(state_flow_payload, dict) else 0
        tanstack_query_count = len((state_flow_payload.get("tanstack_queries") or {})) if isinstance(state_flow_payload, dict) else 0
        tanstack_mutation_count = len((state_flow_payload.get("tanstack_mutations") or {})) if isinstance(state_flow_payload, dict) else 0

    source_files, source_entries, config_files = _collect_workspace_index(scoped_atlas_payload, discovery_payload)
    shared_text_cache = SizeBoundedDict(max_size=max(500, len(source_entries)))
    matrix, summary = _evaluate_capabilities(
        deps=deps,
        source_files=source_files,
        source_entries=source_entries,
        config_files=config_files,
        atlas_features=atlas_features,
        zustand_store_count=zustand_store_count,
        tanstack_query_count=tanstack_query_count,
        tanstack_mutation_count=tanstack_mutation_count,
        text_cache=shared_text_cache,
        scope_label="workspace",
    )
    workspace_confidence = _compute_support_confidence(summary, matrix)

    workspace_mode = get_workspace_mode()
    project_roles = _project_role_map()
    by_project: dict[str, dict] = {}
    project_items = sorted(project_roots.items())
    _log(f"START project-evaluation projects={len(project_items)}")
    for project_index, (project_name, project_root) in enumerate(project_items, start=1):
        project_role = project_roles.get(project_name, "host" if project_name == "MAIN" else "companion")
        _log(f"RUNNING project={project_index}/{len(project_items)} key={project_name} role={project_role}")
        project_package_json = _load_package_json_for_root(project_root)
        project_source_files, project_source_entries, project_config_local = _collect_project_index(project_name, atlas_payload, project_root)
        if project_role == "host" and project_root != ROOT:
            project_deps = _merge_package_deps(project_package_json, package_json)
            project_config_files = sorted({*config_files, *project_config_local})
        else:
            project_deps = _merge_package_deps(project_package_json, {})
            project_config_files = project_config_local
        project_atlas_features = _collect_project_atlas_features(atlas_payload, project_name)
        project_zustand_count = state_flow_zustand_by_project.get(project_name, 0)
        project_tanstack_query_count = state_flow_tanstack_queries_by_project.get(project_name, 0)
        project_tanstack_mutation_count = state_flow_tanstack_mutations_by_project.get(project_name, 0)
        project_matrix, project_summary = _evaluate_capabilities(
            deps=project_deps,
            source_files=project_source_files,
            source_entries=project_source_entries,
            config_files=project_config_files,
            atlas_features=project_atlas_features,
            zustand_store_count=project_zustand_count,
            tanstack_query_count=project_tanstack_query_count,
            tanstack_mutation_count=project_tanstack_mutation_count,
            text_cache=shared_text_cache,
            scope_label=f"project:{project_name}",
        )
        by_project[project_name] = {
            "role": project_role,
            "display_name": project_display_name(project_name),
            "project_root": str(project_root),
            "source_file_count": len(project_source_files),
            "summary": project_summary,
            "confidence": _compute_support_confidence(project_summary, project_matrix),
            "capabilities": project_matrix,
            "backlog": [
                {
                    "key": item["key"],
                    **CAPABILITY_BACKLOG[item["key"]],
                }
                for item in project_matrix
                if item["present_in_repo"] and item["support"] != "detected" and item["key"] in CAPABILITY_BACKLOG
            ],
        }

    payload = {
        "workspace_root": str(ROOT),
        "workspace_mode": {
            **workspace_mode,
            "registered_project_count": len(registered_project_roots),
            "evaluated_project_count": len(project_roots),
            "requested_project_filter": requested_project_filter,
            "project_evaluation_semantics": (
                "registered projects remain topology context; only runtime-filtered projects are evaluated"
            ),
        },
        "source_file_count": len(source_files),
        "summary": summary,
        "confidence": workspace_confidence,
        "cache": {
            "status": "miss",
            "algorithm": cache_key.get("algorithm"),
            "fingerprint": cache_key.get("fingerprint"),
            "inputs": cache_key.get("inputs", {}),
        },
        "capabilities": matrix,
        "by_project": by_project,
        "backlog": [
            {
                "key": item["key"],
                **CAPABILITY_BACKLOG[item["key"]],
            }
            for item in matrix
            if item["present_in_repo"] and item["support"] != "detected" and item["key"] in CAPABILITY_BACKLOG
        ],
    }

    raw_path = RAW_DIR / "react_support_matrix.json"
    save_json_atomic(raw_path, payload)
    _write_matrix_report(payload)
    _log(f"PASS run_validation elapsed={time.perf_counter() - started:.0f}s")
    return payload


def _evaluate_contract(payload: dict[str, Any]) -> dict[str, Any]:
    _log("START contract-validation")
    checks: list[dict[str, Any]] = []
    summary = payload.get("summary", {}) if isinstance(payload, dict) else {}
    by_project = payload.get("by_project", {}) if isinstance(payload, dict) else {}
    workspace_mode = payload.get("workspace_mode", {}) if isinstance(payload, dict) else {}
    capabilities = payload.get("capabilities", []) if isinstance(payload, dict) else []
    workspace_confidence = payload.get("confidence", {}) if isinstance(payload, dict) else {}
    cache = payload.get("cache", {}) if isinstance(payload, dict) else {}

    def as_int(value: Any, default: int = -1) -> int:
        try:
            if value is None:
                return default
            return int(value)
        except (TypeError, ValueError):
            return default

    def add(name: str, passed: bool, details: str = "") -> None:
        checks.append({"name": name, "passed": bool(passed), "details": details})

    add(
        "workspace_mode_present",
        isinstance(workspace_mode, dict) and isinstance(workspace_mode.get("mode"), str),
        f"workspace_mode={workspace_mode}",
    )
    add(
        "project_inventory_present",
        isinstance(by_project, dict) and len(by_project) >= 1,
        f"projects={len(by_project) if isinstance(by_project, dict) else 0}",
    )
    add(
        "workspace_capabilities_present",
        isinstance(capabilities, list) and len(capabilities) >= 1,
        f"capabilities={len(capabilities) if isinstance(capabilities, list) else 0}",
    )
    add(
        "workspace_summary_parity",
        isinstance(summary, dict)
        and as_int(summary.get("repo_present"), -1)
        == as_int(summary.get("detected"), -2)
        + as_int(summary.get("partial"), -2)
        + as_int(summary.get("missing"), -2),
        f"summary={summary}",
    )
    workspace_score = workspace_confidence.get("score") if isinstance(workspace_confidence, dict) else None
    add(
        "workspace_confidence_present_and_range",
        isinstance(workspace_score, (int, float)) and 0.0 <= float(workspace_score) <= 1.0,
        f"workspace_confidence={workspace_confidence}",
    )
    add(
        "cache_status_and_fingerprint_present",
        isinstance(cache, dict)
        and cache.get("status") in {"hit", "miss", "not_applicable"}
        and isinstance(cache.get("fingerprint"), str)
        and len(str(cache.get("fingerprint"))) >= 32
        and cache.get("algorithm") == "react_support_snapshot_v1",
        f"cache={cache}",
    )

    project_parity_ok = True
    project_parity_details: list[str] = []
    if isinstance(by_project, dict):
        for project_name, project_payload in by_project.items():
            project_summary = (
                project_payload.get("summary", {})
                if isinstance(project_payload, dict)
                else {}
            )
            repo_present = as_int(project_summary.get("repo_present"), -1)
            detected = as_int(project_summary.get("detected"), -1)
            partial = as_int(project_summary.get("partial"), -1)
            missing = as_int(project_summary.get("missing"), -1)
            if repo_present != detected + partial + missing:
                project_parity_ok = False
                project_parity_details.append(
                    f"{project_name}: repo_present={repo_present} detected={detected} partial={partial} missing={missing}"
                )
    add(
        "project_summary_parity",
        project_parity_ok,
        "; ".join(project_parity_details) if project_parity_details else "all_projects_ok",
    )
    project_confidence_ok = True
    project_confidence_details: list[str] = []
    if isinstance(by_project, dict):
        for project_name, project_payload in by_project.items():
            confidence = project_payload.get("confidence", {}) if isinstance(project_payload, dict) else {}
            score = confidence.get("score") if isinstance(confidence, dict) else None
            tier = confidence.get("tier") if isinstance(confidence, dict) else None
            if not (isinstance(score, (int, float)) and 0.0 <= float(score) <= 1.0 and isinstance(tier, str) and tier):
                project_confidence_ok = False
                project_confidence_details.append(f"{project_name}: confidence={confidence}")
    add(
        "project_confidence_present_and_range",
        project_confidence_ok,
        "; ".join(project_confidence_details) if project_confidence_details else "all_projects_ok",
    )

    contract = {
        "summary": {
            "total_checks": len(checks),
            "passed_checks": sum(1 for item in checks if item.get("passed")),
            "failed_checks": sum(1 for item in checks if not item.get("passed")),
        },
        "checks": checks,
    }
    save_json_atomic(RAW_DIR / "react_support_validation.json", contract)
    report_lines = [
        "# React Support Validation",
        "",
        f"- Total checks: {contract['summary']['total_checks']}",
        f"- Passed: {contract['summary']['passed_checks']}",
        f"- Failed: {contract['summary']['failed_checks']}",
        "",
        "| Check | Result |",
        "|---|---|",
    ]
    for check in checks:
        report_lines.append(
            f"| `{check['name']}` | {'PASS' if check['passed'] else 'FAIL'} |"
        )
    save_text_atomic(REPORTS_DIR / "react_support_validation.md", "\n".join(report_lines) + "\n")
    _log(
        "PASS contract-validation "
        f"checks={contract['summary']['passed_checks']}/{contract['summary']['total_checks']}"
    )
    return contract


def _write_trend_report(payload: dict[str, Any]) -> None:
    _log("START trend-report")
    by_project = payload.get("by_project", {}) if isinstance(payload, dict) else {}
    lines = [
        "# React Support Trend",
        "",
        "Project-level support metrics to compare runs and external fixtures.",
        "",
        "| Project | Role | Present | Detected | Partial | Missing | Detected/Present | Confidence |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for project_name, project_payload in sorted((by_project or {}).items()):
        summary = project_payload.get("summary", {}) if isinstance(project_payload, dict) else {}
        role = str(project_payload.get("role", "unknown")) if isinstance(project_payload, dict) else "unknown"
        present = int(summary.get("repo_present", 0) or 0)
        detected = int(summary.get("detected", 0) or 0)
        partial = int(summary.get("partial", 0) or 0)
        missing = int(summary.get("missing", 0) or 0)
        ratio = round((detected / present), 3) if present else 0.0
        confidence = project_payload.get("confidence", {}) if isinstance(project_payload, dict) else {}
        confidence_score = float(confidence.get("score", 0.0)) if isinstance(confidence, dict) else 0.0
        lines.append(
            f"| `{project_name}` | `{role}` | {present} | {detected} | {partial} | {missing} | {ratio:.3f} | {confidence_score:.3f} |"
        )
    save_text_atomic(REPORTS_DIR / "react_support_trend.md", "\n".join(lines) + "\n")
    _log(f"PASS trend-report projects={len(by_project)}")


def main() -> int:
    payload = run_validation()
    _write_trend_report(payload)
    contract = _evaluate_contract(payload)
    best_effort_print(json.dumps(contract.get("summary", {}), ensure_ascii=False))
    return 0 if contract.get("summary", {}).get("failed_checks", 1) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
