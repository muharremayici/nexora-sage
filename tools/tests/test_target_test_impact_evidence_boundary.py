from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tools.engines.test_impact_matcher import find_impacted_tests
from tools.mcp import server


def _static_parity_inputs():
    atlas, graph = _inputs()
    atlas["OTHER"] = {"files": {"src/load.test.ts": {}, "src/unrelated.test.ts": {}}}
    graph["nodes"].update({f"OTHER::{path}": {} for path in atlas["OTHER"]["files"]})
    graph["edges"].extend([
        {"source": "OTHER::src/load.test.ts", "target": "MAIN::src/view.ts"},
        {"source": "MAIN::src/load.ts", "target": "MAIN::src/view.ts"},
    ])
    return atlas, graph


def _external_static_projection(tmp_path, atlas, graph):
    with (
        patch.object(server, "load_atlas_data", return_value=atlas),
        patch.object(server, "_atlas", return_value=atlas),
        patch.object(server, "_load_json", return_value=graph),
        patch.object(server, "_resolve_target_node_from_raw", return_value=(
            "MAIN::src/load.ts", {"project": "MAIN", "repo_relative_path": "src/load.ts"})),
        patch.object(server, "_target_path_status", return_value={"exists": True, "indexed": True}),
        patch.object(server, "_repo_relative_from_node", side_effect=lambda _, node: node.split("::", 1)[1]),
        patch.object(server, "_target_ref_from_node", side_effect=lambda _, node: node),
        patch.object(server, "_analysis_root_display", return_value=str(tmp_path)),
        patch.object(server, "_direct_colocated_test_candidates", return_value=[]),
    ):
        return server._normalize_test_impact_payload_for_agent(
            server._test_impact_from_raw(tmp_path / ".raw", "MAIN::src/load.ts", target_root=str(tmp_path)))


@pytest.mark.parametrize("reverse_order", [False, True])
def test_external_static_graph_parity_uses_existing_transitive_and_naming_matcher(tmp_path, reverse_order):
    atlas, graph = _static_parity_inputs()
    if reverse_order:
        graph["edges"].reverse()
    expected = find_impacted_tests("MAIN::src/load.ts", atlas=atlas, circular_deps=graph)
    result = _external_static_projection(tmp_path, atlas, graph)
    fields = ("atlas_node", "project", "type", "confidence", "static_relation", "candidate_evidence", "run_command")
    assert [{key: row.get(key) for key in fields} for row in result["impacted_tests"]] == [
        {key: row.get(key) for key in fields} for row in expected["impacted_tests"]]
    indexed = {row["atlas_node"]: row for row in result["impacted_tests"]}
    _assert_static_boundary(indexed["MAIN::src/load.test.ts"], "direct_import")
    _assert_static_boundary(indexed["MAIN::src/view.test.ts"], "transitive_dependency")
    _assert_static_boundary(indexed["OTHER::src/load.test.ts"], "transitive_dependency")
    _assert_static_boundary(indexed["MAIN::other/load.test.ts"], "naming_only")
    assert "OTHER::src/unrelated.test.ts" not in indexed
    assert indexed["OTHER::src/load.test.ts"]["confidence"] == 0.6


@pytest.mark.parametrize("dependency", [None, {}, []])
def test_external_static_graph_parity_missing_graph_never_reads_host_or_atlas_fallback(tmp_path, dependency):
    atlas, _graph = _static_parity_inputs()
    with (
        patch("tools.engines.test_impact_matcher.load_json_file", side_effect=AssertionError("host graph")),
        patch.object(server, "_dependency_graph_payload_from_atlas", side_effect=AssertionError("implicit fallback")),
    ):
        result = _external_static_projection(tmp_path, atlas, dependency)
    assert {row["atlas_node"] for row in result["impacted_tests"]} == {
        "MAIN::src/load.test.ts", "MAIN::other/load.test.ts"}
    for row in result["impacted_tests"]:
        _assert_static_boundary(row, "naming_only")


def test_external_static_graph_parity_missing_atlas_stops_before_matcher(tmp_path):
    with (
        patch.object(server, "load_atlas_data", return_value={}),
        patch("tools.engines.test_impact_matcher.find_impacted_tests", side_effect=AssertionError("no target Atlas")),
    ):
        assert server._test_impact_from_raw(tmp_path / ".raw", "MAIN::src/load.ts", str(tmp_path)) is None


@pytest.mark.parametrize("external", [False, True])
@pytest.mark.parametrize("format", ["machine", "brief"])
def test_public_static_graph_parity_matcher_failure_is_error_not_empty_verdict(tmp_path, external, format):
    atlas, graph = _static_parity_inputs()
    with (
        patch.object(server, "_raw_dir_for_target", return_value=tmp_path / ".raw"),
        patch.object(server, "_analysis_root_display", return_value=str(tmp_path)),
        patch.object(server, "load_atlas_data", return_value=atlas),
        patch.object(server, "_load_json", return_value=graph),
        patch.object(server, "_resolve_target_node_from_raw", return_value=(
            "MAIN::src/load.ts", {"project": "MAIN", "repo_relative_path": "src/load.ts"})),
        patch.object(server, "_target_path_status", return_value={"exists": True, "indexed": True}),
        patch("tools.engines.test_impact_matcher.find_impacted_tests", side_effect=ValueError("parity probe failure")),
    ):
        response = server.get_test_impact("MAIN::src/load.ts", str(tmp_path) if external else "", format)
    assert "Test impact matching failed: parity probe failure" in response
    if format == "machine":
        payload = json.loads(response)
        assert payload["impacted_tests"] == []
        assert payload["analysis_root"] == str(tmp_path)


