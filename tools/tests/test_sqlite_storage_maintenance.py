import sqlite3
from collections import namedtuple
from pathlib import Path

import pytest

from tools.core.advisory_file_lock import AdvisoryFileLock
from tools.core.db import SQLiteManager
from tools.core import sqlite_storage_maintenance as maintenance


def _prepare_database(root: Path, *, incremental: bool = False) -> Path:
    database = root / "codemaps.db"
    if incremental:
        with sqlite3.connect(database) as conn:
            conn.execute("PRAGMA auto_vacuum=INCREMENTAL;")
            assert int(conn.execute("PRAGMA auto_vacuum;").fetchone()[0]) == 2

    SQLiteManager(database).initialize_schema()
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE maintenance_fixture (id INTEGER PRIMARY KEY, payload BLOB);")
        conn.execute(
            "INSERT INTO maintenance_fixture (id, payload) VALUES (1, ?);",
            (b"keeper",),
        )
        conn.executemany(
            "INSERT INTO maintenance_fixture (id, payload) VALUES (?, ?);",
            [(index, bytes([index % 251]) * 8192) for index in range(2, 258)],
        )
        conn.execute("DELETE FROM maintenance_fixture WHERE id > 1;")
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    assert maintenance.inspect_sqlite_storage(database)["freelist_pages"] > 0
    return database


def _run(database: Path, lock: Path, **overrides):
    options = {
        "lock_path": lock,
        "apply": True,
        "confirmed": True,
        "max_pages": 8,
        "full_vacuum_free_space_percent": 100,
        "timeout_seconds": 2,
        "progress_interval_seconds": 1,
    }
    options.update(overrides)
    return maintenance.run_sqlite_storage_maintenance(database, **options)


def test_read_only_status_does_not_create_missing_database(tmp_path: Path) -> None:
    database = tmp_path / "missing.db"

    profile = maintenance.inspect_sqlite_storage(database)
    result = maintenance.run_sqlite_storage_maintenance(
        database,
        lock_path=tmp_path / ".pipeline_run.lock",
    )

    assert profile["status"] == "MISSING"
    assert result["status"] == "OBSERVED"
    assert result["before"]["status"] == "MISSING"
    assert result["applied"] is False
    assert not database.exists()


def test_storage_profile_separates_physical_freelist_and_estimated_live_bytes(
    tmp_path: Path,
) -> None:
    database = _prepare_database(tmp_path)

    profile = maintenance.inspect_sqlite_storage(database)

    assert profile["status"] == "OBSERVED"
    assert profile["auto_vacuum"] == "none"
    assert profile["reclamation_path"] == "offline_full_vacuum"
    assert profile["freelist_pages"] > 0
    assert profile["freelist_bytes"] == profile["freelist_pages"] * profile["page_size_bytes"]
    assert profile["estimated_live_bytes"] == (
        profile["page_count"] - profile["freelist_pages"]
    ) * profile["page_size_bytes"]
    assert profile["database_bytes"] >= profile["allocated_bytes"]
    assert "not exact logical payload size" in profile["claim_boundary"]


def test_mutation_requires_confirmation_and_creates_no_receipt(tmp_path: Path) -> None:
    database = _prepare_database(tmp_path)
    before = maintenance.inspect_sqlite_storage(database)

    result = maintenance.run_sqlite_storage_maintenance(
        database,
        lock_path=tmp_path / ".pipeline_run.lock",
        apply=True,
        confirmed=False,
    )

    after = maintenance.inspect_sqlite_storage(database)
    assert result["status"] == "REFUSED_CONFIRMATION_REQUIRED"
    assert result["applied"] is False
    assert after["freelist_pages"] == before["freelist_pages"]
    assert after["latest_maintenance_receipt"] is None


def test_shared_pipeline_lock_blocks_maintenance_without_database_mutation(
    tmp_path: Path,
) -> None:
    database = _prepare_database(tmp_path)
    lock_path = tmp_path / ".pipeline_run.lock"
    owner = AdvisoryFileLock(lock_path)
    assert owner.acquire()
    before = maintenance.inspect_sqlite_storage(database)
    try:
        result = _run(database, lock_path)
    finally:
        owner.release()

    after = maintenance.inspect_sqlite_storage(database)
    assert result["status"] == "BLOCKED_ACTIVE_SAGE_OPERATION"
    assert result["applied"] is False
    assert after["freelist_pages"] == before["freelist_pages"]
    assert after["latest_maintenance_receipt"] is None


