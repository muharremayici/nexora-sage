import json
from pathlib import Path
from time import perf_counter

from tools.core.artifact_validator import ensure_valid_payload
from tools.core.atlas_io import load_atlas_data
from tools.core.config import RAW_DIR, REPORTS_DIR, ROOT, save_json_atomic, save_text_atomic
from tools.core.genome_io import load_genome_data
from tools.core.json_io import load_json_file
from tools.core.logger import logger
from tools.core.pipeline_policy import get_quality_gates
from tools.core.proof_envelope_policy import get_proof_envelope_policy
from tools.core.projects_registry import project_display_name, resolve_runtime_projects
from tools.core.report_index import load_oracle_reports


def _ratio(num: float, den: float) -> float:
    if den <= 0:
        return 0.0
    return num / den


def _clamp01(value: float) -> float:
    if value < 0:
        return 0.0
    if value > 1:
        return 1.0
    return value


def _grade(score: float) -> str:
    if score >= 0.9:
        return "A"
    if score >= 0.8:
        return "B"
    if score >= 0.7:
        return "C"
    if score >= 0.6:
        return "D"
    return "F"


def _load_oracle_reports() -> dict:
    reports = load_oracle_reports()

    by_project = {}
    pass_count = 0
    warn_count = 0
    fail_count = 0
    not_applicable_count = 0
    critical_total = 0
    deep_tsc_total = 0
    high_severity_total = 0

    for item in reports:
        project = str(item.get("project") or "UNKNOWN")
        status = str(item.get("status") or "UNKNOWN").upper()
        summary = item.get("oracle_summary", {}) if isinstance(item.get("oracle_summary"), dict) else {}
        critical = len(item.get("critical_blockers", []) or [])
        deep_tsc = int(summary.get("deep_tsc_errors", 0) or 0)
        high_severity = int((summary.get("severities", {}) if isinstance(summary.get("severities"), dict) else {}).get("high", 0) or 0)

        if status == "PASS":
            pass_count += 1
        elif status == "WARN":
            warn_count += 1
        elif status == "FAIL":
            fail_count += 1
        elif status == "NOT_APPLICABLE":
            not_applicable_count += 1

        critical_total += critical
        deep_tsc_total += deep_tsc
        high_severity_total += high_severity
        by_project[project] = {
            "status": status,
            "critical_blockers": critical,
            "deep_tsc_errors": deep_tsc,
            "high_severity": high_severity,
        }

    return {
        "total_reports": len(reports),
        "pass": pass_count,
        "warn": warn_count,
        "fail": fail_count,
        "not_applicable": not_applicable_count,
        "critical_blockers": critical_total,
        "deep_tsc_errors": deep_tsc_total,
        "high_severity_total": high_severity_total,
        "by_project": by_project,
    }


def _collect_dead_code_metrics(atlas: dict, dead_payload: dict) -> dict:
    dead_summary = dead_payload.get("summary", {}) if isinstance(dead_payload, dict) else {}
    dead_total = int(dead_summary.get("total", 0) or 0)
    dead_high = int(dead_summary.get("high", 0) or 0)
    dead_medium = int(dead_summary.get("medium", 0) or 0)

    export_universe = 0
    if isinstance(atlas, dict):
        for pdata in atlas.values():
            if not isinstance(pdata, dict):
                continue
            files = pdata.get("files", {})
            if not isinstance(files, dict):
                continue
            for fdata in files.values():
                if not isinstance(fdata, dict):
                    continue
                exports = fdata.get("exports", [])
                if not isinstance(exports, list):
                    continue
                for exp in exports:
                    if isinstance(exp, dict) and exp.get("type") == "ProxyExport":
                        continue
                    export_universe += 1

    return {
        "dead_total": dead_total,
        "dead_high": dead_high,
        "dead_medium": dead_medium,
        "dead_high_ratio": round(_ratio(dead_high, export_universe), 4),
        "dead_medium_ratio": round(_ratio(dead_medium, export_universe), 4),
        "export_universe": export_universe,
    }


