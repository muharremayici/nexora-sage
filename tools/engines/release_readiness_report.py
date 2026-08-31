from __future__ import annotations

import json
from typing import Any

from tools.core.artifact_validator import validate_all_artifacts
from tools.core.atlas_io import load_atlas_data
from tools.core.config import CONFIG_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.evidence_status import evidence_passed
from tools.core.genome_io import load_genome_data
from tools.core.json_io import load_json_file, load_json_object_strict
from tools.core.logger import logger


def _status_check(name: str, passed: bool, details: str, enforced: bool = True) -> dict[str, Any]:
    return {
        "name": name,
        "passed": bool(passed),
        "enforced": bool(enforced),
        "details": details,
    }


def _artifact_passed(payload: Any) -> bool:
    return evidence_passed(payload, default=False)


def _manual_validation_confidence() -> dict[str, Any]:
    report_path = REPORTS_DIR / "codemaps_report_truth_validation.md"
    if not report_path.exists():
        return {
            "status": "not_available",
            "report": str(report_path),
            "summary": "Manual truth-validation report has not been generated for the current output set.",
            "validated_signal_families": [],
        }

    text = report_path.read_text(encoding="utf-8", errors="replace")
    signal_families = []
    markers = {
        "duplicate_live": ("duplicate-live", "duplicate_live"),
        "dead_code": ("dead_code", "dead-code", "dead code"),
        "ui_runtime": ("ui", "i18n", "runtime"),
        "typescript_oracle": ("tsc", "typescript", "oracle"),
        "ecosystem_signal": ("ecosystem",),
    }
    lowered = text.lower()
    for family, tokens in markers.items():
        if any(token in lowered for token in tokens):
            signal_families.append(family)

    verdict = "available"
    for line in text.splitlines():
        if "overall verdict" in line.lower() or "genel" in line.lower():
            verdict = line.strip().lstrip("#- ").strip() or verdict
            break

    return {
        "status": "available",
        "report": str(report_path),
        "summary": verdict,
        "validated_signal_families": sorted(signal_families),
    }


