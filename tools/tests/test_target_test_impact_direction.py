from __future__ import annotations

import json

import pytest

from tools.engines.test_impact_matcher import find_impacted_tests
from tools.mcp import server
from tools.tests.test_target_test_impact_evidence_boundary import _assert_static_boundary


def _provider_hook_direction_inputs(integration=False):
    sources = {
        "src/useShared.ts": "export const useShared = () => 1;\n",
        "src/Provider.ts": "import {useShared} from './useShared';\nexport const Provider = () => useShared();\n",
        "src/Screen.ts": "import {Provider} from './Provider';\nexport const Screen = () => Provider();\n",
        "src/Sibling.ts": "import {useShared} from './useShared';\nexport const Sibling = () => useShared();\n",
        "src/useShared.test.ts": "import {useShared} from './useShared';\nimport {test, expect} from 'vitest';\ntest('hookOnlyProbe', () => expect(useShared()).toBe(1));\n",
    }
    edges = [
        ("src/Provider.ts", "src/useShared.ts"),
        ("src/Screen.ts", "src/Provider.ts"),
        ("src/Sibling.ts", "src/useShared.ts"),
        ("src/useShared.test.ts", "src/useShared.ts"),
    ]
    if integration:
        sources.update({
            "src/unit.test.ts": "import {Provider} from './Provider';\nimport {test, expect} from 'vitest';\ntest('providerProbe', () => expect(Provider()).toBe(1));\n",
            "tests/screen.test.ts": "import {Screen} from '../src/Screen';\nimport {test, expect} from 'vitest';\ntest('screenProbe', () => expect(Screen()).toBe(1));\n",
        })
        edges.extend([
            ("src/unit.test.ts", "src/Provider.ts"),
            ("tests/screen.test.ts", "src/Screen.ts"),
        ])
    atlas = {"MAIN": {"root_path": ".", "files": {path: {} for path in sources}}}
    graph = {
        "nodes": {f"MAIN::{path}": {} for path in sources},
        "edges": [{"source": f"MAIN::{source}", "target": f"MAIN::{target}"} for source, target in edges],
    }
    return sources, atlas, graph


def _assert_provider_hook_direction_rows(payload, target, integration):
    indexed = {row["atlas_node"]: row for row in payload["impacted_tests"]}
    expected = {}
    if target == "src/useShared.ts":
        expected["MAIN::src/useShared.test.ts"] = "direct_import"
    if integration:
        expected.update({
            "MAIN::src/unit.test.ts": "direct_import" if target == "src/Provider.ts" else "transitive_dependency",
            "MAIN::tests/screen.test.ts": "transitive_dependency",
        })
    assert set(indexed) == set(expected)
    for node, relation in expected.items():
        _assert_static_boundary(indexed[node], relation)


@pytest.mark.parametrize("integration", [False, True])
@pytest.mark.parametrize("target", ["src/Provider.ts", "src/useShared.ts"])
@pytest.mark.parametrize("reverse_order", [False, True])
def test_provider_hook_direction_distinguishes_shared_fork_from_directed_paths(integration, target, reverse_order):
    _sources, atlas, graph = _provider_hook_direction_inputs(integration)
    if reverse_order:
        graph["edges"].reverse()
    payload = find_impacted_tests(f"MAIN::{target}", atlas=atlas, circular_deps=graph)
    _assert_provider_hook_direction_rows(payload, target, integration)


def test_provider_hook_direction_cycle_requires_a_real_back_edge():
    _sources, atlas, graph = _provider_hook_direction_inputs()
    graph["edges"].append({"source": "MAIN::src/useShared.ts", "target": "MAIN::src/Provider.ts"})
    rows = find_impacted_tests("MAIN::src/Provider.ts", atlas=atlas, circular_deps=graph)["impacted_tests"]
    assert [row["atlas_node"] for row in rows] == ["MAIN::src/useShared.test.ts"]
    _assert_static_boundary(rows[0], "transitive_dependency")


