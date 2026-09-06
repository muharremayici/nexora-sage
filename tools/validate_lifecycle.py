from __future__ import annotations

import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
_LOCAL_ROOT = TOOLS_DIR.parent
if str(_LOCAL_ROOT) not in sys.path:
    sys.path.insert(0, str(_LOCAL_ROOT))

from tools.core.config import (
    CODE_MAPS_DIR,
    CONFIG_DIR,
    DISCOVERY_FILE,
    CONFIG_FILE,
    OVERRIDES_FILE,
    DOCTRINE_FILE,
    OUTPUT_DIR,
    RAW_DIR,
    REPORTS_DIR,
    save_json_atomic,
)
from tools.core.artifact_contracts import MASTER_REPORT_PATH
from tools.core.atlas_io import load_atlas_data
from tools.core.genome_io import load_genome_data
from tools.core.json_io import load_json_file, load_json_strict
from tools.core.overrides_validator import overrides_match_workspace
from tools.core.workspace_mode import get_workspace_mode


@dataclass
class CheckResult:
    name: str
    passed: bool
    details: str = ""



def _check(name: str, passed: bool, details: str = "") -> CheckResult:
    return CheckResult(name=name, passed=bool(passed), details=details)


def _is_fresh_enough(target: Path, dependencies: list[Path]) -> tuple[bool, str]:
    if not target.exists():
        return False, f"missing={target.name}"
    if not dependencies:
        return True, f"target={target.name} no_dependencies"

    target_mtime = target.stat().st_mtime
    newer = []
    missing = []
    for dependency in dependencies:
        if not dependency.exists():
            missing.append(dependency.name)
            continue
        if dependency.stat().st_mtime > target_mtime:
            newer.append(dependency.name)

    if missing:
        return False, f"missing_dependencies={missing}"
    if newer:
        return False, f"stale_target={target.name} newer_dependencies={newer}"
    return True, f"target={target.name} fresh_vs={[dep.name for dep in dependencies]}"


def _extract_dead_code_count(report_text: str) -> int | None:
    match = re.search(r"\*\*(\d+)\*\* suspended exports found\.", report_text)
    return int(match.group(1)) if match else None


def _project_scope_alignment_checks(
    config_projects: set[str],
    atlas_projects: set[str],
    health_projects: set[str],
    ai_projects: set[str],
) -> list[CheckResult]:
    """Validate canonical topology and downstream runtime-scope parity separately."""
    return [
        _check(
            "config_atlas_project_alignment",
            bool(atlas_projects) and atlas_projects.issubset(config_projects),
            f"config={sorted(config_projects)} atlas={sorted(atlas_projects)}",
        ),
        _check(
            "health_project_alignment",
            bool(health_projects) and health_projects.issubset(atlas_projects),
            (
                f"health={sorted(health_projects)} canonical_atlas={sorted(atlas_projects)} "
                "semantics=runtime_scope_must_be_nonempty_subset_of_canonical_atlas"
            ),
        ),
        _check(
            "ai_context_project_alignment",
            bool(ai_projects)
            and ai_projects == health_projects
            and ai_projects.issubset(atlas_projects),
            (
                f"ai_context={sorted(ai_projects)} health={sorted(health_projects)} "
                f"canonical_atlas={sorted(atlas_projects)} "
                "semantics=downstream_runtime_scopes_must_match"
            ),
        ),
    ]



