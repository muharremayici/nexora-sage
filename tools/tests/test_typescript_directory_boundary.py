"""Actual compiler traversal and partial-scope regressions in both modes."""

import json
from copy import deepcopy
from pathlib import Path
import subprocess

import pytest

from tools.tests.test_typescript_source_binding import collect, write_receipt
from tools.core.atlas_typescript_inputs import ResolutionInputCapture, capture_auxiliary_inputs
from tools.core.typescript_source_binding import semantic_positive_resolution_errors
from tools.core.config import CODE_MAPS_DIR
from tools.tests.test_atlas_materialization_profile import _sqlite_store


@pytest.mark.parametrize("semantic", [False, True])
@pytest.mark.parametrize("link_scope", ["outside", "inside"])
def test_recursive_glob_checks_boundary_before_enumerating_link(tmp_path, semantic, link_scope):
    root = tmp_path / "repo"
    root.mkdir()
    destination = tmp_path / "external" if link_scope == "outside" else root / "owned"
    (destination / "deep").mkdir(parents=True)
    (destination / "deep" / "linked.ts").write_text("export const linked = 1;")
    link = root / "alias"
    # A Windows junction needs no developer-mode symlink privilege.
    created = subprocess.run(
        ["node", "-e", 'require("fs").symlinkSync(process.argv[1], process.argv[2], "junction")',
         str(destination), str(link)], capture_output=True, text=True, timeout=10,
    )
    assert created.returncode == 0, created.stderr
    trace = tmp_path / "enumerations.json"
    preload = tmp_path / "enumeration-observer.cjs"
    preload.write_text("""
const fs = require("fs"), path = require("path");
const original = fs.readdirSync;
const calls = [];
const observedReadDirectory = function(candidate, ...args) {
  calls.push(path.resolve(candidate));
  return original.call(this, candidate, ...args);
};
fs.readdirSync = observedReadDirectory;
process.on("exit", () => fs.writeFileSync(TRACE, JSON.stringify({
  calls, restored: fs.readdirSync === observedReadDirectory,
})));
""".replace("TRACE", json.dumps(str(trace))))
    payload, atlas = collect(tmp_path, {"src/a.ts": "export const a = 1;"},
                             "--semantic", str(semantic).lower(), node_preload=preload,
                             project_config={"include": ["**/*.ts"], "exclude": []})
    data = payload["projects"]["MAIN"]
    observed = json.loads(trace.read_text())
    assert observed["restored"] is True
    paths = [Path(value) for value in observed["calls"]]
    if link_scope == "outside":
        assert not any(value.is_relative_to(link) or value.is_relative_to(destination) for value in paths)
        assert data["checked_source_manifest"]["complete"] is False
        assert not any("/alias/" in row["file"] or row["file"].startswith("alias/")
                       for row in data["checked_source_manifest"]["files"])
        assert write_receipt(tmp_path, payload, atlas)["status"] == "BLOCKED"
        if semantic:
            row = next(row for row in data["semantic_context"]["observations"]
                       if row["operation"] == "readDirectory")
            assert row["access_status"] == "unavailable"
            assert row["error_code"] == "SAGE_BOUNDARY_DENIED"
    else:
        assert any(value == link or value == destination for value in paths)
        assert any(row["file"].endswith("deep/linked.ts") for row in data["checked_source_manifest"]["files"])
        if not semantic:
            assert data["checked_source_manifest"]["complete"] is True
        else:
            query = next(row for row in data["semantic_context"]["observations"]
                         if row["operation"] == "readDirectory")
            assert any(item["operation"] == "readdirSync" and item["value"]["unsupported_entries"] > 0
                       for item in query["recursive_inputs"]["observations"])
    # Each directory is still enumerated by the one compiler traversal, not a
    # second SAGE scan. Compiler canonical-path deduplication stays in control.
    assert len(paths) == len(set(paths))


