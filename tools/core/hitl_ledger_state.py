from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def parse_optional_iso_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def is_not_expired(value: Any) -> bool:
    parsed = parse_optional_iso_timestamp(value)
    if parsed is None:
        return not bool(value)
    return parsed > datetime.now(timezone.utc)


def ledger_entries(ledger: dict[str, Any]) -> list[dict[str, Any]]:
    entries = ledger.get("entries", []) if isinstance(ledger, dict) else []
    return [entry for entry in entries if isinstance(entry, dict)] if isinstance(entries, list) else []


def latest_decisions_by_gate_scope(entries: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in sorted(entries, key=lambda item: str(item.get("created_at") or "")):
        gate = str(entry.get("gate") or "")
        scope = str(entry.get("scope") or "")
        if gate and scope:
            latest[(gate, scope)] = entry
    return latest


def effective_active_approvals(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    active = []
    for entry in latest_decisions_by_gate_scope(entries).values():
        if entry.get("decision") != "approved" or entry.get("revoked_by"):
            continue
        if not is_not_expired(entry.get("expires_at")):
            continue
        active.append(entry)
    return active


def approval_status(gate: str, scope: str, entries: list[dict[str, Any]]) -> dict[str, Any]:
    latest = latest_decisions_by_gate_scope(entries).get((gate, scope))
    if not latest:
        return {"approved": False, "reason": "matching_approval_not_found"}
    if latest.get("decision") != "approved" or latest.get("revoked_by"):
        return {"approved": False, "reason": f"latest_decision_{latest.get('decision')}", "entry": latest}
    if not is_not_expired(latest.get("expires_at")):
        return {"approved": False, "reason": "approval_expired", "entry": latest}
    return {"approved": True, "reason": "active_signed_approval", "entry": latest}


def summarize_ledger_entries(entries: list[dict[str, Any]]) -> dict[str, Any]:
    effective_active = effective_active_approvals(entries)
    return {
        "entries": len(entries),
        "active_approvals": len(effective_active),
        "effective_active_approvals": len(effective_active),
        "approved": sum(1 for entry in entries if entry.get("decision") == "approved"),
        "historical_approved": sum(1 for entry in entries if entry.get("decision") == "approved"),
        "expired_approvals": sum(
            1
            for entry in entries
            if entry.get("decision") == "approved" and not is_not_expired(entry.get("expires_at"))
        ),
        "rejected": sum(1 for entry in entries if entry.get("decision") == "rejected"),
        "deferred": sum(1 for entry in entries if entry.get("decision") == "deferred"),
        "revoked": sum(1 for entry in entries if entry.get("decision") == "revoked" or entry.get("revoked_by")),
    }
