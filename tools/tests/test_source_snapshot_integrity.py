from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from tools.core.artifact_store import ArtifactStore
from tools.core.db import SQLiteManager
from tools.core import source_evidence, source_snapshot_reader
from tools.core.source_snapshot_integrity import snapshot_content_status, source_text_hash


def persist(tmp_path, content, *, expected=None, algorithm="sha256"):
    root = tmp_path / "repo"
    root.mkdir()
    source = root / "a.ts"
    source.write_bytes(content.encode("utf-8"))
    expected = content if expected is None else expected
    digest = hashlib.new(algorithm, expected.encode("utf-8")).hexdigest()
    atlas = {"MAIN": {"root_path": str(root), "project_type": "typescript", "files": {
        "a.ts": {"language": "typescript", "hash": digest, "size": source.stat().st_size, "symbols": []}},
        "dependencies": {}, "symbols": []}}
    store = ArtifactStore(raw_dir=tmp_path)
    store.use_sqlite = True
    store.backend = "hybrid_sqlite"
    store.db_manager = SQLiteManager(tmp_path / "codemaps.db")
    store._schema_initialized = False
    store._ensure_schema()
    store._save_atlas_to_sqlite(atlas)
    return store, source, atlas


def row_for(store):
    with store.db_manager.get_connection() as conn:
        return dict(conn.execute("SELECT * FROM source_snapshots WHERE project_key='MAIN' AND rel_path='a.ts'").fetchone())


def wire_reader(monkeypatch, tmp_path):
    monkeypatch.setattr(source_snapshot_reader, "DB_PATH", tmp_path / "codemaps.db")
    monkeypatch.setattr(source_snapshot_reader, "record_honesty_event", lambda **kw: None)
    monkeypatch.setattr(source_evidence, "record_honesty_event", lambda **kw: None)


@pytest.mark.parametrize("algorithm", ["md5", "sha256"])
@pytest.mark.parametrize("content", ["", "\ufeffexport const a = 1;\r\n", "export const a = 'ç';\n"])
def test_writer_preserves_exact_text_and_reader_accepts_verified_empty_content(tmp_path, monkeypatch, algorithm, content):
    store, source, atlas = persist(tmp_path, content, algorithm=algorithm)
    row = row_for(store)
    assert row["content"] == content
    assert row["status"] == "ok"
    assert row["content_hash"] == hashlib.new(algorithm, content.encode("utf-8")).hexdigest()
    source.unlink()  # A valid immutable row remains usable without live source.
    wire_reader(monkeypatch, tmp_path)
    assert source_snapshot_reader.load_source_text("MAIN", "a.ts", allow_live_fallback=False) == content


def test_writer_does_not_copy_old_atlas_hash_onto_new_content(tmp_path, monkeypatch):
    store, source, atlas = persist(tmp_path, "export const a = 2;", expected="export const a = 1;")
    row = row_for(store)
    assert row["status"] == "content_mismatch"
    assert row["content_hash"] == hashlib.sha256(row["content"].encode()).hexdigest()
    assert row["content_hash"] != atlas["MAIN"]["files"]["a.ts"]["hash"]
    wire_reader(monkeypatch, tmp_path)
    assert source_snapshot_reader.load_source_text("MAIN", "a.ts", allow_live_fallback=False) is None


@pytest.mark.parametrize("mutation", ["content", "atlas", "missing_identity"])
def test_legacy_bad_rows_are_rejected_by_reader_and_mcp(tmp_path, monkeypatch, mutation):
    from tools.mcp import server

    store, source, atlas = persist(tmp_path, "export const a = 1;")
    wire_reader(monkeypatch, tmp_path)
    row = row_for(store)
    context = {"file_id": row["file_id"], "atlas_node": "MAIN::a.ts", "atlas_relative_path": "a.ts"}
    monkeypatch.setattr(server, "_sqlite_file_context_from_raw", lambda *a: ("MAIN::a.ts", context))
    assert server._source_snapshot_content_for_ref(tmp_path, "MAIN::a.ts") == source.read_text()
    assert server._source_grounding_status(tmp_path, context, source)["drift_check_status"] == "match"
    with store.db_manager.get_connection() as conn:
        if mutation == "content":
            conn.execute("UPDATE source_snapshots SET content = 'wrong old snapshot';")
        elif mutation == "atlas":
            conn.execute("UPDATE files SET hash = ?;", ("a" * 64,))
        else:
            conn.execute("UPDATE source_snapshots SET content_hash = '';")
    assert source_snapshot_reader.load_source_text("MAIN", "a.ts", allow_live_fallback=False) is None
    assert server._source_snapshot_content_for_ref(tmp_path, "MAIN::a.ts") == ""
    status = server._source_grounding_status(tmp_path, context, source)
    assert status["source_snapshot_status"] != "ok"
    assert status["drift_check_status"] == "not_available"
    assert status["target_source_snippets"] == []


def test_mcp_live_check_does_not_normalize_changed_newlines(tmp_path):
    from tools.mcp import server

    store, source, _ = persist(tmp_path, "export const a = 1;\n")
    row = row_for(store)
    context = {"file_id": row["file_id"], "atlas_node": "MAIN::a.ts", "atlas_relative_path": "a.ts"}
    source.write_bytes(b"export const a = 1;\r\n")
    status = server._source_grounding_status(tmp_path, context, source)
    assert status["source_snapshot_status"] == "ok"
    assert status["drift_check_status"] == "mismatch"


