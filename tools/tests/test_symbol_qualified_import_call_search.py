"""Qualified import calls are searchable syntax candidates, never executed edges."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import copy
from pathlib import Path

from tools.core.artifact_store import ArtifactStore
from tools.core.polyglot_imports import (
    extract_direct_import_binding_evidence,
    extract_module_root_import_call_evidence,
)
from tools.engines.generate_atlas import (
    AST_CONTRACT_VERSION,
    _normalize_polyglot_symbols,
    file_contract_is_current,
)
from tools.mcp import server


def _source_atlas(tmp_path: Path, source: str) -> dict:
    node = shutil.which("node")
    assert node, "Node is required for the parser contract"
    root = Path(__file__).resolve().parents[2]
    path = tmp_path / "consumer.ts"
    path.write_text(source, encoding="utf-8", newline="")
    parsed = subprocess.run(
        [node, str(root / "tools/engines/ast_sequencer.cjs"), str(path)],
        cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert parsed.returncode == 0, parsed.stderr
    raw = json.loads(parsed.stdout)
    symbols = _normalize_polyglot_symbols(raw, source, language="typescript")
    return {"MAIN": {"root_path": ".", "files": {"consumer.ts": {
        "hash": hashlib.sha256(source.encode()).hexdigest(),
        "language": "typescript",
        "loc": source.count("\n") + 1,
        "symbols": symbols,
        "direct_import_binding_evidence": extract_direct_import_binding_evidence(raw),
        "module_root_import_call_evidence": extract_module_root_import_call_evidence(raw),
    }}}}


def test_old_typescript_file_contract_cannot_reuse_missing_module_root_evidence():
    file_data = {
        "ast_contract_version": AST_CONTRACT_VERSION,
        "language": "typescript",
        "parser_evidence": {
            "reported_by_adapter": True,
            "status": "observed",
            "parser_kind": "typescript_compiler_api",
        },
        "project_key": "MAIN",
        "atlas_rel_path": "consumer.ts",
        "workspace_rel": "consumer.ts",
        "repo_relative_path": "consumer.ts",
        "target_ref": "MAIN::consumer.ts",
        "symbols": [],
    }
    assert not file_contract_is_current(file_data)
    file_data["module_root_import_call_evidence"] = {
        "status": "observed", "calls": [], "omitted": 0,
        "callsite_scope": "module_root",
    }
    assert file_contract_is_current(file_data)
    file_data["ast_contract_version"] = "v18.9-const-local-rehydrate-alias-evidence"
    assert not file_contract_is_current(file_data)
    file_data["ast_contract_version"] = "v18.10-static-literal-import-member-call-evidence"
    assert not file_contract_is_current(file_data)


def test_module_root_namespace_calls_are_searchable_without_nested_or_shadowed_joins(
    monkeypatch, tmp_path,
):
    source = '''import * as Sentry from "@sentry/react";
import DefaultTool from "./tool";
import type * as TypeOnly from "./types";
Sentry.init({});
const boot = Sentry.start();
{ const Sentry = { skip() {} }; Sentry.skip(); }
Sentry?.optional();
Sentry["computed"]();
DefaultTool.run();
TypeOnly.fake();
setTimeout(() => Sentry.nested(), 0);
export function later() { Sentry.callable(); }
'''
    atlas = _source_atlas(tmp_path, source)
    evidence = atlas["MAIN"]["files"]["consumer.ts"]["module_root_import_call_evidence"]
    assert evidence["status"] == "observed"
    assert [(row["localName"], row["member"]) for row in evidence["calls"]] == [
        ("Sentry", "init"), ("Sentry", "start"), ("Sentry", "computed"),
    ]
    store = ArtifactStore(raw_dir=tmp_path / ".raw")
    store.use_sqlite = True
    store.backend = "hybrid_sqlite"
    store._ensure_schema()
    store._save_atlas_to_sqlite(atlas)
    monkeypatch.setattr(server, "_raw_dir_for_target", lambda _target: store._raw_dir)
    for name in ("Sentry.init", "Sentry.start", "Sentry.computed"):
        rows = json.loads(server.search_symbols(name, target_root=str(tmp_path), format="json"))
        calls = [row for row in rows if row["type"] == "QualifiedImportCallCandidate"]
        assert len(calls) == 1
        assert calls[0]["callsite_scope"] == "module_root"
        assert "caller_symbol" not in calls[0]
    for name in ("Sentry.skip", "Sentry.optional",
                 "DefaultTool.run", "TypeOnly.fake", "Sentry.nested"):
        assert server._find_qualified_import_call_search_matches(
            name, "MAIN", store._raw_dir)[0] == []
    with store.db_manager.get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM dependencies").fetchone()[0] == 0


def test_static_literal_namespace_member_calls_are_search_candidates(monkeypatch, tmp_path):
    source = '''import * as Sentry from "@sentry/react";
import DefaultTool from "./tool";
const key = "dynamic";
Sentry["init"]({});
Sentry[key]({});
Sentry?.["optional"]();
Sentry["not-valid"]();
DefaultTool["run"]();
export function boot() {
  Sentry['start']();
  Sentry[key]();
  { const Sentry = { skip() {} }; Sentry["skip"](); }
  setTimeout(() => Sentry["nested"](), 0);
}
'''
    atlas = _source_atlas(tmp_path, source)
    file_data = atlas["MAIN"]["files"]["consumer.ts"]
    root_calls = file_data["module_root_import_call_evidence"]["calls"]
    boot = next(row for row in file_data["symbols"] if row["name"] == "boot")
    body_calls = boot["import_call_evidence"]["calls"]
    assert [(row["localName"], row["member"]) for row in root_calls] == [
        ("Sentry", "init")]
    assert [(row["localName"], row["member"]) for row in body_calls
            if row["kind"] == "namespace"] == [("Sentry", "start")]
    store = ArtifactStore(raw_dir=tmp_path / ".raw")
    store.use_sqlite = True
    store.backend = "hybrid_sqlite"
    store._ensure_schema()
    store._save_atlas_to_sqlite(atlas)
    monkeypatch.setattr(server, "_raw_dir_for_target", lambda _target: store._raw_dir)
    for name, scope in (("Sentry.init", "module_root"),
                        ("Sentry.start", "indexed_top_level_callable")):
        rows = json.loads(server.search_symbols(name, target_root=str(tmp_path), format="json"))
        candidates = [row for row in rows if row["type"] == "QualifiedImportCallCandidate"]
        assert len(candidates) == 1
        assert candidates[0]["callsite_scope"] == scope
        assert candidates[0]["runtime_execution"] == "not_established"
    for name in ("Sentry.dynamic", "Sentry.optional", "Sentry.not-valid",
                 "Sentry.skip", "Sentry.nested", "DefaultTool.run"):
        assert server._find_qualified_import_call_search_matches(
            name, "MAIN", store._raw_dir)[0] == []


def test_module_root_cap_legacy_coverage_and_project_scope(tmp_path):
    source = ('import * as Sentry from "@sentry/react";\n'
              + "\n".join("Sentry.init({});" for _ in range(65)) + "\n")
    atlas = _source_atlas(tmp_path, source)
    evidence = atlas["MAIN"]["files"]["consumer.ts"]["module_root_import_call_evidence"]
    assert len(evidence["calls"]) == 64
    assert evidence["omitted"] == 1
    atlas["COMPANION"] = {"root_path": "companion", "files": {
        "consumer.ts": copy.deepcopy(atlas["MAIN"]["files"]["consumer.ts"]),
    }}
    store = ArtifactStore(raw_dir=tmp_path / ".raw")
    store.use_sqlite = True
    store.backend = "hybrid_sqlite"
    store._ensure_schema()
    store._save_atlas_to_sqlite(atlas)
    main, coverage = server._find_qualified_import_call_search_matches(
        "Sentry.init", "MAIN", store._raw_dir)
    all_rows, all_coverage = server._find_qualified_import_call_search_matches(
        "Sentry.init", "all", store._raw_dir)
    assert len(main) == 64 and all(row["project"] == "MAIN" for row in main)
    assert coverage["status"] == "partial"
    assert len(all_rows) == 128 and all_coverage["partial_files"] == 2
    legacy = copy.deepcopy(atlas)
    legacy["MAIN"]["files"]["consumer.ts"].pop("module_root_import_call_evidence")
    store._save_atlas_to_sqlite(legacy)
    main, coverage = server._find_qualified_import_call_search_matches(
        "Sentry.init", "MAIN", store._raw_dir)
    assert main == []
    assert coverage["status"] == "partial"
    assert coverage["unprojected_files"] == 0
    assert coverage["partial_files"] == 1


def test_legacy_qualified_call_table_migration_downgrades_old_coverage(tmp_path):
    atlas = _source_atlas(
        tmp_path, 'import * as Sentry from "@sentry/react";\nSentry.init({});\n')
    store = ArtifactStore(raw_dir=tmp_path / ".raw")
    store.use_sqlite = True
    store.backend = "hybrid_sqlite"
    store._ensure_schema()
    store._save_atlas_to_sqlite(atlas)
    with store.db_manager.get_connection() as conn:
        conn.execute("DROP TABLE symbol_search_qualified_import_calls")
        conn.execute("""CREATE TABLE symbol_search_qualified_import_calls (
            call_id INTEGER PRIMARY KEY AUTOINCREMENT, file_id INTEGER NOT NULL,
            name TEXT NOT NULL, receiver_name TEXT NOT NULL,
            member_name TEXT NOT NULL, raw_source TEXT NOT NULL,
            caller_symbol TEXT NOT NULL, caller_line INTEGER NOT NULL,
            line INTEGER NOT NULL, end_line INTEGER NOT NULL
        )""")
        conn.execute(
            "UPDATE files SET qualified_import_call_search_status = 'recorded_qualified_calls'"
        )
    store.db_manager.initialize_schema()
    with store.db_manager.get_connection() as conn:
        columns = {row[1] for row in conn.execute(
            "PRAGMA table_info(symbol_search_qualified_import_calls)")}
        status = conn.execute(
            "SELECT qualified_import_call_search_status FROM files").fetchone()[0]
    assert "callsite_scope" in columns
    assert status is None
    rows, coverage = server._find_qualified_import_call_search_matches(
        "Sentry.init", "MAIN", store._raw_dir)
    assert rows == []
    assert coverage["status"] == "partial"


def test_namespace_qualified_call_search_is_parser_bound_and_syntax_only(monkeypatch, tmp_path):
    source = '''import * as Sentry from "@sentry/react";
import DefaultTool from "./tool";
export function bootstrap() { Sentry.init({}); }
export function shadow(Sentry: any) { Sentry.init({}); }
export function decoys() {
  Sentry?.init({});
  Sentry["init"]({});
  DefaultTool.init({});
  setTimeout(() => Sentry.init({}), 0);
}
Sentry.start();
'''
    atlas = _source_atlas(tmp_path, source)
    symbol = next(row for row in atlas["MAIN"]["files"]["consumer.ts"]["symbols"]
                  if row["name"] == "bootstrap")
    assert any(call["localName"] == "Sentry" and call["member"] == "init"
               and call["kind"] == "namespace"
               for call in symbol["import_call_evidence"]["calls"])
    store = ArtifactStore(raw_dir=tmp_path / ".raw")
    store.use_sqlite = True
    store.backend = "hybrid_sqlite"
    store._ensure_schema()
    store._save_atlas_to_sqlite(atlas)
    monkeypatch.setattr(server, "_raw_dir_for_target", lambda _target: store._raw_dir)
    result = json.loads(server.search_symbols(
        "Sentry.init", target_root=str(tmp_path), format="json",
    ))
    calls = [row for row in result if row["type"] == "QualifiedImportCallCandidate"]
    assert len(calls) == 2
    assert {row["name"] for row in calls} == {"Sentry.init"}
    assert {row["caller_symbol"] for row in calls} == {"bootstrap", "decoys"}
    assert {row["raw_source"] for row in calls} == {"@sentry/react"}
    assert {row["runtime_execution"] for row in calls} == {"not_established"}
    assert {row["module_resolution"] for row in calls} == {"not_established"}
    assert {row["qualified_import_call_search"]["scope"] for row in calls} == {
        "indexed_top_level_callable_and_module_root_direct_namespace_import_calls"}
    assert [row["callsite_scope"] for row in server._find_qualified_import_call_search_matches(
        "Sentry.start", "MAIN", store._raw_dir)[0]] == ["module_root"]
    assert server._find_symbol_matches("Sentry.init", "MAIN", store._raw_dir) == []
    with store.db_manager.get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM symbol_search_qualified_import_calls").fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM dependencies").fetchone()[0] == 0


def test_qualified_call_cap_and_forged_binding_are_partial_not_absent(tmp_path):
    calls = "\n".join("  Sentry.init({});" for _ in range(65))
    source = ('import * as Sentry from "@sentry/react";\n'
              'export function bootstrap() {\n' + calls + '\n}\n')
    atlas = _source_atlas(tmp_path, source)
    store = ArtifactStore(raw_dir=tmp_path / ".raw")
    store.use_sqlite = True
    store.backend = "hybrid_sqlite"
    store._ensure_schema()
    store._save_atlas_to_sqlite(atlas)
    with store.db_manager.get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM symbol_search_qualified_import_calls").fetchone()[0] == 64
    rows, coverage = server._find_qualified_import_call_search_matches(
        "Sentry.init", "MAIN", store._raw_dir)
    assert rows and coverage["status"] == "partial"
    assert coverage["partial_files"] == 1
    assert coverage["source_tree_complete"] is False
    forged = copy.deepcopy(atlas)
    caller = next(row for row in forged["MAIN"]["files"]["consumer.ts"]["symbols"]
                  if row["name"] == "bootstrap")
    caller["import_call_evidence"]["calls"][0]["source"] = "./wrong"
    store._save_atlas_to_sqlite(forged)
    with store.db_manager.get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM symbol_search_qualified_import_calls").fetchone()[0] == 64
        assert conn.execute(
            "SELECT MIN(line) FROM symbol_search_qualified_import_calls"
        ).fetchone()[0] == 4
    assert server._find_qualified_import_call_search_matches(
        "Sentry.init", "MAIN", store._raw_dir)[1]["status"] == "partial"


def test_qualified_call_scoped_replace_full_removal_and_legacy_read(monkeypatch, tmp_path):
    from tools.core import artifact_store

    source = ('import * as Sentry from "@sentry/react";\n'
              'export function bootstrap() { Sentry.init({}); }\n')
    atlas = _source_atlas(tmp_path, source)
    atlas["MAIN"]["files"]["second.ts"] = copy.deepcopy(
        atlas["MAIN"]["files"]["consumer.ts"])
    store = ArtifactStore(raw_dir=tmp_path / ".raw")
    store.use_sqlite = True
    store.backend = "hybrid_sqlite"
    store._ensure_schema()
    store._save_atlas_to_sqlite(atlas)
    reader = server._find_qualified_import_call_search_matches
    assert len(reader("Sentry.init", "MAIN", store._raw_dir)[0]) == 2
    changed = copy.deepcopy(atlas)
    changed["MAIN"]["files"]["consumer.ts"]["symbols"] = []
    monkeypatch.setattr(artifact_store, "_source_snapshot_projection_scope",
                        lambda: {"MAIN": {"consumer.ts"}})
    assert store._save_atlas_to_sqlite(changed)["atlas_relational_mode"] == "scoped"
    rows, coverage = reader("Sentry.init", "MAIN", store._raw_dir)
    assert [row["file"] for row in rows] == ["second.ts"]
    assert coverage["status"] == "available"
    monkeypatch.setattr(artifact_store, "_source_snapshot_projection_scope", lambda: None)
    store._save_atlas_to_sqlite({"MAIN": {"root_path": ".", "files": {}}})
    assert reader("Sentry.init", "MAIN", store._raw_dir)[0] == []
    store._save_atlas_to_sqlite(atlas)
    with store.db_manager.get_connection() as conn:
        conn.execute("DROP TABLE symbol_search_qualified_import_calls")
    before = store.db_manager.db_path.read_bytes()
    rows, coverage = reader("Sentry.init", "MAIN", store._raw_dir)
    assert rows == [] and coverage["status"] == "unavailable"
    assert store.db_manager.db_path.read_bytes() == before
    store.db_manager.initialize_schema()
    rows, coverage = reader("Sentry.init", "MAIN", store._raw_dir)
    assert rows == [] and coverage["status"] == "partial"