def _canonical_static_parity_sample(tmp_path, monkeypatch, external):
    import hashlib
    from tools.core import artifact_store
    from tools.core.artifact_store import ArtifactStore
    from tools.engines import test_impact_matcher as matcher

    monkeypatch.setattr(artifact_store, "ROOT", tmp_path)
    sources = {
        "MAIN": {
            "src/load.ts": "export const load = 1;\n",
            "src/view.ts": "import {load} from './load';\nexport const view = load;\n",
            "src/unit.test.ts": "import {load} from './load';\nimport {test, expect} from 'vitest';\ntest('directProbe', () => expect(load).toBe(1));\n",
            "tests/load.test.ts": "import {view} from '../src/view';\nimport {test, expect} from 'vitest';\ntest('indirectProbe', () => expect(view).toBe(1));\n",
        },
        "OTHER": {
            "tests/load.test.ts": "import {view} from '../../MAIN/src/view';\nimport {test, expect} from 'vitest';\ntest('otherProbe', () => expect(view).toBe(1));\n",
        },
        "UNRELATED": {
            "tests/load.test.ts": "export const disconnected = 1;\n",
        },
    }
    atlas = {}
    for project, files in sources.items():
        root = tmp_path / project
        for path, source in files.items():
            destination = root / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(source, encoding="utf-8", newline="")
        atlas[project] = {"root_path": project, "files": {
            path: {"hash": hashlib.sha256((root / path).read_bytes()).hexdigest(),
                   "symbols": [], "language": "typescript", "atlas_rel_path": path,
                   "workspace_rel": f"{project}/{path}", "project_key": project}
            for path in files}}
    graph = {"nodes": {f"{project}::{path}": {} for project, files in sources.items() for path in files},
             "edges": [
                 {"source": "MAIN::src/view.ts", "target": "MAIN::src/load.ts"},
                 {"source": "MAIN::src/unit.test.ts", "target": "MAIN::src/load.ts"},
                 {"source": "MAIN::tests/load.test.ts", "target": "MAIN::src/view.ts"},
                 {"source": "OTHER::tests/load.test.ts", "target": "MAIN::src/view.ts"},
             ]}
    store = ArtifactStore(raw_dir=tmp_path / ".raw")
    store.use_sqlite = True
    store.backend = "hybrid_sqlite"
    store._ensure_schema()
    store.save_raw("atlas", atlas)
    store.save_raw("circular_deps", graph)
    monkeypatch.setattr(server, "_raw_dir_for_target", lambda _: store._raw_dir)
    monkeypatch.setattr(server, "_analysis_root_display", lambda _: str(tmp_path))
    if external:
        monkeypatch.setattr(matcher, "load_atlas_data", lambda: pytest.fail("external read host Atlas"))
        monkeypatch.setattr(matcher, "load_json_file", lambda *_args: pytest.fail("external read host graph"))
    else:
        monkeypatch.setattr(matcher, "load_atlas_data", lambda: atlas)
        monkeypatch.setattr(matcher, "project_runtime_atlas", lambda data: (data, {}))
        monkeypatch.setattr(matcher, "load_json_file", lambda *_args: graph)
    args = {"target_file": "MAIN::src/load.ts", "target_root": str(tmp_path) if external else ""}
    payload = json.loads(server.get_test_impact(**args, format="machine"))
    brief = server.get_test_impact(**args, format="brief")
    return payload, brief


@pytest.mark.parametrize("external", [False, True])
def test_public_static_graph_parity_with_real_sqlite_snapshot_and_grounding(tmp_path, monkeypatch, external):
    payload, brief = _canonical_static_parity_sample(tmp_path, monkeypatch, external)
    assert payload["target_ref"] == "MAIN::MAIN/src/load.ts"
    assert payload["target_exists"] is True and payload["target_indexed"] is True
    assert payload["target_path_status"]["source_snapshot_status"] == "ok"
    indexed = {row["atlas_node"]: row for row in payload["impacted_tests"]}
    assert set(indexed) == {"MAIN::src/unit.test.ts", "MAIN::tests/load.test.ts", "OTHER::tests/load.test.ts"}
    for node, relation, marker in [
        ("MAIN::src/unit.test.ts", "direct_import", "directProbe"),
        ("MAIN::tests/load.test.ts", "transitive_dependency", "indirectProbe"),
        ("OTHER::tests/load.test.ts", "transitive_dependency", "otherProbe"),
    ]:
        row = indexed[node]
        _assert_static_boundary(row, relation)
        assert row["source_snippet_status"] == "included"
        assert marker in "\n".join(item["code"] for item in row["source_snippets"])
        assert row["file"].startswith(row["project"] + "/")
        assert row["repo_relative_path"] in row["run_command"]
        assert f'target_ref: {json.dumps(row["project"] + "::" + row["repo_relative_path"])}' in brief
    assert indexed["MAIN::tests/load.test.ts"]["type"] == "Dual Vector Match"
    assert indexed["OTHER::tests/load.test.ts"]["confidence"] == 0.6
    assert "UNRELATED" not in brief
    assert payload["evidence_boundary"] in brief


def _project_collision_inputs():
    files = {"src/load.ts": {}, "src/bridge.ts": {}, "src/load.test.ts": {}}
    atlas = {"MAIN": {"files": dict(files)}, "OTHER": {"files": dict(files)}}
    nodes = {f"{project}::{path}": {} for project in atlas for path in files}
    return atlas, {"nodes": nodes, "edges": [
        {"source": "MAIN::src/load.test.ts", "target": "MAIN::src/load.ts"},
        {"source": "OTHER::src/load.test.ts", "target": "MAIN::src/load.ts"},
    ]}


def test_equal_test_paths_keep_project_node_and_naming_rank_separate():
    atlas, graph = _project_collision_inputs()
    rows = find_impacted_tests("MAIN::src/load.ts", atlas=atlas, circular_deps=graph)["impacted_tests"]
    assert len(rows) == 2
    indexed = {row["atlas_node"]: row for row in rows}
    assert indexed["MAIN::src/load.test.ts"]["type"] == "Dual Vector Match"
    assert indexed["OTHER::src/load.test.ts"]["type"] == "Direct Static Import"
    for row in rows:
        _assert_static_boundary(row, "direct_import")


def test_transitive_other_project_cannot_borrow_target_project_naming():
    atlas, graph = _project_collision_inputs()
    graph["edges"] = [
        {"source": "OTHER::src/bridge.ts", "target": "MAIN::src/load.ts"},
        {"source": "OTHER::src/load.test.ts", "target": "OTHER::src/bridge.ts"},
    ]
    rows = find_impacted_tests("MAIN::src/load.ts", atlas=atlas, circular_deps=graph)["impacted_tests"]
    indexed = {row["atlas_node"]: row for row in rows}
    assert len(rows) == 2
    _assert_static_boundary(indexed["MAIN::src/load.test.ts"], "naming_only")
    _assert_static_boundary(indexed["OTHER::src/load.test.ts"], "transitive_dependency")
    assert indexed["OTHER::src/load.test.ts"]["confidence"] == 0.6


