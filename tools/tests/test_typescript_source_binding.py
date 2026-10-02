from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest

from tools.core.analysis_snapshot_lineage import evaluate_snapshot_bound_inputs, receipt_binding, write_lineage_receipt
from tools.core.atlas_integrity import build_atlas_commit
from tools.core.config import CODE_MAPS_DIR
from tools.core.typescript_source_binding import (
    checked_source_errors, semantic_config_root_read_errors, semantic_diagnostic_source_errors,
    semantic_program_declaration_errors, semantic_program_source_errors, semantic_workspace_external_errors,
    semantic_workspace_read_errors,
)
from tools.core.typescript_source_binding import capture_compiler_library_inputs, reconcile_compiler_library_inputs
from tools.engines.merge_simulation_engine import _ts_diagnostics_by_project, simulate_dependency_package


def collect(tmp_path, sources, *args, project_config=None, node_preload=None, collector_cwd=None):
    root = tmp_path / "repo"
    root.mkdir(exist_ok=True)
    for name, content in sources.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(content, encoding="utf-8", newline="")
    (root / "tsconfig.json").write_text(json.dumps(
        project_config if project_config is not None else {"include": ["*.ts"]}), encoding="utf-8")
    config, output = tmp_path / "runtime.json", tmp_path / "diagnostics.json"
    config.write_text(json.dumps({"workspace_root": str(root), "variations": {"MAIN": "."}}), encoding="utf-8")
    result = subprocess.run(
        ["node", *(["--require", str(node_preload)] if node_preload else []),
         str(CODE_MAPS_DIR / "tools/engines/ts_diagnostics_collector.cjs"),
         "--config", str(config), "--out", str(output), *args],
        cwd=collector_cwd or CODE_MAPS_DIR, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    payload["collector_status"] = "OK"
    atlas = {"MAIN": {"project": {"root": str(root)}, "files": {
        # Normal TypeScript AST metadata is SHA-256; Atlas fallback may be MD5.
        name: {"hash": hashlib.sha256(content.encode("utf-8")).hexdigest()}
        for name, content in sources.items()
    }}}
    return payload, atlas


@pytest.fixture
def checked(tmp_path):
    return collect(tmp_path, {"a.ts": "\ufeffexport const value = ;\r\n"})


def write_receipt(raw_dir, payload, atlas):
    return write_lineage_receipt(
        artifact_id="ts_diagnostics", producer="tools.engines.react_frontier_intelligence",
        artifact_payload=payload, atlas=atlas, atlas_commit=build_atlas_commit(atlas),
        raw_dir=raw_dir,
    )


@pytest.fixture
def installed_compiler(tmp_path):
    """Small owned installation, independent of collector-reported paths."""
    base = tmp_path / "sage"
    config = base / "config"
    config.mkdir(parents=True)
    lineage = json.loads((CODE_MAPS_DIR / "config/analysis_snapshot_lineage_contract.json").read_text())
    (config / "analysis_snapshot_lineage_contract.json").write_text(json.dumps(lineage))
    provenance = json.loads((CODE_MAPS_DIR / "config/third_party_distribution_contract.json").read_text())
    owner = next(row for row in provenance["vendored_dependencies"] if row["id"] == "typescript")
    for key in ("manifest_path", "lockfile_path", "runtime_package_path"):
        (base / owner[key]).parent.mkdir(parents=True, exist_ok=True)
    (base / owner["manifest_path"]).write_text(json.dumps({"dependencies": {"typescript": owner["locked_version"]}}))
    (base / owner["lockfile_path"]).write_bytes(b"owned-lock")
    owner["lockfile_sha256"] = hashlib.sha256(b"owned-lock").hexdigest()
    (config / "third_party_distribution_contract.json").write_text(json.dumps(provenance))
    package = base / owner["runtime_package_path"]
    package.write_text(json.dumps({"name": "typescript", "version": owner["locked_version"], "main": "lib/typescript.js"}))
    library = package.parent / "lib"
    library.mkdir()
    (library / "typescript.js").write_bytes(b"/* installed fixture, never executed */")
    (library / "lib.d.ts").write_bytes(b"\xef\xbb\xbfdeclare const example: string;\r\n")
    return base, library, owner


def test_compiler_inventory_preserves_raw_and_actual_host_text_identity(installed_compiler):
    base, library, _ = installed_compiler
    capture = capture_compiler_library_inputs(base, "current")
    assert capture["status"] == "captured"
    raw = (library / "lib.d.ts").read_bytes()
    entry = capture["files"]["lib.d.ts"]
    assert entry["sha256"] == hashlib.sha256(raw).hexdigest()
    assert entry["host_text_sha256"] == hashlib.sha256(raw[3:]).hexdigest()
    assert capture["loaded_code_attested"] is False
    assert capture["snapshot_bound"] is False
    assert capture_compiler_library_inputs(base, "next")["inventory_sha256"] != capture["inventory_sha256"]


@pytest.mark.parametrize("failure", [
    "lock", "version", "module_missing", "entry_escape", "encoding", "package_shape", "read", "directory_as_library",
    "max_files", "max_directory_entries", "max_file_bytes", "max_module_bytes", "max_total_bytes", "invalid_budget",
])
def test_compiler_inventory_failures_are_unavailable_not_authority(installed_compiler, monkeypatch, failure):
    base, library, owner = installed_compiler
    if failure == "lock":
        (base / owner["lockfile_path"]).write_bytes(b"changed")
    elif failure in {"version", "entry_escape", "package_shape"}:
        package = base / owner["runtime_package_path"]
        data = json.loads(package.read_text())
        if failure == "version":
            data["version"] = "0.0.0"
        elif failure == "entry_escape":
            data["main"] = "../outside.js"
        else:
            data = []
        package.write_text(json.dumps(data))
    elif failure == "module_missing":
        (library / "typescript.js").unlink()
    elif failure == "encoding":
        (library / "lib.d.ts").write_bytes(b"\xff\xfe\x00")
    elif failure == "directory_as_library":
        (library / "lib.d.ts").unlink()
        (library / "lib.d.ts").mkdir()
    elif failure == "read":
        original = Path.open
        def denied(candidate, *args, **kwargs):
            if candidate.name == "lib.d.ts":
                raise PermissionError("injected library read failure")
            return original(candidate, *args, **kwargs)
        monkeypatch.setattr(Path, "open", denied)
    else:
        contract = base / "config/analysis_snapshot_lineage_contract.json"
        data = json.loads(contract.read_text())
        policy = data["artifacts"]["ts_diagnostics"]["compiler_library_inputs"]
        policy["max_files" if failure == "invalid_budget" else failure] = False if failure == "invalid_budget" else 1
        if failure == "max_files":
            (library / "lib.extra.d.ts").write_bytes(b"")
        contract.write_text(json.dumps(data))
    capture = capture_compiler_library_inputs(base, "current")
    assert capture["status"] == "unavailable"
    assert capture["files"] == {}
    assert capture["error"]
    assert "inventory_sha256" not in capture


def test_compiler_inventory_rejects_aliased_library(installed_compiler):
    base, library, _ = installed_compiler
    (library / "lib.d.ts").unlink()
    outside = base / "outside.d.ts"
    outside.write_text("declare const other: number;")
    try:
        (library / "lib.d.ts").symlink_to(outside)
    except OSError as error:
        pytest.skip(f"Host cannot create symlink fixture: {error}")
    assert capture_compiler_library_inputs(base, "current")["error"] == "aliased_compiler_input"


@pytest.fixture(scope="module")
def compiler_input_run(tmp_path_factory):
    temp = tmp_path_factory.mktemp("compiler-input")
    inventory = capture_compiler_library_inputs(CODE_MAPS_DIR, "library-input")
    assert inventory["status"] == "captured", inventory.get("error")
    payload, atlas = collect(temp, {"a.ts": "export const x: string = 42;"}, "--semantic", "true",
                             "--requestId", "library-input", project_config={"files": ["a.ts"]})
    return payload, atlas, inventory


def test_real_library_reads_match_independent_invocation_inventory_without_live_lookups(compiler_input_run, monkeypatch, tmp_path):
    payload, atlas, inventory = compiler_input_run
    def forbidden(*args, **kwargs):
        raise AssertionError("Reconciliation must not reopen or resolve live paths")
    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", forbidden)
        patch.setattr(Path, "resolve", forbidden)
        binding = reconcile_compiler_library_inputs(payload, inventory)
    assert binding["status"] == "MATCHED_POSITIVE_READS", binding
    assert "lib.d.ts" in binding["projects"]["MAIN"]["matched_files"]
    assert "lib.d.ts" in binding["projects"]["MAIN"]["matched_program_files"]
    assert binding["loaded_code_attested"] is False
    assert binding["snapshot_bound"] is False
    assert binding["inventory"] == inventory
    with_binding = {**payload, "compiler_library_input_binding": binding}
    receipt = write_receipt(tmp_path, with_binding, atlas)
    assert receipt["status"] == "BLOCKED"
    _, usable = evaluate_snapshot_bound_inputs(
        contract_path=CODE_MAPS_DIR / "config/merge_simulation_input_contract.json",
        raw_dir=tmp_path, expected_snapshot_id=receipt["atlas_snapshot_id"],
        payloads={"ts_diagnostics": with_binding},
    )
    assert "ts_diagnostics" not in usable


def test_program_library_sourcefiles_require_independent_installed_text_and_complete_manifest(
    compiler_input_run, tmp_path,
):
    payload, _, inventory = compiler_input_run
    manifest = payload["projects"]["MAIN"]["checked_source_manifest"]
    library_files = manifest["program_outside_workspace_files"]
    assert manifest["program_outside_workspace_files_total"] == len(library_files)
    assert library_files
    assert all(Path(row["file"]).is_relative_to(Path(inventory["library_root"])) for row in library_files)
    assert reconcile_compiler_library_inputs(payload, inventory)["status"] == "MATCHED_POSITIVE_READS"

    stale = deepcopy(payload)
    relative = os.path.relpath(
        library_files[0]["file"], stale["projects"]["MAIN"]["project_root"],
    ).replace("\\", "/")
    row = next(row for row in stale["projects"]["MAIN"]["checked_source_manifest"]["files"]
               if row["file"] == relative)
    row["sha256"] = "0" * 64
    assert reconcile_compiler_library_inputs(stale, inventory)["status"] == "BLOCKED"

    budgeted, _ = collect(
        tmp_path, {"a.ts": "export const x = 1;"}, "--semantic", "true",
        "--requestId", "library-input", "--maxFiles", "1",
        project_config={"files": ["a.ts"]},
    )
    assert budgeted["projects"]["MAIN"]["checked_source_manifest"][
        "program_outside_workspace_files_total"] > 0
    binding = reconcile_compiler_library_inputs(budgeted, inventory)
    assert binding["status"] == "BLOCKED"
    assert "MAIN:compiler_program_inventory_incomplete" in binding["errors"]


@pytest.mark.parametrize("failure", [
    "nonlibrary", "duplicate", "declaration", "missing_row", "count", "version",
])
def test_program_library_manifest_fails_closed_on_unowned_or_malformed_sourcefile(
    compiler_input_run, failure,
):
    payload, _, inventory = deepcopy(compiler_input_run)
    manifest = payload["projects"]["MAIN"]["checked_source_manifest"]
    entry = manifest["program_outside_workspace_files"][0]
    if failure == "nonlibrary":
        original = entry["file"]
        entry["file"] = str(Path(inventory["library_root"]).parent / "foreign.d.ts").replace("\\", "/")
        original_name = os.path.relpath(original, payload["projects"]["MAIN"]["project_root"]).replace("\\", "/")
        replacement = os.path.relpath(
            entry["file"], payload["projects"]["MAIN"]["project_root"],
        ).replace("\\", "/")
        next(row for row in manifest["files"] if row["file"] == original_name)["file"] = replacement
        expected = "MAIN:compiler_program_nonlibrary_input_unbound"
    elif failure == "duplicate":
        manifest["program_outside_workspace_files"].append(deepcopy(entry))
        manifest["program_outside_workspace_files_total"] += 1
        manifest["source_files_total"] += 1
        manifest["omitted_files"] += 1
        expected = "MAIN:compiler_program_path_unbound"
    elif failure == "declaration":
        entry["declaration"] = False
        expected = "MAIN:compiler_program_source_unbound"
    elif failure == "missing_row":
        relative = os.path.relpath(
            entry["file"], payload["projects"]["MAIN"]["project_root"],
        ).replace("\\", "/")
        manifest["files"] = [row for row in manifest["files"] if row["file"] != relative]
        manifest["omitted_files"] += 1
        expected = "MAIN:compiler_program_manifest_unavailable"
    elif failure == "count":
        manifest["program_outside_workspace_files_total"] = -1
        expected = "MAIN:compiler_program_inventory_unavailable"
    else:
        manifest["version"] = "unknown"
        expected = "MAIN:compiler_program_inventory_unavailable"
    binding = reconcile_compiler_library_inputs(payload, inventory)
    assert binding["status"] == "BLOCKED"
    assert any(error == expected or error.startswith(expected + ":") for error in binding["errors"])


@pytest.mark.parametrize("failure", [
    "stale_run", "nonzero", "unknown_inventory", "compiler_path", "compiler_version", "module_hash",
    "omitted", "read_hash", "read_scope", "read_path", "read_failure", "read_shape", "no_reads", "context_shape",
])
def test_library_reconciliation_fails_closed_without_trusting_child_claims(compiler_input_run, failure):
    payload, _, inventory = deepcopy(compiler_input_run)
    context = payload["projects"]["MAIN"]["semantic_context"]
    row = next(item for item in context["observations"] if item["operation"] == "readFile"
               and item["input_scope"] == "compiler_library")
    if failure == "stale_run":
        payload["meta"]["collector_run_id"] = "old"
    elif failure == "nonzero":
        payload["collector_status"] = "NONZERO_EXIT"
    elif failure == "unknown_inventory":
        inventory["status"] = "unavailable"
    elif failure in {"compiler_path", "compiler_version", "module_hash"}:
        key = {"compiler_path": "installed_module", "compiler_version": "version", "module_hash": "installed_module_sha256"}[failure]
        context["compiler"][key] = "not-the-inventory"
    elif failure == "omitted":
        context["observations_complete"] = False
    elif failure == "read_hash":
        row["value"]["text_sha256"] = "0" * 64
    elif failure == "read_scope":
        row["input_scope"] = "workspace"
    elif failure == "read_path":
        row["path"] = str(Path(inventory["library_root"]).parent / "outside.d.ts")
    elif failure == "read_failure":
        row["access_status"] = "unavailable"
    elif failure == "read_shape":
        row["value"] = None
    elif failure == "context_shape":
        payload["projects"]["MAIN"]["semantic_context"] = []
    else:
        context["observations"] = []
    payload["compiler_library_input_binding"] = {"status": "MATCHED_POSITIVE_READS", "snapshot_bound": True}
    binding = reconcile_compiler_library_inputs(payload, inventory)
    assert binding["status"] == "BLOCKED"
    assert binding["errors"]


def test_actual_library_host_override_does_not_match_preinvocation_inventory(tmp_path):
    inventory = capture_compiler_library_inputs(CODE_MAPS_DIR, "changed-read")
    preload = tmp_path / "library-override.cjs"
    preload.write_text("""
const ts = require(require.resolve("typescript", {paths: [require("path").join(process.cwd(), "tools", "engines")]}));
const original = ts.sys.readFile;
ts.sys.readFile = (candidate, ...args) => {
  const text = original(candidate, ...args);
  return String(candidate).endsWith("lib.d.ts") && typeof text === "string"
    ? text + "\\n// different returned input" : text;
};
""")
    payload, _ = collect(tmp_path, {"a.ts": "export const x = 1;"}, "--semantic", "true",
                         "--requestId", "changed-read", node_preload=preload)
    assert reconcile_compiler_library_inputs(payload, inventory)["status"] == "BLOCKED"


@pytest.mark.parametrize("semantic", [False, True])
def test_frontier_owner_captures_before_launch_and_overwrites_child_authority(tmp_path, monkeypatch, compiler_input_run, semantic):
    from tools.engines import react_frontier_intelligence as frontier
    from types import SimpleNamespace
    payload, _, inventory = deepcopy(compiler_input_run)
    payload["compiler_library_input_binding"] = {"status": "SELF_AUTHORIZED", "snapshot_bound": True}
    events = []
    def capture(base, run_id):
        assert base == CODE_MAPS_DIR and run_id == "library-input"
        events.append("capture")
        return inventory
    def launch(command, **kwargs):
        events.append("launch")
        assert command[command.index("--semantic") + 1] == str(semantic).lower()
        assert set(kwargs["env"]) <= {"PATH", "SYSTEMROOT", "COMSPEC", "TEMP", "TMP"}
        return SimpleNamespace(returncode=0, stdout="", stderr=""), 0.01
    monkeypatch.setattr(frontier, "uuid4", lambda: "library-input")
    monkeypatch.setattr(frontier, "capture_compiler_library_inputs", capture)
    monkeypatch.setattr(frontier, "run_observed_subprocess", launch)
    monkeypatch.setattr(frontier, "load_json_file", lambda *args, **kwargs: payload)
    monkeypatch.setattr(frontier, "save_json_atomic", lambda *args: None)
    result = frontier._collect_ts_diagnostics(semantic=semantic)
    assert events == (["capture", "launch"] if semantic else ["launch"])
    if semantic:
        assert result["compiler_library_input_binding"]["status"] == "MATCHED_POSITIVE_READS"
        assert result["compiler_library_input_binding"]["snapshot_bound"] is False
    else:
        assert "compiler_library_input_binding" not in result


def test_real_frontier_semantic_invocation_persists_owner_reconciliation(tmp_path, monkeypatch):
    from tools.engines import react_frontier_intelligence as frontier
    # Build the same target fixture, then exercise the real Python -> Node ->
    # managed artifact path, not just the standalone comparison helper.
    collect(tmp_path, {"a.ts": "export const x: number = 'invalid';"}, project_config={"files": ["a.ts"]})
    monkeypatch.setattr(frontier, "CONFIG_FILE", tmp_path / "runtime.json")
    monkeypatch.setattr(frontier, "TS_DIAGNOSTICS_PATH", tmp_path / "owned.json")
    saved = []
    monkeypatch.setattr(frontier, "save_json_atomic", lambda path, payload: saved.append(deepcopy(payload)))
    result = frontier._collect_ts_diagnostics(semantic=True)
    assert result["collector_status"] == "OK"
    binding = result["compiler_library_input_binding"]
    assert binding["status"] == "MATCHED_POSITIVE_READS", binding
    assert binding["collector_run_id"] == result["meta"]["collector_run_id"]
    assert saved == [result]


def test_real_collector_binds_exact_parsed_content_including_bom_and_crlf(tmp_path, checked):
    payload, atlas = checked
    policy = json.loads((CODE_MAPS_DIR / "config/analysis_snapshot_lineage_contract.json").read_text(
        encoding="utf-8"))
    assert payload["projects"]["MAIN"]["tsconfig"] == policy["artifacts"]["ts_diagnostics"]["root_config_filename"]
    source = payload["projects"]["MAIN"]["checked_source_manifest"]["files"][0]
    assert source["sha256"] == atlas["MAIN"]["files"]["a.ts"]["hash"]
    assert checked_source_errors(payload, atlas) == []
    receipt = write_receipt(tmp_path, payload, atlas)
    assert receipt["status"] == "COMPLETE"
    assert receipt_binding(raw_dir=tmp_path, artifact_id="ts_diagnostics",
                           artifact_payload=payload, expected_snapshot_id=receipt["atlas_snapshot_id"])[0] == "BOUND"
    tampered = deepcopy(payload)
    tampered["projects"]["MAIN"]["diagnostics"] = []
    assert receipt_binding(raw_dir=tmp_path, artifact_id="ts_diagnostics",
                           artifact_payload=tampered, expected_snapshot_id=receipt["atlas_snapshot_id"])[0] == "MISMATCH"


def test_syntax_atlas_digest_contract_fails_closed_without_matching_identity(checked):
    payload, atlas = checked
    missing = deepcopy(payload)
    missing["projects"]["MAIN"]["checked_source_manifest"]["files"][0].pop("sha256")
    assert "typescript:MAIN:source_content_mismatch:a.ts" in checked_source_errors(missing, atlas)
    fallback = deepcopy(atlas)
    fallback["MAIN"]["files"]["a.ts"]["hash"] = payload["projects"]["MAIN"]["checked_source_manifest"]["files"][0]["md5"]
    assert checked_source_errors(payload, fallback) == []
    missing_fallback = deepcopy(payload)
    missing_fallback["projects"]["MAIN"]["checked_source_manifest"]["files"][0].pop("md5")
    assert "typescript:MAIN:source_content_mismatch:a.ts" in checked_source_errors(missing_fallback, fallback)


@pytest.mark.parametrize("case", ["stale", "missing", "root", "escape", "duplicate", "diagnostic",
                                  "truncated", "semantic", "nonzero", "unknown_project"])
def test_unbound_context_never_receives_complete_lineage(tmp_path, checked, case):
    payload, atlas = checked
    data = payload["projects"]["MAIN"]
    manifest = data["checked_source_manifest"]
    if case == "stale":
        atlas["MAIN"]["files"]["a.ts"]["hash"] = "0" * 64
    elif case == "missing":
        data.pop("checked_source_manifest")
    elif case == "root":
        data["project_root"] = str(tmp_path)
    elif case == "escape":
        manifest["files"][0]["file"] = "../a.ts"
    elif case == "duplicate":
        manifest["files"].append(deepcopy(manifest["files"][0]))
    elif case == "diagnostic":
        data["diagnostics"][0]["file"] = "not-checked.ts"
    elif case == "truncated":
        manifest["complete"] = False
    elif case == "semantic":
        data["mode"] = "semantic"
    elif case == "nonzero":
        payload["collector_status"] = "NONZERO_EXIT"
    elif case == "unknown_project":
        payload["meta"]["execution_scope"]["unavailable_requested_projects"] = ["OTHER"]
    receipt = write_receipt(tmp_path, payload, atlas)
    assert receipt["status"] == "BLOCKED"
    assert receipt["errors"]


@pytest.mark.parametrize("bad_meta", [None, "invalid", {"execution_scope": {"analyzed_projects": [{}]}}])
def test_malformed_scope_is_unavailable_not_an_exception(checked, bad_meta):
    payload, atlas = checked
    payload["meta"] = bad_meta
    assert checked_source_errors(payload, atlas)


def test_syntax_root_binding_does_not_requery_live_filesystem(checked, monkeypatch):
    payload, atlas = checked
    root = atlas["MAIN"]["project"]["root"]
    resolve = Path.resolve
    def denied(path, *args, **kwargs):
        if str(path) == root:
            raise AssertionError("snapshot binder must not resolve the live target root")
        return resolve(path, *args, **kwargs)
    monkeypatch.setattr(Path, "resolve", denied)
    assert checked_source_errors(payload, atlas) == []


def test_real_checked_syntax_diagnostic_stays_syntax_scoped(checked):
    from tools.engines.react_frontier_intelligence import (
        _enrich_findings_with_ts_diagnostics, _ts_diagnostic_findings,
    )

    payload, atlas = checked
    assert checked_source_errors(payload, atlas) == []
    data = payload["projects"]["MAIN"]
    assert data["mode"] == "syntax" and data["diagnostics"]
    findings = _ts_diagnostic_findings(payload)
    assert len(findings) == 1
    assert findings[0]["risk"] == "compiler_reported_syntax_failure"
    assert findings[0]["evidence_scope"] == "checked_syntax_files_only"
    unrelated = [{"project": "MAIN", "file": "a.ts", "dimension": "react_security",
                  "score": 5, "evidence": "static suspicion"}]
    assert _enrich_findings_with_ts_diagnostics(unrelated, payload) == (unrelated, 0)


@pytest.mark.parametrize("suffix", ["\\nested\\..", "\\."])
def test_syntax_root_alias_cannot_borrow_atlas_identity(checked, tmp_path, suffix):
    payload, atlas = checked
    root = atlas["MAIN"]["project"]["root"]
    alias = deepcopy(payload)
    alias["projects"]["MAIN"]["project_root"] = root + suffix
    assert "typescript:MAIN:project_root_mismatch" in checked_source_errors(alias, atlas)
    assert write_receipt(tmp_path, alias, atlas)["status"] == "BLOCKED"


@pytest.mark.parametrize("meta", [{"collector_run_id": "previous-run"}, None, "malformed"])
def test_previous_collector_output_cannot_reenter_current_analysis(monkeypatch, tmp_path, meta):
    from tools.engines import react_frontier_intelligence as frontier
    from types import SimpleNamespace

    collector = tmp_path / "collector.cjs"
    collector.write_text("", encoding="utf-8")
    monkeypatch.setattr(frontier, "TS_COLLECTOR", collector)
    monkeypatch.setattr(frontier, "run_observed_subprocess", lambda *a, **k: (
        SimpleNamespace(returncode=0, stdout="", stderr=""), 0.01))
    monkeypatch.setattr(frontier, "load_json_file", lambda *a, **k: {
        "meta": meta, "projects": {"MAIN": {}}})
    assert frontier._collect_ts_diagnostics()["status"] == "STALE_OUTPUT"


@pytest.mark.parametrize("atlas", [None, "invalid", []])
def test_missing_atlas_is_unavailable_not_an_exception(checked, atlas):
    assert checked_source_errors(checked[0], atlas) == ["typescript_atlas_unavailable"]


def test_diagnostic_and_file_budget_report_actual_coverage(tmp_path):
    sources = {"a.ts": "export const a = ;", "b.ts": "export const b = ;"}
    payload, atlas = collect(tmp_path, sources, "--maxDiagnostics", "1")
    data = payload["projects"]["MAIN"]
    assert data["summary"]["root_files_checked"] == 1
    assert len(data["checked_source_manifest"]["files"]) == 1
    assert data["checked_source_manifest"]["complete"] is False
    assert checked_source_errors(payload, atlas)
    payload, atlas = collect(tmp_path, sources, "--maxFiles", "1")
    assert payload["projects"]["MAIN"]["checked_source_manifest"]["complete"] is False
    assert checked_source_errors(payload, atlas)


def test_semantic_collector_keeps_compiler_context_explicitly_unbound(tmp_path):
    payload, atlas = collect(tmp_path, {"a.ts": "export const a: string = 42;"}, "--semantic", "true", "--maxFiles", "2")
    manifest = payload["projects"]["MAIN"]["checked_source_manifest"]
    assert len(manifest["files"]) <= 2
    assert manifest["scope"] == "semantic_context_not_snapshot_bound"
    assert checked_source_errors(payload, atlas)


def test_semantic_diagnostic_source_text_is_bounded_to_atlas_without_semantic_promotion(tmp_path):
    payload, atlas = collect(
        tmp_path, {"a.ts": "export const a: string = 42;"}, "--semantic", "true",
        project_config={"files": ["a.ts"], "compilerOptions": {"noLib": True, "types": []}},
    )
    data = payload["projects"]["MAIN"]
    assert any(row["file"] == "a.ts" for row in data["diagnostics"])
    assert semantic_diagnostic_source_errors(data, atlas["MAIN"]) == []
    fallback = deepcopy(atlas)
    fallback["MAIN"]["files"]["a.ts"]["hash"] = next(
        row for row in data["checked_source_manifest"]["files"] if row["file"] == "a.ts")["md5"]
    assert semantic_diagnostic_source_errors(data, fallback["MAIN"]) == []
    missing_digest = deepcopy(data)
    next(row for row in missing_digest["checked_source_manifest"]["files"] if row["file"] == "a.ts").pop("md5")
    assert semantic_diagnostic_source_errors(missing_digest, fallback["MAIN"]) == [
        "semantic_diagnostic_source_mismatch:a.ts"]
    assert "typescript:MAIN:semantic_context_not_snapshot_bound" in checked_source_errors(payload, atlas)

    stale = deepcopy(atlas)
    stale["MAIN"]["files"]["a.ts"]["hash"] = "0" * 64
    assert semantic_diagnostic_source_errors(data, stale["MAIN"]) == [
        "semantic_diagnostic_source_mismatch:a.ts"]
    assert "typescript:MAIN:semantic_diagnostic_source_mismatch:a.ts" in checked_source_errors(payload, stale)

    missing = deepcopy(data)
    missing["checked_source_manifest"]["files"] = [
        row for row in missing["checked_source_manifest"]["files"] if row["file"] != "a.ts"]
    missing["checked_source_manifest"]["omitted_files"] += 1
    assert semantic_diagnostic_source_errors(missing, atlas["MAIN"]) == [
        "semantic_diagnostic_manifest_missing:a.ts"]

    duplicate = deepcopy(data)
    manifest = duplicate["checked_source_manifest"]
    manifest["files"].append(deepcopy(next(row for row in manifest["files"] if row["file"] == "a.ts")))
    manifest["source_files_total"] += 1
    assert semantic_diagnostic_source_errors(duplicate, atlas["MAIN"]) == [
        "semantic_diagnostic_manifest_ambiguous:a.ts"]

    unsafe = deepcopy(data)
    unsafe["diagnostics"][0]["file"] = "../a.ts"
    assert semantic_diagnostic_source_errors(unsafe, atlas["MAIN"]) == [
        "semantic_diagnostic_file_path_unbound"]
    malformed = deepcopy(data)
    malformed["diagnostics"][0]["file"] = ["a.ts"]
    assert semantic_diagnostic_source_errors(malformed, atlas["MAIN"]) == [
        "semantic_checked_manifest_unavailable"]


def test_semantic_diagnostic_text_does_not_assume_bom_normalization(tmp_path):
    payload, atlas = collect(
        tmp_path, {"a.ts": "\ufeffexport const a: string = 42;\r\n"}, "--semantic", "true",
        project_config={"files": ["a.ts"], "compilerOptions": {"noLib": True, "types": []}},
    )
    data = payload["projects"]["MAIN"]
    assert any(row["file"] == "a.ts" for row in data["diagnostics"])
    assert semantic_diagnostic_source_errors(data, atlas["MAIN"]) == [
        "semantic_diagnostic_source_mismatch:a.ts"]
    assert "typescript:MAIN:semantic_context_not_snapshot_bound" in checked_source_errors(payload, atlas)


def test_semantic_manifest_budget_prioritizes_emitted_diagnostic_source(tmp_path):
    payload, atlas = collect(
        tmp_path, {"a.ts": "export const a: string = 42;"}, "--semantic", "true", "--maxFiles", "1",
        project_config={"files": ["a.ts"]},
    )
    data = payload["projects"]["MAIN"]
    manifest = data["checked_source_manifest"]
    assert manifest["source_files_total"] > 1  # Installed library files also consume the program.
    assert manifest["omitted_files"] > 0
    assert [row["file"] for row in manifest["files"]] == ["a.ts"]
    assert semantic_diagnostic_source_errors(data, atlas["MAIN"]) == []
    assert "typescript:MAIN:semantic_context_not_snapshot_bound" in checked_source_errors(payload, atlas)


def test_semantic_manifest_budget_keeps_selected_root_without_diagnostic(tmp_path):
    payload, atlas = collect(
        tmp_path, {"a.ts": "export const a = 42;"}, "--semantic", "true", "--maxFiles", "1",
        project_config={"files": ["a.ts"]},
    )
    data = payload["projects"]["MAIN"]
    assert not any(row["file"] == "a.ts" for row in data["diagnostics"])
    assert data["checked_source_manifest"]["omitted_files"] > 0
    assert [row["file"] for row in data["checked_source_manifest"]["files"]] == ["a.ts"]
    assert data["checked_source_manifest"]["selected_root_files"] == ["a.ts"]
    assert data["checked_source_manifest"]["files"][0]["sha256"] == atlas["MAIN"]["files"]["a.ts"]["hash"]
    assert semantic_diagnostic_source_errors(data, atlas["MAIN"]) == []
    stale = deepcopy(atlas)
    stale["MAIN"]["files"]["a.ts"]["hash"] = "0" * 64
    assert semantic_diagnostic_source_errors(data, stale["MAIN"]) == [
        "semantic_root_source_mismatch:a.ts"]
    missing = deepcopy(data)
    missing["checked_source_manifest"]["files"] = []
    missing["checked_source_manifest"]["omitted_files"] += 1
    assert semantic_diagnostic_source_errors(missing, atlas["MAIN"]) == [
        "semantic_root_manifest_missing:a.ts"]
    malformed = deepcopy(data)
    malformed["checked_source_manifest"]["selected_root_files"] = ["../a.ts"]
    assert semantic_diagnostic_source_errors(malformed, atlas["MAIN"]) == [
        "semantic_root_file_path_unbound"]
    assert "typescript:MAIN:semantic_context_not_snapshot_bound" in checked_source_errors(payload, atlas)


def test_semantic_program_dependency_without_diagnostic_is_compared_to_atlas(tmp_path):
    payload, atlas = collect(
        tmp_path, {
            "a.ts": 'import { value } from "./b"; export const result = value;',
            "b.ts": "export const value = 42;",
        }, "--semantic", "true",
        project_config={"files": ["a.ts"], "compilerOptions": {"noLib": True, "types": []}},
    )
    data = payload["projects"]["MAIN"]
    assert data["checked_source_manifest"]["selected_root_files"] == ["a.ts"]
    assert all(row["file"] != "b.ts" for row in data["diagnostics"])
    assert semantic_diagnostic_source_errors(data, atlas["MAIN"]) == []
    assert semantic_program_source_errors(data, atlas["MAIN"]) == []
    stale = deepcopy(atlas)
    stale["MAIN"]["files"]["b.ts"]["hash"] = "0" * 64
    assert semantic_program_source_errors(data, stale["MAIN"]) == [
        "semantic_program_source_mismatch:b.ts"]
    assert "typescript:MAIN:semantic_program_source_mismatch:b.ts" in checked_source_errors(
        payload, stale)
    assert "typescript:MAIN:semantic_context_not_snapshot_bound" in checked_source_errors(
        payload, atlas)
    receipt = write_receipt(tmp_path, payload, stale)
    assert "typescript:MAIN:semantic_program_source_mismatch:b.ts" in receipt["errors"]
    _, usable = evaluate_snapshot_bound_inputs(
        contract_path=CODE_MAPS_DIR / "config/merge_simulation_input_contract.json",
        raw_dir=tmp_path, expected_snapshot_id=build_atlas_commit(stale)["snapshot_id"],
        payloads={"ts_diagnostics": payload},
    )
    assert "ts_diagnostics" not in usable


def test_semantic_program_source_budget_and_missing_authority_remain_unbound(tmp_path):
    payload, atlas = collect(
        tmp_path, {
            "a.ts": 'import { value } from "./b"; export const result = value;',
            "b.ts": "export const value = 42;",
        }, "--semantic", "true", "--maxFiles", "1",
        project_config={"files": ["a.ts"], "compilerOptions": {"noLib": True, "types": []}},
    )
    data = payload["projects"]["MAIN"]
    assert "semantic_program_source_inventory_incomplete" in semantic_program_source_errors(
        data, atlas["MAIN"])
    missing = deepcopy(atlas)
    missing["MAIN"]["files"].pop("a.ts")
    assert "semantic_program_source_unbound:a.ts" in semantic_program_source_errors(
        data, missing["MAIN"])
    malformed = deepcopy(data)
    malformed["checked_source_manifest"].pop("program_project_source_files")
    assert semantic_program_source_errors(malformed, atlas["MAIN"]) == [
        "semantic_program_source_inventory_unavailable"]
    assert "typescript:MAIN:semantic_context_not_snapshot_bound" in checked_source_errors(
        payload, atlas)


@pytest.mark.parametrize("declaration_text", [
    "export interface Shape { x: number; }",
    "\ufeffexport interface Shape { x: number; }\r\n",
])
def test_semantic_program_declaration_text_matches_owned_atlas_auxiliary(tmp_path, declaration_text):
    from tools.core.atlas_typescript_inputs import capture_auxiliary_inputs

    sources = {
        "a.ts": 'import type { Shape } from "./types"; export const shape: Shape = { x: 1 };',
        "types.d.ts": declaration_text,
    }
    payload, atlas = collect(
        tmp_path, sources, "--semantic", "true",
        project_config={"files": ["a.ts"], "compilerOptions": {"noLib": True, "types": []}},
    )
    root = tmp_path / "repo"
    atlas["MAIN"]["files"].pop("types.d.ts")
    atlas["MAIN"]["typescript_auxiliary_inputs"] = capture_auxiliary_inputs(
        root, [(str(root / "types.d.ts"), "types.d.ts", "types.d.ts")])
    data = payload["projects"]["MAIN"]
    manifest = data["checked_source_manifest"]
    assert manifest["program_project_declaration_files"] == ["types.d.ts"]
    assert manifest["program_project_declaration_files_total"] == 1
    assert "types.d.ts" not in manifest["selected_root_files"]
    assert all(row["file"] != "types.d.ts" for row in data["diagnostics"])
    assert semantic_program_declaration_errors(data, atlas["MAIN"]) == []

    stale = deepcopy(atlas)
    stale["MAIN"]["typescript_auxiliary_inputs"]["files"]["types.d.ts"]["host_text_sha256"] = "0" * 64
    assert semantic_program_declaration_errors(data, stale["MAIN"]) == [
        "semantic_program_declaration_mismatch:types.d.ts"]
    assert "typescript:MAIN:semantic_program_declaration_mismatch:types.d.ts" in checked_source_errors(
        payload, stale)
    assert "typescript:MAIN:semantic_context_not_snapshot_bound" in checked_source_errors(
        payload, atlas)
    receipt = write_receipt(tmp_path, payload, stale)
    assert "typescript:MAIN:semantic_program_declaration_mismatch:types.d.ts" in receipt["errors"]
    _, usable = evaluate_snapshot_bound_inputs(
        contract_path=CODE_MAPS_DIR / "config/merge_simulation_input_contract.json",
        raw_dir=tmp_path, expected_snapshot_id=build_atlas_commit(stale)["snapshot_id"],
        payloads={"ts_diagnostics": payload},
    )
    assert "ts_diagnostics" not in usable

    missing = deepcopy(atlas)
    missing["MAIN"]["typescript_auxiliary_inputs"]["files"].pop("types.d.ts")
    assert semantic_program_declaration_errors(data, missing["MAIN"]) == [
        "semantic_program_declaration_unbound:types.d.ts"]
    malformed = deepcopy(data)
    malformed["checked_source_manifest"].pop("program_project_declaration_files")
    assert semantic_program_declaration_errors(malformed, atlas["MAIN"]) == [
        "semantic_program_declaration_inventory_unavailable"]
    unsafe_path = deepcopy(data)
    unsafe_path["checked_source_manifest"]["program_project_declaration_files"] = ["../types.d.ts"]
    assert semantic_program_declaration_errors(unsafe_path, atlas["MAIN"]) == [
        "semantic_program_declaration_path_unbound"]
    unsafe_root = deepcopy(atlas["MAIN"])
    unsafe_root["project"]["root"] = str(root / ".." / "repo")
    assert semantic_program_declaration_errors(data, unsafe_root) == [
        "semantic_program_declaration_inventory_unavailable"]


def test_semantic_program_declaration_budget_omission_is_explicit(tmp_path):
    payload, atlas = collect(
        tmp_path, {
            "a.ts": 'import type { Shape } from "./types"; export const shape: Shape = { x: 1 };',
            "types.d.ts": "export interface Shape { x: number; }",
        }, "--semantic", "true", "--maxFiles", "1",
        project_config={"files": ["a.ts"], "compilerOptions": {"noLib": True, "types": []}},
    )
    manifest = payload["projects"]["MAIN"]["checked_source_manifest"]
    assert manifest["program_project_declaration_files_total"] == 1
    assert manifest["program_project_declaration_files"] == []
    assert "semantic_program_declaration_inventory_incomplete" in semantic_program_declaration_errors(
        payload["projects"]["MAIN"], atlas["MAIN"])


def test_semantic_cross_project_program_source_requires_unique_atlas_owner(tmp_path):
    workspace = tmp_path / "workspace"
    alpha, beta = workspace / "alpha", workspace / "beta"
    alpha.mkdir(parents=True)
    beta.mkdir()
    alpha_source = 'import { value } from "../beta/b"; export const result = value;'
    beta_source = "export const value = 42;"
    (alpha / "a.ts").write_text(alpha_source, encoding="utf-8")
    (beta / "b.ts").write_text(beta_source, encoding="utf-8")
    config_content = {"files": ["a.ts"], "compilerOptions": {"noLib": True, "types": []}}
    (alpha / "tsconfig.json").write_text(json.dumps(config_content), encoding="utf-8")
    (beta / "tsconfig.json").write_text(json.dumps({
        **config_content, "files": ["b.ts"],
    }), encoding="utf-8")
    runtime = tmp_path / "runtime.json"
    output = tmp_path / "diagnostics.json"
    runtime.write_text(json.dumps({
        "workspace_root": str(workspace), "variations": {"ALPHA": "alpha", "BETA": "beta"},
    }), encoding="utf-8")
    result = subprocess.run(
        ["node", str(CODE_MAPS_DIR / "tools/engines/ts_diagnostics_collector.cjs"),
         "--config", str(runtime), "--out", str(output), "--semantic", "true", "--maxFiles", "8"],
        cwd=CODE_MAPS_DIR, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    payload["collector_status"] = "OK"
    atlas = {
        "ALPHA": {"project": {"root": str(alpha)}, "files": {
            "a.ts": {"hash": hashlib.sha256(alpha_source.encode()).hexdigest()},
        }},
        "BETA": {"project": {"root": str(beta)}, "files": {
            "b.ts": {"hash": hashlib.sha256(beta_source.encode()).hexdigest()},
        }},
    }
    data = payload["projects"]["ALPHA"]
    manifest = data["checked_source_manifest"]
    assert manifest["program_workspace_external_files"] == [{
        "file": (beta / "b.ts").as_posix(), "declaration": False,
    }]
    assert manifest["program_workspace_external_files_total"] == 1
    assert semantic_workspace_external_errors(data, atlas, "ALPHA") == []

    stale = deepcopy(atlas)
    stale["BETA"]["files"]["b.ts"]["hash"] = "0" * 64
    assert semantic_workspace_external_errors(data, stale, "ALPHA") == [
        "semantic_workspace_external_mismatch:BETA:b.ts"]
    assert "typescript:ALPHA:semantic_workspace_external_mismatch:BETA:b.ts" in checked_source_errors(
        payload, stale)
    receipt = write_receipt(tmp_path, payload, stale)
    assert "typescript:ALPHA:semantic_workspace_external_mismatch:BETA:b.ts" in receipt["errors"]

    missing = deepcopy(atlas)
    missing.pop("BETA")
    assert semantic_workspace_external_errors(data, missing, "ALPHA") == [
        "semantic_workspace_external_owner_unavailable"]
    ambiguous = deepcopy(atlas)
    ambiguous["MIRROR"] = deepcopy(ambiguous["BETA"])
    assert semantic_workspace_external_errors(data, ambiguous, "ALPHA") == [
        "semantic_workspace_external_owner_ambiguous"]
    malformed = deepcopy(data)
    malformed["checked_source_manifest"].pop("program_workspace_external_files")
    assert semantic_workspace_external_errors(malformed, atlas, "ALPHA") == [
        "semantic_workspace_external_inventory_unavailable"]
    limited_output = tmp_path / "limited.json"
    limited_run = subprocess.run(
        ["node", str(CODE_MAPS_DIR / "tools/engines/ts_diagnostics_collector.cjs"),
         "--config", str(runtime), "--out", str(limited_output), "--semantic", "true",
         "--maxFiles", "1"],
        cwd=CODE_MAPS_DIR, capture_output=True, text=True, timeout=30,
    )
    assert limited_run.returncode == 0, limited_run.stderr
    limited = json.loads(limited_output.read_text(encoding="utf-8"))["projects"]["ALPHA"]
    assert limited["checked_source_manifest"]["program_workspace_external_files_total"] == 1
    assert limited["checked_source_manifest"]["program_workspace_external_files"] == []
    assert semantic_workspace_external_errors(limited, atlas, "ALPHA") == [
        "semantic_workspace_external_inventory_incomplete"]


def test_semantic_cross_project_declaration_uses_unique_owned_auxiliary(tmp_path):
    from tools.core.atlas_typescript_inputs import capture_auxiliary_inputs

    workspace = tmp_path / "workspace"
    alpha, beta = workspace / "alpha", workspace / "beta"
    alpha.mkdir(parents=True)
    beta.mkdir()
    alpha_source = 'import { value } from "../beta/b"; export const result = value;'
    beta_declaration = "export declare const value: number;"
    (alpha / "a.ts").write_text(alpha_source, encoding="utf-8")
    (beta / "b.d.ts").write_text(beta_declaration, encoding="utf-8")
    for root, name in ((alpha, "a.ts"), (beta, "b.d.ts")):
        (root / "tsconfig.json").write_text(json.dumps({
            "files": [name], "compilerOptions": {"noLib": True, "types": []},
        }), encoding="utf-8")
    runtime, output = tmp_path / "runtime.json", tmp_path / "diagnostics.json"
    runtime.write_text(json.dumps({
        "workspace_root": str(workspace), "variations": {"ALPHA": "alpha", "BETA": "beta"},
    }), encoding="utf-8")
    result = subprocess.run(
        ["node", str(CODE_MAPS_DIR / "tools/engines/ts_diagnostics_collector.cjs"),
         "--config", str(runtime), "--out", str(output), "--semantic", "true", "--maxFiles", "8"],
        cwd=CODE_MAPS_DIR, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    data = payload["projects"]["ALPHA"]
    assert data["checked_source_manifest"]["program_workspace_external_files"] == [{
        "file": (beta / "b.d.ts").as_posix(), "declaration": True,
    }]
    atlas = {
        "ALPHA": {"project": {"root": str(alpha)}, "files": {
            "a.ts": {"hash": hashlib.sha256(alpha_source.encode()).hexdigest()},
        }},
        "BETA": {"project": {"root": str(beta)}, "files": {},
                 "typescript_auxiliary_inputs": capture_auxiliary_inputs(
                     beta, [(str(beta / "b.d.ts"), "b.d.ts", "b.d.ts")])},
    }
    assert semantic_workspace_external_errors(data, atlas, "ALPHA") == []
    stale = deepcopy(atlas)
    stale["BETA"]["typescript_auxiliary_inputs"]["files"]["b.d.ts"]["host_text_sha256"] = "0" * 64
    assert semantic_workspace_external_errors(data, stale, "ALPHA") == [
        "semantic_workspace_external_mismatch:BETA:b.d.ts"]


def test_semantic_source_host_read_matches_atlas_content_without_live_target_lookup(tmp_path, monkeypatch):
    payload, atlas = collect(tmp_path, {"a.ts": "export const a: string = 42;"},
                             "--semantic", "true", project_config={"files": ["a.ts"]})
    context = payload["projects"]["MAIN"]["semantic_context"]
    source_reads = [row for row in context["observations"]
                    if row.get("operation") == "readFile" and row.get("path", "").endswith("/a.ts")]
    assert source_reads and all(row["access_status"] == "present" for row in source_reads)
    scoped = {"project_root": context["project_root"], "observations_complete": True,
              "observations": source_reads}
    original = Path.resolve
    def no_live_target(candidate, *args, **kwargs):
        if candidate.is_relative_to(tmp_path / "repo"):
            pytest.fail("semantic read consumer consulted live target")
        return original(candidate, *args, **kwargs)
    monkeypatch.setattr(Path, "resolve", no_live_target)
    assert semantic_workspace_read_errors(scoped, atlas["MAIN"]) == []
    fallback = deepcopy(atlas)
    fallback["MAIN"]["files"]["a.ts"]["hash"] = source_reads[0]["value"]["text_md5"]
    assert semantic_workspace_read_errors(scoped, fallback["MAIN"]) == []
    without_md5 = deepcopy(scoped)
    without_md5["observations"][0]["value"].pop("text_md5")
    assert semantic_workspace_read_errors(without_md5, fallback["MAIN"]) == [
        "workspace_read_content_mismatch:a.ts"]
    atlas["MAIN"]["files"]["a.ts"]["hash"] = "0" * 64
    assert semantic_workspace_read_errors(scoped, atlas["MAIN"]) == [
        "workspace_read_content_mismatch:a.ts"]


def test_semantic_workspace_reads_match_actual_source_and_owned_config_capture(tmp_path):
    from tools.core.atlas_typescript_inputs import capture_auxiliary_inputs

    payload, atlas = collect(tmp_path, {"a.ts": "export const a = 1;"},
                             "--semantic", "true",
                             project_config={"files": ["a.ts"], "compilerOptions": {"noLib": True, "types": []}})
    root = tmp_path / "repo"
    config = root / "tsconfig.json"
    atlas["MAIN"]["typescript_auxiliary_inputs"] = capture_auxiliary_inputs(
        root, [(str(config), config.name, config.name)])
    context = payload["projects"]["MAIN"]["semantic_context"]
    assert semantic_workspace_read_errors(context, atlas["MAIN"]) == []
    atlas["MAIN"]["typescript_auxiliary_inputs"]["files"]["tsconfig.json"]["host_text_sha256"] = "0" * 64
    assert semantic_workspace_read_errors(context, atlas["MAIN"]) == [
        "workspace_read_content_mismatch:tsconfig.json"]


def test_semantic_reported_config_root_requires_actual_atlas_bound_read(tmp_path):
    from tools.core.atlas_typescript_inputs import capture_auxiliary_inputs

    payload, atlas = collect(
        tmp_path, {"a.ts": "export const a = 1;",
                   "base.json": json.dumps({"compilerOptions": {"strict": True}})},
        "--semantic", "true",
        project_config={"extends": "./base.json", "files": ["a.ts"],
                        "compilerOptions": {"noLib": True, "types": []}},
    )
    root = tmp_path / "repo"
    atlas["MAIN"]["typescript_auxiliary_inputs"] = capture_auxiliary_inputs(
        root, [(str(root / name), name, name) for name in ("tsconfig.json", "base.json")])
    data = payload["projects"]["MAIN"]
    policy = json.loads((CODE_MAPS_DIR / "config/analysis_snapshot_lineage_contract.json").read_text(
        encoding="utf-8"))
    assert data["tsconfig"] == policy["artifacts"]["ts_diagnostics"]["root_config_filename"]
    assert semantic_config_root_read_errors(data, atlas["MAIN"]) == []
    assert semantic_workspace_read_errors(data["semantic_context"], atlas["MAIN"]) == []
    assert "typescript:MAIN:semantic_context_not_snapshot_bound" in checked_source_errors(payload, atlas)

    absent = deepcopy(payload)
    absent_rows = absent["projects"]["MAIN"]["semantic_context"]["observations"]
    absent_rows[:] = [row for row in absent_rows if not (
        row.get("stage") == "config" and row.get("operation") == "readFile"
        and row.get("path") == (root / "tsconfig.json").as_posix())]
    assert any(row.get("operation") == "readFile" for row in absent_rows)
    assert semantic_config_root_read_errors(absent["projects"]["MAIN"], atlas["MAIN"]) == [
        "semantic_config_root_read_unbound"]
    assert "typescript:MAIN:semantic_config_root_read_unbound" in checked_source_errors(absent, atlas)

    duplicated = deepcopy(data)
    root_read = next(row for row in duplicated["semantic_context"]["observations"] if (
        row.get("stage") == "config" and row.get("operation") == "readFile"
        and row.get("path") == (root / "tsconfig.json").as_posix()))
    duplicated["semantic_context"]["observations"].append(deepcopy(root_read))
    assert semantic_config_root_read_errors(duplicated, atlas["MAIN"])
    wrong_stage = deepcopy(data)
    root_read = next(row for row in wrong_stage["semantic_context"]["observations"] if (
        row.get("stage") == "config" and row.get("operation") == "readFile"
        and row.get("path") == (root / "tsconfig.json").as_posix()))
    root_read["stage"] = "compiler"
    assert semantic_config_root_read_errors(wrong_stage, atlas["MAIN"])
    wrong_path = deepcopy(data)
    wrong_path["tsconfig"] = "../base.json"
    assert semantic_config_root_read_errors(wrong_path, atlas["MAIN"])
    inherited_as_root = deepcopy(data)
    inherited_as_root["tsconfig"] = "base.json"
    assert any(row.get("stage") == "config" and row.get("operation") == "readFile"
               and row.get("path") == (root / "base.json").as_posix()
               for row in data["semantic_context"]["observations"])
    assert semantic_config_root_read_errors(inherited_as_root, atlas["MAIN"]) == [
        "semantic_config_root_read_unbound"]
    stale = deepcopy(atlas["MAIN"])
    stale["typescript_auxiliary_inputs"]["files"]["tsconfig.json"]["host_text_sha256"] = "0" * 64
    assert semantic_config_root_read_errors(data, stale)
    stale_base = deepcopy(atlas["MAIN"])
    stale_base["typescript_auxiliary_inputs"]["files"]["base.json"]["host_text_sha256"] = "0" * 64
    assert semantic_workspace_read_errors(data["semantic_context"], stale_base) == [
        "workspace_read_content_mismatch:base.json"]
    no_auxiliary = deepcopy(atlas["MAIN"])
    no_auxiliary.pop("typescript_auxiliary_inputs")
    assert semantic_config_root_read_errors(data, no_auxiliary)


def test_semantic_root_config_policy_unavailable_fails_closed(tmp_path, monkeypatch):
    payload, atlas = collect(tmp_path, {"a.ts": "export const a = 1;"}, "--semantic", "true",
                             project_config={"files": ["a.ts"], "compilerOptions": {"noLib": True}})
    monkeypatch.setattr("tools.core.typescript_source_binding.INPUT_POLICY_PATH",
                        tmp_path / "missing-lineage-contract.json")
    assert semantic_config_root_read_errors(payload["projects"]["MAIN"], atlas["MAIN"]) == [
        "semantic_config_root_read_unbound"]


def test_semantic_source_read_does_not_infer_bom_normalization_or_empty_success(tmp_path):
    payload, atlas = collect(tmp_path, {"a.ts": "\ufeffexport const a = 1;"},
                             "--semantic", "true", project_config={"files": ["a.ts"]})
    context = payload["projects"]["MAIN"]["semantic_context"]
    source_read = next(row for row in context["observations"]
                       if row.get("operation") == "readFile" and row.get("path", "").endswith("/a.ts"))
    scoped = {"project_root": context["project_root"], "observations_complete": True,
              "observations": [source_read]}
    assert semantic_workspace_read_errors(scoped, atlas["MAIN"]) == [
        "workspace_read_content_mismatch:a.ts"]
    scoped["observations"] = []
    assert semantic_workspace_read_errors(scoped, atlas["MAIN"]) == [
        "workspace_positive_reads_unavailable"]


@pytest.mark.parametrize("failure", ["missing", "bad_hash", "omitted", "foreign_scope", "denied",
                                           "wrong_path", "missing_digest", "host_failure"])
def test_semantic_source_host_read_never_promotes_unbound_input(tmp_path, failure):
    payload, atlas = collect(tmp_path, {"a.ts": "export const a = 1;"},
                             "--semantic", "true", project_config={"files": ["a.ts"]})
    context = payload["projects"]["MAIN"]["semantic_context"]
    row = next(row for row in context["observations"]
               if row.get("operation") == "readFile" and row.get("path", "").endswith("/a.ts"))
    scoped = {"project_root": context["project_root"], "observations_complete": True,
              "observations": [dict(row)]}
    observed = scoped["observations"][0]
    if failure == "missing":
        atlas["MAIN"]["files"].pop("a.ts")
    elif failure == "bad_hash":
        atlas["MAIN"]["files"]["a.ts"]["hash"] = "bad"
    elif failure == "omitted":
        scoped["observations_complete"] = False
    elif failure == "foreign_scope":
        observed["input_scope"] = "outside"
    elif failure == "denied":
        observed["allowed"] = False
    elif failure == "wrong_path":
        observed["path"] = (tmp_path / "repo" / "../repo/a.ts").as_posix()
    elif failure == "missing_digest":
        observed["value"] = None
    else:
        observed["io_failure"] = {"operation": "readFileSync", "code": "EACCES"}
    assert semantic_workspace_read_errors(scoped, atlas["MAIN"])


def test_semantic_context_records_actual_config_source_and_resolution_reads(tmp_path):
    sources = {
        "base.json": json.dumps({"compilerOptions": {"strict": True}}),
        "a.ts": 'import { value } from "typed-package"; export const a: string = value;',
        "node_modules/typed-package/package.json": json.dumps({"types": "index.d.ts"}),
        "node_modules/typed-package/index.d.ts": "export const value: number;",
    }
    payload, atlas = collect(tmp_path, sources, "--semantic", "true",
                             project_config={"extends": "./base.json", "files": ["a.ts"]})
    data = payload["projects"]["MAIN"]
    context = data["semantic_context"]
    assert context["observations_complete"] is True
    assert context["snapshot_bound"] is False
    assert context["native_project_equivalent"] is False
    assert context["effective_options"]["strict"] is True
    assert len(context["compiler"]["installed_module_sha256"]) == 64
    reads = {row["path"]: row for row in context["observations"] if row["operation"] == "readFile"}
    for name, content in sources.items():
        row = reads[(tmp_path / "repo" / name).as_posix()]
        assert row["value"]["text_sha256"] == hashlib.sha256(content.encode("utf-8")).hexdigest()
    assert any(row["path"].endswith("/tsconfig.json") for row in reads.values())
    assert any(row["path"].endswith("/lib.d.ts") for row in reads.values())
    assert any(row["operation"] == "fileExists" and row["value"] is False for row in context["observations"])
    assert any(row["code"] == "TS2322" for row in data["diagnostics"])
    receipt = write_receipt(tmp_path, payload, atlas)
    assert receipt["status"] == "BLOCKED"
    assert "typescript:MAIN:semantic_context_not_snapshot_bound" in receipt["errors"]
    # A full observation inventory must never impersonate source-snapshot authority.
    assert "export const value" not in json.dumps(context)


@pytest.mark.parametrize("change", ["config", "declaration", "package", "missing_resolution"])
def test_semantic_context_identity_changes_without_root_source_change(tmp_path, change):
    sources = {"a.ts": 'import { value } from "typed-package"; export const a: string = value;',
               "base.json": json.dumps({"compilerOptions": {"strict": True}}),
               "node_modules/typed-package/package.json": json.dumps({"types": "index.d.ts"}),
               "node_modules/typed-package/index.d.ts": "export const value: number;"}
    if change == "missing_resolution":
        sources["a.ts"] = 'import { value } from "./missing"; export const a: string = value;'
    config = {"extends": "./base.json", "files": ["a.ts"]}
    first, _ = collect(tmp_path, sources, "--semantic", "true", project_config=config)
    repeated, _ = collect(tmp_path, sources, "--semantic", "true", project_config=config)
    before = first["projects"]["MAIN"]["semantic_context"]
    assert before["context_sha256"] == repeated["projects"]["MAIN"]["semantic_context"]["context_sha256"]
    if change == "config":
        sources["base.json"] = json.dumps({"compilerOptions": {"strict": False}})
    elif change == "declaration":
        sources["node_modules/typed-package/index.d.ts"] = "export const value: string;"
    elif change == "package":
        sources["node_modules/typed-package/package.json"] = json.dumps({"types": "absent.d.ts"})
    else:
        sources["missing.ts"] = 'export const value = "present";'
    after, _ = collect(tmp_path, sources, "--semantic", "true", project_config=config)
    assert before["context_sha256"] != after["projects"]["MAIN"]["semantic_context"]["context_sha256"]


def test_semantic_context_budget_does_not_truncate_diagnostics_or_claim_binding(tmp_path):
    sources = {"a.ts": "export const a: string = 42;"}
    payload, atlas = collect(tmp_path, sources, "--semantic", "true", "--maxContextEntries", "1")
    context = payload["projects"]["MAIN"]["semantic_context"]
    assert len(context["observations"]) == 1
    assert context["omitted_observations"] > 0
    assert context["observations_complete"] is False
    assert sum(context["access_counts"].values()) == context["observed_calls"]
    assert "context_observation_budget_exceeded" in context["binding_blockers"]
    assert any(row["code"] == "TS2322" for row in payload["projects"]["MAIN"]["diagnostics"])
    assert write_receipt(tmp_path, payload, atlas)["status"] == "BLOCKED"


def test_semantic_self_declared_binding_cannot_authorize_receipt(tmp_path, checked):
    payload, atlas = checked
    data = payload["projects"]["MAIN"]
    data["mode"] = "semantic"
    data["semantic_context"] = {"observations_complete": True, "snapshot_bound": True,
                                 "native_project_equivalent": True}
    assert write_receipt(tmp_path, payload, atlas)["status"] == "BLOCKED"


@pytest.mark.parametrize("launch_directory", ["installation", "target", "foreign"])
def test_semantic_type_discovery_uses_project_not_launch_directory(tmp_path, launch_directory):
    root = tmp_path / "repo"
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    foreign_types = foreign / "node_modules/@types/foreign-env"
    foreign_types.mkdir(parents=True)
    (foreign_types / "index.d.ts").write_text("declare const FOREIGN_VALUE: string;")
    collector_cwd = {"installation": CODE_MAPS_DIR, "target": root, "foreign": foreign}[launch_directory]
    payload, atlas = collect(tmp_path, {
        "a.ts": "export const value: number = PROJECT_VALUE; export const missing = FOREIGN_VALUE;",
        "node_modules/@types/project-env/index.d.ts": "declare const PROJECT_VALUE: number;",
    }, "--semantic", "true", collector_cwd=collector_cwd,
       project_config={"compilerOptions": {"noLib": True}, "files": ["a.ts"]})
    data = payload["projects"]["MAIN"]
    assert not any(row["code"] == "TS2304" and "PROJECT_VALUE" in row["message"]
                   for row in data["diagnostics"]), data["diagnostics"]
    assert any(row["file"] == "node_modules/@types/project-env/index.d.ts"
               for row in data["checked_source_manifest"]["files"])
    assert any(row["code"] == "TS2304" and "FOREIGN_VALUE" in row["message"]
               for row in data["diagnostics"])
    context = data["semantic_context"]
    assert Path(context["current_directory"]) == root
    assert Path(context["process_current_directory"]) == collector_cwd
    assert Path(context["effective_options"]["configFilePath"]) == root / "tsconfig.json"
    assert context["native_project_equivalent"] is False
    assert write_receipt(tmp_path, payload, atlas)["status"] == "BLOCKED"


@pytest.mark.parametrize("semantic", [False, True])
@pytest.mark.parametrize("launch_directory", ["installation", "foreign"])
def test_inherited_glob_roots_do_not_depend_on_launch_directory(tmp_path, semantic, launch_directory):
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    preload = tmp_path / "no-chdir.cjs"
    preload.write_text('process.chdir = () => { throw new Error("global cwd mutation forbidden"); };')
    payload, _ = collect(tmp_path, {
        "config/base.json": json.dumps({"include": ["../src/**/*.ts"],
                                        "exclude": ["../src/excluded/**"],
                                        "compilerOptions": {"noLib": True, "types": []}}),
        "src/a.ts": "export const value = 1;",
        "src/nested/b.ts": "export const value = 2;",
        "src/excluded/skip.ts": "export const value = ;",
        "outside/not-selected.ts": "export const value = ;",
    }, "--semantic", str(semantic).lower(),
       project_config={"extends": "./config/base.json"}, node_preload=preload,
       collector_cwd=CODE_MAPS_DIR if launch_directory == "installation" else foreign)
    data = payload["projects"]["MAIN"]
    assert {row["file"] for row in data["checked_source_manifest"]["files"]} == {"src/a.ts", "src/nested/b.ts"}
    assert data["summary"]["root_files_total"] == 2
    assert not any(row["file"].endswith("skip.ts") for row in data["diagnostics"])
    if semantic:
        assert data["semantic_context"]["native_project_equivalent"] is False
        assert data["semantic_context"]["snapshot_bound"] is False
    else:
        assert data["checked_source_manifest"]["complete"] is True


def test_multi_project_type_context_is_isolated_without_process_chdir(tmp_path):
    root = tmp_path / "workspace"
    for project, kind in [("alpha", "number"), ("beta", "string")]:
        directory = root / project
        library = directory / "node_modules/@types/local-env"
        library.mkdir(parents=True)
        (library / "index.d.ts").write_text(f"declare const PROJECT_VALUE: {kind};")
        (directory / "a.ts").write_text(f"export const value: {kind} = PROJECT_VALUE;")
        (directory / "tsconfig.json").write_text(json.dumps({
            "files": ["a.ts"], "compilerOptions": {"noLib": True}}))
    config, output = tmp_path / "runtime.json", tmp_path / "diagnostics.json"
    config.write_text(json.dumps({"workspace_root": str(root), "variations": {"A": "alpha", "B": "beta"}}))
    preload = tmp_path / "no-chdir.cjs"
    preload.write_text('process.chdir = () => { throw new Error("global cwd mutation forbidden"); };')
    result = subprocess.run(["node", "--require", str(preload),
                             str(CODE_MAPS_DIR / "tools/engines/ts_diagnostics_collector.cjs"),
                             "--config", str(config), "--out", str(output), "--semantic", "true"],
                            cwd=CODE_MAPS_DIR, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    payload = json.loads(output.read_text())
    for project, relative in [("A", "alpha"), ("B", "beta")]:
        data = payload["projects"][project]
        assert not any(row["code"] in {"TS2304", "TS2322"} for row in data["diagnostics"])
        assert Path(data["semantic_context"]["current_directory"]) == root / relative
        assert Path(data["semantic_context"]["process_current_directory"]) == CODE_MAPS_DIR
        assert {row["file"] for row in data["checked_source_manifest"]["files"]} == {
            "a.ts", "node_modules/@types/local-env/index.d.ts"}
        assert data["semantic_context"]["snapshot_bound"] is False


def test_semantic_profile_differences_and_project_references_are_visible(tmp_path):
    payload, atlas = collect(tmp_path, {"a.ts": "export const a = 1;"}, "--semantic", "true",
        project_config={"files": ["a.ts"], "references": [{"path": "./other"}],
                        "compilerOptions": {"paths": {"@/*": ["src/*"]}, "skipLibCheck": False}})
    context = payload["projects"]["MAIN"]["semantic_context"]
    assert {"paths", "skipLibCheck"} <= set(context["overridden_options"])
    assert context["project_references"] == 1
    assert "project_references_not_loaded" in context["binding_blockers"]
    assert context["native_project_equivalent"] is False
    errors = checked_source_errors(payload, atlas)
    assert "typescript:MAIN:semantic_compiler_profile_overridden" in errors
    assert "typescript:MAIN:semantic_project_references_unloaded" in errors
    assert "typescript:MAIN:semantic_context_not_snapshot_bound" in errors
    altered = deepcopy(payload)
    altered["projects"]["MAIN"]["semantic_context"]["project_references_sha256"] = "0" * 64
    altered_errors = checked_source_errors(altered, atlas)
    assert "typescript:MAIN:semantic_project_references_unloaded" in altered_errors
    assert "typescript:MAIN:semantic_project_references_context_mismatch" not in altered_errors


def test_semantic_selected_roots_context_must_match_checked_manifest(tmp_path):
    payload, atlas = collect(tmp_path, {"a.ts": "export const a = 1;", "b.ts": "export const b = 2;"},
        "--semantic", "true",
        project_config={"files": ["b.ts", "a.ts"], "compilerOptions": {"noLib": True, "types": []}})
    assert semantic_diagnostic_source_errors(payload["projects"]["MAIN"], atlas["MAIN"]) == []
    context = payload["projects"]["MAIN"]["semantic_context"]
    assert context["configured_roots"] == context["selected_roots"] == 2
    assert context["configured_roots_sha256"] == context["selected_roots_sha256"]
    reordered = deepcopy(payload)
    reordered["projects"]["MAIN"]["checked_source_manifest"]["selected_root_files"].reverse()
    assert "typescript:MAIN:semantic_selected_roots_context_mismatch" in checked_source_errors(reordered, atlas)
    for field, value in [
        ("selected_roots", 0),
        ("selected_roots_sha256", "0" * 64),
        ("configured_roots", 0),
    ]:
        changed = deepcopy(payload)
        changed["projects"]["MAIN"]["semantic_context"][field] = value
        errors = checked_source_errors(changed, atlas)
        assert "typescript:MAIN:semantic_selected_roots_context_mismatch" in errors
        assert "typescript:MAIN:semantic_context_not_snapshot_bound" in errors

    missing = deepcopy(payload)
    missing["projects"]["MAIN"]["semantic_context"].pop("selected_roots_sha256")
    assert "typescript:MAIN:semantic_selected_roots_context_unavailable" in checked_source_errors(missing, atlas)
    missing_configured = deepcopy(payload)
    missing_configured["projects"]["MAIN"]["semantic_context"].pop("configured_roots_sha256")
    assert "typescript:MAIN:semantic_selected_roots_context_unavailable" in checked_source_errors(
        missing_configured, atlas)
    contradictory = deepcopy(payload)
    contradictory["projects"]["MAIN"]["semantic_context"]["configured_roots_sha256"] = "0" * 64
    assert "typescript:MAIN:semantic_configured_roots_context_mismatch" in checked_source_errors(
        contradictory, atlas)


def test_partial_root_selection_does_not_infer_configured_list_digest(tmp_path):
    payload, atlas = collect(tmp_path, {"a.ts": "export const a = 1;", "b.ts": "export const b = 2;"},
        "--semantic", "true", "--maxFiles", "1",
        project_config={"files": ["a.ts", "b.ts"], "compilerOptions": {"noLib": True, "types": []}})
    context = payload["projects"]["MAIN"]["semantic_context"]
    assert context["configured_roots"] == 2
    assert context["selected_roots"] == 1
    altered = deepcopy(payload)
    altered["projects"]["MAIN"]["semantic_context"]["configured_roots_sha256"] = "0" * 64
    errors = checked_source_errors(altered, atlas)
    assert "typescript:MAIN:semantic_configured_roots_context_mismatch" not in errors
    assert "typescript:MAIN:semantic_context_not_snapshot_bound" in errors


def test_semantic_profile_missing_or_malformed_observation_is_not_clean_profile(tmp_path):
    payload, atlas = collect(tmp_path, {"a.ts": "export const a = 1;"}, "--semantic", "true",
                             project_config={"files": ["a.ts"]})
    baseline = checked_source_errors(payload, atlas)
    assert "typescript:MAIN:semantic_context_not_snapshot_bound" in baseline
    assert "typescript:MAIN:semantic_compiler_profile_overridden" in baseline
    assert "typescript:MAIN:semantic_compiler_profile_unavailable" not in baseline
    assert "typescript:MAIN:semantic_project_references_unavailable" not in baseline
    assert "typescript:MAIN:semantic_project_references_context_mismatch" not in baseline
    assert payload["projects"]["MAIN"]["semantic_context"]["project_references_sha256"] == hashlib.sha256(
        b"[]").hexdigest()
    for field, expected in [
        ("overridden_options", "semantic_compiler_profile_unavailable"),
        ("requested_options_sha256", "semantic_compiler_profile_unavailable"),
        ("effective_options", "semantic_compiler_profile_unavailable"),
        ("project_references", "semantic_project_references_unavailable"),
        ("project_references_sha256", "semantic_project_references_unavailable"),
    ]:
        changed = deepcopy(payload)
        changed["projects"]["MAIN"]["semantic_context"].pop(field)
        errors = checked_source_errors(changed, atlas)
        assert f"typescript:MAIN:{expected}" in errors
        assert "typescript:MAIN:semantic_context_not_snapshot_bound" in errors
    for field, value, expected in [
        ("overridden_options", ["paths", "paths"], "semantic_compiler_profile_unavailable"),
        ("requested_options_sha256", True, "semantic_compiler_profile_unavailable"),
        ("effective_options", [], "semantic_compiler_profile_unavailable"),
        ("project_references", True, "semantic_project_references_unavailable"),
        ("project_references_sha256", "invalid", "semantic_project_references_unavailable"),
    ]:
        changed = deepcopy(payload)
        changed["projects"]["MAIN"]["semantic_context"][field] = value
        assert f"typescript:MAIN:{expected}" in checked_source_errors(changed, atlas)
    contradictory_empty = deepcopy(payload)
    contradictory_empty["projects"]["MAIN"]["semantic_context"]["project_references_sha256"] = "0" * 64
    assert "typescript:MAIN:semantic_project_references_context_mismatch" in checked_source_errors(
        contradictory_empty, atlas)


def test_native_alias_resolution_cannot_be_scored_from_bounded_semantic_profile(tmp_path):
    payload, atlas = collect(
        tmp_path,
        {"a.ts": 'import { value } from "@alias/value"; export const result: number = value;',
         "src/value.ts": "export const value = 1;"},
        "--semantic", "true",
        project_config={"files": ["a.ts"], "compilerOptions": {
            "baseUrl": ".", "paths": {"@alias/*": ["src/*"]}, "types": []}},
    )
    root = tmp_path / "repo"
    native = subprocess.run(
        ["node", str(CODE_MAPS_DIR / "tools/engines/node_modules/typescript/bin/tsc"),
         "--noEmit", "--project", str(root / "tsconfig.json")],
        cwd=root, capture_output=True, text=True, timeout=30,
    )
    assert native.returncode == 0, native.stdout + native.stderr
    semantic = payload["projects"]["MAIN"]
    assert any(row["code"] == "TS2307" for row in semantic["diagnostics"])
    assert {"baseUrl", "paths"} <= set(semantic["semantic_context"]["overridden_options"])
    assert "typescript:MAIN:semantic_compiler_profile_overridden" in checked_source_errors(payload, atlas)
    assert write_receipt(tmp_path, payload, atlas)["status"] == "BLOCKED"


def test_invalid_semantic_context_budget_fails_before_output(tmp_path):
    output = tmp_path / "absent.json"
    result = subprocess.run(
        ["node", str(CODE_MAPS_DIR / "tools/engines/ts_diagnostics_collector.cjs"),
         "--out", str(output), "--semantic", "true", "--maxContextEntries", "-1"],
        cwd=CODE_MAPS_DIR, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode != 0
    assert "semantic context observation budget" in result.stderr
    assert not output.exists()


def test_semantic_config_errors_are_not_dropped_from_diagnostics(tmp_path):
    payload, atlas = collect(tmp_path, {"a.ts": "export const a = 1;"}, "--semantic", "true",
        project_config={"files": ["a.ts"], "compilerOptions": {"target": "invalid-target"}})
    assert any(row["code"] == "TS6046" for row in payload["projects"]["MAIN"]["diagnostics"])
    assert write_receipt(tmp_path, payload, atlas)["status"] == "BLOCKED"


def test_missing_module_and_denied_external_path_are_distinct_observations(tmp_path):
    (tmp_path / "outside.ts").write_text("export const x = 1;")
    payload, atlas = collect(tmp_path, {"a.ts": 'import "./missing"; import "../outside"; export {};'},
                             "--semantic", "true", project_config={"files": ["a.ts"]})
    context = payload["projects"]["MAIN"]["semantic_context"]
    probes = [row for row in context["observations"] if row["operation"] == "fileExists"]
    missing = next(row for row in probes if row["path"] == (tmp_path / "repo/missing.ts").as_posix())
    denied = next(row for row in context["observations"]
                  if row["operation"] == "directoryExists" and row["path"] == tmp_path.as_posix())
    assert missing["allowed"] is True
    assert missing["access_status"] == "missing"
    assert missing["input_scope"] == "workspace"
    assert missing["value"] is False
    assert denied["allowed"] is False
    assert denied["access_status"] == "outside_boundary"
    assert denied["value"] is False
    assert write_receipt(tmp_path, payload, atlas)["status"] == "BLOCKED"


def test_library_reads_are_toolchain_observations_not_project_inputs(tmp_path):
    payload, atlas = collect(tmp_path, {"a.ts": "export const x = 1;"}, "--semantic", "true")
    context = payload["projects"]["MAIN"]["semantic_context"]
    library = next(row for row in context["observations"]
                   if row["operation"] == "readFile" and row["path"].endswith("/lib.d.ts"))
    assert library["input_scope"] == "compiler_library"
    assert library["access_status"] == "present"
    assert Path(library["path"]).is_relative_to(Path(context["compiler"]["library_root"]))
    assert context["compiler"]["authority"] == "collector_toolchain_observation"
    assert context["snapshot_bound"] is False
    receipt = write_receipt(tmp_path, payload, atlas)
    assert receipt["status"] == "BLOCKED"
    assert "typescript:MAIN:compiler_library_not_snapshot_bound" in receipt["errors"]
    assert "typescript:MAIN:auxiliary_input_outside_project" not in receipt["errors"]


@pytest.mark.parametrize("failure", ["realpath", "read"])
def test_host_io_failure_is_unavailable_not_missing(tmp_path, failure):
    preload = tmp_path / "failure.cjs"
    if failure == "realpath":
        preload.write_text("""
const fs = require("fs");
const original = fs.realpathSync.native;
fs.realpathSync.native = (candidate, ...args) => {
  if (String(candidate).endsWith("blocked.ts")) {
    const error = new Error("fixture permission denied"); error.code = "EACCES"; throw error;
  }
  return original(candidate, ...args);
};
""")
    else:
        preload.write_text("""
const ts = require(require.resolve("typescript", {paths: [require("path").join(process.cwd(), "tools", "engines")]}));
const original = ts.sys.readFile;
ts.sys.readFile = (candidate, ...args) => String(candidate).endsWith("blocked.ts")
  ? undefined : original(candidate, ...args);
""")
    payload, atlas = collect(tmp_path, {
        "a.ts": 'import { x } from "./blocked"; export const y = x;',
        "blocked.ts": "export const x = 1;",
    }, "--semantic", "true", project_config={"files": ["a.ts"]}, node_preload=preload)
    context = payload["projects"]["MAIN"]["semantic_context"]
    rows = [row for row in context["observations"] if row["path"].endswith("/blocked.ts")]
    assert any(row["access_status"] == "unavailable" for row in rows)
    assert not any(row["access_status"] == "missing" for row in rows)
    assert "host_access_unavailable" in context["binding_blockers"]
    assert "typescript:MAIN:semantic_host_access_unavailable" in write_receipt(tmp_path, payload, atlas)["errors"]


@pytest.mark.parametrize("operation,fs_operation,error_code", [
    (operation, fs_operation, error_code)
    for operation, fs_operation in [
        ("fileExists", "statSync"), ("directoryExists", "statSync"),
        ("readFile", "readFileSync"), ("getDirectories", "readdirSync"),
        ("readDirectory", "readdirSync"), ("realpath", "realpathSync.native"),
    ]
    for error_code in [None, "EACCES", "EIO", "ENOENT", "UNKNOWN", "STAT_MISSING"]
    if error_code != "STAT_MISSING" or fs_operation == "statSync"
])
def test_swallowed_host_failures_preserve_actual_result_and_error(tmp_path, operation, fs_operation, error_code):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.ts").write_text("export const value = 1;")
    (root / "sub").mkdir()
    candidate = root / "a.ts" if operation in {"fileExists", "readFile"} else root
    collector = CODE_MAPS_DIR / "tools/engines/ts_diagnostics_collector.cjs"
    # Exercise private adapters with the installed TS host. Strip only the CLI
    # invocation; no fake TypeScript filesystem semantics or extra production API.
    script = r"""
const fs = require("fs"), path = require("path"), vm = require("vm");
const {createRequire} = require("module");
const [collector, root, candidate, operation, fsOperation, errorCode] = process.argv.slice(1);
const source = fs.readFileSync(collector, "utf8").replace(/\r\n/g, "\n");
const boundary = source.lastIndexOf("\ntry {\n  main();");
if (boundary < 0) throw new Error("collector entrypoint changed");
let armed = false, calls = 0;
const owner = fsOperation === "realpathSync.native" ? fs.realpathSync : fs;
const key = fsOperation === "realpathSync.native" ? "native" : fsOperation;
const original = owner[key];
const injected = function(...args) {
  if (armed) {
    calls += 1;
    if (errorCode === "STAT_MISSING") return undefined;
    if (errorCode !== "null") {
      const error = new Error("fixture I/O failure, not a diagnostic message");
      if (errorCode !== "UNKNOWN") error.code = errorCode;
      throw error;
    }
  }
  return original.apply(this, args);
};
owner[key] = injected;
const context = {require: createRequire(collector), console, process,
  root, candidate, operation, owner, key, injected, setArmed: value => {armed = value;},
  getCalls: () => calls, __dirname: path.dirname(collector)};
vm.runInNewContext(source.slice(0, boundary) + `
fileSystemObservation = observeFileSystemFailures();
ts = require("typescript");
const recorder = semanticContextRecorder(20);
const raw = {...ts.sys};
const originalHost = raw[operation];
raw[operation] = (...args) => {
  setArmed(true);
  try { return originalHost(...args); } finally { setArmed(false); }
};
const host = observeHost(raw, "config", recorder,
  boundedPathAccess([{root, scope: "workspace"}]), root);
const args = operation === "readDirectory" ? [[".ts"], undefined, ["**/*"]] : [];
let value;
let scopeRestored;
try {
  value = host[operation](candidate, ...args);
  try { fileSystemObservation.run(() => { throw new Error("scope exit fixture"); }); } catch {}
  const afterThrow = fileSystemObservation.run(() => true);
  scopeRestored = afterThrow.count === 0 && afterThrow.failure === null;
}
finally { fileSystemObservation.restore(); }
const observedCalls = getCalls();
const restored = owner[key] === injected;
console.log(JSON.stringify({value: value === undefined ? null : value, scopeRestored,
  context: recorder.finish({binding_blockers: []}), observedCalls, restored}));
`, context);
"""
    result = subprocess.run(["node", "-e", script, str(collector), str(root), str(candidate),
                             operation, fs_operation, error_code or "null"],
                            cwd=CODE_MAPS_DIR, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["restored"] is True
    assert output["scopeRestored"] is True
    assert output["observedCalls"] >= 1
    context = output["context"]
    row, = context["observations"]
    assert row["allowed"] is True  # boundary accepted; the later host call failed
    if error_code is None:
        assert row["access_status"] == "present"
        assert row["error_code"] is None
        assert "io_failure" not in row
        assert "host_access_unavailable" not in context["binding_blockers"]
    else:
        assert row["access_status"] == "unavailable"
        code = {"UNKNOWN": "HOST_IO_UNKNOWN", "STAT_MISSING": "ENOENT"}.get(error_code, error_code)
        assert row["error_code"] == code
        assert row["io_failure"]["code"] == code
        assert row["io_failure"]["count"] == output["observedCalls"]
        assert "host_access_unavailable" in context["binding_blockers"]
        # Retain TS's actual fallback. An empty result cannot conceal the error.
        expected = None if operation == "readFile" else str(candidate)
        if operation in {"fileExists", "directoryExists"}:
            expected = False
        elif operation in {"getDirectories", "readDirectory"}:
            expected = []
        assert output["value"] == expected


def test_in_boundary_names_starting_with_dots_are_not_parent_escapes(tmp_path):
    payload, _ = collect(tmp_path, {
        "a.ts": 'import { x } from "./..local"; export const y: string = x;',
        "..local.ts": "export const x = 1;",
    }, "--semantic", "true", project_config={"files": ["a.ts"]})
    context = payload["projects"]["MAIN"]["semantic_context"]
    assert any(row["path"].endswith("/..local.ts") and row["operation"] == "readFile"
               and row["allowed"] is True for row in context["observations"])
    assert any(row["code"] == "TS2322" for row in payload["projects"]["MAIN"]["diagnostics"])


def test_library_identity_hashes_the_text_actually_read_without_reopening(tmp_path):
    payload, _ = collect(tmp_path, {"a.ts": "export const x = 1;"}, "--semantic", "true")
    before = payload["projects"]["MAIN"]["semantic_context"]
    preload = tmp_path / "library.cjs"
    preload.write_text("""
const ts = require(require.resolve("typescript", {paths: [require("path").join(process.cwd(), "tools", "engines")]}));
const original = ts.sys.readFile;
ts.sys.readFile = (candidate, ...args) => {
  const text = original(candidate, ...args);
  return String(candidate).endsWith("lib.d.ts") && typeof text === "string"
    ? text + "\\n// changed host text" : text;
};
""")
    payload, _ = collect(tmp_path, {"a.ts": "export const x = 1;"}, "--semantic", "true", node_preload=preload)
    after = payload["projects"]["MAIN"]["semantic_context"]
    library = next(row for row in after["observations"]
                   if row["operation"] == "readFile" and row["path"].endswith("/lib.d.ts"))
    returned = Path(library["path"]).read_bytes().decode("utf-8-sig") + "\n// changed host text"
    assert library["value"]["text_sha256"] == hashlib.sha256(returned.encode()).hexdigest()
    assert before["compiler"]["installed_module_sha256"] == after["compiler"]["installed_module_sha256"]
    assert before["context_sha256"] != after["context_sha256"]


@pytest.mark.parametrize("link_kind", ["internal", "external", "dangling"])
def test_resolution_does_not_follow_external_or_dangling_links(tmp_path, link_kind):
    root = tmp_path / "repo"
    root.mkdir()
    outside = root / "internal" if link_kind == "internal" else tmp_path / "outside"
    outside.mkdir()
    (outside / "value.ts").write_text("export const x = 1;")
    target = tmp_path / "nonexistent" if link_kind == "dangling" else outside
    try:
        (root / "link").symlink_to(target, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"Host cannot create symlink fixture: {error}")
    payload, atlas = collect(tmp_path, {"a.ts": 'import { x } from "./link/value"; export const y: string = x;'},
                             "--semantic", "true", project_config={"files": ["a.ts"]})
    context = payload["projects"]["MAIN"]["semantic_context"]
    rows = [row for row in context["observations"] if "/link" in row["path"]]
    assert rows
    if link_kind == "internal":
        assert any(row["allowed"] is True and row["operation"] == "readFile" for row in rows)
        assert any(row["code"] == "TS2322" for row in payload["projects"]["MAIN"]["diagnostics"])
    else:
        assert all(row["allowed"] is False for row in rows)
        assert not any(row["access_status"] == "missing" for row in rows)
    assert write_receipt(tmp_path, payload, atlas)["status"] == "BLOCKED"


def test_bound_syntax_and_absent_project_do_not_become_zero_type_errors(tmp_path, checked):
    payload, _ = checked
    assert _ts_diagnostics_by_project(payload) == {}
    result = simulate_dependency_package({"source": "MAIN", "closure_files": []},
                                         {"MAIN": tmp_path}, main_root=tmp_path, ts_totals={})
    component = result["confidence_components"]["typescript_diagnostics"]
    assert component["status"] == "unavailable"
    assert component["score"] is None


@pytest.mark.parametrize("case", ["syntax", "stale_syntax", "semantic", "semantic_stale_source",
                                  "semantic_self_claim"])
def test_frontier_consumes_only_source_bound_collector_findings(tmp_path, monkeypatch, case):
    from tools.engines import react_frontier_intelligence as frontier

    semantic = case.startswith("semantic")
    source = "export const value: string = 42;" if semantic else "\ufeffexport const value = ;\r\n"
    payload, atlas = collect(tmp_path, {"a.ts": source},
        "--semantic", str(semantic).lower(),
        project_config={"files": ["a.ts"], "compilerOptions": {"noLib": True, "types": []}})
    if case in {"stale_syntax", "semantic_stale_source"}:
        atlas["MAIN"]["files"]["a.ts"]["hash"] = "0" * 64
    if semantic:
        assert any(row["code"] == "TS2322" for row in payload["projects"]["MAIN"]["diagnostics"])
    if case == "semantic_self_claim":
        payload["projects"]["MAIN"]["semantic_context"].update(
            snapshot_bound=True, native_project_equivalent=True)
    (tmp_path / "atlas_commit.json").write_text(json.dumps(build_atlas_commit(atlas)), encoding="utf-8")
    monkeypatch.setattr(frontier, "RAW_DIR", tmp_path)
    monkeypatch.setattr(frontier, "REPORTS_DIR", tmp_path)
    monkeypatch.setattr(frontier, "load_atlas_data", lambda: atlas)
    monkeypatch.setattr(frontier, "project_runtime_atlas", lambda value: (value, {"analyzed_projects": ["MAIN"]}))
    monkeypatch.setattr(frontier, "_read_project_file", lambda *a: "")
    monkeypatch.setattr(frontier, "analyze_frontier_file", lambda *a: None)
    monkeypatch.setattr(frontier, "_collect_ts_diagnostics", lambda **k: payload)
    monkeypatch.setattr(frontier, "_bundle_evidence", lambda *a: ([], []))
    monkeypatch.setattr(frontier, "_profiler_evidence", lambda *a: ([], []))
    result = frontier.run_react_frontier_intelligence()
    evidence = result["evidence_imports"]["typescript_diagnostics"]
    bound_syntax = case == "syntax"
    assert evidence["snapshot_binding"] == ("COMPLETE" if bound_syntax else "BLOCKED")
    assert bool(result["findings"]) is bound_syntax
    assert bool(evidence["binding_errors"]) is not bound_syntax
    if semantic:
        assert "typescript:MAIN:semantic_context_not_snapshot_bound" in evidence["binding_errors"]
    if case == "semantic_stale_source":
        assert "typescript:MAIN:workspace_read_content_mismatch:a.ts" in evidence["binding_errors"]
    # Use the receipt actually written by Frontier, not a fixture-issued COMPLETE.
    _, usable = evaluate_snapshot_bound_inputs(
        contract_path=CODE_MAPS_DIR / "config/merge_simulation_input_contract.json",
        raw_dir=tmp_path, expected_snapshot_id=build_atlas_commit(atlas)["snapshot_id"],
        payloads={"ts_diagnostics": payload},
    )
    assert ("ts_diagnostics" in usable) is bound_syntax
    totals = _ts_diagnostics_by_project(usable.get("ts_diagnostics", {}))
    merged = simulate_dependency_package({"source": "MAIN", "files": []},
        {"MAIN": tmp_path / "repo"}, main_root=tmp_path, ts_totals=totals)
    component = merged["confidence_components"]["typescript_diagnostics"]
    assert component["status"] == "unavailable"
    assert component["score"] is None
    assert "bind_typescript_diagnostics_to_source_snapshot" in merged["required_actions"]
