"""Indexed and Atlas fallback symbol search use literal query text."""

from __future__ import annotations

import json

from tools.core.artifact_store import ArtifactStore
from tools.mcp import server


def _atlas() -> dict:
    files = {
        "src/folder_1/runner.ts": {"hash": "h1", "symbols": [
            {"name": "doThing", "type": "Function", "line": 1},
        ]},
        "src/foo_bar.ts": {"hash": "h2", "symbols": [
            {"name": "foo_bar", "type": "Function", "line": 1},
        ]},
        "src/fooXbar.ts": {"hash": "h3", "symbols": [
            {"name": "fooXbar", "type": "Function", "line": 1},
        ]},
        "src/100%Safe.ts": {"hash": "h4", "symbols": [
            {"name": "percentValue", "type": "Function", "line": 1},
        ]},
        "src/100Safe.ts": {"hash": "h5", "symbols": [
            {"name": "plainValue", "type": "Function", "line": 1},
        ]},
    }
    return {
        "MAIN": {
            "root_path": ".",
            "files": files,
            "symbols": [
                {"name": symbol["name"], "type": symbol["type"], "file": path}
                for path, info in files.items() for symbol in info["symbols"]
            ],
        },
        "COMPANION": {
            "root_path": "companion",
            "files": {"src/foo_bar.ts": {"hash": "other", "workspace_rel": "companion/src/foo_bar.ts", "symbols": [
                {"name": "foo_bar", "type": "Function", "line": 1},
            ]}},
            "symbols": [{"name": "foo_bar", "type": "Function", "file": "src/foo_bar.ts"}],
        },
    }


def _identities(rows: list[dict]) -> set[tuple[str, str, str, str]]:
    return {
        (str(row["project"]), str(row["type"]), str(row["name"]), str(row["file"]))
        for row in rows
    }


def test_literal_metacharacters_and_path_only_symbols_match_both_backends(
    monkeypatch, tmp_path,
) -> None:
    atlas = _atlas()
    indexed_dir = tmp_path / "indexed"
    store = ArtifactStore(raw_dir=indexed_dir)
    store.use_sqlite = True
    store.backend = "hybrid_sqlite"
    store._ensure_schema()
    store._save_atlas_to_sqlite(atlas)
    monkeypatch.setattr(server, "_atlas", lambda **kwargs: atlas)
    fallback_dir = tmp_path / "no_database"

    for query in ("foo_bar", "100%", "folder_1"):
        indexed = server._find_symbol_matches(query, project="MAIN", raw_dir=indexed_dir)
        fallback = server._find_symbol_matches(query, project="MAIN", raw_dir=fallback_dir)
        assert _identities(indexed) == _identities(fallback), query
        assert {row["project"] for row in indexed} == {"MAIN"}
        assert all(query.lower() in row["name"].lower() or query.lower() in row["file"].lower() for row in indexed)

    path_rows = server._find_symbol_matches("folder_1", project="MAIN", raw_dir=fallback_dir)
    assert ("MAIN", "Function", "doThing", "src/folder_1/runner.ts") in _identities(path_rows)
    assert ("MAIN", "Function", "fooXbar", "src/fooXbar.ts") not in _identities(
        server._find_symbol_matches("foo_bar", project="MAIN", raw_dir=indexed_dir)
    )
    assert ("MAIN", "Function", "plainValue", "src/100Safe.ts") not in _identities(
        server._find_symbol_matches("100%", project="MAIN", raw_dir=indexed_dir)
    )
    # A project/workspace prefix is not the SQLite files.rel_path search field.
    companion = server._find_symbol_matches("companion", project="COMPANION", raw_dir=fallback_dir)
    assert not any(row["type"] == "Function" for row in companion)

    monkeypatch.setattr(server, "_raw_dir_for_target", lambda target: indexed_dir)
    public_indexed = json.loads(server.search_symbols(
        "foo_bar", project="MAIN", target_root=str(tmp_path), format="machine",
    ))
    monkeypatch.setattr(server, "_raw_dir_for_target", lambda target: fallback_dir)
    public_fallback = json.loads(server.search_symbols(
        "foo_bar", project="MAIN", target_root=str(tmp_path), format="machine",
    ))
    assert _identities(public_indexed) == _identities(public_fallback)
    assert not any(row["name"] == "fooXbar" for row in public_indexed)
