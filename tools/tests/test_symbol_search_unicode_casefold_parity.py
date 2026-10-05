"""Bounded Unicode casing parity for indexed symbol and file search."""

from __future__ import annotations

import json

import pytest

from tools.core.artifact_store import ArtifactStore
from tools.mcp import server


def _identities(rows: list[dict]) -> set[tuple[str, str, str]]:
    return {
        (str(row["project"]), str(row["type"]), str(row["file"]))
        for row in rows
    }


@pytest.mark.parametrize(
    ("query", "expected_file"),
    [
        ("\u00e9clair", "src/\u00c9clair.ts"),
        ("\u00e9quipe", "src/\u00c9quipe.ts"),
        ("kernel", "src/\u212aernel.ts"),
    ],
)
def test_indexed_and_atlas_fallback_unicode_lower_parity(
    monkeypatch, tmp_path, query: str, expected_file: str,
) -> None:
    files = {
        "src/\u00c9clair.ts": {"hash": "one", "symbols": [
            {"name": "\u00c9clair", "type": "Function", "line": 1},
        ]},
        "src/\u00c9quipe.ts": {"hash": "two", "symbols": [
            {"name": "unrelated", "type": "Function", "line": 1},
        ]},
        "src/\u212aernel.ts": {"hash": "three", "symbols": [
            {"name": "unrelated", "type": "Function", "line": 1},
        ]},
        "src/Equipe.ts": {"hash": "control", "symbols": [
            {"name": "control", "type": "Function", "line": 1},
        ]},
    }
    atlas = {"MAIN": {
        "root_path": ".",
        "files": files,
        "symbols": [
            {"name": symbol["name"], "type": symbol["type"], "file": path}
            for path, info in files.items() for symbol in info["symbols"]
        ],
    }, "\u00c9QUIPE": {
        "root_path": "companion",
        "files": {"src/\u00c9clair.ts": {"hash": "companion", "symbols": [
            {"name": "\u00c9clair", "type": "Function", "line": 1},
        ]}},
        "symbols": [{"name": "\u00c9clair", "type": "Function",
                     "file": "src/\u00c9clair.ts"}],
    }}
    indexed_dir = tmp_path / "indexed"
    store = ArtifactStore(raw_dir=indexed_dir)
    store.use_sqlite = True
    store.backend = "hybrid_sqlite"
    store._ensure_schema()
    store._save_atlas_to_sqlite(atlas)
    monkeypatch.setattr(server, "_atlas", lambda **kwargs: atlas)

    indexed = server._find_symbol_matches(query, project="MAIN", raw_dir=indexed_dir)
    fallback = server._find_symbol_matches(query, project="MAIN", raw_dir=tmp_path / "no_db")
    assert _identities(indexed) == _identities(fallback)
    assert any(row["file"] == expected_file for row in indexed)
    assert all(row["file"] != "src/Equipe.ts" for row in indexed)
    if query == "\u00e9clair":
        scoped_indexed = server._find_symbol_matches(
            query, project="\u00e9quipe", raw_dir=indexed_dir)
        scoped_fallback = server._find_symbol_matches(
            query, project="\u00e9quipe", raw_dir=tmp_path / "no_db")
        assert _identities(scoped_indexed) == _identities(scoped_fallback)
        assert {row["project"] for row in scoped_indexed} == {"\u00c9QUIPE"}


@pytest.mark.parametrize(
    ("project_key", "query_project"),
    [
        ("\u00c9QUIPE", "\u00e9quipe"),
        ("\u212aERNEL", "kernel"),
        ("\u0130STANBUL", "i\u0307stanbul"),
        ("MAIN", "main"),
    ],
)
@pytest.mark.parametrize("path_prefix", ["", "companion/"])
def test_file_context_project_lookup_matches_python_lower_without_scope_leak(
    tmp_path, monkeypatch, project_key: str, query_project: str, path_prefix: str,
) -> None:
    atlas = {
        project_key: {
            "root_path": "companion",
            "files": {"src/a.ts": {"hash": "selected", "symbols": []}},
            "symbols": [],
        },
        "OTHER": {
            "root_path": "other",
            "files": {"src/a.ts": {"hash": "control", "symbols": []}},
            "symbols": [],
        },
    }
    raw_dir = tmp_path / "analysis" / ".raw"
    store = ArtifactStore(raw_dir=raw_dir)
    store.use_sqlite = True
    store.backend = "hybrid_sqlite"
    store._ensure_schema()
    store._save_atlas_to_sqlite(atlas)

    exact = server._sqlite_file_context_from_raw(raw_dir, f"{project_key}::src/a.ts")
    matched = server._sqlite_file_context_from_raw(
        raw_dir, f"{query_project}::{path_prefix}src/a.ts")
    assert exact is not None
    assert matched == exact
    assert matched[0] == f"{project_key}::src/a.ts"
    assert matched[1]["repo_relative_path"] == "companion/src/a.ts"
    other = server._sqlite_file_context_from_raw(raw_dir, "OTHER::src/a.ts")
    assert other is not None
    assert matched[1]["file_id"] != other[1]["file_id"]
    assert server._sqlite_file_context_from_raw(
        raw_dir, f"{query_project}_missing::src/a.ts") is None
    assert server._sqlite_file_context_from_raw(
        raw_dir, f"{query_project}::src/A.ts") is None
    monkeypatch.setattr(server, "_raw_dir_for_target", lambda *args: raw_dir)
    monkeypatch.setattr(server, "_reports_dir_for_target", lambda *args: tmp_path / "reports")
    payload = json.loads(server.inspect_file(
        file_path=f"{query_project}::{path_prefix}src/a.ts", format="machine"))
    assert payload["target_file_context"][0]["atlas_node"] == matched[0]
    assert payload["target_file_context"][0]["repo_relative_path"] == "companion/src/a.ts"
    # Indexed path identity is not proof of unavailable fixture source content.
    assert not payload.get("one_shot_edit_ready")


def test_file_context_prefers_exact_project_and_rejects_ambiguous_lowercase(tmp_path):
    project_keys = ["\u00c9QUIPE", "\u00e9quipe"]
    atlas = {
        key: {"root_path": f"companion-{index}",
              "files": {"src/a.ts": {"hash": str(index), "symbols": []}},
              "symbols": []}
        for index, key in enumerate(project_keys)
    }
    raw_dir = tmp_path / "analysis" / ".raw"
    store = ArtifactStore(raw_dir=raw_dir)
    store.use_sqlite = True
    store.backend = "hybrid_sqlite"
    store._ensure_schema()
    store._save_atlas_to_sqlite(atlas)

    file_ids = set()
    for index, key in enumerate(project_keys):
        matched = server._sqlite_file_context_from_raw(raw_dir, f"{key}::src/a.ts")
        assert matched is not None
        assert matched[0] == f"{key}::src/a.ts"
        assert matched[1]["repo_relative_path"] == f"companion-{index}/src/a.ts"
        file_ids.add(matched[1]["file_id"])
    assert len(file_ids) == 2
    assert server._sqlite_file_context_from_raw(raw_dir, "\u00e9QuiPE::src/a.ts") is None
