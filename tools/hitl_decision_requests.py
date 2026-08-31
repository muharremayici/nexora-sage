from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.core.config import CONFIG_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file


CONTRACT_PATH = CONFIG_DIR / "hitl_decision_request_contract.json"
REQUESTS_PATH = RAW_DIR / "hitl_decision_requests.json"
REPORT_PATH = REPORTS_DIR / "hitl_decision_requests.md"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_requests() -> dict[str, Any]:
    payload = load_json_file(REQUESTS_PATH, {})
    if isinstance(payload, dict) and isinstance(payload.get("requests"), list):
        return payload
    return {
        "meta": {
            "kind": "hitl_decision_requests",
            "version": "v1",
            "created_at": _utc_now(),
            "generator": "tools.hitl_decision_requests",
        },
        "summary": {"requests": 0, "open": 0, "closed": 0, "superseded": 0},
        "requests": [],
    }


def _string_set(value: Any) -> set[str]:
    return {str(item).strip() for item in value if str(item).strip()} if isinstance(value, list) else set()


def _string_list(value: Any) -> list[str]:
    return [str(item).strip() for item in value if str(item).strip()] if isinstance(value, list) else []


def _load_contract() -> dict[str, Any]:
    payload = load_json_file(CONTRACT_PATH, {})
    return payload if isinstance(payload, dict) else {}


def _validation_contract() -> dict[str, Any]:
    payload = _load_contract().get("validation_contract", {})
    return payload if isinstance(payload, dict) else {}


def _valid_statuses() -> set[str]:
    return _string_set(_validation_contract().get("valid_statuses"))


def _valid_risks() -> set[str]:
    return _string_set(_validation_contract().get("valid_risks"))


def _valid_human_responses() -> list[str]:
    return _string_list(_validation_contract().get("valid_human_responses"))


def _safe_text(value: str) -> str:
    return str(value or "").strip()


