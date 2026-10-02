"""Bounded Unicode casing parity for indexed symbol and file search."""

from __future__ import annotations

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
