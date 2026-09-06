from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
CODE_MAPS_DIR = TOOLS_DIR.parent
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))

from tools.core.config import CONFIG_FILE, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic, DOCTRINE
from tools.core.artifact_trust import build_artifact_trust_summary
from tools.core.json_io import load_json_file, load_json_strict
from tools.core.doctrine_contract import require_doctrine_path
from tools.core.analysis_scope_authority import BOUNDED_PROJECT_SELECTION, COMPLETE_REPOSITORY

RAW_OUTPUT_PATH = RAW_DIR / "artifact_trust_validation.json"
REPORT_OUTPUT_PATH = REPORTS_DIR / "artifact_trust_validation.md"
VALID_SYMBOL_RE = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")



def _check(name: str, passed: bool, details: str) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _expected_analysis_projects(
    trust_summary: dict[str, Any],
    config_projects: set[str],
) -> tuple[set[str], str]:
    scope = trust_summary.get("scope") if isinstance(trust_summary, dict) else {}
    scope = scope if isinstance(scope, dict) else {}
    status = str(scope.get("evidence_status") or "")
    effective = scope.get("effective_runtime_projects")
    if isinstance(effective, dict):
        effective_projects = {str(project) for project in effective if str(project)}
    elif isinstance(effective, list):
        effective_projects = {str(project) for project in effective if str(project)}
    else:
        effective_projects = set()
    if status in {COMPLETE_REPOSITORY, BOUNDED_PROJECT_SELECTION} and effective_projects:
        return effective_projects, f"analysis_scope_authority:{status}"
    return set(config_projects), "config_fallback_due_to_unusable_scope_authority"


def _iter_chain_pairs(chain: list[str]) -> list[tuple[str, str]]:
    if len(chain) < 2:
        return []
    return [(chain[idx], chain[idx + 1]) for idx in range(len(chain) - 1)]


def _self_transition_count(chain: list[str]) -> int:
    count = 0
    for src, dst in _iter_chain_pairs(chain):
        if src == dst:
            count += 1
    return count


def _extract_report_self_cycles(report_text: str) -> list[str]:
    suspicious: list[str] = []
    for raw_line in report_text.splitlines():
        if "->" not in raw_line or "`" not in raw_line:
            continue
        chunks = re.findall(r"`([^`]+)`", raw_line)
        if not chunks:
            continue
        chain = chunks[0]
        parts = [part.strip() for part in chain.split("->") if part.strip()]
        if len(parts) < 2:
            continue
        for idx in range(len(parts) - 1):
            if parts[idx] == parts[idx + 1]:
                suspicious.append(chain)
                break
    return suspicious


