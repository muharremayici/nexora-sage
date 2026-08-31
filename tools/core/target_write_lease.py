"""SQLite-backed, target-scoped coordination for cooperating SAGE agents.

This is intentionally a lease protocol, not a claim of a cross-host filesystem
lock. It protects agents sharing one target-analysis SQLite database while
leaving read-only SAGE work concurrent.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any

from tools.core.db import SQLiteManager
from tools.core.json_io import load_json_file


def _policy() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[2] / "config" / "target_write_lease_policy.json"
    payload = load_json_file(path, {})
    return payload if isinstance(payload, dict) else {}


def _normalize_root(analysis_root: str | Path) -> str:
    return str(Path(analysis_root).expanduser().resolve())


def _normalize_target_file(target_file: str) -> str:
    normalized = str(target_file or "").replace("\\", "/").strip().lstrip("/")
    if not normalized or normalized.startswith("../") or "/../" in normalized:
        raise ValueError("target_file must be a normalized repository-relative path")
    return normalized


def _lease_key(analysis_root: str, target_file: str) -> str:
    scope = f"{analysis_root}\n{target_file}".encode("utf-8")
    return hashlib.sha256(scope).hexdigest()


def _ttl_seconds(requested_ttl_seconds: int | None) -> int:
    policy = _policy()
    lease = policy.get("lease") if isinstance(policy.get("lease"), dict) else {}
    default = int(lease.get("default_ttl_seconds") or 300)
    minimum = int(lease.get("minimum_ttl_seconds") or 30)
    maximum = int(lease.get("maximum_ttl_seconds") or 1800)
    requested = default if requested_ttl_seconds is None else int(requested_ttl_seconds)
    return max(minimum, min(maximum, requested))


def target_write_lease_actions() -> set[str]:
    """Return the centrally declared lease actions; malformed policy fails closed."""
    policy = _policy()
    lease = policy.get("lease") if isinstance(policy.get("lease"), dict) else {}
    actions = lease.get("allowed_actions") if isinstance(lease.get("allowed_actions"), list) else []
    return {str(action).strip().lower() for action in actions if str(action).strip()}


def _lease_payload(row: Any, *, now_epoch: float) -> dict[str, Any]:
    expires_at = float(row["expires_at_epoch"])
    return {
        "lease_key": str(row["lease_key"]),
        "analysis_root": str(row["analysis_root"]),
        "target_file": str(row["target_file"]),
        "actor_id": str(row["actor_id"]),
        "source_snapshot_hash": str(row["source_snapshot_hash"]),
        "acquired_at_epoch": float(row["acquired_at_epoch"]),
        "renewed_at_epoch": float(row["renewed_at_epoch"]),
        "expires_at_epoch": expires_at,
        "remaining_seconds": max(0, int(expires_at - now_epoch)),
    }


def acquire_target_write_lease(
    db_path: Path,
    *,
    analysis_root: str | Path,
    target_file: str,
    actor_id: str,
    source_snapshot_hash: str,
    ttl_seconds: int | None = None,
) -> dict[str, Any]:
    """Atomically acquire or renew one target-file lease in a target SQLite DB."""
    actor = str(actor_id or "").strip()
    snapshot_hash = str(source_snapshot_hash or "").strip()
    if not actor:
        raise ValueError("actor_id is required for a target write lease")
    if not snapshot_hash:
        raise ValueError("source_snapshot_hash is required for a target write lease")

    root = _normalize_root(analysis_root)
    file_path = _normalize_target_file(target_file)
    key = _lease_key(root, file_path)
    ttl = _ttl_seconds(ttl_seconds)
    now = time.time()
    manager = SQLiteManager(Path(db_path))
    manager.initialize_schema()

    with manager.get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE;")
        conn.execute("DELETE FROM target_write_leases WHERE expires_at_epoch <= ?;", (now,))
        row = conn.execute(
            "SELECT * FROM target_write_leases WHERE lease_key = ?;", (key,)
        ).fetchone()
        if row and str(row["actor_id"]) != actor:
            active = _lease_payload(row, now_epoch=now)
            return {
                "status": "busy",
                "lease": active,
                "retry_after_seconds": max(1, active["remaining_seconds"]),
                "agent_instruction": _policy().get("agent_protocol", {}).get("busy_instruction", "Wait and retry."),
            }

        expires = now + ttl
        if row:
            conn.execute(
                """
                UPDATE target_write_leases
                SET source_snapshot_hash = ?, expires_at_epoch = ?, renewed_at_epoch = ?
                WHERE lease_key = ?;
                """,
                (snapshot_hash, expires, now, key),
            )
            state = "renewed"
        else:
            conn.execute(
                """
                INSERT INTO target_write_leases (
                    lease_key, analysis_root, target_file, actor_id,
                    source_snapshot_hash, acquired_at_epoch, expires_at_epoch, renewed_at_epoch
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (key, root, file_path, actor, snapshot_hash, now, expires, now),
            )
            state = "acquired"

        current = conn.execute(
            "SELECT * FROM target_write_leases WHERE lease_key = ?;", (key,)
        ).fetchone()
    return {"status": state, "lease": _lease_payload(current, now_epoch=now), "retry_after_seconds": 0}


def release_target_write_lease(
    db_path: Path,
    *,
    analysis_root: str | Path,
    target_file: str,
    actor_id: str,
) -> dict[str, Any]:
    """Release only the caller's active target-file lease."""
    actor = str(actor_id or "").strip()
    if not actor:
        raise ValueError("actor_id is required to release a target write lease")
    root = _normalize_root(analysis_root)
    file_path = _normalize_target_file(target_file)
    key = _lease_key(root, file_path)
    now = time.time()
    manager = SQLiteManager(Path(db_path))
    manager.initialize_schema()
    with manager.get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE;")
        conn.execute("DELETE FROM target_write_leases WHERE expires_at_epoch <= ?;", (now,))
        row = conn.execute("SELECT * FROM target_write_leases WHERE lease_key = ?;", (key,)).fetchone()
        if row is None:
            return {"status": "not_found", "released": False}
        if str(row["actor_id"]) != actor:
            active = _lease_payload(row, now_epoch=now)
            return {"status": "not_owner", "released": False, "lease": active}
        conn.execute("DELETE FROM target_write_leases WHERE lease_key = ?;", (key,))
    return {"status": "released", "released": True}


def inspect_target_write_lease(
    db_path: Path,
    *,
    analysis_root: str | Path,
    target_file: str,
) -> dict[str, Any]:
    """Return the current active lease without acquiring a mutation right."""
    root = _normalize_root(analysis_root)
    file_path = _normalize_target_file(target_file)
    key = _lease_key(root, file_path)
    now = time.time()
    manager = SQLiteManager(Path(db_path))
    manager.initialize_schema()
    with manager.get_connection() as conn:
        conn.execute("DELETE FROM target_write_leases WHERE expires_at_epoch <= ?;", (now,))
        row = conn.execute("SELECT * FROM target_write_leases WHERE lease_key = ?;", (key,)).fetchone()
    return {"status": "available", "lease": None} if row is None else {"status": "held", "lease": _lease_payload(row, now_epoch=now)}