def test_live_merge_keeps_other_project_and_unbound_legacy_rows(tmp_path):
    rows = [
        {"file": "load.test.ts", "repo_relative_path": "load.test.ts", "project": "OTHER",
         "atlas_node": "OTHER::load.test.ts", "type": "Transitive Static Dependency", "confidence": 0.6},
        {"file": "load.test.ts", "type": "Legacy", "confidence": 0.2},
    ]
    live = {"file": "load.test.ts", "repo_relative_path": "load.test.ts",
            "type": "Direct Co-located Static Import", "confidence": 1.0, "run_command": "pnpm exec vitest run load.test.ts"}
    with patch.object(server, "_direct_colocated_test_candidates", return_value=[live]):
        result = server._merge_live_direct_test_candidates(
            {"target_project": "MAIN", "target_ref": "MAIN::load.ts", "impacted_tests": rows},
            tmp_path, "load.ts",
        )
    assert len(result["impacted_tests"]) == 3
    assert {row.get("project") for row in result["impacted_tests"]} == {"MAIN", "OTHER", None}
    assert next(row for row in result["impacted_tests"] if row.get("project") == "OTHER")["confidence"] == 0.6


def test_live_merge_retains_canonical_node_of_same_project_snapshot(tmp_path):
    old = {"project": "MAIN", "file": "load.test.ts", "repo_relative_path": "packages/a/load.test.ts",
           "atlas_node": "MAIN::load.test.ts", "type": "Transitive Static Dependency",
           "static_relation": "transitive_dependency", "confidence": 0.6}
    live = {"file": "packages/a/load.test.ts", "repo_relative_path": "packages/a/load.test.ts",
            "type": "Direct Co-located Static Import", "confidence": 1.0}
    with patch.object(server, "_direct_colocated_test_candidates", return_value=[live]):
        result = server._merge_live_direct_test_candidates(
            {"target_project": "MAIN", "impacted_tests": [old]}, tmp_path, "packages/a/load.ts",
        )
    assert len(result["impacted_tests"]) == 1
    row = result["impacted_tests"][0]
    assert row["project"] == "MAIN"
    assert row["atlas_node"] == "MAIN::load.test.ts"
    normalized = server._normalize_test_impact_payload_for_agent(result)
    _assert_static_boundary(normalized["impacted_tests"][0], "direct_import")


@pytest.mark.parametrize("row,expected", [
    ({}, ""),
    ({"project": "MAIN"}, "MAIN"),
    ({"atlas_node": "OTHER::src/a.test.ts"}, "OTHER"),
    ({"project": "MAIN", "atlas_node": "OTHER::src/a.test.ts"}, ""),
    ({"project": "MAIN", "atlas_node": "::src/a.test.ts"}, ""),
    ({"project": "MAIN", "atlas_node": "MAIN::"}, ""),
    ({"project": "Projé", "atlas_node": "Projé::src/a.test.ts"}, "Projé"),
])
def test_candidate_project_uses_only_consistent_candidate_identity(row, expected):
    from tools.core.test_impact_profiles import test_candidate_project
    assert test_candidate_project(row) == expected


def test_equal_rank_candidate_order_is_independent_of_graph_order():
    atlas, graph = _project_collision_inputs()
    first = find_impacted_tests("MAIN::src/load.ts", atlas=atlas, circular_deps=graph)["impacted_tests"]
    graph["edges"].reverse()
    second = find_impacted_tests("MAIN::src/load.ts", atlas=atlas, circular_deps=graph)["impacted_tests"]
    assert first == second


def test_live_merge_preserves_distinct_canonical_nodes_on_ambiguous_display_path(tmp_path):
    rows = [{"project": "MAIN", "file": path, "repo_relative_path": "same.test.ts",
             "atlas_node": f"MAIN::{path}", "confidence": 0.6}
            for path in ["a.test.ts", "b.test.ts"]]
    with patch.object(server, "_direct_colocated_test_candidates", return_value=[
        {"file": "same.test.ts", "repo_relative_path": "same.test.ts", "confidence": 1.0}
    ]):
        result = server._merge_live_direct_test_candidates(
            {"target_project": "MAIN", "impacted_tests": rows}, tmp_path, "same.ts")
    assert {row.get("atlas_node") for row in result["impacted_tests"]} == {
        "MAIN::a.test.ts", "MAIN::b.test.ts", None}


@pytest.mark.parametrize("row", [
    {"file": "consumer.ts"},
    {"project": "OTHER", "atlas_node": "MAIN::consumer.ts", "file": "consumer.ts"},
])
def test_unbound_candidate_never_borrows_target_snapshot(tmp_path, monkeypatch, row):
    store, _atlas_data, payload = _snapshot_mock_fixture(
        tmp_path, monkeypatch, "import {load} from './load';\n")
    payload["impacted_tests"] = [{**row, "source_snippets": [{"code": "borrowed"}]}]
    result = server._attach_test_source_snippets(store._raw_dir, payload)
    candidate = result["impacted_tests"][0]
    assert candidate["source_snippets"] == []
    assert candidate["source_snippet_status"] == "candidate_project_identity_unavailable"
    assert candidate["mock_declaration_evidence"]["status"] == "unknown"


def test_ambiguous_candidate_file_mapping_cannot_choose_first_snapshot(tmp_path, monkeypatch):
    from copy import deepcopy
    store, atlas, payload = _snapshot_mock_fixture(tmp_path, monkeypatch, "import {load} from './load';\n")
    original = atlas["MAIN"]["files"]["consumer.ts"]
    original["workspace_rel"] = "display.test.ts"
    atlas["MAIN"]["files"]["alias.ts"] = {**deepcopy(original), "workspace_rel": "display.test.ts"}
    payload["impacted_tests"][0]["file"] = "display.test.ts"
    payload["impacted_tests"][0]["repo_relative_path"] = "display.test.ts"
    result = server._attach_test_source_snippets(store._raw_dir, payload)
    candidate = result["impacted_tests"][0]
    assert candidate["source_snippet_status"] == "candidate_file_identity_unavailable"
    assert candidate["mock_declaration_evidence"]["status"] == "unknown"