def test_provider_hook_direction_naming_remains_independent_not_a_dependency():
    _sources, atlas, graph = _provider_hook_direction_inputs()
    atlas["MAIN"]["files"]["tests/Provider.spec.ts"] = {}
    rows = find_impacted_tests("MAIN::src/Provider.ts", atlas=atlas, circular_deps=graph)["impacted_tests"]
    assert [row["atlas_node"] for row in rows] == ["MAIN::tests/Provider.spec.ts"]
    _assert_static_boundary(rows[0], "naming_only")


def _provider_hook_direction_public_sample(tmp_path, monkeypatch, external, integration):
    import hashlib
    from tools.core import artifact_store
    from tools.core.artifact_store import ArtifactStore
    from tools.engines import test_impact_matcher as matcher

    sources, atlas, graph = _provider_hook_direction_inputs(integration)
    monkeypatch.setattr(artifact_store, "ROOT", tmp_path)
    for path, source in sources.items():
        destination = tmp_path / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(source, encoding="utf-8", newline="")
        atlas["MAIN"]["files"][path] = {
            "hash": hashlib.sha256(destination.read_bytes()).hexdigest(), "symbols": [],
            "language": "typescript", "atlas_rel_path": path, "workspace_rel": path, "project_key": "MAIN",
        }
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
    samples = {}
    for target in ["src/Provider.ts", "src/useShared.ts"]:
        args = {"target_file": f"MAIN::{target}", "target_root": str(tmp_path) if external else ""}
        samples[target] = {
            "machine": json.loads(server.get_test_impact(**args, format="machine")),
            "brief": server.get_test_impact(**args, format="brief"),
        }
    return samples


@pytest.mark.parametrize("external", [False, True])
@pytest.mark.parametrize("integration", [False, True])
def test_provider_hook_direction_public_sqlite_machine_and_brief(tmp_path, monkeypatch, external, integration):
    samples = _provider_hook_direction_public_sample(tmp_path, monkeypatch, external, integration)
    for target, sample in samples.items():
        payload, brief = sample["machine"], sample["brief"]
        assert payload["target_path_status"]["source_snapshot_status"] == "ok"
        _assert_provider_hook_direction_rows(payload, target, integration)
        assert payload["evidence_boundary"] in brief
        for row in payload["impacted_tests"]:
            assert f'target_ref: {json.dumps(row["project"] + "::" + row["repo_relative_path"])}' in brief
            assert "not_run_by_sage" in brief and "not_established" in brief
            assert row["source_snippet_status"] == "included"
            assert row["mock_declaration_evidence"]["status"] == "unknown"
    if not integration:
        assert samples["src/Provider.ts"]["machine"]["impacted_tests"] == []
        assert "run_nearest_feature_or_package_validation" in samples["src/Provider.ts"]["brief"]
        assert "An empty impacted_tests list is not proof that no tests matter." in samples["src/Provider.ts"]["brief"]


@pytest.mark.parametrize("integration", [False, True])
def test_provider_hook_direction_test_gap_sibling_keeps_the_same_boundary(monkeypatch, integration):
    from tools import generate_test_gap_report as gap

    _sources, atlas, graph = _provider_hook_direction_inputs(integration)
    reverse = {}
    for edge in graph["edges"]:
        reverse.setdefault(edge["target"], []).append(edge["source"])
    monkeypatch.setattr(gap, "load_atlas_data", lambda: atlas)
    monkeypatch.setattr(gap, "_default_agent_project_scope", lambda _: "MAIN")
    monkeypatch.setattr(gap, "_load_reverse_dependency_graph", lambda: reverse)
    monkeypatch.setattr(gap, "_blast_scores", lambda: {})
    monkeypatch.setattr(gap, "_active_signal_nodes", lambda: set())
    report = gap.build_test_gap_report()
    for target in ["src/Provider.ts", "src/useShared.ts"]:
        entry = next(row for row in report["source_with_test_candidates"] + report["source_without_test_candidates"]
                     if row["file"] == target)
        _assert_provider_hook_direction_rows(
            {"impacted_tests": [{**row, "atlas_node": row["node_key"]} for row in entry["test_candidates"]]},
            target, integration)