@pytest.mark.parametrize("semantic", [False, True])
@pytest.mark.parametrize("failed_directory", ["repo", "nested"])
def test_swallowed_directory_failure_reaches_lineage_consumer(tmp_path, semantic, failed_directory):
    preload = tmp_path / "directory-failure.cjs"
    preload.write_text("""
const fs = require("fs"), path = require("path");
const original = fs.readdirSync;
fs.readdirSync = (candidate, ...args) => {
  if (path.basename(String(candidate)) === FAILED_DIRECTORY) {
    const error = new Error("fixture listing denied"); error.code = "EACCES"; throw error;
  }
  return original(candidate, ...args);
};
""".replace("FAILED_DIRECTORY", json.dumps(failed_directory)))
    payload, atlas = collect(tmp_path, {"a.ts": "export const value = 1;",
                                       "nested/b.ts": "export const b = 2;"},
                             "--semantic", str(semantic).lower(), node_preload=preload,
                             project_config={"include": ["**/*.ts"]})
    data = payload["projects"]["MAIN"]
    assert data["checked_source_manifest"]["complete"] is False
    receipt = write_receipt(tmp_path, payload, atlas)
    assert receipt["status"] == "BLOCKED"
    if not semantic:
        assert data["summary"]["root_enumeration_complete"] is False
        assert data["summary"]["root_files_checked"] == (0 if failed_directory == "repo" else 1)
        return
    context = payload["projects"]["MAIN"]["semantic_context"]
    row = next(row for row in context["observations"] if row["operation"] == "readDirectory")
    assert row["value"]["entries"] == (0 if failed_directory == "repo" else 1)
    assert row["access_status"] == "unavailable"
    assert row["error_code"] == "EACCES"
    assert row["io_failure"]["operation"] == "readdirSync"
    assert "host_access_unavailable" in context["binding_blockers"]
    assert "typescript:MAIN:semantic_host_access_unavailable" in receipt["errors"]


def test_filtered_explicit_root_cannot_claim_complete_syntax_scope(tmp_path):
    (tmp_path / "external.ts").write_text("export const outside = 1;")
    payload, atlas = collect(tmp_path, {"a.ts": "export const value = 1;"},
                             project_config={"files": ["a.ts", "../external.ts"]})
    data = payload["projects"]["MAIN"]
    assert data["summary"]["root_files_checked"] == 1
    assert data["checked_source_manifest"]["complete"] is False
    assert write_receipt(tmp_path, payload, atlas)["status"] == "BLOCKED"


@pytest.fixture
def recursive_checked(tmp_path):
    sources = {
        "base.json": json.dumps({"include": ["src/**/*.ts"], "exclude": ["src/ignored/**"],
                                "compilerOptions": {"noLib": True, "types": []}}),
        "src/a.ts": "export const a = 1;",
        "src/nested/b.ts": "export const b = 2;",
        "src/ignored/bad.ts": "export const invalid = ;",
    }
    payload, atlas = collect(tmp_path, sources, "--semantic", "true",
                             project_config={"extends": "./base.json"})
    root = tmp_path / "repo"
    capture = ResolutionInputCapture(root)
    capture.observe_directory(str(root), ["src"], ["base.json", "tsconfig.json"])
    capture.observe_directory(str(root / "src"), ["nested", "ignored"], ["a.ts"])
    capture.observe_directory(str(root / "src/nested"), [], ["b.ts"])
    capture.observe_directory(str(root / "src/ignored"), [], ["bad.ts"])
    project = atlas["MAIN"]
    project["files"].pop("base.json")
    project["typescript_resolution_inputs"] = capture.payload
    project["typescript_auxiliary_inputs"] = capture_auxiliary_inputs(
        root, [(str(root / name), name, name) for name in ["base.json", "tsconfig.json"]])
    context = deepcopy(payload["projects"]["MAIN"]["semantic_context"])
    context["observations"] = [row for row in context["observations"] if row["operation"] == "readDirectory"]
    return payload, atlas, context


def test_actual_recursive_glob_inputs_match_atlas_without_rescan(recursive_checked, tmp_path, monkeypatch):
    payload, atlas, context = recursive_checked
    row, = context["observations"]
    assert row["recursive_inputs"]["complete"] is True
    inputs = row["recursive_inputs"]["observations"]
    enumerated = {Path(item["path"]).relative_to(tmp_path / "repo").as_posix()
                  for item in inputs if item["operation"] == "readdirSync"}
    assert enumerated == {".", "src", "src/nested"}  # Excludes stay with TypeScript.
    assert "export const" not in json.dumps(row["recursive_inputs"])
    original_open, original_resolve = Path.open, Path.resolve
    def no_target_open(candidate, *args, **kwargs):
        if candidate.is_relative_to(tmp_path / "repo"):
            pytest.fail("binding reopened target")
        return original_open(candidate, *args, **kwargs)
    def no_target_resolve(candidate, *args, **kwargs):
        if candidate.is_relative_to(tmp_path / "repo"):
            pytest.fail("binding resolved live target")
        return original_resolve(candidate, *args, **kwargs)
    monkeypatch.setattr(Path, "open", no_target_open)
    monkeypatch.setattr(Path, "resolve", no_target_resolve)
    monkeypatch.setattr("os.walk", lambda *args, **kwargs: pytest.fail("second target walk"))
    assert semantic_positive_resolution_errors(context, atlas["MAIN"]) == []
    assert write_receipt(tmp_path, payload, atlas)["status"] == "BLOCKED"