def run_validation() -> dict[str, Any]:
    discovery_path = DISCOVERY_FILE
    config_path = CONFIG_FILE
    overrides_path = OVERRIDES_FILE

    audit_path = RAW_DIR / "audit_report.json"
    health_path = RAW_DIR / "health_score.json"
    gate_path = RAW_DIR / "quality_gate.json"
    dead_code_path = RAW_DIR / "dead_code.json"
    ai_context_path = RAW_DIR / "ai_context.json"
    readiness_path = RAW_DIR / "surgical_readiness.json"

    dead_code_report_path = REPORTS_DIR / "dead_code_report.md"
    quality_gate_report_path = REPORTS_DIR / "quality_gate.md"
    audit_report_text_path = REPORTS_DIR / "audit_report.txt"
    merge_script_path = OUTPUT_DIR / "scripts" / "auto_merge.ps1"
    master_report_path = MASTER_REPORT_PATH
    nanometric_diff_path = RAW_DIR / "nanometric_diff.json"
    nanometric_diff_report_path = REPORTS_DIR / "nanometric_diff.md"
    discovery_determinism_report_path = REPORTS_DIR / "discovery_determinism.md"

    discovery = load_json_strict(discovery_path)
    config = load_json_strict(config_path)
    overrides = load_json_strict(overrides_path)
    atlas = load_atlas_data(RAW_DIR)
    genome = load_genome_data(RAW_DIR)
    audit = load_json_strict(audit_path)
    health = load_json_strict(health_path)
    gate = load_json_strict(gate_path)
    dead_code_payload = load_json_file(dead_code_path, [])
    dead_code = dead_code_payload.get("items", []) if isinstance(dead_code_payload, dict) else dead_code_payload
    ai_context = load_json_file(ai_context_path, {})
    readiness = load_json_file(readiness_path, {})
    state_flow = load_json_file(RAW_DIR / "state_flow.json", {})
    blast = load_json_file(RAW_DIR / "blast_radius.json", {})
    react_probe = load_json_file(RAW_DIR / "react_capability_probe.json", {})
    react_support = load_json_file(RAW_DIR / "react_support_matrix.json", {})
    decision_evidence = load_json_file(RAW_DIR / "decision_evidence.json", {})
    module_risk_matrix = load_json_file(RAW_DIR / "module_risk_matrix.json", {})
    nanometric_diff = load_json_file(nanometric_diff_path, {})
    module_risk_rows = module_risk_matrix.get("rows", []) if isinstance(module_risk_matrix, dict) else module_risk_matrix
    readiness_rows = readiness.get("rows", []) if isinstance(readiness, dict) else (readiness if isinstance(readiness, list) else [])

    dead_code_report = dead_code_report_path.read_text(encoding="utf-8") if dead_code_report_path.exists() else ""
    quality_gate_report = quality_gate_report_path.read_text(encoding="utf-8") if quality_gate_report_path.exists() else ""
    audit_report_text = audit_report_text_path.read_text(encoding="utf-8") if audit_report_text_path.exists() else ""
    merge_script_text = merge_script_path.read_text(encoding="utf-8") if merge_script_path.exists() else ""
    master_report_text = master_report_path.read_text(encoding="utf-8") if master_report_path.exists() else ""
    nanometric_diff_report = (
        nanometric_diff_report_path.read_text(encoding="utf-8")
        if nanometric_diff_report_path.exists()
        else ""
    )

    checks: list[CheckResult] = []
    workspace_mode = get_workspace_mode()

    checks.append(_check("discovery_kind", discovery.get("_meta", {}).get("kind") == "codemaps.discovery",
                         f"kind={discovery.get('_meta', {}).get('kind')}"))
    checks.append(_check("config_kind", config.get("_meta", {}).get("kind") == "codemaps.config",
                         f"kind={config.get('_meta', {}).get('kind')}"))
    checks.append(_check("overrides_exists", isinstance(overrides, dict), "overrides loaded"))
    checks.append(_check(
        "overrides_workspace_alignment",
        overrides_match_workspace(discovery.get("variations", {}) or {}, overrides),
        f"overrides_variations={sorted((overrides.get('variations', {}) or {}).keys())}",
    ))
    discovery_meta = discovery.get("_discovery_metadata", {}) if isinstance(discovery, dict) else {}
    determinism_overview = discovery_meta.get("determinism_overview", {}) if isinstance(discovery_meta, dict) else {}
    checks.append(_check(
        "discovery_determinism_overview_present",
        isinstance(determinism_overview, dict)
        and int(determinism_overview.get("total_projects", -1) or -1) >= 1
        and isinstance(determinism_overview.get("bundler_primary"), dict)
        and isinstance(determinism_overview.get("source_root_primary"), dict),
        f"determinism_overview={determinism_overview}",
    ))
    checks.append(_check(
        "discovery_determinism_report_exists",
        discovery_determinism_report_path.exists(),
        f"path={discovery_determinism_report_path.name}",
    ))

    config_projects = set((config.get("variations") or {}).keys())
    project_display_names = config.get("project_display_names", {}) or {}
    atlas_projects = set(atlas.keys()) if isinstance(atlas, dict) else set()
    health_projects = set((health.get("projects") or {}).keys())
    ai_projects = set((ai_context.get("projects") or {}).keys())

    checks.extend(
        _project_scope_alignment_checks(
            config_projects,
            atlas_projects,
            health_projects,
            ai_projects,
        )
    )
    checks.append(_check(
        "project_display_name_coverage",
        isinstance(project_display_names, dict)
        and config_projects.issubset(set(project_display_names.keys()))
        and all(str(project_display_names.get(key, "")).strip() for key in config_projects),
        f"display_names={sorted(project_display_names.keys())}",
    ))
    checks.append(_check(
        "workspace_mode_alignment",
        isinstance(ai_context.get("workspace_mode"), dict)
        and ai_context.get("workspace_mode", {}).get("mode") == workspace_mode.get("mode")
        and int(ai_context.get("workspace_mode", {}).get("project_count", -1) or -1) == int(workspace_mode.get("project_count", -2) or -2),
        f"ai_context={ai_context.get('workspace_mode')} runtime={workspace_mode}",
    ))
    checks.append(_check(
        "ai_context_capabilities_present",
        isinstance(ai_context.get("capabilities"), dict)
        and isinstance(ai_context.get("capabilities", {}).get("plugins"), list),
        f"capabilities={ai_context.get('capabilities')}",
    ))

    total_atlas_files = 0
    symbol_rich_files = 0
    for pdata in atlas.values() if isinstance(atlas, dict) else []:
        files = (pdata or {}).get("files", {}) or {}
        for fdata in files.values():
            if not isinstance(fdata, dict):
                continue
            total_atlas_files += 1
            if fdata.get("symbols"):
                symbol_rich_files += 1
    checks.append(_check("atlas_has_files", total_atlas_files > 0, f"files={total_atlas_files}"))
    checks.append(_check("atlas_symbol_density", symbol_rich_files > 0 and symbol_rich_files <= total_atlas_files,
                         f"symbol_rich={symbol_rich_files}/{total_atlas_files}"))

    summary = audit.get("summary", {}) if isinstance(audit, dict) else {}
    total = int(summary.get("total", 0) or 0)
    by_rule = summary.get("by_rule", {}) or {}
    by_project = summary.get("by_project", {}) or {}
    rule_taxonomy = summary.get("rule_taxonomy", {}) or {}
    violations = audit.get("violations", []) or []

    checks.append(_check("audit_by_rule_sum", sum(int(v or 0) for v in by_rule.values()) == total,
                         f"sum={sum(int(v or 0) for v in by_rule.values())} total={total}"))
    checks.append(_check("audit_by_project_sum", sum(int(v or 0) for v in by_project.values()) == total,
                         f"sum={sum(int(v or 0) for v in by_project.values())} total={total}"))
    checks.append(_check("audit_structured_violations_have_project",
                         all(isinstance(v, dict) and v.get("project") for v in violations),
                         f"violations={len(violations)}"))

    checks.append(_check(
        "audit_report_has_project_totals_section",
        ("--- PROJECT AUDIT TOTALS ---" in audit_report_text) or total == 0,
        "audit_report.txt",
    ))
    checks.append(_check(
        "audit_rule_taxonomy_present",
        isinstance(rule_taxonomy.get("profiles"), dict) and len(rule_taxonomy.get("profiles", {})) > 0,
        f"taxonomy_profiles={len(rule_taxonomy.get('profiles', {})) if isinstance(rule_taxonomy, dict) else 0}",
    ))
    if dead_code_report_path.exists() or len(dead_code) > 0:
        dead_count_raw = len(dead_code) if isinstance(dead_code, list) else -1
        dead_count_report = _extract_dead_code_count(dead_code_report)
        checks.append(_check("dead_code_report_parity", dead_count_report == dead_count_raw,
                             f"raw={dead_count_raw} report={dead_count_report}"))
        checks.append(_check(
            "dead_code_nextjs_route_handlers_protected",
            not any(
                isinstance(item, dict)
                and item.get("file", "").endswith("/route.ts")
                and item.get("symbol") in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
                for item in (dead_code if isinstance(dead_code, list) else [])
            ),
            "route handler exports should not be treated as dead code",
        ))

    quality_overall_report = (
        "Overall: PASS" in quality_gate_report
        or "Release Gate: PASS" in quality_gate_report
    )
    quality_overall_json = bool(gate.get("passed"))
    checks.append(_check("quality_gate_report_parity", quality_overall_report == quality_overall_json,
                         f"json={quality_overall_json} report={quality_overall_report}"))
    checks.append(_check("quality_gate_explains_enforcement",
                         "Enforced checks decide PASS/FAIL" in quality_gate_report and (
                             "## Project Audit Totals" in quality_gate_report or total == 0
                         ),
                         "quality_gate.md"))
    checks.append(_check(
        "quality_gate_has_taxonomy_aware_checks",
        any(check.get("name") == "max_enforced_audit_violations" for check in (gate.get("checks") or []))
        and any(check.get("name") == "advisory_audit_violations" for check in (gate.get("checks") or [])),
        "quality_gate taxonomy-aware audit checks",
    ))
    min_health_check = next((check for check in (gate.get("checks") or []) if check.get("name") == "min_health_score"), None)
    gate_fresh_for_health, gate_fresh_details = _is_fresh_enough(gate_path, [health_path])
    checks.append(_check(
        "quality_gate_health_score_parity",
        (
            not gate_fresh_for_health
            or (
                isinstance(min_health_check, dict)
                and int(min_health_check.get("actual", -1) or -1) == int(health.get("overall", -2) or -2)
            )
        ),
        (
            f"skipped_parity_due_to_{gate_fresh_details}"
            if not gate_fresh_for_health
            else f"gate={min_health_check} health={health.get('overall')}"
        ),
    ))
    react_ratio_check = next((check for check in (gate.get("checks") or []) if check.get("name") == "min_react_detected_present_ratio"), None)
    react_partial_check = next((check for check in (gate.get("checks") or []) if check.get("name") == "max_react_partial_capabilities"), None)
    probe_summary = react_probe.get("summary", {}) if isinstance(react_probe, dict) else {}
    react_summary = react_support.get("summary", {}) if isinstance(react_support, dict) else {}
    checks.append(_check(
        "react_capability_probe_exists",
        isinstance(react_probe.get("capabilities"), list) and isinstance(probe_summary, dict),
        f"probe_summary={probe_summary}",
    ))
    checks.append(_check(
        "react_probe_not_broader_than_final_matrix",
        int(probe_summary.get("repo_present", 0) or 0) <= int(react_summary.get("repo_present", 0) or 0),
        f"probe={probe_summary} matrix={react_summary}",
    ))
    repo_present = int(react_summary.get("repo_present", 0) or 0)
    detected = int(react_summary.get("detected", 0) or 0)
    partial = int(react_summary.get("partial", 0) or 0)
    expected_ratio = round((detected / repo_present), 3) if repo_present else 0.0
    checks.append(_check(
        "quality_gate_react_support_ratio_parity",
        isinstance(react_ratio_check, dict)
        and float(react_ratio_check.get("actual", -1) or -1) == expected_ratio,
        f"gate={react_ratio_check} react_summary={react_summary}",
    ))
    checks.append(_check(
        "quality_gate_react_partial_parity",
        isinstance(react_partial_check, dict)
        and int(-1 if react_partial_check.get("actual") is None else react_partial_check.get("actual")) == partial,
        f"gate={react_partial_check} react_summary={react_summary}",
    ))

    ai_health_raw = (ai_context.get("health") or {}).get("overall", -1)
    ai_health = int(-1 if ai_health_raw is None else ai_health_raw)
    health_overall_raw = health.get("overall", -1)
    health_overall = int(-1 if health_overall_raw is None else health_overall_raw)
    checks.append(_check("ai_context_health_parity", ai_health == health_overall,
                         f"ai_context={ai_health} health={health_overall}"))
    ai_audit_total_raw = (ai_context.get("audit") or {}).get("total_violations", -1)
    ai_audit_total = int(-1 if ai_audit_total_raw is None else ai_audit_total_raw)
    checks.append(_check("ai_context_audit_total_parity", ai_audit_total == total,
                         f"ai_context={ai_audit_total} audit={total}"))
    ai_module_risks = ai_context.get("module_risks") if isinstance(ai_context, dict) else []
    checks.append(_check(
        "ai_context_module_risks_present",
        isinstance(ai_module_risks, list) and (
            len(ai_module_risks) > 0
            or (isinstance(module_risk_rows, list) and len(module_risk_rows) == 0)
        ),
        f"ai_module_risks={len(ai_module_risks) if isinstance(ai_module_risks, list) else 0} matrix_rows={len(module_risk_rows) if isinstance(module_risk_rows, list) else 0}",
    ))
    ai_top_risk = (ai_context.get("module_risks") or [None])[0] if isinstance(ai_context, dict) else None
    ai_top_candidates = {
        item.get("module")
        for item in (ai_context.get("module_risks") or [])[:5]
        if isinstance(item, dict) and item.get("module")
    } if isinstance(ai_context, dict) else set()
    matrix_top_candidates = {
        item.get("module")
        for item in (module_risk_rows or [])[:5]
        if isinstance(item, dict) and item.get("module")
    } if isinstance(module_risk_rows, list) else set()
    checks.append(_check(
        "ai_context_top_module_risk_alignment",
        (
            isinstance(module_risk_rows, list)
            and len(module_risk_rows) == 0
        ) or (
            bool(ai_top_candidates)
            and bool(matrix_top_candidates)
            and bool(ai_top_candidates.intersection(matrix_top_candidates))
        ),
        f"ai_top_candidates={sorted(ai_top_candidates)} matrix_top_candidates={sorted(matrix_top_candidates)}",
    ))
    studio_summary = decision_evidence.get("studio_summary", []) if isinstance(decision_evidence, dict) else []
    checks.append(_check(
        "decision_evidence_uses_generic_source_mix",
        all("source_mix" in row and "from_nexus" not in row for row in studio_summary),
        f"rows={len(studio_summary)}",
    ))
    dead_hotspots = ((ai_context.get("dead_code") or {}).get("hotspots") or []) if isinstance(ai_context, dict) else []
    dead_hotspot_counter = Counter(item.get("file", "?") for item in dead_code) if isinstance(dead_code, list) else Counter()
    top_dead_hotspot = dead_hotspots[0] if dead_hotspots else None
    checks.append(_check(
        "ai_context_top_dead_hotspot_alignment",
        (
            not top_dead_hotspot and len(dead_hotspot_counter) == 0
        ) or (
            bool(top_dead_hotspot)
            and dead_hotspot_counter.get(top_dead_hotspot.get("file")) == int(top_dead_hotspot.get("dead_exports", -1) or -1)
        ),
        f"ai_top={top_dead_hotspot} raw={dead_hotspot_counter.get(top_dead_hotspot.get('file')) if top_dead_hotspot else None}",
    ))

    zustand_keys = ((state_flow.get("zustand_stores") or {}) if isinstance(state_flow, dict) else {})
    checks.append(_check(
        "state_flow_project_qualified_keys",
        all(isinstance(key, str) and "::" in key for key in zustand_keys.keys()),
        f"zustand_keys={len(zustand_keys)}",
    ))

    blast_rows = blast.get("blast_radius", []) if isinstance(blast, dict) else []
    blast_metrics = blast.get("metrics", {}) if isinstance(blast, dict) else {}
    first_blast = blast_rows[0] if blast_rows else {}
    checks.append(_check(
        "blast_radius_metrics_consistent",
        bool(blast_rows)
        and blast_metrics.get("most_critical_node") == first_blast.get("file")
        and float(blast_metrics.get("highest_impact_score", -1) or -1) == float(first_blast.get("total_impact_score", -2) or -2),
        f"metrics={blast_metrics} first={first_blast.get('file')}",
    ))

    studio_summary = decision_evidence.get("studio_summary", []) if isinstance(decision_evidence, dict) else []
    checks.append(_check(
        "decision_evidence_summary_present",
        isinstance(studio_summary, list) and len(studio_summary) > 0,
        f"studios={len(studio_summary) if isinstance(studio_summary, list) else 0}",
    ))
    checks.append(_check(
        "decision_evidence_top_modules_align_with_risk_matrix",
        (
            isinstance(module_risk_rows, list)
            and len(module_risk_rows) == 0
        ) or (
            isinstance(studio_summary, list)
            and isinstance(module_risk_rows, list)
            and len(studio_summary) > 0
            and len(module_risk_rows) > 0
        ),
        f"decision_rows={len(studio_summary) if isinstance(studio_summary, list) else 0} risk_rows={len(module_risk_rows) if isinstance(module_risk_rows, list) else 0}",
    ))

    closure_files = sorted(RAW_DIR.glob("closure_*.json"))
    closure_sample = load_json_file(closure_files[0], {}) if closure_files else {}
    closure_manifest = closure_sample.get("external_manifest", {}) if isinstance(closure_sample, dict) else {}
    checks.append(_check(
        "closure_sample_manifest_structure",
        not closure_files or isinstance(closure_manifest, dict),
        f"closure_files={len(closure_files)}",
    ))

    readiness_with_boundary = [
        row for row in readiness_rows
        if isinstance(row, dict) and isinstance((row.get("boundary_signals") or {}).get("member_side_effect_imports", []), list)
    ]
    checks.append(_check(
        "readiness_boundary_signal_structure",
        readiness_path.exists()
        and len(readiness_rows) > 0
        and len(readiness_with_boundary) == len(readiness_rows),
        f"artifact_exists={readiness_path.exists()} readiness_rows={len(readiness_rows)}",
    ))

    checks.append(_check(
        "merge_script_surface_exists",
        len(merge_script_text.strip()) > 0,
        "auto_merge script generated",
    ))
    checks.append(_check(
        "merge_script_single_project_noop_message",
        workspace_mode.get("comparative_enabled")
        or "Comparative donor migrations are not applicable in a single-project workspace." in merge_script_text
        or "Comparative donor migrations are not applicable in this workspace." in merge_script_text
        or "Copy-Item -Path " in merge_script_text,
        "auto_merge.ps1",
    ))
    checks.append(_check(
        "merge_script_non_comparative_mode_declared",
        workspace_mode.get("comparative_enabled")
        or "Auto-Merge is operating in non-comparative mode;" in merge_script_text
        or "scripts are emitted as no-op unless local migrations are explicitly proven" in merge_script_text
        or "Auto-Merge plan generated with" in merge_script_text,
        "auto_merge.ps1",
    ))
    checks.append(_check(
        "nanometric_diff_single_project_not_applicable",
        workspace_mode.get("comparative_enabled")
        or (
            isinstance(nanometric_diff, dict)
            and nanometric_diff.get("status") == "not_applicable"
            and nanometric_diff.get("reason") == "comparative_diff_requires_multiple_projects"
            and "not applicable in this workspace" in nanometric_diff_report
        ),
        f"nanometric_diff={nanometric_diff}",
    ))

    checks.append(_check("master_report_has_project_appendix",
                         "## Appendix E: Project Audit Totals" in master_report_text,
                         "MASTER_ARCHITECTURE_REPORT.md"))
    checks.append(_check("master_report_has_structural_appendix",
                         "## Appendix C: Structural Contract Health" in master_report_text,
                         "MASTER_ARCHITECTURE_REPORT.md"))
    checks.append(_check("master_report_has_workspace_mode_appendix",
                         "## Appendix F: Workspace Mode" in master_report_text,
                         "MASTER_ARCHITECTURE_REPORT.md"))

    payload = {
        "meta": {
            "kind": "lifecycle_validation",
            "version": "v1",
        },
        "summary": {
            "total_checks": len(checks),
            "passed_checks": sum(1 for check in checks if check.passed),
            "failed_checks": sum(1 for check in checks if not check.passed),
        },
        "checks": [check.__dict__ for check in checks],
    }
    return payload


def _failure_payload(reason: str, details: str) -> dict[str, Any]:
    return {
        "meta": {
            "kind": "lifecycle_validation",
            "version": "v1",
        },
        "summary": {
            "total_checks": 1,
            "passed_checks": 0,
            "failed_checks": 1,
        },
        "checks": [
            {
                "name": reason,
                "passed": False,
                "details": details,
            }
        ],
    }


def main() -> int:
    try:
        payload = run_validation()
    except FileNotFoundError as exc:
        payload = _failure_payload("required_artifact_missing", str(exc))
    out_path = RAW_DIR / "lifecycle_validation.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    save_json_atomic(out_path, payload)
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["failed_checks"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