def _component_render_ref_inputs():
    sources = {
        "View.tsx": "export const View = () => <div>View</div>;\n",
        "Screen.tsx": "import {View} from './View';\nexport default () => <View/>;\n",
        "smoke.test.tsx": "import Screen from '@/Screen';\nimport {render} from '@testing-library/react';\nimport {it} from 'vitest';\nit('render', () => render(<Screen/>));\n",
    }
    atlas = {"MAIN": {"root_path": "src", "files": {
        path: {"workspace_rel": "src/" + path} for path in sources
    }}}
    graph = {
        "nodes": {f"MAIN::{path}": {} for path in sources},
        "edges": [
            {"source": "MAIN::smoke.test.tsx", "target": "MAIN::Screen.tsx"},
            {"source": "MAIN::Screen.tsx", "target": "MAIN::View.tsx"},
        ],
    }
    return sources, atlas, graph


@pytest.mark.parametrize("target", ["MAIN::View.tsx", "MAIN::src/View.tsx", "src/View.tsx"])
def test_component_render_path_canonical_and_repo_refs_keep_same_transitive_candidate(target):
    _sources, atlas, graph = _component_render_ref_inputs()
    result = find_impacted_tests(target, atlas=atlas, circular_deps=graph)
    assert result["target"] == "MAIN::View.tsx"
    assert result["target_ref"] == "MAIN::src/View.tsx"
    assert result["target_file_context"]["atlas_relative_path"] == "View.tsx"
    assert [row["atlas_node"] for row in result["impacted_tests"]] == ["MAIN::smoke.test.tsx"]
    _assert_static_boundary(result["impacted_tests"][0], "transitive_dependency")


@pytest.mark.parametrize("target", ["OTHER::src/View.tsx", "MAIN::missing/View.tsx"])
def test_component_render_path_ref_does_not_borrow_other_scope_or_missing_target(target):
    _sources, atlas, graph = _component_render_ref_inputs()
    result = find_impacted_tests(target, atlas=atlas, circular_deps=graph)
    assert result["impacted_tests"] == []


def test_component_render_path_ref_without_graph_does_not_invent_render_coverage():
    _sources, atlas, _graph = _component_render_ref_inputs()
    result = find_impacted_tests("MAIN::src/View.tsx", atlas=atlas, circular_deps={})
    assert result["impacted_tests"] == []


def test_component_render_path_ref_ambiguous_workspace_mapping_does_not_choose_first():
    _sources, atlas, graph = _component_render_ref_inputs()
    atlas["MAIN"]["files"]["other/View.tsx"] = {"workspace_rel": "src/View.tsx"}
    result = find_impacted_tests("MAIN::src/View.tsx", atlas=atlas, circular_deps=graph)
    assert result["target"] == "MAIN::src/View.tsx"
    assert result["impacted_tests"] == []


def test_component_render_path_ref_exact_atlas_key_takes_precedence_over_alias():
    _sources, atlas, graph = _component_render_ref_inputs()
    atlas["MAIN"]["files"]["src/View.tsx"] = {"workspace_rel": "other/src/View.tsx"}
    result = find_impacted_tests("MAIN::src/View.tsx", atlas=atlas, circular_deps=graph)
    assert result["target"] == "MAIN::src/View.tsx"
    assert result["impacted_tests"] == []


def test_component_render_path_ref_other_known_project_keeps_its_own_graph():
    from copy import deepcopy
    _sources, atlas, graph = _component_render_ref_inputs()
    atlas["OTHER"] = deepcopy(atlas["MAIN"])
    result = find_impacted_tests("OTHER::src/View.tsx", atlas=atlas, circular_deps=graph)
    assert result["target"] == "OTHER::View.tsx"
    assert result["impacted_tests"] == []


