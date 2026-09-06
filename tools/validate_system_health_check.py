from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.agent_surface_seal_contract import is_effective_human_seal
from tools.core.json_io import load_json_file, load_text_file


HEALTH_RAW = RAW_DIR / "system_health_check.json"
HEALTH_REPORT = REPORTS_DIR / "system_health_check.md"
VALIDATION_RAW = RAW_DIR / "system_health_check_validation.json"
VALIDATION_REPORT = REPORTS_DIR / "system_health_check_validation.md"


def _check(name: str, passed: bool, details: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _release_proof_context_is_acceptable(release_proof: dict[str, Any]) -> bool:
    # System health is itself a release-proof input, so the embedded latest
    # release proof is advisory context. Direct health categories own the
    # current verdict; requiring a prior bundle to PASS would create a
    # self-referential finalizer cycle.
    if release_proof.get("context_role") != "advisory_prior_run":
        return False
    if release_proof.get("authoritative_for_current_health") is not False:
        return False
    status = str(release_proof.get("status") or "").upper()
    if status not in {"PASS", "FAIL", "ATTENTION", "UNKNOWN"}:
        return False
    if status == "UNKNOWN":
        return release_proof.get("source") is None
    if not release_proof.get("source"):
        return False
    return release_proof.get("release_ready") is not True or bool(release_proof.get("allowed_release_claim"))


def _seal_impact_blocks_machine_readiness(human_seal: dict[str, Any]) -> bool:
    return bool(human_seal.get("seal_currentness_blocks_machine_readiness"))


def _render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# System Health Check Validation",
        "",
        f"- status: `{summary.get('status')}`",
        f"- checks: `{summary.get('passed_checks')}/{summary.get('total_checks')}`",
        f"- health_status: `{summary.get('health_status')}`",
        f"- machine_verdict: `{summary.get('machine_verdict')}`",
        f"- human_seal_required: `{str(summary.get('human_seal_required')).lower()}`",
        f"- human_seal_satisfied: `{str(summary.get('human_seal_satisfied')).lower()}`",
        "",
        "| Check | Result | Details |",
        "|---|---|---|",
    ]
    for check in payload.get("checks", []):
        details = json.dumps(check.get("details"), ensure_ascii=False, sort_keys=True)[:700]
        lines.append(f"| `{check.get('name')}` | `{'PASS' if check.get('passed') else 'FAIL'}` | `{details}` |")
    return "\n".join(lines) + "\n"


def run_validation() -> dict[str, Any]:
    health = load_json_file(HEALTH_RAW, {})
    report = load_text_file(HEALTH_REPORT)
    meta = health.get("meta", {}) if isinstance(health.get("meta"), dict) else {}
    summary = health.get("summary", {}) if isinstance(health.get("summary"), dict) else {}
    categories = health.get("categories", []) if isinstance(health.get("categories"), list) else []
    human_seal = health.get("human_seal", {}) if isinstance(health.get("human_seal"), dict) else {}
    release_proof = health.get("release_proof", {}) if isinstance(health.get("release_proof"), dict) else {}
    seal_impact_blocking = _seal_impact_blocks_machine_readiness(human_seal)
    human_authority_current = bool(human_seal.get("human_seal_authority_current"))
    effective_seal_status = human_seal.get("effective_human_seal_status")

    category_ids = {str(item.get("id") or "") for item in categories if isinstance(item, dict)}
    required_categories = {
        "release_governance",
        "react_v1_claim",
        "agent_surface",
        "ssot_sqlite",
        "honesty_telemetry",
        "lineage_layers",
        "source_governance_decisions",
        "installation_distribution",
        "performance",
    }
    category_failures = [
        {
            "id": item.get("id"),
            "status": item.get("status"),
            "required": f"{item.get('required_passed')}/{item.get('required_total')}",
            "advisory": f"{item.get('advisory_passed')}/{item.get('advisory_total')}",
        }
        for item in categories
        if isinstance(item, dict) and item.get("status") != "PASS"
    ]

    checks = [
        _check(
            "health_artifact_exists_and_has_kind",
            bool(health) and meta.get("kind") == "system_health_check",
            {"path": HEALTH_RAW.as_posix(), "meta": meta},
        ),
        _check(
            "health_summary_state_is_consistent",
            (
                summary.get("status") == "PASS"
                and summary.get("machine_verdict") == "READY_FOR_HUMAN_SEAL"
                and summary.get("blocking_categories") == []
            )
            or (
                seal_impact_blocking
                and summary.get("status") == "FAIL"
                and summary.get("machine_verdict") == "BLOCKED"
                and "release_governance" in (summary.get("blocking_categories") or [])
            ),
            {"summary": summary, "seal_impact_blocking": seal_impact_blocking},
        ),
        _check(
            "human_seal_is_required_not_automated",
            summary.get("human_seal_required") is True
            and str(summary.get("machine_verdict") or "") != "SEALED"
            and "Progressive HITL" in str(human_seal.get("rule") or ""),
            {"summary": summary, "human_seal": human_seal},
        ),
        _check(
            "human_seal_satisfied_matches_ledger_backed_status",
            summary.get("human_seal_satisfied") == (
                is_effective_human_seal(effective_seal_status)
                and bool(human_seal.get("human_seal_ledger_entry"))
                and human_authority_current
            ),
            {"summary": summary, "human_seal": human_seal},
        ),
        _check(
            "category_envelope_is_complete",
            required_categories <= category_ids
            and (
                not category_failures
                or (
                    seal_impact_blocking
                    and len(category_failures) == 1
                    and category_failures[0].get("id") == "release_governance"
                )
            ),
            {"required_categories": sorted(required_categories), "category_ids": sorted(category_ids), "category_failures": category_failures},
        ),
        _check(
            "release_proof_context_is_explicitly_advisory_and_internally_consistent",
            _release_proof_context_is_acceptable(release_proof)
            and summary.get("prior_release_proof_status") == release_proof.get("status")
            and summary.get("prior_release_proof_source") == release_proof.get("source")
            and summary.get("release_proof_context_role") == "advisory_prior_run"
            and summary.get("release_proof_authoritative_for_current_health") is False
            and "latest_release_proof_status" not in summary
            and "latest_release_proof_source" not in summary,
            {"release_proof": release_proof, "summary": summary},
        ),
        _check(
            "health_report_declares_scope_boundary",
            "This is SAGE's own release health check, not a target repository health verdict." in report
            and "A target repository release/health verdict must be emitted by a separate target-repo proof bundle." in report,
            {"path": HEALTH_REPORT.as_posix()},
        ),
    ]
    failed = [check for check in checks if not check["passed"]]
    payload = {
        "meta": {"kind": "system_health_check_validation", "version": "v1"},
        "summary": {
            "status": "PASS" if not failed else "FAIL",
            "total_checks": len(checks),
            "passed_checks": len(checks) - len(failed),
            "failed_checks": len(failed),
            "health_status": summary.get("status"),
            "machine_verdict": summary.get("machine_verdict"),
            "human_seal_required": summary.get("human_seal_required"),
            "human_seal_satisfied": summary.get("human_seal_satisfied"),
        },
        "checks": checks,
    }
    save_json_atomic(VALIDATION_RAW, payload)
    save_text_atomic(VALIDATION_REPORT, _render_report(payload))
    return payload


def main() -> int:
    payload = run_validation()
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
