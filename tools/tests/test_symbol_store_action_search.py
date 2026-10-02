"""Store action search uses recorded syntax, not runtime or declaration truth."""
from __future__ import annotations

import copy
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tools.core.artifact_store import ArtifactStore
from tools.engines.generate_atlas import _normalize_polyglot_symbols
from tools.mcp import server


@pytest.fixture
def store(tmp_path):
    result = ArtifactStore(raw_dir=tmp_path)
    result.use_sqlite = True
    result.backend = "hybrid_sqlite"
    result._ensure_schema()
    return result


def _atlas(symbols, *, rel="store.ts"):
    return {"MAIN": {"root_path": ".", "files": {
        rel: {"hash": "fixture", "language": "typescript", "symbols": symbols}}}}


def _recorded_action(name="update"):
    return {"name": "store", "type": "Variable", "line": 1, "end_line": 5,
            "initializer_member_evidence": {"status": "syntax_observed", "members": [{
                "name": name, "kind": "method", "attribution": "returned_object_candidate",
                "line": 2, "end_line": 2, "source_lines": "L2-L2",
                "dependencies": [], "dependencyImports": [],
                "zustand_setter_call_evidence": {"status": "observed", "factory_api": "vanilla_store",
                    "calls": [{"parameter": "set", "line": 2, "end_line": 2, "optional": False}],
                    "factory_form": "direct", "middleware_form": "none", "limitations": [],
                    "binding_scope": "single_file_lexical_store_factory_parameter",
                    "runtime_execution": "not_established"}}], "limitations": [],
                "runtime_owner_binding": "not_established", "lexical_binding": "unverified"}}


def _projection_state(store):
    with store.db_manager.get_connection() as conn:
        return {table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY 1;")]
                for table in ("files", "symbols", "symbol_search_actions", "source_snapshots")}


def _search(monkeypatch, store, query="useStore.update", project="MAIN", format="json"):
    monkeypatch.setattr(server, "_raw_dir_for_target", lambda target: store._raw_dir)
    return server.search_symbols(query, project=project, target_root=str(store._raw_dir), format=format)