def _request_id(gate: str, scope: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    raw = f"{gate}-{scope}"[:90]
    safe = "".join(ch.lower() if ch.isalnum() else "_" for ch in raw).strip("_") or "decision_request"
    return f"{stamp}_{safe}"


def _summarize(requests: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "requests": len(requests),
        "open": sum(1 for item in requests if item.get("status") == "open"),
        "closed": sum(1 for item in requests if item.get("status") == "closed"),
        "superseded": sum(1 for item in requests if item.get("status") == "superseded"),
    }


def _write(payload: dict[str, Any]) -> dict[str, Any]:
    payload["meta"]["updated_at"] = _utc_now()
    payload["summary"] = _summarize(payload.get("requests", []))
    save_json_atomic(REQUESTS_PATH, payload)
    save_text_atomic(REPORT_PATH, render_report(payload))
    return payload


def create_request(
    gate: str,
    scope: str,
    proposed_action: str,
    risk: str,
    rationale: str,
    evidence: list[str] | None = None,
    confidence: str = "medium",
    requested_by: str = "ai_agent",
) -> dict[str, Any]:
    gate = _safe_text(gate)
    scope = _safe_text(scope)
    risk = _safe_text(risk).lower()
    if not gate:
        raise ValueError("gate is required")
    if not scope:
        raise ValueError("scope is required")
    valid_risks = _valid_risks()
    if risk not in valid_risks:
        raise ValueError(f"risk must be one of: {', '.join(sorted(valid_risks))}")

    payload = _load_requests()
    request = {
        "id": _request_id(gate, scope),
        "created_at": _utc_now(),
        "status": "open",
        "gate": gate,
        "scope": scope,
        "proposed_action": _safe_text(proposed_action),
        "risk": risk,
        "confidence": _safe_text(confidence) or "medium",
        "requested_by": _safe_text(requested_by) or "ai_agent",
        "rationale": _safe_text(rationale),
        "evidence": [item for item in (evidence or []) if _safe_text(item)],
        "required_human_response": "|".join(_valid_human_responses()),
    }
    payload["requests"].append(request)
    _write(payload)
    return request


def ensure_derived_request(policy_id: str, scope: str, evidence: list[str] | None = None) -> dict[str, Any]:
    policies = _load_contract().get("derived_request_policies", {})
    policies = policies if isinstance(policies, dict) else {}
    policy = policies.get(policy_id)
    if not isinstance(policy, dict):
        raise ValueError(f"derived HITL request policy not found: {policy_id}")

    payload = _load_requests()
    for item in payload.get("requests", []):
        if (
            isinstance(item, dict)
            and item.get("status") == "open"
            and item.get("derived_policy_id") == policy_id
            and item.get("scope") == scope
        ):
            return item

    request = create_request(
        gate=str(policy.get("gate") or ""),
        scope=scope,
        proposed_action=str(policy.get("proposed_action") or ""),
        risk=str(policy.get("risk") or ""),
        rationale=str(policy.get("rationale") or ""),
        evidence=evidence,
        confidence=str(policy.get("confidence") or "medium"),
        requested_by=str(policy.get("requested_by") or "SAGE"),
    )
    payload = _load_requests()
    for item in payload.get("requests", []):
        if isinstance(item, dict) and item.get("id") == request.get("id"):
            item["derived_policy_id"] = policy_id
            _write(payload)
            return item
    raise RuntimeError("derived HITL request was created but could not be reloaded")


def update_request_status(request_id: str, status: str, reason: str = "", linked_ledger_entry: str = "") -> dict[str, Any]:
    request_id = _safe_text(request_id)
    status = _safe_text(status).lower()
    valid_statuses = _valid_statuses()
    if status not in valid_statuses:
        raise ValueError(f"status must be one of: {', '.join(sorted(valid_statuses))}")
    if not request_id:
        raise ValueError("request_id is required")

    payload = _load_requests()
    for item in payload.get("requests", []):
        if not isinstance(item, dict) or item.get("id") != request_id:
            continue
        item["status"] = status
        item["updated_at"] = _utc_now()
        item["status_reason"] = _safe_text(reason)
        if linked_ledger_entry:
            item["linked_ledger_entry"] = _safe_text(linked_ledger_entry)
        _write(payload)
        return item
    raise ValueError(f"request not found: {request_id}")


def init_requests() -> dict[str, Any]:
    return _write(_load_requests())


def render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# HITL Decision Requests",
        "",
        f"- updated_at: `{payload.get('meta', {}).get('updated_at')}`",
        f"- requests: `{summary.get('requests')}`",
        f"- open: `{summary.get('open')}`",
        f"- closed: `{summary.get('closed')}`",
        f"- superseded: `{summary.get('superseded')}`",
        "",
        "## Requests",
        "",
    ]
    requests = payload.get("requests", [])
    if not requests:
        lines.append("- No decision requests recorded yet.")
        return "\n".join(lines) + "\n"

    lines.extend(["| Created | Status | Gate | Risk | Scope | Proposed Action | Evidence |", "|---|---|---|---|---|---|---|"])
    for item in requests:
        evidence = "<br>".join(f"`{path}`" for path in item.get("evidence", []))
        lines.append(
            f"| `{item.get('created_at')}` | `{item.get('status')}` | `{item.get('gate')}` | `{item.get('risk')}` | `{item.get('scope')}` | {item.get('proposed_action') or ''} | {evidence} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage HITL decision requests opened by AI agents.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Create or refresh the decision request report.")
    init_parser.set_defaults(func=lambda args: init_requests())

    create_parser = subparsers.add_parser("create", help="Create a decision request for human review.")
    create_parser.add_argument("--gate", required=True)
    create_parser.add_argument("--scope", required=True)
    create_parser.add_argument("--proposed-action", required=True)
    create_parser.add_argument("--risk", required=True, choices=sorted(_valid_risks()))
    create_parser.add_argument("--rationale", default="")
    create_parser.add_argument("--evidence", action="append", default=[])
    create_parser.add_argument("--confidence", default="medium")
    create_parser.add_argument("--requested-by", default="ai_agent")
    create_parser.set_defaults(
        func=lambda args: create_request(
            gate=args.gate,
            scope=args.scope,
            proposed_action=args.proposed_action,
            risk=args.risk,
            rationale=args.rationale,
            evidence=args.evidence,
            confidence=args.confidence,
            requested_by=args.requested_by,
        )
    )

    update_parser = subparsers.add_parser("status", help="Update a decision request status.")
    update_parser.add_argument("--request-id", required=True)
    update_parser.add_argument("--status", required=True, choices=sorted(_valid_statuses()))
    update_parser.add_argument("--reason", default="")
    update_parser.add_argument("--linked-ledger-entry", default="")
    update_parser.set_defaults(
        func=lambda args: update_request_status(
            request_id=args.request_id,
            status=args.status,
            reason=args.reason,
            linked_ledger_entry=args.linked_ledger_entry,
        )
    )

    args = parser.parse_args()
    result = args.func(args)
    if isinstance(result, dict) and result.get("requests") is not None:
        print(json.dumps(result.get("summary", {}), ensure_ascii=False))
    else:
        print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
