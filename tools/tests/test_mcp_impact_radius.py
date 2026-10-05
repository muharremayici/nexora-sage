from __future__ import annotations

import copy
import json
import sqlite3

import pytest

from tools.generate_agent_surface_quality_review import _review_named_sample
from tools.mcp import server


def _compact_status():
    spans = [{'symbol': 'meta', 'start_line': 0, 'end_line': 0, 'source_lines': '',
              'line_status': 'not_available'}]
    spans.extend({'symbol': symbol, 'start_line': line, 'end_line': line,
                  'source_lines': f'L{line}-L{line}', 'line_status': 'available'}
                 for symbol, line in [('First', 2), ('Alias', 2), ('Second', 4)])
    return {'exists': True, 'indexed': True, 'target_ref': 'MAIN::sample.ts',
            'source_snapshot_status': 'ok', 'drift_check_status': 'match',
            'target_span_count': len(spans), 'target_spans': spans,
            'target_source_snippets': server._bounded_source_snippets('header\nfirst\ngap\nsecond', spans)}


@pytest.mark.parametrize('cap', [0, 1, 2])
def test_compact_selection_uses_one_scope_with_deduplicated_ranges(cap):
    status = _compact_status()
    before = copy.deepcopy(status)
    compact = server._bounded_source_grounding_for_agent(status, max_spans=cap)
    expected = ['First', 'Second'][:cap]
    assert [row['symbol'] for row in compact['target_spans']] == expected
    assert [row['symbol'] for row in compact['target_source_snippets']] == expected
    assert compact['target_spans_shown'] == compact['target_source_snippets_shown'] == cap
    assert compact['target_spans_omitted'] == len(status['target_spans']) - cap
    assert compact['target_source_snippets_omitted'] == len(status['target_source_snippets']) - cap
    body = '\n'.join(server._source_grounding_yaml_lines(status, max_spans=cap))
    assert f'target_spans_shown: {cap}' in body
    assert f'target_source_snippets_shown: {cap}' in body
    assert 'symbol: "meta"' not in body and 'symbol: "Alias"' not in body
    for symbol in expected:
        assert body.count(f'symbol: "{symbol}"') == 2
    assert status == before


@pytest.mark.parametrize('case', ['wrong_range', 'wrong_symbol', 'reordered', 'missing', 'no_span',
                                'later_pair', 'alias_pair'])
def test_compact_selection_does_not_pair_unrelated_source(case):
    status = _compact_status()
    if case == 'wrong_range':
        status['target_source_snippets'] = [{**status['target_source_snippets'][0], 'source_lines': 'L9-L9'}]
    elif case == 'wrong_symbol':
        status['target_source_snippets'] = [{**status['target_source_snippets'][0], 'symbol': 'Other'}]
    elif case == 'reordered':
        status['target_source_snippets'].reverse()
    elif case == 'missing':
        status.update(source_snapshot_status='missing', drift_check_status='not_available', target_source_snippets=[])
    elif case == 'later_pair':
        status['target_source_snippets'] = status['target_source_snippets'][1:]
    elif case == 'alias_pair':
        status['target_source_snippets'] = [{**status['target_source_snippets'][0], 'symbol': 'Alias'}]
    else:
        status.update(target_spans=[], target_span_count=0,
                      target_source_snippets=server._bounded_source_snippets('header\nfirst', []))
    compact = server._bounded_source_grounding_for_agent(status, max_spans=1)
    if case in {'wrong_range', 'wrong_symbol', 'missing'}:
        assert compact['target_source_snippets'] == []
        assert compact['target_source_snippets_omitted'] == len(status['target_source_snippets'])
        assert compact['target_spans'][0]['symbol'] == 'First'
    elif case == 'reordered':
        assert compact['target_spans'][0]['symbol'] == compact['target_source_snippets'][0]['symbol'] == 'First'
    elif case in {'later_pair', 'alias_pair'}:
        expected = 'Second' if case == 'later_pair' else 'Alias'
        assert compact['target_spans'][0]['symbol'] == compact['target_source_snippets'][0]['symbol'] == expected
    else:
        assert compact['target_spans'] == []
        assert compact['target_source_snippets'][0]['snippet_status'] == 'included_no_symbol_span'
    assert compact['source_snapshot_status'] == status['source_snapshot_status']
    assert compact['drift_check_status'] == status['drift_check_status']


@pytest.mark.parametrize('budget', [20, 4000])
def test_compact_selection_preserves_partial_and_omitted_context(budget):
    span = {'symbol': 'Large', 'start_line': 1, 'end_line': 160, 'source_lines': 'L1-L160',
            'line_status': 'available', 'target_ref': 'MAIN::sample.ts'}
    snippets = server._bounded_source_snippets('\n'.join('body' for _ in range(160)),
                                               [span], max_total_chars=budget)
    status = {**_compact_status(), 'target_spans': [span], 'target_span_count': 1,
              'target_source_snippets': snippets}
    compact = server._bounded_source_grounding_for_agent(status)
    assert compact['target_spans'] == [span] and compact['target_source_snippets'] == snippets
    body = '\n'.join(server._source_grounding_yaml_lines(status, max_spans=1))
    if budget == 20:
        assert snippets[0]['snippet_status'] == 'omitted_context_budget' and 'code' not in snippets[0]
        assert 'omitted_context_budget' in body
    else:
        assert snippets[0]['one_shot_edit_ready'] is False
        assert snippets[0]['shown_lines'] == 40 and snippets[0]['omitted_lines'] == 120
        assert 'one_shot_edit_ready: false' in body and 'do_not_edit_omitted_lines_without_follow_up' in body


@pytest.mark.parametrize('end,expected_status', [(3, 'available'), (999, 'clamped_to_file_bounds')])
def test_compact_selection_preserves_explicit_inspection_range(tmp_path, monkeypatch, end, expected_status):
    from tools.tests.test_target_absolute_root_workspace_projection import _sample
    _sample(tmp_path, monkeypatch, True)
    payload = json.loads(server.inspect_file('OTHER::consumer.ts', target_root=str(tmp_path),
                        line_start=3, line_end=end, format='machine'))
    status = payload['target_path_status']
    compact = server._bounded_source_grounding_for_agent(status)
    assert compact['target_spans'] == status['target_spans']
    assert compact['target_spans'][0]['symbol'] == 'requested_line_range'
    assert compact['target_spans'][0]['line_status'] == expected_status
    assert compact['target_source_snippets'] == status['target_source_snippets']
    assert payload['one_shot_edit_ready'] is (expected_status == 'available')


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