def _build_ladder(signals: dict, ladder_thresholds: dict) -> tuple[int, dict]:
    l0 = bool(signals["required_artifacts_ready"] and signals["health_score"] >= int(ladder_thresholds.get("l0_health_score_min", 70)))
    l1 = bool(
        l0
        and signals["contract_file_ratio"] >= float(ladder_thresholds.get("l1_contract_file_ratio_min", 0.97))
        and signals["contract_genome_ratio"] >= float(ladder_thresholds.get("l1_contract_genome_ratio_min", 0.97))
    )
    l2 = bool(
        l1
        and signals["react_ratio"] >= float(ladder_thresholds.get("l2_react_ratio_min", 0.95))
        and signals["state_flow_ratio"] >= float(ladder_thresholds.get("l2_state_flow_ratio_min", 0.9))
        and signals["oracle_critical"] == 0
    )
    l3 = bool(
        l2
        and signals["oracle_fail"] == 0
        and signals["dead_high_ratio"] <= float(ladder_thresholds.get("l3_dead_high_ratio_max", 0.08))
        and signals["cycle_count"] <= int(ladder_thresholds.get("l3_cycle_count_max", 80))
    )

    proof_obligations = load_json_file(RAW_DIR / "proof_obligations.json", {})
    proof_summary = proof_obligations.get("summary", {}) if isinstance(proof_obligations, dict) else {}
    proof_ratio = float(proof_summary.get("verification_ratio", 0.0) or 0.0)
    formal_ok = bool(
        isinstance(proof_obligations, dict)
        and proof_obligations.get("passed") is True
        and int(proof_obligations.get("verified_obligations", 0) or 0) > 0
    )
    l4 = bool(
        l3
        and formal_ok
        and proof_ratio >= float(ladder_thresholds.get("l4_proof_ratio_min", 0.95))
        and signals["quality_score"] >= float(ladder_thresholds.get("l4_quality_score_min", 0.8))
        and signals["oracle_critical"] == 0
        and signals["dead_high_ratio"] <= float(ladder_thresholds.get("l4_dead_high_ratio_max", 0.1))
        and signals["cycle_count"] <= int(ladder_thresholds.get("l4_cycle_count_max", 80))
    )

    levels = {
        "L0_static_hygiene": {
            "passed": l0,
            "description": "Core artifacts + baseline hygiene checks are available.",
        },
        "L1_structural_contracts": {
            "passed": l1,
            "description": "AST/genome contract coverage is near-complete.",
        },
        "L2_behavioral_safety": {
            "passed": l2,
            "description": "State-flow/react capability and oracle critical safety checks are stable.",
        },
        "L3_integration_confidence": {
            "passed": l3,
            "description": "Oracle fail-free plus bounded architectural risk indicators.",
        },
        "L4_formalized_proof_envelope": {
            "passed": l4,
            "description": "Formal proof obligations verified and high-confidence envelope satisfied.",
        },
    }

    level = 0
    if l0:
        level = 1
    if l1:
        level = 2
    if l2:
        level = 3
    if l3:
        level = 4
    if l4:
        level = 5
    return level, levels