@pytest.mark.parametrize("external", [False, True])
def test_public_equal_path_candidates_use_distinct_canonical_sqlite_snapshots(tmp_path, monkeypatch, external):
    import hashlib
    from tools.core import artifact_store
    from tools.core.artifact_store import ArtifactStore

    monkeypatch.setattr(artifact_store, "ROOT", tmp_path)
    atlas, graph = _project_collision_inputs()
    for project, marker in [("MAIN", "mainEvidence"), ("OTHER", "otherEvidence")]:
        root = tmp_path / project
        (root / "src").mkdir(parents=True)
        import_path = "./load" if project == "MAIN" else "../../MAIN/src/load"
        source = f"import {{load}} from '{import_path}';\nexport const {marker} = load;\n"
        (root / "src/load.test.ts").write_text(source, encoding="utf-8", newline="")
        (root / "src/load.ts").write_text("export const load = 1;\n", encoding="utf-8", newline="")
        atlas[project]["root_path"] = project
        atlas[project]["files"] = {
            path: {"hash": hashlib.sha256((root / path).read_bytes()).hexdigest(),
                   "symbols": [], "language": "typescript", "atlas_rel_path": path,
                   "workspace_rel": f"{project}/{path}", "repo_relative_path": f"{project}/{path}", "project_key": project}
            for path in ["src/load.ts", "src/load.test.ts"]
        }
    atlas["UNRELATED"] = {"files": {"src/load.test.ts": {}}}
    store = ArtifactStore(raw_dir=tmp_path / ".raw")
    store.use_sqlite = True
    store.backend = "hybrid_sqlite"
    store._ensure_schema()
    store.save_raw("atlas", atlas)
    store.save_raw("circular_deps", graph)
    monkeypatch.setattr(server, "_raw_dir_for_target", lambda _: store._raw_dir)
    monkeypatch.setattr(server, "_analysis_root_display", lambda _: str(tmp_path))
    monkeypatch.setattr(server, "_target_path_status", lambda *_args, **_kwargs: {"exists": True, "indexed": True})
    monkeypatch.setattr("tools.engines.test_impact_matcher.find_impacted_tests",
                        lambda target, **inputs: find_impacted_tests(
                            target, atlas=inputs.get("atlas", atlas), circular_deps=inputs.get("circular_deps", graph)))
    args = {"target_file": "MAIN::src/load.ts", "target_root": str(tmp_path) if external else ""}
    result = json.loads(server.get_test_impact(**args, format="machine"))
    assert len(result["impacted_tests"]) == 2
    indexed = {row["project"]: row for row in result["impacted_tests"]}
    assert set(indexed) == {"MAIN", "OTHER"}
    for project, marker in [("MAIN", "mainEvidence"), ("OTHER", "otherEvidence")]:
        row = indexed[project]
        assert row["atlas_node"] == f"{project}::src/load.test.ts"
        assert row["source_snippet_status"] == "included"
        snippets = "\n".join(str(item.get("code") or "") for item in row["source_snippets"])
        assert marker in snippets
        assert ("otherEvidence" if project == "MAIN" else "mainEvidence") not in snippets
        _assert_static_boundary(row, "direct_import")
    brief = server.get_test_impact(**args, format="brief")
    assert 'project: "MAIN"' in brief and 'project: "OTHER"' in brief
    assert "UNRELATED" not in brief
    assert 'target_ref: "MAIN::MAIN/src/load.test.ts"' in brief
    assert 'target_ref: "OTHER::OTHER/src/load.test.ts"' in brief
    assert "atlas_node:" not in brief


def _inputs():
    files = {
        "src/load.ts": {},
        "src/view.ts": {},
        "src/load.test.ts": {},
        "src/view.test.ts": {},
        "other/load.test.ts": {},
    }
    atlas = {"MAIN": {"files": files}}
    graph = {
        "nodes": {f"MAIN::{path}": {} for path in files},
        "edges": [
            {"source": "MAIN::src/load.test.ts", "target": "MAIN::src/load.ts"},
            {"source": "MAIN::src/view.ts", "target": "MAIN::src/load.ts"},
            {"source": "MAIN::src/view.test.ts", "target": "MAIN::src/view.ts"},
        ],
    }
    return atlas, graph


def _assert_static_boundary(row, relation):
    evidence = row["candidate_evidence"]
    assert evidence["relation"] == relation
    assert evidence["confidence_semantics"] == "static_candidate_ranking_not_behavioral_coverage"
    assert evidence["test_execution"] == "not_run_by_sage"
    assert evidence["changed_behavior"] == "not_established"
    assert evidence["mock_binding"] == "not_assessed"


def test_engine_preserves_static_relation_when_naming_promotes_rank():
    atlas, graph = _inputs()
    rows = find_impacted_tests("MAIN::src/load.ts", atlas=atlas, circular_deps=graph)["impacted_tests"]
    indexed = {row["file"]: row for row in rows}
    assert indexed["src/load.test.ts"]["confidence"] == 1.0
    assert indexed["src/load.test.ts"]["type"] == "Dual Vector Match"
    _assert_static_boundary(indexed["src/load.test.ts"], "direct_import")
    _assert_static_boundary(indexed["src/view.test.ts"], "transitive_dependency")
    _assert_static_boundary(indexed["other/load.test.ts"], "naming_only")


def test_dual_vector_does_not_turn_transitive_relation_into_direct_behavior():
    atlas, graph = _inputs()
    graph["edges"] = [
        {"source": "MAIN::src/view.ts", "target": "MAIN::src/load.ts"},
        {"source": "MAIN::src/load.test.ts", "target": "MAIN::src/view.ts"},
    ]
    rows = find_impacted_tests("MAIN::src/load.ts", atlas=atlas, circular_deps=graph)["impacted_tests"]
    row = next(row for row in rows if row["file"] == "src/load.test.ts")
    assert row["type"] == "Dual Vector Match"
    assert row["confidence"] == 1.0
    _assert_static_boundary(row, "transitive_dependency")


