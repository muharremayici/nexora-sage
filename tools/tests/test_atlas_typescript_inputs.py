from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path

import pytest

from tools.core import atlas_typescript_inputs as inputs
from tools.core.atlas_integrity import build_atlas_commit
from tools.core.typescript_source_binding import semantic_auxiliary_input_errors, semantic_negative_resolution_errors
from tools.core.typescript_source_binding import semantic_positive_resolution_errors
from tools.tests.test_atlas_materialization_profile import _sqlite_store
from tools.tests.test_typescript_source_binding import collect, write_receipt


def walk_entries(root, names):
    return [(str(root / name), Path(name).name, name) for name in names]


@pytest.mark.parametrize("data", [b"", b"{}", b"\xef\xbb\xbf{}\r\n", "declare const x: 'ç';\r\n".encode()])
def test_capture_exact_bytes_and_typescript_host_text_without_rescan(tmp_path, monkeypatch, data):
    (tmp_path / "env.d.ts").write_bytes(data)
    monkeypatch.setattr("os.walk", lambda *a, **k: pytest.fail("second repository walk"))
    inventory = inputs.capture_auxiliary_inputs(tmp_path, walk_entries(tmp_path, ["env.d.ts"]))
    assert inventory["status"] == "captured"
    row = inventory["files"]["env.d.ts"]
    assert row["raw_sha256"] == hashlib.sha256(data).hexdigest()
    assert row["host_text_sha256"] == hashlib.sha256(data.decode("utf-8-sig").encode()).hexdigest()
    assert row["canonical_file_path"] is True
    assert "content" not in row
    assert inventory["negative_resolution_authority"] is False


def test_capture_respects_existing_owned_inventory_and_surgical_invalidation(tmp_path):
    (tmp_path / "included.json").write_text("{}")
    (tmp_path / "excluded.json").write_text("{}")
    entries = walk_entries(tmp_path, ["included.json"])
    assert set(inputs.capture_auxiliary_inputs(tmp_path, entries)["files"]) == {"included.json"}
    surgical = inputs.capture_auxiliary_inputs(tmp_path, entries, surgical=True)
    assert surgical["status"] == "unavailable_surgical_scan"
    assert surgical["files"] == {}


def test_auxiliary_capture_marks_internal_file_link_noncanonical(tmp_path):
    target = tmp_path / "actual.json"
    target.write_text("{}")
    link = tmp_path / "alias.json"
    try:
        link.symlink_to(target)
    except OSError as error:
        pytest.skip(f"Host cannot create file symlink fixture: {error}")
    inventory = inputs.capture_auxiliary_inputs(tmp_path, walk_entries(tmp_path, ["alias.json"]))
    assert inventory["files"]["alias.json"]["status"] == "ok"
    assert inventory["files"]["alias.json"]["canonical_file_path"] is False


@pytest.mark.parametrize("case", ["missing", "directory", "encoding", "outside", "mismatched_path"])
def test_capture_failure_paths_never_fabricate_identity(tmp_path, case):
    root = tmp_path / "repo"
    root.mkdir()
    source = root / "config.json"
    if case == "directory":
        source.mkdir()
    elif case == "encoding":
        source.write_bytes(b"\xff\xfe{\x00}\x00")
    elif case in {"outside", "mismatched_path"}:
        source = tmp_path / "outside.json" if case == "outside" else root / "different.json"
        source.write_text("{}")
    result = inputs.capture_auxiliary_inputs(root, [(str(source), "config.json", "config.json")])
    assert result["status"] == "partial"
    assert result["files"]["config.json"]["status"] != "ok"
    assert "host_text_sha256" not in result["files"]["config.json"]


def test_capture_budgets_are_deterministic_and_omissions_explicit(tmp_path, monkeypatch):
    policy = deepcopy(inputs.auxiliary_input_policy())
    policy.update(max_files=2, max_file_bytes=4, max_total_bytes=4)
    monkeypatch.setattr(inputs, "auxiliary_input_policy", lambda: policy)
    for name in ["a.json", "b.json", "c.json"]:
        (tmp_path / name).write_bytes(b"abcdef")
    entries = walk_entries(tmp_path, ["c.json", "b.json", "a.json"])
    result = inputs.capture_auxiliary_inputs(tmp_path, entries)
    assert result == inputs.capture_auxiliary_inputs(tmp_path, reversed(entries))
    assert result["status"] == "partial"
    assert result["selected_files"] == 3 and result["omitted_files"] == 1
    assert result["bytes_read"] == 5  # One extra byte detects a growing/oversized file.
    assert result["files"]["a.json"]["status"] == "byte_budget_exceeded"
    assert result["files"]["b.json"]["status"] == "total_byte_budget_exceeded"


def test_auxiliary_change_changes_snapshot_but_not_analysis_file_population(tmp_path):
    source = tmp_path / "tsconfig.json"
    source.write_text("{}")
    entries = walk_entries(tmp_path, ["tsconfig.json"])
    atlas = {"MAIN": {"project": {"root": str(tmp_path)}, "files": {},
                     "typescript_auxiliary_inputs": inputs.capture_auxiliary_inputs(tmp_path, entries)}}
    before = build_atlas_commit(atlas)
    source.write_text('{"compilerOptions":{"strict":true}}')
    atlas["MAIN"]["typescript_auxiliary_inputs"] = inputs.capture_auxiliary_inputs(tmp_path, entries)
    after = build_atlas_commit(atlas)
    assert before["snapshot_id"] != after["snapshot_id"]
    assert before["counts"] == after["counts"]
    assert after["counts"]["files"] == 0


