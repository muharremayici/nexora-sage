from __future__ import annotations

import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.operational_limits import sqlite_read_timeout_seconds
from tools.core.agent_command_contracts import target_repo_validation_policy
from tools.core.contextos_mcp import (
    build_agent_action_directives,
    build_surgical_operation_packet,
    render_active_signals,
    render_surgical_operation_brief,
)
from tools.core.atlas_io import load_atlas_data
from tools.core.json_io import load_json_file
from tools.generate_nexora_operator_packet import build_operator_packet
from tools.inspect_target import build_inspection, render_agent_inspection_brief
from tools.mcp import server as mcp_server
from tools.validate_engine_signal_contracts import validate_engine_signal_contracts
from tools.validate_pipeline_execution_contract import validate_pipeline_execution_contract


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log(message: str) -> None:
    print(f"[agent-context-payloads] {message}", flush=True)


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)


def _timed_step(name: str, producer):
    started = time.perf_counter()
    _log(f"START {name}")
    try:
        result = producer()
    except Exception as exc:
        _log(f"FAIL {name} ({_elapsed_ms(started)}ms): {exc}")
        raise
    _log(f"PASS {name} ({_elapsed_ms(started)}ms)")
    return result


def _check(name: str, passed: bool, details: Any = None) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _has_passing_context_budget(text: str) -> bool:
    return (
        "context_budget:" in text
        and "deterministic_char_token_estimate_v1" in text
        and "estimated_tokens:" in text
        and "budget_tokens:" in text
        and ("status: pass" in text or 'status: "pass"' in text)
        and "exact_tokenizer: false" in text
    )


def _yaml_list_items(text: str, key: str) -> list[str]:
    lines = text.splitlines()
    marker = f"{key}:"
    for index, line in enumerate(lines):
        if line.strip() != marker:
            continue
        key_indent = len(line) - len(line.lstrip(" "))
        items: list[str] = []
        for child in lines[index + 1 :]:
            stripped = child.strip()
            if not stripped:
                continue
            child_indent = len(child) - len(child.lstrip(" "))
            if child_indent <= key_indent:
                break
            if stripped.startswith("- "):
                items.append(stripped[2:].strip().strip('"'))
        return items
    return []


def _activation(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("capability_activation")
    return value if isinstance(value, dict) else {}


def _activation_summary(payload: dict[str, Any]) -> dict[str, Any]:
    return _activation(payload).get("summary", {}) if isinstance(_activation(payload).get("summary"), dict) else {}


def _project_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [row for row in _activation(payload).get("projects", []) if isinstance(row, dict)]


def _capability_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        cap
        for project in _project_rows(payload)
        for cap in project.get("capabilities", [])
        if isinstance(cap, dict)
    ]


def _has_disabled_detail_rows(payload: dict[str, Any]) -> bool:
    return any(str(row.get("status")) == "disabled" for row in _capability_rows(payload))


def _has_full_plan_pointer(payload: dict[str, Any]) -> bool:
    policy = _activation(payload).get("surface_policy", {})
    return (
        isinstance(policy, dict)
        and policy.get("full_plan_artifact") == "output/.raw/capability_activation_plan.json"
        and policy.get("full_plan_mcp_tool") == "get_capability_activation_plan"
    )