def test_full_vacuum_refuses_insufficient_disk_and_records_reason(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    database = _prepare_database(tmp_path)
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(
        maintenance.shutil,
        "disk_usage",
        lambda _path: usage(total=1, used=1, free=0),
    )

    result = _run(
        database,
        tmp_path / ".pipeline_run.lock",
        full_vacuum_free_space_percent=200,
    )
    profile = maintenance.inspect_sqlite_storage(database)

    assert result["status"] == "REFUSED"
    assert result["error_type"] == "insufficient_free_disk"
    assert result["applied"] is False
    assert profile["latest_maintenance_receipt"]["status"] == "FAILED"
    assert profile["latest_maintenance_receipt"]["phase"] == "REFUSED"
    with sqlite3.connect(database) as conn:
        assert conn.execute(
            "SELECT payload FROM maintenance_fixture WHERE id = 1;"
        ).fetchone()[0] == b"keeper"


def test_offline_full_vacuum_reclaims_pages_and_preserves_integrity(
    tmp_path: Path,
) -> None:
    database = _prepare_database(tmp_path)
    before = maintenance.inspect_sqlite_storage(database)
    phases: list[str] = []

    result = _run(
        database,
        tmp_path / ".pipeline_run.lock",
        progress=lambda phase, _details: phases.append(phase),
    )
    after = maintenance.inspect_sqlite_storage(database)

    assert result["status"] == "COMPLETED"
    assert result["operation"] == "offline_full_vacuum"
    assert result["applied"] is True
    assert result["reclaimed_pages"] > 0
    assert after["database_bytes"] < before["database_bytes"]
    assert after["freelist_pages"] < before["freelist_pages"]
    assert after["latest_maintenance_receipt"]["status"] == "COMPLETED"
    assert {"preflight", "reclaiming", "completed"} <= set(phases)
    with sqlite3.connect(database) as conn:
        assert conn.execute("PRAGMA quick_check;").fetchone()[0] == "ok"
        assert conn.execute(
            "SELECT payload FROM maintenance_fixture WHERE id = 1;"
        ).fetchone()[0] == b"keeper"


def test_reclamation_failure_preserves_data_and_leaves_terminal_receipt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    database = _prepare_database(tmp_path)

    def fail_reclamation(*_args, **_kwargs):
        raise sqlite3.OperationalError("forced maintenance interruption")

    monkeypatch.setattr(maintenance, "_execute_reclamation", fail_reclamation)
    result = _run(database, tmp_path / ".pipeline_run.lock")
    profile = maintenance.inspect_sqlite_storage(database)

    assert result["status"] == "FAILED"
    assert result["error_type"] == "OperationalError"
    assert profile["latest_maintenance_receipt"]["status"] == "FAILED"
    assert profile["latest_maintenance_receipt"]["phase"] == "FAILED"
    with sqlite3.connect(database) as conn:
        assert conn.execute("PRAGMA quick_check;").fetchone()[0] == "ok"
        assert conn.execute(
            "SELECT payload FROM maintenance_fixture WHERE id = 1;"
        ).fetchone()[0] == b"keeper"


def test_failure_receipt_write_error_is_explicit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database = _prepare_database(tmp_path)

    def fail_reclamation(*_args, **_kwargs):
        raise sqlite3.OperationalError("forced maintenance interruption")

    def fail_receipt(*_args, **_kwargs):
        raise RuntimeError("forced receipt failure")

    monkeypatch.setattr(maintenance, "_execute_reclamation", fail_reclamation)
    monkeypatch.setattr(maintenance, "_finish_receipt", fail_receipt)

    result = _run(database, tmp_path / ".pipeline_run.lock")
    captured = capsys.readouterr()

    assert result["status"] == "FAILED"
    assert result["error_type"] == "OperationalError"
    assert result["receipt_write_error"] == {
        "type": "RuntimeError",
        "message": "forced receipt failure",
    }
    assert "Failed to persist SQLite maintenance failure receipt" in captured.out


def test_stale_in_progress_receipt_is_reconciled_on_next_run(tmp_path: Path) -> None:
    database = _prepare_database(tmp_path)
    stale_run_id = "sqlite-maintenance-hard-interrupted"
    with sqlite3.connect(database) as conn:
        conn.execute(
            """
            INSERT INTO sage_sqlite_maintenance_runs (
                run_id, operation, status, phase, database_identity,
                requested_pages, reclaimed_pages, before_profile, error_type
            ) VALUES (?, 'offline_full_vacuum', 'IN_PROGRESS', 'RECLAIMING',
                      'fixture-database', 1, 0, '{}', 'none');
            """,
            (stale_run_id,),
        )
        conn.commit()

    result = _run(database, tmp_path / ".pipeline_run.lock")

    assert result["status"] == "COMPLETED"
    with sqlite3.connect(database) as conn:
        stale = conn.execute(
            """
            SELECT status, phase, error_type, finished_at
            FROM sage_sqlite_maintenance_runs
            WHERE run_id = ?;
            """,
            (stale_run_id,),
        ).fetchone()
    assert stale[:3] == ("FAILED", "INTERRUPTED", "superseded_interrupted_run")
    assert stale[3]

def test_incremental_database_uses_bounded_reclamation_path(tmp_path: Path) -> None:
    database = _prepare_database(tmp_path, incremental=True)
    before = maintenance.inspect_sqlite_storage(database)

    result = _run(
        database,
        tmp_path / ".pipeline_run.lock",
        max_pages=8,
        full_vacuum_free_space_percent=10_000,
    )
    after = maintenance.inspect_sqlite_storage(database)

    assert before["auto_vacuum"] == "incremental"
    assert before["reclamation_path"] == "bounded_incremental_vacuum"
    assert result["status"] == "COMPLETED"
    assert result["operation"] == "bounded_incremental_vacuum"
    assert 0 < result["reclaimed_pages"] <= 8
    assert after["freelist_pages"] < before["freelist_pages"]