@pytest.mark.parametrize("kind,relation", [
    ("Direct Static Import", "direct_import"),
    ("Direct Co-located Static Import", "direct_import"),
    ("Transitive Static Dependency", "transitive_dependency"),
    ("Semantic Convention Match", "naming_only"),
    ("Dual Vector Match", "unknown_static_relation"),
    ("Unrecognized Match", "unknown_static_relation"),
])
def test_legacy_or_unknown_type_never_invents_execution_from_score(kind, relation):
    row = {"file": "load.test.ts", "type": kind, "confidence": 1.0}
    normalized = server._normalize_test_impact_payload_for_agent({"impacted_tests": [row]})
    _assert_static_boundary(normalized["impacted_tests"][0], relation)


def test_live_direct_test_with_mock_remains_a_candidate_not_behavioral_proof(tmp_path: Path):
    source = tmp_path / "load.ts"
    source.write_text("export function load() { throw new Error('not exercised'); }", encoding="utf-8")
    (tmp_path / "load.test.ts").write_text(
        "import { load } from './load';\n"
        "vi.mock('./load', () => ({ load: vi.fn() }));\n"
        "test('import smoke', () => expect(load).toBeDefined());\n",
        encoding="utf-8",
    )
    rows = server._direct_colocated_test_candidates(tmp_path, "load.ts")
    assert len(rows) == 1
    assert rows[0]["confidence"] == 1.0
    result = server._normalize_test_impact_payload_for_agent({"impacted_tests": rows})
    _assert_static_boundary(result["impacted_tests"][0], "direct_import")
    # Static source alone neither confirms the mock binding nor executes the throwing function.
    assert not list(tmp_path.glob("*.json"))


def test_normalization_discards_borrowed_behavior_proof_and_is_idempotent():
    row = {
        "file": "src/load.test.ts", "type": "Direct Static Import", "confidence": 1.0,
        "candidate_evidence": {"changed_behavior": "confirmed", "test_execution": "passed", "mock_binding": "none"},
    }
    result = server._normalize_test_impact_payload_for_agent({"impacted_tests": [row]})
    _assert_static_boundary(result["impacted_tests"][0], "direct_import")
    assert server._normalize_test_impact_payload_for_agent(result) == result
    assert row["candidate_evidence"]["changed_behavior"] == "confirmed"  # no caller mutation


def test_brief_discloses_boundary_and_relation_before_optional_snippets():
    payload = server._normalize_test_impact_payload_for_agent({
        "target": "MAIN::src/load.ts", "target_file": "src/load.ts",
        "target_exists": True, "target_indexed": True,
        "impacted_tests": [{"file": "src/load.test.ts", "type": "Direct Static Import", "confidence": 1.0}],
    })
    brief = server._render_test_impact_brief(payload)
    assert "static_candidate_ranking_not_behavioral_coverage" in brief
    assert "direct_import" in brief
    assert "not_run_by_sage" in brief
    assert "not_established" in brief
    assert "not_assessed" in brief


@pytest.mark.parametrize("identity, expected_project, expected_ref", [
    ({"project": "MAIN", "atlas_node": "MAIN::src/load.test.ts",
      "repo_relative_path": "packages/main/src/load.test.ts"},
     "MAIN", "MAIN::packages/main/src/load.test.ts"),
    ({"atlas_node": "OTHER::src/load.test.ts"}, "OTHER", "OTHER::src/load.test.ts"),
    ({}, None, None),
    ({"project": "MAIN", "atlas_node": "OTHER::src/load.test.ts"}, None, None),
])
def test_brief_uses_candidate_owned_public_ref_without_internal_graph_labels(
    identity, expected_project, expected_ref,
):
    from copy import deepcopy

    row = {"file": "src/load.test.ts", "type": "Direct Static Import", "confidence": 1.0,
           "run_command": "pnpm test src/load.test.ts", **identity}
    row["run_command"] = "pnpm test " + (row.get("repo_relative_path") or row["file"])
    payload = {"target": "MAIN::src/load.ts", "target_project": "MAIN",
               "target_file": "src/load.ts", "impacted_tests": [row]}
    before = deepcopy(payload)
    brief = server._render_test_impact_brief(payload)
    assert "atlas_node:" not in brief
    assert f"    project: {json.dumps(expected_project)}" in brief
    assert f"    target_ref: {json.dumps(expected_ref)}" in brief
    assert f"  - file: {json.dumps(row.get('repo_relative_path') or row['file'])}" in brief
    assert 'target_ref_usage: "SAGE/MCP reference only; not a filesystem path"' in brief
    assert f'run: {json.dumps(row["run_command"])}' in brief
    assert "static_candidate_ranking_not_behavioral_coverage" in brief
    assert "not_run_by_sage" in brief
    assert payload == before
    machine = server._normalize_test_impact_payload_for_agent(payload)
    assert machine["impacted_tests"][0].get("atlas_node") == row.get("atlas_node")


@pytest.mark.parametrize("external", [False, True])
def test_public_mcp_machine_and_brief_keep_same_static_boundary(tmp_path: Path, external: bool):
    raw = tmp_path / ".raw"
    result = {
        "target": "MAIN::src/load.ts", "target_file": "src/load.ts",
        "target_exists": True, "target_indexed": True,
        "impacted_tests": [{"file": "src/load.test.ts", "type": "Direct Static Import", "confidence": 1.0}],
    }
    with (
        patch.object(server, "_raw_dir_for_target", return_value=raw),
        patch.object(server, "_test_impact_from_raw", return_value=result),
        patch("tools.engines.test_impact_matcher.find_impacted_tests", return_value=result),
        patch.object(server, "_resolve_target_node_from_raw", return_value=("MAIN::src/load.ts", {"project": "MAIN", "repo_relative_path": "src/load.ts"})),
        patch.object(server, "_analysis_root_display", return_value=str(tmp_path)),
        patch.object(server, "_target_path_status", return_value={"exists": True, "indexed": True}),
        patch.object(server, "_merge_live_direct_test_candidates", side_effect=lambda payload, *_: payload),
        patch.object(server, "_attach_test_source_snippets", side_effect=lambda _, payload: payload),
    ):
        args = {"target_file": "MAIN::src/load.ts", "target_root": str(tmp_path) if external else ""}
        machine = json.loads(server.get_test_impact(**args, format="machine"))
        brief = server.get_test_impact(**args, format="brief")
    _assert_static_boundary(machine["impacted_tests"][0], "direct_import")
    assert machine["evidence_boundary"] in brief