def test_component_render_path_ref_unicode_project_preserves_exact_identity():
    _sources, atlas, graph = _component_render_ref_inputs()
    atlas["Prójé"] = atlas.pop("MAIN")
    graph["nodes"] = {node.replace("MAIN::", "Prójé::"): meta for node, meta in graph["nodes"].items()}
    graph["edges"] = [{key: value.replace("MAIN::", "Prójé::") for key, value in edge.items()} for edge in graph["edges"]]
    result = find_impacted_tests("Prójé::src/View.tsx", atlas=atlas, circular_deps=graph)
    assert result["target"] == "Prójé::View.tsx"
    assert [row["atlas_node"] for row in result["impacted_tests"]] == ["Prójé::smoke.test.tsx"]


def test_component_render_path_test_gap_sibling_starts_from_canonical_nodes(monkeypatch):
    from tools import generate_test_gap_report as gap

    _sources, atlas, graph = _component_render_ref_inputs()
    reverse = {}
    for edge in graph["edges"]:
        reverse.setdefault(edge["target"], []).append(edge["source"])
    indexed = gap._iter_atlas_files(atlas)
    target = next(row for row in indexed if row["relative_path"] == "View.tsx")
    tests = {row["node_key"] for row in indexed if row["is_test"]}
    monkeypatch.setattr(gap, "load_atlas_data", lambda: atlas)
    monkeypatch.setattr(gap, "_default_agent_project_scope", lambda _: "MAIN")
    monkeypatch.setattr(gap, "_load_reverse_dependency_graph", lambda: reverse)
    monkeypatch.setattr(gap, "_blast_scores", lambda: {})
    monkeypatch.setattr(gap, "_active_signal_nodes", lambda: set())
    report = gap.build_test_gap_report()
    entry = next(row for row in report["source_with_test_candidates"] if row["file"] == "View.tsx")
    candidates = entry["test_candidates"]
    assert [row["node_key"] for row in candidates] == ["MAIN::smoke.test.tsx"]
    _assert_static_boundary(candidates[0], "transitive_dependency")
    assert gap._reachable_tests(target["node_key"], {}, tests) == []


def _component_render_ref_public_sample(tmp_path, monkeypatch, external, target):
    import hashlib
    from tools.core import artifact_store
    from tools.core.artifact_store import ArtifactStore
    from tools.engines import test_impact_matcher as matcher

    sources, atlas, graph = _component_render_ref_inputs()
    monkeypatch.setattr(artifact_store, "ROOT", tmp_path)
    for path, source in sources.items():
        destination = tmp_path / "src" / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(source, encoding="utf-8", newline="")
        atlas["MAIN"]["files"][path].update({
            "hash": hashlib.sha256(destination.read_bytes()).hexdigest(), "symbols": [],
            "language": "typescript", "atlas_rel_path": path, "project_key": "MAIN",
        })
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
        monkeypatch.setattr(matcher, "load_atlas_data", lambda: server.load_atlas_data(store._raw_dir))
        monkeypatch.setattr(matcher, "project_runtime_atlas", lambda data: (data, {}))
        monkeypatch.setattr(matcher, "load_json_file", lambda *_args: store.load_raw("circular_deps", {}))
    args = {"target_file": target, "target_root": str(tmp_path) if external else ""}
    return {
        "machine": json.loads(server.get_test_impact(**args, format="machine")),
        "brief": server.get_test_impact(**args, format="brief"),
    }


