from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.core import cache_manager
from tools.core import atlas_typescript_inputs as inputs
from tools.engines import generate_atlas as generator
from tools.tests.test_atlas_materialization_profile import _sqlite_store


@pytest.mark.parametrize("name", ["tsconfig.json", "base.json", "biome.jsonc",
                                  "package.json", "env.d.ts", "env.d.mts", "env.d.cts"])
@pytest.mark.parametrize("mutation", ["edit", "delete", "add"])
def test_project_cache_detects_auxiliary_change_without_source_or_mtime_change(tmp_path, monkeypatch, name, mutation):
    root = tmp_path / "repo"
    root.mkdir()
    source = root / "a.ts"
    source.write_text("export const x = 1;")
    auxiliary = root / name
    if mutation != "add":
        auxiliary.write_text("old")
        os.utime(auxiliary, (1700000000, 1700000000))
    fingerprints = cache_manager.get_project_source_fingerprints({"MAIN": root})
    cache_file = tmp_path / "cache.json"
    cache_file.write_text(json.dumps({
        "project_mtimes": {"MAIN": 1}, "project_source_fingerprints": fingerprints,
        "global_config_mtime": 1, "atlas_generator_fingerprint": "current",
        "completion_authority": "committed_atlas_inputs_v1",
        "verified_atlas_snapshot_id": "snapshot",
        "atlas_state_identity": "sqlite:atlas",
        "execution_context": {"configuration_fingerprint": "config"},
    }))
    monkeypatch.setattr(cache_manager, "CACHE_FILE", cache_file)
    monkeypatch.setattr(cache_manager, "resolve_projects", lambda *a: {"MAIN": root})
    monkeypatch.setattr(cache_manager, "get_project_mtimes", lambda: {"MAIN": 1})
    monkeypatch.setattr(cache_manager, "get_config_mtime", lambda: 1)
    monkeypatch.setattr(cache_manager, "atlas_generator_fingerprint", lambda: "current")
    monkeypatch.setattr(cache_manager, "configuration_fingerprint", lambda: "config")
    monkeypatch.setattr(cache_manager, "load_raw_artifact_path",
                        lambda *a: {"state": "complete", "snapshot_id": "snapshot"})
    monkeypatch.setattr(cache_manager, "raw_artifact_content_fingerprint", lambda *a: "sqlite:atlas")
    assert cache_manager.get_stale_projects() == []
    if mutation == "delete":
        auxiliary.unlink()
    else:
        auxiliary.write_text("new")
        os.utime(auxiliary, (1700000000, 1700000000))
    assert cache_manager.get_stale_projects() == ["MAIN"]


def test_cache_uses_one_walk_and_does_not_fingerprint_skipped_outputs(tmp_path, monkeypatch):
    (tmp_path / "output").mkdir()
    (tmp_path / "output" / "report.json").write_text("old")
    (tmp_path / "base.json").write_text("{}")
    monkeypatch.setattr(cache_manager, "SKIP_DIRS", {"output"})
    walk = os.walk
    calls = []
    def observed_walk(*args, **kwargs):
        calls.append(args)
        return walk(*args, **kwargs)
    monkeypatch.setattr(cache_manager.os, "walk", observed_walk)
    before = cache_manager.get_project_source_fingerprints({"MAIN": tmp_path})
    assert len(calls) == 1
    (tmp_path / "output" / "report.json").write_text("new")
    assert cache_manager.get_project_source_fingerprints({"MAIN": tmp_path}) == before