def test_real_external_raw_projection_keeps_static_relation(tmp_path: Path):
    atlas, graph = _inputs()
    with (
        patch.object(server, "load_atlas_data", return_value=atlas),
        patch.object(server, "_atlas", return_value=atlas),
        patch.object(server, "_load_json", return_value=graph),
        patch.object(server, "_dependency_graph_from_raw", return_value=(graph["nodes"], graph["edges"], {"MAIN::src/load.ts": ["MAIN::src/load.test.ts"]})),
        patch.object(server, "_resolve_target_node_from_raw", return_value=("MAIN::src/load.ts", {"project": "MAIN", "repo_relative_path": "src/load.ts"})),
        patch.object(server, "_target_path_status", return_value={"exists": True, "indexed": True}),
        patch.object(server, "_repo_relative_from_node", side_effect=lambda _, node: node.split("::", 1)[1]),
        patch.object(server, "_target_ref_from_node", side_effect=lambda _, node: node),
    ):
        result = server._test_impact_from_raw(tmp_path / ".raw", "MAIN::src/load.ts", target_root=str(tmp_path))
    normalized = server._normalize_test_impact_payload_for_agent(result)
    indexed = {row["file"]: row for row in normalized["impacted_tests"]}
    _assert_static_boundary(indexed["src/load.test.ts"], "direct_import")
    _assert_static_boundary(indexed["other/load.test.ts"], "naming_only")


def test_test_gap_sibling_preserves_transitive_dual_vector_origin(monkeypatch):
    from tools import generate_test_gap_report as gap

    atlas, _ = _inputs()
    monkeypatch.setattr(gap, "load_atlas_data", lambda: atlas)
    monkeypatch.setattr(gap, "_default_agent_project_scope", lambda _: "MAIN")
    monkeypatch.setattr(gap, "_load_reverse_dependency_graph", lambda: {
        "MAIN::src/load.ts": ["MAIN::src/view.ts"],
        "MAIN::src/view.ts": ["MAIN::src/load.test.ts"],
    })
    monkeypatch.setattr(gap, "_blast_scores", lambda: {})
    monkeypatch.setattr(gap, "_active_signal_nodes", lambda: set())
    payload = gap.build_test_gap_report()
    source = next(row for row in payload["source_with_test_candidates"] if row["file"] == "src/load.ts")
    row = next(row for row in source["test_candidates"] if row["file"] == "src/load.test.ts")
    assert row["type"] == "Dual Vector Match"
    _assert_static_boundary(row, "transitive_dependency")
    assert payload["evidence_boundary"] in gap.render_report(payload)


@pytest.mark.parametrize("relation", [None, [], {}, "confirmed_behavior"])
def test_invalid_declared_relation_cannot_invent_behavioral_proof(relation):
    row = {"file": "a.test.ts", "type": "Unrecognized", "static_relation": relation, "confidence": 1.0}
    result = server._normalize_test_impact_payload_for_agent({"impacted_tests": [row]})
    _assert_static_boundary(result["impacted_tests"][0], "unknown_static_relation")


def test_optional_schema_extension_rejects_forged_execution_and_accepts_legacy():
    from copy import deepcopy
    from tools.core.artifact_validator import validate_payload

    atlas, graph = _inputs()
    payload = find_impacted_tests("MAIN::src/load.ts", atlas=atlas, circular_deps=graph)
    assert validate_payload("test_impact_report", payload) == []
    legacy = {"target": "MAIN::src/load.ts", "impacted_tests": [{
        "file": "src/load.test.ts", "project": "MAIN", "type": "Dual Vector Match",
        "confidence": 1.0, "run_command": "pnpm test src/load.test.ts",
    }]}
    assert validate_payload("test_impact_report", legacy) == []
    forged = deepcopy(payload)
    forged["impacted_tests"][0]["candidate_evidence"]["changed_behavior"] = "confirmed"
    assert validate_payload("test_impact_report", forged)


def test_missing_policy_is_explicit_failure_not_a_hardcoded_default(monkeypatch, tmp_path: Path):
    from tools.core import test_impact_profiles

    path = tmp_path / "profile.json"
    path.write_text(json.dumps({"languages": {"typescript": {}}, "confidence": {"direct": 1.0}, "fallback_command": "run-test {test_path}"}), encoding="utf-8")
    monkeypatch.setattr(test_impact_profiles, "PROFILE_PATH", path)
    with pytest.raises(ValueError, match="missing evidence_policy"):
        test_impact_profiles.static_candidate_evidence({"type": "Direct Static Import"})


def test_empty_candidate_list_keeps_unknown_behavior_and_honest_fallback(monkeypatch):
    monkeypatch.setattr(server, "_nearest_package_validation_commands", lambda _: ["npm test"])
    payload = server._normalize_test_impact_payload_for_agent({"impacted_tests": [], "target_exists": True, "target_indexed": True})
    assert payload["impacted_tests"] == []
    brief = server._render_test_impact_brief(payload)
    assert "An empty impacted_tests list is not proof that no tests matter." in brief
    assert "npm test" in brief
    assert payload["evidence_boundary"] in brief


@pytest.mark.parametrize("format", ["machine", "brief"])
def test_public_missing_evidence_policy_fails_before_target_analysis(monkeypatch, format):
    from tools.engines import test_impact_matcher

    def forbidden(*_, **__):
        raise AssertionError("Missing policy must not run target matching")

    def missing_policy():
        raise ValueError("Test impact profiles missing evidence_policy")

    monkeypatch.setattr(server, "static_test_evidence_policy", missing_policy)
    monkeypatch.setattr(server, "_test_impact_from_raw", forbidden)
    monkeypatch.setattr(test_impact_matcher, "find_impacted_tests", forbidden)
    result = json.loads(server.get_test_impact("MAIN::src/load.ts", format=format))
    assert result["impacted_tests"] == []
    assert result["error"] == "Test impact profiles missing evidence_policy"

