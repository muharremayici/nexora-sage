"""Source caps keep token and prefix matches ahead of weaker substrings."""

from __future__ import annotations

import pytest

from tools.core.artifact_store import ArtifactStore
from tools.mcp import server


@pytest.mark.parametrize(
    ("kind", "weak_name", "strong_name", "expected_score"),
    [
        ("symbol", "retarget", "targetWorker", 3),
        ("symbol", "targetWorker", "pre-target-now", 1),
        ("file", "a-retarget.ts", "z/targeting.ts", 3),
        ("file", "a-targeting.ts", "z-target.ts", 1),
    ],
)
def test_nonexact_quality_survives_source_cap_in_both_backends(
    monkeypatch, tmp_path, kind, weak_name, strong_name, expected_score,
):
    if kind == "symbol":
        files = {
            "src/a.ts": {"hash": "a", "symbols": [{"name": weak_name, "type": "Function", "line": 1}]},
            "src/z.ts": {"hash": "z", "symbols": [{"name": strong_name, "type": "Function", "line": 1}]},
        }
        symbols = [
            {"name": weak_name, "type": "Function", "file": "src/a.ts"},
            {"name": strong_name, "type": "Function", "file": "src/z.ts"},
        ]
    else:
        files = {
            f"src/{weak_name}": {"hash": "a", "symbols": []},
            f"src/{strong_name}": {"hash": "z", "symbols": []},
        }
        symbols = []
    atlas = {"MAIN": {"root_path": ".", "files": files, "symbols": symbols}}
    indexed_dir = tmp_path / "indexed"
    store = ArtifactStore(raw_dir=indexed_dir)
    store.use_sqlite = True
    store.backend = "hybrid_sqlite"
    store._ensure_schema()
    store._save_atlas_to_sqlite(atlas)
    monkeypatch.setattr(server, "_atlas", lambda **kwargs: atlas)
    policy = {**server._symbol_search_policy(), "max_candidate_rows_per_source": 1}
    monkeypatch.setattr(server, "_symbol_search_policy", lambda: policy)

    for raw_dir in (indexed_dir, tmp_path / "no_database"):
        rows = server._find_symbol_matches("target", project="MAIN", raw_dir=raw_dir)
        assert len(rows) == 1
        assert rows[0]["name"] == strong_name.rsplit("/", 1)[-1]
        assert rows[0]["match_score"] == expected_score
        assert rows[0]["search_truncated"] is True
