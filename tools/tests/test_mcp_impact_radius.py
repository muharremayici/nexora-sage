from __future__ import annotations

import json
import sqlite3

from tools.generate_agent_surface_quality_review import _review_named_sample
from tools.mcp import server


def _sqlite_star(raw_dir, dependent_count: int) -> None:
    with sqlite3.connect(raw_dir / "codemaps.db") as conn:
        conn.executescript(
            """
            CREATE TABLE projects (project_key TEXT PRIMARY KEY, path TEXT);
            CREATE TABLE files (file_id INTEGER PRIMARY KEY, project_key TEXT, rel_path TEXT);
            CREATE TABLE dependencies (source_file_id INTEGER, target_file_id INTEGER);
            INSERT INTO projects (project_key, path) VALUES ('MAIN', '');
            INSERT INTO files (file_id, project_key, rel_path) VALUES (1, 'MAIN', 'target.ts');
            """
        )
        conn.executemany(
            "INSERT INTO files (file_id, project_key, rel_path) VALUES (?, 'MAIN', ?)",
            [(index + 2, f"dependent_{index:03}.ts") for index in range(dependent_count)],
        )
        conn.executemany(
            "INSERT INTO dependencies (source_file_id, target_file_id) VALUES (?, 1)",
            [(index + 2,) for index in range(dependent_count)],
        )


def test_depth_zero_sqlite_is_bounded_target_reachability_not_a_full_graph(
    monkeypatch, tmp_path
) -> None:
    raw_dir = tmp_path / ".raw"
    raw_dir.mkdir()
    _sqlite_star(raw_dir, 205)
    monkeypatch.setattr(
        server,
        "_sqlite_file_context_from_raw",
        lambda _raw, _target: (
            "MAIN::target.ts",
            {"file_id": 1, "repo_relative_path": "target.ts"},
        ),
    )
    monkeypatch.setattr(server, "_target_path_status", lambda *_args, **_kwargs: {"exists": True, "indexed": True})

    payload = server._sqlite_impact_radius_from_raw(raw_dir, "MAIN::target.ts", depth=0)
    assert payload is not None
    assert payload["dependency_graph_source"] == "sqlite_dependencies"
    assert payload["scope_kind"] == "target_reachable_dependents"
    assert payload["scope_completeness"] == "bounded"
    assert payload["scope_limits"] == {
        "traversal_depth_limit": 128,
        "count_depth_limit": 128,
        "returned_transitive_limit": 200,
    }
    assert payload["blast_radius_size"] == 205
    assert payload["returned_scope_size"] == 200
    assert payload["transitive_dependents_omitted"] == 5
    assert "full graph" not in payload["next_depth_hint"].lower()

    brief = server._render_impact_brief(payload)
    assert 'scope_kind: "target_reachable_dependents"' in brief
    assert 'scope_completeness: "bounded"' in brief
    assert "bounded_debug_scope:" in brief
    assert 'full_graph: "not_available"' in brief
    assert "direct_dependents_omitted: 193" in brief
    assert "backend_transitive_rows_omitted: 5" in brief

    monkeypatch.setattr(server, "_raw_dir_for_target", lambda _target_root: raw_dir)
    monkeypatch.setattr(
        server,
        "_record_mcp_call_result",
        lambda _tool, _started, result, **_kwargs: result,
    )
    machine = json.loads(
        server.get_impact_radius(
            "MAIN::target.ts", target_root="bounded-target", format="machine", depth=0
        )
    )
    assert machine["scope_completeness"] == "bounded"
    assert machine["transitive_dependents_omitted"] == 5
    assert 'full_graph: "not_available"' in server.get_impact_radius(
        "MAIN::target.ts", target_root="bounded-target", depth=0
    )

    depth_two = server._sqlite_impact_radius_from_raw(raw_dir, "MAIN::target.ts", depth=2)
    assert depth_two is not None
    review = _review_named_sample(
        {"name": "impact_radius", "body": server._render_impact_brief(depth_two)}
    )
    assert review["checks"]["impact_radius_is_depth_limited_and_path_safe"]


def test_depth_zero_atlas_fallback_is_only_complete_for_loaded_target_graph(
    monkeypatch, tmp_path
) -> None:
    raw_dir = tmp_path / ".raw"
    raw_dir.mkdir()
    (raw_dir / "atlas.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(server, "_sqlite_impact_radius_from_raw", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        server,
        "_resolve_target_node_from_raw",
        lambda _raw, _target: ("MAIN::target.ts", {"repo_relative_path": "target.ts"}),
    )
    monkeypatch.setattr(
        server,
        "_atlas",
        lambda **_kwargs: {
            "MAIN": {
                "files": {
                    "target.ts": {},
                    "dependent.ts": {},
                    "unrelated.ts": {},
                }
            }
        },
    )
    monkeypatch.setattr(
        server,
        "_dependency_graph_from_raw",
        lambda _raw: (
            {"MAIN::target.ts": {}, "MAIN::dependent.ts": {}, "MAIN::unrelated.ts": {}},
            [],
            {"MAIN::target.ts": ["MAIN::dependent.ts"]},
        ),
    )
    monkeypatch.setattr(server, "_dependency_graph_source", lambda _raw: "atlas_imports_fallback")
    monkeypatch.setattr(server, "_precomputed_blast_counts", lambda _raw, _target: {"direct": 999, "transitive": 999})
    monkeypatch.setattr(server, "_target_path_status", lambda *_args, **_kwargs: {"exists": True, "indexed": True})

    payload = server._impact_radius_from_raw(raw_dir, "MAIN::target.ts", depth=0)
    assert payload is not None
    assert payload["scope_kind"] == "target_reachable_dependents"
    assert payload["scope_completeness"] == "complete_within_loaded_graph"
    assert payload["scope_limits"] == {}
    assert payload["blast_radius_size"] == 1
    assert payload["returned_scope_size"] == 1
    assert payload["transitive_dependents"] == ["dependent.ts"]
    assert "unrelated.ts" not in payload["transitive_dependents"]
    assert payload["transitive_dependents_omitted"] == 0


