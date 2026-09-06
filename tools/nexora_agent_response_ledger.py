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

from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file
from tools.validate_nexora_agent_response import response_contract_fingerprint, validate_response


LEDGER_PATH = RAW_DIR / "nexora_agent_response_ledger.json"
REPORT_PATH = REPORTS_DIR / "nexora_agent_response_ledger.md"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_ledger() -> dict[str, Any]:
    payload = load_json_file(LEDGER_PATH, {})
    if isinstance(payload, dict) and isinstance(payload.get("entries"), list):
        return payload
    return {
        "meta": {
            "kind": "nexora_agent_response_ledger",
            "version": "v1",
            "created_at": _utc_now(),
            "generator": "tools.nexora_agent_response_ledger",
        },
        "summary": {
            "entries": 0,
            "valid": 0,
            "invalid": 0,
            "human_approval_required": 0,
            "basis": "recorded_validation_status_at_response_time",
        },
        "entries": [],
    }


def _entry_id(task_id: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    raw = str(task_id or "agent_response")[:80]
    safe = "".join(ch.lower() if ch.isalnum() else "_" for ch in raw).strip("_") or "agent_response"
    return f"{stamp}_{safe}"


def _refresh_current_contract_status(entries: list[dict[str, Any]]) -> None:
    current_fingerprint = response_contract_fingerprint()
    for item in entries:
        validation = validate_response(item.get("response") if isinstance(item.get("response"), dict) else {})
        recorded_fingerprint = str(item.get("recorded_contract_fingerprint") or "")
        item["current_contract_fingerprint"] = current_fingerprint
        item["current_contract_validation_status"] = validation.get("summary", {}).get("status")
        item["contract_fingerprint_status"] = (
            "match" if recorded_fingerprint == current_fingerprint
            else "legacy_missing" if not recorded_fingerprint
            else "changed_since_recording"
        )


def _summarize(entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "entries": len(entries),
        "valid": sum(1 for item in entries if item.get("validation_status") == "PASS"),
        "invalid": sum(1 for item in entries if item.get("validation_status") != "PASS"),
        "human_approval_required": sum(1 for item in entries if (item.get("response") or {}).get("human_approval_required") is True),
        "basis": "recorded_validation_status_at_response_time",
        "current_contract_valid": sum(1 for item in entries if item.get("current_contract_validation_status") == "PASS"),
        "current_contract_invalid": sum(1 for item in entries if item.get("current_contract_validation_status") != "PASS"),
        "contract_fingerprint_changed_or_missing": sum(1 for item in entries if item.get("contract_fingerprint_status") != "match"),
        "current_contract_basis": "revalidated_against_current_response_contract",
    }


def _write_ledger(ledger: dict[str, Any]) -> dict[str, Any]:
    _refresh_current_contract_status(ledger.get("entries", []))
    ledger["meta"]["updated_at"] = _utc_now()
    ledger["summary"] = _summarize(ledger.get("entries", []))
    save_json_atomic(LEDGER_PATH, ledger)
    save_text_atomic(REPORT_PATH, render_report(ledger))
    return ledger


def init_ledger() -> dict[str, Any]:
    return _write_ledger(_load_ledger())


def record_response(response: dict[str, Any], task_id: str = "", actor: str = "ai_agent") -> dict[str, Any]:
    validation = validate_response(response if isinstance(response, dict) else {})
    entry = {
        "id": _entry_id(task_id),
        "created_at": _utc_now(),
        "task_id": str(task_id or "").strip() or None,
        "actor": str(actor or "ai_agent").strip(),
        "validation_status": validation.get("summary", {}).get("status"),
        "validation_summary": validation.get("summary", {}),
        "recorded_contract_fingerprint": validation.get("summary", {}).get("contract_fingerprint"),
        "response": response,
    }
    ledger = _load_ledger()
    ledger["entries"].append(entry)
    _write_ledger(ledger)
    return entry


def render_report(ledger: dict[str, Any]) -> str:
    summary = ledger.get("summary", {})
    lines = [
        "# Nexora Agent Response Ledger",
        "",
        f"- updated_at: `{ledger.get('meta', {}).get('updated_at')}`",
        f"- entries: `{summary.get('entries')}`",
        f"- valid: `{summary.get('valid')}`",
        f"- invalid: `{summary.get('invalid')}`",
        f"- human_approval_required: `{summary.get('human_approval_required')}`",
        f"- basis: `{summary.get('basis')}`",
        f"- current_contract_valid: `{summary.get('current_contract_valid')}`",
        f"- current_contract_invalid: `{summary.get('current_contract_invalid')}`",
        f"- contract_fingerprint_changed_or_missing: `{summary.get('contract_fingerprint_changed_or_missing')}`",
        f"- current_contract_basis: `{summary.get('current_contract_basis')}`",
        "",
        "## Entries",
        "",
    ]
    entries = ledger.get("entries", [])
    if not entries:
        lines.append("- No agent responses recorded yet.")
        return "\n".join(lines) + "\n"

    lines.extend(["| Created | Task | Recorded | Current Contract | Fingerprint | Risk | Approval Required | Verdict | Sources |", "|---|---|---|---|---|---|---|---|---|"])
    for entry in entries[-50:]:
        response = entry.get("response") or {}
        sources = "<br>".join(f"`{item}`" for item in response.get("source_artifacts", []))
        verdict = str(response.get("verdict", "")).replace("|", "\\|")
        lines.append(
            f"| `{entry.get('created_at')}` | `{entry.get('task_id')}` | `{entry.get('validation_status')}` | `{entry.get('current_contract_validation_status')}` | `{entry.get('contract_fingerprint_status')}` | `{response.get('risk')}` | `{response.get('human_approval_required')}` | {verdict} | {sources} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Record validated Nexora agent responses.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Create or refresh the agent response ledger.")
    init_parser.set_defaults(func=lambda args: init_ledger())

    record_parser = subparsers.add_parser("record", help="Validate and record an agent response JSON file.")
    record_parser.add_argument("--file", required=True)
    record_parser.add_argument("--task-id", default="")
    record_parser.add_argument("--actor", default="ai_agent")
    record_parser.set_defaults(
        func=lambda args: record_response(
            response=load_json_file(Path(args.file), {}),
            task_id=args.task_id,
            actor=args.actor,
        )
    )

    args = parser.parse_args()
    result = args.func(args)
    if isinstance(result, dict) and result.get("entries") is not None:
        print(json.dumps(result.get("summary", {}), ensure_ascii=False))
    else:
        print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