def test_auxiliary_inventory_survives_existing_partitioned_sqlite(tmp_path):
    source = tmp_path / "tsconfig.json"
    source.write_text("{}")
    inventory = inputs.capture_auxiliary_inputs(tmp_path, walk_entries(tmp_path, ["tsconfig.json"]))
    atlas = {"MAIN": {"project": {"root": str(tmp_path)}, "files": {},
                     "typescript_auxiliary_inputs": inventory}}
    store = _sqlite_store(tmp_path / "raw", inline_limit=64, part_size=32)
    profile = store._save_payload_to_state_table("atlas", atlas)
    assert profile["state_payload_storage_mode"] == "partitioned_json_v1"
    source.unlink()
    assert store.load_raw("atlas", {}) == atlas


@pytest.mark.parametrize("boundary", ["normal", "excluded", "unreadable"])
def test_real_atlas_ingests_auxiliary_inputs_without_analyzing_them(tmp_path, monkeypatch, boundary):
    from tools.core import artifact_store, config
    from tools.engines import generate_atlas as generator

    root, raw = tmp_path / "repo", tmp_path / "raw"
    root.mkdir()
    raw.mkdir()
    (root / "tsconfig.json").write_text('{"files":["env.d.ts"]}')
    (root / "env.d.ts").write_text("declare const ambient: string;")
    if boundary != "normal":
        child = root / "child"
        child.mkdir()
        (child / "hidden.json").write_text("{}")
    if boundary == "unreadable":
        original_scandir = generator.os.scandir

        def deny_child(path):
            if Path(path) == child:
                raise PermissionError("fixture enumeration denied")
            return original_scandir(path)

        monkeypatch.setattr(generator.os, "scandir", deny_child)
    store = _sqlite_store(raw, inline_limit=64, part_size=32)
    monkeypatch.setattr(generator, "ROOT", root)
    monkeypatch.setattr(generator, "RAW_DIR", raw)
    monkeypatch.setattr(generator, "resolve_runtime_projects", lambda *a, **k: {"MAIN": root})
    monkeypatch.setattr(generator, "configured_project_ownership_exclusions",
                        lambda *a, **k: {"MAIN": [child] if boundary == "excluded" else []})
    monkeypatch.setattr(generator, "load_previous_atlas", lambda: {})
    monkeypatch.setattr(generator, "ensure_output_dir", lambda: None)
    monkeypatch.setattr(generator, "run_observed_subprocess", lambda *a, **k: pytest.fail("auxiliary AST execution"))
    monkeypatch.setattr(generator, "save_json_atomic",
                        lambda path, payload, **kw: store.save_raw(Path(path).stem, payload, **kw))
    monkeypatch.setattr(generator, "DYNAMIC_CONFIG", {"use_sqlite": True})
    monkeypatch.setattr(artifact_store, "STORE", store)
    monkeypatch.setattr(config, "PROJECT_FILTER", [])
    monkeypatch.setenv("SAGE_SYNC_SHADOW_WRITES", "1")
    atlas, _, _ = generator.generate_atlas(stale_projects=["MAIN"])
    auxiliary = atlas["MAIN"]["typescript_auxiliary_inputs"]
    assert auxiliary["status"] == "captured"
    assert set(auxiliary["files"]) == {"tsconfig.json", "env.d.ts"}
    assert all(row["status"] == "ok" for row in auxiliary["files"].values())
    assert atlas["MAIN"]["files"] == {}
    assert atlas["MAIN"]["resolved_module_importer_index"]["status"] == "complete"
    assert atlas["MAIN"]["resolved_module_importer_index"]["indexed_file_count"] == 0
    assert atlas["MAIN"]["resolved_module_importer_index"]["by_source"] == {}
    assert store.load_raw("atlas", {})["MAIN"]["typescript_auxiliary_inputs"] == auxiliary
    resolution = atlas["MAIN"]["typescript_resolution_inputs"]
    expected_names = ["env.d.ts", "tsconfig.json"]
    if boundary != "normal":
        expected_names.insert(0, "child")
        assert "child" not in resolution["directories"]
    assert resolution["directories"]["."] == expected_names
    assert resolution["status"] == ("partial" if boundary == "unreadable" else "captured")
    assert resolution["walk_errors"] == (1 if boundary == "unreadable" else 0)
    assert store.load_raw("atlas", {})["MAIN"]["typescript_resolution_inputs"] == resolution


@pytest.mark.parametrize("mutation", ["none", "config", "declaration", "deleted", "surgical"])
def test_actual_semantic_reads_match_only_original_ingestion(tmp_path, mutation):
    sources = {"a.ts": "export const x: Ambient = 1;",
               "env.d.ts": "\ufefftype Ambient = number;\r\n",
               "base.json": '{"compilerOptions":{"noLib":true}}'}
    config = {"extends": "./base.json", "files": ["a.ts", "env.d.ts"]}
    payload, atlas = collect(tmp_path, sources, "--semantic", "true", project_config=config)
    root = tmp_path / "repo"
    inventory = inputs.capture_auxiliary_inputs(root, walk_entries(root, [*sources, "tsconfig.json"]),
                                               surgical=mutation == "surgical")
    atlas["MAIN"]["typescript_auxiliary_inputs"] = inventory
    if mutation == "config":
        sources["base.json"] = '{"compilerOptions":{"noLib":true,"strict":true}}'
    elif mutation == "declaration":
        sources["env.d.ts"] = "type Ambient = string;"
    if mutation in {"config", "declaration"}:
        payload, _ = collect(tmp_path, sources, "--semantic", "true", project_config=config)
    elif mutation == "deleted":
        # Deleting live files after capture must not change persisted identity.
        (root / "env.d.ts").unlink()
    context = payload["projects"]["MAIN"]["semantic_context"]
    errors = semantic_auxiliary_input_errors(context, atlas["MAIN"])
    if mutation in {"none", "deleted"}:
        assert errors == []
    else:
        assert errors
    receipt = write_receipt(tmp_path, payload, atlas)
    assert receipt["status"] == "BLOCKED"
    assert "typescript:MAIN:semantic_context_not_snapshot_bound" in receipt["errors"]


