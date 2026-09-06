#!/usr/bin/env python3
"""Validate final_suspicion_audit against release evidence boundaries."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.config import CONFIG_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file, load_text_file
from tools.core.release_completion import completion_gaps


CONTRACT_PATH = CONFIG_DIR / "final_suspicion_audit_contract.json"
RAW_PATH = RAW_DIR / "final_suspicion_audit.json"
REPORT_PATH = REPORTS_DIR / "final_suspicion_audit.md"
VALIDATION_RAW = RAW_DIR / "final_suspicion_audit_validation.json"
VALIDATION_REPORT = REPORTS_DIR / "final_suspicion_audit_validation.md"


def _check(name: str, passed: bool, details: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _summary(name: str) -> dict[str, Any]:
    payload = load_json_file(RAW_DIR / f"{name}.json", {})
    summary = payload.get("summary", {}) if isinstance(payload, dict) else {}
    return summary if isinstance(summary, dict) else {}


def _action_ids(rows: list[dict[str, Any]]) -> list[str]:
    return [str(row.get("id") or "") for row in rows]


def _action_identities_match(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> bool:
    left_ids = _action_ids(left)
    right_ids = _action_ids(right)
    if not left_ids and not right_ids:
        return True
    return (
        bool(left_ids) == bool(right_ids)
        and all(left_ids)
        and all(right_ids)
        and len(left_ids) == len(set(left_ids))
        and len(right_ids) == len(set(right_ids))
        and set(left_ids) == set(right_ids)
    )


def _render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# Final Suspicion Audit Validation",
        "",
        f"- status: `{summary.get('status')}`",
        f"- checks: `{summary.get('passed_checks')}/{summary.get('total_checks')}`",
        "",
        "| Check | Result | Details |",
        "|---|---|---|",
    ]
    for check in payload.get("checks", []):
        details = json.dumps(check.get("details"), ensure_ascii=False, sort_keys=True)[:900]
        lines.append(f"| `{check.get('name')}` | `{'PASS' if check.get('passed') else 'FAIL'}` | `{details}` |")
    return "\n".join(lines) + "\n"


def run_validation() -> dict[str, Any]:
    contract = load_json_file(CONTRACT_PATH, {})
    audit = load_json_file(RAW_PATH, {})
    report = load_text_file(REPORT_PATH)
    seal_summary = _summary("release_seal_remaining_actions")
    health_summary = _summary("system_health_check")
    manual_summary = _summary("agent_surface_manual_seal_pack")

    meta = audit.get("meta", {}) if isinstance(audit.get("meta"), dict) else {}
    summary = audit.get("summary", {}) if isinstance(audit.get("summary"), dict) else {}
    domains = audit.get("audit_domains", []) if isinstance(audit.get("audit_domains"), list) else []
    domain_ids = {str(row.get("id") or "") for row in domains if isinstance(row, dict)}
    required_domain_ids = {
        str(row.get("id") or "")
        for row in contract.get("audit_domains", [])
        if isinstance(row, dict) and row.get("id")
    }
    public_blockers = audit.get("human_public_release_blockers", [])
    public_blockers = public_blockers if isinstance(public_blockers, list) else []
    machine_actions = audit.get("machine_blocking_actions", [])
    machine_actions = machine_actions if isinstance(machine_actions, list) else []
    release_actions = load_json_file(RAW_DIR / "release_seal_remaining_actions.json", {}).get("actions", [])
    release_actions = release_actions if isinstance(release_actions, list) else []
    release_machine_actions = [
        row
        for row in release_actions
        if isinstance(row, dict) and row.get("blocking_for_machine_readiness") is True
    ]
    machine_action_ids = _action_ids(machine_actions)
    release_machine_action_ids = _action_ids(release_machine_actions)
    machine_action_identity_matches = _action_identities_match(
        machine_actions,
        release_machine_actions,
    )
    negative_probe_review = audit.get("negative_probe_review", {})
    negative_probe_review = negative_probe_review if isinstance(negative_probe_review, dict) else {}
    completion_requirement_ids = {
        str(row.get("id") or "")
        for row in contract.get("completion_requirements", [])
        if isinstance(row, dict) and row.get("id")
    }
    expected_completion_findings = {
        str(row.get("id") or "")
        for row in completion_gaps(contract if isinstance(contract, dict) else {}, RAW_DIR)
    }
    completion_findings = {
        str(row.get("id") or ""): row
        for row in audit.get("machine_blocking_findings", [])
        if isinstance(row, dict) and row.get("id")
    }

    checks = [
        _check(
            "artifact_has_expected_kind",
            bool(audit) and meta.get("kind") == "final_suspicion_audit",
            {"meta": meta},
        ),
        _check(
            "all_contract_domains_are_reported",
            required_domain_ids <= domain_ids,
            {"required_domain_ids": sorted(required_domain_ids), "reported_domain_ids": sorted(domain_ids)},
        ),
        _check(
            "required_inputs_are_named_as_source_evidence",
            set(contract.get("required_inputs", []))
            <= set(audit.get("source_evidence", {}).get("required_inputs", []))
            if isinstance(audit.get("source_evidence"), dict)
            else False,
            {"required_inputs": contract.get("required_inputs", []), "source_evidence": audit.get("source_evidence")},
        ),
        _check(
            "machine_readiness_matches_release_seal",
            summary.get("machine_verdict") == seal_summary.get("machine_verdict")
            and summary.get("final_seal_state") == seal_summary.get("final_seal_state"),
            {"audit_summary": summary, "release_seal_summary": seal_summary},
        ),
        _check(
            "machine_blocking_actions_are_visible_when_machine_blocked",
            summary.get("machine_blocking_actions") == len(release_machine_actions)
            and machine_action_identity_matches
            and (
                summary.get("machine_verdict") != "BLOCKED"
                or ("## Machine Blocking Actions" in report and len(machine_actions) > 0)
            ),
            {
                "audit_summary": summary,
                "machine_blocking_actions": machine_actions,
                "release_machine_actions": release_machine_actions,
                "machine_action_ids": machine_action_ids,
                "release_machine_action_ids": release_machine_action_ids,
            },
        ),
        _check(
            "system_health_score_matches_health_check",
            summary.get("system_health_score") == health_summary.get("overall_score"),
            {"audit_summary": summary, "health_summary": health_summary},
        ),
        _check(
            "agent_surface_human_seal_matches_manual_pack",
            summary.get("agent_surface_human_seal_status")
            == (manual_summary.get("effective_human_seal_status") or manual_summary.get("human_seal_status"))
            and summary.get("agent_surface_effective_human_seal_status") == manual_summary.get("effective_human_seal_status"),
            {"audit_summary": summary, "manual_pack_summary": manual_summary},
        ),
        _check(
            "public_release_readiness_not_invented",
            summary.get("public_release_ready") == seal_summary.get("public_release_ready")
            and summary.get("public_release_blockers") == len(
                [
                    row
                    for row in release_actions
                    if isinstance(row, dict) and row.get("blocking_for_public_release") is True
                ]
            ),
            {"audit_summary": summary, "release_seal_summary": seal_summary},
        ),
        _check(
            "public_release_blockers_are_not_hidden",
            (summary.get("public_release_ready") is False and len(public_blockers) > 0)
            or summary.get("public_release_ready") is True,
            {"public_release_ready": summary.get("public_release_ready"), "public_blockers": public_blockers},
        ),
        _check(
            "manual_eye_debt_is_explicit",
            bool(audit.get("manual_audit_required_domains"))
            and summary.get("automated_pass_is_not_human_seal") is True
            and "does not replace human review" in report,
            {
                "manual_audit_required_domains": audit.get("manual_audit_required_domains"),
                "automated_pass_is_not_human_seal": summary.get("automated_pass_is_not_human_seal"),
            },
        ),
        _check(
            "negative_probe_review_is_visible",
            negative_probe_review.get("manual_eye_required") is True
            and negative_probe_review.get("intentional_negative_probe_count", 0) >= 1,
            {"negative_probe_review": negative_probe_review},
        ),
        _check(
            "completion_requirements_are_semantically_enforced",
            expected_completion_findings == (set(completion_findings) & completion_requirement_ids),
            {
                "expected_completion_findings": sorted(expected_completion_findings),
                "reported_completion_findings": sorted(set(completion_findings) & completion_requirement_ids),
            },
        ),
    ]
    failed = [check for check in checks if not check["passed"]]
    result = {
        "meta": {"kind": "final_suspicion_audit_validation", "version": "1.0.0"},
        "summary": {
            "status": "PASS" if not failed else "FAIL",
            "total_checks": len(checks),
            "passed_checks": len(checks) - len(failed),
            "failed_checks": len(failed),
        },
        "checks": checks,
    }
    save_json_atomic(VALIDATION_RAW, result)
    save_text_atomic(VALIDATION_REPORT, _render_report(result))
    return result


def main() -> int:
    payload = run_validation()
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
