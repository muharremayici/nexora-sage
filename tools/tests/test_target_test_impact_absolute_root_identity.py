from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.mcp import server
from tools.tests.test_target_test_impact_evidence_boundary import _assert_static_boundary


def _configured_root_identity_sample(tmp_path, monkeypatch, external, absolute_roots):
    import hashlib
    from tools.core import artifact_store, config, projects_registry
    from tools.core.artifact_store import ArtifactStore
    from tools.engines import test_impact_matcher as matcher

    sources = {
        "MAIN": {"load.ts": "export const load = 1;\n",
                 "load.test.ts": "import {load} from './load'; // mainOwnedProbe\nimport {test, expect} from 'vitest';\ntest('mainOwnedProbe', () => expect(load).toBe(1));\n"},
        "OTHER": {"load.test.ts": "import {load} from '../MAIN/load'; // otherOwnedProbe\nimport {test, expect} from 'vitest';\ntest('otherOwnedProbe', () => expect(load).toBe(1));\n"},
    }
    for project, files in sources.items():
        (tmp_path / project).mkdir()
        for path, text in files.items():
            (tmp_path / project / path).write_text(text, encoding="utf-8", newline="")
    hints = {project: str(tmp_path / project) if absolute_roots else project for project in sources}
    monkeypatch.setattr(projects_registry, "PROJECTS", {project: [path] for project, path in hints.items()})
    monkeypatch.setattr(config, "PROJECT_FILTER", set())
    roots = projects_registry.resolve_runtime_projects(tmp_path)
    assert roots == {project: tmp_path / project for project in sources}
    atlas = {project: {"files": {
        path: {"hash": hashlib.sha256(text.encode()).hexdigest(), "symbols": [],
               "language": "typescript", "project_key": project, "atlas_rel_path": path,
               "workspace_rel": (roots[project] / path).relative_to(tmp_path).as_posix(),
               "repo_relative_path": (roots[project] / path).relative_to(tmp_path).as_posix()}
        for path, text in files.items()}} for project, files in sources.items()}
    graph = {"nodes": {f"{project}::{path}": {} for project, files in sources.items() for path in files},
             "edges": [{"source": f"{project}::load.test.ts", "target": "MAIN::load.ts"} for project in sources]}
    monkeypatch.setattr(artifact_store, "ROOT", tmp_path)
    monkeypatch.setattr(artifact_store, "DYNAMIC_CONFIG", {**artifact_store.DYNAMIC_CONFIG, "variations": hints})
    store = ArtifactStore(raw_dir=tmp_path / ".raw")
    store.use_sqlite, store.backend = True, "hybrid_sqlite"
    store._ensure_schema()
    store.save_raw("atlas", atlas)
    store.save_raw("circular_deps", graph)
    monkeypatch.setattr(server, "_raw_dir_for_target", lambda _: store._raw_dir)
    monkeypatch.setattr(server, "_analysis_root_display", lambda _: str(tmp_path))
    if external:
        monkeypatch.setattr(matcher, "load_atlas_data", lambda: pytest.fail("external read host Atlas"))
        monkeypatch.setattr(matcher, "load_json_file", lambda *_: pytest.fail("external read host graph"))
    else:
        monkeypatch.setattr(matcher, "load_atlas_data", lambda: atlas)
        monkeypatch.setattr(matcher, "project_runtime_atlas", lambda data: (data, {}))
        monkeypatch.setattr(matcher, "load_json_file", lambda *_: graph)
    args = {"target_file": "MAIN::load.ts", "target_root": str(tmp_path) if external else ""}
    return json.loads(server.get_test_impact(**args, format="machine")), server.get_test_impact(**args, format="brief")


