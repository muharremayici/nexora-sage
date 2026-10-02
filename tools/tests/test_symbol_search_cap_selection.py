"""Exact symbol and file candidates survive source caps in both search backends."""

from __future__ import annotations

import json

from tools.core.artifact_store import ArtifactStore
from tools.mcp import server


def _indexed_atlas(raw_dir, atlas):
    store = ArtifactStore(raw_dir=raw_dir)
    store.use_sqlite = True
    store.backend = "hybrid_sqlite"
    store._ensure_schema()
    store._save_atlas_to_sqlite(atlas)


def _search(monkeypatch, tmp_path, atlas, query):
    policy = {**server._symbol_search_policy(), "max_candidate_rows_per_source": 1}
    monkeypatch.setattr(server, "_symbol_search_policy", lambda: policy)
    monkeypatch.setattr(server, "_atlas", lambda **kwargs: atlas)
    indexed_dir = tmp_path / "indexed"
    _indexed_atlas(indexed_dir, atlas)
    indexed = server._find_symbol_matches(query, project="MAIN", raw_dir=indexed_dir)
    fallback = server._find_symbol_matches(query, project="MAIN", raw_dir=tmp_path / "no_database")
    return indexed, fallback


def test_exact_symbol_beats_earlier_substring_before_source_cap(monkeypatch, tmp_path):
    atlas = {
        "MAIN": {
            "root_path": ".",
            "files": {
                "src/a.ts": {"hash": "a", "symbols": [{"name": "targetOther", "type": "Function", "line": 1}]},
                "src/z.ts": {"hash": "z", "symbols": [{"name": "target", "type": "Function", "line": 1}]},
            },
            "symbols": [
                {"name": "targetOther", "type": "Function", "file": "src/a.ts"},
                {"name": "target", "type": "Function", "file": "src/z.ts"},
            ],
        }
    }
    indexed, fallback = _search(monkeypatch, tmp_path, atlas, "target")
    for rows in (indexed, fallback):
        symbols = [row for row in rows if row["type"] == "Function"]
        assert [row["name"] for row in symbols] == ["target"]
        assert symbols[0]["match_score"] == 0
        assert all(row["search_truncated"] is True for row in rows)


def test_exact_file_basename_beats_earlier_substring_before_source_cap(monkeypatch, tmp_path):
    atlas = {
        "MAIN": {
            "root_path": ".",
            "files": {
                "src/target.ts-copy": {"hash": "copy", "symbols": []},
                "src/target.ts": {"hash": "target", "symbols": []},
            },
            "symbols": [],
        }
    }
    indexed, fallback = _search(monkeypatch, tmp_path, atlas, "target.ts")
    for rows in (indexed, fallback):
        assert [(row["type"], row["file"]) for row in rows] == [("File", "src/target.ts")]
        assert rows[0]["match_score"] == 0
        assert rows[0]["search_truncated"] is True


def test_exact_filename_beats_same_scored_backup_stem_across_directories(monkeypatch, tmp_path):
    atlas = {
        "MAIN": {
            "root_path": ".",
            "files": {
                "src/a/not-target.ts": {"hash": "suffix", "symbols": []},
                "src/a/target.ts.backup": {"hash": "backup", "symbols": []},
                "src/z/target.ts": {"hash": "target", "symbols": []},
            },
            "symbols": [],
        }
    }
    indexed, fallback = _search(monkeypatch, tmp_path, atlas, "target.ts")
    for rows in (indexed, fallback):
        assert [(row["type"], row["file"]) for row in rows] == [("File", "src/z/target.ts")]
        assert rows[0]["search_truncated"] is True


def test_nonexact_file_tie_is_deterministic_across_backends(monkeypatch, tmp_path):
    atlas = {
        "MAIN": {
            "root_path": ".",
            "files": {
                "src/z-match.ts": {"hash": "z", "symbols": []},
                "src/a-match.ts": {"hash": "a", "symbols": []},
            },
            "symbols": [],
        }
    }
    indexed, fallback = _search(monkeypatch, tmp_path, atlas, "match")
    for rows in (indexed, fallback):
        assert [(row["type"], row["file"]) for row in rows] == [("File", "src/a-match.ts")]
        assert rows[0]["search_truncated"] is True


def test_exact_indexed_path_is_not_demoted_by_companion_display_prefix(monkeypatch, tmp_path):
    atlas = {
        "COMPANION": {
            "root_path": "packages/companion",
            "files": {
                "src/target.ts-copy": {"hash": "copy", "workspace_rel": "packages/companion/src/target.ts-copy", "symbols": []},
                "src/target.ts": {"hash": "exact", "workspace_rel": "packages/companion/src/target.ts", "symbols": []},
            },
            "symbols": [],
        }
    }
    policy = {**server._symbol_search_policy(), "max_candidate_rows_per_source": 1}
    monkeypatch.setattr(server, "_symbol_search_policy", lambda: policy)
    monkeypatch.setattr(server, "_atlas", lambda **kwargs: atlas)
    indexed_dir = tmp_path / "indexed"
    _indexed_atlas(indexed_dir, atlas)
    for raw_dir in (indexed_dir, tmp_path / "no_database"):
        rows = server._find_symbol_matches("src/target.ts", project="COMPANION", raw_dir=raw_dir)
        assert [(row["file"], row["match_score"]) for row in rows] == [("src/target.ts", 0)]
        assert rows[0]["repo_relative_path"] == "packages/companion/src/target.ts"
        assert rows[0]["search_truncated"] is True


def test_public_merge_keeps_exact_file_ahead_of_same_scored_derived_candidate(monkeypatch, tmp_path):
    backup = {
        "name": "target.ts.backup", "file": "src/a/target.ts.backup",
        "repo_relative_path": "src/a/target.ts.backup", "project": "MAIN",
        "type": "File", "match_score": 0, "search_truncated": False,
    }
    exact = {
        "name": "target.ts", "file": "src/z/target.ts",
        "repo_relative_path": "src/z/target.ts", "project": "MAIN",
        "type": "File", "match_score": 0, "search_truncated": False,
    }
    derived = {
        "name": "other.target.ts", "file": "src/m/other.target.ts",
        "repo_relative_path": "src/m/other.target.ts", "project": "MAIN",
        "type": "ClassMethod", "match_score": 4, "search_truncated": False,
    }
    monkeypatch.setattr(server, "_find_symbol_matches", lambda *args, **kwargs: [exact, backup])
    monkeypatch.setattr(server, "_find_class_method_search_matches", lambda *args, **kwargs: ([derived], {"status": "available"}))
    monkeypatch.setattr(server, "_find_store_action_search_matches", lambda *args, **kwargs: ([], {"status": "unavailable"}))
    monkeypatch.setattr(server, "_raw_dir_for_target", lambda target: tmp_path)
    rows = json.loads(server.search_symbols("target.ts", target_root=str(tmp_path), format="machine"))
    assert [row["file"] for row in rows] == ["src/z/target.ts", "src/a/target.ts.backup", "src/m/other.target.ts"]


def test_exact_relative_path_is_scored_before_substring_and_empty_query_is_not_exact():
    assert server._symbol_search_match_score("src/target.ts", "target.ts", "src/target.ts")[0] == 0
    assert server._symbol_search_match_score("target.ts", "target.ts-copy", "src/target.ts-copy")[0] == 3
    assert server._symbol_search_match_score("", "", "")[0] != 0