def _execution_summary(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("pipeline_execution_contract")
    if not isinstance(value, dict):
        return {}
    summary = value.get("summary")
    return summary if isinstance(summary, dict) else {}


def _engine_signal_summary(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("engine_signal_contract")
    if not isinstance(value, dict):
        return {}
    summary = value.get("summary")
    return summary if isinstance(summary, dict) else {}


def _directives(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [row for row in payload.get("agent_action_directives", []) if isinstance(row, dict)]


def _target_repository_agent_surface(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("target_repository_agent_surface")
    return value if isinstance(value, dict) else {}


def _target_agent_surface_is_clean(payload: dict[str, Any]) -> bool:
    surface = _target_repository_agent_surface(payload)
    if not surface:
        return False
    if surface.get("contains_platform_status") is not False:
        return False
    if surface.get("contains_debug_artifacts") is not False:
        return False
    if surface.get("default_projection") != "agent_action_directives":
        return False
    if not surface.get("analysis_root"):
        return False
    path_contract = surface.get("path_contract")
    if not isinstance(path_contract, dict):
        return False
    if "analysis_root" not in str(path_contract.get("open_files_with") or ""):
        return False
    if "not filesystem paths" not in str(path_contract.get("target_refs_usage") or ""):
        return False
    surface_directives = surface.get("directives")
    canonical_directives = _directives(payload)
    if not isinstance(surface_directives, list) or len(surface_directives) != len(canonical_directives):
        return False
    for public_row, canonical_row in zip(surface_directives, canonical_directives):
        if not isinstance(public_row, dict):
            return False
        for field in ("id", "intent", "target_files", "related_files", "rule", "rule_explanation", "action"):
            if public_row.get(field) != canonical_row.get(field):
                return False
        if any(field in public_row for field in ("source_artifacts", "atlas_nodes", "file_context", "debug_internal_refs")):
            return False
    forbidden = {
        "mission_control",
        "release_readiness",
        "release_proof",
        "quality_gate",
        "pipeline_execution_contract",
        "engine_signal_contract",
        "source_artifacts",
        "workspace_root",
    }
    surface_text = json.dumps(surface, ensure_ascii=False)
    return not any(token in surface_text for token in forbidden)


def _target_agent_surface_defaults_to_main_scope(payload: dict[str, Any]) -> bool:
    surface = _target_repository_agent_surface(payload)
    directives = surface.get("directives", []) if isinstance(surface, dict) else []
    if not isinstance(directives, list) or not directives:
        return False
    for row in directives:
        if not isinstance(row, dict):
            return False
        for target_file in row.get("target_files", []) or []:
            if str(target_file).replace("\\", "/").startswith("Variations/"):
                return False
        for target_ref in row.get("target_refs", []) or []:
            if "::Variations/" in str(target_ref).replace("\\", "/"):
                return False
    return True


def _top_level_directives_default_to_main_scope(payload: dict[str, Any]) -> bool:
    directives = _directives(payload)
    if not directives:
        return False
    for row in directives:
        for target_file in row.get("target_files", []) or []:
            if str(target_file).replace("\\", "/").startswith("Variations/"):
                return False
        for target_ref in row.get("target_refs", []) or []:
            if "::Variations/" in str(target_ref).replace("\\", "/"):
                return False
    return True


def _directives_are_actionable(payload: dict[str, Any]) -> bool:
    rows = _directives(payload)
    if not rows:
        return False
    for row in rows:
        if not row.get("intent") or not row.get("rule") or not row.get("action"):
            return False
        if not isinstance(row.get("target_files", []), list):
            return False
        if not row.get("validation_tools") or not row.get("source_artifacts"):
            return False
        explanation = row.get("rule_explanation")
        if not isinstance(explanation, dict) or not explanation.get("label") or not explanation.get("rationale"):
            return False
    return True


def _audit_violations_have_canonical_agent_paths(sample_size: int = 100) -> dict[str, Any]:
    payload = load_json_file(RAW_DIR / "audit_report.json", {})
    violations = payload.get("violations", []) if isinstance(payload, dict) else []
    if not isinstance(violations, list):
        violations = []
    sampled = [row for row in violations if isinstance(row, dict)][:sample_size]
    missing: list[dict[str, Any]] = []
    for row in sampled:
        project = str(row.get("project_key") or row.get("project") or "").strip()
        target_ref = str(row.get("target_ref") or "").strip()
        repo_relative_path = str(row.get("repo_relative_path") or "").replace("\\", "/").strip().strip("/")
        atlas_rel_path = str(row.get("atlas_rel_path") or row.get("file") or "").replace("\\", "/").strip().strip("/")
        workspace_rel = str(row.get("workspace_rel") or "").replace("\\", "/").strip().strip("/")
        expected_ref = f"{project}::{repo_relative_path}" if project and repo_relative_path else ""
        if (
            not project
            or not target_ref
            or not repo_relative_path
            or not atlas_rel_path
            or not workspace_rel
            or target_ref != expected_ref
            or "::" not in target_ref
        ):
            missing.append(
                {
                    "project": row.get("project"),
                    "project_key": row.get("project_key"),
                    "file": row.get("file"),
                    "repo_relative_path": row.get("repo_relative_path"),
                    "workspace_rel": row.get("workspace_rel"),
                    "target_ref": row.get("target_ref"),
                    "expected_ref": expected_ref,
                }
            )
            if len(missing) >= 10:
                break
    return {
        "checked": len(sampled),
        "total": len(violations),
        "missing": missing,
        "applicable": bool(sampled),
        "passed": not missing,
    }


def _ensure_context_contract_artifacts() -> None:
    if not (RAW_DIR / "pipeline_step_registry.json").exists():
        from tools.core.pipeline_registry import catalog_args_from_execution_policy, step_registry_from_catalog
        from tools.core.json_io import load_json_object_strict
        from tools.orchestrators.orchestrator import build_step_catalog

        policy = load_json_object_strict(CONFIG_DIR / "pipeline_execution_policy.json", label="Pipeline execution policy")
        catalog_args = catalog_args_from_execution_policy(policy)
        save_json_atomic(RAW_DIR / "pipeline_step_registry.json", step_registry_from_catalog(build_step_catalog(catalog_args)))
    if not (RAW_DIR / "pipeline_execution_contract_validation.json").exists():
        validate_pipeline_execution_contract()
    if not (RAW_DIR / "engine_signal_contract_validation.json").exists():
        validate_engine_signal_contracts()


def _iter_atlas_file_rows() -> list[dict[str, str]]:
    atlas = load_atlas_data()
    rows: list[dict[str, str]] = []
    for project, payload in (atlas or {}).items():
        if not isinstance(payload, dict):
            continue
        files = payload.get("files")
        if not isinstance(files, dict):
            continue
        for file_key, info in files.items():
            if not isinstance(info, dict):
                info = {}
            rel = str(info.get("workspace_rel") or file_key or "").replace("\\", "/").strip().strip("/")
            if rel:
                rows.append({"project": str(project), "rel": rel})
    return rows


def _pick_inspection_target() -> dict[str, str]:
    rows = _iter_atlas_file_rows()
    source_like = [
        row
        for row in rows
        if row["rel"].lower().endswith((".tsx", ".ts", ".jsx", ".js", ".py", ".go", ".java", ".cs"))
        and not row["rel"].replace("\\", "/").startswith("Variations/")
    ]
    main_source_like = [row for row in source_like if row.get("project") == "MAIN"]
    if main_source_like:
        return main_source_like[0]
    if source_like:
        return source_like[0]
    if rows:
        return rows[0]
    return {"project": "MAIN", "rel": "src/App.tsx"}


def _pick_component_span_target() -> dict[str, Any]:
    """Select a real MAIN component span from SQLite and expose agent paths."""

    db_path = RAW_DIR / "codemaps.db"
    if not db_path.exists():
        return {}
    atlas_rows = {
        (row["project"], row["rel"].removeprefix("src/")): row["rel"]
        for row in _iter_atlas_file_rows()
        if row.get("project") and row.get("rel")
    }
    try:
        with sqlite3.connect(db_path, timeout=float(sqlite_read_timeout_seconds())) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT files.project_key, files.rel_path, symbols.name, symbols.line,
                       symbols.end_line, symbols.source_lines
                FROM symbols
                JOIN files ON files.file_id = symbols.file_id
                WHERE files.project_key = 'MAIN'
                  AND symbols.type = 'Component'
                  AND symbols.line > 0
                  AND symbols.end_line >= symbols.line
                ORDER BY files.rel_path, symbols.line
                LIMIT 1;
                """
            ).fetchone()
    except Exception:
        return {}
    if row is None:
        return {}
    project = str(row["project_key"])
    atlas_rel = str(row["rel_path"]).replace("\\", "/").strip("/")
    workspace_rel = atlas_rows.get((project, atlas_rel), f"src/{atlas_rel}".replace("//", "/"))
    return {
        "project": project,
        "atlas_rel": atlas_rel,
        "rel": workspace_rel,
        "symbol": str(row["name"]),
        "line": int(row["line"] or 0),
        "end_line": int(row["end_line"] or row["line"] or 0),
        "source_lines": str(row["source_lines"] or ""),
    }


def validate_agent_context_payloads() -> dict[str, Any]:
    _timed_step("ensure_context_contract_artifacts", _ensure_context_contract_artifacts)
    inspection_target = _timed_step("pick_inspection_target", _pick_inspection_target)
    operator_packet = _timed_step("build_operator_packet", build_operator_packet)
    support_artifacts = _timed_step(
        "load_support_artifacts",
        lambda: {
            "signals": load_json_file(RAW_DIR / "signals.json", {}),
            "circular": load_json_file(RAW_DIR / "circular_deps.json", {}),
        },
    )
    signals = support_artifacts["signals"]
    circular = support_artifacts["circular"]
    surgical_packet = _timed_step(
        "build_surgical_operation_packet",
        lambda: build_surgical_operation_packet(
            signals if isinstance(signals, dict) else {},
            circular_deps_data=circular if isinstance(circular, dict) else {},
            max_signals=3,
        ),
    )
    operator_activation = _activation_summary(operator_packet)
    surgical_activation = _activation_summary(surgical_packet)
    operator_json = json.dumps(operator_packet, ensure_ascii=False)
    surgical_json = json.dumps(surgical_packet, ensure_ascii=False)
    audit_path_contract = _timed_step("audit_violation_path_contract", _audit_violations_have_canonical_agent_paths)
    synthetic_directives = _timed_step(
        "build_synthetic_action_directives",
        lambda: build_agent_action_directives(
            {"active_signals": []},
            audit_report={
                "violations": [
                    {
                        "project": inspection_target["project"],
                        "file": inspection_target["rel"],
                        "rule": "relative_imports_no_alias",
                        "detail": "Synthetic agent-surface drill for repo-relative target path resolution.",
                    }
                ]
            },
            quality_gate={},
            max_items=1,
        ),
    )
    synthetic_directive = synthetic_directives[0] if synthetic_directives else {}
    stripped_inspection_rel = (
        inspection_target["rel"][4:]
        if str(inspection_target["rel"]).startswith("src/")
        else str(inspection_target["rel"])
    )
    synthetic_brief = render_surgical_operation_brief(
        {
            "mission_brief": [],
            "analysis_root": str(ROOT),
            "summary": {},
            "architecture_governance_context": {},
            "upstream_traces": [
                {
                    "target_file": "src/unrelated.ts",
                    "upstream_dependency_files": ["src/shared/noise.ts"],
                    "direct_dependent_files": ["src/noise-dependent.ts"],
                },
                {
                    "target_file": inspection_target["rel"],
                    "upstream_dependency_files": [stripped_inspection_rel],
                    "direct_dependent_files": ["src/direct-dependent.ts"],
                }
            ],
            "agent_action_directives": synthetic_directives,
            "source_grounding": {
                "target_ref": "MAIN::src/App.tsx",
                "target_file": "src/App.tsx",
                "target_project": "MAIN",
                "target_exists": True,
                "target_indexed": True,
                "target_grounding_status": "grounded",
                "source_snapshot_status": "ok",
                "source_snapshot_hash_prefix": "1234567890ab",
                "drift_check_status": "match",
                "target_span_count": 1,
                "target_spans_shown": 1,
                "target_spans_omitted": 0,
                "target_spans": [
                    {
                        "symbol": "App",
                        "type": "component",
                        "start_line": 1,
                        "end_line": 2,
                        "source_lines": "L1-L2",
                        "line_status": "available",
                    }
                ],
                "target_source_snippets_shown": 1,
                "target_source_snippets_omitted": 0,
                "target_source_snippets": [
                    {
                        "symbol": "App",
                        "source_lines": "L1-L2",
                        "snippet_status": "included",
                        "code": "1: import React from 'react';\n2: export const App = () => null;",
                    }
                ],
            },
        }
    )
    synthetic_inspect_first = _yaml_list_items(synthetic_brief, "inspect_first")
    partial_snippet_brief = render_surgical_operation_brief(
        {
            "mission_brief": [],
            "analysis_root": str(ROOT),
            "summary": {},
            "architecture_governance_context": {},
            "upstream_traces": [],
            "agent_action_directives": synthetic_directives,
            "source_grounding": {
                "target_ref": "MAIN::src/large-hook.ts",
                "target_file": "src/large-hook.ts",
                "target_project": "MAIN",
                "target_exists": True,
                "target_indexed": True,
                "target_grounding_status": "grounded",
                "source_snapshot_status": "ok",
                "source_snapshot_hash_prefix": "abcdef123456",
                "drift_check_status": "match",
                "target_span_count": 1,
                "target_spans_shown": 1,
                "target_spans_omitted": 0,
                "target_spans": [
                    {
                        "symbol": "largeHook",
                        "type": "hook",
                        "start_line": 10,
                        "end_line": 70,
                        "source_lines": "L10-L70",
                        "line_status": "available",
                    }
                ],
                "target_source_snippets_shown": 1,
                "target_source_snippets_omitted": 0,
                "target_source_snippets": [
                    {
                        "symbol": "largeHook",
                        "source_lines": "L10-L70",
                        "snippet_status": "partial_included_span_too_large",
                        "line_count": 61,
                        "shown_lines": 20,
                        "omitted_lines": 41,
                        "omitted_range": "L22-L62",
                        "next_chunk_lines": "L22-L41",
                        "one_shot_edit_ready": False,
                        "snippet_role": "orientation",
                        "snippet_strategy": "boundary_slice_for_large_symbol",
                        "snippet_purpose": "orientation_not_complete_edit_context",
                        "omitted_context_policy": "do_not_read_or_edit_all_omitted_lines_by_default",
                        "edit_scope": "do_not_edit_omitted_lines_without_follow_up",
                        "follow_up_if_needed": "Call inspect_file(file_path=\"MAIN::src/large-hook.ts\", line_start=22, line_end=41) only if you need to inspect that omitted body chunk before editing outside the shown boundary snippet.",
                        "code": (
                            "10: line 10\n"
                            "11: line 11\n"
                            "12: line 12\n"
                            "13: line 13\n"
                            "14: line 14\n"
                            "15: line 15\n"
                            "16: line 16\n"
                            "17: line 17\n"
                            "18: line 18\n"
                            "19: line 19\n"
                            "20: line 20\n"
                            "21: line 21\n"
                            "... 41 omitted lines inside large symbol ...\n"
                            "63: line 63\n"
                            "64: line 64\n"
                            "65: line 65\n"
                            "66: line 66\n"
                            "67: line 67\n"
                            "68: line 68\n"
                            "69: line 69\n"
                            "70: line 70"
                        ),
                    }
                ],
            },
        }
    )
    file_inspection = _timed_step("build_file_inspection", lambda: build_inspection("file", inspection_target["rel"]))
    file_context = file_inspection.get("target_file_context", [])
    file_inspection_brief = _timed_step("render_file_inspection_brief", lambda: render_agent_inspection_brief(file_inspection))
    component_span_target = _timed_step("pick_component_span_target", _pick_component_span_target)
    component_symbol_inspection: dict[str, Any] = {}
    component_symbol_brief = ""
    if component_span_target:
        component_symbol_inspection = _timed_step(
            "build_component_symbol_inspection",
            lambda: build_inspection("symbol", str(component_span_target.get("symbol") or "")),
        )
        component_symbol_brief = _timed_step(
            "render_component_symbol_inspection_brief",
            lambda: render_agent_inspection_brief(component_symbol_inspection),
        )
    component_file_brief = ""
    if component_span_target:
        component_file_brief = _timed_step(
            "inspect_component_span_file",
            lambda: mcp_server.inspect_file(
                f"{component_span_target['project']}::{component_span_target['rel']}",
                format="brief",
            ),
        )
    synthetic_target_path_status = {
        "target_ref": "MAIN::src/App.tsx",
        "target_file": "src/App.tsx",
        "target_project": "MAIN",
        "exists": True,
        "inside_root": True,
        "indexed": True,
        "source_snapshot_status": "ok",
        "source_snapshot_hash": "1234567890abcdef",
        "drift_check_status": "match",
        "target_span_count": 1,
        "target_spans": [
            {
                "symbol": "App",
                "type": "component",
                "start_line": 1,
                "end_line": 10,
                "source_lines": "L1-L10",
                "line_status": "available",
            }
        ],
        "target_source_snippets": [
            {
                "symbol": "App",
                "source_lines": "L1-L10",
                "snippet_status": "included",
                "code": "1: import React from 'react';\n2: export const App = () => null;",
            }
        ],
        "evidence_source_snippets": [
            {
                "evidence": "src/App.tsx imports ./service",
                "source_lines": "L1-L2",
                "matched_line": 1,
                "snippet_status": "included_evidence_line",
                "code": "1: import service from './service';\n2: export const App = () => null;",
            }
        ],
    }
    synthetic_service_path_status = {
        "target_ref": "MAIN::src/service.ts",
        "target_file": "src/service.ts",
        "target_project": "MAIN",
        "exists": True,
        "inside_root": True,
        "indexed": True,
        "source_snapshot_status": "ok",
        "source_snapshot_hash": "abcdef1234567890",
        "drift_check_status": "match",
        "target_span_count": 1,
        "target_spans": [
            {
                "symbol": "service",
                "type": "function",
                "start_line": 1,
                "end_line": 3,
                "source_lines": "L1-L3",
                "line_status": "available",
            }
        ],
        "target_source_snippets": [
            {
                "symbol": "service",
                "source_lines": "L1-L3",
                "snippet_status": "included",
                "code": "1: import helper from './helper';\n2: export const service = () => helper();",
            }
        ],
        "evidence_source_snippets": [
            {
                "evidence": "src/service.ts imports ./helper",
                "source_lines": "L1-L2",
                "matched_line": 1,
                "snippet_status": "included_evidence_line",
                "code": "1: import helper from './helper';\n2: export const service = () => helper();",
            }
        ],
    }
    impact_brief = mcp_server._render_impact_brief(
        {
            "target": "MAIN::src/App.tsx",
            "target_ref": "MAIN::src/App.tsx",
            "analysis_root": str(ROOT),
            "target_project": "MAIN",
            "target_file": "src/App.tsx",
            "target_path_status": synthetic_target_path_status,
            "radius_depth": 2,
            "duration_ms": 12.5,
            "slow_warning": "",
            "blast_radius_size": 1,
            "returned_scope_size": 1,
            "direct_dependents_count": 1,
            "direct_dependents": ["src/main.tsx"],
            "direct_dependent_refs": ["MAIN::src/main.tsx"],
            "transitive_dependents": ["src/routes.tsx"],
            "transitive_dependent_refs": ["MAIN::src/routes.tsx"],
            "transitive_dependent_depths": {"src/routes.tsx": 2},
        }
    )
    missing_impact_brief = mcp_server._render_impact_brief(
        {
            "target": "MAIN::src/NO_SUCH_FILE.tsx",
            "target_ref": "MAIN::src/NO_SUCH_FILE.tsx",
            "analysis_root": str(ROOT),
            "target_project": "MAIN",
            "target_file": "src/NO_SUCH_FILE.tsx",
            "target_path_status": {
                "target_ref": "MAIN::src/NO_SUCH_FILE.tsx",
                "target_file": "src/NO_SUCH_FILE.tsx",
                "target_project": "MAIN",
                "exists": False,
                "inside_root": True,
                "indexed": False,
                "source_snapshot_status": "missing",
                "drift_check_status": "not_available",
                "target_span_count": 0,
                "target_spans": [],
                "target_source_snippets": [],
                "evidence_source_snippets": [],
            },
            "radius_depth": 2,
            "blast_radius_size": 0,
            "returned_scope_size": 0,
            "direct_dependents_count": 0,
            "direct_dependents": [],
            "direct_dependent_refs": [],
            "transitive_dependents": [],
            "transitive_dependent_refs": [],
        }
    )
    test_impact_brief = mcp_server._render_test_impact_brief(
        {
            "target": "MAIN::src/App.tsx",
            "target_ref": "MAIN::src/App.tsx",
            "analysis_root": str(ROOT),
            "target_project": "MAIN",
            "target_file": "src/App.tsx",
            "target_path_status": synthetic_target_path_status,
            "test_source_snippet_limit": 1,
            "test_source_snippets_attached": 1,
            "test_source_snippets_omitted": 1,
            "impacted_tests": [
                {
                    "file": "src/App.test.tsx",
                    "repo_relative_path": "src/App.test.tsx",
                    "type": "Synthetic Direct Test",
                    "confidence": 1.0,
                    "run_command": "pnpm test src/App.test.tsx",
                    "source_snippets": [
                        {
                            "evidence": "bounded test assertion/context evidence for src/App.test.tsx",
                            "source_lines": "L5-L7",
                            "matched_line": 6,
                            "snippet_status": "included_test_evidence_line",
                            "code": "5: describe('App', () => {\n6:   it('renders shell', () => {\n7:     expect(true).toBe(true);",
                        }
                    ],
                },
                {
                    "file": "src/App.integration.test.tsx",
                    "repo_relative_path": "src/App.integration.test.tsx",
                    "type": "Synthetic Transitive Test",
                    "confidence": 0.6,
                    "run_command": "pnpm test src/App.integration.test.tsx",
                    "source_snippet_status": "omitted_context_budget",
                    "source_snippet_note": "Snippet omitted because this test is outside the bounded test-source snippet budget; run the command or inspect this test only if earlier listed evidence is insufficient.",
                    "source_snippets": [],
                }
            ],
        }
    )
    confidence_brief = mcp_server._render_confidence_brief(
        {
            "target": "MAIN::src/App.tsx",
            "target_ref": "MAIN::src/App.tsx",
            "analysis_root": str(ROOT),
            "target_project": "MAIN",
            "target_file": "src/App.tsx",
            "target_exists": True,
            "target_indexed": True,
            "target_grounding_status": "grounded",
            "target_path_status": synthetic_target_path_status,
            "confidence_matrix": {"merge_safety": "MEDIUM", "architecture_drift_certainty": 0.25},
            "input_evidence": {
                "circular_deps": {"status": "PASS", "source": "synthetic_fixture", "shape_status": "valid"}
            },
            "reasons": ["Direct dependent count: 1"],
        }
    )
    upstream_brief = mcp_server._render_upstream_trace_brief(
        {
            "target": "MAIN::src/App.tsx",
            "target_ref": "MAIN::src/App.tsx",
            "analysis_root": str(ROOT),
            "target_project": "MAIN",
            "target_file": "src/App.tsx",
            "target_path_status": synthetic_target_path_status,
            "traces": ["MAIN::src/main.tsx"],
            "direct_dependent_files": ["src/main.tsx"],
            "upstream_dependency_evidence": [
                {
                    "file": "src/main.tsx",
                    "target_ref": "MAIN::src/main.tsx",
                    "import_specifier": "App",
                    "snippet_status": "included_dependency_evidence_line",
                    "source_snippets": [
                        {
                            "evidence": "src/App.tsx imports App",
                            "source_lines": "L1-L3",
                            "matched_line": 2,
                            "snippet_status": "included_dependency_evidence_line",
                            "code": "1: import React from 'react';\n2: import App from './App';\n3: render(<App />);",
                        }
                    ],
                }
            ],
            "upstream_dependency_evidence_omitted": 0,
        }
    )
    patch_validation_brief = mcp_server._render_patch_validation_brief(
        {
            "status": "PASS",
            "analysis_root": str(ROOT),
            "target_project": "MAIN",
            "target_file": "src/App.tsx",
            "target_ref": "MAIN::src/App.tsx",
            "target_path_status": synthetic_target_path_status,
            "proposed_change_snippets": [
                {
                    "hunk_header": "@@ -1,2 +1,2 @@",
                    "snippet_status": "included_patch_hunk",
                    "omitted_changed_lines": 0,
                    "changed_lines": [
                        "-import React from 'react';",
                        "+import React from 'react';",
                    ],
                }
            ],
            "violations": [],
        },
        "MAIN::src/App.tsx",
    )
    work_queue_brief = mcp_server._render_violation_work_queue_brief(
        {
            "analysis_root": str(ROOT),
            "total_violations": 1,
            "page": 1,
            "page_size": 1,
            "artifact_trust": {
                "status": "PASS",
                "scope": {"audited_project_count": 1, "atlas_project_count": 1},
                "failures": [],
                "warnings": [],
            },
            "items": [
                {
                    "id": "audit_1",
                    "target_project": "MAIN",
                    "target_file": "src/App.tsx",
                    "target_ref": "MAIN::src/App.tsx",
                    "source_grounding": synthetic_target_path_status,
                    "rule": "relative_imports_no_alias",
                    "label": "Relative Imports No Alias",
                    "priority": "normal",
                    "human_approval_required": False,
                    "why_it_matters": "Synthetic queue path-contract drill.",
                    "fix_strategy": "Use the canonical import boundary.",
                    "inspect_first": ["src/App.tsx"],
                    "evidence": "src/App.tsx imports ./service",
                    "recommended_action": "Inspect src/App.tsx and make the smallest safe patch.",
                },
                {
                    "id": "audit_2",
                    "target_project": "MAIN",
                    "target_file": "src/service.ts",
                    "target_ref": "MAIN::src/service.ts",
                    "source_grounding": synthetic_service_path_status,
                    "rule": "relative_imports_no_alias",
                    "label": "Relative Imports No Alias",
                    "priority": "normal",
                    "human_approval_required": False,
                    "why_it_matters": "Synthetic queue path-contract drill.",
                    "fix_strategy": "Use the canonical import boundary.",
                    "inspect_first": ["src/service.ts", "src/helper.ts"],
                    "evidence": "src/service.ts imports ./helper",
                    "recommended_action": "Inspect src/service.ts and make the smallest safe patch.",
                }
            ],
        }
    )
    ambiguous_file_brief = render_agent_inspection_brief(
        {
            "target": {"kind": "file", "value": "App.tsx"},
            "summary": {"audit_violations": 2},
            "target_file_context": [
                {"project": "P1", "file": "App.tsx", "workspace_rel": "apps/one/App.tsx", "atlas_node": "P1::App.tsx"},
                {"project": "P2", "file": "App.tsx", "workspace_rel": "apps/two/App.tsx", "atlas_node": "P2::App.tsx"},
            ],
            "audit_violations": [
                {"project": "P1", "file": "App.tsx", "rule": "relative_imports_no_alias", "detail": "P1 detail"},
                {"project": "P2", "file": "App.tsx", "rule": "relative_imports_no_alias", "detail": "P2 detail"},
            ],
        }
    )
    default_symbol_brief = _timed_step("inspect_symbol_default_scope", lambda: mcp_server.inspect_symbol("App", format="brief"))
    all_project_symbol_brief = _timed_step("inspect_symbol_all_projects", lambda: mcp_server.inspect_symbol("App", format="brief", project="all"))
    ranged_file_brief = _timed_step(
        "inspect_file_requested_line_range",
        lambda: mcp_server.inspect_file("MAIN::src/App.tsx", format="brief", line_start=1, line_end=5),
    )
    empty_active_signals_brief = render_active_signals(
        {"analysis_root": str(ROOT), "active_signals": [], "summary": {}},
        resolve_absolute_path=lambda rel_path, project_key="MAIN": ROOT / rel_path,
    )
    synthetic_active_signal_path = "tools/tests/fixtures/react_v11/src/router.tsx"
    active_signal_body_brief = _timed_step(
        "render_synthetic_active_signals_with_body",
        lambda: render_active_signals(
            {
                "analysis_root": str(ROOT),
                "summary": {"halo_files": 0, "is_scene_pivot": False},
                "active_signals": [
                    {
                        "node_key": f"MAIN::{synthetic_active_signal_path}",
                        "target_ref": f"MAIN::{synthetic_active_signal_path}",
                        "relative_path": synthetic_active_signal_path,
                        "signal_kind": "source",
                        "impact_score": 0.0,
                        "impact_score_status": "synthetic_validator_fixture",
                        "risk_claim_boundary": "renderer_contract_only",
                        "direct_dependents": [],
                        "transitive_dependents": [],
                        "active_violations": [],
                        "circular_cycles": [],
                        "circular_cycles_status": "deferred_broad_proof",
                    }
                ],
            },
            output_format="markdown",
            include_bodies=True,
            max_files=1,
            max_chars_per_file=1200,
            scope="l1",
            resolve_absolute_path=lambda rel_path, project_key="MAIN": ROOT / rel_path,
        ),
    )
    symbol_evidence_snippets = mcp_server._evidence_source_snippets(
        "import argparse\n\n\ndef cmd_doctor(args):\n    return 0\n",
        ["codemaps.py symbol:cmd_doctor (154 lines)"],
    )
    target_import_snippets = mcp_server._test_source_snippets(
        "import argparse\nimport json\nfrom codemaps import _safe_command_env\n",
        "tools/tests/test_pipeline_registry.py",
        target_file="codemaps.py",
        target_import_needles=["codemaps"],
        target_symbols=["argparse", "json", "_safe_command_env"],
    )
    original_run_cli = mcp_server._run_cli
    original_watchdog_session_roots = mcp_server._watchdog_session_roots
    try:
        mcp_server._run_cli = lambda *_args, **_kwargs: "Command failed (1):\nmissing watchdog"
        mcp_server._watchdog_session_roots = lambda _target_root, _profile_id: (
            RAW_DIR,
            REPORTS_DIR,
            str(ROOT),
        )
        failed_watchdog_payload = json.loads(
            mcp_server.run_watchdog_once(
                path=str(ROOT),
                target_root=str(ROOT),
                profile_id="sage_self",
            )
        )
    finally:
        mcp_server._run_cli = original_run_cli
        mcp_server._watchdog_session_roots = original_watchdog_session_roots

    checks = [
        _check(
            "failed_watchdog_run_does_not_inherit_historical_session",
            failed_watchdog_payload.get("status") == "FAILED"
            and failed_watchdog_payload.get("current_watchdog_session") is None
            and (failed_watchdog_payload.get("historical_session") or {}).get("status")
            == "historical_not_current_for_failed_run"
            and "watchdog_session" not in failed_watchdog_payload,
            failed_watchdog_payload,
        ),
        _check(
            "symbol_evidence_snippet_matches_declared_symbol",
            len(symbol_evidence_snippets) == 1
            and symbol_evidence_snippets[0].get("matched_line") == 4
            and "def cmd_doctor" in str(symbol_evidence_snippets[0].get("code") or "")
            and "import argparse" not in str(symbol_evidence_snippets[0].get("code") or ""),
            symbol_evidence_snippets,
        ),
        _check(
            "test_snippet_prefers_exact_target_import_identity",
            len(target_import_snippets) == 1
            and target_import_snippets[0].get("matched_line") == 3
            and target_import_snippets[0].get("snippet_status") == "included_test_target_import_line"
            and "from codemaps import _safe_command_env" in str(target_import_snippets[0].get("code") or ""),
            target_import_snippets,
        ),
        _check(
            "operator_packet_has_capability_activation_context",
            operator_activation.get("status") == "PASS"
            and operator_activation.get("planning_only") is False
            and operator_activation.get("pipeline_scheduler_enforced") is True,
            operator_activation,
        ),
        _check(
            "operator_packet_keeps_disabled_as_non_ban_semantics",
            operator_activation.get("disabled_semantics") == "no_current_project_dna_signal_not_a_policy_ban"
            and "refresh project DNA" in str(operator_activation.get("dependency_change_policy") or ""),
            operator_activation,
        ),
        _check(
            "operator_packet_uses_bounded_activation_projection",
            len(_project_rows(operator_packet)) <= 3
            and not _has_disabled_detail_rows(operator_packet)
            and _has_full_plan_pointer(operator_packet),
            {
                "project_rows": len(_project_rows(operator_packet)),
                "has_disabled_detail_rows": _has_disabled_detail_rows(operator_packet),
            },
        ),
        _check(
            "surgical_packet_has_capability_activation_context",
            surgical_activation.get("status") == "PASS"
            and surgical_activation.get("planning_only") is False
            and surgical_activation.get("pipeline_scheduler_enforced") is True,
            surgical_activation,
        ),
        _check(
            "surgical_packet_uses_bounded_activation_projection",
            len(_project_rows(surgical_packet)) <= 3
            and not _has_disabled_detail_rows(surgical_packet)
            and _has_full_plan_pointer(surgical_packet),
            {
                "project_rows": len(_project_rows(surgical_packet)),
                "has_disabled_detail_rows": _has_disabled_detail_rows(surgical_packet),
            },
        ),
        _check(
            "operator_packet_has_pipeline_execution_contract_context",
            _execution_summary(operator_packet).get("status") == "PASS"
            and isinstance(_execution_summary(operator_packet).get("scheduler_counts"), dict)
            and operator_packet.get("pipeline_execution_contract", {}).get("mcp_tool") == "get_pipeline_execution_contract",
            _execution_summary(operator_packet),
        ),
        _check(
            "surgical_packet_has_pipeline_execution_contract_context",
            _execution_summary(surgical_packet).get("status") == "PASS"
            and isinstance(_execution_summary(surgical_packet).get("scheduler_counts"), dict)
            and surgical_packet.get("pipeline_execution_contract", {}).get("mcp_tool") == "get_pipeline_execution_contract",
            _execution_summary(surgical_packet),
        ),
        _check(
            "operator_packet_has_engine_signal_contract_context",
            _engine_signal_summary(operator_packet).get("status") == "PASS"
            and operator_packet.get("engine_signal_contract", {}).get("mcp_tool") == "get_engine_signal_contracts"
            and operator_packet.get("engine_signal_contract", {}).get("relevant_contracts"),
            _engine_signal_summary(operator_packet),
        ),
        _check(
            "surgical_packet_has_engine_signal_contract_context",
            _engine_signal_summary(surgical_packet).get("status") == "PASS"
            and surgical_packet.get("engine_signal_contract", {}).get("mcp_tool") == "get_engine_signal_contracts"
            and surgical_packet.get("engine_signal_contract", {}).get("relevant_contracts"),
            _engine_signal_summary(surgical_packet),
        ),
        _check(
            "operator_packet_has_actionable_agent_directives",
            _directives_are_actionable(operator_packet),
            {"directives": _directives(operator_packet)[:2]},
        ),
        _check(
            "operator_packet_separates_platform_status_from_target_agent_surface",
            _target_agent_surface_is_clean(operator_packet)
            and operator_packet.get("role_surfaces", {}).get("operator_platform_status", {}).get("contains_platform_status") is True
            and operator_packet.get("role_surfaces", {}).get("target_repository_agent", {}).get("contains_platform_status") is False,
            {
                "target_repository_agent_surface": _target_repository_agent_surface(operator_packet),
                "role_surfaces": operator_packet.get("role_surfaces", {}),
            },
        ),
        _check(
            "operator_packet_target_agent_surface_defaults_to_main_scope",
            _target_agent_surface_defaults_to_main_scope(operator_packet),
            {
                "target_repository_agent_surface": _target_repository_agent_surface(operator_packet),
            },
        ),
        _check(
            "surgical_packet_has_actionable_agent_directives",
            _directives_are_actionable(surgical_packet),
            {"directives": _directives(surgical_packet)[:2]},
        ),
        _check(
            "surgical_packet_defaults_to_main_scope",
            _top_level_directives_default_to_main_scope(surgical_packet),
            {"directives": _directives(surgical_packet)[:2]},
        ),
        _check(
            "agent_directives_translate_atlas_nodes_to_repo_relative_targets",
            synthetic_directive.get("target_files") == [inspection_target["rel"]]
            and synthetic_directive.get("atlas_nodes")
            and synthetic_directive.get("file_context", [{}])[0].get("project_key") == inspection_target["project"]
            and synthetic_directive.get("file_context", [{}])[0].get("repo_relative_path") == inspection_target["rel"]
            and synthetic_directive.get("file_context", [{}])[0].get("atlas_node") == synthetic_directive.get("atlas_nodes", [None])[0],
            {"target": inspection_target, "directive": synthetic_directive},
        ),
        _check(
            "audit_violations_carry_canonical_agent_path_contract",
            audit_path_contract.get("passed") is True,
            audit_path_contract,
        ),
        _check(
            "surgical_brief_exposes_target_refs_without_debug_graph_terms",
            f"{inspection_target['project']}::{inspection_target['rel']}" in synthetic_brief
            and "analysis_root:" in synthetic_brief
            and "target_refs:" in synthetic_brief
            and "path_contract:" in synthetic_brief
            and "target_refs_usage:" in synthetic_brief
            and f"mode: {target_repo_validation_policy().get('mode')}" in synthetic_brief
            and "tools:" in synthetic_brief
            and "get_test_impact(target_file)" in synthetic_brief
            and "validate_patch(target_file, patch_content)" in synthetic_brief
            and "completion_rule:" in synthetic_brief
            and _has_passing_context_budget(synthetic_brief)
            and "Run these from the SAGE workspace or through SAGE MCP" not in synthetic_brief
            and "atlas_node" not in synthetic_brief
            and "debug_internal_refs" not in synthetic_brief,
            {"target": inspection_target, "brief": synthetic_brief[:1200]},
        ),
        _check(
            "surgical_brief_inspect_first_starts_with_target_and_rejects_unmatched_trace",
            synthetic_inspect_first[:1] == [inspection_target["rel"]]
            and (
                stripped_inspection_rel == inspection_target["rel"]
                or stripped_inspection_rel not in synthetic_inspect_first
            )
            and "src/shared/noise.ts" not in synthetic_inspect_first
            and "src/noise-dependent.ts" not in synthetic_inspect_first,
            {
                "target": inspection_target,
                "inspect_first": synthetic_inspect_first,
                "starts_with_target": synthetic_inspect_first[:1] == [inspection_target["rel"]],
                "has_stripped_duplicate": stripped_inspection_rel in synthetic_inspect_first,
                "has_unmatched_trace_upstream": "src/shared/noise.ts" in synthetic_inspect_first,
                "has_unmatched_trace_dependent": "src/noise-dependent.ts" in synthetic_inspect_first,
            },
        ),
        _check(
            "inspect_file_resolves_repo_relative_target_without_cross_project_bleed",
            file_inspection.get("summary", {}).get("atlas_files") == 1
            and file_context
            and file_context[0].get("project") == inspection_target["project"]
            and file_context[0].get("workspace_rel") == inspection_target["rel"],
            {
                "target": inspection_target,
                "summary": file_inspection.get("summary", {}),
                "target_file_context": file_context[:3],
            },
        ),
        _check(
            "inspect_file_default_brief_is_target_repo_agent_friendly",
            file_inspection_brief.startswith("# Target Inspection Brief")
            and "```yaml" in file_inspection_brief
            and 'target_files:' in file_inspection_brief
            and 'target_refs:' in file_inspection_brief
            and "path_contract:" in file_inspection_brief
            and "target_refs_usage:" in file_inspection_brief
            and f"{inspection_target['project']}::{inspection_target['rel']}" in file_inspection_brief
            and inspection_target["rel"] in file_inspection_brief
            and "atlas_node" not in file_inspection_brief
            and "SOVEREIGN_ELITE" not in file_inspection_brief
            and "output/.raw" not in file_inspection_brief,
            {"target": inspection_target, "brief_chars": len(file_inspection_brief)},
        ),
        _check(
            "inspect_file_brief_carries_target_symbol_spans",
            bool(component_span_target)
            and "target_spans:" in component_file_brief
            and f"symbol: {json.dumps(component_span_target.get('symbol'), ensure_ascii=False)}" in component_file_brief
            and "line_status: \"available\"" in component_file_brief
            and f"start_line: {component_span_target.get('line')}" in component_file_brief
            and f"end_line: {component_span_target.get('end_line')}" in component_file_brief
            and f"target_file: {json.dumps(component_span_target.get('rel'), ensure_ascii=False)}" in component_file_brief
            and f"target_ref: {json.dumps(component_span_target.get('project') + '::' + component_span_target.get('rel'), ensure_ascii=False)}" in component_file_brief,
            {"target": component_span_target, "brief": component_file_brief[:1600]},
        ),
        _check(
            "direct_inspect_target_symbol_projection_carries_sqlite_spans",
            bool(component_span_target)
            and any(
                isinstance(row, dict)
                and row.get("project") == component_span_target.get("project")
                and row.get("symbol") == component_span_target.get("symbol")
                and row.get("workspace_rel") == component_span_target.get("rel")
                and row.get("line") == component_span_target.get("line")
                and row.get("end_line") == component_span_target.get("end_line")
                and row.get("source_lines") == component_span_target.get("source_lines")
                for row in component_symbol_inspection.get("atlas_symbols", [])
            )
            and f"start_line: {component_span_target.get('line')}" in component_symbol_brief
            and f"end_line: {component_span_target.get('end_line')}" in component_symbol_brief
            and f"source_lines: {json.dumps(component_span_target.get('source_lines'), ensure_ascii=False)}" in component_symbol_brief,
            {
                "target": component_span_target,
                "brief": component_symbol_brief[:1600],
                "atlas_symbols": component_symbol_inspection.get("atlas_symbols", [])[:3],
            },
        ),
        _check(
            "inspect_file_brief_preserves_project_specific_target_refs_for_ambiguous_names",
            "P1::apps/one/App.tsx" in ambiguous_file_brief
            and "P2::apps/two/App.tsx" in ambiguous_file_brief
            and "P1::apps/two/App.tsx" not in ambiguous_file_brief
            and "P2::apps/one/App.tsx" not in ambiguous_file_brief
            and "Do not edit yet; this target maps to multiple project-scoped targets." in ambiguous_file_brief
            and "Do not patch while multiple target_refs are present." in ambiguous_file_brief
            and "Make the smallest code change" not in ambiguous_file_brief,
            {"brief": ambiguous_file_brief[:1200]},
        ),
        _check(
            "inspect_symbol_default_scope_is_main_not_variation_edit_surface",
            'target: "App"' in default_symbol_brief
            and 'symbol_query: "App"' in default_symbol_brief
            and 'project_scope: "MAIN"' in default_symbol_brief
            and 'target_query_ref: "MAIN::App"' in default_symbol_brief
            and "target_query_ref_usage:" in default_symbol_brief
            and "MAIN::src/App.tsx" in default_symbol_brief
            and "Variations/" not in default_symbol_brief
            and "Do not edit yet; this target maps to multiple project-scoped targets." in default_symbol_brief
            and "Make the smallest code change" not in default_symbol_brief,
            {"brief": default_symbol_brief[:1200]},
        ),
        _check(
            "inspect_symbol_brief_carries_target_symbol_spans",
            "target_spans:" in default_symbol_brief
            and "target_spans_shown:" in default_symbol_brief
            and "target_spans_omitted:" in default_symbol_brief
            and "symbol: \"App\"" in default_symbol_brief
            and "line_status: \"available\"" in default_symbol_brief
            and "start_line:" in default_symbol_brief
            and "end_line:" in default_symbol_brief
            and "target_file: \"src/App.tsx\"" in default_symbol_brief
            and "target_ref: \"MAIN::src/App.tsx\"" in default_symbol_brief,
            {"brief": default_symbol_brief[:1600]},
        ),
        _check(
            "inspect_symbol_brief_explains_missing_source_snippet",
            "target_source_snippets_shown:" in default_symbol_brief
            and "target_source_snippets_omitted:" in default_symbol_brief
            and "target_source_snippet_status:" in default_symbol_brief
            and (
                'target_source_snippet_status: "included"' in default_symbol_brief
                or "target_source_snippet_next_action:" in default_symbol_brief
            ),
            {"brief": default_symbol_brief[:1800]},
        ),
        _check(
            "inspect_symbol_all_project_scope_is_explicit_and_fail_closed_for_edits",
            'target: "App"' in all_project_symbol_brief
            and 'symbol_query: "App"' in all_project_symbol_brief
            and 'project_scope: "all"' in all_project_symbol_brief
            and 'target_query_ref: "App"' in all_project_symbol_brief
            and "target_query_ref_usage:" in all_project_symbol_brief
            and "Variations/" in all_project_symbol_brief
            and "Do not edit yet; this target maps to multiple project-scoped targets." in all_project_symbol_brief
            and "Make the smallest code change" not in all_project_symbol_brief,
            {"brief": all_project_symbol_brief[:1200]},
        ),
        _check(
            "impact_radius_brief_preserves_target_ref_without_debug_terms",
            impact_brief.startswith("# Impact Radius Brief")
            and "analysis_root:" in impact_brief
            and 'target_project: "MAIN"' in impact_brief
            and 'target_file: "src/App.tsx"' in impact_brief
            and 'target_ref: "MAIN::src/App.tsx"' in impact_brief
            and "radius_depth: 2" in impact_brief
            and 'evidence_basis: "static dependency graph"' in impact_brief
            and "direct_dependents_omitted:" in impact_brief
            and "transitive_dependents_omitted:" in impact_brief
            and "depth-limited bounded sample" in impact_brief
            and "directive:" in impact_brief
            and "next_action:" in impact_brief
            and '  inspect_first:\n    - "src/App.tsx"\n    - "src/main.tsx"' in impact_brief
            and "follow_up:" in impact_brief
            and "when_to_use:" in impact_brief
            and "depth=3" in impact_brief
            and "depth=0" in impact_brief
            and "path_contract:" in impact_brief
            and "target_ref_usage:" in impact_brief
            and "source_grounding:" in impact_brief
            and 'source_snapshot_status: "ok"' in impact_brief
            and 'drift_check_status: "match"' in impact_brief
            and "target_spans:" in impact_brief
            and "target_source_snippets:" in impact_brief
            and "snippet_status: \"included\"" in impact_brief
            and "1: import React from 'react';" in impact_brief
            and 'symbol: "App"' in impact_brief
            and _has_passing_context_budget(impact_brief)
            and 'file: "src/main.tsx"' in impact_brief
            and "depth: 1" in impact_brief
            and 'direct_dependents:' in impact_brief
            and 'file: "src/routes.tsx"' in impact_brief
            and "depth: 2" in impact_brief
            and "atlas_node" not in impact_brief
            and "SOVEREIGN_ELITE" not in impact_brief
            and "output/.raw" not in impact_brief
            and "duration_ms:" not in impact_brief
            and "slow_warning:" not in impact_brief
            and "dependency_graph_source:" not in impact_brief,
            {"brief": impact_brief[:900]},
        ),
        _check(
            "impact_radius_missing_target_blocks_deeper_scope_until_grounded",
            'target_grounding_status: "missing_or_unindexed"' in missing_impact_brief
            and 'next_action: "refresh_target_analysis_before_impact_decision"' in missing_impact_brief
            and "Do not request deeper impact scope until this target is grounded" in missing_impact_brief
            and "not_available_until_target_grounded" in missing_impact_brief
            and "depth=3" not in missing_impact_brief
            and "depth=0" not in missing_impact_brief,
            {"brief": missing_impact_brief[:1200]},
        ),
        _check(
            "test_impact_brief_preserves_target_ref_without_debug_terms",
            test_impact_brief.startswith("# Test Impact Brief")
            and "analysis_root:" in test_impact_brief
            and 'target_project: "MAIN"' in test_impact_brief
            and 'target_file: "src/App.tsx"' in test_impact_brief
            and 'target_ref: "MAIN::src/App.tsx"' in test_impact_brief
            and "target_exists: true" in test_impact_brief
            and "target_indexed: true" in test_impact_brief
            and 'target_grounding_status: "grounded"' in test_impact_brief
            and "path_contract:" in test_impact_brief
            and "target_ref_usage:" in test_impact_brief
            and "source_grounding:" in test_impact_brief
            and 'source_snapshot_status: "ok"' in test_impact_brief
            and 'drift_check_status: "match"' in test_impact_brief
            and "target_spans:" in test_impact_brief
            and "target_source_snippets:" in test_impact_brief
            and "target_source_snippet_usage:" in test_impact_brief
            and "snippet_status: \"included\"" in test_impact_brief
            and "test_source_snippet_limit: 1" in test_impact_brief
            and "test_source_snippets_attached: 1" in test_impact_brief
            and "source_snippets:" in test_impact_brief
            and "source_snippet_status:" in test_impact_brief
            and "snippet_status: \"included_test_evidence_line\"" in test_impact_brief
            and "6:   it('renders shell', () => {" in test_impact_brief
            and _has_passing_context_budget(test_impact_brief)
            and "Do not grep the whole test tree before using listed source_snippets" in test_impact_brief
            and "atlas_node" not in test_impact_brief
            and "SOVEREIGN_ELITE" not in test_impact_brief
            and "output/.raw" not in test_impact_brief,
            {"brief": test_impact_brief[:900]},
        ),
        _check(
            "test_impact_filled_explains_omitted_test_snippets",
            "source_snippet_status: \"included\"" in test_impact_brief
            and "source_snippet_status: \"omitted_context_budget\"" in test_impact_brief
            and "source_snippet_note:" in test_impact_brief
            and "Snippet omitted because this test is outside the bounded test-source snippet budget" in test_impact_brief,
            {"brief": test_impact_brief[:1800]},
        ),
        _check(
            "confidence_brief_has_agent_path_contract_without_debug_terms",
            confidence_brief.startswith("# Confidence Brief")
            and "analysis_root:" in confidence_brief
            and 'target_file: "src/App.tsx"' in confidence_brief
            and 'target_ref: "MAIN::src/App.tsx"' in confidence_brief
            and "target_exists: true" in confidence_brief
            and "target_indexed: true" in confidence_brief
            and 'target_grounding_status: "grounded"' in confidence_brief
            and "path_contract:" in confidence_brief
            and "target_ref_usage:" in confidence_brief
            and "source_grounding:" in confidence_brief
            and 'source_snapshot_status: "ok"' in confidence_brief
            and 'drift_check_status: "match"' in confidence_brief
            and "target_spans:" in confidence_brief
            and "target_source_snippets:" in confidence_brief
            and "target_source_snippet_usage:" in confidence_brief
            and "snippet_status: \"included\"" in confidence_brief
            and _has_passing_context_budget(confidence_brief)
            and "atlas_node" not in confidence_brief
            and "SOVEREIGN_ELITE" not in confidence_brief
            and "output/.raw" not in confidence_brief,
            {"brief": confidence_brief[:900]},
        ),
        _check(
            "upstream_trace_brief_has_agent_path_contract_without_debug_terms",
            upstream_brief.startswith("# Upstream Cause Brief")
            and "analysis_root:" in upstream_brief
            and 'target_file: "src/App.tsx"' in upstream_brief
            and 'target_ref: "MAIN::src/App.tsx"' in upstream_brief
            and "path_contract:" in upstream_brief
            and "target_ref_usage:" in upstream_brief
            and "source_grounding:" in upstream_brief
            and 'source_snapshot_status: "ok"' in upstream_brief
            and 'drift_check_status: "match"' in upstream_brief
            and "target_spans:" in upstream_brief
            and "target_source_snippets:" in upstream_brief
            and "target_source_snippet_usage:" in upstream_brief
            and "snippet_status: \"included\"" in upstream_brief
            and _has_passing_context_budget(upstream_brief)
            and "upstream_candidates_shown:" in upstream_brief
            and "upstream_candidates_omitted:" in upstream_brief
            and "follow_up:" in upstream_brief
            and "downstream_impact:" in upstream_brief
            and "depth=3" in upstream_brief
            and "upstream_candidates:" in upstream_brief
            and "target_ref: \"MAIN::src/main.tsx\"" in upstream_brief
            and "bidirectional_dependency: true" in upstream_brief
            and "inspect both import directions before editing" in upstream_brief
            and "dependency_evidence_snippets:" in upstream_brief
            and "snippet_status: \"included_dependency_evidence_line\"" in upstream_brief
            and "matched_line: 2" in upstream_brief
            and "2: import App from './App';" in upstream_brief
            and "dependency_evidence_snippets_omitted: 0" in upstream_brief
            and "direct_dependents_sample:" in upstream_brief
            and "atlas_node" not in upstream_brief
            and "SOVEREIGN_ELITE" not in upstream_brief
            and "output/.raw" not in upstream_brief,
            {"brief": upstream_brief[:900]},
        ),
        _check(
            "patch_validation_brief_has_agent_path_contract_without_debug_terms",
            patch_validation_brief.startswith("# Patch Validation Brief")
            and "analysis_root:" in patch_validation_brief
            and 'target_file: "src/App.tsx"' in patch_validation_brief
            and 'target_ref: "MAIN::src/App.tsx"' in patch_validation_brief
            and "path_contract:" in patch_validation_brief
            and "target_ref_usage:" in patch_validation_brief
            and "source_grounding:" in patch_validation_brief
            and 'source_snapshot_status: "ok"' in patch_validation_brief
            and 'drift_check_status: "match"' in patch_validation_brief
            and "target_spans:" in patch_validation_brief
            and "target_source_snippets:" in patch_validation_brief
            and "snippet_status: \"included\"" in patch_validation_brief
            and "proposed_change_snippets:" in patch_validation_brief
            and "snippet_status: \"included_patch_hunk\"" in patch_validation_brief
            and "-import React from 'react';" in patch_validation_brief
            and _has_passing_context_budget(patch_validation_brief)
            and "atlas_node" not in patch_validation_brief
            and "SOVEREIGN_ELITE" not in patch_validation_brief
            and "output/.raw" not in patch_validation_brief,
            {"brief": patch_validation_brief[:900]},
        ),
        _check(
            "work_queue_brief_has_agent_path_contract_without_debug_terms",
            work_queue_brief.startswith("# Technical Debt Work Queue")
            and "analysis_root:" in work_queue_brief
            and 'target_file: "src/App.tsx"' in work_queue_brief
            and 'target_ref: "MAIN::src/App.tsx"' in work_queue_brief
            and "raw_page_items:" in work_queue_brief
            and "grouped_work_items:" in work_queue_brief
            and "raw_page_items_grouped:" in work_queue_brief
            and "grouping_policy:" in work_queue_brief
            and "returned_work_items: 2" in work_queue_brief
            and "src/App.tsx imports ./service" in work_queue_brief
            and "src/service.ts" in work_queue_brief
            and "src/service.ts imports ./helper" in work_queue_brief
            and "path_contract:" in work_queue_brief
            and "target_ref_usage:" in work_queue_brief
            and "edit_focus:" in work_queue_brief
            and "source_grounding:" in work_queue_brief
            and "source_snapshot_status:" in work_queue_brief
            and "target_source_snippets:" in work_queue_brief
            and "snippet_status: \"included\"" in work_queue_brief
            and "evidence_source_snippets:" in work_queue_brief
            and "snippet_status: \"included_evidence_line\"" in work_queue_brief
            and "1: import service from './service';" in work_queue_brief
            and "detail_status: \"omitted_progressive_disclosure\"" in work_queue_brief
            and "request a filtered queue or inspect_file before editing" in work_queue_brief
            and _has_passing_context_budget(work_queue_brief)
            and "atlas_node" not in work_queue_brief
            and "SOVEREIGN_ELITE" not in work_queue_brief
            and "output/.raw" not in work_queue_brief,
            {"brief": work_queue_brief[:900]},
        ),
        _check(
            "source_grounding_large_spans_are_partial_not_blind_omissions",
            (lambda snippet: (
                snippet.get("snippet_status") == "partial_included_span_too_large"
                and snippet.get("shown_lines") == 20
                and snippet.get("omitted_lines") == 41
                and snippet.get("source_lines") == "L10-L70"
                and snippet.get("line_count") == 61
                and snippet.get("snippet_strategy") == "boundary_slice_for_large_symbol"
                and snippet.get("snippet_role") == "orientation"
                and snippet.get("omitted_context_policy") == "do_not_read_or_edit_all_omitted_lines_by_default"
                and snippet.get("edit_scope") == "do_not_edit_omitted_lines_without_follow_up"
                and "inspect_file(file_path=" in str(snippet.get("follow_up_if_needed") or "")
                and "line_start=22" in str(snippet.get("follow_up_if_needed") or "")
                and "line_end=41" in str(snippet.get("follow_up_if_needed") or "")
                and "10: line 10" in str(snippet.get("code") or "")
                and "21: line 21" in str(snippet.get("code") or "")
                and "63: line 63" in str(snippet.get("code") or "")
                and "70: line 70" in str(snippet.get("code") or "")
                and "omitted_span_too_large" not in str(snippet.get("snippet_status") or "")
            ))(
                (
                    mcp_server._bounded_source_snippets(
                        "\n".join(f"line {index}" for index in range(1, 81)),
                        [
                            {
                                "symbol": "largeHook",
                                "source_lines": "L10-L70",
                                "target_ref": "MAIN::src/large-hook.ts",
                                "start_line": 10,
                                "end_line": 70,
                            }
                        ],
                        max_snippets=1,
                        max_lines_per_snippet=20,
                        max_total_chars=2000,
                    )
                    or [{}]
                )[0]
            ),
            "Large source spans must include a bounded first slice plus omitted-line metadata instead of leaving the agent blind.",
        ),
        _check(
            "surgical_brief_partial_snippet_guard_is_visible",
            "snippet_status: partial_included_span_too_large" in partial_snippet_brief
            and "source_lines: L10-L70" in partial_snippet_brief
            and "line_count: 61" in partial_snippet_brief
            and "omitted_lines: 41" in partial_snippet_brief
            and "line_start=22" in partial_snippet_brief
            and "line_end=41" in partial_snippet_brief
            and "snippet_strategy: boundary_slice_for_large_symbol" in partial_snippet_brief
            and "follow_up_if_needed:" in partial_snippet_brief
            and "follow_up_before_patch:" in partial_snippet_brief
            and "not one-shot patch-ready" in partial_snippet_brief
            and "edit_scope: do_not_edit_omitted_lines_without_follow_up" in partial_snippet_brief
            and "Do not edit omitted source lines from a partial snippet alone" in partial_snippet_brief
            and _has_passing_context_budget(partial_snippet_brief),
            {"brief": partial_snippet_brief[:1200]},
        ),
        _check(
            "inspect_file_line_range_returns_source_grounded_snippet",
            "requested_line_range:" in ranged_file_brief
            and "line_start: 1" in ranged_file_brief
            and "line_end: 5" in ranged_file_brief
            and "snippet_status: \"included\"" in ranged_file_brief
            and "source_lines: \"L1-L5\"" in ranged_file_brief
            and "target_ref: \"MAIN::src/App.tsx\"" in ranged_file_brief
            and _has_passing_context_budget(ranged_file_brief),
            {"brief": ranged_file_brief[:1200]},
        ),
        _check(
            "surgical_brief_includes_bounded_advisory_engineering_principles",
            synthetic_brief.startswith("# Repository Surgical Brief")
            and "analysis_root:" in synthetic_brief
            and "path_contract:" in synthetic_brief
            and "target_files:" in synthetic_brief
            and "target_refs:" in synthetic_brief
            and "inspect_first:" in synthetic_brief
            and "validate_patch(target_file, patch_content)" in synthetic_brief
            and "get_test_impact(target_file)" in synthetic_brief
            and "source_grounding:" in synthetic_brief
            and "target_source_snippets:" in synthetic_brief
            and "snippet_status:" in synthetic_brief
            and "code: |-" in synthetic_brief
            and "engineering_principles:" in synthetic_brief
            and _has_passing_context_budget(synthetic_brief)
            and "principle.scope.smallest_safe_patch" in synthetic_brief
            and "advisory" in synthetic_brief
            and "architecture_profile:" not in synthetic_brief
            and "atlas_node" not in synthetic_brief
            and "output/.raw" not in synthetic_brief,
            {"brief": synthetic_brief[:1000]},
        ),
        _check(
            "empty_active_signals_default_brief_is_action_safe",
            empty_active_signals_brief.startswith("# ContextOS: No Active Surgery Signals")
            and "```yaml" in empty_active_signals_brief
            and "analysis_root:" in empty_active_signals_brief
            and "target_files:" in empty_active_signals_brief
            and _has_passing_context_budget(empty_active_signals_brief)
            and "Do not make a repository edit" in empty_active_signals_brief
            and "Do not invent cleanup work" in empty_active_signals_brief,
            {"brief": empty_active_signals_brief[:600]},
        ),
        _check(
            "active_signals_body_uses_target_repo_root_without_file_not_found",
            active_signal_body_brief.startswith("# ContextOS: Active Surgery Signals")
            and "Bodies Included:** True" in active_signal_body_brief
            and "path_contract:" in active_signal_body_brief
            and "target_files:" in active_signal_body_brief
            and "target_refs:" in active_signal_body_brief
            and "context_budget:" in active_signal_body_brief
            and "#### File Body:" in active_signal_body_brief
            and "[File not found on disk]" not in active_signal_body_brief
            and ("```ts" in active_signal_body_brief or "```tsx" in active_signal_body_brief or "```txt" in active_signal_body_brief),
            {"brief": active_signal_body_brief[:900]},
        ),
        _check(
            "agent_context_payloads_stay_context_window_friendly",
            len(operator_json) < 90000 and len(surgical_json) < 90000,
            {"operator_chars": len(operator_json), "surgical_chars": len(surgical_json)},
        ),
    ]
    status = "PASS" if all(check["passed"] for check in checks) else "FAIL"
    _log(f"{status} checks ({sum(1 for check in checks if check['passed'])}/{len(checks)})")
    payload = {
        "meta": {
            "kind": "agent_context_payload_validation",
            "version": "v1",
            "generated_at": _utc_now(),
            "generator": "tools.validate_agent_context_payloads",
        },
        "summary": {
            "status": status,
            "checks": len(checks),
            "passed": sum(1 for check in checks if check["passed"]),
        },
        "checks": checks,
    }
    _timed_step("write_agent_context_payload_artifacts", lambda: save_json_atomic(RAW_DIR / "agent_context_payload_validation.json", payload))
    _timed_step("write_agent_context_payload_report", lambda: save_text_atomic(REPORTS_DIR / "agent_context_payload_validation.md", render_report(payload)))
    return payload


def render_report(payload: dict[str, Any]) -> str:
    lines = [
        "# Agent Context Payload Validation",
        "",
        f"- status: `{payload.get('summary', {}).get('status')}`",
        f"- passed: `{payload.get('summary', {}).get('passed')}/{payload.get('summary', {}).get('checks')}`",
        "",
        "| Check | Passed |",
        "|---|---|",
    ]
    for check in payload.get("checks", []):
        lines.append(f"| `{check.get('name')}` | `{check.get('passed')}` |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    db_path = RAW_DIR / "codemaps.db"
    if not db_path.exists():
        summary = {
            "status": "SKIP",
            "reason": "requires_run_artifacts",
            "required_artifact": str(db_path.relative_to(ROOT)).replace("\\", "/"),
        }
        _log("SKIP requires_run_artifacts: output/.raw/codemaps.db is missing")
        print(json.dumps(summary, ensure_ascii=False))
        return 0
    payload = validate_agent_context_payloads()
    print(json.dumps(payload.get("summary", {}), ensure_ascii=False))
    return 0 if payload.get("summary", {}).get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
