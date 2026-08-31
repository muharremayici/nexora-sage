from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from tools.core.config import CONFIG_DIR
from tools.core.json_io import load_json_file


SUPPRESSIONS_FILE = CONFIG_DIR / "codemaps.suppressions.json"


def stable_decision_key(row: dict[str, Any]) -> str:
    source = str(row.get("source") or "")
    candidate = str(row.get("candidate") or "")
    target_path = str(row.get("target_path") or "")
    return f"merge_decision:{source}:{candidate}:{target_path}"


def load_suppressions(path: Path = SUPPRESSIONS_FILE) -> list[dict[str, Any]]:
    payload = load_json_file(path, {}) if path.exists() else {}
    entries = payload.get("suppressions", []) if isinstance(payload, dict) else []
    return [entry for entry in entries if isinstance(entry, dict)]


def _is_active(entry: dict[str, Any], today: date | None = None) -> bool:
    if entry.get("enabled", True) is False:
        return False
    expires_on = str(entry.get("expires_on") or "").strip()
    if not expires_on:
        return True
    try:
        return date.fromisoformat(expires_on) >= (today or date.today())
    except ValueError:
        return False


def _field_matches(entry: dict[str, Any], row: dict[str, Any], field: str) -> bool:
    expected = entry.get(field)
    if expected in (None, "", "*"):
        return True
    actual = row.get(field)
    if isinstance(expected, list):
        return str(actual or "") in {str(item) for item in expected}
    return str(actual or "") == str(expected)


def find_suppression(
    artifact: str,
    row: dict[str, Any],
    entries: list[dict[str, Any]] | None = None,
    today: date | None = None,
) -> dict[str, Any] | None:
    entries = entries if entries is not None else load_suppressions()
    key = stable_decision_key(row)
    for entry in entries:
        if str(entry.get("artifact") or artifact) != artifact:
            continue
        if not _is_active(entry, today=today):
            continue
        expected_key = str(entry.get("key") or "").strip()
        if expected_key and expected_key != key:
            continue
        if not all(_field_matches(entry, row, field) for field in ("source", "candidate", "target_path", "action", "decision")):
            continue
        return {
            "key": expected_key or key,
            "reason": str(entry.get("reason") or "suppressed_by_policy"),
            "owner": str(entry.get("owner") or ""),
            "expires_on": str(entry.get("expires_on") or ""),
        }
    return None
