"""Search display caps are not evidence of a complete indexed candidate set."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from tools.mcp import server


def _rows(count: int, *, source_truncated: bool = False) -> list[dict]:
    return [
        {
            "name": "aiService",
            "type": "Function",
            "project": "MAIN" if index % 2 == 0 else "COMPANION",
            "file": f"src/module_{index}/aiService.ts",
            "repo_relative_path": f"src/module_{index}/aiService.ts",
            "atlas_node": f"{'MAIN' if index % 2 == 0 else 'COMPANION'}::src/module_{index}/aiService.ts",
            "match_score": 0,
            "search_truncated": source_truncated,
        }
        for index in range(count)
    ]


def _brief_payload(text: str) -> dict:
    # Parse only this brief's flat JSON-valued fields; no extra YAML dependency.
    payload = {"task": {}, "matches": [], "do_not": []}
    section = ""
    for line in text.split("```yaml", 1)[1].split("```", 1)[0].splitlines():
        if line and not line.startswith(" "):
            section = line.rstrip(":")
        elif section == "task" and ": " in line:
            key, value = line.strip().split(": ", 1)
            payload["task"][key] = json.loads(value)
        elif section == "matches" and ": " in line:
            key, value = line.strip().split(": ", 1)
            if key.startswith("- "):
                payload["matches"].append({})
                key = key[2:]
            payload["matches"][-1][key] = json.loads(value)
        elif section == "do_not" and line.startswith("  - "):
            payload["do_not"].append(line[4:])
    return payload


@pytest.mark.parametrize("count", [0, 1, 10, 11, 25])
@pytest.mark.parametrize("source_truncated", [False, True])
def test_brief_reports_display_and_source_caps_independently(monkeypatch, tmp_path, count, source_truncated):
    rows = _rows(count, source_truncated=source_truncated)
    monkeypatch.setattr(server, "_raw_dir_for_target", lambda target: tmp_path)
    monkeypatch.setattr(server, "_find_symbol_matches", lambda *args, **kwargs: rows)
    payload = _brief_payload(server.search_symbols("aiService", project="all", target_root=str(tmp_path)))
    task = payload["task"]
    shown = min(count, 10)
    assert task["returned"] == task["shown"] == shown
    assert task["candidate_count"] == count
    assert task["omitted"] == count - shown
    assert task["display_truncated"] is (count > shown)
    assert task["search_truncated"] is bool(count and source_truncated)
    assert task["candidate_count_semantics"] == ("lower_bound" if count and source_truncated else "complete_indexed_match_set")
    assert task["returned_count_semantics"] == ("bounded_display" if count > shown else "lower_bound" if count and source_truncated else "complete_match_set")
    assert task["search_scope"] == "indexed_symbols_files_and_derived_syntax_not_source_tree"
    assert len(payload["matches"]) == shown
    assert "Do not edit from search results alone." in payload["do_not"]
    for row, original in zip(payload["matches"], rows):
        assert row["target_ref"] == original["atlas_node"]
    assert all("shown" not in row for row in rows), "Rendering must not mutate the collected search evidence"


@pytest.mark.parametrize("format", ["json", "machine"])
@pytest.mark.parametrize("count,source_truncated", [(1, False), (20, False), (21, False), (25, True)])
def test_machine_list_preserves_identity_and_discloses_omissions(monkeypatch, tmp_path, format, count, source_truncated):
    rows = _rows(count, source_truncated=source_truncated)
    monkeypatch.setattr(server, "_raw_dir_for_target", lambda target: tmp_path)
    monkeypatch.setattr(server, "_find_symbol_matches", lambda *args, **kwargs: rows)
    result = json.loads(server.search_symbols("aiService", project="all", target_root=str(tmp_path), format=format))
    assert isinstance(result, list), "Keep the existing machine-list compatibility"
    assert len(result) == min(count, 20)
    for original, row in zip(rows, result):
        assert row["atlas_node"] == original["atlas_node"]
        assert row["repo_relative_path"] == original["repo_relative_path"]
        assert row["candidate_count"] == count
        assert row["shown"] == len(result)
        assert row["omitted"] == count - len(result)
        assert row["display_truncated"] is (count > len(result))
        assert row["search_truncated"] is source_truncated
        assert row["candidate_count_semantics"] == ("lower_bound" if source_truncated else "complete_indexed_match_set")
        assert row["returned_count_semantics"] == ("bounded_display" if count > len(result) else "lower_bound" if source_truncated else "complete_match_set")
    assert all("shown" not in row for row in rows)


def test_display_limits_are_owned_by_the_shared_contract(monkeypatch):
    contract = server._agent_surface_contract()
    policy = {**contract["symbol_search_policy"], "brief_max_visible_items": 2, "machine_max_visible_items": 3}
    monkeypatch.setattr(server, "_agent_surface_contract", lambda: {**contract, "symbol_search_policy": policy})
    monkeypatch.setattr(server, "_raw_dir_for_target", lambda target: Path("isolated"))
    monkeypatch.setattr(server, "_find_symbol_matches", lambda *args, **kwargs: _rows(5))
    brief = _brief_payload(server.search_symbols("aiService"))
    machine = json.loads(server.search_symbols("aiService", format="json"))
    assert brief["task"]["shown"] == 2
    assert brief["task"]["omitted"] == 3
    assert len(machine) == 3
    assert machine[0]["omitted"] == 2


def test_sqlite_duplicate_basenames_survive_collection_beyond_brief_cap(monkeypatch):
    from tools.core.artifact_store import ArtifactStore
    from tools.core.db import SQLiteManager

    # Avoid Windows long-path fixture failures; this store is not live SAGE truth.
    with tempfile.TemporaryDirectory(prefix="sage-search-") as temp:
        raw_dir = Path(temp)
        store = ArtifactStore(raw_dir=raw_dir)
        store.use_sqlite = True
        store.backend = "hybrid_sqlite"
        store.db_manager = SQLiteManager(raw_dir / "codemaps.db")
        store._schema_initialized = False
        store._ensure_schema()
        atlas = {
            project: {
                "root_path": ".",
                "files": {f"src/module_{index}/aiService.ts": {"hash": "fixture", "symbols": [
                    {"name": "aiService", "type": "Function", "line": 1}
                ]} for index in indexes},
            }
            for project, indexes in [("MAIN", range(13)), ("COMPANION", range(13, 15))]
        }
        store._save_atlas_to_sqlite(atlas)
        monkeypatch.setattr(server, "_raw_dir_for_target", lambda target: raw_dir)
        main_rows = server._find_symbol_matches("aiService", project="MAIN", raw_dir=raw_dir)
        assert len(main_rows) == 26  # Distinct file and symbol candidates, not 26 files.
        assert {row["project"] for row in main_rows} == {"MAIN"}
        assert len({row["atlas_node"] for row in main_rows}) == 13
        payload = _brief_payload(server.search_symbols("aiService", target_root=str(raw_dir)))
        assert payload["task"]["candidate_count"] == 26
        assert payload["task"]["shown"] == 10
        assert payload["task"]["omitted"] == 16
        assert payload["task"]["search_truncated"] is False
        all_rows = server._find_symbol_matches("aiService", project="all", raw_dir=raw_dir)
        assert len(all_rows) == 30
        assert len({row["atlas_node"] for row in all_rows}) == 15
        exact = server._find_symbol_matches("src/module_12/aiService.ts", project="MAIN", raw_dir=raw_dir)
        assert {row["repo_relative_path"] for row in exact} == {"src/module_12/aiService.ts"}
        policy = {**server._symbol_search_policy(), "max_candidate_rows_per_source": 2}
        monkeypatch.setattr(server, "_symbol_search_policy", lambda: policy)
        capped = _brief_payload(server.search_symbols("aiService", target_root=str(raw_dir)))
        assert capped["task"]["candidate_count"] == 4
        assert capped["task"]["omitted"] == 0
        assert capped["task"]["display_truncated"] is False
        assert capped["task"]["search_truncated"] is True
        assert capped["task"]["candidate_count_semantics"] == "lower_bound"


@pytest.mark.parametrize("project", ["*", "all", "ANY", " main ", "missing"])
def test_atlas_fallback_uses_same_project_scope_and_source_caps(monkeypatch, tmp_path, project):
    atlas = {
        name: {"root_path": ".", "symbols": [
            {"name": "aiService", "type": "Function", "file": f"src/{index}/aiService.ts"}
            for index in range(3)
        ], "files": {f"src/{index}/aiService.ts": {"workspace_rel": f"src/{index}/aiService.ts"} for index in range(3)}}
        for name in ["MAIN", "COMPANION"]
    }
    policy = {**server._symbol_search_policy(), "max_candidate_rows_per_source": 4}
    monkeypatch.setattr(server, "_symbol_search_policy", lambda: policy)
    monkeypatch.setattr(server, "_atlas", lambda **kwargs: atlas)
    monkeypatch.setattr(server, "_raw_dir_for_target", lambda target: tmp_path)
    rows = server._find_symbol_matches("aiService", project=project, raw_dir=tmp_path)
    expected = 0 if project == "missing" else 6 if project.strip().lower() == "main" else 8
    assert len(rows) == expected
    assert {row["project"] for row in rows} == (set() if not rows else {"MAIN"} if project.strip().lower() == "main" else {"MAIN", "COMPANION"})
    brief = _brief_payload(server.search_symbols("aiService", project=project, target_root=str(tmp_path)))
    assert brief["task"]["candidate_count"] == expected
    assert brief["task"]["shown"] == expected
    assert brief["task"]["search_truncated"] is (expected == 8)


@pytest.mark.parametrize("bad_value", [None, 0, -1, True, "10"])
def test_invalid_or_missing_display_policy_fails_closed(monkeypatch, bad_value):
    contract = server._agent_surface_contract()
    policy = {**contract["symbol_search_policy"], "brief_max_visible_items": bad_value}
    if bad_value is None:
        del policy["brief_max_visible_items"]
    monkeypatch.setattr(server, "_agent_surface_contract", lambda: {**contract, "symbol_search_policy": policy})
    with pytest.raises(ValueError, match="brief_max_visible_items"):
        server._symbol_search_policy()


def test_prefix_quality_is_not_mislabeled_as_arbitrary_substring():
    rows = _rows(1)
    rows[0]["match_score"] = 3
    brief = _brief_payload(server._render_symbol_search_brief("ai", rows, analysis_root="isolated"))
    assert brief["matches"][0]["match_quality"] == "prefix"


def test_invalid_external_target_does_not_borrow_workspace_results(monkeypatch):
    def invalid(target):
        raise ValueError("invalid external root")
    monkeypatch.setattr(server, "_raw_dir_for_target", invalid)
    monkeypatch.setattr(server, "_find_symbol_matches", lambda *args, **kwargs: pytest.fail("Cross-target borrowing"))
    assert "invalid_external_target" in server.search_symbols("aiService", target_root="missing")