def test_invalid_auxiliary_policy_fails_closed(tmp_path, monkeypatch):
    policy = {"artifacts": {"ts_diagnostics": {"atlas_auxiliary_inputs": {
        "file_suffixes": [".json"], "max_files": True, "max_file_bytes": 1, "max_total_bytes": 1}}}}
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(policy))
    monkeypatch.setattr(inputs, "POLICY_PATH", path)
    with pytest.raises(ValueError, match="budget"):
        inputs.capture_auxiliary_inputs(tmp_path, [])


@pytest.mark.parametrize("case", ["root", "scope", "negative_authority", "missing_file", "unsafe_path", "external"])
def test_auxiliary_consumer_cannot_promote_missing_or_wrong_scope(tmp_path, case):
    source = tmp_path / "base.json"
    source.write_text("{}")
    inventory = inputs.capture_auxiliary_inputs(tmp_path, walk_entries(tmp_path, ["base.json"]))
    project = {"project": {"root": str(tmp_path)}, "typescript_auxiliary_inputs": inventory}
    context = {"project_root": str(tmp_path), "observations": [{
        "operation": "readFile", "path": source.as_posix(), "allowed": True,
        "value": {"text_sha256": hashlib.sha256(b"{}").hexdigest()}}]}
    if case == "root":
        context.pop("project_root")
    elif case == "scope":
        inventory["scope"] = "entire_repository"
    elif case == "negative_authority":
        inventory["negative_resolution_authority"] = True
    elif case == "missing_file":
        inventory["files"] = {}
    elif case == "unsafe_path":
        context["observations"][0]["path"] = "../base.json"
    else:
        context["observations"][0]["path"] = (tmp_path.parent / "base.json").as_posix()
    assert semantic_auxiliary_input_errors(context, project)


def resolution_fixture(root, *, names=(), surgical=False):
    capture = inputs.ResolutionInputCapture(root, surgical=surgical)
    capture.observe_directory(str(root), [], list(names))
    project = {"project": {"root": str(root)}, "typescript_resolution_inputs": capture.payload}
    context = {"project_root": str(root), "observations": [{
        "operation": "fileExists", "path": (root / "not_present_anywhere.ts").as_posix(),
        "allowed": True, "access_status": "missing", "input_scope": "workspace",
        "error_code": None, "filtered_entries": 0, "value": False,
    }]}
    return capture, project, context


def test_snapshot_absence_uses_captured_names_without_live_filesystem(tmp_path, monkeypatch):
    (tmp_path / "child").mkdir()
    capture, project, context = resolution_fixture(tmp_path, names=["a.ts", "node_modules", "child"])
    capture.observe_directory(str(tmp_path / "child"), [], [])
    context["observations"] += [
        {**context["observations"][0], "path": (tmp_path / "child" / "missing.ts").as_posix()},
        {**context["observations"][0], "path": (tmp_path / "absent_directory" / "deep" / "x.ts").as_posix()},
    ]
    before = build_atlas_commit({"MAIN": project})
    (tmp_path / "not_present_anywhere.ts").write_text("export const later = 1;")
    original_resolve = Path.resolve

    def no_target_resolve(path, *args, **kwargs):
        if path.is_relative_to(tmp_path):
            pytest.fail("live consumer realpath")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", no_target_resolve)
    monkeypatch.setattr("os.walk", lambda *a, **k: pytest.fail("second repository walk"))
    assert semantic_negative_resolution_errors(context, project) == []
    assert build_atlas_commit({"MAIN": project})["snapshot_id"] == before["snapshot_id"]
    assert before["counts"]["files"] == 0


@pytest.mark.parametrize("case", [
    "listed", "case_variant", "excluded", "omitted", "surgical", "root_mismatch",
    "outside", "parent_escape", "directory_missing", "invalid_name", "invalid_row",
    "invalid_scope", "legacy", "denied_missing", "library_scope", "read_error",
    "true_probe", "directory_result", "root_probe",
])
def test_snapshot_absence_rejects_unknown_or_contradictory_input(tmp_path, case):
    capture, project, context = resolution_fixture(tmp_path, names=["a.ts", "node_modules"])
    inventory = capture.payload
    row = context["observations"][0]
    if case == "listed":
        inventory["directories"]["."].append("not_present_anywhere.ts")
    elif case == "case_variant":
        inventory["directories"]["."].append("NOT_PRESENT_ANYWHERE.TS")
    elif case == "excluded":
        row["path"] = (tmp_path / "node_modules" / "missing.ts").as_posix()
    elif case == "omitted":
        inventory.update(status="partial", directories={})
    elif case == "surgical":
        inventory["status"] = "unavailable_surgical_scan"
    elif case == "root_mismatch":
        inventory["project_root"] = str(tmp_path.parent)
    elif case == "outside":
        row["path"] = (tmp_path.parent / "missing.ts").as_posix()
    elif case == "parent_escape":
        row["path"] = (tmp_path / "x" / ".." / "missing.ts").as_posix()
    elif case == "directory_missing":
        inventory["directories"]["."].append("not_present_anywhere.ts")
        row["path"] += "/child.ts"
    elif case == "invalid_name":
        inventory["directories"]["."].append("../unknown")
    elif case == "invalid_row":
        inventory["directories"]["."] = "not a directory record"
    elif case == "invalid_scope":
        inventory["scope"] = "whole_filesystem"
    elif case == "legacy":
        project.pop("typescript_resolution_inputs")
    elif case == "denied_missing":
        row["allowed"] = False
    elif case == "library_scope":
        row["input_scope"] = "compiler_library"
    elif case == "read_error":
        row.update(operation="readFile", value=None, error_code="EACCES")
    elif case == "true_probe":
        row["value"] = True
    elif case == "directory_result":
        row.update(operation="readDirectory", value={"entries": 0})
    else:
        row["path"] = tmp_path.as_posix()
    assert semantic_negative_resolution_errors(context, project) == ["negative_resolution_not_snapshot_bound"]