def _snapshot_mock_fixture(tmp_path, monkeypatch, source):
    import hashlib
    from tools.core.artifact_store import ArtifactStore
    from tools.core.path_engine import resolve_project_import
    from tools.tests.test_symbol_qualified_import_call_search import _source_atlas

    atlas = _source_atlas(tmp_path, source)
    atlas["MAIN"]["root_path"] = str(tmp_path)
    info = atlas["MAIN"]["files"]["consumer.ts"]
    target = "export function load() { throw new Error('must not execute'); }\n"
    (tmp_path / "load.ts").write_text(target, encoding="utf-8", newline="")
    info["import_records"] = [
        {"raw_source": record["source"],
         "source": resolve_project_import(record["source"], str(tmp_path), str(tmp_path),
                                          str(tmp_path), alias_map={"@lib/": "."}),
         "kind": "type" if record["typeOnly"] else record["kind"], "scope": "top_level"}
        for record in info["direct_import_binding_evidence"]["records"]
    ]
    atlas["MAIN"]["files"]["load.ts"] = {
        "hash": hashlib.sha256(target.encode()).hexdigest(), "symbols": [], "language": "typescript",
    }
    store = ArtifactStore(raw_dir=tmp_path / ".raw")
    store.use_sqlite = True
    store.backend = "hybrid_sqlite"
    store._ensure_schema()
    store._save_atlas_to_sqlite(atlas)
    monkeypatch.setattr(server, "_atlas", lambda **_: atlas)
    payload = server._normalize_test_impact_payload_for_agent({
        "target": "MAIN::load.ts", "target_ref": "MAIN::load.ts",
        "target_file": "load.ts", "target_project": "MAIN",
        "impacted_tests": [{"file": "consumer.ts", "project": "MAIN",
                           "type": "Direct Static Import", "confidence": 1.0}],
    })
    return store, atlas, payload


@pytest.mark.parametrize("source", [
    "import { vi } from 'vitest';\nimport {load} from './load';\nvi.mock('./load', () => ({load: () => 1}));\n",
    "import { vi as runtime } from 'vitest';\nimport {load} from './load';\nruntime['mock']('./load');\n",
    "import { jest as runtime } from '@jest/globals';\nimport {load} from './load';\nruntime.doMock(\x60./load\x60);\n",
    "import {vi} from 'vitest';\nimport {load} from '@lib/load';\nvi.mock('@lib/load');\n",
])
def test_snapshot_bound_literal_mock_declaration_is_not_runtime_binding(tmp_path, monkeypatch, source):
    store, atlas, payload = _snapshot_mock_fixture(tmp_path, monkeypatch, source)
    # No live file dependency and no target/test execution after snapshot capture.
    (tmp_path / "consumer.ts").unlink()
    result = server._attach_test_source_snippets(store._raw_dir, payload)
    row = result["impacted_tests"][0]
    observation = row["mock_declaration_evidence"]
    assert observation["status"] == "target_mock_declaration_observed"
    assert observation["runtime_binding"] == "not_established"
    assert observation["source_hash"] == atlas["MAIN"]["files"]["consumer.ts"]["hash"]
    assert observation["target_ref"] == "MAIN::load.ts"
    assert observation["declarations"][0]["module_specifier"] in {"./load", "@lib/load"}
    _assert_static_boundary(row, "direct_import")
    brief = server._render_test_impact_brief(result)
    assert "target_mock_declaration_observed" in brief
    assert "not_run_by_sage" in brief


@pytest.mark.parametrize("external", [False, True])
def test_public_mock_observation_recomputed_from_snapshot_in_both_formats(tmp_path, monkeypatch, external):
    source = "import {vi} from 'vitest';\nimport {load} from './load';\nvi.mock('./load');\n"
    canonical_atlas_reader = server._atlas
    store, atlas, payload = _snapshot_mock_fixture(tmp_path, monkeypatch, source)
    store.save_raw("atlas", atlas)
    monkeypatch.setattr(server, "_atlas", canonical_atlas_reader)
    payload["target_exists"] = payload["target_indexed"] = True
    payload["impacted_tests"][0]["mock_declaration_evidence"] = {"status": "none", "runtime_binding": "confirmed"}
    with (
        patch.object(server, "_raw_dir_for_target", return_value=store._raw_dir),
        patch.object(server, "_test_impact_from_raw", return_value=payload),
        patch("tools.engines.test_impact_matcher.find_impacted_tests", return_value=payload),
        patch.object(server, "_resolve_target_node_from_raw", return_value=("MAIN::load.ts", {"project": "MAIN", "repo_relative_path": "load.ts"})),
        patch.object(server, "_analysis_root_display", return_value=str(tmp_path)),
        patch.object(server, "_target_path_status", return_value={"exists": True, "indexed": True}),
        patch.object(server, "_merge_live_direct_test_candidates", side_effect=lambda data, *_: data),
    ):
        args = {"target_file": "MAIN::load.ts", "target_root": str(tmp_path) if external else ""}
        result = json.loads(server.get_test_impact(**args, format="machine"))
        brief = server.get_test_impact(**args, format="brief")
    observation = result["impacted_tests"][0]["mock_declaration_evidence"]
    assert observation["status"] == "target_mock_declaration_observed"
    assert observation["runtime_binding"] == "not_established"
    assert "target_mock_declaration_observed" in brief


def test_mock_observation_schema_rejects_forged_runtime_or_unbound_positive(tmp_path, monkeypatch):
    from copy import deepcopy
    from tools.core.artifact_validator import validate_payload

    store, _, payload = _snapshot_mock_fixture(
        tmp_path, monkeypatch, "import {vi} from 'vitest';\nimport {load} from './load';\nvi.mock('./load');\n")
    result = server._attach_test_source_snippets(store._raw_dir, payload)
    assert validate_payload("test_impact_report", result) == []
    for mutation in ("runtime_binding", "source_hash", "declarations"):
        forged = deepcopy(result)
        observation = forged["impacted_tests"][0]["mock_declaration_evidence"]
        if mutation == "runtime_binding":
            observation[mutation] = "confirmed"
        else:
            observation.pop(mutation)
        assert validate_payload("test_impact_report", forged)


def test_conditional_mock_is_syntax_only_and_declaration_output_is_bounded(tmp_path, monkeypatch):
    source = ("import {vi} from 'vitest';\nimport {load} from './load';\n"
              + "if(false) {vi.mock('./load');}\n" * 66)
    store, _, payload = _snapshot_mock_fixture(tmp_path, monkeypatch, source)
    row = server._attach_test_source_snippets(store._raw_dir, payload)["impacted_tests"][0]
    observation = row["mock_declaration_evidence"]
    assert observation["status"] == "target_mock_declaration_observed"
    assert len(observation["declarations"]) == 3
    assert observation["declarations_omitted"] == 61
    assert observation["runtime_binding"] == "not_established"
    _assert_static_boundary(row, "direct_import")