@pytest.mark.parametrize("external", [False, True])
@pytest.mark.parametrize("target", ["MAIN::View.tsx", "MAIN::src/View.tsx"])
def test_component_render_path_public_refs_use_same_sqlite_candidate(tmp_path, monkeypatch, external, target):
    sample = _component_render_ref_public_sample(tmp_path, monkeypatch, external, target)
    payload, brief = sample["machine"], sample["brief"]
    assert payload["target"] == "MAIN::View.tsx"
    assert payload["target_ref"] == "MAIN::src/View.tsx"
    assert payload["target_path_status"]["source_snapshot_status"] == "ok"
    assert payload["target_path_status"]["drift_check_status"] == "match"
    assert [row["atlas_node"] for row in payload["impacted_tests"]] == ["MAIN::smoke.test.tsx"]
    candidate = payload["impacted_tests"][0]
    assert candidate["repo_relative_path"] == "src/smoke.test.tsx"
    _assert_static_boundary(candidate, "transitive_dependency")
    assert candidate["source_snippet_status"] == "included"
    assert candidate["mock_declaration_evidence"]["status"] == "unknown"
    assert f'target_ref: {json.dumps(candidate["project"] + "::" + candidate["repo_relative_path"])}' in brief
    assert "not_run_by_sage" in brief and "not_established" in brief

def _service_path_inputs(monkeypatch, indexed_test=True):
    from tools.engines import circular_dependency_finder as dependency

    sources = {
        "storage.ts": "export const Storage = {read: () => 1};\n",
        "Service.ts": "import {Storage} from '@/storage';\nexport const Service = () => Storage.read();\n",
    }
    dependencies = {"storage.ts": [], "Service.ts": ["storage.ts"]}
    if indexed_test:
        sources["Service.test.ts"] = "import {Service} from './Service';\nimport {it, expect} from 'vitest';\nit('read', () => expect(Service()).toBe(1));\n"
        dependencies["Service.test.ts"] = ["Service.ts"]
    atlas = {"MAIN": {"root_path": "src", "files": {
        path: {"workspace_rel": "src/" + path} for path in sources
    }, "dependencies": dependencies}}
    monkeypatch.setattr(dependency, "load_atlas_data", lambda: atlas)
    monkeypatch.setattr(dependency, "project_runtime_atlas", lambda data: (data, {"analyzed_projects": ["MAIN"]}))
    finder = object.__new__(dependency.CircularDependencyFinder)
    adjacency, nodes, filtered, _atlas, _scope = finder._build_import_graph()
    assert filtered == []
    graph = {"nodes": {node: {} for node in nodes},
             "edges": [{"source": source, "target": target}
                       for source in sorted(adjacency) for target in sorted(adjacency[source])]}
    return sources, atlas, graph


@pytest.mark.parametrize("indexed_test", [False, True])
def test_service_path_graph_builder_keeps_declared_test_to_service_to_storage(monkeypatch, indexed_test):
    _sources, atlas, graph = _service_path_inputs(monkeypatch, indexed_test)
    expected_edges = [{"source": "MAIN::Service.ts", "target": "MAIN::storage.ts"}]
    if indexed_test:
        expected_edges.append({"source": "MAIN::Service.test.ts", "target": "MAIN::Service.ts"})
    assert {tuple(sorted(row.items())) for row in graph["edges"]} == {
        tuple(sorted(row.items())) for row in expected_edges}
    result = find_impacted_tests("MAIN::storage.ts", atlas=atlas, circular_deps=graph)
    assert [row["atlas_node"] for row in result["impacted_tests"]] == (
        ["MAIN::Service.test.ts"] if indexed_test else [])
    if indexed_test:
        _assert_static_boundary(result["impacted_tests"][0], "transitive_dependency")