def build_release_readiness_payload(artifacts: dict[str, Any], artifact_errors: dict[str, list[str]] | None = None) -> dict[str, Any]:
    quality = artifacts.get("quality_gate", {}) if isinstance(artifacts.get("quality_gate"), dict) else {}
    merge_regression = artifacts.get("merge_intelligence_regression", {}) if isinstance(artifacts.get("merge_intelligence_regression"), dict) else {}
    adapter_registry = artifacts.get("adapter_registry", {}) if isinstance(artifacts.get("adapter_registry"), dict) else {}
    cockpit = artifacts.get("merge_decision_cockpit", {}) if isinstance(artifacts.get("merge_decision_cockpit"), dict) else {}
    taskpacks = artifacts.get("ai_task_packs", {}) if isinstance(artifacts.get("ai_task_packs"), dict) else {}
    suppressions = artifacts.get("codemaps_suppressions", {}) if isinstance(artifacts.get("codemaps_suppressions"), dict) else {}
    distribution = artifacts.get("distribution_hardening_validation", {}) if isinstance(artifacts.get("distribution_hardening_validation"), dict) else {}
    entrypoints = artifacts.get("entrypoint_failure_validation", {}) if isinstance(artifacts.get("entrypoint_failure_validation"), dict) else {}
    operational = artifacts.get("operational_parity_validation", {}) if isinstance(artifacts.get("operational_parity_validation"), dict) else {}
    performance = artifacts.get("performance_budget_validation", {}) if isinstance(artifacts.get("performance_budget_validation"), dict) else {}
    performance_ledger = artifacts.get("performance_ledger", {}) if isinstance(artifacts.get("performance_ledger"), dict) else {}
    universal_proof = artifacts.get("universal_proof_validation", {}) if isinstance(artifacts.get("universal_proof_validation"), dict) else {}
    react_universal = artifacts.get("react_universal_readiness", {}) if isinstance(artifacts.get("react_universal_readiness"), dict) else {}
    react_fixtures = artifacts.get("react_fixture_matrix_validation", {}) if isinstance(artifacts.get("react_fixture_matrix_validation"), dict) else {}
    mcp_agent_surface = artifacts.get("mcp_agent_surface_validation", {}) if isinstance(artifacts.get("mcp_agent_surface_validation"), dict) else {}

    artifact_errors = artifact_errors if artifact_errors is not None else {}
    artifact_error_count = sum(len(errors) for errors in artifact_errors.values())
    regression_summary = merge_regression.get("summary", {}) if isinstance(merge_regression, dict) else {}
    adapter_summary = adapter_registry.get("summary", {}) if isinstance(adapter_registry, dict) else {}
    cockpit_summary = cockpit.get("summary", {}) if isinstance(cockpit, dict) else {}
    taskpack_summary = taskpacks.get("summary", {}) if isinstance(taskpacks, dict) else {}
    distribution_summary = distribution.get("summary", {}) if isinstance(distribution, dict) else {}
    entrypoint_summary = entrypoints.get("summary", {}) if isinstance(entrypoints, dict) else {}
    operational_summary = operational.get("summary", {}) if isinstance(operational, dict) else {}
    performance_summary = performance.get("summary", {}) if isinstance(performance, dict) else {}
    performance_metrics = performance.get("metrics", {}) if isinstance(performance.get("metrics"), dict) else {}
    performance_runs = performance_ledger.get("runs", []) if isinstance(performance_ledger, dict) else []
    universal_summary = universal_proof.get("summary", {}) if isinstance(universal_proof, dict) else {}
    react_universal_summary = react_universal.get("summary", {}) if isinstance(react_universal, dict) else {}
    react_fixture_summary = react_fixtures.get("summary", {}) if isinstance(react_fixtures, dict) else {}
    mcp_agent_surface_summary = mcp_agent_surface.get("summary", {}) if isinstance(mcp_agent_surface, dict) else {}
    manual_validation = _manual_validation_confidence()
    external_fixture_pool_required = bool(react_universal_summary.get("external_fixture_pool_required", True))
    allowed_react_claim = str(react_universal_summary.get("allowed_claim") or "")
    release_identity = load_json_object_strict(CONFIG_DIR / "release_identity.json", label="Release identity")
    identity_release_claim = (
        (release_identity.get("release_claim") or {}) if isinstance(release_identity, dict) else {}
    )
    identity_claim = str(identity_release_claim.get("allowed") or "")
    react_claim_ready = (
        bool(react_universal_summary.get("universal_ready")) is True
        and bool(identity_claim)
        and allowed_react_claim == identity_claim
    )
    cockpit_candidates = int(cockpit_summary.get("candidates", 0) or 0)
    has_analysis_snapshot = bool(load_atlas_data()) and bool(load_genome_data())

    checks = [
        _status_check(
            "quality_gate_pass",
            quality.get("release_gate_status") == "PASS" or quality.get("passed") is True,
            f"release_gate_status={quality.get('release_gate_status')} ecosystem_signal_status={quality.get('ecosystem_signal_status')}",
            enforced=False,
        ),
        _status_check(
            "artifact_contracts_valid",
            artifact_error_count == 0,
            f"artifact_error_count={artifact_error_count}",
            enforced=has_analysis_snapshot,
        ),
        _status_check(
            "merge_intelligence_regression_pass",
            _artifact_passed(merge_regression),
            f"summary={regression_summary}",
        ),
        _status_check(
            "adapter_registry_valid",
            int(adapter_summary.get("enabled", 0) or 0) > 0
            and int(adapter_summary.get("valid", 0) or 0) == int(adapter_summary.get("total", 0) or 0),
            f"summary={adapter_summary}",
        ),
        _status_check(
            "cockpit_confidence_accounted",
            sum(int(v or 0) for v in (cockpit_summary.get("confidence_tiers", {}) or {}).values())
            == int(cockpit_summary.get("candidates", 0) or 0),
            f"summary={cockpit_summary}",
        ),
        _status_check(
            "taskpacks_generated",
            int((taskpack_summary.get("generated") or taskpack_summary.get("taskpacks") or 0)) > 0 or cockpit_candidates == 0,
            f"summary={taskpack_summary} cockpit_candidates={cockpit_candidates}",
        ),
        _status_check(
            "suppression_registry_explicit",
            isinstance(suppressions.get("suppressions"), list),
            f"entries={len(suppressions.get('suppressions', [])) if isinstance(suppressions.get('suppressions'), list) else 'invalid'}",
        ),
        _status_check(
            "distribution_hardening_pass",
            _artifact_passed(distribution),
            f"summary={distribution_summary}",
        ),
        _status_check(
            "entrypoint_failure_drills_pass",
            _artifact_passed(entrypoints),
            f"summary={entrypoint_summary}",
        ),
        _status_check(
            "operational_parity_pass",
            operational_summary.get("passed") is True if operational_summary else True,
            f"summary={operational_summary or {'status': 'not_run_optional_heavy_gate'}}",
            enforced=bool(operational_summary),
        ),
        _status_check(
            "performance_budget_pass",
            _artifact_passed(performance),
            f"summary={performance_summary}",
            enforced=False,
        ),
        _status_check(
            "performance_ledger_present",
            isinstance(performance_runs, list) and len(performance_runs) > 0,
            f"runs={len(performance_runs) if isinstance(performance_runs, list) else 'invalid'}",
            enforced=False,
        ),
        _status_check(
            "react_fixture_matrix_pass",
            _artifact_passed(react_fixtures),
            f"summary={react_fixture_summary}",
            enforced=external_fixture_pool_required,
        ),
        _status_check(
            "react_universal_readiness_pass",
            react_claim_ready,
            f"summary={react_universal_summary} identity_claim={identity_claim} external_fixture_pool_required={external_fixture_pool_required}",
        ),
        _status_check(
            "universal_proof_pass",
            _artifact_passed(universal_proof),
            f"summary={universal_summary}",
            enforced=external_fixture_pool_required,
        ),
        _status_check(
            "mcp_agent_surface_pass",
            mcp_agent_surface_summary.get("status") == "PASS"
            and len(mcp_agent_surface_summary.get("missing_required_tools") or []) == 0,
            f"summary={mcp_agent_surface_summary}",
        ),
    ]

    blocking_failures = [check for check in checks if check.get("enforced") and not check.get("passed")]
    readiness = "PRODUCTION_READY" if not blocking_failures else "NOT_READY"
    ecosystem_signal_status = quality.get("ecosystem_signal_status")
    ecosystem_attention = ecosystem_signal_status not in (None, "PASS", "OK", "CLEAR")
    performance_evidence_status = str(performance_metrics.get("performance_evidence_status") or "unknown")
    performance_release_proof_refresh_required = bool(performance_metrics.get("release_proof_refresh_required"))
    return {
        "meta": {"kind": "release_readiness", "version": "v1"},
        "readiness": readiness,
        "summary": {
            "checks": len(checks),
            "passed": sum(1 for check in checks if check.get("passed")),
            "failed": len(blocking_failures),
            "platform_readiness": readiness,
            "ecosystem_signal_status": ecosystem_signal_status,
            "ecosystem_attention": ecosystem_attention,
            "artifact_error_count": artifact_error_count,
            "manual_validation_confidence": manual_validation.get("status"),
            "react_allowed_claim": allowed_react_claim,
            "performance_evidence_status": performance_evidence_status,
            "performance_release_proof_refresh_required": performance_release_proof_refresh_required,
        },
        "checks": checks,
        "evidence": {
            "quality_gate": {
                "release_gate_status": quality.get("release_gate_status"),
                "ecosystem_signal_status": quality.get("ecosystem_signal_status"),
                "ecosystem_warning_signals": quality.get("ecosystem_warning_signals", {}),
            },
            "merge_intelligence_regression": regression_summary,
            "adapter_registry": adapter_summary,
            "merge_decision_cockpit": cockpit_summary,
            "ai_task_packs": taskpack_summary,
            "distribution_hardening_validation": distribution_summary,
            "entrypoint_failure_validation": entrypoint_summary,
            "operational_parity_validation": operational_summary,
            "performance_budget_validation": performance_summary,
            "performance_budget_metrics": {
                "pipeline_profile": performance_metrics.get("pipeline_profile"),
                "selected_session_started_at": performance_metrics.get("selected_session_started_at"),
                "selected_session_last_event_at": performance_metrics.get("selected_session_last_event_at"),
                "physical_latest_session_mode": performance_metrics.get("physical_latest_session_mode"),
                "physical_latest_session_started_at": performance_metrics.get("physical_latest_session_started_at"),
                "physical_latest_session_last_event_at": performance_metrics.get("physical_latest_session_last_event_at"),
                "performance_evidence_status": performance_evidence_status,
                "release_proof_refresh_required": performance_release_proof_refresh_required,
            },
            "performance_ledger": {"runs": len(performance_runs) if isinstance(performance_runs, list) else 0},
            "react_fixture_matrix_validation": react_fixture_summary,
            "react_universal_readiness": react_universal_summary,
            "universal_proof_validation": universal_summary,
            "mcp_agent_surface_validation": mcp_agent_surface_summary,
            "manual_validation_confidence": manual_validation,
            "artifact_contract_errors": {key: value for key, value in artifact_errors.items() if value},
        },
    }