def run_quality_review():
    started = perf_counter()
    logger.info("Running quality review oracle + proof ladder...")
    policy = get_proof_envelope_policy()
    quality_policy = (policy.get("quality_review", {}) if isinstance(policy, dict) else {})
    ladder_thresholds = quality_policy.get("ladder", {}) if isinstance(quality_policy, dict) else {}
    advisory_thresholds = quality_policy.get("advisory", {}) if isinstance(quality_policy, dict) else {}

    gates = get_quality_gates()
    health = load_json_file(RAW_DIR / "health_score.json", {})
    dead = load_json_file(RAW_DIR / "dead_code.json", {})
    circular = load_json_file(RAW_DIR / "circular_deps.json", {})
    react = load_json_file(RAW_DIR / "react_support_matrix.json", {})
    state_flow = load_json_file(RAW_DIR / "state_flow.json", {})
    atlas = load_atlas_data()
    genome = load_genome_data()
    required = gates.get("required_artifacts", [])
    required_ready = all((RAW_DIR / f"{name}.json").exists() for name in required)

    health_score = int(health.get("overall", 0) or 0)
    dead_metrics = _collect_dead_code_metrics(atlas, dead)
    dead_total = dead_metrics["dead_total"]
    dead_high = dead_metrics["dead_high"]
    dead_medium = dead_metrics["dead_medium"]
    dead_high_ratio = float(dead_metrics["dead_high_ratio"])
    cycles = len(circular.get("cycles", []) or [])

    react_summary = react.get("summary", {}) if isinstance(react, dict) else {}
    react_present = int(react_summary.get("repo_present", 0) or 0)
    react_detected = int(react_summary.get("detected", 0) or 0)
    react_ratio = _ratio(react_detected, react_present)

    state_boundary = state_flow.get("boundary_signals", {}) if isinstance(state_flow, dict) else {}
    state_density = _ratio(len(state_boundary) if isinstance(state_boundary, dict) else 0, max(react_present, 1))
    state_proof = state_flow.get("state_proof", {}) if isinstance(state_flow, dict) else {}
    state_proof_by_project = state_flow.get("state_proof_by_project", {}) if isinstance(state_flow, dict) else {}
    transition_ratio = float((state_proof if isinstance(state_proof, dict) else {}).get("transition_ratio", 0.0) or 0.0)
    property_ratio = float((state_proof if isinstance(state_proof, dict) else {}).get("property_ratio", 0.0) or 0.0)
    property_ratio_strong = float((state_proof if isinstance(state_proof, dict) else {}).get("property_ratio_strong", 0.0) or 0.0)
    property_ratio_strict = float((state_proof if isinstance(state_proof, dict) else {}).get("property_ratio_strict", 0.0) or 0.0)
    if transition_ratio > 0:
        state_flow_ratio = _clamp01(transition_ratio)
    else:
        state_flow_ratio = _clamp01(state_density)

    contract_file_total = 0
    contract_file_current = 0
    if isinstance(atlas, dict):
        for pdata in atlas.values():
            files = (pdata or {}).get("files", {}) if isinstance(pdata, dict) else {}
            for fdata in files.values():
                if not isinstance(fdata, dict):
                    continue
                contract_file_total += 1
                if fdata.get("ast_contract_version"):
                    contract_file_current += 1
    contract_file_ratio = _ratio(contract_file_current, contract_file_total)

    occurrence_total = 0
    occurrence_contract = 0
    if isinstance(genome, dict):
        for occs in genome.values():
            for occ in occs or []:
                if not isinstance(occ, dict):
                    continue
                occurrence_total += 1
                if occ.get("ast_contract_version"):
                    occurrence_contract += 1
    contract_genome_ratio = _ratio(occurrence_contract, occurrence_total)

    oracle = _load_oracle_reports()
    expected_projects = sorted(project for project in resolve_runtime_projects(ROOT) if project != "MAIN")
    for project in expected_projects:
        oracle["by_project"].setdefault(
            project,
            {
                "status": "UNKNOWN",
                "critical_blockers": 0,
                "deep_tsc_errors": 0,
                "high_severity": 0,
            },
        )
    oracle["expected_reports"] = len(expected_projects)
    oracle["applicable_reports"] = max(0, int(oracle["total_reports"]) - int(oracle.get("not_applicable", 0) or 0))
    oracle["coverage_ratio"] = round(_ratio(oracle["applicable_reports"], oracle["expected_reports"]), 3)
    oracle["availability_ratio"] = round(_ratio(oracle["total_reports"], oracle["expected_reports"]), 3)
    oracle_penalty = _clamp01((oracle["critical_blockers"] * 2 + oracle["fail"] + (oracle["warn"] * 0.5)) / 20.0)

    score = (
        0.24 * _clamp01(health_score / 100.0)
        + 0.16 * _clamp01(contract_file_ratio)
        + 0.12 * _clamp01(contract_genome_ratio)
        + 0.14 * _clamp01(react_ratio)
        + 0.10 * _clamp01(1.0 - min(1.0, cycles / 120.0))
        + 0.12 * _clamp01(1.0 - min(1.0, dead_high / 180.0))
        + 0.06 * _clamp01(1.0 - min(1.0, dead_medium / 500.0))
        + 0.06 * _clamp01(1.0 - oracle_penalty)
    )
    score = round(_clamp01(score), 3)

    signals = {
        "required_artifacts_ready": required_ready,
        "health_score": health_score,
        "contract_file_ratio": round(contract_file_ratio, 3),
        "contract_genome_ratio": round(contract_genome_ratio, 3),
        "react_ratio": round(react_ratio, 3),
        "state_flow_ratio": round(state_flow_ratio, 3),
        "state_transition_ratio": round(_clamp01(transition_ratio), 3),
        "state_property_ratio": round(_clamp01(property_ratio), 3),
        "state_property_ratio_strong": round(_clamp01(property_ratio_strong), 3),
        "state_property_ratio_strict": round(_clamp01(property_ratio_strict), 3),
        "dead_total": dead_total,
        "dead_high": dead_high,
        "dead_medium": dead_medium,
        "dead_high_ratio": round(dead_high_ratio, 3),
        "dead_medium_ratio": round(float(dead_metrics["dead_medium_ratio"]), 3),
        "dead_export_universe": int(dead_metrics["export_universe"]),
        "cycle_count": cycles,
        "oracle_critical": oracle["critical_blockers"],
        "oracle_fail": oracle["fail"],
        "oracle_warn": oracle["warn"],
        "oracle_coverage_ratio": oracle["coverage_ratio"],
        "oracle_availability_ratio": oracle["availability_ratio"],
        "oracle_not_applicable": oracle.get("not_applicable", 0),
        "quality_score": score,
    }

    proof_level, ladder = _build_ladder(signals, ladder_thresholds)
    proof_label = f"L{max(0, proof_level - 1)}"
    math_status = "heuristic_only"
    if proof_level >= 5:
        math_status = "bounded_formalized"
    elif proof_level >= 4:
        math_status = "strong_evidence_no_formal_proof"

    findings = []
    if not required_ready:
        findings.append(
            {
                "id": "missing_required_artifacts",
                "severity": "blocker",
                "category": "pipeline_integrity",
                "message": "Required artifacts are missing; quality inference is incomplete.",
                "evidence": {"required_artifacts": required},
            }
        )
    if oracle["critical_blockers"] > 0:
        findings.append(
            {
                "id": "oracle_critical_blockers",
                "severity": "blocker",
                "category": "integration_safety",
                "message": "Oracle detected critical blockers.",
                "evidence": {"critical_blockers": oracle["critical_blockers"]},
            }
        )
    if health_score < int(advisory_thresholds.get("health_score_warning_min", 75)):
        findings.append(
            {
                "id": "health_score_low",
                "severity": "warning",
                "category": "structural_quality",
                "message": "Overall health score is below recommended threshold.",
                "evidence": {"health_score": health_score, "recommended_min": int(advisory_thresholds.get("health_score_warning_min", 75))},
            }
        )
    if cycles > int(advisory_thresholds.get("cycle_warning_max", 80)):
        findings.append(
            {
                "id": "circular_dependency_pressure",
                "severity": "warning",
                "category": "dependency_graph",
                "message": "Circular dependency count is above stable envelope.",
                "evidence": {"cycles": cycles, "recommended_max": int(advisory_thresholds.get("cycle_warning_max", 80))},
            }
        )
    if dead_high > int(advisory_thresholds.get("dead_high_warning_max", 120)):
        findings.append(
            {
                "id": "dead_code_high_confidence_pressure",
                "severity": "warning",
                "category": "dead_code",
                "message": "High-confidence dead code findings are elevated.",
                "evidence": {"dead_high": dead_high, "recommended_max": int(advisory_thresholds.get("dead_high_warning_max", 120))},
            }
        )
    oracle_coverage_warning_min = float(advisory_thresholds.get("oracle_coverage_warning_min", 0.5))
    oracle_min_expected_reports = int(advisory_thresholds.get("oracle_coverage_warning_expected_min", 5))
    if int(oracle.get("expected_reports", 0) or 0) >= oracle_min_expected_reports and float(oracle.get("coverage_ratio", 0.0) or 0.0) < oracle_coverage_warning_min:
        findings.append(
            {
                "id": "oracle_coverage_low",
                "severity": "warning",
                "category": "oracle_coverage",
                "message": "Oracle project coverage is below advisory minimum; confidence should be interpreted cautiously.",
                "evidence": {
                    "coverage_ratio": round(float(oracle.get("coverage_ratio", 0.0) or 0.0), 3),
                    "availability_ratio": round(float(oracle.get("availability_ratio", 0.0) or 0.0), 3),
                    "total_reports": int(oracle.get("total_reports", 0) or 0),
                    "applicable_reports": int(oracle.get("applicable_reports", 0) or 0),
                    "expected_reports": int(oracle.get("expected_reports", 0) or 0),
                    "not_applicable": int(oracle.get("not_applicable", 0) or 0),
                    "recommended_min": oracle_coverage_warning_min,
                    "min_expected_reports": oracle_min_expected_reports,
                },
            }
        )
    strict_warning_min = float(advisory_thresholds.get("state_property_strict_warning_min", 0.2))
    strong_relief_min = float(
        (
            (policy.get("proof_obligations", {}) if isinstance(policy, dict) else {}).get("thresholds", {})
            if isinstance((policy.get("proof_obligations", {}) if isinstance(policy, dict) else {}), dict)
            else {}
        ).get("property_ratio_strong_min", 0.55)
    )
    if property_ratio_strict < strict_warning_min and property_ratio_strong < strong_relief_min:
        strict_by_project = {}
        if isinstance(state_proof_by_project, dict):
            for project, payload in state_proof_by_project.items():
                if isinstance(payload, dict):
                    strict_by_project[str(project)] = round(float(payload.get("property_ratio_strict", 0.0) or 0.0), 3)
        findings.append(
            {
                "id": "state_property_strict_coverage_low",
                "severity": "warning",
                "category": "proof_envelope",
                "message": "Strict state-property coverage is below baseline and strong contractual coverage is also insufficient.",
                "evidence": {
                    "state_property_ratio_strict": round(property_ratio_strict, 3),
                    "state_property_ratio_strong": round(property_ratio_strong, 3),
                    "recommended_min": strict_warning_min,
                    "strong_relief_min": strong_relief_min,
                    "by_project": strict_by_project,
                },
            }
        )
    elif property_ratio_strict < strict_warning_min:
        findings.append(
            {
                "id": "state_property_strict_coverage_low_relaxed",
                "severity": "info",
                "category": "proof_envelope",
                "message": "Strict selector/memo evidence is limited, but strong contractual state coverage is above baseline.",
                "evidence": {
                    "state_property_ratio_strict": round(property_ratio_strict, 3),
                    "state_property_ratio_strong": round(property_ratio_strong, 3),
                    "strict_warning_min": strict_warning_min,
                    "strong_relief_min": strong_relief_min,
                },
            }
        )

    blocker_count = sum(1 for item in findings if item.get("severity") == "blocker")
    warning_count = sum(1 for item in findings if item.get("severity") == "warning")

    by_project = {}
    health_projects = health.get("project_details", {}) if isinstance(health, dict) else {}
    dead_projects = dead.get("by_project", {}) if isinstance(dead, dict) else {}
    circular_projects = circular.get("by_project", {}) if isinstance(circular, dict) else {}
    for project in sorted(set(list(health_projects.keys()) + list(dead_projects.keys()) + list(circular_projects.keys()) + list(oracle["by_project"].keys()))):
        p_health = int((health_projects.get(project, {}) if isinstance(health_projects.get(project, {}), dict) else {}).get("overall", 0) or 0)
        p_dead = dead_projects.get(project, {}) if isinstance(dead_projects.get(project, {}), dict) else {}
        p_dead_high = int((p_dead.get("summary", {}) if isinstance(p_dead.get("summary", {}), dict) else {}).get("high", 0) or 0)
        p_cycles = int((circular_projects.get(project, {}) if isinstance(circular_projects.get(project, {}), dict) else {}).get("cycle_count", 0) or 0)
        p_oracle = oracle["by_project"].get(project, {})
        p_state_proof = state_proof_by_project.get(project, {}) if isinstance(state_proof_by_project, dict) else {}
        p_penalty = (
            0.35 * min(1.0, p_cycles / 25.0)
            + 0.35 * min(1.0, p_dead_high / 40.0)
            + 0.30 * (1.0 if str(p_oracle.get("status", "")).upper() == "FAIL" else 0.4 if str(p_oracle.get("status", "")).upper() == "WARN" else 0.0)
        )
        p_score = _clamp01((p_health / 100.0) * (1.0 - min(0.85, p_penalty)))
        by_project[project] = {
            "display_name": project_display_name(project),
            "score": round(p_score, 3),
            "grade": _grade(p_score),
            "health_score": p_health,
            "dead_high": p_dead_high,
            "cycles": p_cycles,
            "oracle_status": str(p_oracle.get("status") or "UNKNOWN"),
            "state_property_ratio_strict": round(float((p_state_proof if isinstance(p_state_proof, dict) else {}).get("property_ratio_strict", 0.0) or 0.0), 3),
        }

    payload = {
        "meta": {
            "version": "quality_review_v1",
            "generated_by": "quality_review_engine",
            "runtime_seconds": round(perf_counter() - started, 3),
        },
        "summary": {
            "score": score,
            "grade": _grade(score),
            "proof_ladder_level": proof_level,
            "proof_ladder_label": proof_label,
            "mathematical_proof_status": math_status,
            "blockers": blocker_count,
            "warnings": warning_count,
            "findings_total": len(findings),
        },
        "signals": signals,
        "proof_ladder": ladder,
        "findings": findings,
        "oracle": oracle,
        "by_project": by_project,
        "quality_gate_snapshot": {
            "status": "not_available_in_quality_review_phase",
            "reason": "Quality Gates consumes quality_review and runs after Quality Review Oracle.",
        },
    }

    ensure_valid_payload("quality_review", payload)
    save_json_atomic(RAW_DIR / "quality_review.json", payload)

    lines = [
        "# Quality Review Oracle",
        "",
        f"- Score: `{payload['summary']['score']}` ({payload['summary']['grade']})",
        f"- Proof Ladder: `{payload['summary']['proof_ladder_label']}` (internal level `{payload['summary']['proof_ladder_level']}`)",
        f"- Mathematical proof status: `{payload['summary']['mathematical_proof_status']}`",
        f"- Findings: `{payload['summary']['findings_total']}` (blockers `{blocker_count}`, warnings `{warning_count}`)",
        f"- Oracle applicable coverage: `{payload['oracle'].get('applicable_reports', 0)}/{payload['oracle'].get('expected_reports', 0)}` (ratio `{payload['oracle'].get('coverage_ratio', 0)}`)",
        f"- Oracle report availability: `{payload['oracle'].get('total_reports', 0)}/{payload['oracle'].get('expected_reports', 0)}` with `NOT_APPLICABLE={payload['oracle'].get('not_applicable', 0)}`",
        "",
        "## Proof Ladder",
        "",
        "| Level | Passed | Description |",
        "|---|---|---|",
    ]
    for level_name, info in payload["proof_ladder"].items():
        lines.append(f"| `{level_name}` | {'yes' if info.get('passed') else 'no'} | {info.get('description', '')} |")

    lines.extend([
        "",
        "## Top Findings",
        "",
        "| Severity | Category | Message |",
        "|---|---|---|",
    ])
    if findings:
        for item in findings[:25]:
            lines.append(f"| `{item.get('severity', 'info')}` | `{item.get('category', '')}` | {item.get('message', '')} |")
    else:
        lines.append("| `info` | `none` | No critical findings in current review pass. |")

    lines.extend([
        "",
        "## Per Project",
        "",
        "| Project | Score | Grade | Health | DeadHigh | Cycles | Oracle | StatePropertyStrict |",
        "|---|---:|---|---:|---:|---:|---|---:|",
    ])
    for project, data in sorted(by_project.items(), key=lambda item: item[1].get("score", 0), reverse=True):
        lines.append(
            f"| {data.get('display_name', project)} [{project}] | {data.get('score', 0)} | {data.get('grade', 'F')} | {data.get('health_score', 0)} | {data.get('dead_high', 0)} | {data.get('cycles', 0)} | {data.get('oracle_status', 'UNKNOWN')} | {data.get('state_property_ratio_strict', 0)} |"
        )

    save_text_atomic(REPORTS_DIR / "quality_review.md", "\n".join(lines))
    logger.info("Quality review oracle generated: reports/quality_review.md")


if __name__ == "__main__":
    run_quality_review()