@pytest.mark.parametrize("operation,value", [("fileExists", False), ("directoryExists", False), ("readFile", None)])
def test_absence_accepts_only_supported_negative_query_results(tmp_path, operation, value):
    _, project, context = resolution_fixture(tmp_path)
    context["observations"][0].update(operation=operation, value=value)
    assert semantic_negative_resolution_errors(context, project) == []


@pytest.mark.parametrize("budget", ["max_directories", "max_entries", "max_name_bytes"])
def test_directory_budget_omits_whole_listings_not_individual_names(tmp_path, monkeypatch, budget):
    policy = deepcopy(inputs.resolution_input_policy())
    policy[budget] = 1
    monkeypatch.setattr(inputs, "resolution_input_policy", lambda: policy)
    capture = inputs.ResolutionInputCapture(tmp_path)
    capture.observe_directory(str(tmp_path), [], ["a", "b"])
    capture.observe_directory(str(tmp_path / "child"), [], [])
    assert capture.payload["status"] == "partial"
    assert capture.payload["omitted_directories"] == 1
    if budget == "max_directories":
        assert capture.payload["directories"] == {".": ["a", "b"]}
    else:
        assert capture.payload["directories"] == {"child": []}


@pytest.mark.parametrize("positive_source", ["source", "auxiliary", "directory"])
def test_inconsistent_enumeration_cannot_claim_absence(tmp_path, positive_source):
    capture, project, context = resolution_fixture(tmp_path)
    if positive_source == "source":
        project["files"] = {"not_present_anywhere.ts": {"hash": "a" * 64}}
    elif positive_source == "auxiliary":
        project["typescript_auxiliary_inputs"] = {"files": {"not_present_anywhere.ts": {"status": "unreadable"}}}
    else:
        capture.observe_directory(str(tmp_path / "not_present_anywhere.ts"), [], [])
    assert semantic_negative_resolution_errors(context, project)


def test_resolution_policy_change_invalidates_atlas_producer_identity(tmp_path, monkeypatch):
    from tools.core import atlas_integrity

    policy = json.loads(inputs.POLICY_PATH.read_text(encoding="utf-8"))
    path = tmp_path / "config/analysis_snapshot_lineage_contract.json"
    path.parent.mkdir()
    path.write_text(json.dumps(policy), encoding="utf-8")
    monkeypatch.setattr(inputs, "POLICY_PATH", path)
    monkeypatch.setattr(atlas_integrity, "__file__", str(tmp_path / "tools/core/atlas_integrity.py"))
    before = atlas_integrity.generator_fingerprint()
    old_budget = inputs.resolution_input_policy()["max_entries"]
    policy["artifacts"]["ts_diagnostics"]["atlas_resolution_inputs"]["max_entries"] += 1
    path.write_text(json.dumps(policy), encoding="utf-8")
    assert inputs.resolution_input_policy()["max_entries"] == old_budget + 1
    assert atlas_integrity.generator_fingerprint() != before


@pytest.mark.parametrize("value", [0, -1, True, "1", None])
def test_invalid_resolution_policy_fails_closed(tmp_path, monkeypatch, value):
    settings = dict(inputs.resolution_input_policy(), max_entries=value)
    path = tmp_path / "policy.json"
    path.write_text(json.dumps({"artifacts": {"ts_diagnostics": {"atlas_resolution_inputs": settings}}}))
    monkeypatch.setattr(inputs, "POLICY_PATH", path)
    with pytest.raises(ValueError, match="resolution input budget"):
        inputs.ResolutionInputCapture(tmp_path)


def test_walk_failure_and_surgical_capture_preserve_unknown(tmp_path):
    capture, project, context = resolution_fixture(tmp_path, names=["unreadable"])
    capture.record_walk_error(PermissionError("denied"))
    assert capture.payload["status"] == "partial" and capture.payload["walk_errors"] == 1
    assert semantic_negative_resolution_errors(context, project) == []  # Unrelated complete parent listing.
    context["observations"][0]["path"] = (tmp_path / "unreadable" / "x.ts").as_posix()
    assert semantic_negative_resolution_errors(context, project)
    surgical, _, _ = resolution_fixture(tmp_path, surgical=True)
    assert surgical.payload["directories"] == {}


@pytest.mark.parametrize("kind", ["internal", "external"])
def test_aliased_directory_does_not_gain_absence_authority(tmp_path, kind):
    root = tmp_path / "repo"
    root.mkdir()
    target = (root if kind == "internal" else tmp_path) / "target"
    target.mkdir()
    link = root / "alias"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"Symlink creation unavailable: {error}")
    capture, project, context = resolution_fixture(root, names=["alias"])
    capture.observe_directory(str(link), [], [])
    assert capture.payload["status"] == "partial"
    assert "alias" not in capture.payload["directories"]
    context["observations"][0]["path"] = (link / "missing.ts").as_posix()
    assert semantic_negative_resolution_errors(context, project)


def test_directory_identity_survives_partitioned_sqlite_and_changes_snapshot(tmp_path):
    capture, project, context = resolution_fixture(tmp_path, names=["a.ts"])
    atlas = {"MAIN": project}
    before = build_atlas_commit(atlas)
    store = _sqlite_store(tmp_path / "raw", inline_limit=64, part_size=32)
    profile = store._save_payload_to_state_table("atlas", atlas)
    assert profile["state_payload_storage_mode"] == "partitioned_json_v1"
    assert semantic_negative_resolution_errors(context, store.load_raw("atlas", {})["MAIN"]) == []
    capture.observe_directory(str(tmp_path), [], ["a.ts", "not_present_anywhere.ts"])
    after = build_atlas_commit(atlas)
    assert before["snapshot_id"] != after["snapshot_id"]
    assert before["counts"] == after["counts"]
    assert semantic_negative_resolution_errors(context, project)


