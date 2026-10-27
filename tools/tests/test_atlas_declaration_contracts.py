"""Finite declaration syntax and owned Atlas metadata integration, not runtime proof."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from tools.core import atlas_typescript_inputs as inputs
from tools.core.atlas_integrity import build_atlas_commit
from tools.tests.test_atlas_materialization_profile import _sqlite_store


def walk_entries(root, names):
    return [(str(root / name), Path(name).name, name) for name in names]


def declaration_batch(paths):
    root = Path(__file__).resolve().parents[2]
    command = ["node", str(root / "tools/engines/ast_sequencer.cjs"), "--batch-json", *map(str, paths)]
    process = subprocess.run(command, cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert process.returncode == 0, process.stderr
    return json.loads(process.stdout)


@pytest.fixture(scope="module")
def native_declaration_capture(tmp_path_factory):
    root = tmp_path_factory.mktemp("declaration-headers")
    content = ("\ufeff// ç 🌍\r\nexport interface Contract { value: string }\r\n"
        "export type Alias = Contract;\r\ndeclare const value: number;\r\n"
        "export declare function run(): void;\r\nexport declare class Client {}\r\n"
        "export { Contract as PublicContract } from './types';\r\n"
        "declare global { interface Window { extra: string } }\r\n")
    names = ["index.d.ts", "index.d.mts", "index.d.cts"]
    for name in names:
        (root / name).write_bytes(content.encode("utf-8"))
    capture = inputs.capture_auxiliary_inputs(root, walk_entries(root, names))
    paths = inputs.declaration_input_paths(root, capture)
    return root, capture, declaration_batch(paths), content.encode("utf-8")


@pytest.mark.parametrize("suffix", [".d.ts", ".d.mts", ".d.cts"])
def test_native_declaration_headers_preserve_raw_identity_and_byte_spans(native_declaration_capture, suffix):
    root, capture, responses, data = native_declaration_capture
    view = inputs.build_declaration_contract_evidence(root, capture, responses)
    assert view["status"] == "partial"  # Nested ambient members were not traversed.
    row = view["files"]["index" + suffix]
    assert row["parser_input_sha256"] == hashlib.sha256(data).hexdigest()
    assert row["parser_input_sha256"] != capture["files"]["index" + suffix]["host_text_sha256"]
    assert row["parser_kind"] == "typescript_compiler_api"
    assert row["offset_unit"] == "utf8_bytes" and row["coverage"] == "finite_top_level_headers_only"
    assert row["record_count"] == 7 and row["uninspected_module_bodies"] == 1
    assert row["omitted_statements"] == row["omitted_name_nodes"] == 0
    records = row["records"]
    assert [r["syntax_kind"] for r in records] == ["InterfaceDeclaration", "TypeAliasDeclaration", "VariableStatement",
        "FunctionDeclaration", "ClassDeclaration", "ExportDeclaration", "ModuleDeclaration"]
    assert records[0]["names"] == ["Contract"]
    assert records[0]["start_byte"] == data.index(b"export interface")
    assert data[records[0]["start_byte"]:records[0]["end_byte"]] == b"export interface Contract { value: string }"
    assert records[5]["module_specifier"] == "./types" and records[5]["names"] == ["PublicContract"]
    assert records[-1]["global_augmentation"] is True
    assert row["runtime_execution"] == "not_established"
    assert not any(key in row for key in ("symbols", "dependencies", "loc", "content"))


@pytest.mark.parametrize("mutation", ["hash", "size", "policy", "version", "record_count", "kind", "span", "bool_span",
    "overlap", "name", "surrogate", "scope", "runtime", "observed_omissions", "missing", "extra_record_field", "carrier"])
def test_declaration_header_admission_rejects_malformed_or_foreign_native_evidence(native_declaration_capture, mutation):
    root, capture, original, _ = native_declaration_capture
    responses = deepcopy(original)
    key = (root / "index.d.ts").as_posix()
    row = responses[key][0]["declarationContractEvidence"]
    if mutation == "hash": row["parser_input_sha256"] = "a" * 64
    elif mutation == "size": row["size_bytes"] += 1
    elif mutation == "policy": row["policy_sha256"] = "b" * 64
    elif mutation == "version": row["version"] = "foreign"
    elif mutation == "record_count": row["record_count"] += 1
    elif mutation == "kind": row["records"][0]["syntax_kind"] = "RuntimeImplementation"
    elif mutation == "span": row["records"][0]["end_byte"] = 10000000
    elif mutation == "bool_span": row["records"][0]["start_byte"] = True
    elif mutation == "overlap": row["records"].append(deepcopy(row["records"][0])); row["record_count"] += 1
    elif mutation == "name": row["records"][0]["names"] = [0]
    elif mutation == "surrogate": row["records"][0]["names"] = ["\ud800"]
    elif mutation == "scope": row["coverage"] = "complete_type_system"
    elif mutation == "runtime": row["runtime_execution"] = "confirmed"
    elif mutation == "observed_omissions": row["status"] = "observed"
    elif mutation == "missing": responses.pop(key)
    elif mutation == "extra_record_field": row["records"][0]["runtime_symbol"] = "not allowed"
    elif mutation == "carrier": responses = []
    view = inputs.build_declaration_contract_evidence(root, capture, responses)
    rejected = view["files"]["index.d.ts"]
    assert rejected["status"] == "unavailable" and "records" not in rejected
    assert view["files"]["index.d.mts"]["status"] == ("unavailable" if mutation == "carrier" else "partial")


@pytest.mark.parametrize("mutation", ["missing", "surgical", "foreign_root", "noncanonical", "unreadable", "escape", "wrong_scope"])
def test_declaration_selection_uses_only_owned_canonical_capture(native_declaration_capture, mutation):
    root, original, responses, _ = native_declaration_capture
    capture = deepcopy(original)
    if mutation == "missing": capture = {}
    elif mutation == "surgical": capture["status"] = "unavailable_surgical_scan"
    elif mutation == "foreign_root": capture["project_root"] = (root / "foreign").as_posix()
    elif mutation == "wrong_scope": capture["scope"] = "any_live_files"
    elif mutation == "noncanonical": capture["files"]["index.d.ts"]["canonical_file_path"] = False
    elif mutation == "unreadable": capture["files"]["index.d.ts"]["status"] = "unreadable"
    elif mutation == "escape": capture["files"]["../outside.d.ts"] = capture["files"].pop("index.d.ts")
    paths = inputs.declaration_input_paths(root, capture)
    assert (root / "index.d.ts").as_posix() not in paths
    assert not any("outside" in path for path in paths)
    view = inputs.build_declaration_contract_evidence(root, capture, responses)
    if mutation in {"missing", "surgical", "foreign_root", "wrong_scope"}:
        assert view["status"] == "unavailable" and view["files"] == {}
    else:
        assert view["status"] == "partial"


def test_declaration_budgets_keep_omissions_and_unknown_capture_totals(native_declaration_capture, monkeypatch):
    root, capture, responses, _ = native_declaration_capture
    policy, digest = inputs.declaration_contract_policy()
    small = {**policy, "max_files": 1}
    monkeypatch.setattr(inputs, "declaration_contract_policy", lambda: (small, digest))
    assert len(inputs.declaration_input_paths(root, capture)) == 1
    view = inputs.build_declaration_contract_evidence(root, capture, responses)
    assert view["selected_files"] == 1 and view["omitted_files"] == 2 and view["status"] == "partial"
    partial = {**capture, "omitted_files": 1, "status": "partial"}
    assert inputs.build_declaration_contract_evidence(root, partial, responses)["omitted_files"] is None
    small["max_captured_bytes"] = 1
    assert inputs.declaration_input_paths(root, capture) == []
    assert inputs.build_declaration_contract_evidence(root, capture, responses)["files"]["index.d.cts"]["reason"] == "declaration_byte_budget_exceeded"


def test_native_declaration_refusals_empty_scope_and_record_budget(tmp_path):
    sources = {"empty.d.ts": b"", "bad.d.ts": b"export interface {", "encoding.d.ts": b"\xff",
        "many.d.ts": b"\n".join(f"export interface Name{i} {{}}".encode() for i in range(70))}
    for name, content in sources.items(): (tmp_path / name).write_bytes(content)
    capture = inputs.capture_auxiliary_inputs(tmp_path, walk_entries(tmp_path, list(sources)))
    responses = declaration_batch(list((tmp_path / name).as_posix() for name in sources))
    view = inputs.build_declaration_contract_evidence(tmp_path, capture, responses)
    assert view["files"]["bad.d.ts"]["status"] == "unavailable"
    assert view["files"]["encoding.d.ts"]["status"] == "unavailable"
    assert view["files"]["empty.d.ts"]["status"] == "observed"
    assert view["files"]["empty.d.ts"]["record_count"] == 0
    many = view["files"]["many.d.ts"]
    assert many["status"] == "partial" and many["record_count"] == 64
    assert many["omitted_statements"] == 6 and many["reasons"]["record_budget_exceeded"] == 6


def test_native_declaration_actual_reread_cannot_borrow_capture_hash(tmp_path):
    source = tmp_path / "index.d.ts"
    source.write_bytes(b"export type Before = string;\r\n")
    capture = inputs.capture_auxiliary_inputs(tmp_path, walk_entries(tmp_path, [source.name]))
    source.write_bytes(b"export type After = number;\r\n")
    view = inputs.build_declaration_contract_evidence(tmp_path, capture, declaration_batch([source.as_posix()]))
    assert view["files"][source.name]["status"] == "unavailable"


def test_native_declaration_name_node_and_byte_bounds_are_explicit(tmp_path):
    huge = tmp_path / "large.d.ts"
    huge.write_bytes(b" " * (inputs.auxiliary_input_policy()["max_file_bytes"] + 1))
    pattern = tmp_path / "pattern.d.ts"
    pattern.write_bytes(b"declare const { first, second }: { first: string, second: string };")
    result = declaration_batch([huge.as_posix(), pattern.as_posix()])
    assert result[huge.as_posix()][0]["declarationContractEvidence"]["status"] == "unavailable"
    assert result[huge.as_posix()][0]["declarationContractEvidence"]["reasons"] == {"byte_budget_exceeded": 1}
    row = result[pattern.as_posix()][0]["declarationContractEvidence"]
    assert row["status"] == "partial" and row["omitted_name_nodes"] == 1  # One pattern, not two semantic bindings.
    assert row["records"][0]["names"] == []


def test_declaration_worker_reuses_existing_transport_and_keeps_implementation_separate(native_declaration_capture):
    from tools.core.node_ast_worker import NodeAstWorkerSession
    root, _, batch, _ = native_declaration_capture
    repo = Path(__file__).resolve().parents[2]
    engine = repo / "tools/engines/ast_sequencer.cjs"
    paths = list(batch)
    with NodeAstWorkerSession(["node", str(engine), "--worker-jsonl"], cwd=repo) as session:
        first, first_metrics = session.request(request_id="declarations-1", paths=paths, timeout_seconds=30)
        second, second_metrics = session.request(request_id="declarations-2", paths=paths, timeout_seconds=30)
    assert json.loads(first)["results"] == json.loads(second)["results"] == batch
    assert first_metrics["worker_process_starts"] == 1 and second_metrics["worker_process_starts"] == 0
    assert not first_metrics["request_replays"] and not second_metrics["request_replays"]
    implementation = root / "implementation.ts"
    implementation.write_text("export const implementation = 1;", encoding="utf-8")
    result = declaration_batch([paths[0], implementation.as_posix()])
    assert result[paths[0]] == batch[paths[0]]
    assert not any("declarationContractEvidence" in row for row in result[implementation.as_posix()])
    assert any(row["name"] == "implementation" for row in result[implementation.as_posix()])


@pytest.mark.parametrize("boundary", ["normal", "excluded", "unreadable", "mixed"])
@pytest.mark.parametrize("declaration_suffix", [".d.ts", ".d.mts", ".d.cts"])
def test_real_atlas_ingests_auxiliary_inputs_without_analyzing_them(tmp_path, monkeypatch, boundary, declaration_suffix):
    from tools.core import artifact_store, config
    from tools.engines import generate_atlas as generator

    root, raw = tmp_path / "repo", tmp_path / "raw"
    root.mkdir()
    raw.mkdir()
    declaration_name = "env" + declaration_suffix
    (root / "tsconfig.json").write_text(json.dumps({"files": [declaration_name]}))
    (root / declaration_name).write_text("declare const ambient: string;")
    (root / "package.json").write_text(json.dumps({"types": "./" + declaration_name,
        "exports": {".": {"types": "./" + declaration_name, "default": "./missing.js"}}}), encoding="utf-8")
    if boundary == "mixed":
        (root / "value.ts").write_text("export const value = 1;", encoding="utf-8")
    if boundary in {"excluded", "unreadable"}:
        child = root / "child"
        child.mkdir()
        (child / "hidden.json").write_text("{}")
        (child / "hidden.d.ts").write_text("export interface Hidden {}", encoding="utf-8")
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
    observed_commands = []
    real_runner = generator.run_observed_subprocess

    def observed_runner(command, **kwargs):
        # Actual declared-source ingestion is now allowed; configuration and
        # the target's own scripts must still never execute.
        assert command[0] == "node"
        assert Path(command[1]).name == "ast_sequencer.cjs"
        observed_commands.append(command)
        return real_runner(command, **kwargs)

    monkeypatch.setattr(generator, "run_observed_subprocess", observed_runner)
    monkeypatch.setattr(generator, "save_json_atomic",
                        lambda path, payload, **kw: store.save_raw(Path(path).stem, payload, **kw))
    monkeypatch.setattr(generator, "DYNAMIC_CONFIG", {"use_sqlite": True})
    monkeypatch.setattr(artifact_store, "STORE", store)
    monkeypatch.setattr(config, "PROJECT_FILTER", [])
    monkeypatch.setenv("SAGE_SYNC_SHADOW_WRITES", "1")
    atlas, _, _ = generator.generate_atlas(stale_projects=["MAIN"])
    auxiliary = atlas["MAIN"]["typescript_auxiliary_inputs"]
    assert auxiliary["status"] == "captured"
    assert set(auxiliary["files"]) == {"tsconfig.json", "package.json", declaration_name}
    assert all(row["status"] == "ok" for row in auxiliary["files"].values())
    assert set(atlas["MAIN"]["files"]) == ({"value.ts"} if boundary == "mixed" else set())
    assert atlas["MAIN"]["resolved_module_importer_index"]["status"] == "complete"
    assert atlas["MAIN"]["resolved_module_importer_index"]["indexed_file_count"] == (1 if boundary == "mixed" else 0)
    assert atlas["MAIN"]["resolved_module_importer_index"]["by_source"] == {}
    assert store.load_raw("atlas", {})["MAIN"]["typescript_auxiliary_inputs"] == auxiliary
    type_view = atlas["MAIN"]["public_contracts"]["packages"][0]["type_entry_evidence"]
    assert type_view["status"] == "observed"
    assert type_view["source_hash"] == auxiliary["files"]["package.json"]["raw_sha256"]
    assert type_view["records"][1]["condition_path"] == ["types"]
    assert store.load_raw("atlas", {})["MAIN"]["public_contracts"]["packages"][0]["type_entry_evidence"] == type_view
    declarations = atlas["MAIN"]["declaration_contract_evidence"]
    assert declarations["status"] == "observed"
    assert set(declarations["files"]) == {declaration_name}
    assert declarations["files"][declaration_name]["records"][0]["syntax_kind"] == "VariableStatement"
    assert declarations["files"][declaration_name]["records"][0]["names"] == ["ambient"]
    assert store.load_raw("atlas", {})["MAIN"]["declaration_contract_evidence"] == declarations
    assert observed_commands
    from tools.core.artifact_validator import ensure_valid_payload
    ensure_valid_payload("atlas", atlas)
    resolution = atlas["MAIN"]["typescript_resolution_inputs"]
    expected_names = [declaration_name, "package.json", "tsconfig.json"]
    if boundary in {"excluded", "unreadable"}:
        expected_names.insert(0, "child")
        assert "child" not in resolution["directories"]
    if boundary == "mixed":
        expected_names.append("value.ts")
    assert resolution["directories"]["."] == expected_names
    assert resolution["status"] == ("partial" if boundary == "unreadable" else "captured")
    assert resolution["walk_errors"] == (1 if boundary == "unreadable" else 0)
    assert store.load_raw("atlas", {})["MAIN"]["typescript_resolution_inputs"] == resolution

    if boundary == "normal" and declaration_suffix == ".d.ts":
        # A new evidence plane must not recreate the clock/identity drift loop.
        repeated, _, _ = generator.generate_atlas(stale_projects=["MAIN"])
        assert repeated["MAIN"]["declaration_contract_evidence"] == declarations
        assert build_atlas_commit(repeated)["snapshot_id"] == build_atlas_commit(atlas)["snapshot_id"]

    # Unrelated surgical edits may retain legacy paths, never old type authority.
    monkeypatch.setattr(generator, "load_previous_atlas", lambda: atlas)
    commands_before_surgical = len(observed_commands)
    updated, _, _ = generator.generate_atlas(stale_projects=["MAIN"], surgical_files=["MAIN::absent.ts"])
    rejected = updated["MAIN"]["public_contracts"]["packages"][0]["type_entry_evidence"]
    assert rejected["status"] == "unavailable"
    assert rejected["reasons"] == ["surgical_scope_unavailable"]
    assert rejected["source_hash"] is None and rejected["records"] == []
    assert atlas["MAIN"]["public_contracts"]["packages"][0]["type_entry_evidence"] == type_view
    assert updated["MAIN"]["declaration_contract_evidence"]["status"] == "unavailable"
    assert updated["MAIN"]["declaration_contract_evidence"]["files"] == {}
    assert len(observed_commands) == commands_before_surgical
    assert atlas["MAIN"]["declaration_contract_evidence"] == declarations