@pytest.mark.parametrize("format", ["machine", "brief"])
def test_public_missing_mock_policy_is_explicit_failure_before_target_matching(monkeypatch, format):
    from copy import deepcopy
    from tools.core import test_impact_profiles

    profile = deepcopy(test_impact_profiles.load_test_impact_profiles())
    profile["evidence_policy"].pop("mock_declaration_policy")
    monkeypatch.setattr(test_impact_profiles, "load_test_impact_profiles", lambda: profile)
    def forbidden(*_, **__):
        raise AssertionError("Missing mock policy must fail before target lookup")
    monkeypatch.setattr(server, "_raw_dir_for_target", forbidden)
    result = json.loads(server.get_test_impact("MAIN::load.ts", format=format))
    assert result["impacted_tests"] == []
    assert "missing mock_declaration_policy" in result["error"]


@pytest.mark.parametrize("source", [
    "// vi.mock('./load');\nimport { load } from './load';\n",
    "const note = \"vi.mock('./load')\";\nimport { load } from './load';\n",
    "import {vi} from 'vitest';\nimport {load} from './load';\n{const vi={mock() {}}; vi.mock('./load');}\n",
    "import type {vi} from 'vitest';\nimport {load} from './load';\nvi.mock('./load');\n",
    "import {vi} from 'other-runtime';\nimport {load} from './load';\nvi.mock('./load');\n",
    "import {vi} from 'vitest';\nimport {load} from './load';\nconst path='./load'; vi.mock(path);\n",
    "import {vi} from 'vitest';\nimport {load} from './load';\nvi?.mock('./load');\n",
    "import {vi} from 'vitest';\nimport {load} from './load';\nfunction later(){vi.mock('./load');}\n",
    "import {vi} from 'vitest';\nimport {load} from './load';\nvi.mock('./other');\n",
    "import {load} from './load';\nvi.mock('./load');\n",
])
def test_unsupported_mock_source_never_proves_no_mocks(tmp_path, monkeypatch, source):
    store, _, payload = _snapshot_mock_fixture(tmp_path, monkeypatch, source)
    row = server._attach_test_source_snippets(store._raw_dir, payload)["impacted_tests"][0]
    assert row["mock_declaration_evidence"]["status"] == "unknown"
    assert row["mock_declaration_evidence"]["runtime_binding"] == "not_established"
    _assert_static_boundary(row, "direct_import")


@pytest.mark.parametrize("mutation", ["content", "atlas", "missing", "unresolved", "ambiguous", "other_project", "legacy", "budget", "missing_target"])
def test_mock_observation_rejects_unbound_snapshot_resolution_or_budget(tmp_path, monkeypatch, mutation):
    source = "import {vi} from 'vitest';\nimport {load} from './load';\nvi.mock('./load');\n"
    store, atlas, payload = _snapshot_mock_fixture(tmp_path, monkeypatch, source)
    info = atlas["MAIN"]["files"]["consumer.ts"]
    if mutation in {"content", "missing"}:
        with store.db_manager.get_connection() as conn:
            if mutation == "content":
                conn.execute("UPDATE source_snapshots SET content='tampered';")
            else:
                conn.execute("DELETE FROM source_snapshots;")
    elif mutation == "atlas":
        info["hash"] = "a" * 64
    elif mutation == "unresolved":
        info["import_records"] = []
    elif mutation == "ambiguous":
        info["import_records"].append({"raw_source": "./load", "source": "other.ts", "kind": "named"})
    elif mutation == "other_project":
        payload["impacted_tests"][0]["project"] = "COMPANION"
    elif mutation == "legacy":
        info["module_root_import_call_evidence"].pop("literal_argument_evidence_version", None)
    elif mutation == "missing_target":
        atlas["MAIN"]["files"].pop("load.ts")
    payload["impacted_tests"][0]["mock_declaration_evidence"] = {
        "status": "target_mock_declaration_observed", "runtime_binding": "confirmed",
    }
    result = server._attach_test_source_snippets(
        store._raw_dir, payload, max_tests_with_snippets=0 if mutation == "budget" else 3)
    observation = result["impacted_tests"][0]["mock_declaration_evidence"]
    assert observation["status"] == "unknown"
    assert observation["runtime_binding"] == "not_established"

@pytest.mark.parametrize("ambiguous", [False, True])
def test_mock_target_uses_unique_atlas_identity_not_repo_path_guess(tmp_path, monkeypatch, ambiguous):
    from copy import deepcopy
    source = "import {vi} from 'vitest';\nimport {load} from './load';\nvi.mock('./load');\n"
    store, atlas, payload = _snapshot_mock_fixture(tmp_path, monkeypatch, source)
    target_info = atlas["MAIN"]["files"]["load.ts"]
    target_info.update(repo_relative_path="src/load.ts", workspace_rel="src/load.ts", atlas_rel_path="load.ts")
    payload["target_file"] = "src/load.ts"
    payload["target_ref"] = "MAIN::src/load.ts"
    if ambiguous:
        atlas["MAIN"]["files"]["other.ts"] = deepcopy(target_info)
    observation = server._attach_test_source_snippets(
        store._raw_dir, payload)["impacted_tests"][0]["mock_declaration_evidence"]
    assert observation["status"] == ("unknown" if ambiguous else "target_mock_declaration_observed")
    assert observation["target_ref"] == "MAIN::src/load.ts"


def test_mock_source_assessment_budget_does_not_infer_no_mocks_for_fourth_candidate(tmp_path, monkeypatch):
    from copy import deepcopy
    store, _, payload = _snapshot_mock_fixture(
        tmp_path, monkeypatch, "import {vi} from 'vitest';\nimport {load} from './load';\nvi.mock('./load');\n")
    payload["impacted_tests"] *= 4
    payload = deepcopy(payload)
    rows = server._attach_test_source_snippets(store._raw_dir, payload)["impacted_tests"]
    assert all(row["mock_declaration_evidence"]["status"] == "target_mock_declaration_observed" for row in rows[:3])
    assert rows[3]["mock_declaration_evidence"]["status"] == "unknown"
    assert rows[3]["mock_declaration_evidence"]["reason"] == "context_budget_omitted"