def run_validation() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    required_list = require_doctrine_path("required_artifacts", expected_type=list)
    required_raw = {Path(p).stem: RAW_DIR / p for p in required_list}

    for key, path in required_raw.items():
        payload = load_json_file(path, None)
        exists = payload is not None and payload not in ({}, [])
        shadow_size = path.stat().st_size if path.exists() else 0
        checks.append(
            _check(
                f"artifact_exists:{key}",
                exists,
                f"path={path.name} sqlite_or_shadow_payload={exists} shadow_size={shadow_size}",
            )
        )

    config = load_json_file(CONFIG_FILE, {})
    atlas = load_json_file(required_raw["atlas"], {})
    circular = load_json_file(required_raw["circular_deps"], {})
    dead_code = load_json_file(required_raw["dead_code"], {})
    dead_code_tuning = load_json_file(required_raw["dead_code_tuning"], {})
    react_support = load_json_file(RAW_DIR / "react_support_matrix.json", {})
    state_flow = load_json_file(RAW_DIR / "state_flow.json", {})
    decision_evidence = load_json_file(RAW_DIR / "decision_evidence.json", {})
    host_merge = load_json_file(RAW_DIR / "host_merge_intelligence.json", {})
    merge_plan = load_json_file(required_raw["merge_plan_summary"], {})
    audit = load_json_file(RAW_DIR / "audit_report.json", {})
    trust_summary = build_artifact_trust_summary(RAW_DIR)

    for row in trust_summary.get("checks", []):
        checks.append(
            _check(
                f"artifact_chain:{row.get('name')}",
                bool(row.get("passed")),
                str(row.get("details") or ""),
            )
        )

    config_projects = set((config.get("variations") or {}).keys()) if isinstance(config, dict) else set()
    atlas_projects = set(atlas.keys()) if isinstance(atlas, dict) else set()
    expected_analysis_projects, project_scope_source = _expected_analysis_projects(
        trust_summary,
        config_projects,
    )
    checks.append(
        _check(
            "project_alignment:config_vs_atlas",
            bool(config_projects) and config_projects.issubset(atlas_projects),
            f"config={sorted(config_projects)} atlas={sorted(atlas_projects)}",
        )
    )

    dead_by_project = set((dead_code.get("by_project") or {}).keys()) if isinstance(dead_code, dict) else set()
    checks.append(
        _check(
            "project_alignment:dead_code_by_project",
            dead_by_project == expected_analysis_projects,
            f"dead_code={sorted(dead_by_project)} expected={sorted(expected_analysis_projects)} source={project_scope_source}",
        )
    )
    dead_summary_conf = (dead_code.get("summary", {}) or {}).get("confidence", {}) if isinstance(dead_code, dict) else {}
    dead_score = dead_summary_conf.get("score") if isinstance(dead_summary_conf, dict) else None
    checks.append(
        _check(
            "dead_code:workspace_confidence_in_range",
            isinstance(dead_score, (int, float)) and 0.0 <= float(dead_score) <= 1.0,
            f"workspace_confidence={dead_summary_conf}",
        )
    )
    dead_project_conf_ok = True
    dead_project_conf_details: list[str] = []
    dead_by_project_payload = dead_code.get("by_project", {}) if isinstance(dead_code, dict) else {}
    if isinstance(dead_by_project_payload, dict):
        for project_name, payload in dead_by_project_payload.items():
            confidence = (((payload.get("summary", {}) or {}).get("confidence", {})) if isinstance(payload, dict) else {})
            score = confidence.get("score") if isinstance(confidence, dict) else None
            if not (isinstance(score, (int, float)) and 0.0 <= float(score) <= 1.0):
                dead_project_conf_ok = False
                dead_project_conf_details.append(f"{project_name}: confidence={confidence}")
    checks.append(
        _check(
            "dead_code:project_confidence_in_range",
            dead_project_conf_ok,
            "; ".join(dead_project_conf_details) if dead_project_conf_details else "all_projects_ok",
        )
    )
    tuning_summary = dead_code_tuning.get("summary", {}) if isinstance(dead_code_tuning, dict) else {}
    tuning_rules = dead_code_tuning.get("suggested_rules", []) if isinstance(dead_code_tuning, dict) else []
    tuning_low_total = tuning_summary.get("low_total") if isinstance(tuning_summary, dict) else None
    dead_low = (dead_code.get("summary", {}) or {}).get("low") if isinstance(dead_code, dict) else None
    checks.append(
        _check(
            "dead_code_tuning:low_total_parity",
            isinstance(tuning_low_total, int) and isinstance(dead_low, int) and tuning_low_total == dead_low,
            f"tuning_low={tuning_low_total} dead_low={dead_low}",
        )
    )
    checks.append(
        _check(
            "dead_code_tuning:suggested_rules_shape",
            isinstance(tuning_rules, list),
            f"suggested_rules_type={type(tuning_rules).__name__}",
        )
    )

    react_by_project = set((react_support.get("by_project") or {}).keys()) if isinstance(react_support, dict) else set()
    checks.append(
        _check(
            "project_alignment:react_support_by_project",
            react_by_project == expected_analysis_projects,
            f"react_support={sorted(react_by_project)} expected={sorted(expected_analysis_projects)} source={project_scope_source}",
        )
    )

    react_confidence_ok = True
    react_confidence_details: list[str] = []
    if isinstance(react_support, dict):
        by_project_payload = react_support.get("by_project", {}) or {}
        for project_name, project_payload in (by_project_payload.items() if isinstance(by_project_payload, dict) else []):
            confidence = project_payload.get("confidence", {}) if isinstance(project_payload, dict) else {}
            score = confidence.get("score") if isinstance(confidence, dict) else None
            if not (isinstance(score, (int, float)) and 0.0 <= float(score) <= 1.0):
                react_confidence_ok = False
                react_confidence_details.append(f"{project_name}: confidence={confidence}")
    checks.append(
        _check(
            "react_support:project_confidence_in_range",
            react_confidence_ok,
            "; ".join(react_confidence_details) if react_confidence_details else "all_projects_ok",
        )
    )

    state_flow_by_project = set((state_flow.get("by_project") or {}).keys()) if isinstance(state_flow, dict) else set()
    checks.append(
        _check(
            "project_alignment:state_flow_by_project",
            state_flow_by_project == expected_analysis_projects,
            f"state_flow={sorted(state_flow_by_project)} expected={sorted(expected_analysis_projects)} source={project_scope_source}",
        )
    )

    decision_by_project = set((decision_evidence.get("by_project") or {}).keys()) if isinstance(decision_evidence, dict) else set()
    checks.append(
        _check(
            "project_alignment:decision_evidence_by_project",
            decision_by_project == expected_analysis_projects,
            f"decision={sorted(decision_by_project)} expected={sorted(expected_analysis_projects)} source={project_scope_source}",
        )
    )
    decision_confidence_ok = True
    decision_confidence_details: list[str] = []
    decision_by_project_payload = decision_evidence.get("by_project", {}) if isinstance(decision_evidence, dict) else {}
    if isinstance(decision_by_project_payload, dict):
        for project_name, project_payload in decision_by_project_payload.items():
            confidence = project_payload.get("confidence", {}) if isinstance(project_payload, dict) else {}
            score = confidence.get("score") if isinstance(confidence, dict) else None
            tier = confidence.get("tier") if isinstance(confidence, dict) else None
            if not (isinstance(score, (int, float)) and 0.0 <= float(score) <= 1.0 and isinstance(tier, str) and tier):
                decision_confidence_ok = False
                decision_confidence_details.append(f"{project_name}: confidence={confidence}")
    checks.append(
        _check(
            "decision_evidence:project_confidence_in_range",
            decision_confidence_ok,
            "; ".join(decision_confidence_details) if decision_confidence_details else "all_projects_ok",
        )
    )
    decision_workspace_conf = (
        (decision_evidence.get("meta", {}) or {}).get("confidence", {})
        if isinstance(decision_evidence, dict)
        else {}
    )
    decision_workspace_score = decision_workspace_conf.get("score") if isinstance(decision_workspace_conf, dict) else None
    checks.append(
        _check(
            "decision_evidence:workspace_confidence_in_range",
            isinstance(decision_workspace_score, (int, float)) and 0.0 <= float(decision_workspace_score) <= 1.0,
            f"workspace_confidence={decision_workspace_conf}",
        )
    )

    workspace_mode = host_merge.get("workspace_mode", {}) if isinstance(host_merge, dict) else {}
    host_studios = host_merge.get("studios", {}) if isinstance(host_merge, dict) else {}
    comparative_enabled = bool(workspace_mode.get("comparative_enabled"))
    comparative_scope_expected = comparative_enabled and len(expected_analysis_projects) > 1
    host_studio_ok = bool(host_studios) if comparative_scope_expected else True
    checks.append(
        _check(
            "host_merge:studio_inventory_present_when_comparative",
            host_studio_ok,
            f"comparative_enabled={comparative_enabled} scope_projects={sorted(expected_analysis_projects)} studios={len(host_studios) if isinstance(host_studios, dict) else 0}",
        )
    )
    host_summary = host_merge.get("summary", {}) if isinstance(host_merge, dict) else {}
    host_conf = host_summary.get("confidence", {}) if isinstance(host_summary, dict) else {}
    host_score = host_conf.get("score") if isinstance(host_conf, dict) else None
    checks.append(
        _check(
            "host_merge:workspace_confidence_in_range",
            isinstance(host_score, (int, float)) and 0.0 <= float(host_score) <= 1.0,
            f"workspace_confidence={host_conf}",
        )
    )
    host_studio_conf_ok = True
    host_studio_conf_details: list[str] = []
    if isinstance(host_studios, dict):
        for studio_name, studio_payload in host_studios.items():
            merge_summary = studio_payload.get("merge_summary", {}) if isinstance(studio_payload, dict) else {}
            confidence = merge_summary.get("confidence", {}) if isinstance(merge_summary, dict) else {}
            score = confidence.get("score") if isinstance(confidence, dict) else None
            tier = confidence.get("tier") if isinstance(confidence, dict) else None
            if not (isinstance(score, (int, float)) and 0.0 <= float(score) <= 1.0 and isinstance(tier, str) and tier):
                host_studio_conf_ok = False
                host_studio_conf_details.append(f"{studio_name}: confidence={confidence}")
    checks.append(
        _check(
            "host_merge:studio_confidence_in_range",
            host_studio_conf_ok,
            "; ".join(host_studio_conf_details) if host_studio_conf_details else "all_studios_ok",
        )
    )
    merge_workspace_conf = (merge_plan.get("summary", {}) or {}).get("confidence", {}) if isinstance(merge_plan, dict) else {}
    merge_workspace_score = merge_workspace_conf.get("score") if isinstance(merge_workspace_conf, dict) else None
    checks.append(
        _check(
            "merge_plan:workspace_confidence_in_range",
            isinstance(merge_workspace_score, (int, float)) and 0.0 <= float(merge_workspace_score) <= 1.0,
            f"workspace_confidence={merge_workspace_conf}",
        )
    )
    merge_by_source = merge_plan.get("by_source_project", {}) if isinstance(merge_plan, dict) else {}
    merge_project_keys = set(merge_by_source.keys()) if isinstance(merge_by_source, dict) else set()
    checks.append(
        _check(
            "project_alignment:merge_plan_by_source_subset",
            merge_project_keys.issubset(expected_analysis_projects),
            f"merge_by_source={sorted(merge_project_keys)} expected={sorted(expected_analysis_projects)} source={project_scope_source}",
        )
    )
    merge_source_conf_ok = True
    merge_source_conf_details: list[str] = []
    if isinstance(merge_by_source, dict):
        for project_name, payload in merge_by_source.items():
            confidence = payload.get("confidence", {}) if isinstance(payload, dict) else {}
            score = confidence.get("score") if isinstance(confidence, dict) else None
            tier = confidence.get("tier") if isinstance(confidence, dict) else None
            if not (isinstance(score, (int, float)) and 0.0 <= float(score) <= 1.0 and isinstance(tier, str) and tier):
                merge_source_conf_ok = False
                merge_source_conf_details.append(f"{project_name}: confidence={confidence}")
    checks.append(
        _check(
            "merge_plan:by_source_confidence_in_range",
            merge_source_conf_ok,
            "; ".join(merge_source_conf_details) if merge_source_conf_details else "all_sources_ok",
        )
    )

    edges = circular.get("edges", []) if isinstance(circular, dict) else []
    cycles = circular.get("cycles", []) if isinstance(circular, dict) else []
    cycles_found = int(circular.get("cycles_found", 0) or 0) if isinstance(circular, dict) else 0
    edge_set = set()
    self_edges = 0
    for edge in edges if isinstance(edges, list) else []:
        if not isinstance(edge, dict):
            continue
        src = str(edge.get("source") or "")
        dst = str(edge.get("target") or "")
        if not src or not dst:
            continue
        edge_set.add((src, dst))
        if src == dst:
            self_edges += 1

    checks.append(_check("circular:self_edges_absent", self_edges == 0, f"self_edges={self_edges}"))
    checks.append(_check("circular:cycle_count_consistent", cycles_found == len(cycles), f"cycles_found={cycles_found} len(cycles)={len(cycles)}"))
    circular_by_project = circular.get("by_project", {}) if isinstance(circular, dict) else {}
    circular_project_keys = set(circular_by_project.keys()) if isinstance(circular_by_project, dict) else set()
    checks.append(
        _check(
            "project_alignment:circular_by_project_superset",
            bool(circular_project_keys) and expected_analysis_projects.issubset(circular_project_keys),
            f"circular={sorted(circular_project_keys)} expected={sorted(expected_analysis_projects)} source={project_scope_source}",
        )
    )
    circular_shape_ok = True
    circular_shape_details: list[str] = []
    if isinstance(circular_by_project, dict):
        for project_name, project_payload in circular_by_project.items():
            if not isinstance(project_payload, dict):
                circular_shape_ok = False
                circular_shape_details.append(f"{project_name}: non_dict_payload")
                continue
            edge_count = project_payload.get("edge_count")
            cycle_count = project_payload.get("cycle_count")
            has_cycles = project_payload.get("has_cycles")
            if not (
                isinstance(edge_count, int)
                and edge_count >= 0
                and isinstance(cycle_count, int)
                and cycle_count >= 0
                and isinstance(has_cycles, bool)
            ):
                circular_shape_ok = False
                circular_shape_details.append(
                    f"{project_name}: edge_count={edge_count} cycle_count={cycle_count} has_cycles={has_cycles}"
                )
    checks.append(
        _check(
            "circular:by_project_shape_valid",
            circular_shape_ok,
            "; ".join(circular_shape_details) if circular_shape_details else "all_projects_ok",
        )
    )

    malformed_cycles = 0
    self_transition_cycles = 0
    unsupported_edges = 0
    for row in cycles if isinstance(cycles, list) else []:
        chain = row.get("chain", []) if isinstance(row, dict) else []
        if not isinstance(chain, list) or len(chain) < 2:
            malformed_cycles += 1
            continue
        if chain[0] != chain[-1]:
            malformed_cycles += 1
            continue
        unique_nodes = set(chain[:-1])
        if len(unique_nodes) < 2:
            malformed_cycles += 1
            continue
        self_transition_cycles += _self_transition_count(chain)
        for src, dst in _iter_chain_pairs(chain):
            if (src, dst) not in edge_set:
                unsupported_edges += 1

    checks.append(_check("circular:cycle_shape_valid", malformed_cycles == 0, f"malformed_cycles={malformed_cycles}"))
    checks.append(_check("circular:no_self_transition_in_cycle", self_transition_cycles == 0, f"self_transitions={self_transition_cycles}"))
    checks.append(_check("circular:cycle_edges_backed_by_graph", unsupported_edges == 0, f"unsupported_edges={unsupported_edges}"))

    dead_items = dead_code.get("items", []) if isinstance(dead_code, dict) else []
    dead_scope_mismatch = 0
    invalid_dead_symbols: list[str] = []
    for item in dead_items if isinstance(dead_items, list) else []:
        if not isinstance(item, dict):
            continue
        scoped = str(item.get("scoped_file") or "")
        project = str(item.get("project") or "")
        file_rel = str(item.get("file") or "")
        symbol = str(item.get("symbol") or "").strip()
        if symbol and not VALID_SYMBOL_RE.fullmatch(symbol):
            invalid_dead_symbols.append(symbol)
        if not scoped or "::" not in scoped:
            dead_scope_mismatch += 1
            continue
        scoped_project, scoped_file = scoped.split("::", 1)
        if scoped_project != project or scoped_file != file_rel:
            dead_scope_mismatch += 1
    checks.append(_check("dead_code:scope_consistency", dead_scope_mismatch == 0, f"mismatches={dead_scope_mismatch}"))
    checks.append(
        _check(
            "dead_code:symbol_identifier_shape",
            len(invalid_dead_symbols) == 0,
            f"invalid_symbols={len(invalid_dead_symbols)} sample={sorted(set(invalid_dead_symbols))[:8]}",
        )
    )

    circular_report = (REPORTS_DIR / "circular_deps_report.md")
    report_text = circular_report.read_text(encoding="utf-8") if circular_report.exists() else ""
    report_self_cycles = _extract_report_self_cycles(report_text)
    checks.append(
        _check(
            "circular_report:no_inline_self_cycle",
            len(report_self_cycles) == 0,
            f"suspicious_rows={len(report_self_cycles)}",
        )
    )

    summary = {
        "total_checks": len(checks),
        "passed_checks": sum(1 for row in checks if row["passed"]),
        "failed_checks": sum(1 for row in checks if not row["passed"]),
    }
    payload = {
        "meta": {"kind": "artifact_trust_validation", "version": "v1"},
        "summary": summary,
        "artifact_chain": {
            "status": trust_summary.get("status"),
            "scope": trust_summary.get("scope"),
            "freshness": trust_summary.get("freshness"),
            "failures": trust_summary.get("failures", []),
            "warnings": trust_summary.get("warnings", []),
        },
        "checks": checks,
    }

    lines = [
        "# Artifact Trust Validation",
        "",
        f"- Total checks: **{summary['total_checks']}**",
        f"- Passed: **{summary['passed_checks']}**",
        f"- Failed: **{summary['failed_checks']}**",
        f"- Artifact chain status: **{trust_summary.get('status')}**",
        "",
        "| Check | Status | Details |",
        "|---|---|---|",
    ]
    for row in checks:
        status = "PASS" if row["passed"] else "FAIL"
        lines.append(f"| `{row['name']}` | {status} | {row['details']} |")

    if report_self_cycles:
        lines.extend(
            [
                "",
                "## Suspicious Circular Report Rows",
                "",
            ]
        )
        for chain in report_self_cycles[:25]:
            lines.append(f"- `{chain}`")

    save_json_atomic(RAW_OUTPUT_PATH, payload)
    save_text_atomic(REPORT_OUTPUT_PATH, "\n".join(lines) + "\n")
    return payload


def main() -> int:
    payload = run_validation()
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["failed_checks"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
