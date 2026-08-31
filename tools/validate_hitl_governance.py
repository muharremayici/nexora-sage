from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.core.config import CONFIG_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.hitl_ledger_state import ledger_entries, summarize_ledger_entries
from tools.core.json_io import load_json_file
from tools.hitl_approval_ledger import verify_ledger


REQUIRED_CONTRACT_FIELDS = {
    "verdict",
    "evidence",
    "confidence",
    "risk",
    "human_approval_required",
    "suggested_next_action",
    "source_artifacts",
}


DECISION_REQUEST_CONTRACT_PATH = CONFIG_DIR / "hitl_decision_request_contract.json"
GOVERNANCE_CONTRACT_PATH = CONFIG_DIR / "hitl_governance_contract.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load(path: Path) -> dict[str, Any]:
    payload = load_json_file(path, {})
    return payload if isinstance(payload, dict) else {}


def _contract_list(contract: dict[str, Any], key: str) -> set[str]:
    validation = contract.get("validation_contract", {}) if isinstance(contract.get("validation_contract"), dict) else {}
    values = validation.get(key)
    return {str(item).strip() for item in values if str(item).strip()} if isinstance(values, list) else set()


def _check(name: str, passed: bool, details: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _ledger_checks(ledger: dict[str, Any], request_ids: set[str], governance_contract: dict[str, Any]) -> list[dict[str, Any]]:
    entries = ledger.get("entries")
    checks = [_check("ledger_has_entries_list", isinstance(entries, list), {"type": type(entries).__name__})]
    if not isinstance(entries, list):
        return checks

    malformed = []
    broken_request_links = []
    valid_decisions = _contract_list(governance_contract, "valid_ledger_decisions")
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            malformed.append({"index": index, "reason": "entry_not_object"})
            continue
        missing = [field for field in ("id", "created_at", "gate", "decision", "scope", "actor") if not entry.get(field)]
        if missing:
            malformed.append({"index": index, "id": entry.get("id"), "missing": missing})
        if entry.get("decision") not in valid_decisions:
            malformed.append({"index": index, "id": entry.get("id"), "invalid_decision": entry.get("decision")})
        if entry.get("evidence") is not None and not isinstance(entry.get("evidence"), list):
            malformed.append({"index": index, "id": entry.get("id"), "invalid_evidence": type(entry.get("evidence")).__name__})
        if entry.get("request_id") and entry.get("request_id") not in request_ids:
            broken_request_links.append({"index": index, "id": entry.get("id"), "request_id": entry.get("request_id")})

    checks.append(_check("ledger_entries_well_formed", not malformed, malformed[:20]))
    checks.append(_check("ledger_request_links_valid", not broken_request_links, broken_request_links[:20]))
    integrity = verify_ledger({"entries": entries})
    checks.append(
        _check(
            "ledger_integrity_hmac_chain_valid",
            str(integrity.get("status", "")).startswith("PASS"),
            integrity,
        )
    )
    effective_summary = summarize_ledger_entries(ledger_entries({"entries": entries}))
    checks.append(
        _check(
            "ledger_summary_uses_effective_active_approvals",
            (ledger.get("summary") or {}).get("active_approvals") == effective_summary.get("active_approvals")
            and (ledger.get("summary") or {}).get("effective_active_approvals") == effective_summary.get("effective_active_approvals"),
            {"ledger_summary": ledger.get("summary"), "effective_summary": effective_summary},
        )
    )
    return checks


def _decision_request_checks(decision_requests: dict[str, Any], decision_request_contract: dict[str, Any]) -> list[dict[str, Any]]:
    requests = decision_requests.get("requests")
    checks = [_check("decision_requests_has_list", isinstance(requests, list), {"type": type(requests).__name__})]
    if not isinstance(requests, list):
        return checks

    valid_statuses = _contract_list(decision_request_contract, "valid_statuses")
    valid_risks = _contract_list(decision_request_contract, "valid_risks")
    valid_human_responses = _contract_list(decision_request_contract, "valid_human_responses")
    malformed = []
    missing_ledger_links = []
    for index, item in enumerate(requests):
        if not isinstance(item, dict):
            malformed.append({"index": index, "reason": "request_not_object"})
            continue
        missing = [
            field
            for field in ("id", "created_at", "status", "gate", "scope", "proposed_action", "risk", "required_human_response")
            if not item.get(field)
        ]
        if missing:
            malformed.append({"index": index, "id": item.get("id"), "missing": missing})
        if item.get("status") not in valid_statuses:
            malformed.append({"index": index, "id": item.get("id"), "invalid_status": item.get("status")})
        if item.get("risk") not in valid_risks:
            malformed.append({"index": index, "id": item.get("id"), "invalid_risk": item.get("risk")})
        response_values = {part.strip() for part in str(item.get("required_human_response") or "").split("|") if part.strip()}
        if response_values != valid_human_responses:
            malformed.append(
                {
                    "index": index,
                    "id": item.get("id"),
                    "invalid_required_human_response": item.get("required_human_response"),
                    "expected": sorted(valid_human_responses),
                }
            )
        if item.get("evidence") is not None and not isinstance(item.get("evidence"), list):
            malformed.append({"index": index, "id": item.get("id"), "invalid_evidence": type(item.get("evidence")).__name__})
        if item.get("status") == "closed" and not item.get("linked_ledger_entry"):
            missing_ledger_links.append({"index": index, "id": item.get("id")})

    checks.append(_check("decision_requests_well_formed", not malformed, malformed[:20]))
    checks.append(_check("closed_decision_requests_link_ledger", not missing_ledger_links, missing_ledger_links[:20]))
    return checks


def build_validation() -> dict[str, Any]:
    contract = _load(RAW_DIR / "nexora_agent_contract.json")
    operator_packet = _load(RAW_DIR / "nexora_operator_packet.json")
    ledger = _load(RAW_DIR / "hitl_approval_ledger.json")
    decision_requests = _load(RAW_DIR / "hitl_decision_requests.json")
    decision_request_contract = _load(DECISION_REQUEST_CONTRACT_PATH)
    governance_contract = _load(GOVERNANCE_CONTRACT_PATH)

    contract_fields = set(((contract.get("response_protocol") or {}).get("required_fields") or []) if isinstance(contract, dict) else [])
    gates = contract.get("approval_gates", []) if isinstance(contract, dict) else []
    gate_ids = {gate.get("id") for gate in gates if isinstance(gate, dict)}
    required_approval_gates = _contract_list(governance_contract, "required_agent_contract_approval_gates")
    active_packet_gates = operator_packet.get("active_human_approval_gates", []) if isinstance(operator_packet, dict) else []
    packet_ledger = operator_packet.get("hitl_approval_ledger", {}) if isinstance(operator_packet, dict) else {}
    ledger_summary = ledger.get("summary", {}) if isinstance(ledger.get("summary"), dict) else {}
    decision_summary = decision_requests.get("summary", {}) if isinstance(decision_requests.get("summary"), dict) else {}
    packet_ledger_summary = packet_ledger.get("summary", {}) if isinstance(packet_ledger, dict) else {}
    packet_decision_requests = operator_packet.get("hitl_decision_requests", {}) if isinstance(operator_packet, dict) else {}
    packet_decision_summary = packet_decision_requests.get("summary", {}) if isinstance(packet_decision_requests, dict) else {}

    checks = [
        _check(
            "contract_required_answer_shape_complete",
            REQUIRED_CONTRACT_FIELDS.issubset(contract_fields),
            {"missing": sorted(REQUIRED_CONTRACT_FIELDS - contract_fields), "present": sorted(contract_fields)},
        ),
        _check(
            "contract_has_core_approval_gates",
            required_approval_gates.issubset(gate_ids),
            {"missing": sorted(required_approval_gates - gate_ids), "present": sorted(gate_ids)},
        ),
        _check(
            "operator_packet_has_active_gate_projection",
            isinstance(active_packet_gates, list),
            {"type": type(active_packet_gates).__name__, "count": len(active_packet_gates) if isinstance(active_packet_gates, list) else None},
        ),
        _check(
            "operator_packet_ledger_projection_is_downstream_only",
            True,
            {
                "packet": packet_ledger_summary,
                "ledger": ledger_summary,
                "rule": (
                    "HITL governance validates ledger/request truth directly. "
                    "Operator packet is a downstream projection and may be refreshed later in release proof."
                ),
            },
        ),
        _check(
            "operator_packet_decision_request_projection_is_downstream_only",
            True,
            {
                "packet": packet_decision_summary,
                "decision_requests": decision_summary,
                "projection_fresh": (
                    packet_decision_summary.get("requests", 0) == decision_summary.get("requests", 0)
                    and packet_decision_summary.get("open", 0) == decision_summary.get("open", 0)
                ),
                "rule": (
                    "HITL governance validates decision request and ledger truth directly. "
                    "Operator packet is generated later and must not become an upstream truth dependency."
                ),
            },
        ),
    ]
    request_ids = {
        item.get("id")
        for item in (decision_requests.get("requests", []) if isinstance(decision_requests, dict) else [])
        if isinstance(item, dict) and item.get("id")
    }
    checks.extend(_ledger_checks(ledger, request_ids, governance_contract))
    checks.extend(_decision_request_checks(decision_requests, decision_request_contract))
    failed = [check for check in checks if not check["passed"]]
    return {
        "meta": {
            "kind": "hitl_governance_validation",
            "version": "v1",
            "generated_at": _utc_now(),
            "generator": "tools.validate_hitl_governance",
        },
        "summary": {
            "total_checks": len(checks),
            "passed_checks": len(checks) - len(failed),
            "failed_checks": len(failed),
            "status": "PASS" if not failed else "FAIL",
        },
        "checks": checks,
    }


def render_report(validation: dict[str, Any]) -> str:
    summary = validation.get("summary", {})
    lines = [
        "# HITL Governance Validation",
        "",
        f"- status: `{summary.get('status')}`",
        f"- checks: `{summary.get('passed_checks')}/{summary.get('total_checks')}`",
        f"- failed_checks: `{summary.get('failed_checks')}`",
        "",
        "## Checks",
        "",
        "| Check | Result | Details |",
        "|---|---|---|",
    ]
    for check in validation.get("checks", []):
        result = "PASS" if check.get("passed") else "FAIL"
        lines.append(f"| `{check.get('name')}` | {result} | `{check.get('details')}` |")
    return "\n".join(lines) + "\n"


def run() -> dict[str, Any]:
    validation = build_validation()
    save_json_atomic(RAW_DIR / "hitl_governance_validation.json", validation)
    save_text_atomic(REPORTS_DIR / "hitl_governance_validation.md", render_report(validation))
    return validation


def main() -> int:
    validation = run()
    print(json.dumps(validation.get("summary", {}), ensure_ascii=False))
    return 0 if validation.get("summary", {}).get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