def _parse(tmp_path, source):
    node = shutil.which("node")
    assert node, "Node is required for the recorded store-action producer contract"
    root = Path(__file__).resolve().parents[2]
    path = tmp_path / "store.ts"
    path.write_text(source, encoding="utf-8", newline="")
    proc = subprocess.run([node, str(root / "tools/engines/ast_sequencer.cjs"), str(path)],
                          cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert proc.returncode == 0, proc.stderr
    return _normalize_polyglot_symbols(json.loads(proc.stdout), source, language="typescript")


def test_real_parser_atlas_sqlite_public_store_action_search(monkeypatch, store, tmp_path):
    source = '''import { create as makeStore } from "zustand";
export const useStore = makeStore((set) => ({
  update: () => set({ready: true}),
  read() { return 1; },
  value: 3,
}));
export const fakeStore = fakeCreate((set) => ({ fake: () => set({ready: true}) }));
export const plain = { update: () => 1 };
'''
    symbols = _parse(tmp_path, source)
    parent = next(row for row in symbols if row["name"] == "useStore")
    members = parent["initializer_member_evidence"]["members"]
    assert {m["name"] for m in members} == {"update", "read", "value"}
    store._save_atlas_to_sqlite(_atlas(symbols))
    rows = json.loads(_search(monkeypatch, store))
    assert len(rows) == 1
    row = rows[0]
    assert row["name"] == "useStore.update" and row["type"] == "StoreActionCandidate"
    assert row["line"] == next(m["line"] for m in members if m["name"] == "update")
    assert row["member_kind"] == "member" and row["factory_api"] == "react_bound_hook"
    assert row["runtime_execution"] == row["runtime_owner_binding"] == "not_established"
    assert row["store_action_search"]["status"] == "available"
    assert row["atlas_node"] == "MAIN::store.ts" and "char" not in row
    assert "dependencies" not in row and "is_static" not in row
    assert server._find_symbol_matches("useStore.update", "MAIN", store._raw_dir) == []
    assert "candidate_count: 0" in _search(monkeypatch, store, query="useStore.read", format="brief")
    assert "fakeStore.fake" not in _search(monkeypatch, store, query="fake", format="brief")
    plain_brief = _search(monkeypatch, store, query="plain.update", format="brief")
    assert "candidate_count: 0" in plain_brief and "matches:\n  []" in plain_brief
    with store.db_manager.get_connection() as conn:
        assert [r["name"] for r in conn.execute("SELECT name FROM symbol_search_actions ORDER BY line")] == [
            "useStore.update"]
        assert "StoreActionCandidate" not in {r["type"] for r in conn.execute("SELECT type FROM symbols")}


def test_namespace_imported_zustand_factories_flow_to_search_without_runtime_claim(
    monkeypatch, store, tmp_path,
):
    from tools.core.state_flow import project_state_flow_focus

    source = '''import * as zustand from "zustand";
import * as vanilla from "zustand/vanilla";
import * as middleware from "zustand/middleware";
import * as other from "not-zustand";
import * as otherMiddleware from "not-zustand/middleware";
import type * as typeOnly from "zustand";
export const useStore = zustand.create((set) => ({
  update: () => set({ready: true}),
}));
export const vanillaStore = vanilla.createStore(middleware.persist((set) => ({
  reset: () => set({ready: false}),
}), {name: "store"}));
export const wrong = other.create((set) => ({nope: () => set({ready: true})}));
export const wrongMiddleware = vanilla.createStore(otherMiddleware.persist((set) => ({
  nope: () => set({ready: true}),
}), {name: "store"}));
export const typed = typeOnly.create((set) => ({nope: () => set({ready: true})}));
export const optional = zustand.create?.((set) => ({nope: () => set({ready: true})}));
export const optionalAccess = zustand?.create((set) => ({nope: () => set({ready: true})}));
export const computed = zustand["create"]((set) => ({nope: () => set({ready: true})}));
'''
    symbols = _parse(tmp_path, source)
    store._save_atlas_to_sqlite(_atlas(symbols))
    rows = json.loads(_search(monkeypatch, store, query="store", project="MAIN"))
    actions = {(row["name"], row["factory_api"]) for row in rows
               if row["type"] == "StoreActionCandidate"}
    assert actions == {("useStore.update", "react_bound_hook"),
                       ("vanillaStore.reset", "vanilla_store")}
    assert all(row["runtime_execution"] == "not_established"
               and row["runtime_owner_binding"] == "not_established"
               for row in rows if row["type"] == "StoreActionCandidate")
    assert "candidate_count: 0" in _search(monkeypatch, store, query="nope", format="brief")
    focused = project_state_flow_focus(
        _atlas(symbols), project="MAIN", file="store.ts", symbol="useStore.update",
        max_items=3, scan_limit=100,
    )
    assert focused["status"] == "selected"
    assert focused["symbol_context"]["setter_calls"]["status"] == "observed"
    assert focused["symbol_context"]["setter_calls"]["runtime_execution"] == "not_established"


def test_local_object_named_like_zustand_is_not_a_store_action(tmp_path):
    symbols = _parse(tmp_path, '''const zustand = {create: (factory: any) => factory};
export const local = zustand.create((set: any) => ({
  nope: () => set({ready: true}),
}));
''')
    parent = next(row for row in symbols if row["name"] == "local")
    assert not any(member.get("zustand_setter_call_evidence") for member in
                   parent["initializer_member_evidence"]["members"])


def test_invalid_and_legacy_evidence_is_partial_not_an_action(monkeypatch, store):
    valid_member = {"name": "update", "kind": "member", "attribution": "returned_object_candidate",
                    "line": 2, "end_line": 2, "source_lines": "L2-L2",
                    "dependencies": [], "dependencyImports": [],
                    "zustand_setter_call_evidence": {"status": "observed", "factory_api": "vanilla_store",
                        "calls": [{"parameter": "set", "line": 2, "end_line": 2, "optional": False}],
                        "factory_form": "direct", "middleware_form": "none", "limitations": [],
                        "binding_scope": "single_file_lexical_store_factory_parameter",
                        "runtime_execution": "not_established"}}
    def parent(name, evidence):
        return {"name": name, "type": "Variable", "line": 1, "end_line": 10,
                "initializer_member_evidence": evidence}
    evidence = {"status": "syntax_observed", "members": [valid_member], "limitations": [],
                "runtime_owner_binding": "not_established", "lexical_binding": "unverified"}
    malformed = [
        {**valid_member, "name": "[dynamic]"},
        {**valid_member, "line": 0},
        {**valid_member, "attribution": "direct_object_initializer"},
        {**valid_member, "zustand_setter_call_evidence": {"status": "observed", "factory_api": "unknown"}},
        {**valid_member, "zustand_setter_call_evidence": {
            **valid_member["zustand_setter_call_evidence"],
            "calls": [{"parameter": "set", "line": 99, "end_line": 99, "optional": False}]}},
    ]
    store._save_atlas_to_sqlite(_atlas([parent("useStore", {**evidence,
        "members": [valid_member, *malformed]}), parent("legacy", None)]))
    rows = json.loads(_search(monkeypatch, store, query="update"))
    actions = [r for r in rows if r["type"] == "StoreActionCandidate"]
    assert [r["name"] for r in actions] == ["useStore.update"]
    assert actions[0]["store_action_search"]["status"] == "partial"
    assert actions[0]["store_action_search"]["partial_files"] == 1
    with store.db_manager.get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM symbol_search_actions").fetchone()[0] == 1


def test_unresolved_member_shape_lowers_known_factory_coverage(monkeypatch, store, tmp_path):
    source = '''import { create } from "zustand";
export const useStore = create((set) => ({
  update: () => set({ready: true}),
  [dynamicName]: () => set({maybe: true}),
}));
'''
    symbols = _parse(tmp_path, source)
    store._save_atlas_to_sqlite(_atlas(symbols))
    rows = json.loads(_search(monkeypatch, store))
    assert [row["name"] for row in rows] == ["useStore.update"]
    assert rows[0]["store_action_search"]["status"] == "partial"
    assert server._find_store_action_search_matches("%", "MAIN", store._raw_dir)[0] == []
    assert server._find_store_action_search_matches("_", "MAIN", store._raw_dir)[0] == []


def test_project_collisions_scoped_replacement_and_legacy_reader(monkeypatch, store):
    atlas = _atlas([_recorded_action()])
    atlas["COMPANION"] = copy.deepcopy(atlas["MAIN"])
    atlas["COMPANION"]["root_path"] = "packages/companion"
    store._save_atlas_to_sqlite(atlas)
    assert len(json.loads(_search(monkeypatch, store, query="store.update", project="all"))) == 2
    assert len(json.loads(_search(monkeypatch, store, query="store.update", project="MAIN"))) == 1
    assert "packages/companion/store.ts" in _search(monkeypatch, store, query="store.update", project="COMPANION")
    with store.db_manager.get_connection() as conn:
        conn.execute("DROP TABLE symbol_search_actions")
    rows, coverage = server._find_store_action_search_matches("store.update", "MAIN", store._raw_dir)
    assert rows == [] and coverage["status"] == "unavailable"
    assert coverage["reason"] == "legacy_or_unreadable_action_projection"


def test_scoped_replacement_deletion_and_full_rebuild(monkeypatch, store):
    from tools.core import artifact_store

    atlas = _atlas([_recorded_action()])
    atlas["MAIN"]["files"]["other.ts"] = copy.deepcopy(atlas["MAIN"]["files"]["store.ts"])
    store._save_atlas_to_sqlite(atlas)
    with store.db_manager.get_connection() as conn:
        other_id = conn.execute("SELECT file_id FROM files WHERE rel_path = 'other.ts'").fetchone()[0]
        other_before = [tuple(row) for row in conn.execute(
            "SELECT * FROM symbol_search_actions WHERE file_id = ?", (other_id,))]
    changed = copy.deepcopy(atlas)
    changed["MAIN"]["files"]["store.ts"]["symbols"] = [_recorded_action("replace")]
    monkeypatch.setattr(artifact_store, "_source_snapshot_projection_scope", lambda: {"MAIN": {"store.ts"}})
    profile = store._save_atlas_to_sqlite(changed)
    assert profile["atlas_relational_mode"] == "scoped"
    assert [r["name"] for r in server._find_store_action_search_matches("replace", "MAIN", store._raw_dir)[0]] == ["store.replace"]
    with store.db_manager.get_connection() as conn:
        assert [tuple(row) for row in conn.execute(
            "SELECT * FROM symbol_search_actions WHERE file_id = ?", (other_id,))] == other_before
    changed["MAIN"]["files"]["store.ts"]["symbols"] = []
    store._save_atlas_to_sqlite(changed)
    assert server._find_store_action_search_matches("replace", "MAIN", store._raw_dir)[0] == []
    del changed["MAIN"]["files"]["store.ts"]
    store._save_atlas_to_sqlite(changed)
    assert len(server._find_store_action_search_matches("update", "MAIN", store._raw_dir)[0]) == 1
    monkeypatch.setattr(artifact_store, "_source_snapshot_projection_scope", lambda: None)
    changed["MAIN"]["files"] = {}
    store._save_atlas_to_sqlite(changed)
    assert _projection_state(store)["symbol_search_actions"] == []


@pytest.mark.parametrize("scoped", [False, True])
def test_action_projection_failure_rolls_back_with_atlas(monkeypatch, store, scoped):
    from tools.core import artifact_store

    store._save_atlas_to_sqlite(_atlas([_recorded_action()]))
    before = _projection_state(store)
    original = store._project_store_action_search

    def fail(conn, file_id, file_info):
        original(conn, file_id, file_info)
        raise RuntimeError("injected action projection failure")

    monkeypatch.setattr(store, "_project_store_action_search", fail)
    monkeypatch.setattr(artifact_store, "_source_snapshot_projection_scope",
                        lambda: {"MAIN": {"store.ts"}} if scoped else None)
    with pytest.raises(RuntimeError, match="injected"):
        store._save_atlas_to_sqlite(_atlas([_recorded_action("replace")]))
    assert _projection_state(store) == before


def test_migrated_empty_action_table_does_not_upgrade_legacy_coverage(monkeypatch, store):
    store._save_atlas_to_sqlite(_atlas([_recorded_action()]))
    with store.db_manager.get_connection() as conn:
        conn.execute("DROP TABLE symbol_search_actions")
        conn.execute("ALTER TABLE files DROP COLUMN store_action_search_status")
    before = store.db_manager.db_path.read_bytes()
    monkeypatch.setattr(server, "_atlas", lambda **kwargs: pytest.fail("Action search must not load Atlas"))
    assert server._find_store_action_search_matches("update", "MAIN", store._raw_dir)[0] == []
    assert store.db_manager.db_path.read_bytes() == before
    store.db_manager.initialize_schema()
    rows, coverage = server._find_store_action_search_matches("update", "MAIN", store._raw_dir)
    assert rows == [] and coverage["status"] == "partial"
    assert coverage["unprojected_files"] == 1
