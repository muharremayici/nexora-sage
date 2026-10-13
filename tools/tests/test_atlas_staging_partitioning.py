import hashlib
import json
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

import pytest

from tools.core.artifact_store import ArtifactStore, CorruptPartitionedPayloadError
from tools.core.json_syntax import JSONSelectionLimitError, select_json_object_path
from tools.core.db import SQLiteManager


def _sqlite_store(root: Path) -> ArtifactStore:
    store = ArtifactStore(raw_dir=root)
    store.use_sqlite = True
    store.backend = "hybrid_sqlite"
    store.db_manager = SQLiteManager(root / "codemaps.db")
    store._schema_initialized = False
    store._ensure_schema()
    return store


@pytest.mark.parametrize("chunk", [1, 2, 7, 32])
def test_primary_file_selector_handles_escaped_exact_keys_and_chunk_boundaries(chunk):
    import io
    project, file = 'MÇ"🚀', 'src/a\\b".tsx'
    expected = {"hash": "h", "nested": [None, True, False, -1.25e3, {"text": "\\\"\n🚀"}]}
    payload = {"padding": ["{} files MAIN", {"files": {file: {"wrong": True}}}],
               project: {"files": {file: expected, "other": {"large": "\\" * 100}}}}
    text = json.dumps(payload, ensure_ascii=True)
    assert select_json_object_path(io.StringIO(text), (project, "files", file),
        chunk_size=chunk, max_value_bytes=4096) == expected


@pytest.mark.parametrize("text", [
    '{"MAIN":{"files":{"a.ts":{}}},"MAIN":{}}',
    '{"MAIN":{"files":{"a.ts":{}},"files":{}}}',
    '{"MAIN":{"files":{"a.ts":{},"a.ts":{}}}}',
    '{"MAIN":{"files":{"a.ts":{"x":1,"x":2}}}}',
    '{"MAIN":{"files":{"a.ts":{}}},"tail":"\\q"}',
    '{"MAIN":{"files":{"a.ts":{}}},"tail":"\\u00zz"}',
    '{"MAIN":{"files":{"a.ts":{}}},"tail":"bad\ntext"}',
    '{"MAIN":{"files":{"a.ts":{}}},"tail":[1,]}',
    '{"MAIN":{"files":{"a.ts":{}}},"tail":01}',
    '{"MAIN":{"files":{"a.ts":{}}},"tail":NaN}',
    '{"MAIN":{"files":{"a.ts":{}}}} trailing',
    '{"MAIN":{"files":{"a.ts":{}}}',
])
def test_primary_file_selector_rejects_ambiguous_target_or_invalid_tail(text):
    import io
    with pytest.raises(ValueError):
        select_json_object_path(io.StringIO(text), ("MAIN", "files", "a.ts"),
            chunk_size=3, max_value_bytes=4096)


@pytest.mark.parametrize("payload", [{}, {"OTHER": {"files": {"a.ts": {}}}},
    {"MAIN": {"files": ["a.ts"]}}, {"MAIN": None}])
def test_primary_file_selector_missing_path_is_not_an_empty_file_verdict(payload):
    import io
    assert select_json_object_path(io.StringIO(json.dumps(payload)), ("MAIN", "files", "a.ts"),
        chunk_size=2, max_value_bytes=64) is None


@pytest.mark.parametrize("partitioned", [False, True])
def test_primary_file_metadata_reads_only_exact_canonical_project_file(tmp_path, monkeypatch, partitioned):
    store = _sqlite_store(tmp_path)
    store.state_payload_inline_limit_bytes = 1 if partitioned else 4096
    store.state_payload_part_size_bytes = 17
    wanted = {"hash": "h", "evidence": ["🚀", True, None]}
    atlas = {"MAIN": {"files": {"a.ts": wanted}}, "OTHER": {"files": {"a.ts": {"wrong": True}}}}
    store._save_payload_to_state_table("atlas", atlas)
    monkeypatch.setattr(store, "load_raw", lambda *a, **k: pytest.fail("Whole Atlas/shadow fallback"))
    monkeypatch.setattr(store, "_load_state_payload_row", lambda *a, **k: pytest.fail("Whole object decoder"))
    assert store.load_sqlite_atlas_file_metadata("MAIN", "a.ts") == wanted
    assert store.load_sqlite_atlas_file_metadata("main", "a.ts") is None
    assert store.load_sqlite_atlas_file_metadata("MAIN", "missing.ts") is None


