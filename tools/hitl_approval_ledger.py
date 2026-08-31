from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.core.config import CONFIG_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.hitl_ledger_state import summarize_ledger_entries
from tools.core.json_io import load_json_file


LEDGER_PATH = RAW_DIR / "hitl_approval_ledger.json"
REPORT_PATH = REPORTS_DIR / "hitl_approval_ledger.md"
GOVERNANCE_CONTRACT_PATH = CONFIG_DIR / "hitl_governance_contract.json"
SIGNATURE_ALGORITHM = "HMAC-SHA256"
GENESIS_CHAIN_HASH = "GENESIS"
SECRET_PATH = CONFIG_DIR / ".nexora_hitl_secret"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_ledger() -> dict[str, Any]:
    payload = load_json_file(LEDGER_PATH, {})
    if isinstance(payload, dict) and isinstance(payload.get("entries"), list):
        return payload
    return {
        "meta": {
            "kind": "hitl_approval_ledger",
            "version": "v1",
            "created_at": _utc_now(),
            "generator": "tools.hitl_approval_ledger",
        },
        "summary": {
            "entries": 0,
            "active_approvals": 0,
            "rejected": 0,
            "deferred": 0,
            "revoked": 0,
        },
        "entries": [],
    }


def _safe_text(value: str) -> str:
    return str(value or "").strip()


def _valid_decisions() -> set[str]:
    payload = load_json_file(GOVERNANCE_CONTRACT_PATH, {})
    validation = payload.get("validation_contract", {}) if isinstance(payload, dict) else {}
    values = validation.get("valid_ledger_decisions")
    return {str(item).strip() for item in values if str(item).strip()} if isinstance(values, list) else set()


def _get_signing_secret() -> tuple[bytes, str]:
    env_secret = os.environ.get("NEXORA_HITL_SECRET") or os.environ.get("CODEMAPS_HITL_SECRET")
    if env_secret:
        return env_secret.encode("utf-8"), "env:NEXORA_HITL_SECRET"

    if not SECRET_PATH.exists():
        SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
        SECRET_PATH.write_text(secrets.token_hex(32), encoding="utf-8")
    return SECRET_PATH.read_text(encoding="utf-8").strip().encode("utf-8"), str(SECRET_PATH)


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _signature_payload(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in entry.items()
        if key not in {"signature", "chain_hash", "signature_algorithm", "signature_key_source"}
    }


def _legacy_entry_hash(entry: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(_signature_payload(entry)).encode("utf-8")).hexdigest()


def _sign_entry(entry: dict[str, Any], prev_chain_hash: str) -> dict[str, Any]:
    secret, source = _get_signing_secret()
    entry["prev_chain_hash"] = prev_chain_hash
    payload = _signature_payload(entry)
    signature = hmac.new(secret, _canonical_json(payload).encode("utf-8"), hashlib.sha256).hexdigest()
    chain_hash = hashlib.sha256(f"{prev_chain_hash}:{signature}".encode("utf-8")).hexdigest()
    entry["signature_algorithm"] = SIGNATURE_ALGORITHM
    entry["signature_key_source"] = source
    entry["signature"] = signature
    entry["chain_hash"] = chain_hash
    return entry


