"""Atlas fallback file eligibility uses indexed paths, not display prefixes."""

from __future__ import annotations

import json

from tools.core.artifact_store import ArtifactStore
from tools.mcp import server


def _fixture():
    return {
        "MAIN": {
            "root_path": ".",
            "files": {"src/main.ts": {"hash": "main", "symbols": []}},
            "symbols": [],
        },
        "COMPANION": {
            "root_path": "packages/companion",
            "files": {
                "src/target.ts": {
                    "hash": "target",
                    "workspace_rel": "packages/companion/src/target.ts",
                    "symbols": [],
                }
            },
            "symbols": [],
        },
    }


def test_display_only_workspace_prefix_does_not_create_file_match(monkeypatch, tmp_path):
    atlas = _fixture()
    indexed_dir = tmp_path / "indexed"
    store = ArtifactStore(raw_dir=indexed_dir)
    store.use_sqlite = True
    store.backend = "hybrid_sqlite"
    store._ensure_schema()
    store._save_atlas_to_sqlite(atlas)
    monkeypatch.setattr(server, "_atlas", lambda **kwargs: atlas)

    for raw_dir in (indexed_dir, tmp_path / "no_database"):
        assert server._find_symbol_matches(
            "packages/companion", project="COMPANION", raw_dir=raw_dir,
        ) == []
        assert server._find_symbol_matches(
            "packages/companion/src/target.ts", project="COMPANION", raw_dir=raw_dir,
        ) == []
        rows = server._find_symbol_matches(
            "src/target.ts", project="COMPANION", raw_dir=raw_dir,
        )
        assert [(row["project"], row["type"], row["file"]) for row in rows] == [
            ("COMPANION", "File", "src/target.ts")
        ]
        assert rows[0]["repo_relative_path"] == "packages/companion/src/target.ts"
        assert rows[0]["search_truncated"] is False


def test_literal_workspace_prefix_is_not_a_cross_project_file_hit(monkeypatch, tmp_path):
    atlas = _fixture()
    monkeypatch.setattr(server, "_atlas", lambda **kwargs: atlas)
    assert server._find_symbol_matches("companion", project="all", raw_dir=tmp_path) == []


def test_public_search_preserves_openable_path_without_searching_display_prefix(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "_atlas", lambda **kwargs: _fixture())
    monkeypatch.setattr(server, "_raw_dir_for_target", lambda target: tmp_path)
    absent = server.search_symbols(
        "packages/companion", project="COMPANION", target_root=str(tmp_path), format="machine",
    )
    assert absent.startswith("No symbols found matching")
    rows = json.loads(server.search_symbols(
        "src/target.ts", project="COMPANION", target_root=str(tmp_path), format="machine",
    ))
    assert [(row["type"], row["file"], row["repo_relative_path"]) for row in rows] == [
        ("File", "src/target.ts", "packages/companion/src/target.ts")
    ]