def run_release_readiness_report() -> dict[str, Any]:
    logger.info("Building release readiness report...")
    artifact_errors = validate_all_artifacts()
    artifacts = {
        "quality_gate": load_json_file(RAW_DIR / "quality_gate.json", {}),
        "merge_intelligence_regression": load_json_file(RAW_DIR / "merge_intelligence_regression.json", {}),
        "adapter_registry": load_json_file(RAW_DIR / "adapter_registry.json", {}),
        "distribution_hardening_validation": load_json_file(RAW_DIR / "distribution_hardening_validation.json", {}),
        "entrypoint_failure_validation": load_json_file(RAW_DIR / "entrypoint_failure_validation.json", {}),
        "operational_parity_validation": load_json_file(RAW_DIR / "operational_parity_validation.json", {}),
        "performance_budget_validation": load_json_file(RAW_DIR / "performance_budget_validation.json", {}),
        "performance_ledger": load_json_file(RAW_DIR / "performance_ledger.json", {}),
        "react_fixture_matrix_validation": load_json_file(RAW_DIR / "react_fixture_matrix_validation.json", {}),
        "react_universal_readiness": load_json_file(RAW_DIR / "react_universal_readiness.json", {}),
        "universal_proof_validation": load_json_file(RAW_DIR / "universal_proof_validation.json", {}),
        "mcp_agent_surface_validation": load_json_file(RAW_DIR / "mcp_agent_surface_validation.json", {}),
        "merge_decision_cockpit": load_json_file(RAW_DIR / "merge_decision_cockpit.json", {}),
        "ai_task_packs": load_json_file(RAW_DIR / "ai_task_packs.json", {}),
        "codemaps_suppressions": load_json_file(CONFIG_DIR / "codemaps.suppressions.json", {}),
    }
    payload = build_release_readiness_payload(artifacts, artifact_errors)
    save_json_atomic(RAW_DIR / "release_readiness.json", payload)

    lines = [
        "# Release Readiness",
        "",
        f"- readiness: `{payload['readiness']}`",
        f"- checks: `{payload['summary']['passed']}/{payload['summary']['checks']}`",
        f"- platform_readiness: `{payload['summary'].get('platform_readiness')}`",
        f"- ecosystem_signal_status: `{payload['summary'].get('ecosystem_signal_status')}`",
        f"- ecosystem_attention: `{payload['summary'].get('ecosystem_attention')}`",
        f"- manual_validation_confidence: `{payload['summary'].get('manual_validation_confidence')}`",
        f"- react_allowed_claim: `{payload['summary'].get('react_allowed_claim')}`",
        f"- performance_evidence_status: `{payload['summary'].get('performance_evidence_status')}`",
        f"- performance_release_proof_refresh_required: `{payload['summary'].get('performance_release_proof_refresh_required')}`",
        "",
        "Platform readiness means Nexora SAGE release contracts are satisfied. Ecosystem attention means the analyzed repository or merge/import candidates still contain advisory signals that should be reviewed before acting on those repo-level changes.",
        "",
        "## Manual Validation Confidence",
        "",
        f"- status: `{payload['evidence']['manual_validation_confidence'].get('status')}`",
        f"- report: `{payload['evidence']['manual_validation_confidence'].get('report')}`",
        f"- validated_signal_families: `{payload['evidence']['manual_validation_confidence'].get('validated_signal_families')}`",
        f"- summary: `{payload['evidence']['manual_validation_confidence'].get('summary')}`",
        "",
        "## Checks",
        "",
        "| Check | Enforced | Result | Details |",
        "|---|---|---|---|",
    ]
    for check in payload["checks"]:
        lines.append(
            f"| `{check['name']}` | `{check['enforced']}` | "
            f"{'PASS' if check['passed'] else 'FAIL'} | `{check['details']}` |"
        )
    lines.extend([
        "",
        "## Evidence",
        "",
        f"- quality_gate: `{payload['evidence']['quality_gate']}`",
        f"- merge_intelligence_regression: `{payload['evidence']['merge_intelligence_regression']}`",
        f"- adapter_registry: `{payload['evidence']['adapter_registry']}`",
        f"- distribution_hardening_validation: `{payload['evidence']['distribution_hardening_validation']}`",
        f"- entrypoint_failure_validation: `{payload['evidence']['entrypoint_failure_validation']}`",
        f"- operational_parity_validation: `{payload['evidence']['operational_parity_validation']}`",
        f"- performance_budget_validation: `{payload['evidence']['performance_budget_validation']}`",
        f"- performance_budget_metrics: `{payload['evidence']['performance_budget_metrics']}`",
        f"- performance_ledger: `{payload['evidence']['performance_ledger']}`",
        f"- react_fixture_matrix_validation: `{payload['evidence']['react_fixture_matrix_validation']}`",
        f"- react_universal_readiness: `{payload['evidence']['react_universal_readiness']}`",
        f"- universal_proof_validation: `{payload['evidence']['universal_proof_validation']}`",
        f"- mcp_agent_surface_validation: `{payload['evidence']['mcp_agent_surface_validation']}`",
        f"- merge_decision_cockpit: `{payload['evidence']['merge_decision_cockpit']}`",
        f"- manual_validation_confidence: `{payload['evidence']['manual_validation_confidence']}`",
    ])
    save_text_atomic(REPORTS_DIR / "release_readiness.md", "\n".join(lines) + "\n")
    return payload


if __name__ == "__main__":
    result = run_release_readiness_report()
    print(json.dumps(result.get("summary", {}), ensure_ascii=False))