@pytest.mark.parametrize("failure", [
    "legacy", "partial", "omitted", "count_bool", "dropped_input", "lost_enumeration",
    "lost_realpath", "wrong_kind", "changed_names", "unknown_kind", "missing_parent",
    "missing_nested", "untyped_file", "alias_realpath", "foreign_input", "unsupported_stat",
    "entry_count_bool", "input_digest", "argument_digest", "result_count_bool",
    "result_digest", "io_failure", "filtered", "budget", "incomplete_context",
])
def test_recursive_glob_rejects_missing_or_changed_authority(recursive_checked, tmp_path, failure):
    _payload, atlas, context = recursive_checked
    row, = context["observations"]
    trace = row["recursive_inputs"]
    observations = trace["observations"]
    listing = next(item for item in observations
                   if item["operation"] == "readdirSync" and item["path"].endswith("/src"))
    if failure == "legacy":
        row.pop("recursive_inputs")
    elif failure == "partial":
        trace["complete"] = False
    elif failure == "omitted":
        trace["omitted_observations"] = 1
    elif failure == "count_bool":
        trace["observed_calls"] = True
    elif failure == "dropped_input":
        observations.pop()
    elif failure in {"lost_enumeration", "lost_realpath"}:
        trace["observations"] = [item for item in observations if not (
            item["path"].endswith("/src/nested")
            and (item["operation"] == "readdirSync") == (failure == "lost_enumeration"))]
        trace["observed_calls"] = len(trace["observations"])
    elif failure == "wrong_kind":
        listing["value"]["files"], listing["value"]["directories"] = (
            listing["value"]["directories"], listing["value"]["files"])
    elif failure == "changed_names":
        atlas["MAIN"]["typescript_resolution_inputs"]["directories"]["src"].append("new.ts")
        atlas["MAIN"]["files"]["src/new.ts"] = {"hash": "b" * 64}
    elif failure == "unknown_kind":
        listing["value"]["unsupported_entries"] = 1
    elif failure in {"missing_parent", "missing_nested"}:
        atlas["MAIN"]["typescript_resolution_inputs"]["directories"].pop(
            "." if failure == "missing_parent" else "src/nested")
    elif failure == "untyped_file":
        atlas["MAIN"]["files"].pop("src/a.ts")
    elif failure == "alias_realpath":
        next(item for item in observations if item["operation"].startswith("realpath"))["value"] = (
            tmp_path / "repo/src").as_posix()
    elif failure == "foreign_input":
        listing["path"] = (tmp_path / "outside").as_posix()
    elif failure == "unsupported_stat":
        listing["operation"] = "statSync"
    elif failure == "entry_count_bool":
        listing["value"]["files"]["entries"] = True
    elif failure == "input_digest":
        listing["value"]["files"]["sha256"] = "0" * 64
    elif failure == "argument_digest":
        row["arguments_sha256"] = None
    elif failure == "result_count_bool":
        row["value"]["entries"] = True
    elif failure == "result_digest":
        row["value"]["sha256"] = "invalid"
    elif failure == "io_failure":
        row["io_failure"] = {"code": "EACCES", "operation": "readdirSync", "count": 1}
    elif failure == "filtered":
        row["filtered_entries"] = 1
    elif failure == "budget":
        context["observation_limit"] = 1
    else:
        context["observations_complete"] = False
    assert semantic_positive_resolution_errors(context, atlas["MAIN"])


def test_recursive_glob_capture_survives_partitioned_sqlite(recursive_checked, tmp_path):
    payload, atlas, context = recursive_checked
    store = _sqlite_store(tmp_path / "raw", inline_limit=64, part_size=32)
    store._save_payload_to_state_table("atlas", atlas)
    restored = store.load_raw("atlas", {})
    assert semantic_positive_resolution_errors(context, restored["MAIN"]) == []
    restored["MAIN"]["typescript_resolution_inputs"]["directories"].pop("src/nested")
    assert semantic_positive_resolution_errors(context, restored["MAIN"])
    assert write_receipt(tmp_path, payload, restored)["status"] == "BLOCKED"


