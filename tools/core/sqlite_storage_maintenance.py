"""Read-only SQLite storage telemetry and explicit interruption-honest reclamation."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import socket
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from tools.core.advisory_file_lock import AdvisoryFileLock
from tools.core.db import SQLiteManager
from tools.core.unmanaged_atomic_io import native_filesystem_path


_AUTO_VACUUM_NAMES = {0: "none", 1: "full", 2: "incremental"}
_SUCCESS_STATUSES = {"OBSERVED", "NO_RECLAIMABLE_SPACE", "COMPLETED"}


class SQLiteMaintenanceRefused(RuntimeError):
    """A maintenance mutation was safely refused before reclamation."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = str(code)


def maintenance_succeeded(result: dict[str, Any]) -> bool:
    return str(result.get("status") or "") in _SUCCESS_STATUSES


def _database_identity(path: Path) -> str:
    normalized = os.path.normcase(str(path.resolve())).replace("\\", "/")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _connect(path: Path, *, timeout_seconds: int, read_only: bool = False) -> sqlite3.Connection:
    native_path = Path(native_filesystem_path(path))
    if read_only:
        conn = sqlite3.connect(
            native_path.resolve().as_uri() + "?mode=ro",
            uri=True,
            timeout=float(max(0, timeout_seconds)),
        )
    else:
        conn = sqlite3.connect(native_path, timeout=float(max(0, timeout_seconds)))
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={max(0, int(timeout_seconds)) * 1000};")
    return conn


def _pragma_int(conn: sqlite3.Connection, name: str) -> int:
    row = conn.execute(f"PRAGMA {name};").fetchone()
    return int(row[0] if row else 0)


def _latest_receipt(conn: sqlite3.Connection) -> dict[str, Any] | None:
    table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sage_sqlite_maintenance_runs';"
    ).fetchone()
    if table is None:
        return None
    row = conn.execute(
        """
        SELECT run_id, operation, status, phase, requested_pages, reclaimed_pages,
               error_type, started_at, updated_at, finished_at
        FROM sage_sqlite_maintenance_runs
        ORDER BY started_at DESC, run_id DESC
        LIMIT 1;
        """
    ).fetchone()
    return dict(row) if row is not None else None


def _profile_from_connection(conn: sqlite3.Connection, database_path: Path) -> dict[str, Any]:
    page_size = _pragma_int(conn, "page_size")
    page_count = _pragma_int(conn, "page_count")
    freelist_pages = _pragma_int(conn, "freelist_count")
    auto_vacuum = _pragma_int(conn, "auto_vacuum")
    journal_row = conn.execute("PRAGMA journal_mode;").fetchone()
    main_bytes = int(database_path.stat().st_size) if database_path.is_file() else 0
    wal_path = Path(str(database_path) + "-wal")
    shm_path = Path(str(database_path) + "-shm")
    wal_bytes = int(wal_path.stat().st_size) if wal_path.is_file() else 0
    shm_bytes = int(shm_path.stat().st_size) if shm_path.is_file() else 0
    allocated_bytes = page_size * page_count
    freelist_bytes = page_size * freelist_pages
    live_pages = max(0, page_count - freelist_pages)
    reclaimable_ratio = (freelist_pages / page_count) if page_count else 0.0
    return {
        "database_path": str(database_path.resolve()),
        "database_identity": _database_identity(database_path),
        "database_bytes": main_bytes,
        "wal_bytes": wal_bytes,
        "shm_bytes": shm_bytes,
        "total_storage_bytes": main_bytes + wal_bytes + shm_bytes,
        "page_size_bytes": page_size,
        "page_count": page_count,
        "allocated_bytes": allocated_bytes,
        "freelist_pages": freelist_pages,
        "freelist_bytes": freelist_bytes,
        "estimated_live_pages": live_pages,
        "estimated_live_bytes": live_pages * page_size,
        "reclaimable_ratio": round(reclaimable_ratio, 6),
        "reclaimable_percent": round(reclaimable_ratio * 100.0, 3),
        "auto_vacuum": _AUTO_VACUUM_NAMES.get(auto_vacuum, f"unknown:{auto_vacuum}"),
        "journal_mode": str(journal_row[0] if journal_row else "unknown").lower(),
        "reclamation_path": (
            "none"
            if freelist_pages <= 0
            else "bounded_incremental_vacuum"
            if auto_vacuum == 2
            else "offline_full_vacuum"
        ),
        "latest_maintenance_receipt": _latest_receipt(conn),
        "claim_boundary": (
            "estimated_live_bytes excludes whole free pages only; it is storage telemetry, "
            "not exact logical payload size or proof that partially filled pages are compact."
        ),
    }