def _service_path_public_sample(tmp_path, monkeypatch, external, indexed_test, public_ref):
    import hashlib
    from tools.core import artifact_store
    from tools.core.artifact_store import ArtifactStore
    from tools.engines import test_impact_matcher as matcher

    sources, atlas, graph = _service_path_inputs(monkeypatch, indexed_test)
    monkeypatch.setattr(artifact_store, "ROOT", tmp_path)
    for path, source in sources.items():
        destination = tmp_path / "src" / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(source, encoding="utf-8", newline="")
        atlas["MAIN"]["files"][path].update({
            "hash": hashlib.sha256(destination.read_bytes()).hexdigest(), "symbols": [],
            "language": "typescript", "atlas_rel_path": path, "project_key": "MAIN",
        })
    # A test in a different repository is not evidence for this snapshot.
    foreign_test = tmp_path / "different-repository" / "src" / "Service.test.ts"
    foreign_test.parent.mkdir(parents=True, exist_ok=True)
    foreign_test.write_text("import {Service} from './Service';\n", encoding="utf-8")
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
        monkeypatch.setattr(matcher, "load_atlas_data", lambda: server.load_atlas_data(store._raw_dir))
        monkeypatch.setattr(matcher, "project_runtime_atlas", lambda data: (data, {}))
        monkeypatch.setattr(matcher, "load_json_file", lambda *_args: store.load_raw("circular_deps", {}))
    samples = {}
    prefix = "MAIN::src/" if public_ref else "MAIN::"
    for path in ["storage.ts", "Service.ts", "storage.test.ts"]:
        args = {"target_file": prefix + path, "target_root": str(tmp_path) if external else ""}
        samples[path] = {"machine": json.loads(server.get_test_impact(**args, format="machine")),
                         "brief": server.get_test_impact(**args, format="brief")}
    return samples


@pytest.mark.parametrize("external", [False, True])
@pytest.mark.parametrize("indexed_test", [False, True])
@pytest.mark.parametrize("public_ref", [False, True])
def test_service_path_public_sqlite_distinguishes_production_from_absent_test(
        tmp_path, monkeypatch, external, indexed_test, public_ref):
    samples = _service_path_public_sample(tmp_path, monkeypatch, external, indexed_test, public_ref)
    for path, relation in [("storage.ts", "transitive_dependency"), ("Service.ts", "direct_import")]:
        payload, brief = samples[path]["machine"], samples[path]["brief"]
        assert payload["target"] == "MAIN::" + path
        assert payload["target_indexed"] is True and payload["target_exists"] is True
        assert payload["target_path_status"]["source_snapshot_status"] == "ok"
        assert [row["atlas_node"] for row in payload["impacted_tests"]] == (
            ["MAIN::Service.test.ts"] if indexed_test else [])
        if indexed_test:
            candidate = payload["impacted_tests"][0]
            _assert_static_boundary(candidate, relation)
            assert candidate["source_snippet_status"] == "included"
            assert candidate["mock_declaration_evidence"]["status"] == "unknown"
            assert f'target_ref: {json.dumps(candidate["project"] + "::" + candidate["repo_relative_path"])}' in brief
            assert "not_run_by_sage" in brief and "not_established" in brief
        else:
            assert "An empty impacted_tests list is not proof that no tests matter." in brief
    missing = samples["storage.test.ts"]["machine"]
    assert missing["target_indexed"] is False and missing["target_exists"] is False
    assert missing["target_grounding_status"] == "missing_or_unindexed"
    assert missing["impacted_tests"] == []
    assert "not proof that no tests matter" in samples["storage.test.ts"]["brief"]


def test_service_path_test_gap_sibling_keeps_transitive_not_executed_proof(monkeypatch):
    from tools import generate_test_gap_report as gap

    _sources, atlas, graph = _service_path_inputs(monkeypatch)
    reverse = {}
    for edge in graph["edges"]:
        reverse.setdefault(edge["target"], []).append(edge["source"])
    monkeypatch.setattr(gap, "load_atlas_data", lambda: atlas)
    monkeypatch.setattr(gap, "_default_agent_project_scope", lambda _: "MAIN")
    monkeypatch.setattr(gap, "_load_reverse_dependency_graph", lambda: reverse)
    monkeypatch.setattr(gap, "_blast_scores", lambda: {})
    monkeypatch.setattr(gap, "_active_signal_nodes", lambda: set())
    report = gap.build_test_gap_report()
    entry = next(row for row in report["source_with_test_candidates"] if row["file"] == "storage.ts")
    assert [row["node_key"] for row in entry["test_candidates"]] == ["MAIN::Service.test.ts"]
    _assert_static_boundary(entry["test_candidates"][0], "transitive_dependency")
