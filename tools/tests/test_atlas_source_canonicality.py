"""Source-only TypeScript physical-path evidence across Atlas and its consumer."""

from pathlib import Path

import pytest

from tools.core.repository_topology import project_owned_path_identity
from tools.core.typescript_source_binding import semantic_positive_resolution_errors
from tools.tests.test_atlas_materialization_profile import _sqlite_store
from tools.tests.test_atlas_typescript_inputs import positive_probe, positive_resolution_fixture


def test_owned_source_path_identity_reuses_ownership_resolution(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    target = root / "actual.ts"
    target.write_text("export const value = 1;")
    assert project_owned_path_identity(root, "actual.ts", excluded_roots=[]) == (True, True)
    link = root / "alias.ts"
    try:
        link.symlink_to(target)
    except OSError as error:
        pytest.skip(f"Host cannot create file symlink fixture: {error}")
    assert project_owned_path_identity(root, "alias.ts", excluded_roots=[]) == (True, False)
    assert project_owned_path_identity(root, "../outside.ts", excluded_roots=[]) == (False, False)
    outside = tmp_path / "outside.ts"
    outside.write_text(target.read_text())
    external_link = root / "external.ts"
    external_link.symlink_to(outside)
    assert project_owned_path_identity(root, "external.ts", excluded_roots=[]) == (False, False)


def test_real_atlas_refreshes_source_canonicality_when_same_text_becomes_link(tmp_path, monkeypatch):
    from tools.core import artifact_store, config
    from tools.engines import generate_atlas as generator

    root, companion, raw = tmp_path / "repo", tmp_path / "companion", tmp_path / "raw"
    root.mkdir()
    companion.mkdir()
    raw.mkdir()
    source = root / "a.ts"
    source.write_text("export const value = 1;")
    (root / "actual.txt").write_text(source.read_text())
    store = _sqlite_store(raw, inline_limit=64, part_size=32)
    previous = {}
    monkeypatch.setattr(generator, "ROOT", root)
    monkeypatch.setattr(generator, "RAW_DIR", raw)
    monkeypatch.setattr(generator, "resolve_runtime_projects",
                        lambda *a, **k: {"MAIN": root, "COMPANION": companion})
    monkeypatch.setattr(generator, "configured_project_ownership_exclusions",
                        lambda *a, **k: {"MAIN": [], "COMPANION": []})
    monkeypatch.setattr(generator, "load_previous_atlas", lambda: previous)
    monkeypatch.setattr(generator, "ensure_output_dir", lambda: None)
    monkeypatch.setattr(generator, "save_json_atomic",
                        lambda path, payload, **kw: store.save_raw(Path(path).stem, payload, **kw))
    monkeypatch.setattr(generator, "DYNAMIC_CONFIG", {"use_sqlite": True})
    monkeypatch.setattr(artifact_store, "STORE", store)
    monkeypatch.setattr(config, "PROJECT_FILTER", [])
    monkeypatch.setenv("SAGE_SYNC_SHADOW_WRITES", "1")
    first, _, _ = generator.generate_atlas(stale_projects=["MAIN", "COMPANION"])
    assert first["MAIN"]["files"]["a.ts"]["canonical_file_path"] is True

    source.unlink()
    try:
        source.symlink_to(root / "actual.txt")
    except OSError as error:
        pytest.skip(f"Host cannot create file symlink fixture: {error}")
    previous = first
    monkeypatch.setattr(generator, "run_observed_subprocess",
                        lambda *a, **k: pytest.fail("identical source text should lift cached AST"))
    second, _, _ = generator.generate_atlas(stale_projects=["MAIN"])
    assert second["MAIN"]["files"]["a.ts"]["canonical_file_path"] is False
    assert store.load_raw("atlas", {})["MAIN"]["files"]["a.ts"]["canonical_file_path"] is False


def test_source_realpath_requires_canonical_atlas_source_bit(tmp_path):
    project, context = positive_resolution_fixture(tmp_path)
    source = project["files"]["src/a.ts"]
    context["observations"] = [positive_probe(
        tmp_path, "realpath", "src/a.ts", (tmp_path / "src/a.ts").as_posix())]
    assert semantic_positive_resolution_errors(context, project)
    source["canonical_file_path"] = True
    assert semantic_positive_resolution_errors(context, project) == []
    source["canonical_file_path"] = False
    assert semantic_positive_resolution_errors(context, project)
    source["canonical_file_path"] = True
    source["language"] = "python"
    assert semantic_positive_resolution_errors(context, project)
    source.pop("canonical_file_path")
    assert semantic_positive_resolution_errors(context, project)
