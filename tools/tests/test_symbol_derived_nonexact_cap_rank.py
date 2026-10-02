"""Derived syntax candidates must be ranked before their own source caps."""

from __future__ import annotations

import pytest

from tools.core.artifact_store import ArtifactStore
from tools.mcp import server


def _method_symbol(member: str) -> dict:
    return {
        "name": "Worker", "type": "Class", "line": 1, "end_line": 5,
        "member_details": [{"name": member, "kind": "method",
                            "line": 2, "end_line": 2, "static": False}],
    }


def _action_symbol(member: str) -> dict:
    return {
        "name": "useStore", "type": "Variable", "line": 1, "end_line": 5,
        "initializer_member_evidence": {
            "status": "syntax_observed",
            "members": [{
                "name": member, "kind": "method",
                "attribution": "returned_object_candidate",
                "line": 2, "end_line": 2, "source_lines": "L2-L2",
                "dependencies": [], "dependencyImports": [],
                "zustand_setter_call_evidence": {
                    "status": "observed", "factory_api": "vanilla_store",
                    "calls": [{"parameter": "set", "line": 2, "end_line": 2,
                               "optional": False}],
                    "factory_form": "direct", "middleware_form": "none",
                    "limitations": [],
                    "binding_scope": "single_file_lexical_store_factory_parameter",
                    "runtime_execution": "not_established",
                },
            }],
            "limitations": [],
            "runtime_owner_binding": "not_established",
            "lexical_binding": "unverified",
        },
    }


@pytest.mark.parametrize("kind", ["method", "action"])
@pytest.mark.parametrize(
    ("weak_member", "strong_member", "expected_score"),
    [("retarget", "targeting", 3), ("targeting", "target_update", 1)],
)
def test_derived_token_or_prefix_candidate_survives_own_source_cap(
    monkeypatch, tmp_path, kind, weak_member, strong_member, expected_score,
):
    symbol = _method_symbol if kind == "method" else _action_symbol
    atlas = {"MAIN": {"root_path": ".", "files": {
        "src/a.ts": {"hash": "a", "language": "typescript",
                     "symbols": [symbol(weak_member)]},
        "src/z.ts": {"hash": "z", "language": "typescript",
                     "symbols": [symbol(strong_member)]},
    }}}
    store = ArtifactStore(raw_dir=tmp_path)
    store.use_sqlite = True
    store.backend = "hybrid_sqlite"
    store._ensure_schema()
    store._save_atlas_to_sqlite(atlas)
    policy = {**server._symbol_search_policy(), "max_candidate_rows_per_source": 1}
    monkeypatch.setattr(server, "_symbol_search_policy", lambda: policy)

    reader = (
        server._find_class_method_search_matches
        if kind == "method" else server._find_store_action_search_matches
    )
    rows, coverage = reader("target", "MAIN", tmp_path)
    assert coverage["status"] == "available"
    assert len(rows) == 1
    assert rows[0]["file"] == "src/z.ts"
    assert rows[0]["member_name"] == strong_member
    assert rows[0]["match_score"] == expected_score
    assert rows[0]["search_truncated"] is True


@pytest.mark.parametrize("kind", ["method", "action"])
def test_exact_indexed_filename_beats_backup_stem_before_derived_cap(
    monkeypatch, tmp_path, kind,
):
    symbol = _method_symbol if kind == "method" else _action_symbol
    atlas = {"MAIN": {"root_path": ".", "files": {
        "src/a/target.ts.backup": {"hash": "backup", "language": "typescript",
                                   "symbols": [symbol("run")]},
        "src/z/target.ts": {"hash": "target", "language": "typescript",
                            "symbols": [symbol("run")]},
    }}}
    store = ArtifactStore(raw_dir=tmp_path)
    store.use_sqlite = True
    store.backend = "hybrid_sqlite"
    store._ensure_schema()
    store._save_atlas_to_sqlite(atlas)
    policy = {**server._symbol_search_policy(), "max_candidate_rows_per_source": 1}
    monkeypatch.setattr(server, "_symbol_search_policy", lambda: policy)
    reader = (
        server._find_class_method_search_matches
        if kind == "method" else server._find_store_action_search_matches
    )
    rows, coverage = reader("target.ts", "MAIN", tmp_path)
    assert coverage["status"] == "available"
    assert [(row["file"], row["match_score"]) for row in rows] == [("src/z/target.ts", 0)]
    assert rows[0]["search_truncated"] is True


@pytest.mark.parametrize("kind", ["method", "action"])
def test_companion_display_prefix_does_not_improve_indexed_rank(
    monkeypatch, tmp_path, kind,
):
    symbol = _method_symbol if kind == "method" else _action_symbol
    atlas = {"COMPANION": {"root_path": "packages/target", "files": {
        "src/worker.ts": {"hash": "worker", "language": "typescript",
                          "symbols": [symbol("retarget")]},
    }}}
    store = ArtifactStore(raw_dir=tmp_path)
    store.use_sqlite = True
    store.backend = "hybrid_sqlite"
    store._ensure_schema()
    store._save_atlas_to_sqlite(atlas)
    reader = (
        server._find_class_method_search_matches
        if kind == "method" else server._find_store_action_search_matches
    )
    rows, coverage = reader("target", "COMPANION", tmp_path)
    assert coverage["status"] == "available"
    assert len(rows) == 1
    assert rows[0]["repo_relative_path"] == "packages/target/src/worker.ts"
    assert rows[0]["match_score"] == 4