@pytest.fixture
def atlas_run(tmp_path, monkeypatch):
    from tools.core import artifact_store, config

    root, raw = tmp_path / "repo", tmp_path / ".raw"
    root.mkdir()
    raw.mkdir()
    (root / "a.ts").write_text("export const x = 1;", encoding="utf-8")
    (root / "tsconfig.json").write_text('{"compilerOptions":{"strict":true}}')
    store = _sqlite_store(raw, inline_limit=64, part_size=32)
    monkeypatch.setattr(generator, "ROOT", root)
    monkeypatch.setattr(generator, "RAW_DIR", raw)
    monkeypatch.setattr(generator, "resolve_runtime_projects", lambda *a, **k: {"MAIN": root})
    monkeypatch.setattr(generator, "configured_project_ownership_exclusions", lambda *a, **k: {"MAIN": []})
    monkeypatch.setattr(generator, "ensure_output_dir", lambda: None)
    monkeypatch.setattr(generator, "save_json_atomic",
                        lambda path, payload, **kw: store.save_raw(Path(path).stem, payload, **kw))
    runtime = {"use_sqlite": True, "variations": {"MAIN": str(root)}}
    monkeypatch.setattr(generator, "DYNAMIC_CONFIG", runtime)
    monkeypatch.setattr(artifact_store, "DYNAMIC_CONFIG", runtime)
    monkeypatch.setattr(artifact_store, "STORE", store)
    monkeypatch.setattr(config, "PROJECT_FILTER", [])
    monkeypatch.setenv("SAGE_SYNC_SHADOW_WRITES", "1")
    baseline, calls = {}, []
    monkeypatch.setattr(generator, "load_previous_atlas", lambda: deepcopy(baseline))

    def sequence(command, **kwargs):
        calls.append(command)
        source = root / "a.ts"
        content = source.read_bytes()
        request_id = command[command.index("--request-id") + 1]
        return SimpleNamespace(returncode=0, stderr="", stdout=json.dumps({
            "batchMeta": {"protocolVersion": 1, "requestId": request_id, "filesRequested": 1, "filesReported": 1},
            "results": {source.resolve().as_posix(): [{
                "name": "__file_meta__", "type": "Meta", "start": 0, "end": 0,
                "parserStatus": "observed", "parserKind": "typescript_compiler_api",
                "semanticDepth": "ast_normalized",
                "features": ["Hash:" + hashlib.sha256(content).hexdigest()],
            }]},
        })), 0.01
    monkeypatch.setattr(generator, "run_observed_subprocess", sequence)

    def run():
        # Explicit scope preserves the old canonical Atlas without rebuilding
        # unrelated projects; the producer still makes its own reuse decision.
        monkeypatch.setattr(config, "PROJECT_FILTER", ["MAIN"])
        atlas, _, _ = generator.generate_atlas(stale_projects=["MAIN"])
        baseline.clear()
        baseline.update(deepcopy(atlas))
        return atlas
    return root, baseline, calls, run