def test_matching_metadata_does_not_authorize_changed_live_source(tmp_path, monkeypatch):
    source = tmp_path / "a.ts"
    old = "export const a = 1;"
    source.write_text(old)
    stat = source.stat()
    entry = {"hash": hashlib.sha256(old.encode()).hexdigest(), "size": stat.st_size, "mtime": stat.st_mtime}
    source.write_text("export const a = 2;")
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    monkeypatch.setattr(source_evidence, "load_source_text", lambda *a, **kw: None)
    monkeypatch.setattr(source_evidence, "record_honesty_event", lambda **kw: None)
    assert source_evidence.read_atlas_bound_source(component="test", project="MAIN", project_root=tmp_path,
        rel_path="a.ts", atlas_entry=entry, reason="same size and mtime") is None


def test_snapshot_from_different_atlas_does_not_satisfy_callers_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(source_evidence, "load_source_text", lambda *a, **kw: "new snapshot")
    monkeypatch.setattr(source_evidence, "record_honesty_event", lambda **kw: None)
    assert source_evidence.read_atlas_bound_source(component="test", project="MAIN", project_root=tmp_path,
        rel_path="a.ts", atlas_entry={"hash": hashlib.sha256(b"old snapshot").hexdigest()}, reason="stale caller") is None


@pytest.mark.parametrize("state", ["missing", "too_large", "error"])
def test_writer_failure_never_copies_atlas_identity_without_content(tmp_path, monkeypatch, state):
    from tools.core import artifact_store, honesty_telemetry

    store, source, atlas = persist(tmp_path, "export const a = 1;")
    monkeypatch.setattr(honesty_telemetry, "record_honesty_event", lambda **kw: None)
    if state == "missing":
        source.unlink()
    elif state == "too_large":
        monkeypatch.setitem(artifact_store.DYNAMIC_CONFIG, "source_snapshot_max_bytes", 1)
    else:
        original_open = Path.open
        def unavailable(path, *args, **kwargs):
            if path == source:
                raise PermissionError("fixture read denied")
            return original_open(path, *args, **kwargs)
        monkeypatch.setattr(Path, "open", unavailable)
    store._save_atlas_to_sqlite(atlas)
    row = row_for(store)
    assert row["status"] == state
    assert row["content_hash"] == ""


@pytest.mark.parametrize("tamper", [False, True])
def test_symbol_span_validator_cannot_validate_lines_against_unbound_text(tmp_path, monkeypatch, tamper):
    from tools import validate_symbol_span_integrity as validator

    store, _, _ = persist(tmp_path, "export const a = 1;\n")
    row = row_for(store)
    with store.db_manager.get_connection() as conn:
        conn.execute("INSERT INTO symbols (file_id, name, type, line, char, end_line, source_lines, export_status) "
                     "VALUES (?, 'a', 'variable', 1, 0, 1, 'L1-L1', 'exported');", (row["file_id"],))
        conn.execute("INSERT INTO symbols (file_id, name, type, line, char, end_line, source_lines, export_status) "
                     "VALUES (?, 'b', 'variable', 1, 0, 1, 'L1-L1', 'exported');", (row["file_id"],))
        if tamper:
            conn.execute("UPDATE source_snapshots SET content = 'different same-line text';")
    monkeypatch.setattr(validator, "DB_PATH", tmp_path / "codemaps.db")
    monkeypatch.setattr(validator, "save_json_atomic", lambda *a: None)
    monkeypatch.setattr(validator, "save_text_atomic", lambda *a: None)
    checked = []
    original = validator.snapshot_content_status
    def observed(*args, **kwargs):
        checked.append(args)
        return original(*args, **kwargs)
    monkeypatch.setattr(validator, "snapshot_content_status", observed)
    result = validator.validate_symbol_span_integrity()
    assert len(checked) == 1  # Hash once per file, not once per symbol.
    assert result["summary"]["status"] == ("FAIL" if tamper else "PASS")


@pytest.mark.parametrize("bad_hash", ["", "hash-fixture", "err", "a" * 40])
def test_unknown_content_identity_never_counts_as_a_match(bad_hash):
    assert source_text_hash("source", bad_hash) is None
    assert snapshot_content_status("source", bad_hash, bad_hash) == "identity_unavailable"


@pytest.mark.parametrize("tamper", [False, True])
def test_validator_checks_actual_text_not_just_equal_hash_columns(tmp_path, monkeypatch, tamper):
    from tools import validate_source_snapshot_store as validator

    store, _, atlas = persist(tmp_path, "")
    if tamper:
        with store.db_manager.get_connection() as conn:
            conn.execute("UPDATE source_snapshots SET content = 'tampered';")
    monkeypatch.setattr(validator, "DB_PATH", tmp_path / "codemaps.db")
    monkeypatch.setattr(validator, "load_atlas_data", lambda: atlas)
    monkeypatch.setattr(validator, "set_runtime_project_filter", lambda *a: None)
    monkeypatch.setattr(validator, "project_runtime_atlas", lambda value: (value, {"analyzed_projects": ["MAIN"]}))
    monkeypatch.setattr(validator, "save_json_atomic", lambda *a: None)
    monkeypatch.setattr(validator, "save_text_atomic", lambda *a: None)
    result = validator.validate_source_snapshot_store(["MAIN"])
    assert result["summary"]["status"] == ("FAIL" if tamper else "PASS")
    content_check = next(row for row in result["checks"] if row["name"] == "stored_text_matches_content_identity")
    assert content_check["passed"] is not tamper