def inspect_sqlite_storage(database_path: Path, *, timeout_seconds: int = 5) -> dict[str, Any]:
    """Inspect one existing database without creating or mutating it."""
    path = Path(database_path)
    if not path.is_file():
        return {
            "status": "MISSING",
            "database_path": str(path.resolve()),
            "database_identity": _database_identity(path),
            "reclamation_path": "not_available",
            "claim_boundary": "No database was opened or created.",
        }
    with _connect(path, timeout_seconds=timeout_seconds, read_only=True) as conn:
        profile = _profile_from_connection(conn, path)
    profile["status"] = "OBSERVED"
    return profile


def _quick_check(conn: sqlite3.Connection) -> None:
    rows = [str(row[0]) for row in conn.execute("PRAGMA quick_check;").fetchall()]
    if rows != ["ok"]:
        raise sqlite3.DatabaseError("sqlite_quick_check_failed:" + "|".join(rows[:5]))


def _receipt_profile(profile: dict[str, Any]) -> str:
    bounded = {
        key: value
        for key, value in profile.items()
        if key not in {"database_path", "latest_maintenance_receipt"}
    }
    return json.dumps(bounded, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _begin_receipt(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    operation: str,
    database_identity: str,
    requested_pages: int,
    before: dict[str, Any],
) -> None:
    conn.execute(
        """
        UPDATE sage_sqlite_maintenance_runs
        SET status = 'FAILED', phase = 'INTERRUPTED',
            error_type = 'superseded_interrupted_run',
            updated_at = strftime('%Y-%m-%d %H:%M:%f', 'now'),
            finished_at = strftime('%Y-%m-%d %H:%M:%f', 'now')
        WHERE status = 'IN_PROGRESS';
        """
    )
    conn.execute(
        """
        INSERT INTO sage_sqlite_maintenance_runs (
            run_id, operation, status, phase, database_identity, requested_pages,
            reclaimed_pages, before_profile, after_profile, error_type
        ) VALUES (?, ?, 'IN_PROGRESS', 'PREFLIGHT', ?, ?, 0, ?, NULL, 'none');
        """,
        (
            run_id,
            operation,
            database_identity,
            requested_pages,
            _receipt_profile(before),
        ),
    )
    conn.commit()


def _update_receipt_phase(conn: sqlite3.Connection, run_id: str, phase: str) -> None:
    conn.execute(
        """
        UPDATE sage_sqlite_maintenance_runs
        SET phase = ?, updated_at = strftime('%Y-%m-%d %H:%M:%f', 'now')
        WHERE run_id = ?;
        """,
        (str(phase), str(run_id)),
    )
    conn.commit()


def _finish_receipt(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    status: str,
    phase: str,
    error_type: str,
    reclaimed_pages: int = 0,
    after: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        """
        UPDATE sage_sqlite_maintenance_runs
        SET status = ?, phase = ?, error_type = ?, reclaimed_pages = ?,
            after_profile = ?, updated_at = strftime('%Y-%m-%d %H:%M:%f', 'now'),
            finished_at = strftime('%Y-%m-%d %H:%M:%f', 'now')
        WHERE run_id = ?;
        """,
        (
            str(status),
            str(phase),
            str(error_type),
            max(0, int(reclaimed_pages)),
            _receipt_profile(after) if isinstance(after, dict) else None,
            str(run_id),
        ),
    )
    conn.commit()


def _execute_reclamation(
    conn: sqlite3.Connection,
    *,
    mode: str,
    max_pages: int,
) -> None:
    if mode == "bounded_incremental_vacuum":
        conn.execute(f"PRAGMA incremental_vacuum({max(1, int(max_pages))});")
        conn.commit()
        return
    if mode == "offline_full_vacuum":
        conn.execute("VACUUM;")
        return
    raise SQLiteMaintenanceRefused("unsupported_reclamation_mode", f"Unsupported mode: {mode}")


def run_sqlite_storage_maintenance(
    database_path: Path,
    *,
    lock_path: Path,
    apply: bool = False,
    confirmed: bool = False,
    max_pages: int = 4096,
    full_vacuum_free_space_percent: int = 200,
    timeout_seconds: int = 30,
    progress_interval_seconds: int = 15,
    progress: Callable[[str, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Inspect by default; reclaim only behind explicit apply+confirm and the shared lock."""
    path = Path(database_path)
    before = inspect_sqlite_storage(path, timeout_seconds=min(5, max(1, timeout_seconds)))
    if not apply:
        return {
            "status": "OBSERVED",
            "applied": False,
            "before": before,
            "next_action": (
                "No reclaimable whole pages."
                if int(before.get("freelist_pages") or 0) <= 0
                else "Re-run with --apply --confirm during an offline maintenance window."
            ),
        }
    if not confirmed:
        return {
            "status": "REFUSED_CONFIRMATION_REQUIRED",
            "applied": False,
            "before": before,
            "next_action": "Review status, stop active SAGE processes, then add --confirm.",
        }
    if before.get("status") == "MISSING":
        return {
            "status": "REFUSED_DATABASE_MISSING",
            "applied": False,
            "before": before,
        }

    lock = AdvisoryFileLock(Path(lock_path))
    if not lock.acquire():
        return {
            "status": "BLOCKED_ACTIVE_SAGE_OPERATION",
            "applied": False,
            "before": before,
            "lock_path": str(Path(lock_path)),
        }

    run_id = "sqlite-maintenance-" + uuid.uuid4().hex
    lock_payload = {
        "kind": "sqlite_storage_maintenance_lock",
        "run_id": run_id,
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "command": "sqlite-storage-maintenance",
        "started_at_epoch": time.time(),
        "heartbeat_epoch": time.time(),
    }
    conn: sqlite3.Connection | None = None
    receipt_started = False
    emit = progress or (lambda _phase, _details: None)
    try:
        lock.write_text(json.dumps(lock_payload, ensure_ascii=False, sort_keys=True))
        manager = SQLiteManager(path)
        manager.initialize_schema()
        conn = _connect(path, timeout_seconds=timeout_seconds)
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        before = _profile_from_connection(conn, path)
        mode = str(before.get("reclamation_path") or "none")
        requested_pages = (
            min(max(1, int(max_pages)), int(before.get("freelist_pages") or 0))
            if mode == "bounded_incremental_vacuum"
            else int(before.get("freelist_pages") or 0)
        )
        _begin_receipt(
            conn,
            run_id=run_id,
            operation=mode,
            database_identity=str(before["database_identity"]),
            requested_pages=requested_pages,
            before=before,
        )
        receipt_started = True
        emit("preflight", {"run_id": run_id, "mode": mode, "freelist_pages": requested_pages})

        if int(before.get("freelist_pages") or 0) <= 0:
            after = _profile_from_connection(conn, path)
            _finish_receipt(
                conn,
                run_id,
                status="COMPLETED",
                phase="NO_RECLAIMABLE_SPACE",
                error_type="none",
                after=after,
            )
            return {
                "status": "NO_RECLAIMABLE_SPACE",
                "applied": False,
                "run_id": run_id,
                "before": before,
                "after": after,
            }

        checkpoint = conn.execute("PRAGMA wal_checkpoint(TRUNCATE);").fetchone()
        checkpoint_busy = int(checkpoint[0] if checkpoint else 1)
        if checkpoint_busy:
            raise SQLiteMaintenanceRefused(
                "wal_checkpoint_busy",
                "WAL checkpoint could not complete because another database user is active.",
            )
        _quick_check(conn)

        free_bytes = int(shutil.disk_usage(path.parent).free)
        required_free_bytes = 0
        if mode == "offline_full_vacuum":
            required_free_bytes = math.ceil(
                int(before.get("database_bytes") or 0)
                * max(100, int(full_vacuum_free_space_percent))
                / 100.0
            )
            if free_bytes < required_free_bytes:
                raise SQLiteMaintenanceRefused(
                    "insufficient_free_disk",
                    f"Full VACUUM requires {required_free_bytes} free bytes; observed {free_bytes}.",
                )

        _update_receipt_phase(conn, run_id, "RECLAIMING")
        emit(
            "reclaiming",
            {
                "run_id": run_id,
                "mode": mode,
                "requested_pages": requested_pages,
                "free_bytes": free_bytes,
                "required_free_bytes": required_free_bytes,
            },
        )
        last_progress = time.monotonic()

        def heartbeat() -> int:
            nonlocal last_progress
            now = time.monotonic()
            if now - last_progress >= max(1, int(progress_interval_seconds)):
                try:
                    lock_payload["heartbeat_epoch"] = time.time()
                    lock.write_text(json.dumps(lock_payload, ensure_ascii=False, sort_keys=True))
                    emit(
                        "reclaiming_heartbeat",
                        {"run_id": run_id, "elapsed_seconds": round(now - last_progress, 3)},
                    )
                except (OSError, RuntimeError):
                    pass
                last_progress = now
            return 0

        conn.set_progress_handler(heartbeat, 10000)
        try:
            _execute_reclamation(conn, mode=mode, max_pages=requested_pages)
        finally:
            conn.set_progress_handler(None, 0)

        _update_receipt_phase(conn, run_id, "VERIFYING")
        post_checkpoint = conn.execute("PRAGMA wal_checkpoint(TRUNCATE);").fetchone()
        if int(post_checkpoint[0] if post_checkpoint else 1):
            raise SQLiteMaintenanceRefused(
                "post_reclamation_checkpoint_busy",
                "Reclamation completed but its WAL checkpoint could not be finalized.",
            )
        _quick_check(conn)
        after = _profile_from_connection(conn, path)
        reclaimed_pages = max(
            0,
            int(before.get("freelist_pages") or 0)
            - int(after.get("freelist_pages") or 0),
        )
        _finish_receipt(
            conn,
            run_id,
            status="COMPLETED",
            phase="COMPLETED",
            error_type="none",
            reclaimed_pages=reclaimed_pages,
            after=after,
        )
        emit("completed", {"run_id": run_id, "reclaimed_pages": reclaimed_pages})
        return {
            "status": "COMPLETED",
            "applied": True,
            "run_id": run_id,
            "operation": mode,
            "reclaimed_pages": reclaimed_pages,
            "before": before,
            "after": after,
        }
    except KeyboardInterrupt:
        error_code = "operator_interrupt"
        if conn is not None and receipt_started:
            _finish_receipt(
                conn,
                run_id,
                status="FAILED",
                phase="INTERRUPTED",
                error_type=error_code,
            )
        return {
            "status": "INTERRUPTED",
            "applied": False,
            "run_id": run_id,
            "error_type": error_code,
            "before": before,
        }
    except Exception as exc:
        error_code = exc.code if isinstance(exc, SQLiteMaintenanceRefused) else type(exc).__name__
        receipt_write_error: dict[str, str] | None = None
        if conn is not None and receipt_started:
            try:
                _finish_receipt(
                    conn,
                    run_id,
                    status="FAILED",
                    phase="REFUSED" if isinstance(exc, SQLiteMaintenanceRefused) else "FAILED",
                    error_type=error_code,
                )
            except Exception as receipt_exc:
                receipt_write_error = {
                    "type": type(receipt_exc).__name__,
                    "message": str(receipt_exc),
                }
                print(
                    "Warning: Failed to persist SQLite maintenance failure receipt: "
                    f"{type(receipt_exc).__name__}: {receipt_exc}"
                )
        result = {
            "status": "REFUSED" if isinstance(exc, SQLiteMaintenanceRefused) else "FAILED",
            "applied": False,
            "run_id": run_id,
            "error_type": error_code,
            "message": str(exc),
            "before": before,
        }
        if receipt_write_error is not None:
            result["receipt_write_error"] = receipt_write_error
        return result
    finally:
        if conn is not None:
            conn.close()
        lock.release()