@pytest.mark.parametrize("external", [False, True])
@pytest.mark.parametrize("absolute_roots", [False, True])
def test_configured_project_root_identity_is_relative_and_not_live_duplicated(tmp_path, monkeypatch, external, absolute_roots):
    payload, brief = _configured_root_identity_sample(tmp_path, monkeypatch, external, absolute_roots)
    assert payload["target_file"] == "MAIN/load.ts"
    assert payload["target_exists"] is True and payload["target_indexed"] is True
    rows = payload["impacted_tests"]
    assert len(rows) == 2
    assert {row["atlas_node"] for row in rows} == {"MAIN::load.test.ts", "OTHER::load.test.ts"}
    for row in rows:
        project = row["project"]
        assert row["file"] == row["repo_relative_path"] == f"{project}/load.test.ts"
        assert row["repo_relative_path"] in row["run_command"]
        assert str(tmp_path).replace("\\", "/") not in row["run_command"]
        assert row["source_snippet_status"] == "included"
        assert f"{project.lower()}OwnedProbe" in "\n".join(snippet["code"] for snippet in row["source_snippets"])
        _assert_static_boundary(row, "direct_import")
    assert str(tmp_path).replace("\\", "/") not in brief.split("impacted_tests:")[-1]
    public = json.loads(server.get_test_impact(
        "MAIN::MAIN/load.ts", str(tmp_path) if external else "", format="machine"))
    assert public["target"] == payload["target"]
    assert [(row["atlas_node"], row["file"]) for row in public["impacted_tests"]] == [
        (row["atlas_node"], row["file"]) for row in rows]
    # One actual sibling consumer of the shared context, not a broader replay.
    inspected = json.loads(server.inspect_file(
        "MAIN::MAIN/load.ts", target_root=str(tmp_path) if external else "", format="machine"))
    assert inspected["target_file_context"][0]["atlas_node"] == "MAIN::load.ts"
    assert inspected["target_file_context"][0]["repo_relative_path"] == "MAIN/load.ts"


def _absolute_sqlite_context_fixture(tmp_path, monkeypatch, files):
    import sqlite3
    raw_dir = tmp_path / ".raw"
    raw_dir.mkdir()
    with sqlite3.connect(raw_dir / "codemaps.db") as conn:
        conn.execute("CREATE TABLE projects (project_key TEXT, path TEXT)")
        conn.execute("CREATE TABLE files (file_id INTEGER, project_key TEXT, rel_path TEXT)")
        conn.execute("INSERT INTO projects VALUES (?, ?)", ("MAIN", str(tmp_path / "MAIN")))
        conn.executemany("INSERT INTO files VALUES (?, ?, ?)", [(i, "MAIN", path) for i, path in enumerate(files, 1)])
    monkeypatch.setattr(server, "_atlas", lambda **_: {"MAIN": {"files": files}})
    monkeypatch.setattr(Path, "iterdir", lambda *_: pytest.fail("context must not walk the target"))
    return raw_dir


@pytest.mark.parametrize("workspace", [None, "", "../other/load.ts", "/foreign/load.ts", "C:/foreign/load.ts"])
def test_absolute_root_context_missing_or_escaping_metadata_is_not_a_relative_path(tmp_path, monkeypatch, workspace):
    raw_dir = _absolute_sqlite_context_fixture(tmp_path, monkeypatch, {"load.ts": {"workspace_rel": workspace}})
    assert server._sqlite_file_context_from_raw(raw_dir, "MAIN::load.ts") is None
    assert server._sqlite_file_context_from_raw(raw_dir, "load.ts") is None


def test_absolute_root_context_rejects_ambiguous_display_without_guessing_canonical_identity(tmp_path, monkeypatch):
    files = {"a.ts": {"workspace_rel": "MAIN/shared.ts"}, "b.ts": {"workspace_rel": "MAIN/shared.ts"}}
    raw_dir = _absolute_sqlite_context_fixture(tmp_path, monkeypatch, files)
    assert server._sqlite_file_context_from_raw(raw_dir, "MAIN::MAIN/shared.ts") is None
    canonical = server._sqlite_file_context_from_raw(raw_dir, "MAIN::a.ts")
    assert canonical[0] == "MAIN::a.ts"
    assert canonical[1]["repo_relative_path_source"] == "declared_atlas_workspace_rel"
    assert server._sqlite_file_context_from_raw(raw_dir, "OTHER::a.ts") is None


def test_absolute_root_context_exact_index_identity_precedes_a_display_collision(tmp_path, monkeypatch):
    files = {"load.ts": {"workspace_rel": "MAIN/load.ts"}, "other.ts": {"workspace_rel": "load.ts"}}
    raw_dir = _absolute_sqlite_context_fixture(tmp_path, monkeypatch, files)
    canonical = server._sqlite_file_context_from_raw(raw_dir, "MAIN::load.ts")
    public = server._sqlite_file_context_from_raw(raw_dir, "MAIN::MAIN/load.ts")
    assert canonical == public
    assert canonical[0] == "MAIN::load.ts"
    assert canonical[1]["repo_relative_path"] == "MAIN/load.ts"
