import hashlib
import json
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

import pytest

from tools.core.artifact_store import ArtifactStore
from tools.core.db import SQLiteManager


def _sqlite_store(root: Path) -> ArtifactStore:
    store = ArtifactStore(raw_dir=root)
    store.use_sqlite = True
    store.backend = "hybrid_sqlite"
    store.db_manager = SQLiteManager(root / "codemaps.db")
    store._schema_initialized = False
    store._ensure_schema()
    return store


def test_legacy_inline_staging_schema_migrates_without_losing_checkpoint() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db_path = root / "codemaps.db"
        payload = {"mtime": 1.0, "size": 10, "value": "legacy"}
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        with closing(sqlite3.connect(db_path)) as conn:
            conn.executescript(
                """
                CREATE TABLE atlas_staging_runs (
                    run_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    producer_contract TEXT NOT NULL,
                    processed_files INTEGER NOT NULL DEFAULT 0,
                    reused_files INTEGER NOT NULL DEFAULT 0,
                    skipped_oversize_files INTEGER NOT NULL DEFAULT 0,
                    error_type TEXT NOT NULL DEFAULT 'none',
                    started_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE atlas_staging_files (
                    project_key TEXT NOT NULL,
                    rel_path TEXT NOT NULL,
                    stage_kind TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    producer_contract TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    payload_sha TEXT NOT NULL,
                    source_mtime REAL,
                    size_bytes INTEGER NOT NULL,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (project_key, rel_path, stage_kind),
                    FOREIGN KEY(run_id) REFERENCES atlas_staging_runs(run_id) ON DELETE CASCADE
                );
                """
            )
            conn.execute(
                """
                INSERT INTO atlas_staging_runs (run_id, status, producer_contract)
                VALUES ('legacy-run', 'FAILED', 'atlas-staging-v1:legacy');
                """
            )
            conn.execute(
                """
                INSERT INTO atlas_staging_files (
                    project_key, rel_path, stage_kind, run_id, producer_contract,
                    payload, payload_sha, source_mtime, size_bytes
                ) VALUES ('MAIN', 'legacy.ts', 'atlas_file', 'legacy-run',
                          'atlas-staging-v1:legacy', ?, ?, 1.0, 10);
                """,
                (serialized, hashlib.sha256(serialized.encode("utf-8")).hexdigest()),
            )
            conn.commit()

        store = ArtifactStore(raw_dir=root)
        store.use_sqlite = True
        store.backend = "hybrid_sqlite"
        store.db_manager = SQLiteManager(db_path)
        store._schema_initialized = False
        store._ensure_schema()

        assert store.load_atlas_staging_files("atlas-staging-v1:legacy")[
            ("MAIN", "legacy.ts")
        ] == payload
        with store.db_manager.get_connection() as conn:
            columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(atlas_staging_files);").fetchall()
            }
            parts_table = conn.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name = 'atlas_staging_file_parts';
                """
            ).fetchone()
        assert {"payload_bytes", "storage_mode", "generation_id", "part_count"} <= columns
        assert parts_table["name"] == "atlas_staging_file_parts"


def test_atlas_staging_keeps_inline_fast_path_without_parts() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = _sqlite_store(Path(tmp))
        store.atlas_staging_file_payload_limit_bytes = 1024
        store.atlas_staging_file_aggregate_limit_bytes = 4096
        store.state_payload_part_size_bytes = 32
        producer = "atlas-staging-v1:inline"
        run_id = store.begin_atlas_staging_run(producer)
        payload = {"mtime": 1.0, "size": 10, "value": "small"}

        result = store.save_atlas_staging_batch(
            run_id,
            producer,
            "MAIN",
            [("small.ts", payload)],
        )

        with store.db_manager.get_connection() as conn:
            row = conn.execute(
                """
                SELECT storage_mode, generation_id, part_count
                FROM atlas_staging_files
                WHERE project_key = 'MAIN' AND rel_path = 'small.ts';
                """
            ).fetchone()
            part_count = int(conn.execute(
                "SELECT COUNT(*) FROM atlas_staging_file_parts;"
            ).fetchone()[0])
        assert result == {"persisted": 1, "partitioned": 0, "skipped_oversize": 0}
        assert row["storage_mode"] == "inline_json"
        assert row["generation_id"] is None
        assert int(row["part_count"]) == 0
        assert part_count == 0
        assert store.load_atlas_staging_files(producer)[("MAIN", "small.ts")] == payload


def test_atlas_staging_partitions_oversized_file_and_reassembles_exact_bytes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = _sqlite_store(Path(tmp))
        store.atlas_staging_file_payload_limit_bytes = 64
        store.atlas_staging_file_aggregate_limit_bytes = 4096
        store.state_payload_part_size_bytes = 32
        producer = "atlas-staging-v1:partitioned"
        run_id = store.begin_atlas_staging_run(producer)
        payload = {"mtime": 1.0, "size": 10, "value": "🚀ç" * 80}

        result = store.save_atlas_staging_batch(
            run_id,
            producer,
            "MAIN",
            [("dense.ts", payload)],
            stage_kind="sequencer_result",
        )

        with store.db_manager.get_connection() as conn:
            row = conn.execute(
                """
                SELECT storage_mode, generation_id, part_count, payload_bytes, payload_sha
                FROM atlas_staging_files
                WHERE project_key = 'MAIN' AND rel_path = 'dense.ts'
                  AND stage_kind = 'sequencer_result';
                """
            ).fetchone()
            parts = conn.execute(
                """
                SELECT part_index, payload, payload_bytes, payload_sha, producer_contract
                FROM atlas_staging_file_parts
                ORDER BY part_index;
                """
            ).fetchall()
        expected_bytes = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        assert result == {"persisted": 1, "partitioned": 1, "skipped_oversize": 0}
        assert row["storage_mode"] == "partitioned_json_v1"
        assert row["generation_id"]
        assert int(row["part_count"]) == len(parts) > 1
        assert int(row["payload_bytes"]) == len(expected_bytes)
        assert row["payload_sha"] == hashlib.sha256(expected_bytes).hexdigest()
        assert [int(part["part_index"]) for part in parts] == list(range(len(parts)))
        assert all(0 < int(part["payload_bytes"]) <= 32 for part in parts)
        assert all(part["producer_contract"] == producer for part in parts)
        assert b"".join(bytes(part["payload"]) for part in parts) == expected_bytes
        assert store.load_atlas_staging_files(
            producer,
            stage_kind="sequencer_result",
        )[("MAIN", "dense.ts")] == payload


def test_atlas_staging_skips_payload_above_aggregate_bound_without_partial_rows() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = _sqlite_store(Path(tmp))
        store.atlas_staging_file_payload_limit_bytes = 64
        store.atlas_staging_file_aggregate_limit_bytes = 96
        store.state_payload_part_size_bytes = 32
        producer = "atlas-staging-v1:aggregate-bound"
        run_id = store.begin_atlas_staging_run(producer)
        payload = {"mtime": 1.0, "size": 10, "value": "x" * 200}

        result = store.save_atlas_staging_batch(
            run_id,
            producer,
            "MAIN",
            [("too-large.ts", payload)],
        )

        with store.db_manager.get_connection() as conn:
            parent_count = int(conn.execute(
                "SELECT COUNT(*) FROM atlas_staging_files;"
            ).fetchone()[0])
            part_count = int(conn.execute(
                "SELECT COUNT(*) FROM atlas_staging_file_parts;"
            ).fetchone()[0])
            receipt = conn.execute(
                "SELECT processed_files, skipped_oversize_files FROM atlas_staging_runs WHERE run_id = ?;",
                (run_id,),
            ).fetchone()
        assert result == {"persisted": 0, "partitioned": 0, "skipped_oversize": 1}
        assert parent_count == 0
        assert part_count == 0
        assert int(receipt["processed_files"]) == 0
        assert int(receipt["skipped_oversize_files"]) == 1


@pytest.mark.parametrize("corruption", ["missing_part", "changed_bytes"])
def test_partitioned_atlas_staging_rejects_missing_or_corrupt_part(corruption: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = _sqlite_store(Path(tmp))
        store.atlas_staging_file_payload_limit_bytes = 64
        store.atlas_staging_file_aggregate_limit_bytes = 4096
        store.state_payload_part_size_bytes = 32
        producer = "atlas-staging-v1:corrupt-part"
        run_id = store.begin_atlas_staging_run(producer)
        payload = {"mtime": 1.0, "size": 10, "value": "x" * 200}
        store.save_atlas_staging_batch(run_id, producer, "MAIN", [("dense.ts", payload)])
        with store.db_manager.get_connection() as conn:
            if corruption == "missing_part":
                conn.execute(
                    """
                    DELETE FROM atlas_staging_file_parts
                    WHERE project_key = 'MAIN' AND rel_path = 'dense.ts' AND part_index = 1;
                    """
                )
            else:
                conn.execute(
                    """
                    UPDATE atlas_staging_file_parts
                    SET payload = ?
                    WHERE project_key = 'MAIN' AND rel_path = 'dense.ts' AND part_index = 0;
                    """,
                    (b"changed-without-matching-hash",),
                )

        assert store.load_atlas_staging_files(producer) == {}


def test_partitioned_staging_replacement_is_atomic_and_terminal_cleanup_removes_parts() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = _sqlite_store(Path(tmp))
        store.atlas_staging_file_payload_limit_bytes = 64
        store.atlas_staging_file_aggregate_limit_bytes = 4096
        store.state_payload_part_size_bytes = 32
        producer = "atlas-staging-v1:atomic-parts"
        first_run = store.begin_atlas_staging_run(producer)
        original = {"mtime": 1.0, "size": 10, "value": "a" * 200}
        replacement = {"mtime": 2.0, "size": 10, "value": "b" * 200}
        store.save_atlas_staging_batch(first_run, producer, "MAIN", [("dense.ts", original)])

        replacement_run = store.begin_atlas_staging_run(producer)
        with store.db_manager.get_connection() as conn:
            conn.executescript(
                """
                CREATE TRIGGER fail_second_atlas_staging_part
                BEFORE INSERT ON atlas_staging_file_parts
                WHEN NEW.part_index = 1
                BEGIN
                    SELECT RAISE(ABORT, 'forced partition interruption');
                END;
                """
            )
        with pytest.raises(sqlite3.IntegrityError, match="forced partition interruption"):
            store.save_atlas_staging_batch(
                replacement_run,
                producer,
                "MAIN",
                [("dense.ts", replacement)],
            )

        assert store.load_atlas_staging_files(producer)[("MAIN", "dense.ts")] == original
        with store.db_manager.get_connection() as conn:
            generations = conn.execute(
                """
                SELECT generation_id, part_count
                FROM atlas_staging_files
                WHERE project_key = 'MAIN' AND rel_path = 'dense.ts';
                """
            ).fetchall()
            part_generations = conn.execute(
                "SELECT DISTINCT generation_id FROM atlas_staging_file_parts;"
            ).fetchall()
            conn.execute("DROP TRIGGER fail_second_atlas_staging_part;")
        assert len(generations) == 1
        assert len(part_generations) == 1
        assert generations[0]["generation_id"] == part_generations[0]["generation_id"]

        store.finish_atlas_staging_run(replacement_run, producer, status="COMPLETED")
        with store.db_manager.get_connection() as conn:
            assert int(conn.execute("SELECT COUNT(*) FROM atlas_staging_files;").fetchone()[0]) == 0
            assert int(conn.execute("SELECT COUNT(*) FROM atlas_staging_file_parts;").fetchone()[0]) == 0
