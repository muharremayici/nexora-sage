"""Literal package/header linkage through actual producers and primary transport."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from tools.core import artifact_store, atlas_io, atlas_typescript_inputs as inputs, manifest_declaration_links as links
from tools.core.agent_packet_budget import COMPACT_AGENT_PACKET_TOKENS, context_budget_profile
from tools.core.artifact_store import ArtifactStore
from tools.core.declaration_contract_view import declaration_file_inspection
from tools.core.package_contracts import build_package_public_contracts
from tools.mcp import server


@pytest.fixture(scope="module")
def source_inputs(tmp_path_factory):
    root = tmp_path_factory.mktemp("manifest-header-links")
    manifests = {"package.json": {"name": "@fixture/root", "types": "./index.d.ts", "typings": "index.d.ts",
        "exports": {".": {"import": {"types": "./index.d.mts"}}}, "typesVersions": {">=5": {"*": ["./v5/*.d.ts"]}}},
        "packages/ui/package.json": {"types": "./types/index.d.mts", "typings": "./types/index.d.cts"}}
    files = {"index.d.ts": "\ufeff// Ö 🌍\r\nexport interface Options { mode: string }\r\nexport declare function run(): void;\r\n",
        "index.d.mts": "export type Alternate = string;", "packages/ui/types/index.d.mts": "export interface Nested {}",
        "packages/ui/types/index.d.cts": "export declare const value: number;", "empty.d.ts": "",
        "partial.d.ts": "declare global { interface Window { marker: string } }"}
    for path, payload in manifests.items(): files[path] = json.dumps(payload, ensure_ascii=False, indent=2).replace("\n", "\r\n")
    for path, text in files.items():
        selected = root / path
        selected.parent.mkdir(parents=True, exist_ok=True)
        selected.write_bytes(text.encode("utf-8"))
    capture = inputs.capture_auxiliary_inputs(root, [(str(root / path), Path(path).name, path) for path in files])
    result = subprocess.run(["node", str(Path(__file__).resolve().parents[2] / "tools/engines/ast_sequencer.cjs"),
        "--batch-json", *inputs.declaration_input_paths(root, capture)], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr
    plane = inputs.build_declaration_contract_evidence(root, capture, json.loads(result.stdout))
    contracts = build_package_public_contracts(root, [root / path for path in manifests], auxiliary_inputs=capture)
    return root, capture, contracts, plane


def derive(source_inputs):
    _, capture, contracts, plane = deepcopy(source_inputs)
    group = links.build_manifest_declaration_links(capture, contracts, plane)
    return capture, contracts, plane, group


def test_actual_manifest_and_native_header_identity_correspondence(source_inputs):
    capture, contracts, plane, group = derive(source_inputs)
    assert links.manifest_link_group_valid(group, plane)
    observed = [row for row in group["records"] if row["link_status"] == "observed"]
    assert len(observed) == 4
    assert [r["candidate_declaration_ref"] for r in observed] == ["index.d.ts", "index.d.ts", "packages/ui/types/index.d.mts", "packages/ui/types/index.d.cts"]
    for row in observed:
        assert row["source_manifest_sha256"] == capture["files"][row["source_manifest"]]["raw_sha256"]
        assert row["captured_declaration_sha256"] == plane["files"][row["candidate_declaration_ref"]]["parser_input_sha256"]
        assert row["resolution_status"] == "not_evaluated" and row["reason"] == "literal_target_correspondence"
    unresolved = [r for r in group["records"] if r["link_status"] == "unresolved"]
    assert len(unresolved) == 2 and all(r["reason"] == "conditional_or_versioned_entry" for r in unresolved)
    assert unresolved[0]["condition_path"] == ["import", "types"] and unresolved[1]["version_selector"] == ">=5"
    assert group["status"] == "partial" and group["runtime_execution"] == "not_established"
    assert contracts == source_inputs[2] and plane == source_inputs[3]  # no in-place graph/header mutation


def test_bom_manifest_binds_actual_declaration_bytes_without_native_authority(tmp_path):
    manifest = tmp_path / "package.json"
    header = tmp_path / "Ö.d.mts"
    manifest_data = b"\xef\xbb\xbf" + '{\r\n"types":"./Ö.d.mts"\r\n}'.encode("utf-8")
    manifest.write_bytes(manifest_data)
    header.write_bytes(b"export interface Options { value: string }\r\n")
    capture = inputs.capture_auxiliary_inputs(tmp_path, [(str(path), path.name, path.name) for path in (manifest, header)])
    result = subprocess.run(["node", str(Path(__file__).resolve().parents[2] / "tools/engines/ast_sequencer.cjs"),
        "--batch-json", *inputs.declaration_input_paths(tmp_path, capture)], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr
    plane = inputs.build_declaration_contract_evidence(tmp_path, capture, json.loads(result.stdout))
    original = deepcopy(plane)
    contracts = build_package_public_contracts(tmp_path, [manifest], auxiliary_inputs=capture)
    out = links.build_manifest_declaration_links(capture, contracts, plane)
    assert out["status"] == "observed" and out["shown_count"] == 1
    assert links.manifest_link_group_valid(out, plane)
    row = out["records"][0]
    assert row["candidate_declaration_ref"] == "Ö.d.mts" and row["link_status"] == "observed"
    assert row["source_manifest_sha256"] == hashlib.sha256(manifest_data).hexdigest()
    assert row["source_manifest_sha256"] != hashlib.sha256(manifest_data[3:]).hexdigest()
    assert row["captured_declaration_sha256"] == hashlib.sha256(header.read_bytes()).hexdigest()
    assert row["resolution_status"] == "not_evaluated" and out["runtime_execution"] == "not_established"
    assert plane == original and manifest.read_bytes() == manifest_data
    # A normalized-text hash cannot be substituted for captured raw bytes.
    forged = deepcopy(contracts)
    forged["packages"][0]["type_entry_evidence"]["source_hash"] = hashlib.sha256(manifest_data[3:]).hexdigest()
    rejected = links.build_manifest_declaration_links(capture, forged, plane)
    assert rejected["status"] == "partial" and rejected["records"] == []
    assert "manifest_evidence_unavailable" in rejected["reasons"]


@pytest.mark.parametrize("literal,expected", [("./index.d.ts", "index.d.ts"), ("index.d.ts", "index.d.ts"),
    ("./Ö🌍.d.ts", "Ö🌍.d.ts"), ("./ Index.d.ts ", " Index.d.ts "), ("./a//b.d.ts", None),
    ("././index.d.ts", None), ("./a/../index.d.ts", None), ("../index.d.ts", None), ("/index.d.ts", None),
    ("C:/index.d.ts", None), ("types\\index.d.ts", None), ("./*.d.ts", None), ("./%2e/index.d.ts", None),
    ("./index.d.ts?raw", None), ("./index.d.ts#types", None), ("", None), ("https://x/index.d.ts", None)])
def test_lexical_subset_never_resolves_or_trims_target(source_inputs, literal, expected):
    capture, contracts, plane, _ = derive(source_inputs)
    package = contracts["packages"][0]
    evidence = package["type_entry_evidence"]
    evidence["records"] = [{**evidence["records"][0], "target_literal": literal}]
    evidence["shown_count"] = 1
    contracts["packages"] = [package]
    out = links.build_manifest_declaration_links(capture, contracts, plane)
    assert out["records"][0]["candidate_declaration_ref"] == expected
    if expected != "index.d.ts": assert out["records"][0]["link_status"] == "unresolved"
    assert out["records"][0]["target_literal"] == literal and links.manifest_link_group_valid(out, plane)


@pytest.mark.parametrize("damage", ["foreign_root", "surgical", "missing_aux", "stale_manifest", "noncanonical", "package_root",
    "header_hash", "capture_hash", "capture_size", "missing_target", "old_policy", "bad_header", "header_unavailable", "manifest_partial", "aux_partial"])
def test_binding_refusals_never_borrow_another_capture_or_live_file(source_inputs, monkeypatch, damage):
    capture, contracts, plane, _ = derive(source_inputs)
    if damage == "foreign_root": capture["project_root"] += "/foreign"
    elif damage == "surgical": capture["status"] = "unavailable_surgical_scan"
    elif damage == "missing_aux": capture["files"].pop("package.json")
    elif damage == "stale_manifest": contracts["packages"][0]["type_entry_evidence"]["source_hash"] = "0" * 64
    elif damage == "noncanonical": capture["files"]["package.json"]["canonical_file_path"] = False
    elif damage == "package_root": contracts["packages"][0]["package_root"] = "packages/ui"
    elif damage == "header_hash": plane["files"]["index.d.ts"]["parser_input_sha256"] = "0" * 64
    elif damage == "capture_hash": capture["files"]["index.d.ts"]["raw_sha256"] = "0" * 64
    elif damage == "capture_size": capture["files"]["index.d.ts"]["size_bytes"] += 1
    elif damage == "missing_target": plane["files"].pop("index.d.ts")
    elif damage == "old_policy": plane["policy_sha256"] = "0" * 64
    elif damage == "bad_header": plane["files"]["index.d.ts"]["record_count"] += 1
    elif damage == "header_unavailable": plane["files"]["index.d.ts"] = {"status": "unavailable", "reason": "fixture"}
    elif damage == "manifest_partial": contracts["packages"][0]["type_entry_evidence"].update(status="partial", omitted_count=None, reasons=["node_budget_exceeded"])
    else: capture.update(status="partial", omitted_files=1)
    original_resolve = Path.resolve
    def resolve(path, *args, **kwargs):
        assert not path.is_relative_to(source_inputs[0]), "Link derivation must not inspect target filesystem"
        return original_resolve(path, *args, **kwargs)
    monkeypatch.setattr(Path, "resolve", resolve)
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: pytest.fail("No native/runtime execution during derivation"))
    out = links.build_manifest_declaration_links(capture, contracts, plane)
    if damage in {"foreign_root", "surgical", "old_policy"}: assert out["status"] == "unavailable"
    elif damage in {"aux_partial", "manifest_partial"}:
        assert out["status"] == "partial" and out["omitted_count"] is None
        assert any(r["link_status"] == "observed" for r in out["records"])
    else:
        assert not any(r["source_manifest"] == "package.json" and r["link_status"] == "observed" for r in out["records"])


@pytest.mark.parametrize("key,value", [("max_records", 1), ("max_candidates", 1), ("max_packages", 1),
    ("max_record_bytes", 300), ("max_total_bytes", 1100), ("max_text_bytes", 8)])
def test_project_wide_record_and_byte_limits_preserve_unknown_omissions(source_inputs, monkeypatch, key, value):
    settings = deepcopy(inputs.auxiliary_input_policy())
    settings["manifest_declaration_links"][key] = value
    if key == "max_total_bytes": settings["manifest_declaration_links"]["max_record_bytes"] = 1024
    if key == "max_record_bytes": settings["manifest_declaration_links"]["max_text_bytes"] = 256
    monkeypatch.setattr(links, "auxiliary_input_policy", lambda: settings)
    capture, contracts, plane, out = derive(source_inputs)
    assert out["status"] in {"partial", "unavailable"} and out["omitted_count"] is None
    assert len(out["records"]) <= settings["manifest_declaration_links"]["max_records"]
    assert len(links._encoded(out)) <= settings["manifest_declaration_links"]["max_total_bytes"]
    if out["status"] != "unavailable": assert links.manifest_link_group_valid(out, plane)


@pytest.mark.parametrize("mutation", ["policy", "manifest_policy", "header_policy", "capture", "root", "runtime", "count_bool", "omitted_bool",
    "reason", "observed_partial", "extra", "row_extra", "row_not_object", "hash", "target_hash", "field", "order", "condition", "selected", "traversal", "surrogate", "duplicate"])
def test_invalid_link_group_cannot_promote_valid_headers(source_inputs, mutation):
    _, _, plane, out = derive(source_inputs)
    row = out["records"][0]
    if mutation == "policy": out["policy_sha256"] = "0" * 64
    elif mutation == "manifest_policy": out["manifest_policy_sha256"] = "0" * 64
    elif mutation == "header_policy": out["declaration_policy_sha256"] = "0" * 64
    elif mutation == "capture": out["capture_sha256"] = None
    elif mutation == "root": out["project_root"] += "/other"
    elif mutation == "runtime": out["runtime_execution"] = "confirmed"
    elif mutation == "count_bool": out["shown_count"] = True
    elif mutation == "omitted_bool": out["omitted_count"] = False
    elif mutation == "reason": out["reasons"].append("invented")
    elif mutation == "observed_partial": out["status"] = "observed"
    elif mutation == "extra": out["source_text"] = "not allowed"
    elif mutation == "row_extra": row["source_text"] = "not allowed"
    elif mutation == "row_not_object": out["records"][0] = []
    elif mutation == "hash": row["source_manifest_sha256"] = "broken"
    elif mutation == "target_hash": row["captured_declaration_sha256"] = "0" * 64
    elif mutation == "field": row["field_path"] = ["exports", ".", "types"]
    elif mutation == "order": row["sibling_order"] = [True]
    elif mutation == "condition": row["condition_path"] = ["types"]
    elif mutation == "selected": row["resolution_status"] = "resolved"
    elif mutation == "traversal": row["target_literal"] = "../index.d.ts"
    elif mutation == "surrogate": row["target_literal"] = "\ud800"
    else: out["records"].append(deepcopy(row)); out["shown_count"] += 1
    assert not links.manifest_link_group_valid(out, plane)


@pytest.mark.parametrize("literal,reason", [("./implementation.ts", "outside_declaration_subset"),
    ("./Index.d.ts", "target_not_in_captured_headers"), ("./empty.d.ts", "literal_target_correspondence"),
    ("./partial.d.ts", "literal_target_correspondence")])
def test_exact_literal_kind_and_empty_or_partial_header_coverage_stay_separate(source_inputs, literal, reason):
    capture, contracts, plane, _ = derive(source_inputs)
    package = contracts["packages"][0]
    package["type_entry_evidence"]["records"] = [{**package["type_entry_evidence"]["records"][0], "target_literal": literal}]
    package["type_entry_evidence"]["shown_count"] = 1
    out = links.build_manifest_declaration_links(capture, {"packages": [package]}, plane)
    assert out["records"][0]["reason"] == reason and links.manifest_link_group_valid(out, plane)
    if literal == "./empty.d.ts": assert plane["files"]["empty.d.ts"]["record_count"] == 0
    if literal == "./partial.d.ts": assert plane["files"]["partial.d.ts"]["status"] == "partial"


def test_exact_record_budget_does_not_invent_or_hide_omissions(source_inputs, monkeypatch):
    settings = deepcopy(inputs.auxiliary_input_policy())
    settings["manifest_declaration_links"]["max_records"] = 6
    monkeypatch.setattr(links, "auxiliary_input_policy", lambda: settings)
    _, _, plane, out = derive(source_inputs)
    assert len(out["records"]) == 6 and out["omitted_count"] == 0
    assert "record_budget_exceeded" not in out["reasons"] and links.manifest_link_group_valid(out, plane)


def test_storage_plane_budget_preserves_headers_instead_of_adding_oversized_links(source_inputs):
    capture, contracts, plane, out = derive(source_inputs)
    original = deepcopy(plane)
    required = len(links._encoded({**plane, "manifest_type_links": out}))
    assert links.build_manifest_declaration_links(capture, contracts, plane, max_plane_bytes=required) == out
    assert links.build_manifest_declaration_links(capture, contracts, plane, max_plane_bytes=required - 1) is None
    assert plane == original
    with pytest.raises(ValueError): links.build_manifest_declaration_links(capture, contracts, plane, max_plane_bytes=True)


@pytest.mark.parametrize("key,value", [("max_records", True), ("max_candidates", 0), ("max_total_bytes", -1),
    ("max_total_bytes", 10), ("literal_fields", []), ("literal_fields", ["types", "types"]), ("version", "v99")])
def test_invalid_central_policy_never_silently_defaults(monkeypatch, key, value):
    settings = deepcopy(inputs.auxiliary_input_policy())
    settings["manifest_declaration_links"][key] = value
    monkeypatch.setattr(links, "auxiliary_input_policy", lambda: settings)
    with pytest.raises(ValueError): links.manifest_link_policy()


def test_malformed_literal_tokens_are_omitted_not_executed(source_inputs):
    capture, contracts, plane, _ = derive(source_inputs)
    contracts["packages"][0]["type_entry_evidence"]["records"][0]["field_path"] = [[]]
    out = links.build_manifest_declaration_links(capture, contracts, plane)
    assert out["status"] == "partial" and out["omitted_count"] is None and "record_shape_unavailable" in out["reasons"]
    assert links.manifest_link_group_valid(out, plane)


@pytest.mark.parametrize("omitted", ["all", "nested"])
def test_captured_manifest_skipped_by_literal_reader_is_unknown_not_empty(source_inputs, omitted):
    capture, contracts, plane, _ = derive(source_inputs)
    contracts["packages"] = [] if omitted == "all" else contracts["packages"][:1]
    out = links.build_manifest_declaration_links(capture, contracts, plane)
    assert out["status"] == "partial" and out["omitted_count"] is None
    assert "manifest_evidence_unavailable" in out["reasons"] and links.manifest_link_group_valid(out, plane)
    if omitted == "all": assert out["records"] == []


def test_existing_generator_fingerprint_observes_link_helper_bytes(monkeypatch):
    from tools.core import atlas_integrity
    original = Path.read_bytes
    before = atlas_integrity.generator_fingerprint()
    source = Path(__file__).resolve().parents[2] / "tools/core/manifest_declaration_links.py"
    def changed(path):
        value = original(path)
        return value + b"\n# isolated fingerprint observation\n" if path == source else value
    monkeypatch.setattr(Path, "read_bytes", changed)
    assert atlas_integrity.generator_fingerprint() != before


@pytest.mark.parametrize("mode", ["inline", "partitioned", "json"])
@pytest.mark.parametrize("rel", ["index.d.ts", "packages/ui/types/index.d.mts", "packages/ui/types/index.d.cts"])
def test_real_primary_and_mcp_keep_exact_project_path_and_authority(source_inputs, tmp_path, monkeypatch, mode, rel):
    _, _, plane, group = derive(source_inputs)
    plane["manifest_type_links"] = group
    project = 'LibÇ"🌍'
    raw = tmp_path / ".raw"
    store = ArtifactStore(raw_dir=raw)
    raw.mkdir(parents=True, exist_ok=True)
    atlas = {project: {"declaration_contract_evidence": plane}}
    store.use_sqlite = mode != "json"
    if mode == "json":
        data = ("\ufeff" + json.dumps(atlas, ensure_ascii=False)).encode("utf-8")
        (raw / "atlas.json").write_bytes(data)
        monkeypatch.setattr(atlas_io, "RAW_DIR", raw)
        monkeypatch.setattr(artifact_store, "STORE", store)
    else:
        store._ensure_schema()
        if mode == "partitioned": store.state_payload_inline_limit_bytes = 1
        store._save_payload_to_state_table("atlas", atlas)
    monkeypatch.setattr(artifact_store, "ArtifactStore", lambda **_k: store)
    monkeypatch.setattr(server, "_raw_dir_for_target", lambda *_a: raw)
    monkeypatch.setattr(server, "_reports_dir_for_target", lambda *_a: raw.parent / "reports")
    monkeypatch.setattr(atlas_io, "load_atlas_data_with_identity", lambda *_a: pytest.fail("Do not read the full Atlas"))
    out = json.loads(server.inspect_file(project + "::" + rel, format="json"))
    link = out["manifest_type_links"]
    assert link["shown"] >= 1 and link["shown"] + link["omitted"] == link["matching_stored_records"]
    assert all(row["candidate_declaration_ref"] == rel for row in link["records"])
    assert out["source_binding"] == "stored_declaration_metadata_only" and not out["one_shot_edit_ready"]
    assert out["runtime_execution"] == out["semantic_type_safety"] == "not_established" and out["target_files"] == []
    if mode == "json": assert out["stored_atlas_identity"] == "json:" + hashlib.sha256(data).hexdigest()
    brief = server.inspect_file(project + "::" + rel)
    assert context_budget_profile(brief, budget_tokens=COMPACT_AGENT_PACKET_TOKENS)["status"] == "pass"
    assert json.loads(server.inspect_file(project.lower() + "::" + rel, format="json"))["declaration_contract"]["status"] == "unavailable"


@pytest.mark.parametrize("mode", ["missing", "malformed", "unknown_version", "oversized"])
def test_link_refusal_does_not_destroy_valid_legacy_header_view(source_inputs, tmp_path, monkeypatch, mode):
    _, _, plane, group = derive(source_inputs)
    if mode == "malformed": group["records"][0]["resolution_status"] = "resolved"
    if mode == "unknown_version": group["version"] = "v99"
    if mode == "oversized": group["records"] = group["records"] * 1000; group["shown_count"] = len(group["records"])
    if mode != "missing": plane["manifest_type_links"] = group
    monkeypatch.setattr("tools.core.declaration_contract_view.load_atlas_declaration_metadata",
        lambda *_a: atlas_io.AtlasRead({"MAIN": {"declaration_contract_evidence": plane}}, "json:" + "0" * 64, "json_primary"))
    out = declaration_file_inspection(tmp_path, "MAIN::index.d.ts", record_limit=6)
    assert out["declaration_contract"]["status"] == "observed" and out["declaration_contract"]["records"]
    assert out["manifest_type_links"]["status"] == "unavailable" and not out["one_shot_edit_ready"]


def test_actual_atlas_producer_surgical_invalidation_and_repeat_identity(tmp_path, monkeypatch):
    from tools.core import config
    from tools.engines import generate_atlas as generator
    root, raw = tmp_path / "target", tmp_path / "raw"
    root.mkdir(); raw.mkdir()
    (root / "index.d.ts").write_bytes(b"export interface Public {}\r\n")
    (root / "package.json").write_bytes(b'{"types":"./index.d.ts"}')
    store = ArtifactStore(raw_dir=raw)
    store.use_sqlite = True
    store._ensure_schema()
    monkeypatch.setattr(generator, "ROOT", root)
    monkeypatch.setattr(generator, "RAW_DIR", raw)
    monkeypatch.setattr(generator, "resolve_runtime_projects", lambda *_a, **_k: {"MAIN": root})
    monkeypatch.setattr(generator, "configured_project_ownership_exclusions", lambda *_a, **_k: {"MAIN": []})
    monkeypatch.setattr(generator, "load_previous_atlas", lambda: {})
    monkeypatch.setattr(generator, "ensure_output_dir", lambda: None)
    monkeypatch.setattr(generator, "save_json_atomic", lambda path, payload, **kw: store.save_raw(Path(path).stem, payload, **kw))
    monkeypatch.setattr(generator, "DYNAMIC_CONFIG", {"use_sqlite": True})
    monkeypatch.setattr(artifact_store, "STORE", store)
    monkeypatch.setattr(config, "PROJECT_FILTER", [])
    monkeypatch.setenv("SAGE_SYNC_SHADOW_WRITES", "1")
    first, _, _ = generator.generate_atlas(stale_projects=["MAIN"])
    plane = first["MAIN"]["declaration_contract_evidence"]
    assert plane["manifest_type_links"]["records"][0]["link_status"] == "observed"
    assert first["MAIN"]["files"] == {} and first["MAIN"]["symbols"] == []
    from tools.core.artifact_validator import ensure_valid_payload
    ensure_valid_payload("atlas", first)
    repeated, _, _ = generator.generate_atlas(stale_projects=["MAIN"])
    assert repeated["MAIN"]["declaration_contract_evidence"] == plane
    monkeypatch.setattr(generator, "load_previous_atlas", lambda: first)
    updated, _, _ = generator.generate_atlas(stale_projects=["MAIN"], surgical_files=["MAIN::absent.ts"])
    assert updated["MAIN"]["declaration_contract_evidence"]["manifest_type_links"]["status"] == "unavailable"
    assert first["MAIN"]["declaration_contract_evidence"] == plane