def test_real_compiler_missing_probes_bind_only_to_captured_atlas_names(tmp_path):
    payload, atlas = collect(tmp_path, {"a.ts": 'import "./not_present_anywhere";'},
                             "--semantic", "true", project_config={
                                 "compilerOptions": {"noLib": True}, "files": ["a.ts"]})
    root = tmp_path / "repo"
    capture = inputs.ResolutionInputCapture(root)
    capture.observe_directory(str(root), [], ["a.ts", "tsconfig.json"])
    atlas["MAIN"]["typescript_resolution_inputs"] = capture.payload
    context = payload["projects"]["MAIN"]["semantic_context"]
    assert any(row["access_status"] == "missing" for row in context["observations"])
    assert semantic_negative_resolution_errors(context, atlas["MAIN"]) == []
    receipt = write_receipt(tmp_path, payload, atlas)
    assert receipt["status"] == "BLOCKED"
    assert "typescript:MAIN:negative_resolution_not_snapshot_bound" not in receipt["errors"]
    # A name that existed in the captured image contradicts the later missing probe.
    capture.observe_directory(str(root), [], ["a.ts", "tsconfig.json", "not_present_anywhere.ts"])
    assert semantic_negative_resolution_errors(context, atlas["MAIN"])


@pytest.mark.parametrize("name", ["UNKNOW~1.TS", "short.ts", "NUL.ts", "COM1", "filename.ts.",
                                  "filename.ts ", "name:stream", "bad\0name", "küçük.ts"])
def test_uncaptured_windows_alias_and_namespace_semantics_stay_unknown(tmp_path, name):
    capture, project, context = resolution_fixture(tmp_path, names=["long-existing-filename.ts"])
    capture.payload["path_semantics"] = "windows_directory_names"
    context["observations"][0]["path"] = (tmp_path / name).as_posix()
    assert semantic_negative_resolution_errors(context, project)


def test_unobserved_unicode_equivalence_is_not_snapshot_absence(tmp_path):
    _, project, context = resolution_fixture(tmp_path, names=["\u0131nformation.json"])
    context["observations"][0]["path"] = (tmp_path / "Information.json").as_posix()
    assert semantic_negative_resolution_errors(context, project)


def positive_resolution_fixture(root):
    (root / "src").mkdir()
    (root / "src/a.ts").write_text("export const a = 1;")
    (root / "tsconfig.json").write_text("{}")
    capture = inputs.ResolutionInputCapture(root)
    capture.observe_directory(str(root), ["src"], ["tsconfig.json"])
    capture.observe_directory(str(root / "src"), [], ["a.ts"])
    auxiliary = inputs.capture_auxiliary_inputs(root, walk_entries(root, ["tsconfig.json"]))
    project = {"project": {"root": str(root)}, "files": {"src/a.ts": {
        "hash": "a" * 64, "language": "typescript"}},
               "typescript_resolution_inputs": capture.payload, "typescript_auxiliary_inputs": auxiliary}
    context = {"project_root": str(root), "observations": []}
    return project, context


def positive_probe(root, operation, relative, value=True):
    return {"operation": operation, "stage": "compiler", "path": (root / relative).as_posix(),
            "arguments_sha256": hashlib.sha256(b"[]").hexdigest(), "allowed": True,
            "access_status": "present", "input_scope": "workspace", "error_code": None,
            "filtered_entries": 0, "value": value}


@pytest.mark.parametrize("operation,relative", [
    ("fileExists", "src/a.ts"), ("fileExists", "tsconfig.json"), ("directoryExists", "."),
    ("directoryExists", "src"), ("realpath", "src"), ("realpath", "tsconfig.json"),
])
def test_positive_lookup_matches_captured_kind_without_consulting_live_target(tmp_path, monkeypatch, operation, relative):
    project, context = positive_resolution_fixture(tmp_path)
    value = (tmp_path / relative).as_posix() if operation == "realpath" else True
    context["observations"] = [positive_probe(tmp_path, operation, relative, value)]
    # Remove a previously observed input: comparison must concern captured state,
    # not silently acquire a later namespace while consuming evidence.
    (tmp_path / "src/a.ts").unlink()
    original = Path.resolve
    def guarded(path, *args, **kwargs):
        if path.is_relative_to(tmp_path):
            pytest.fail("consumer consulted live target")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "resolve", guarded)
    monkeypatch.setattr("os.walk", lambda *args, **kwargs: pytest.fail("consumer walked target"))
    assert semantic_positive_resolution_errors(context, project) == []


@pytest.mark.parametrize("case", ["source_only", "legacy_auxiliary", "noncanonical_auxiliary"])
def test_file_realpath_needs_captured_physical_canonicality(tmp_path, case):
    project, context = positive_resolution_fixture(tmp_path)
    relative = "src/a.ts" if case == "source_only" else "tsconfig.json"
    context["observations"] = [positive_probe(
        tmp_path, "realpath", relative, (tmp_path / relative).as_posix())]
    if case == "legacy_auxiliary":
        project["typescript_auxiliary_inputs"]["files"][relative].pop("canonical_file_path", None)
    elif case == "noncanonical_auxiliary":
        project["typescript_auxiliary_inputs"]["files"][relative]["canonical_file_path"] = False
    assert semantic_positive_resolution_errors(context, project)


