"""Class method search candidates remain separate from declaration/graph truth."""
from __future__ import annotations

import copy
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tools.core.artifact_store import ArtifactStore
from tools.mcp import server


def _method(name="run", line=2, end_line=3, static=False):
    return {"name": name, "kind": "method", "line": line, "end_line": end_line, "static": static}


def _class(name="Worker", members=None):
    return {"name": name, "type": "Class", "line": 1, "end_line": 20,
            "member_details": [_method()] if members is None else members}


def _atlas(symbols=None):
    return {"MAIN": {"root_path": ".", "files": {
        "worker.ts": {"hash": "fixture", "language": "typescript",
                      "symbols": [_class()] if symbols is None else symbols}}}}


@pytest.fixture
def store(tmp_path):
    result = ArtifactStore(raw_dir=tmp_path)
    result.use_sqlite = True
    result.backend = "hybrid_sqlite"
    result._ensure_schema()
    return result


def _search(monkeypatch, store, query="run", project="MAIN", format="json"):
    monkeypatch.setattr(server, "_raw_dir_for_target", lambda target: store._raw_dir)
    return server.search_symbols(query, project=project, target_root=str(store._raw_dir), format=format)


def test_real_parser_to_public_method_search(monkeypatch, store, tmp_path):
    from tools.engines.generate_atlas import _normalize_polyglot_symbols

    node = shutil.which("node")
    assert node, "Node is required for the recorded Class method producer contract"
    root = Path(__file__).resolve().parents[2]
    source = "export class Worker {\n  static run() {\n    return 1;\n  }\n}\n"
    path = tmp_path / "worker.ts"
    path.write_text(source, encoding="utf-8", newline="")
    parsed = subprocess.run([node, str(root / "tools/engines/ast_sequencer.cjs"), str(path)],
                            cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert parsed.returncode == 0, parsed.stderr
    symbols = _normalize_polyglot_symbols(json.loads(parsed.stdout), source, language="typescript")
    parent = next(row for row in symbols if row["type"] == "Class")
    member = next(row for row in parent["member_details"] if row["name"] == "run")
    assert member["kind"] == "method" and member["static"] is True
    store._save_atlas_to_sqlite(_atlas(symbols))
    output = _search(monkeypatch, store, "Worker.run")
    assert output.startswith("["), output
    row = json.loads(output)[0]
    assert row["name"] == "Worker.run"
    assert row["type"] == "ClassMethod" and row["declaring_symbol"] == "Worker"
    assert row["line"] == member["line"] and row["end_line"] == member["end_line"]
    assert row["is_static"] is True
    assert "char" not in row and "dependencies" not in row
    assert row["atlas_node"] == "MAIN::worker.ts"
    assert row["class_method_search"]["status"] == "available"
    with store.db_manager.get_connection() as conn:
        assert [row["type"] for row in conn.execute("SELECT type FROM symbols")] == [row["type"] for row in symbols]

def test_collisions_overloads_static_and_project_scope(monkeypatch, store):
    atlas = _atlas([_class(members=[_method(line=2), _method(line=5, end_line=5, static=True)]),
                    _class("Other", [_method(line=8, end_line=8)])])
    atlas["COMPANION"] = copy.deepcopy(atlas["MAIN"])
    atlas["COMPANION"]["root_path"] = "packages/companion"
    store._save_atlas_to_sqlite(atlas)
    rows = json.loads(_search(monkeypatch, store))
    assert [(row["declaring_symbol"], row["line"], row["is_static"]) for row in rows] == [
        ("Other", 8, False), ("Worker", 2, False), ("Worker", 5, True)]
    assert len({(row["name"], row["line"]) for row in rows}) == 3
    for scope, expected in [("*", 6), ("all", 6), (" ANY ", 6), (" main ", 3), ("companion", 3)]:
        selected = json.loads(_search(monkeypatch, store, project=scope))
        assert len(selected) == expected
        assert selected[0]["candidate_count"] == expected
        assert all(row["class_method_search"]["source_tree_complete"] is False for row in selected)
    companion = json.loads(_search(monkeypatch, store, project="COMPANION"))
    assert {row["repo_relative_path"] for row in companion} == {"packages/companion/worker.ts"}
    assert {row["atlas_node"] for row in companion} == {"COMPANION::worker.ts"}
    assert "unavailable" in _search(monkeypatch, store, project="missing")


@pytest.mark.parametrize("invalid", [
    {"name": "[route]"}, {"name": "'run'"}, {"name": "#private"},
    {"line": True}, {"line": 0}, {"line": "2"}, {"end_line": 21},
    {"end_line": 1}, {"static": "false"},
])
def test_malformed_or_unsupported_method_is_not_invented(monkeypatch, store, invalid):
    bad = {**_method("bad"), **invalid}
    store._save_atlas_to_sqlite(_atlas([_class(members=[_method(), bad])]))
    rows = json.loads(_search(monkeypatch, store))
    assert [row["name"] for row in rows] == ["Worker.run"]
    assert rows[0]["class_method_search"]["status"] == "partial"
    assert rows[0]["class_method_search"]["partial_files"] == 1


def test_non_class_non_method_and_global_fallback_do_not_gain_method_authority(monkeypatch, store):
    atlas = _atlas([_class(members=[_method(), {"name": "property", "kind": "property"}]),
                    {**_class("Face"), "type": "Interface"},
                    {"name": "store", "type": "Variable", "line": 1, "end_line": 5,
                     "initializer_member_evidence": {"members": [_method("action")]}}])
    atlas["MAIN"]["symbols"] = [{**_class("GlobalOnly"), "file": "worker.ts"}]
    store._save_atlas_to_sqlite(atlas)
    rows, coverage = server._find_class_method_search_matches("", "MAIN", store._raw_dir)
    assert [row["name"] for row in rows] == ["Worker.run"]
    assert coverage["status"] == "available"
    # Other primary consumers never receive method candidate rows.
    assert server._find_symbol_matches("Worker.run", "MAIN", store._raw_dir) == []


def _projection_state(store):
    with store.db_manager.get_connection() as conn:
        return {table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY 1;")]
                for table in ["files", "symbols", "symbol_search_members", "source_snapshots"]}


def test_scoped_replacement_deletion_and_full_rebuild_preserve_index_lifecycle(monkeypatch, store):
    from tools.core import artifact_store

    atlas = _atlas()
    atlas["MAIN"]["files"]["other.ts"] = copy.deepcopy(atlas["MAIN"]["files"]["worker.ts"])
    store._save_atlas_to_sqlite(atlas)
    before = _projection_state(store)
    with store.db_manager.get_connection() as conn:
        other_id = conn.execute("SELECT file_id FROM files WHERE rel_path = 'other.ts'").fetchone()[0]
        original_other = [tuple(row) for row in conn.execute(
            "SELECT * FROM symbol_search_members WHERE file_id = ?", (other_id,))]
    changed = copy.deepcopy(atlas)
    changed["MAIN"]["files"]["worker.ts"]["symbols"] = [_class(members=[_method("next")])]
    monkeypatch.setattr(artifact_store, "_source_snapshot_projection_scope", lambda: {"MAIN": {"worker.ts"}})
    profile = store._save_atlas_to_sqlite(changed)
    assert profile["atlas_relational_mode"] == "scoped"
    rows, _ = server._find_class_method_search_matches("next", "MAIN", store._raw_dir)
    assert [(row["name"], row["file"]) for row in rows] == [("Worker.next", "worker.ts")]
    with store.db_manager.get_connection() as conn:
        assert [tuple(row) for row in conn.execute(
            "SELECT * FROM symbol_search_members WHERE file_id = ?", (other_id,))] == original_other
    after = _projection_state(store)
    assert [row for row in before["source_snapshots"] if row[0] == other_id] == [
        row for row in after["source_snapshots"] if row[0] == other_id]
    # Removing the class clears methods; deleting its file cascades member rows.
    changed["MAIN"]["files"]["worker.ts"]["symbols"] = []
    store._save_atlas_to_sqlite(changed)
    assert server._find_class_method_search_matches("next", "MAIN", store._raw_dir)[0] == []
    del changed["MAIN"]["files"]["worker.ts"]
    store._save_atlas_to_sqlite(changed)
    assert len(server._find_class_method_search_matches("run", "MAIN", store._raw_dir)[0]) == 1
    monkeypatch.setattr(artifact_store, "_source_snapshot_projection_scope", lambda: None)
    changed["MAIN"]["files"] = {}
    store._save_atlas_to_sqlite(changed)
    assert _projection_state(store)["symbol_search_members"] == []


@pytest.mark.parametrize("scoped", [False, True])
def test_projection_failure_rolls_back_parent_member_and_coverage_together(monkeypatch, store, scoped):
    from tools.core import artifact_store

    atlas = _atlas()
    store._save_atlas_to_sqlite(atlas)
    before = _projection_state(store)
    original = store._project_class_method_search

    def fail(conn, file_id, file_info):
        original(conn, file_id, file_info)
        raise RuntimeError("injected member projection failure")

    monkeypatch.setattr(store, "_project_class_method_search", fail)
    monkeypatch.setattr(artifact_store, "_source_snapshot_projection_scope",
                        lambda: {"MAIN": {"worker.ts"}} if scoped else None)
    changed = _atlas([_class(members=[_method("replacement")])])
    with pytest.raises(RuntimeError, match="injected"):
        store._save_atlas_to_sqlite(changed)
    assert _projection_state(store) == before


def test_legacy_schema_and_migrated_empty_index_are_not_complete(monkeypatch, store):
    store._save_atlas_to_sqlite(_atlas())
    with store.db_manager.get_connection() as conn:
        conn.execute("DROP TABLE symbol_search_members")
        conn.execute("ALTER TABLE files DROP COLUMN class_method_search_status")
    before = store.db_manager.db_path.read_bytes()
    monkeypatch.setattr(server, "_atlas", lambda **kwargs: pytest.fail("Method query must not load Atlas"))
    rows, coverage = server._find_class_method_search_matches("run", "MAIN", store._raw_dir)
    assert rows == [] and coverage["status"] == "unavailable"
    assert store.db_manager.db_path.read_bytes() == before, "Read must not migrate the legacy schema"
    store.db_manager.initialize_schema()
    rows, coverage = server._find_class_method_search_matches("run", "MAIN", store._raw_dir)
    assert rows == [] and coverage["status"] == "partial"
    assert coverage["unprojected_files"] == 1


def test_missing_member_evidence_partial_scope_and_later_refresh(monkeypatch, store):
    atlas = _atlas()
    atlas["MAIN"]["files"]["legacy.ts"] = {"symbols": [{k: v for k, v in _class().items()
                                                      if k != "member_details"}]}
    store._save_atlas_to_sqlite(atlas)
    coverage = json.loads(_search(monkeypatch, store))[0]["class_method_search"]
    assert coverage["status"] == "partial" and coverage["partial_files"] == 1
    atlas["MAIN"]["files"]["legacy.ts"]["symbols"] = []
    store._save_atlas_to_sqlite(atlas)
    coverage = json.loads(_search(monkeypatch, store))[0]["class_method_search"]
    assert coverage["status"] == "available"
    with store.db_manager.get_connection() as conn:
        conn.execute("UPDATE files SET class_method_search_status = NULL WHERE rel_path = 'legacy.ts'")
    coverage = json.loads(_search(monkeypatch, store))[0]["class_method_search"]
    assert coverage["status"] == "partial" and coverage["unprojected_files"] == 1


def test_brief_machine_caps_method_identity_and_no_fabricated_columns(monkeypatch, store):
    atlas = _atlas([_class(members=[_method(f"run_{index}", line=index + 2, end_line=index + 2)
                                  for index in range(15)])])
    store._save_atlas_to_sqlite(atlas)
    brief = _search(monkeypatch, store, format="brief")
    assert "candidate_count: 15" in brief and "shown: 10" in brief and "omitted: 5" in brief
    assert "declaring_symbol:" in brief and "is_static:" in brief
    assert "    char:" not in brief and "    dependencies:" not in brief
    assert 'inspect_file(file_path=' in brief and "not proven calls" in brief
    policy = {**server._symbol_search_policy(), "brief_max_visible_items": 2, "machine_max_visible_items": 3}
    monkeypatch.setattr(server, "_symbol_search_policy", lambda: policy)
    displayed = json.loads(_search(monkeypatch, store))
    assert len(displayed) == 3 and displayed[0]["omitted"] == 12
    assert displayed[0]["display_truncated"] is True and displayed[0]["search_truncated"] is False
    policy = {**policy, "max_candidate_rows_per_source": 2}
    monkeypatch.setattr(server, "_symbol_search_policy", lambda: policy)
    rows = json.loads(_search(monkeypatch, store))
    assert len(rows) == 2 and all(row["search_truncated"] for row in rows)
    assert rows[0]["candidate_count_semantics"] == "lower_bound"
    assert rows[0]["omitted"] == 0
    # Literal underscore/percent queries must not manufacture method matches.
    assert server._find_class_method_search_matches("%", "MAIN", store._raw_dir)[0] == []
    assert server._find_class_method_search_matches("run_14", "MAIN", store._raw_dir)[0][0]["member_name"] == "run_14"


def test_empty_brief_explicitly_reports_unknown_method_coverage(monkeypatch, store):
    monkeypatch.setattr(server, "_find_symbol_matches", lambda *args, **kwargs: [])
    brief = _search(monkeypatch, store, format="brief")
    assert '"status": "unavailable"' in brief and "not evidence of absence" in brief
    assert "candidate_count: 0" in brief

def test_real_overloads_and_unsupported_member_syntax(monkeypatch, store, tmp_path):
    from tools.engines.generate_atlas import _normalize_polyglot_symbols

    source = ("export class Reader {\n"
              "  open(value: string): void;\n"
              "  open(value: number): void;\n"
              "  open(value: string | number) {}\n"
              '  ["computed"]() {}\n'
              "  get value() { return 1; }\n"
              "}\n")
    path = tmp_path / "reader.ts"
    path.write_text(source, encoding="utf-8", newline="")
    root = Path(__file__).resolve().parents[2]
    parsed = subprocess.run([shutil.which("node"), str(root / "tools/engines/ast_sequencer.cjs"), str(path)],
                            cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert parsed.returncode == 0, parsed.stderr
    symbols = _normalize_polyglot_symbols(json.loads(parsed.stdout), source, language="typescript")
    parent = next(row for row in symbols if row["type"] == "Class")
    expected = [row for row in parent["member_details"] if row["name"] == "open"]
    assert len(expected) == 3, "Use actual compiler overload records, not synthetic dedup assumptions"
    store._save_atlas_to_sqlite(_atlas(symbols))
    rows = json.loads(_search(monkeypatch, store, "Reader.open"))
    assert [(row["line"], row["end_line"]) for row in rows] == [
        (row["line"], row["end_line"]) for row in expected]
    assert len(rows) == 3 and rows[0]["class_method_search"]["status"] == "partial"
    indexed, _ = server._find_class_method_search_matches("", "MAIN", store._raw_dir)
    assert [row["member_name"] for row in indexed] == ["open", "open", "open"]


def test_same_class_name_in_different_files_is_not_collapsed(monkeypatch, store):
    atlas = _atlas()
    atlas["MAIN"]["files"]["nested/worker.ts"] = copy.deepcopy(atlas["MAIN"]["files"]["worker.ts"])
    store._save_atlas_to_sqlite(atlas)
    rows = json.loads(_search(monkeypatch, store, "Worker.run"))
    assert len(rows) == 2
    assert {row["atlas_node"] for row in rows} == {"MAIN::worker.ts", "MAIN::nested/worker.ts"}
    assert all(row["name"] == "Worker.run" for row in rows)


def test_exact_bare_method_is_ranked_before_source_cap(monkeypatch, store):
    store._save_atlas_to_sqlite(_atlas([_class(members=[
        _method("run_noise", line=2, end_line=2),
        _method("run_other", line=3, end_line=3),
        _method("run", line=4, end_line=4),
    ])]))
    policy = {**server._symbol_search_policy(), "max_candidate_rows_per_source": 1}
    monkeypatch.setattr(server, "_symbol_search_policy", lambda: policy)
    rows = json.loads(_search(monkeypatch, store))
    assert [row["name"] for row in rows] == ["Worker.run"]
    assert rows[0]["match_score"] == 0 and rows[0]["search_truncated"] is True


def test_read_only_uri_preserves_long_and_escaped_database_paths(monkeypatch, tmp_path):
    from tools.core.unmanaged_atomic_io import native_filesystem_path

    raw_dir = tmp_path / ("long-" + "x" * 170) / ("nested-" + "y" * 60) / "space # percent%"
    isolated = ArtifactStore(raw_dir=raw_dir)
    isolated.use_sqlite = True
    isolated.backend = "hybrid_sqlite"
    isolated._ensure_schema()
    isolated._save_atlas_to_sqlite(_atlas())
    db_path = Path(native_filesystem_path(isolated.db_manager.db_path))
    before = db_path.read_bytes()
    rows = json.loads(_search(monkeypatch, isolated, "Worker.run"))
    assert rows[0]["name"] == "Worker.run"
    assert rows[0]["class_method_search"]["status"] == "available"
    assert db_path.read_bytes() == before