@pytest.mark.parametrize("damage", ["missing_part", "part_bytes", "part_length", "part_hash",
    "aggregate_bytes", "aggregate_hash", "generation", "manifest", "sequence"])
def test_primary_file_metadata_rejects_partition_damage_even_after_target(tmp_path, damage):
    store = _sqlite_store(tmp_path)
    store.state_payload_inline_limit_bytes, store.state_payload_part_size_bytes = 1, 32
    store._save_payload_to_state_table("atlas", {"MAIN": {"files": {"a.ts": {"good": True}}}, "Z": "x" * 150})
    with store.db_manager.get_connection() as conn:
        if damage == "missing_part":
            conn.execute("DELETE FROM state_payload_parts WHERE part_index = (SELECT MAX(part_index) FROM state_payload_parts)")
        elif damage.startswith("part_"):
            field, value = {"part_bytes": ("payload", b"bad"), "part_length": ("payload_bytes", 0), "part_hash": ("payload_sha", "bad")}[damage]
            conn.execute(f"UPDATE state_payload_parts SET {field}=? WHERE part_index=1", (value,))
        elif damage in {"aggregate_bytes", "aggregate_hash", "generation"}:
            field, value = {"aggregate_bytes": ("payload_bytes", 1), "aggregate_hash": ("payload_sha", "0" * 64), "generation": ("generation_id", "other")}[damage]
            conn.execute(f"UPDATE state_payloads SET {field}=? WHERE name='atlas'", (value,))
        elif damage == "manifest":
            conn.execute("UPDATE state_payloads SET payload='{}' WHERE name='atlas'")
        else:
            conn.execute("UPDATE state_payload_parts SET part_index=999 WHERE part_index=1")
    with pytest.raises(CorruptPartitionedPayloadError):
        store.load_sqlite_atlas_file_metadata("MAIN", "a.ts")


@pytest.mark.parametrize("damage", ["legacy_hash", "missing_row", "inline_hash", "inline_length", "oversize_record", "oversize_part", "invalid_utf8"])
def test_primary_file_metadata_unknown_corruption_and_limits(tmp_path, damage):
    store = _sqlite_store(tmp_path)
    store._save_payload_to_state_table("atlas", {"MAIN": {"files": {"a.ts": {"text": "x" * 100}}}})
    if damage in {"legacy_hash", "missing_row", "inline_hash", "inline_length"}:
        with store.db_manager.get_connection() as conn:
            if damage == "missing_row":
                conn.execute("DELETE FROM state_payloads WHERE name='atlas'")
            else:
                field, value = {"legacy_hash": ("payload_sha", ""), "inline_hash": ("payload_sha", "0" * 64), "inline_length": ("payload_bytes", 1)}[damage]
                conn.execute(f"UPDATE state_payloads SET {field}=? WHERE name='atlas'", (value,))
        if damage in {"legacy_hash", "missing_row"}:
            assert store.load_sqlite_atlas_file_metadata("MAIN", "a.ts") is None
            return
        expected = CorruptPartitionedPayloadError
    elif damage == "oversize_record":
        store.atlas_staging_file_aggregate_limit_bytes = 32
        expected = JSONSelectionLimitError
    else:
        store.state_payload_inline_limit_bytes, store.state_payload_part_size_bytes = 1, 1024
        store._save_payload_to_state_table("atlas", {"MAIN": {"files": {"a.ts": {}}}})
        if damage == "oversize_part":
            store.state_payload_part_size_bytes = 1
            expected = JSONSelectionLimitError
        else:
            data = b'{"MAIN":{"files":{"a.ts":{}}},"Z":"\xff"}'
            digest = hashlib.sha256(data).hexdigest()
            with store.db_manager.get_connection() as conn:
                row = conn.execute("SELECT * FROM state_payloads WHERE name='atlas'").fetchone()
                manifest = json.loads(row["payload"])
                manifest["__sage_partitioned_payload__"].update(payload_bytes=len(data), payload_sha256=digest)
                conn.execute("UPDATE state_payloads SET payload=?, payload_bytes=?, payload_sha=? WHERE name='atlas'", (json.dumps(manifest), len(data), digest))
                conn.execute("UPDATE state_payload_parts SET payload=?, payload_bytes=?, payload_sha=?", (data, len(data), digest))
            expected = UnicodeDecodeError
    with pytest.raises(expected):
        store.load_sqlite_atlas_file_metadata("MAIN", "a.ts")