@pytest.mark.parametrize("failure", [
    "name_only", "unvisited_directory", "wrong_kind", "omitted_parent", "root_mismatch", "surgical",
    "outside", "escape", "case_alias", "unicode", "special", "library_scope", "denied", "read_failure",
    "filtered", "arguments", "false", "getDirectories", "readDirectory", "file_realpath_alias",
    "different_realpath", "invalid_source_hash", "numeric_source_hash", "aux_unreadable",
    "aux_foreign_root", "aux_wrong_scope", "numeric_aux_hash", "malformed_row",
])
def test_positive_lookup_never_promotes_unproved_namespace(tmp_path, failure):
    project, context = positive_resolution_fixture(tmp_path)
    row = positive_probe(tmp_path, "fileExists", "src/a.ts")
    context["observations"] = [row]
    directories = project["typescript_resolution_inputs"]["directories"]
    if failure == "name_only":
        project["files"] = {}
    elif failure in {"unvisited_directory", "wrong_kind"}:
        row.update(operation="directoryExists")
        if failure == "unvisited_directory":
            row["path"] = (tmp_path / "excluded").as_posix()
            directories["."].append("excluded")
    elif failure == "omitted_parent":
        directories.pop("src")
    elif failure == "root_mismatch":
        context["project_root"] = tmp_path.parent.as_posix()
    elif failure == "surgical":
        project["typescript_resolution_inputs"]["status"] = "unavailable_surgical_scan"
    elif failure in {"outside", "escape", "case_alias", "unicode", "special"}:
        row["path"] = {"outside": tmp_path.parent / "a.ts", "escape": tmp_path / "src/../src/a.ts",
                       "case_alias": tmp_path / "src/A.ts", "unicode": tmp_path / "küçük.ts",
                       "special": tmp_path / "src/a.ts."}[failure].as_posix()
    elif failure == "library_scope":
        row["input_scope"] = "compiler_library"
    elif failure == "denied":
        row["allowed"] = False
    elif failure == "read_failure":
        row.update(access_status="unavailable", error_code="EACCES")
    elif failure == "filtered":
        row["filtered_entries"] = 1
    elif failure == "arguments":
        row["arguments_sha256"] = "0" * 64
    elif failure == "false":
        row["value"] = False
    elif failure in {"getDirectories", "readDirectory"}:
        row.update(operation=failure, path=tmp_path.as_posix(), value={
            "entries": 0, "sha256": hashlib.sha256(b"[]").hexdigest()})
    elif failure in {"file_realpath_alias", "different_realpath"}:
        row.update(operation="realpath", value=row["path"])
        if failure == "file_realpath_alias":
            row["value"] = (tmp_path / "src/other.ts").as_posix()
        else:
            row["path"] = (tmp_path / "src").as_posix()
    elif failure in {"invalid_source_hash", "numeric_source_hash"}:
        project["files"]["src/a.ts"]["hash"] = "bad" if failure == "invalid_source_hash" else 10 ** 63
    elif failure.startswith("aux_") or failure == "numeric_aux_hash":
        row["path"] = (tmp_path / "tsconfig.json").as_posix()
        auxiliary = project["typescript_auxiliary_inputs"]
        if failure == "aux_unreadable":
            auxiliary["files"]["tsconfig.json"]["status"] = "unreadable"
        elif failure == "aux_foreign_root":
            auxiliary["project_root"] = tmp_path.parent.as_posix()
        elif failure == "aux_wrong_scope":
            auxiliary["scope"] = "untrusted"
        else:
            auxiliary["files"]["tsconfig.json"]["host_text_sha256"] = 10 ** 63
    else:
        context["observations"] = [None]
    assert semantic_positive_resolution_errors(context, project)


@pytest.mark.parametrize("conflict", ["same_path", "file_parent", "auxiliary_directory"])
def test_positive_and_negative_probes_reject_file_directory_contradictions(tmp_path, conflict):
    project, context = positive_resolution_fixture(tmp_path)
    if conflict == "same_path":
        project["files"]["src"] = {"hash": "a" * 64}
    elif conflict == "file_parent":
        project["files"]["src/a.ts/child.ts"] = {"hash": "a" * 64}
    else:
        project["typescript_auxiliary_inputs"]["files"]["src"] = {"status": "unreadable"}
    context["observations"] = [positive_probe(tmp_path, "directoryExists", ".")]
    assert semantic_positive_resolution_errors(context, project)
    context["observations"] = [dict(positive_probe(tmp_path, "fileExists", "not_present_anywhere.ts"),
                                     access_status="missing", value=False)]
    assert semantic_negative_resolution_errors(context, project)


@pytest.mark.parametrize("value", [None, "not an observation", []])
def test_malformed_lookup_rows_cannot_disappear_as_no_queries(tmp_path, value):
    project, context = positive_resolution_fixture(tmp_path)
    context["observations"] = [value]
    assert semantic_positive_resolution_errors(context, project)
    assert semantic_negative_resolution_errors(context, project)