def test_depth_zero_sqlite_zero_omissions_does_not_claim_unbounded_reachability(
    monkeypatch, tmp_path
) -> None:
    raw_dir = tmp_path / ".raw"
    raw_dir.mkdir()
    with sqlite3.connect(raw_dir / "codemaps.db") as conn:
        conn.executescript(
            """
            CREATE TABLE projects (project_key TEXT PRIMARY KEY, path TEXT);
            CREATE TABLE files (file_id INTEGER PRIMARY KEY, project_key TEXT, rel_path TEXT);
            CREATE TABLE dependencies (source_file_id INTEGER, target_file_id INTEGER);
            INSERT INTO projects (project_key, path) VALUES ('MAIN', '');
            INSERT INTO files (file_id, project_key, rel_path) VALUES (1, 'MAIN', 'target.ts');
            """
        )
        conn.executemany(
            "INSERT INTO files (file_id, project_key, rel_path) VALUES (?, 'MAIN', ?)",
            [(index + 2, f"node_{index:03}.ts") for index in range(129)],
        )
        conn.executemany(
            "INSERT INTO dependencies (source_file_id, target_file_id) VALUES (?, ?)",
            [(index + 2, index + 1) for index in range(129)],
        )
    monkeypatch.setattr(
        server,
        "_sqlite_file_context_from_raw",
        lambda _raw, _target: (
            "MAIN::target.ts",
            {"file_id": 1, "repo_relative_path": "target.ts"},
        ),
    )
    monkeypatch.setattr(server, "_target_path_status", lambda *_args, **_kwargs: {"exists": True, "indexed": True})

    payload = server._sqlite_impact_radius_from_raw(raw_dir, "MAIN::target.ts", depth=0)
    assert payload is not None
    assert payload["blast_radius_size"] == 128
    assert payload["returned_scope_size"] == 128
    assert payload["transitive_dependents_omitted"] == 0
    assert payload["scope_completeness"] == "bounded"
    assert payload["scope_limits"]["traversal_depth_limit"] == 128


def test_brief_omissions_count_unshown_rows_not_only_backend_limit() -> None:
    transitive = ["direct.ts", *[f"indirect_{index:03}.ts" for index in range(199)]]
    payload = {
        "target": "MAIN::target.ts",
        "target_ref": "MAIN::target.ts",
        "target_file": "target.ts",
        "target_path_status": {"exists": True, "indexed": True},
        "radius_depth": 0,
        "scope_kind": "target_reachable_dependents",
        "scope_completeness": "bounded",
        "scope_limits": {"traversal_depth_limit": 128, "returned_transitive_limit": 200},
        "blast_radius_size": 205,
        "returned_scope_size": 200,
        "direct_dependents_count": 1,
        "direct_dependents": ["direct.ts"],
        "direct_dependent_refs": ["MAIN::direct.ts"],
        "transitive_dependents": transitive,
        "transitive_dependent_refs": [f"MAIN::{path}" for path in transitive],
        "transitive_dependents_omitted": 5,
    }

    brief = server._render_impact_brief(payload)
    assert "transitive_dependents_shown: 20" in brief
    assert "transitive_dependents_omitted: 184" in brief
    assert "backend_transitive_rows_omitted: 5" in brief
    assert "returned_scope_size: 200" in brief
    assert 'full_graph: "not_available"' in brief


def test_brief_preserves_zero_returned_rows_when_total_is_nonzero() -> None:
    brief = server._render_impact_brief(
        {
            "target": "MAIN::target.ts",
            "target_ref": "MAIN::target.ts",
            "target_file": "target.ts",
            "target_path_status": {"exists": True, "indexed": True},
            "scope_kind": "target_reachable_dependents",
            "scope_completeness": "bounded",
            "blast_radius_size": 5,
            "returned_scope_size": 0,
            "direct_dependents_count": 0,
            "direct_dependents": [],
            "transitive_dependents": [],
            "transitive_dependents_omitted": 5,
        }
    )
    assert "blast_radius_size: 5" in brief
    assert "returned_scope_size: 0" in brief
    assert "backend_transitive_rows_omitted: 5" in brief


def test_default_mcp_path_does_not_run_unscoped_legacy_simulator_when_graph_is_missing(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(server, "_raw_dir_for_target", lambda _target_root: tmp_path)
    monkeypatch.setattr(server, "_impact_radius_from_raw", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        server,
        "_run_python_script",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("legacy simulator must not run")
        ),
    )
    monkeypatch.setattr(
        server,
        "_missing_target_artifact_brief",
        lambda *_args, **_kwargs: "MISSING_GRAPH_EVIDENCE",
    )
    recorded = []
    monkeypatch.setattr(
        server,
        "_record_mcp_call_result",
        lambda _tool, _started, result, **kwargs: (
            recorded.append(kwargs),
            result,
        )[1],
    )

    assert server.get_impact_radius("MAIN::missing.ts", depth=0) == "MISSING_GRAPH_EVIDENCE"
    assert recorded == [
        {"status": "fail_closed", "fail_closed_reason": "missing_target_artifact"}
    ]