def verify_ledger(ledger: dict[str, Any] | None = None) -> dict[str, Any]:
    ledger = ledger if isinstance(ledger, dict) else _load_ledger()
    entries = ledger.get("entries", [])
    if not isinstance(entries, list):
        return {
            "status": "FAIL",
            "signed_entries": 0,
            "legacy_unsigned_entries": 0,
            "invalid_entries": [{"index": None, "reason": "entries_not_list"}],
            "last_chain_hash": None,
        }

    try:
        secret, source = _get_signing_secret()
    except Exception as exc:
        return {
            "status": "FAIL",
            "signed_entries": 0,
            "legacy_unsigned_entries": 0,
            "invalid_entries": [{"index": None, "reason": f"secret_unavailable:{exc}"}],
            "last_chain_hash": None,
        }

    prev = GENESIS_CHAIN_HASH
    signed = 0
    unsigned = 0
    invalid: list[dict[str, Any]] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            invalid.append({"index": index, "reason": "entry_not_object"})
            continue

        signature = entry.get("signature")
        chain_hash = entry.get("chain_hash")
        if not signature or not chain_hash:
            unsigned += 1
            prev = _legacy_entry_hash(entry)
            continue

        if entry.get("signature_algorithm") != SIGNATURE_ALGORITHM:
            invalid.append({"index": index, "id": entry.get("id"), "reason": "unsupported_signature_algorithm"})
        if entry.get("prev_chain_hash") != prev:
            invalid.append(
                {
                    "index": index,
                    "id": entry.get("id"),
                    "reason": "prev_chain_hash_mismatch",
                    "expected": prev,
                    "actual": entry.get("prev_chain_hash"),
                }
            )

        expected_sig = hmac.new(
            secret,
            _canonical_json(_signature_payload(entry)).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        expected_chain = hashlib.sha256(f"{entry.get('prev_chain_hash')}:{expected_sig}".encode("utf-8")).hexdigest()
        if not hmac.compare_digest(str(signature), expected_sig):
            invalid.append({"index": index, "id": entry.get("id"), "reason": "signature_mismatch"})
        if not hmac.compare_digest(str(chain_hash), expected_chain):
            invalid.append({"index": index, "id": entry.get("id"), "reason": "chain_hash_mismatch"})
        signed += 1
        prev = str(chain_hash)

    status = "PASS" if not invalid else "FAIL"
    if unsigned and not invalid:
        status = "PASS_WITH_LEGACY_UNSIGNED"
    return {
        "status": status,
        "signed_entries": signed,
        "legacy_unsigned_entries": unsigned,
        "invalid_entries": invalid[:50],
        "last_chain_hash": prev if entries else GENESIS_CHAIN_HASH,
        "signature_algorithm": SIGNATURE_ALGORITHM,
        "signature_key_source": source,
    }


def _entry_id(gate: str, decision: str, scope: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    raw = f"{gate}-{decision}-{scope}"[:80]
    safe = "".join(ch.lower() if ch.isalnum() else "_" for ch in raw).strip("_") or "approval"
    return f"{stamp}_{safe}"


def _summarize(entries: list[dict[str, Any]]) -> dict[str, Any]:
    return summarize_ledger_entries(entries)


def _write_ledger(ledger: dict[str, Any]) -> dict[str, Any]:
    entries = ledger.get("entries", [])
    ledger["meta"]["updated_at"] = _utc_now()
    ledger["summary"] = _summarize(entries if isinstance(entries, list) else [])
    ledger["integrity"] = verify_ledger(ledger)
    save_json_atomic(LEDGER_PATH, ledger)
    save_text_atomic(REPORT_PATH, render_report(ledger))
    return ledger


def record_decision(
    gate: str,
    decision: str,
    scope: str,
    actor: str,
    rationale: str,
    evidence: list[str] | None = None,
    expires_at: str = "",
    request_id: str = "",
) -> dict[str, Any]:
    decision = _safe_text(decision).lower()
    valid_decisions = _valid_decisions()
    if decision not in valid_decisions:
        raise ValueError(f"decision must be one of: {', '.join(sorted(valid_decisions))}")
    gate = _safe_text(gate)
    scope = _safe_text(scope)
    if not gate:
        raise ValueError("gate is required")
    if not scope:
        raise ValueError("scope is required")

    ledger = _load_ledger()
    integrity = verify_ledger(ledger)
    if integrity.get("status") == "FAIL":
        raise ValueError(f"HITL ledger integrity check failed: {integrity.get('invalid_entries')}")
    prev_chain_hash = str(integrity.get("last_chain_hash") or GENESIS_CHAIN_HASH)
    entry = {
        "id": _entry_id(gate, decision, scope),
        "created_at": _utc_now(),
        "gate": gate,
        "decision": decision,
        "scope": scope,
        "actor": _safe_text(actor) or "human",
        "rationale": _safe_text(rationale),
        "evidence": [item for item in (evidence or []) if _safe_text(item)],
        "expires_at": _safe_text(expires_at) or None,
        "request_id": _safe_text(request_id) or None,
    }
    _sign_entry(entry, prev_chain_hash)
    ledger["entries"].append(entry)
    _write_ledger(ledger)
    return entry


def render_report(ledger: dict[str, Any]) -> str:
    summary = ledger.get("summary", {})
    lines = [
        "# HITL Approval Ledger",
        "",
        f"- updated_at: `{ledger.get('meta', {}).get('updated_at')}`",
        f"- entries: `{summary.get('entries')}`",
        f"- active_approvals: `{summary.get('active_approvals')}`",
        f"- effective_active_approvals: `{summary.get('effective_active_approvals')}`",
        f"- approved: `{summary.get('approved')}`",
        f"- expired_approvals: `{summary.get('expired_approvals')}`",
        f"- rejected: `{summary.get('rejected')}`",
        f"- deferred: `{summary.get('deferred')}`",
        f"- revoked: `{summary.get('revoked')}`",
        f"- integrity: `{(ledger.get('integrity') or {}).get('status')}`",
        f"- signed_entries: `{(ledger.get('integrity') or {}).get('signed_entries')}`",
        f"- legacy_unsigned_entries: `{(ledger.get('integrity') or {}).get('legacy_unsigned_entries')}`",
        "",
        "## Entries",
        "",
    ]
    entries = ledger.get("entries", [])
    if not entries:
        lines.append("- No approval decisions recorded yet.")
        return "\n".join(lines) + "\n"

    lines.extend(["| Created | Gate | Decision | Scope | Actor | Rationale | Evidence |", "|---|---|---|---|---|---|---|"])
    for entry in entries:
        evidence = "<br>".join(f"`{item}`" for item in entry.get("evidence", []))
        lines.append(
            f"| `{entry.get('created_at')}` | `{entry.get('gate')}` | `{entry.get('decision')}` | `{entry.get('scope')}` | `{entry.get('actor')}` | {entry.get('rationale') or ''} | {evidence} |"
        )
    return "\n".join(lines) + "\n"


def init_ledger() -> dict[str, Any]:
    ledger = _load_ledger()
    return _write_ledger(ledger)


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage the Human-in-the-Loop approval ledger.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Create or refresh the ledger report.")
    init_parser.set_defaults(func=lambda args: init_ledger())

    verify_parser = subparsers.add_parser("verify", help="Verify HMAC signatures and the ledger hash chain.")
    verify_parser.set_defaults(func=lambda args: verify_ledger())

    record_parser = subparsers.add_parser("record", help="Record a human approval decision.")
    record_parser.add_argument("--gate", required=True, help="Approval gate id, e.g. mutation_scripts.")
    record_parser.add_argument("--decision", required=True, choices=sorted(_valid_decisions()))
    record_parser.add_argument("--scope", required=True, help="Action/file/repo scope covered by the decision.")
    record_parser.add_argument("--actor", default="human", help="Human approver/reviewer name.")
    record_parser.add_argument("--rationale", default="", help="Reason for the decision.")
    record_parser.add_argument("--evidence", action="append", default=[], help="Evidence artifact path; repeatable.")
    record_parser.add_argument("--expires-at", default="", help="Optional ISO timestamp/date for approval expiry.")
    record_parser.add_argument("--request-id", default="", help="Optional HITL decision request id this decision resolves.")
    record_parser.set_defaults(
        func=lambda args: record_decision(
            gate=args.gate,
            decision=args.decision,
            scope=args.scope,
            actor=args.actor,
            rationale=args.rationale,
            evidence=args.evidence,
            expires_at=args.expires_at,
            request_id=args.request_id,
        )
    )

    args = parser.parse_args()
    result = args.func(args)
    if isinstance(result, dict) and result.get("entries") is not None:
        print(json.dumps(result.get("summary", {}), ensure_ascii=False))
    elif isinstance(result, dict) and result.get("invalid_entries") is not None:
        print(json.dumps(result, ensure_ascii=False))
        return 0 if str(result.get("status", "")).startswith("PASS") else 1
    else:
        print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