def test_real_compiler_positive_probes_bind_to_partitioned_atlas_capture(tmp_path):
    payload, atlas = collect(tmp_path, {
        "src/a.ts": 'import { value } from "./value"; export const a: string = value;',
        "src/value.ts": "export const value = 1;",
    }, "--semantic", "true", project_config={"compilerOptions": {"noLib": True, "types": []}, "files": ["src/a.ts"]})
    root = tmp_path / "repo"
    capture = inputs.ResolutionInputCapture(root)
    for directory, dirs, files in os.walk(root):
        capture.observe_directory(directory, dirs, files)
    atlas["MAIN"]["typescript_resolution_inputs"] = capture.payload
    atlas["MAIN"]["typescript_auxiliary_inputs"] = inputs.capture_auxiliary_inputs(root, walk_entries(root, ["tsconfig.json"]))
    context = payload["projects"]["MAIN"]["semantic_context"]
    # Keep exactly this helper's supported observed query slice. The complete
    # receipt must still expose every other unresolved context observation.
    rows = [row for row in context["observations"] if row["operation"] in {"fileExists", "directoryExists"}
            and row["value"] is True and row["input_scope"] == "workspace"]
    assert any(row["operation"] == "fileExists" for row in rows)
    assert any(row["operation"] == "directoryExists" for row in rows)
    scope = {**context, "observations": rows}
    store = _sqlite_store(tmp_path / "raw", inline_limit=64, part_size=32)
    assert store._save_payload_to_state_table("atlas", atlas)["state_payload_storage_mode"] == "partitioned_json_v1"
    restored = store.load_raw("atlas", {})
    assert semantic_positive_resolution_errors(scope, restored["MAIN"]) == []
    receipt = write_receipt(tmp_path, payload, restored)
    assert receipt["status"] == "BLOCKED"
    restored["MAIN"]["files"].pop("src/value.ts")
    assert semantic_positive_resolution_errors(scope, restored["MAIN"])


def test_real_compiler_file_realpath_requires_explicit_captured_file_scope(tmp_path):
    payload, atlas = collect(tmp_path, {
        "a.ts": 'import { value } from "typed-package"; export const a = value;',
        "node_modules/typed-package/package.json": '{"types":"index.d.ts"}',
        "node_modules/typed-package/index.d.ts": "export const value: number;",
    }, "--semantic", "true", project_config={"files": ["a.ts"]})
    root = tmp_path / "repo"
    context = payload["projects"]["MAIN"]["semantic_context"]
    rows = [row for row in context["observations"] if row["operation"] == "realpath"
            and row["input_scope"] == "workspace" and row["path"].endswith("/index.d.ts")]
    assert rows and all(Path(row["value"]) == Path(row["path"]) for row in rows)
    scope = {**context, "observations": rows}

    # Explicit fixture capture proves the helper's supported input only. An
    # ordinary Atlas walk prunes node_modules and must not inherit this proof.
    capture = inputs.ResolutionInputCapture(root)
    for directory, dirs, files in os.walk(root):
        capture.observe_directory(directory, dirs, files)
    atlas["MAIN"]["typescript_resolution_inputs"] = capture.payload
    atlas["MAIN"]["typescript_auxiliary_inputs"] = inputs.capture_auxiliary_inputs(
        root, walk_entries(root, ["tsconfig.json", "node_modules/typed-package/package.json",
                                  "node_modules/typed-package/index.d.ts"]))
    atlas["MAIN"]["files"].pop("node_modules/typed-package/index.d.ts")
    store = _sqlite_store(tmp_path / "raw", inline_limit=64, part_size=32)
    store._save_payload_to_state_table("atlas", atlas)
    restored = store.load_raw("atlas", {})
    assert semantic_positive_resolution_errors(scope, restored["MAIN"]) == []
    assert write_receipt(tmp_path, payload, restored)["status"] == "BLOCKED"
    restored["MAIN"]["typescript_resolution_inputs"]["directories"].pop("node_modules/typed-package")
    assert semantic_positive_resolution_errors(scope, restored["MAIN"])


