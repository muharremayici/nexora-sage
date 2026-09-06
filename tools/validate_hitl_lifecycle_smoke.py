from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.core.config import CODE_MAPS_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.validate_nexora_agent_response import response_template, validate_response


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check(name: str, passed: bool, details: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _run_isolated_smoke(tmp_root: Path) -> dict[str, Any]:
    import tools.hitl_approval_ledger as approval_ledger
    import tools.hitl_decision_requests as decision_requests
    import tools.nexora_agent_response_ledger as response_ledger

    raw_dir = tmp_root / "raw"
    reports_dir = tmp_root / "reports"
    raw_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)

    original_paths = {
        "approval_ledger": (approval_ledger.LEDGER_PATH, approval_ledger.REPORT_PATH),
        "decision_requests": (decision_requests.REQUESTS_PATH, decision_requests.REPORT_PATH),
        "response_ledger": (response_ledger.LEDGER_PATH, response_ledger.REPORT_PATH),
    }

    try:
        approval_ledger.LEDGER_PATH = raw_dir / "hitl_approval_ledger.json"
        approval_ledger.REPORT_PATH = reports_dir / "hitl_approval_ledger.md"
        decision_requests.REQUESTS_PATH = raw_dir / "hitl_decision_requests.json"
        decision_requests.REPORT_PATH = reports_dir / "hitl_decision_requests.md"
        response_ledger.LEDGER_PATH = raw_dir / "nexora_agent_response_ledger.json"
        response_ledger.REPORT_PATH = reports_dir / "nexora_agent_response_ledger.md"

        request = decision_requests.create_request(
            gate="mutation_scripts",
            scope="output/scripts/auto_merge.ps1",
            proposed_action="Run generated auto-merge script",
            risk="critical",
            rationale="Lifecycle smoke test request in isolated temp storage.",
            evidence=["output/scripts/auto_merge_manifest.json"],
            confidence="high",
            requested_by="hitl_lifecycle_smoke",
        )
        decision = approval_ledger.record_decision(
            gate="mutation_scripts",
            decision="deferred",
            scope="output/scripts/auto_merge.ps1",
            actor="hitl_lifecycle_smoke",
            rationale="Smoke test decision; no real approval granted.",
            evidence=["output/scripts/auto_merge_manifest.json"],
            request_id=request["id"],
        )
        closed_request = decision_requests.update_request_status(
            request_id=request["id"],
            status="closed",
            reason="Resolved by smoke-test ledger decision.",
            linked_ledger_entry=decision["id"],
        )
        reseal_request = decision_requests.ensure_derived_request(
            "refresh_human_seal_after_impacting_changes",
            "v1.0.0_agent_surface",
            evidence=["output/reports/seal_impact_validation.md"],
        )
        same_reseal_request = decision_requests.ensure_derived_request(
            "refresh_human_seal_after_impacting_changes",
            "v1.0.0_agent_surface",
            evidence=["output/reports/seal_impact_validation.md"],
        )
        template = response_template()
        validation = validate_response(template)
        response_entry = response_ledger.record_response(template, task_id="hitl_lifecycle_smoke", actor="hitl_lifecycle_smoke")

        request_payload = json.loads(decision_requests.REQUESTS_PATH.read_text(encoding="utf-8"))
        ledger_payload = json.loads(approval_ledger.LEDGER_PATH.read_text(encoding="utf-8"))
        response_ledger_payload = json.loads(response_ledger.LEDGER_PATH.read_text(encoding="utf-8"))
    finally:
        approval_ledger.LEDGER_PATH, approval_ledger.REPORT_PATH = original_paths["approval_ledger"]
        decision_requests.REQUESTS_PATH, decision_requests.REPORT_PATH = original_paths["decision_requests"]
        response_ledger.LEDGER_PATH, response_ledger.REPORT_PATH = original_paths["response_ledger"]

    checks = [
        _check("decision_request_created", bool(request.get("id")), request),
        _check("ledger_decision_recorded", bool(decision.get("id")) and decision.get("request_id") == request.get("id"), decision),
        _check(
            "request_closed_with_ledger_link",
            closed_request.get("status") == "closed" and closed_request.get("linked_ledger_entry") == decision.get("id"),
            closed_request,
        ),
        _check("request_summary_updated", request_payload.get("summary", {}).get("closed") == 1, request_payload.get("summary", {})),
        _check("ledger_summary_updated", ledger_payload.get("summary", {}).get("deferred") == 1, ledger_payload.get("summary", {})),
        _check("response_template_valid", validation.get("summary", {}).get("status") == "PASS", validation.get("summary", {})),
        _check("response_recorded", response_entry.get("validation_status") == "PASS", response_entry),
        _check("response_contract_fingerprint_recorded", bool(response_entry.get("recorded_contract_fingerprint")), response_entry),
        _check("response_current_contract_revalidated", response_entry.get("current_contract_validation_status") == "PASS" and response_entry.get("contract_fingerprint_status") == "match", response_entry),
        _check("response_ledger_summary_updated", response_ledger_payload.get("summary", {}).get("valid") == 1 and response_ledger_payload.get("summary", {}).get("current_contract_valid") == 1, response_ledger_payload.get("summary", {})),
        _check("reseal_request_created_without_approval", reseal_request.get("status") == "open" and reseal_request.get("derived_policy_id") == "refresh_human_seal_after_impacting_changes", reseal_request),
        _check("reseal_request_is_idempotent", same_reseal_request.get("id") == reseal_request.get("id"), same_reseal_request),
    ]
    failed = [check for check in checks if not check["passed"]]
    return {
        "summary": {
            "total_checks": len(checks),
            "passed_checks": len(checks) - len(failed),
            "failed_checks": len(failed),
            "status": "PASS" if not failed else "FAIL",
        },
        "checks": checks,
    }


def build_validation() -> dict[str, Any]:
    scratch_root = CODE_MAPS_DIR / "scratch"
    scratch_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="hitl_lifecycle_smoke_", dir=str(scratch_root)) as tmp:
        result = _run_isolated_smoke(Path(tmp))
    return {
        "meta": {
            "kind": "hitl_lifecycle_smoke",
            "version": "v1",
            "generated_at": _utc_now(),
            "generator": "tools.validate_hitl_lifecycle_smoke",
            "isolation": "temporary_scratch_directory",
        },
        **result,
    }


def render_report(validation: dict[str, Any]) -> str:
    summary = validation.get("summary", {})
    lines = [
        "# HITL Lifecycle Smoke",
        "",
        f"- status: `{summary.get('status')}`",
        f"- checks: `{summary.get('passed_checks')}/{summary.get('total_checks')}`",
        f"- failed_checks: `{summary.get('failed_checks')}`",
        f"- isolation: `{validation.get('meta', {}).get('isolation')}`",
        "",
        "## Checks",
        "",
        "| Check | Result | Details |",
        "|---|---|---|",
    ]
    for check in validation.get("checks", []):
        result = "PASS" if check.get("passed") else "FAIL"
        details = json.dumps(check.get("details"), ensure_ascii=False)[:500]
        lines.append(f"| `{check.get('name')}` | {result} | `{details}` |")
    return "\n".join(lines) + "\n"


def run() -> dict[str, Any]:
    validation = build_validation()
    save_json_atomic(RAW_DIR / "hitl_lifecycle_smoke.json", validation)
    save_text_atomic(REPORTS_DIR / "hitl_lifecycle_smoke.md", render_report(validation))
    return validation


def main() -> int:
    validation = run()
    print(json.dumps(validation.get("summary", {}), ensure_ascii=False))
    return 0 if validation.get("summary", {}).get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