@pytest.mark.parametrize("mutation", ["same_mtime", "config_only", "unknown_hash", "missing_context", "unchanged"])
def test_real_atlas_reuse_requires_source_and_auxiliary_identity(atlas_run, mutation):
    from tools.core.state_flow_import_index import build_resolved_module_importer_index

    root, baseline, calls, run = atlas_run
    first = run()
    assert len(calls) == 1
    assert first["MAIN"]["resolved_module_importer_index"] == build_resolved_module_importer_index(
        first["MAIN"]["files"])
    source = root / "a.ts"
    before_mtime = source.stat().st_mtime_ns
    if mutation == "same_mtime":
        source.write_text("export const x = 2;")
        os.utime(source, ns=(before_mtime, before_mtime))
    elif mutation == "config_only":
        (root / "tsconfig.json").write_text('{"compilerOptions":{"strict":false}}')
    elif mutation == "unknown_hash":
        baseline["MAIN"]["files"]["a.ts"]["hash"] = ""
    elif mutation == "missing_context":
        baseline["MAIN"]["files"]["a.ts"].pop("auxiliary_input_identity")
    second = run()
    assert len(calls) == (1 if mutation == "unchanged" else 2)
    assert second["MAIN"]["resolved_module_importer_index"] == build_resolved_module_importer_index(
        second["MAIN"]["files"])
    row = second["MAIN"]["files"]["a.ts"]
    assert row["hash"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert row["auxiliary_input_identity"] == inputs.auxiliary_context_identity(second["MAIN"]["typescript_auxiliary_inputs"])
    if mutation == "config_only":
        assert row["hash"] == first["MAIN"]["files"]["a.ts"]["hash"]
        assert row["auxiliary_input_identity"] != first["MAIN"]["files"]["a.ts"]["auxiliary_input_identity"]
        assert (second["MAIN"]["resolved_module_importer_index"]["file_identity_sha256"]
                != first["MAIN"]["resolved_module_importer_index"]["file_identity_sha256"])


def test_partial_auxiliary_inventory_cannot_authorize_cached_dependencies():
    assert inputs.auxiliary_context_identity({"version": "v1", "status": "partial"}) == ""


def test_generator_fingerprint_hashes_owned_policy_without_runtime_import_cycle(tmp_path, monkeypatch):
    from tools.core import atlas_integrity

    policy = tmp_path / "config" / "analysis_snapshot_lineage_contract.json"
    policy.parent.mkdir()
    policy.write_text('{"budget":1}', encoding="utf-8")
    monkeypatch.setattr(atlas_integrity, "__file__", str(tmp_path / "tools/core/atlas_integrity.py"))
    def forbidden():
        pytest.fail("Generator identity must not import runtime input-policy consumers")
    monkeypatch.setattr(inputs, "auxiliary_input_policy", forbidden)
    monkeypatch.setattr(inputs, "resolution_input_policy", forbidden)
    before = atlas_integrity.generator_fingerprint()
    assert atlas_integrity.generator_fingerprint() == before
    policy.write_text('{"budget":2}', encoding="utf-8")
    assert atlas_integrity.generator_fingerprint() != before


def test_identity_read_failure_resequences_and_is_visible(atlas_run, monkeypatch):
    _, _, calls, run = atlas_run
    run()
    events = []
    def unavailable(*args, **kwargs):
        raise PermissionError("fixture read denied")
    monkeypatch.setattr(generator, "_live_atlas_text_hash", unavailable)
    monkeypatch.setattr(generator, "record_honesty_event", lambda **event: events.append(event))
    run()
    assert len(calls) == 2
    assert any(event["operation"] == "verify_cached_source_identity" for event in events)


def test_interrupted_retry_refreshes_config_dependencies_but_reuses_raw_ast(atlas_run, monkeypatch):
    root, _, calls, run = atlas_run
    save = generator.save_json_atomic
    def interrupted(path, payload, **kwargs):
        if Path(path).stem == "atlas":
            raise RuntimeError("fixture interrupted commit")
        return save(path, payload, **kwargs)
    monkeypatch.setattr(generator, "save_json_atomic", interrupted)
    with pytest.raises(RuntimeError, match="interrupted commit"):
        run()
    assert len(calls) == 1
    (root / "tsconfig.json").write_text('{"compilerOptions":{"strict":false}}')
    monkeypatch.setattr(generator, "save_json_atomic", save)
    atlas = run()
    assert len(calls) == 1  # Raw syntax work is independent of target tsconfig.
    assert atlas["MAIN"]["files"]["a.ts"]["auxiliary_input_identity"] == inputs.auxiliary_context_identity(
        atlas["MAIN"]["typescript_auxiliary_inputs"])


def test_completion_without_atlas_authority_preserves_previous_cache(tmp_path, monkeypatch):
    cache = tmp_path / "cache.json"
    cache.write_text('{"previous":"evidence"}')
    monkeypatch.setattr(cache_manager, "CACHE_FILE", cache)
    monkeypatch.setattr(cache_manager, "_project_mtimes_from_atlas", lambda: {"MAIN": 1})
    monkeypatch.setattr(cache_manager, "resolve_projects", lambda *a: {"MAIN": tmp_path})
    def forbidden(*args, **kwargs):
        pytest.fail("A run without completed Atlas authority must not fingerprint live inputs")
    monkeypatch.setattr(cache_manager, "get_project_source_fingerprints", forbidden)
    assert cache_manager.update_cache() is False
    assert cache.read_text() == '{"previous":"evidence"}'


@pytest.fixture
def completed_atlas_cache(atlas_run, monkeypatch):
    root, baseline, calls, run = atlas_run
    monkeypatch.setattr(cache_manager, "ROOT", root)
    monkeypatch.setattr(cache_manager, "RAW_DIR", generator.RAW_DIR)
    monkeypatch.setattr(cache_manager, "CACHE_FILE", generator.RAW_DIR / ".pipeline_cache.json")
    monkeypatch.setattr(cache_manager, "get_config_mtime", lambda: 1)
    monkeypatch.setattr(cache_manager, "resolve_projects", lambda *a: {"MAIN": root})
    monkeypatch.setattr(cache_manager.runtime_config, "DYNAMIC_CONFIG", generator.DYNAMIC_CONFIG)
    context = cache_manager.cache_execution_context()
    run()
    completion = cache_manager.completed_atlas_identity()
    def complete(**kwargs):
        identity = kwargs.pop("atlas_completion", completion)
        return cache_manager.update_cache(atlas_completion=identity, execution_context=context, **kwargs)
    return root, complete


def test_completion_binds_real_sqlite_atlas_in_one_existing_walk(completed_atlas_cache, monkeypatch):
    root, complete = completed_atlas_cache
    walk, walks = os.walk, []
    def observed(*args, **kwargs):
        walks.append(args)
        return walk(*args, **kwargs)
    monkeypatch.setattr(cache_manager.os, "walk", observed)
    assert complete() is True
    assert len(walks) == 1
    cache = json.loads(cache_manager.CACHE_FILE.read_text())
    assert cache["completion_authority"] == "committed_atlas_inputs_v1"
    assert cache["verified_atlas_snapshot_id"]
    assert cache_manager.get_stale_projects() == []
    source = root / "a.ts"
    before = source.stat().st_mtime_ns
    source.write_text("export const x = 2;")
    os.utime(source, ns=(before, before))
    assert cache_manager.get_stale_projects() == ["MAIN"]


@pytest.mark.parametrize("mutation", [
    "source_edit", "source_add", "source_delete", "config_edit", "config_add", "config_delete",
])
def test_completion_cannot_bless_edits_after_atlas_generation(completed_atlas_cache, mutation):
    root, complete = completed_atlas_cache
    assert complete() is True
    previous = cache_manager.CACHE_FILE.read_bytes()
    source = root / ("a.ts" if mutation.startswith("source") else "tsconfig.json")
    if mutation.endswith("add"):
        (root / ("b.ts" if mutation.startswith("source") else "base.json")).write_text("{}")
    elif mutation.endswith("delete"):
        source.unlink()
    else:
        before = source.stat().st_mtime_ns
        source.write_text("changed")
        os.utime(source, ns=(before, before))
    assert complete() is False
    assert cache_manager.CACHE_FILE.read_bytes() == previous
    assert cache_manager.get_stale_projects() == ["MAIN"]


def test_failed_step_preserves_baseline_without_walking(completed_atlas_cache, monkeypatch):
    _, complete = completed_atlas_cache
    def forbidden(*args, **kwargs):
        pytest.fail("A failed run must not refresh the cache")
    monkeypatch.setattr(cache_manager, "get_project_source_fingerprints", forbidden)
    assert complete(failed_steps={"Audit"}) is False
    assert not cache_manager.CACHE_FILE.exists()


@pytest.mark.parametrize("phase", ["before_verification", "during_verification"])
def test_completion_rejects_producer_config_change(completed_atlas_cache, monkeypatch, phase):
    _, complete = completed_atlas_cache
    if phase == "before_verification":
        monkeypatch.setattr(cache_manager, "get_config_mtime", lambda: 2)
    else:
        read = cache_manager.get_project_source_fingerprints
        def changed(*args, **kwargs):
            result = read(*args, **kwargs)
            monkeypatch.setattr(cache_manager, "get_config_mtime", lambda: 2)
            return result
        monkeypatch.setattr(cache_manager, "get_project_source_fingerprints", changed)
    assert complete() is False
    assert not cache_manager.CACHE_FILE.exists()


def test_completion_rejects_replaced_commit(completed_atlas_cache, monkeypatch):
    _, complete = completed_atlas_cache
    load = cache_manager.load_raw_artifact_path
    reads = []
    def replaced(*args, **kwargs):
        result = load(*args, **kwargs)
        reads.append(True)
        if len(reads) > 1:
            return dict(result, snapshot_id="concurrently-replaced")
        return result
    monkeypatch.setattr(cache_manager, "load_raw_artifact_path", replaced)
    assert complete() is False
    assert not cache_manager.CACHE_FILE.exists()


@pytest.mark.parametrize("failure", ["missing", "wrong_hash", "incomplete", "wrong_kind", "wrong_config"])
def test_completion_rejects_invalid_commit(completed_atlas_cache, monkeypatch, failure):
    _, complete = completed_atlas_cache
    commit = cache_manager.load_raw_artifact_path(cache_manager.RAW_DIR / "atlas_commit.json", {})
    if failure == "missing":
        commit = {}
    elif failure == "wrong_hash":
        commit["atlas_sha256"] = "0" * 64
    elif failure == "incomplete":
        commit["state"] = "partial"
    elif failure == "wrong_kind":
        commit["meta"]["kind"] = "unrelated"
    else:
        commit["configuration_fingerprint"] = "changed"
    monkeypatch.setattr(cache_manager, "load_raw_artifact_path", lambda *a: commit)
    assert complete() is False
    assert not cache_manager.CACHE_FILE.exists()


@pytest.mark.parametrize("failure", ["source", "walk"])
def test_read_failure_never_becomes_stable_cache_evidence(completed_atlas_cache, monkeypatch, failure):
    _, complete = completed_atlas_cache
    if failure == "source":
        import builtins
        original = builtins.open
        def denied(path, *args, **kwargs):
            if Path(path).name == "a.ts":
                raise PermissionError("fixture source denied")
            return original(path, *args, **kwargs)
        monkeypatch.setattr(builtins, "open", denied)
    else:
        def denied_walk(path, *, onerror):
            onerror(PermissionError("fixture directory denied"))
            return iter(())
        monkeypatch.setattr(cache_manager.os, "walk", denied_walk)
    assert complete() is False
    assert not cache_manager.CACHE_FILE.exists()


def test_changed_commit_invalidates_previously_current_baseline(completed_atlas_cache, monkeypatch):
    _, complete = completed_atlas_cache
    assert complete() is True
    assert cache_manager.get_stale_projects() == []
    commit = cache_manager.load_raw_artifact_path(cache_manager.RAW_DIR / "atlas_commit.json", {})
    monkeypatch.setattr(cache_manager, "load_raw_artifact_path", lambda *a: dict(commit, snapshot_id="different"))
    assert cache_manager.get_stale_projects() == ["MAIN"]


def test_same_mtime_changed_sage_config_invalidates_baseline(completed_atlas_cache, monkeypatch):
    _, complete = completed_atlas_cache
    assert complete() is True
    monkeypatch.setattr(cache_manager, "configuration_fingerprint", lambda: "changed")
    assert cache_manager.get_stale_projects() == ["MAIN"]


@pytest.mark.parametrize("encoding", ["utf8", "utf8_bom_crlf", "empty", "invalid_utf8"])
@pytest.mark.parametrize("algorithm", ["md5", "sha256"])
def test_completion_source_identity_uses_existing_algorithms(tmp_path, monkeypatch, encoding, algorithm):
    data = {"utf8": "export const x = 'ü';".encode(), "utf8_bom_crlf": b"\xef\xbb\xbf// a\r\n",
            "empty": b"", "invalid_utf8": b"// \xff"}[encoding]
    source = tmp_path / "a.ts"
    source.write_bytes(data)
    auxiliary = inputs.capture_auxiliary_inputs(tmp_path, [(str(source), source.name, source.name)])
    atlas = {"MAIN": {"files": {"a.ts": {"hash": hashlib.new(algorithm, data).hexdigest()}},
                      "typescript_auxiliary_inputs": auxiliary}}
    monkeypatch.setattr(cache_manager, "resolve_projects", lambda *a: {"MAIN": tmp_path})
    result = cache_manager.get_project_source_fingerprints({"MAIN": tmp_path}, expected_atlas=atlas)
    assert bool(result["MAIN"]) is (encoding != "invalid_utf8")


@pytest.mark.parametrize("status", ["partial", "unavailable_surgical_scan"])
def test_incomplete_auxiliary_inventory_cannot_authorize_completion(tmp_path, monkeypatch, status):
    inventory = inputs.capture_auxiliary_inputs(tmp_path, [])
    inventory["status"] = status
    monkeypatch.setattr(cache_manager, "resolve_projects", lambda *a: {"MAIN": tmp_path})
    assert cache_manager.get_project_source_fingerprints(
        {"MAIN": tmp_path}, expected_atlas={"MAIN": {"files": {}, "typescript_auxiliary_inputs": inventory}},
    ) == {"MAIN": ""}


@pytest.mark.parametrize("declared", [False, True])
def test_cache_walk_shares_nested_project_ownership(tmp_path, monkeypatch, declared):
    from tools.core.repository_topology import configured_project_ownership_exclusions
    child = tmp_path / "child"
    child.mkdir()
    (child / "config.json").write_text("old")
    roots = {"MAIN": tmp_path} if declared else {"MAIN": tmp_path, "CHILD": child}
    config = {"_repository_topology": {"project_ownership_exclusions": {"MAIN": ["child"]}}} if declared else {}
    monkeypatch.setattr(cache_manager, "ROOT", tmp_path)
    monkeypatch.setattr(cache_manager, "resolve_projects", lambda *a: roots)
    monkeypatch.setattr(cache_manager.runtime_config, "DYNAMIC_CONFIG", config)
    assert configured_project_ownership_exclusions(roots, root=tmp_path, dynamic_config=config)["MAIN"] == [child]
    before = cache_manager.get_project_source_fingerprints({"MAIN": tmp_path})
    (child / "config.json").write_text("new")
    assert cache_manager.get_project_source_fingerprints({"MAIN": tmp_path}) == before


@pytest.mark.parametrize("unselected", ["unchanged", "changed_atlas", "absent_atlas"])
def test_scoped_completion_carries_only_unchanged_atlas_evidence(completed_atlas_cache, monkeypatch, unselected):
    from tools.core import artifact_store
    from tools.core.atlas_integrity import build_atlas_commit
    root, complete = completed_atlas_cache
    other = root.parent / "other"
    other.mkdir()
    config = other / "config.json"
    config.write_text("{}")
    roots = {"MAIN": root, "OTHER": other}
    monkeypatch.setattr(cache_manager, "resolve_projects",
                        lambda _root, selected=None: {k: v for k, v in roots.items() if not selected or k in selected})
    atlas = artifact_store.STORE.load_raw("atlas", {})
    atlas["OTHER"] = {
        "project": {"root": str(other)}, "files": {},
        "typescript_auxiliary_inputs": inputs.capture_auxiliary_inputs(other, [(str(config), config.name, config.name)]),
    }
    def persist():
        profile = artifact_store.STORE.save_raw("atlas", atlas)
        commit = build_atlas_commit(atlas, atlas_sha256=profile.get("state_payload_sha256"))
        artifact_store.STORE.save_raw("atlas_commit", commit)
    persist()
    monkeypatch.setattr(cache_manager.runtime_config, "PROJECT_FILTER", [])
    assert complete(atlas_completion=cache_manager.completed_atlas_identity()) is True
    assert cache_manager.get_stale_projects() == []
    monkeypatch.setattr(cache_manager.runtime_config, "PROJECT_FILTER", ["MAIN"])
    if unselected == "changed_atlas":
        config.write_text('{"changed":true}')
        atlas["OTHER"]["typescript_auxiliary_inputs"] = inputs.capture_auxiliary_inputs(
            other, [(str(config), config.name, config.name)])
    elif unselected == "absent_atlas":
        atlas.pop("OTHER")
    persist()
    assert complete(atlas_completion=cache_manager.completed_atlas_identity()) is True
    cache = json.loads(cache_manager.CACHE_FILE.read_text())
    assert cache["verified_projects"] == ["MAIN"]
    assert ("OTHER" in cache["project_source_fingerprints"]) is (unselected == "unchanged")
    monkeypatch.setattr(cache_manager.runtime_config, "PROJECT_FILTER", [])
    assert cache_manager.get_stale_projects() == ([] if unselected == "unchanged" else ["OTHER"])


def test_generator_change_invalidates_authorized_baseline(completed_atlas_cache, monkeypatch):
    _, complete = completed_atlas_cache
    assert complete() is True
    monkeypatch.setattr(cache_manager, "atlas_generator_fingerprint", lambda: "new-producer")
    assert cache_manager.get_stale_projects() == ["MAIN"]


def test_unknown_source_hash_cannot_authorize_completion(tmp_path, monkeypatch):
    source = tmp_path / "a.ts"
    source.write_text("export const x = 1;")
    monkeypatch.setattr(cache_manager, "resolve_projects", lambda *a: {"MAIN": tmp_path})
    atlas = {"MAIN": {"files": {"a.ts": {"hash": ""}},
                      "typescript_auxiliary_inputs": inputs.capture_auxiliary_inputs(tmp_path, [])}}
    assert cache_manager.get_project_source_fingerprints({"MAIN": tmp_path}, expected_atlas=atlas) == {"MAIN": ""}


def test_completion_rejects_commit_replaced_before_verification(completed_atlas_cache, monkeypatch):
    _, complete = completed_atlas_cache
    commit = cache_manager.load_raw_artifact_path(cache_manager.RAW_DIR / "atlas_commit.json", {})
    monkeypatch.setattr(cache_manager, "load_raw_artifact_path", lambda *a: dict(commit, snapshot_id="another-run"))
    assert complete() is False
    assert not cache_manager.CACHE_FILE.exists()


def test_legacy_cache_requires_migration(completed_atlas_cache):
    _, complete = completed_atlas_cache
    assert complete() is True
    cache = json.loads(cache_manager.CACHE_FILE.read_text())
    cache.pop("completion_authority")
    cache_manager.CACHE_FILE.write_text(json.dumps(cache))
    assert cache_manager.get_stale_projects() == ["MAIN"]
