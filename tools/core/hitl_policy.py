from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tools.core.config import CONFIG_DIR
from tools.core.hitl_ledger_state import approval_status, ledger_entries


POLICY_PATH = CONFIG_DIR / "hitl_policy.json"


def load_hitl_policy(path: Path | None = None) -> dict[str, Any]:
    try:
        payload = json.loads((path or POLICY_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def watchdog_scope(path: Path | str) -> str:
    return f"watchdog:{Path(path).resolve().as_posix()}"


def active_approval(gate: str, scope: str, *, ledger: dict[str, Any] | None = None) -> dict[str, Any]:
    from tools.hitl_approval_ledger import _load_ledger, verify_ledger

    payload = ledger if isinstance(ledger, dict) else _load_ledger()
    integrity = verify_ledger(payload)
    if not str(integrity.get("status") or "").startswith("PASS"):
        return {"approved": False, "reason": "ledger_integrity_failed", "integrity": integrity}
    decision = approval_status(gate, scope, ledger_entries(payload))
    return {**decision, "integrity": integrity}


def authorize_action(action_id: str, scope: str, *, ledger: dict[str, Any] | None = None) -> dict[str, Any]:
    """Authorize a governance action through the single progressive-HITL policy gate."""
    policy = load_hitl_policy()
    actions = policy.get("actions", {}) if isinstance(policy.get("actions"), dict) else {}
    action = actions.get(action_id, {}) if isinstance(actions.get(action_id), dict) else {}
    approval_mode = str(action.get("approval_mode") or "hard").lower()
    gate = str(action.get("gate") or action_id)
    fallback = action.get("fallback", "advise_without_mutation")

    base = {
        "action_id": action_id,
        "approval_mode": approval_mode,
        "gate": gate,
        "scope": scope,
        "fallback": fallback,
    }

    if approval_mode in {"deny", "deny_by_default", "forbidden"}:
        return {**base, "approved": False, "reason": "policy_denied"}
    if approval_mode in {"soft", "advisory", "auto"}:
        return {**base, "approved": True, "reason": f"{approval_mode}_policy"}

    decision = active_approval(gate, scope, ledger=ledger)
    return {**base, **decision}


def authorize_watchdog_restore(path: Path | str) -> dict[str, Any]:
    scope = watchdog_scope(path)
    return authorize_action("watchdog_auto_restore", scope)
