"""Direct import bindings are search orientation, never declarations or graph edges."""
from __future__ import annotations

import copy
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tools.core.artifact_store import ArtifactStore
from tools.core.polyglot_imports import (
    extract_direct_import_binding_evidence,
    extract_typescript_import_evidence,
)
from tools.mcp import server


@pytest.fixture
def store(tmp_path):
    result = ArtifactStore(raw_dir=tmp_path)
    result.use_sqlite = True
    result.backend = "hybrid_sqlite"
    result._ensure_schema()
    return result


def _parse(tmp_path, source):
    node = shutil.which("node")
    assert node, "Node is required for direct import syntax proof"
    root = Path(__file__).resolve().parents[2]
    path = tmp_path / "consumer.ts"
    path.write_text(source, encoding="utf-8", newline="")
    parsed = subprocess.run(
        [node, str(root / "tools/engines/ast_sequencer.cjs"), str(path)],
        cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert parsed.returncode == 0, parsed.stderr
    return json.loads(parsed.stdout)


def _atlas(evidence, *, rel="consumer.ts"):
    return {"MAIN": {"root_path": ".", "files": {
        rel: {"hash": "fixture", "language": "typescript", "symbols": [],
              "direct_import_binding_evidence": evidence},
    }}}


def _search(monkeypatch, store, query="Sentry", *, project="MAIN", format="json"):
    monkeypatch.setattr(server, "_raw_dir_for_target", lambda target: store._raw_dir)
    return server.search_symbols(query, project=project, target_root=str(store._raw_dir), format=format)


def test_parser_atlas_sqlite_public_search_preserves_alias_and_type_only(monkeypatch, store, tmp_path):
    source = '''import * as Sentry from "@sentry/react";
import { captureException as report, type Scope as LocalScope } from "@sentry/react";
import DefaultTool from "./tool";
import type { TypeOnly } from "./types";
export { renamed as outward } from "./reexport";
const lazy = import("./dynamic");
const loaded = require("./commonjs");
'''
    raw = _parse(tmp_path, source)
    evidence = extract_direct_import_binding_evidence(raw)
    assert evidence["status"] == "observed"
    assert [(item["localName"], item["importedName"], item["line"], item["typeOnly"])
            for item in evidence["records"]] == [
        ("Sentry", "*", 1, False), ("report", "captureException", 2, False),
        ("LocalScope", "Scope", 2, True), ("DefaultTool", "default", 3, False),
        ("TypeOnly", "TypeOnly", 4, True),
    ]
    assert any(item["kind"] == "dynamic" for item in extract_typescript_import_evidence(raw)["records"])
    store._save_atlas_to_sqlite(_atlas(evidence))
    sentry = json.loads(_search(monkeypatch, store))[0]
    assert sentry["type"] == "ImportBindingCandidate"
    assert (sentry["name"], sentry["imported_name"], sentry["raw_source"]) == (
        "Sentry", "*", "@sentry/react")
    assert sentry["line"] == sentry["end_line"] == 1
    assert sentry["binding_scope"] == "file_top_level_import_declaration"
    assert sentry["runtime_execution"] == "not_established"
    assert sentry["import_binding_search"]["status"] == "available"
    assert "char" not in sentry and "dependencies" not in sentry
    assert server._find_symbol_matches("Sentry", "MAIN", store._raw_dir) == []
    assert json.loads(_search(monkeypatch, store, query="captureException"))[0]["name"] == "report"
    assert json.loads(_search(monkeypatch, store, query="LocalScope"))[0]["type_only"] is True
    for nonbinding in ("outward", "lazy", "loaded"):
        assert server._find_direct_import_binding_search_matches(
            nonbinding, "MAIN", store._raw_dir)[0] == []
    brief = _search(monkeypatch, store, format="brief")
    assert "ImportBindingCandidate" in brief
    assert 'raw_source: "@sentry/react"' in brief
    assert "Import binding candidates are top-level ES import syntax" in brief
    with store.db_manager.get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM symbol_search_import_bindings").fetchone()[0] == 5
        assert conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM dependencies").fetchone()[0] == 0


def test_missing_and_malformed_evidence_is_partial_not_absence(monkeypatch, store):
    valid = {"source": "./lib", "localName": "Tool", "importedName": "default",
             "kind": "default", "typeOnly": False, "line": 1, "endLine": 1,
             "bindingScope": "file_top_level_import_declaration"}
    atlas = _atlas({"status": "observed", "records": [
        valid, {**valid, "localName": "[dynamic]"}, {**valid, "line": True},
        {**valid, "bindingScope": "local"},
    ]})
    atlas["MAIN"]["files"]["legacy.ts"] = {"hash": "legacy", "language": "typescript", "symbols": []}
    atlas["MAIN"]["files"]["other.py"] = {"hash": "python", "language": "python", "symbols": []}
    store._save_atlas_to_sqlite(atlas)
    rows, coverage = server._find_direct_import_binding_search_matches("Tool", "MAIN", store._raw_dir)
    assert [row["name"] for row in rows] == ["Tool"]
    assert coverage["status"] == "partial" and coverage["partial_files"] == 1
    assert coverage["unprojected_files"] == 1 and coverage["indexed_files"] == 2
    assert json.loads(_search(monkeypatch, store, query="Tool"))[0]["import_binding_search"] == coverage


def test_scoped_refresh_project_collisions_and_legacy_read(monkeypatch, store):
    row = {"source": "./lib", "localName": "Tool", "importedName": "default",
           "kind": "default", "typeOnly": False, "line": 1, "endLine": 1,
           "bindingScope": "file_top_level_import_declaration"}
    atlas = _atlas({"status": "observed", "records": [row]})
    atlas["MAIN"]["files"]["other.ts"] = copy.deepcopy(atlas["MAIN"]["files"]["consumer.ts"])
    atlas["COMPANION"] = copy.deepcopy(atlas["MAIN"])
    atlas["COMPANION"]["root_path"] = "packages/companion"
    store._save_atlas_to_sqlite(atlas)
    assert len(server._find_direct_import_binding_search_matches("Tool", "all", store._raw_dir)[0]) == 4
    assert len(server._find_direct_import_binding_search_matches("Tool", "COMPANION", store._raw_dir)[0]) == 2
    from tools.core import artifact_store
    changed = copy.deepcopy(atlas)
    changed["MAIN"]["files"]["consumer.ts"]["direct_import_binding_evidence"]["records"][0]["localName"] = "Replacement"
    monkeypatch.setattr(artifact_store, "_source_snapshot_projection_scope",
                        lambda: {"MAIN": {"consumer.ts"}})
    profile = store._save_atlas_to_sqlite(changed)
    assert profile["atlas_relational_mode"] == "scoped"
    assert len(server._find_direct_import_binding_search_matches("Replacement", "all", store._raw_dir)[0]) == 1
    assert len(server._find_direct_import_binding_search_matches("Tool", "all", store._raw_dir)[0]) == 3
    with store.db_manager.get_connection() as conn:
        conn.execute("DROP TABLE symbol_search_import_bindings")
    before = store.db_manager.db_path.read_bytes()
    rows, coverage = server._find_direct_import_binding_search_matches("Tool", "MAIN", store._raw_dir)
    assert rows == [] and coverage["status"] == "unavailable"
    assert coverage["reason"] == "legacy_or_unreadable_import_binding_projection"
    assert store.db_manager.db_path.read_bytes() == before
    store.db_manager.initialize_schema()
    rows, coverage = server._find_direct_import_binding_search_matches("Tool", "MAIN", store._raw_dir)
    assert rows == [] and coverage["status"] == "partial"
    assert coverage["unprojected_files"] == 2


def test_full_rebuild_removes_stale_import_binding_rows(store):
    row = {"source": "./lib", "localName": "Tool", "importedName": "default",
           "kind": "default", "typeOnly": False, "line": 1, "endLine": 1,
           "bindingScope": "file_top_level_import_declaration"}
    atlas = _atlas({"status": "observed", "records": [row]})
    store._save_atlas_to_sqlite(atlas)
    assert server._find_direct_import_binding_search_matches("Tool", "MAIN", store._raw_dir)[0]
    store._save_atlas_to_sqlite({"MAIN": {"root_path": ".", "files": {}}})
    assert server._find_direct_import_binding_search_matches("Tool", "MAIN", store._raw_dir)[0] == []
    with store.db_manager.get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM symbol_search_import_bindings").fetchone()[0] == 0


@pytest.mark.parametrize(
    ("table", "reader", "needle"),
    [
        ("symbol_search_members", server._find_class_method_search_matches, "Worker.run"),
        ("symbol_search_actions", server._find_store_action_search_matches, "useStore.update"),
    ],
)
def test_recreated_sibling_search_tables_downgrade_stale_coverage(store, table, reader, needle):
    method = {"name": "Worker", "type": "Class", "line": 1, "end_line": 3,
              "member_details": [{"name": "run", "kind": "method", "line": 2,
                                  "end_line": 2, "static": False}]}
    action = {"name": "useStore", "type": "Variable", "line": 1, "end_line": 3,
              "initializer_member_evidence": {"status": "syntax_observed",
                  "runtime_owner_binding": "not_established", "lexical_binding": "unverified",
                  "limitations": [], "members": [{
                      "name": "update", "kind": "method", "line": 2, "end_line": 2,
                      "attribution": "returned_object_candidate",
                      "zustand_setter_call_evidence": {
                          "status": "observed", "factory_api": "vanilla_store",
                          "factory_form": "direct", "middleware_form": "none",
                          "binding_scope": "single_file_lexical_store_factory_parameter",
                          "runtime_execution": "not_established",
                          "calls": [{"parameter": "set", "line": 2, "end_line": 2,
                                     "optional": False}],
                      },
                  }]}}
    store._save_atlas_to_sqlite({"MAIN": {"root_path": ".", "files": {
        "method.ts": {"hash": "m", "language": "typescript", "symbols": [method]},
        "action.ts": {"hash": "a", "language": "typescript", "symbols": [action]},
    }}})
    assert reader(needle, "MAIN", store._raw_dir)[1]["status"] == "available"
    with store.db_manager.get_connection() as conn:
        conn.execute(f"DROP TABLE {table}")
    store.db_manager.initialize_schema()
    rows, coverage = reader(needle, "MAIN", store._raw_dir)
    assert rows == [] and coverage["status"] == "partial"
    assert coverage["unprojected_files"] >= 1


def test_import_binding_source_cap_prefers_token(monkeypatch, store):
    def record(local):
        return {"source": "./lib", "localName": local, "importedName": "default",
                "kind": "default", "typeOnly": False, "line": 1, "endLine": 1,
                "bindingScope": "file_top_level_import_declaration"}
    atlas = _atlas({"status": "observed", "records": [record("retarget")]}, rel="a.ts")
    atlas["MAIN"]["files"]["z.ts"] = {
        "hash": "z", "language": "typescript", "symbols": [],
        "direct_import_binding_evidence": {"status": "observed", "records": [record("targeting")]},
    }
    store._save_atlas_to_sqlite(atlas)
    policy = {**server._symbol_search_policy(), "max_candidate_rows_per_source": 1}
    monkeypatch.setattr(server, "_symbol_search_policy", lambda: policy)
    rows, coverage = server._find_direct_import_binding_search_matches("target", "MAIN", store._raw_dir)
    assert coverage["status"] == "available"
    assert len(rows) == 1 and rows[0]["file"] == "z.ts"
    assert rows[0]["search_truncated"] is True


@pytest.mark.parametrize("scoped", [False, True])
def test_import_binding_projection_failure_rolls_back(monkeypatch, store, scoped):
    from tools.core import artifact_store

    original = {"source": "./lib", "localName": "Tool", "importedName": "default",
                "kind": "default", "typeOnly": False, "line": 1, "endLine": 1,
                "bindingScope": "file_top_level_import_declaration"}
    atlas = _atlas({"status": "observed", "records": [original]})
    store._save_atlas_to_sqlite(atlas)
    with store.db_manager.get_connection() as conn:
        before = [tuple(row) for row in conn.execute(
            "SELECT * FROM symbol_search_import_bindings ORDER BY binding_id")]
    changed = copy.deepcopy(atlas)
    changed["MAIN"]["files"]["consumer.ts"]["direct_import_binding_evidence"]["records"][0]["localName"] = "Changed"
    project = store._project_direct_import_binding_search

    def fail(conn, file_id, file_info):
        project(conn, file_id, file_info)
        raise RuntimeError("injected import binding projection failure")

    monkeypatch.setattr(store, "_project_direct_import_binding_search", fail)
    monkeypatch.setattr(artifact_store, "_source_snapshot_projection_scope",
                        lambda: {"MAIN": {"consumer.ts"}} if scoped else None)
    with pytest.raises(RuntimeError, match="injected"):
        store._save_atlas_to_sqlite(changed)
    with store.db_manager.get_connection() as conn:
        after = [tuple(row) for row in conn.execute(
            "SELECT * FROM symbol_search_import_bindings ORDER BY binding_id")]
    assert after == before


def test_degraded_parser_can_orient_but_downgrades_coverage(monkeypatch, store, tmp_path):
    raw = _parse(tmp_path, 'import { Tool } from "./lib";\nconst broken = ;\n')
    evidence = extract_direct_import_binding_evidence(raw)
    assert evidence["status"] == "degraded"
    store._save_atlas_to_sqlite(_atlas(evidence))
    rows, coverage = server._find_direct_import_binding_search_matches("Tool", "MAIN", store._raw_dir)
    assert [row["name"] for row in rows] == ["Tool"]
    assert coverage["status"] == "partial" and coverage["partial_files"] == 1