@pytest.mark.parametrize("budget", ["max_entries", "max_name_bytes", "observation_limit"])
def test_recursive_input_budget_limits_evidence_not_diagnostics(tmp_path, budget):
    preload = tmp_path / "budget.cjs"
    preload.write_text("""
const fs = require("fs");
const original = fs.readFileSync;
fs.readFileSync = function(candidate, ...args) {
  const result = original.call(this, candidate, ...args);
  if (String(candidate).endsWith("analysis_snapshot_lineage_contract.json") && BUDGET !== "observation_limit") {
    const contract = JSON.parse(result);
    contract.artifacts.ts_diagnostics.atlas_resolution_inputs[BUDGET] = 1;
    return JSON.stringify(contract);
  }
  return result;
};
""".replace("BUDGET", json.dumps(budget)))
    payload, atlas = collect(tmp_path, {
        "src/a.ts": "export const a: string = 1;",
        "src/nested/b.ts": "export const b = 2;",
    }, "--semantic", "true", *(["--maxContextEntries", "4"] if budget == "observation_limit" else []),
        node_preload=preload, project_config={"include": ["src/**/*.ts"]})
    data = payload["projects"]["MAIN"]
    context = data["semantic_context"]
    assert any(row["code"] == "TS2322" for row in data["diagnostics"])
    assert context["observations_complete"] is False
    assert context["omitted_observations"] > 0
    assert "context_observation_budget_exceeded" in context["binding_blockers"]
    units = len(context["observations"]) + sum(
        len(row.get("recursive_inputs", {}).get("observations", [])) for row in context["observations"])
    assert units <= context["observation_limit"]
    assert semantic_positive_resolution_errors(context, atlas["MAIN"])
    assert write_receipt(tmp_path, payload, atlas)["status"] == "BLOCKED"


def test_empty_successful_glob_is_distinct_from_failed_enumeration(tmp_path):
    payload, atlas = collect(tmp_path, {}, "--semantic", "true",
                             project_config={"include": ["**/*.ts"], "compilerOptions": {"noLib": True, "types": []}})
    root = tmp_path / "repo"
    capture = ResolutionInputCapture(root)
    capture.observe_directory(str(root), [], ["tsconfig.json"])
    project = atlas["MAIN"]
    project["typescript_resolution_inputs"] = capture.payload
    project["typescript_auxiliary_inputs"] = capture_auxiliary_inputs(
        root, [(str(root / "tsconfig.json"), "tsconfig.json", "tsconfig.json")])
    context = deepcopy(payload["projects"]["MAIN"]["semantic_context"])
    context["observations"] = [row for row in context["observations"] if row["operation"] == "readDirectory"]
    assert context["observations"][0]["value"]["entries"] == 0
    assert semantic_positive_resolution_errors(context, project) == []
    assert write_receipt(tmp_path, payload, atlas)["status"] == "BLOCKED"


def test_recursive_query_budget_is_shared_across_queries(tmp_path):
    root = tmp_path / "repo"
    (root / "sub").mkdir(parents=True)
    (root / "a.ts").write_text("export const a = 1;")
    collector = CODE_MAPS_DIR / "tools/engines/ts_diagnostics_collector.cjs"
    script = r"""
const fs = require("fs"), path = require("path"), vm = require("vm");
const {createRequire} = require("module");
const [collector, root] = process.argv.slice(1);
const source = fs.readFileSync(collector, "utf8").replace(/\r\n/g, "\n");
const boundary = source.lastIndexOf("\ntry {\n  main();");
if (boundary < 0) throw new Error("collector entrypoint changed");
vm.runInNewContext(source.slice(0, boundary) + `
fileSystemObservation = observeFileSystemFailures();
ts = require("typescript");
const recorder = semanticContextRecorder(9, {max_entries: 65536, max_name_bytes: 4194304});
const host = boundedParseHost(root, recorder, root, {complete: true});
const results = [];
try {
  for (let i = 0; i < 3; i += 1) results.push(host.readDirectory(root, [".ts"], undefined, ["**/*"]));
} finally { fileSystemObservation.restore(); }
console.log(JSON.stringify({results, context: recorder.finish({binding_blockers: []})}));
`, {require: createRequire(collector), Buffer, console, process, root, __dirname: path.dirname(collector)});
"""
    result = subprocess.run(["node", "-e", script, str(collector), str(root)],
                            cwd=CODE_MAPS_DIR, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["results"][0] == data["results"][1] == data["results"][2]
    assert len(data["results"][0]) == 1
    context = data["context"]
    assert context["input_observation_units"] + len(context["observations"]) <= 9
    assert context["observations_complete"] is False
    assert context["omitted_observations"] > 0