def test_primary_file_metadata_memory_is_independent_of_unselected_string(tmp_path, monkeypatch):
    import tracemalloc
    store = _sqlite_store(tmp_path)
    store.state_payload_inline_limit_bytes, store.state_payload_part_size_bytes = 64, 16384
    store._save_payload_to_state_table("atlas", {"MAIN": {"files": {"a.ts": {"wanted": True}}}, "Z": "x" * (10 * 1024 * 1024)})
    monkeypatch.setattr(store, "_load_state_payload_row", lambda *a, **k: pytest.fail("Whole Atlas decoder"))
    tracemalloc.start()
    try:
        assert store.load_sqlite_atlas_file_metadata("MAIN", "a.ts") == {"wanted": True}
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 2 * 1024 * 1024, f"Unselected 10 MiB string was materialized: peak={peak}"


def test_primary_file_selector_matches_stdlib_on_generated_json_values():
    import io
    import random
    rng = random.Random(142)
    strings = ['a', '', 'files', '"\\\r\n\t', '🚀ç', '\u0000', '{}[],:']
    def value(depth=0):
        leaves = [None, True, False, rng.randint(-10000, 10000), rng.random(), rng.choice(strings)]
        if depth > 2:
            return rng.choice(leaves)
        return rng.choice(leaves + [[value(depth + 1), value(depth + 1)], {rng.choice(strings): value(depth + 1)}])
    for index in range(100):
        expected = {"value": value()}
        body = {"before": value(), "MAIN": {"files": {"a.ts": expected, "other": value()}}, "after": value()}
        text = json.dumps(body, ensure_ascii=bool(index % 2), indent=2 if index % 3 else None)
        actual = select_json_object_path(io.StringIO(text), ("MAIN", "files", "a.ts"),
            chunk_size=(1, 5, 64)[index % 3], max_value_bytes=4096)
        assert actual == json.loads(text)["MAIN"]["files"]["a.ts"]


@pytest.mark.parametrize("text", ['{"MAIN":{"files":{"a.ts":{}}},"MAIN":{}}',
    '{"MAIN":{"files":{"a.ts":{}}},"tail":[1,]}'])
def test_primary_file_metadata_valid_checksum_does_not_certify_json_meaning(tmp_path, text):
    store = _sqlite_store(tmp_path)
    store._save_payload_to_state_table("atlas", {})
    encoded = text.encode("utf-8")
    with store.db_manager.get_connection() as conn:
        conn.execute("UPDATE state_payloads SET payload=?, payload_bytes=?, payload_sha=? WHERE name='atlas'",
            (text, len(encoded), hashlib.sha256(encoded).hexdigest()))
    with pytest.raises(ValueError):
        store.load_sqlite_atlas_file_metadata("MAIN", "a.ts")


def test_primary_file_metadata_read_snapshot_cannot_mix_replaced_generations(tmp_path, monkeypatch):
    from contextlib import contextmanager
    store = _sqlite_store(tmp_path)
    store.state_payload_inline_limit_bytes, store.state_payload_part_size_bytes = 1, 32
    store._save_payload_to_state_table("atlas", {"MAIN": {"files": {"a.ts": {"version": "old"}}}})
    with store.db_manager.get_connection() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
    native = store._state_payload_stream
    @contextmanager
    def replace_during_read(conn, row):
        store._save_payload_to_state_table("atlas", {"MAIN": {"files": {"a.ts": {"version": "new"}}}})
        with native(conn, row) as stream:
            yield stream
    monkeypatch.setattr(store, "_state_payload_stream", replace_during_read)
    assert store.load_sqlite_atlas_file_metadata("MAIN", "a.ts") == {"version": "old"}
    monkeypatch.setattr(store, "_state_payload_stream", native)
    assert store.load_sqlite_atlas_file_metadata("MAIN", "a.ts") == {"version": "new"}


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