def directory_list_value(names):
    return {"entries": len(names), "sha256": hashlib.sha256(
        json.dumps(names, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()}


@pytest.mark.parametrize("relative,names", [(".", ["Zeta", "src"]), ("src", []), ("Zeta", [])])
def test_directory_list_binds_complete_kinds_without_live_io(tmp_path, monkeypatch, relative, names):
    project, context = positive_resolution_fixture(tmp_path)
    directories = project["typescript_resolution_inputs"]["directories"]
    directories["."].insert(0, "Zeta")
    directories["Zeta"] = []
    row = positive_probe(tmp_path, "getDirectories", relative, directory_list_value(names))
    context["observations"] = [row, {**row, "stage": "config"}]
    original = Path.open
    original_resolve = Path.resolve
    def guarded(path, *args, **kwargs):
        if path.is_relative_to(tmp_path):
            pytest.fail("consumer reread target")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", guarded)
    def guarded_resolve(path, *args, **kwargs):
        if path.is_relative_to(tmp_path):
            pytest.fail("consumer resolved target")
        return original_resolve(path, *args, **kwargs)
    monkeypatch.setattr(Path, "resolve", guarded_resolve)
    monkeypatch.setattr("os.walk", lambda *args, **kwargs: pytest.fail("consumer walked target"))
    from tools.core import typescript_source_binding as binding
    calls = []
    compare = binding._captured_child_directories
    def counted(*args):
        calls.append(args[0])
        return compare(*args)
    monkeypatch.setattr(binding, "_captured_child_directories", counted)
    assert semantic_positive_resolution_errors(context, project) == []
    assert calls == [relative]  # Repeated queries reuse one bounded snapshot projection.


@pytest.mark.parametrize("failure", [
    "name_only", "omitted_directory", "missing_source", "invalid_source", "unreadable_aux",
    "foreign_aux", "unicode", "reserved", "duplicate", "case_collision",
    "wrong_order", "absolute_names", "extra_child", "wrong_count", "boolean_count", "invalid_hash",
    "filtered", "io_failure", "contradictory_io_failure", "malformed_io_failure",
    "unavailable", "arguments", "glob_query",
])
def test_directory_list_cannot_hide_unknown_entry_kind_or_failed_query(tmp_path, failure):
    project, context = positive_resolution_fixture(tmp_path)
    directories = project["typescript_resolution_inputs"]["directories"]
    directories["."].append("Zeta")
    directories["Zeta"] = []
    row = positive_probe(tmp_path, "getDirectories", ".", directory_list_value(["Zeta", "src"]))
    context["observations"] = [row]
    if failure == "name_only":
        directories["."].append("excluded")
    elif failure == "omitted_directory":
        directories.pop("Zeta")
    elif failure in {"missing_source", "invalid_source"}:
        row.update(path=(tmp_path / "src").as_posix(), value=directory_list_value([]))
        if failure == "missing_source":
            project["files"].clear()
        else:
            project["files"]["src/a.ts"]["hash"] = 10 ** 63
    elif failure == "unreadable_aux":
        project["typescript_auxiliary_inputs"]["files"]["tsconfig.json"]["status"] = "unreadable"
    elif failure == "foreign_aux":
        project["typescript_auxiliary_inputs"]["project_root"] = tmp_path.parent.as_posix()
    elif failure in {"unicode", "reserved"}:
        name = "café" if failure == "unicode" else "NUL"
        directories["."].append(name)
        directories[name] = []
        row["value"] = directory_list_value(sorted(["Zeta", "src", name]))
    elif failure == "duplicate":
        directories["."].append("src")
    elif failure == "case_collision":
        directories["."].append("SRC")
        directories["SRC"] = []
        row["value"] = directory_list_value(["SRC", "Zeta", "src"])
    elif failure == "wrong_order":
        row["value"] = directory_list_value(["src", "Zeta"])
    elif failure == "absolute_names":
        row["value"] = directory_list_value([(tmp_path / name).as_posix() for name in ["Zeta", "src"]])
    elif failure == "extra_child":
        row["value"] = directory_list_value(["Zeta", "invented", "src"])
    elif failure == "wrong_count":
        row["value"]["entries"] = 0
    elif failure == "boolean_count":
        row.update(path=(tmp_path / "Zeta").as_posix(), value=directory_list_value([]))
        row["value"]["entries"] = False
    elif failure == "invalid_hash":
        row["value"]["sha256"] = None
    elif failure == "filtered":
        row["filtered_entries"] = 1
    elif failure == "io_failure":
        row["error_code"] = "EACCES"
    elif failure == "contradictory_io_failure":
        row["io_failure"] = {"operation": "readdirSync", "code": "EACCES", "count": 1}
    elif failure == "malformed_io_failure":
        row["io_failure"] = {}
    elif failure == "unavailable":
        row["access_status"] = "unavailable"
    elif failure == "arguments":
        row["arguments_sha256"] = "0" * 64
    else:
        row["operation"] = "readDirectory"
    assert semantic_positive_resolution_errors(context, project)


def test_actual_compiler_directory_lists_match_only_captured_complete_namespace(tmp_path):
    payload, atlas = collect(tmp_path, {
        "a.ts": "export const value = 1;",
        "node_modules/@types/zeta/index.d.ts": "declare const zeta: number;",
        "node_modules/@types/Alpha/index.d.ts": "declare const alpha: number;",
    }, "--semantic", "true", project_config={"compilerOptions": {"noLib": True}, "files": ["a.ts"]})
    root = tmp_path / "repo"
    capture = inputs.ResolutionInputCapture(root)
    # Explicit complete fixture inventory, not proof that ordinary Atlas scans
    # excluded dependencies. The omitted-directory control below must reject it.
    for directory, dirs, files in os.walk(root):
        capture.observe_directory(directory, dirs, files)
    project = atlas["MAIN"]
    project["typescript_resolution_inputs"] = capture.payload
    project["typescript_auxiliary_inputs"] = inputs.capture_auxiliary_inputs(root, walk_entries(root, ["tsconfig.json"]))
    context = payload["projects"]["MAIN"]["semantic_context"]
    rows = [row for row in context["observations"] if row["operation"] == "getDirectories"
            and row["path"] == (root / "node_modules/@types").as_posix()]
    assert rows, "real compiler must exercise this query, not a fabricated host row"
    assert rows[0]["value"] == directory_list_value(["Alpha", "zeta"])
    scope = {**context, "observations": rows}
    store = _sqlite_store(tmp_path / "raw", inline_limit=64, part_size=32)
    store._save_payload_to_state_table("atlas", atlas)
    restored = store.load_raw("atlas", {})
    assert semantic_positive_resolution_errors(scope, restored["MAIN"]) == []
    assert write_receipt(tmp_path, payload, restored)["status"] == "BLOCKED"
    restored["MAIN"]["typescript_resolution_inputs"]["directories"].pop("node_modules/@types/Alpha")
    assert semantic_positive_resolution_errors(scope, restored["MAIN"])
def test_typescript_esm_emitted_extensions_require_one_source_candidate(tmp_path):
    from tools.core.path_engine import resolve_project_import

    source = tmp_path / "src" / "utilities"
    caller = tmp_path / "src" / "dev"
    source.mkdir(parents=True)
    caller.mkdir(parents=True)
    for name in ("eventBus.ts", "view.tsx", "module.mts", "legacy.cts",
                 "ambiguous.ts", "ambiguous.tsx"):
        (source / name).write_text("export const value = true;\n", encoding="utf-8")

    def resolved(import_path, language="typescript"):
        return resolve_project_import(import_path, str(caller), str(tmp_path),
                                      str(tmp_path), language=language)

    assert resolved("../utilities/eventBus.js") == "src/utilities/eventBus.ts"
    assert resolved("../utilities/view.jsx") == "src/utilities/view.tsx"
    assert resolved("../utilities/module.mjs") == "src/utilities/module.mts"
    assert resolved("../utilities/legacy.cjs") == "src/utilities/legacy.cts"
    assert resolved("../utilities/ambiguous.js") == "../utilities/ambiguous.js"
    assert resolved("../utilities/eventBus.js", language="go") == "../utilities/eventBus.js"
    (source / "eventBus.js").write_text("export const emitted = true;\n", encoding="utf-8")
    assert resolved("../utilities/eventBus.js") == "src/utilities/eventBus.js"
