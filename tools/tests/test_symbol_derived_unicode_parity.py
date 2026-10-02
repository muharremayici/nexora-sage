"""Unicode path and project casing parity for recorded derived search syntax."""

from __future__ import annotations

import pytest

from tools.core.artifact_store import ArtifactStore
from tools.mcp import server


def _file() -> dict:
    return {
        "hash": "unicode-derived",
        "language": "typescript",
        "loc": 10,
        "symbols": [
            {"name": "Worker", "type": "Class", "line": 1, "end_line": 5,
             "member_details": [{"name": "run", "kind": "method", "line": 2,
                                 "end_line": 2, "static": False}]},
            {"name": "useStore", "type": "Variable", "line": 1, "end_line": 5,
             "initializer_member_evidence": {
                 "status": "syntax_observed", "runtime_owner_binding": "not_established",
                 "lexical_binding": "unverified", "limitations": [],
                 "members": [{"name": "update", "kind": "method",
                              "attribution": "returned_object_candidate", "line": 2,
                              "end_line": 2, "zustand_setter_call_evidence": {
                                  "status": "observed", "factory_api": "vanilla_store",
                                  "calls": [{"parameter": "set", "line": 2, "end_line": 2,
                                             "optional": False}],
                                  "factory_form": "direct", "middleware_form": "none",
                                  "binding_scope": "single_file_lexical_store_factory_parameter",
                                  "runtime_execution": "not_established"}}]}},
        ],
        "direct_import_binding_evidence": {"status": "observed", "records": [{
            "localName": "Sentry", "importedName": "*", "source": "./lib",
            "kind": "namespace", "typeOnly": False, "line": 1, "endLine": 1,
            "bindingScope": "file_top_level_import_declaration",
        }]},
        "module_root_import_call_evidence": {
            "status": "observed", "binding_scope": "single_file_lexical_import",
            "callsite_scope": "module_root", "runtime_execution": "not_established",
            "omitted": 0, "calls": [{
                "localName": "Sentry", "member": "init", "source": "./lib",
                "importedName": "*", "kind": "namespace", "optional": False,
                "line": 4, "end_line": 4,
            }],
        },
    }


READERS = (
    server._find_class_method_search_matches,
    server._find_store_action_search_matches,
    server._find_direct_import_binding_search_matches,
    server._find_qualified_import_call_search_matches,
)


@pytest.fixture
def raw_dir(tmp_path):
    atlas = {
        "MAIN": {"root_path": ".", "files": {
            "src/\u00c9clair.ts": _file(),
            "src/Equipe.ts": _file(),
            "src/\u0130tem.ts": _file(),
            "src/\u0130tem.ts.backup": _file(),
            "src/\u212aernel.ts": _file(),
        }},
        "\u00c9QUIPE": {"root_path": "companion", "files": {
            "src/\u00c9clair.ts": _file(),
        }},
    }
    store = ArtifactStore(raw_dir=tmp_path)
    store.use_sqlite = True
    store.backend = "hybrid_sqlite"
    store._ensure_schema()
    store._save_atlas_to_sqlite(atlas)
    return tmp_path


@pytest.mark.parametrize("reader", READERS)
def test_derived_unicode_path_and_project_scope(reader, raw_dir):
    rows, coverage = reader("\u00e9clair", "MAIN", raw_dir)
    assert coverage["status"] == "available"
    assert [row["file"] for row in rows] == ["src/\u00c9clair.ts"]

    scoped, scoped_coverage = reader("\u00e9clair", "\u00e9quipe", raw_dir)
    assert scoped_coverage["status"] == "available"
    assert [(row["project"], row["file"]) for row in scoped] == [
        ("\u00c9QUIPE", "src/\u00c9clair.ts"),
    ]
    unrelated, _ = reader("\u00e9clair", "NONEXISTENT", raw_dir)
    assert unrelated == []


@pytest.mark.parametrize("reader", READERS)
def test_derived_unicode_exact_basename_survives_source_cap(
    reader, raw_dir, monkeypatch,
):
    policy = {**server._symbol_search_policy(), "max_candidate_rows_per_source": 1}
    monkeypatch.setattr(server, "_symbol_search_policy", lambda: policy)
    rows, coverage = reader("\u00e9clair.ts", "MAIN", raw_dir)
    assert coverage["status"] == "available"
    assert [row["file"] for row in rows] == ["src/\u00c9clair.ts"]
    assert rows[0]["search_truncated"] is False


@pytest.mark.parametrize("reader", READERS)
def test_derived_expanding_lower_and_kelvin_path_rank(reader, raw_dir, monkeypatch):
    policy = {**server._symbol_search_policy(), "max_candidate_rows_per_source": 1}
    monkeypatch.setattr(server, "_symbol_search_policy", lambda: policy)
    rows, coverage = reader("i\u0307tem.ts", "MAIN", raw_dir)
    assert coverage["status"] == "available"
    assert [(row["file"], row["match_score"]) for row in rows] == [
        ("src/\u0130tem.ts", 0),
    ]
    assert rows[0]["search_truncated"] is True
    kelvin, _ = reader("kernel", "MAIN", raw_dir)
    assert [row["file"] for row in kelvin] == ["src/\u212aernel.ts"]
