"""Bounded literal type pointers to ordinary source; no target code executes."""
from contextlib import closing
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess

import pytest

from tools.core import artifact_store, atlas_io, atlas_typescript_inputs as inputs, manifest_source_links as links
from tools.core.agent_packet_budget import COMPACT_AGENT_PACKET_TOKENS, context_budget_profile
from tools.core.artifact_store import ArtifactStore
from tools.core.package_contracts import build_package_public_contracts
from tools.engines import generate_atlas as generator
from tools.inspect_target import project_manifest_source_links, render_agent_inspection_brief
from tools.mcp import server


@pytest.fixture(scope="module")
def source_inputs(tmp_path_factory):
    root = tmp_path_factory.mktemp("manifest-source-links")
    manifests = {"package.json": {"types": "./src/index.ts", "typings": "src/index.ts", "exports": {".": {"types": "./missing.d.ts"}}},
        "packages/ui/package.json": {"types": "./src/widget.tsx", "typings": "./src/util.mts"}}
    sources = {"src/index.ts": "\ufeff// Ö 🌍\r\nexport const value = 1;\r\nthrow new Error('Never execute target');\r\n",
        "packages/ui/src/widget.tsx": "export const Widget = () => <div />;\n",
        "packages/ui/src/util.mts": "export const util = 2;\n", "src/common.cts": "export const common = 3;\n"}
    for path, source in sources.items():
        selected = root / path
        selected.parent.mkdir(parents=True, exist_ok=True)
        selected.write_bytes(source.encode("utf-8"))
    for path, manifest in manifests.items():
        (root / path).write_bytes(json.dumps(manifest, ensure_ascii=False).encode("utf-8"))
    walks = [(str(root / path), Path(path).name, path) for path in [*manifests, *sources]]
    capture = inputs.capture_auxiliary_inputs(root, walks)
    contracts = build_package_public_contracts(root, [root / path for path in manifests], auxiliary_inputs=capture)
    assert len(contracts["packages"]) == 2, "Positive fixture requires admitted literal manifests"
    run = subprocess.run(["node", str(Path(__file__).resolve().parents[2] / "tools/engines/ast_sequencer.cjs"),
        "--batch-json", *[str(root / path) for path in sources]], capture_output=True, text=True, encoding="utf-8", timeout=40)
    assert run.returncode == 0, run.stderr
    parsed = {Path(key).resolve(): value for key, value in json.loads(run.stdout).items()}
    files = {}
    for rel, text in sources.items():
        raw = parsed[(root / rel).resolve()]
        meta = next(row for row in raw if row.get("name") == "__file_meta__")
        identity = next(value.split(":", 1)[1] for value in meta["features"] if value.startswith("Hash:"))
        assert identity == hashlib.sha256(text.encode("utf-8")).hexdigest()
        files[rel] = {"type": "typescript", "language": "typescript", "exports": [], "imports": [], "symbols": [],
            "project_key": "MAIN", "atlas_rel_path": rel, "workspace_rel": rel, "repo_relative_path": rel,
            "target_ref": "MAIN::" + rel, "canonical_file_path": True, "hash": identity,
            "ast_contract_version": generator.AST_CONTRACT_VERSION, "parser_evidence": generator._file_parser_evidence(raw, "typescript"),
            "auxiliary_input_identity": inputs.auxiliary_context_identity(capture)}
    return root, capture, contracts, files


def derive(source_inputs):
    root, capture, contracts, files = deepcopy(source_inputs)
    index = links.build_manifest_source_link_index("MAIN", capture, contracts)
    for rel, file in files.items():
        links.attach_manifest_source_links(file, "MAIN", rel, index)
    return root, capture, contracts, files, index


def test_source_only_package_and_actual_parser_identity(source_inputs):
    _, capture, _, files, index = derive(source_inputs)
    assert not any(path.endswith(".d.ts") for path in capture["files"])
    assert index["status"] == "partial"  # Conditional record is not native-selected.
    for rel in ("src/index.ts", "packages/ui/src/widget.tsx", "packages/ui/src/util.mts"):
        group = files[rel]["manifest_type_entry_links"]
        assert links.manifest_source_link_group_valid(group, files[rel], "MAIN", rel)
        assert group["source_text_identity"]["value"] == files[rel]["hash"]
        assert group["source_text_identity"]["algorithm"] == "sha256"
        assert group["runtime_execution"] == "not_established" and group["resolution_status"] == "not_evaluated"
        assert all(row["source_manifest_sha256"] == capture["files"][row["source_manifest"]]["raw_sha256"] for row in group["records"])
    assert len(files["src/index.ts"]["manifest_type_entry_links"]["records"]) == 2
    assert "manifest_type_entry_links" not in files["src/common.cts"]
    assert "manifest_type_entry_links" not in source_inputs[3]["src/index.ts"]


@pytest.mark.parametrize("data", [b'\xef\xbb\xbf\xef\xbb\xbf{"types":"./index.ts"}',
    b' \xef\xbb\xbf{"types":"./index.ts"}', b'\xef\xbb\xbf', b'\xef\xbb\xbf{"types":"./index.ts"}trailing'])
def test_malformed_manifest_stays_unknown_after_bom_admission(tmp_path, data):
    manifest = tmp_path / "package.json"
    manifest.write_bytes(data)
    capture = inputs.capture_auxiliary_inputs(tmp_path, [(str(manifest), "package.json", "package.json")])
    assert capture["files"]["package.json"]["status"] == "ok"
    contracts = build_package_public_contracts(tmp_path, [manifest], auxiliary_inputs=capture)
    index = links.build_manifest_source_link_index("MAIN", capture, contracts)
    assert index["status"] == "partial" and index["producer_omitted"] is None
    assert "manifest_evidence_unavailable" in index["reasons"] and not index["by_source"]


def test_cts_source_is_not_reclassified_as_a_declaration(source_inputs):
    _, capture, contracts, files = deepcopy(source_inputs)
    evidence = contracts["packages"][0]["type_entry_evidence"]
    evidence.update(records=[{**evidence["records"][0], "target_literal": "./src/common.cts"}], shown_count=1)
    index = links.build_manifest_source_link_index("MAIN", capture, contracts)
    file = files["src/common.cts"]
    links.attach_manifest_source_links(file, "MAIN", "src/common.cts", index)
    assert links.manifest_source_link_group_valid(file["manifest_type_entry_links"], file, "MAIN", "src/common.cts")
    assert file["language"] == "typescript" and "declaration_contract_evidence" not in file


@pytest.mark.parametrize("key,value", [("max_records", True), ("max_candidates", 0), ("max_group_bytes", 20),
    ("literal_fields", []), ("version", "unknown")])
def test_invalid_policy_has_no_silent_local_default(monkeypatch, key, value):
    settings = deepcopy(inputs.auxiliary_input_policy())
    settings["manifest_source_links"][key] = value
    monkeypatch.setattr(links, "auxiliary_input_policy", lambda: settings)
    with pytest.raises(ValueError):
        links.manifest_source_link_policy()


@pytest.mark.parametrize("literal", ["../src/index.ts", "C:/src/index.ts", "/src/index.ts", "src\\index.ts", "./src//index.ts",
    "./src/./index.ts", "./src/*.ts", "./src/%69ndex.ts", "./src/index.ts?raw", "./src/Index.ts", "./index.d.ts", "./index.d.mts", "./src/index.js"])
def test_nonmatching_literal_never_invents_a_source(source_inputs, literal):
    _, capture, contracts, files = deepcopy(source_inputs)
    evidence = contracts["packages"][0]["type_entry_evidence"]
    evidence.update(records=[{**evidence["records"][0], "target_literal": literal}], shown_count=1)
    index = links.build_manifest_source_link_index("MAIN", capture, {"packages": contracts["packages"][:1]})
    links.attach_manifest_source_links(files["src/index.ts"], "MAIN", "src/index.ts", index)
    assert "manifest_type_entry_links" not in files["src/index.ts"]


@pytest.mark.parametrize("damage", ["foreign_project", "foreign_path", "canonicality", "language", "ast", "context", "parser", "parser_kind", "hash", "workspace", "target_ref"])
def test_source_binding_refuses_wrong_or_degraded_metadata(source_inputs, damage, monkeypatch):
    root, _, _, files, index = derive(source_inputs)
    file = files["src/index.ts"]
    if damage == "foreign_project": file["project_key"] = "OTHER"
    elif damage == "foreign_path": file["atlas_rel_path"] = "src/other.ts"
    elif damage == "canonicality": file["canonical_file_path"] = False
    elif damage == "language": file["language"] = "javascript"
    elif damage == "ast": file["ast_contract_version"] = "old"
    elif damage == "context": file["auxiliary_input_identity"] = "0" * 64
    elif damage == "parser": file["parser_evidence"]["status"] = "degraded"
    elif damage == "parser_kind": file["parser_evidence"]["parser_kind"] = "regex"
    elif damage == "hash": file["hash"] = "err"
    elif damage == "workspace": file["workspace_rel"] = "../escape.ts"
    else: file["target_ref"] = "MAIN::foreign.ts"
    old_resolve = Path.resolve
    def own_policy_only(path, *args, **kwargs):
        assert not path.is_relative_to(root), "No target path read during correspondence"
        return old_resolve(path, *args, **kwargs)
    monkeypatch.setattr(Path, "resolve", own_policy_only)
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: pytest.fail("No target execution"))
    links.attach_manifest_source_links(file, "MAIN", "src/index.ts", index)
    assert "manifest_type_entry_links" not in file


@pytest.mark.parametrize("damage", ["partial", "surgical", "missing_manifest", "manifest_hash", "manifest_canonicality", "manifest_root", "missing_packages"])
def test_unknown_manifest_capture_is_not_empty_success(source_inputs, damage):
    _, capture, contracts, files = deepcopy(source_inputs)
    if damage == "partial": capture["status"] = "partial"
    elif damage == "surgical": capture["status"] = "unavailable_surgical_scan"
    elif damage == "missing_manifest": contracts["packages"] = []
    elif damage == "manifest_hash": contracts["packages"][0]["type_entry_evidence"]["source_hash"] = "0" * 64
    elif damage == "manifest_canonicality": capture["files"]["package.json"]["canonical_file_path"] = False
    elif damage == "manifest_root": contracts["packages"][0]["package_root"] = "foreign"
    else: contracts = {}
    index = links.build_manifest_source_link_index("MAIN", capture, contracts)
    links.attach_manifest_source_links(files["src/index.ts"], "MAIN", "src/index.ts", index)
    assert "manifest_type_entry_links" not in files["src/index.ts"]
    assert index["status"] != "observed"


@pytest.mark.parametrize("damage", ["policy", "manifest_policy", "project", "path", "ast", "context", "algorithm", "basis", "hash", "count_bool", "omitted_bool",
    "reason", "reason_object", "extra", "selected", "row_extra", "row_hash", "literal", "condition", "order_bool", "duplicate", "old_version"])
def test_independent_stored_admission_rejects_malformed_or_promoted_group(source_inputs, damage):
    _, _, _, files, _ = derive(source_inputs)
    file = files["src/index.ts"]; group = file["manifest_type_entry_links"]; row = group["records"][0]
    if damage == "policy": group["policy_sha256"] = "0" * 64
    elif damage == "manifest_policy": group["manifest_policy_sha256"] = "0" * 64
    elif damage == "project": group["project_key"] = "OTHER"
    elif damage == "path": group["atlas_rel_path"] = "src/foreign.ts"
    elif damage == "ast": group["ast_contract_version"] = "old"
    elif damage == "context": group["auxiliary_context_identity"] = "0" * 64
    elif damage == "algorithm": group["source_text_identity"]["algorithm"] = "raw_sha256"
    elif damage == "basis": group["source_text_identity"]["basis"] = "raw_capture"
    elif damage == "hash": group["source_text_identity"]["value"] = "0" * 64
    elif damage == "count_bool": group["shown_count"] = True
    elif damage == "omitted_bool": group["producer_omitted"] = False
    elif damage == "reason": group["reasons"].append("invented")
    elif damage == "reason_object": group["reasons"].append({})
    elif damage == "extra": group["native_resolved"] = True
    elif damage == "selected": row["resolution_status"] = "resolved"
    elif damage == "row_extra": row["native_resolved"] = True
    elif damage == "row_hash": row["source_manifest_sha256"] = "bad"
    elif damage == "literal": row["target_literal"] = "./other.ts"
    elif damage == "condition": row["condition_path"] = ["types"]
    elif damage == "order_bool": row["sibling_order"] = [True]
    elif damage == "duplicate": group["records"].append(deepcopy(row)); group["shown_count"] += 1
    else: group["version"] = "v99"
    assert not links.manifest_source_link_group_valid(group, file, "MAIN", "src/index.ts")


@pytest.mark.parametrize("key,value", [("max_packages", 1), ("max_candidates", 1), ("max_records", 1), ("max_text_bytes", 8),
    ("max_record_bytes", 200), ("max_group_bytes", 1500), ("max_total_bytes", 1800)])
def test_project_wide_retention_bounds_and_unknowns(source_inputs, monkeypatch, key, value):
    settings = deepcopy(inputs.auxiliary_input_policy())
    policy = settings["manifest_source_links"]; policy[key] = value
    if key == "max_record_bytes": policy["max_text_bytes"] = 128
    if key in {"max_group_bytes", "max_total_bytes"}:
        policy["max_text_bytes"] = 128; policy["max_record_bytes"] = 1024
    if key == "max_total_bytes": policy["max_group_bytes"] = 1500
    monkeypatch.setattr(links, "auxiliary_input_policy", lambda: settings)
    _, _, _, files, index = derive(source_inputs)
    groups = [file["manifest_type_entry_links"] for file in files.values() if "manifest_type_entry_links" in file]
    assert sum(len(group["records"]) for group in groups) <= policy["max_records"]
    assert sum(len(links._encoded(group)) for group in groups) <= policy["max_total_bytes"]
    for group in groups:
        assert len(links._encoded(group)) <= policy["max_group_bytes"]
    assert index["status"] != "observed"


def test_optional_section_does_not_destroy_base_file_or_old_authority(source_inputs):
    _, _, _, files, index = derive(source_inputs)
    file = files["src/index.ts"]
    base = {key: value for key, value in file.items() if key != "manifest_type_entry_links"}
    links.attach_manifest_source_links(file, "MAIN", "src/index.ts", index, max_file_bytes=len(links._encoded(base)))
    assert file == base
    file["hash"] = hashlib.md5(b"fallback-text").hexdigest()
    links.attach_manifest_source_links(file, "MAIN", "src/index.ts", index)
    assert file["manifest_type_entry_links"]["source_text_identity"]["algorithm"] == "md5"
    assert links.manifest_source_link_group_valid(file["manifest_type_entry_links"], file, "MAIN", "src/index.ts")


def test_unmatched_files_clear_stale_evidence_without_reacquiring_policy(source_inputs, monkeypatch):
    _, _, _, files, index = derive(source_inputs)
    file = files["src/common.cts"]
    file["manifest_type_entry_links"] = deepcopy(files["src/index.ts"]["manifest_type_entry_links"])
    monkeypatch.setattr(links, "manifest_source_link_policy", lambda: pytest.fail("No per-unmatched-file policy acquisition"))
    monkeypatch.setattr(links, "_source_identity", lambda *_a: pytest.fail("No unmatched-file identity probe"))
    links.attach_manifest_source_links(file, "MAIN", "src/common.cts", index)
    assert "manifest_type_entry_links" not in file


@pytest.mark.parametrize("mode", ["inline", "partitioned", "json"])
def test_existing_primary_file_read_and_real_mcp_projection(source_inputs, tmp_path, monkeypatch, mode):
    _, _, _, files, _ = derive(source_inputs)
    raw = tmp_path / ".raw"; raw.mkdir()
    store = ArtifactStore(raw_dir=raw); store.use_sqlite = mode != "json"
    atlas = {"MAIN": {"files": files}}
    if mode == "json":
        (raw / "atlas.json").write_bytes(("\ufeff" + json.dumps(atlas, ensure_ascii=False)).encode("utf-8"))
        monkeypatch.setattr(atlas_io, "RAW_DIR", raw); monkeypatch.setattr(artifact_store, "STORE", store)
    else:
        store._ensure_schema()
        if mode == "partitioned": store.state_payload_inline_limit_bytes = 1
        store._save_payload_to_state_table("atlas", atlas)
    monkeypatch.setattr(artifact_store, "ArtifactStore", lambda **_k: store)
    monkeypatch.setattr(server, "_raw_dir_for_target", lambda *_a: raw)
    monkeypatch.setattr(server, "_reports_dir_for_target", lambda *_a: tmp_path / "reports")
    # Isolate only unrelated snapshot/findings producers. The actual MCP method,
    # selected primary file read, new admission, projection and renderer run.
    monkeypatch.setattr(server, "_sqlite_file_inspection_payload", lambda *_a, **_k: {
        "target": {"kind": "file", "value": "MAIN::src/index.ts"}, "summary": {},
        "target_file_context": [{"project_key": "MAIN", "file": "src/index.ts", "repo_relative_path": "src/index.ts", "atlas_node": "MAIN::src/index.ts"}],
        "target_path_status": {"target_ref": "MAIN::src/index.ts"}, "one_shot_edit_ready": False})
    monkeypatch.setattr(server, "_add_inspection_grounding_status", lambda *_a, **_k: None)
    calls = []
    selected = server.load_atlas_file_metadata
    def read(*args):
        calls.append(args[1:])
        actual = selected(*args)
        if actual is not None:
            assert actual == files["src/index.ts"], "Selected primary must retain exact source metadata"
        return actual
    monkeypatch.setattr(server, "load_atlas_file_metadata", read)
    machine = json.loads(server.inspect_file("MAIN::src/index.ts", format="json"))
    assert calls == [("MAIN", "src/index.ts")]
    section = machine["manifest_type_entry_links"]
    assert section["shown"] == 2 and section["omitted"] == 0 and section["producer_omitted"] == 1, section
    assert section["runtime_execution"] == section["semantic_type_safety"] == "not_established"
    assert not machine["one_shot_edit_ready"] and "stored_atlas_identity" not in section
    assert "manifest_type_entry_links:" in server.inspect_file("MAIN::src/index.ts")
    if mode == "partitioned":
        with closing(sqlite3.connect(raw / "codemaps.db")) as conn:
            conn.execute("DELETE FROM state_payload_parts WHERE name='atlas'"); conn.commit()
        # No shadow is supplied; selected-primary corruption must stay unknown.
        failed = json.loads(server.inspect_file("MAIN::src/index.ts", format="json"))
        assert failed["manifest_type_entry_links"]["status"] == "unavailable"


def test_shared_compact_budget_preserves_source_grounding(source_inputs):
    _, _, _, files, _ = derive(source_inputs)
    payload = {"target": {"kind": "file", "value": "MAIN::src/index.ts"}, "summary": {},
        "target_refs": ["MAIN::src/index.ts"], "target_files": ["src/index.ts"], "one_shot_edit_ready": True,
        "target_path_status": {"target_ref": "MAIN::src/index.ts"}}
    before = deepcopy(payload)
    project_manifest_source_links(payload, files["src/index.ts"], "MAIN", "src/index.ts")
    assert payload["manifest_type_entry_links"]["shown"] > 0
    assert {key: value for key, value in payload.items() if key != "manifest_type_entry_links"} == before
    assert context_budget_profile(render_agent_inspection_brief(payload), budget_tokens=COMPACT_AGENT_PACKET_TOKENS)["status"] == "pass"
    oversized = {**before, "retained_existing_detail": "x" * 30000}
    project_manifest_source_links(oversized, files["src/index.ts"], "MAIN", "src/index.ts")
    assert "manifest_type_entry_links" not in oversized and oversized["one_shot_edit_ready"]


@pytest.mark.parametrize("prior_records", [5, 6])
def test_additive_lane_uses_remaining_shared_record_budget(source_inputs, prior_records):
    _, _, _, files, _ = derive(source_inputs)
    older = {"existing_syntax": {"records": [{"source_role": str(i)} for i in range(prior_records)]}}
    payload = {"target": {"kind": "file", "value": "MAIN::src/index.ts"}, "summary": {}, **deepcopy(older)}
    project_manifest_source_links(payload, files["src/index.ts"], "MAIN", "src/index.ts", syntax_sections=older)
    section = payload["manifest_type_entry_links"]
    assert section["shown"] == 6 - prior_records and section["shown"] + section["omitted"] == 2
    assert payload["existing_syntax"] == older["existing_syntax"]
    assert context_budget_profile(json.dumps({**older, "manifest_type_entry_links": section}), budget_tokens=COMPACT_AGENT_PACKET_TOKENS)["status"] == "pass"


@pytest.mark.parametrize("manifest_prefix", [b"", b"\xef\xbb\xbf"], ids=["utf8", "utf8-bom"])
def test_actual_producer_all_lifts_surgical_clear_and_fingerprints(tmp_path, monkeypatch, manifest_prefix):
    from tools.core import config
    root, raw = tmp_path / "target", tmp_path / ".raw"
    root.mkdir(); raw.mkdir()
    manifest_data = manifest_prefix + b'{"types":"./index.ts"}'
    (root / "package.json").write_bytes(manifest_data)
    (root / "index.ts").write_bytes(b"export const value = 1;\r\nthrow new Error('Do not execute');\r\n")
    store = ArtifactStore(raw_dir=raw); store.use_sqlite = True; store._ensure_schema()
    monkeypatch.setattr(generator, "ROOT", root); monkeypatch.setattr(generator, "RAW_DIR", raw)
    monkeypatch.setattr(generator, "resolve_runtime_projects", lambda *_a, **_k: {"MAIN": root})
    monkeypatch.setattr(generator, "configured_project_ownership_exclusions", lambda *_a, **_k: {"MAIN": []})
    monkeypatch.setattr(generator, "load_previous_atlas", lambda: {})
    monkeypatch.setattr(generator, "ensure_output_dir", lambda: None)
    monkeypatch.setattr(generator, "save_json_atomic", lambda path, payload, **kw: store.save_raw(Path(path).stem, payload, **kw))
    monkeypatch.setattr(generator, "DYNAMIC_CONFIG", {"use_sqlite": True})
    monkeypatch.setattr(artifact_store, "STORE", store); monkeypatch.setattr(config, "PROJECT_FILTER", [])
    monkeypatch.setattr(artifact_store, "ROOT", root)
    monkeypatch.setattr(artifact_store, "DYNAMIC_CONFIG", {"variations": {"MAIN": "."}})
    monkeypatch.setenv("SAGE_SYNC_SHADOW_WRITES", "1")
    writer = generator.save_json_atomic
    with monkeypatch.context() as interrupted:
        def fail_before_canonical_write(path, payload, **kwargs):
            if Path(path).stem == "atlas":
                raise RuntimeError("isolated interruption before canonical Atlas write")
            return writer(path, payload, **kwargs)
        interrupted.setattr(generator, "save_json_atomic", fail_before_canonical_write)
        with pytest.raises(RuntimeError, match="isolated interruption"):
            generator.generate_atlas(stale_projects=["MAIN"])
    staged = store.load_atlas_staging_files(generator.atlas_staging_producer_contract(), stage_kind="atlas_file")
    assert links.manifest_source_link_group_valid(staged[("MAIN", "index.ts")]["manifest_type_entry_links"], staged[("MAIN", "index.ts")], "MAIN", "index.ts")
    with monkeypatch.context() as resumed:
        resumed.setattr(subprocess, "Popen", lambda *_a, **_k: pytest.fail("Staged source must not start a parser process again"))
        first, _, _ = generator.generate_atlas(stale_projects=["MAIN"])
    original = deepcopy(first)
    file = first["MAIN"]["files"]["index.ts"]
    assert links.manifest_source_link_group_valid(file["manifest_type_entry_links"], file, "MAIN", "index.ts")
    assert file["manifest_type_entry_links"]["records"][0]["source_manifest_sha256"] == hashlib.sha256(manifest_data).hexdigest()
    from tools.core.artifact_validator import ensure_valid_payload
    ensure_valid_payload("atlas", first)
    monkeypatch.setattr(server, "_raw_dir_for_target", lambda *_a: raw)
    monkeypatch.setattr(server, "_reports_dir_for_target", lambda *_a: tmp_path / "reports")
    monkeypatch.setattr(server, "_analysis_root_display", lambda *_a: str(root))
    # Real ordinary target/snapshot/inspection owners, without a synthetic path bridge.
    inspected = json.loads(server.inspect_file("MAIN::index.ts", target_root=str(root), format="json"))
    assert inspected["manifest_type_entry_links"]["shown"] == 1
    assert inspected["manifest_type_entry_links"]["records"][0]["target_literal"] == "./index.ts"
    assert inspected["target_path_status"]["source_snapshot_status"] == "ok"
    assert inspected["target_path_status"]["drift_check_status"] == "match"
    with monkeypatch.context() as no_links:
        no_links.setattr(server, "project_manifest_source_links", lambda *_a, **_k: None)
        baseline = json.loads(server.inspect_file("MAIN::index.ts", target_root=str(root), format="json"))
    for key in ("source_grounded_for_inspection", "one_shot_edit_ready", "target_files", "target_path_status"):
        assert inspected.get(key) == baseline.get(key)
    (tmp_path / "manifest_source_manual_sample.json").write_text(json.dumps({
        "source_hash": file["hash"], "source_identity": file["manifest_type_entry_links"]["source_text_identity"],
        "manifest_hash": file["manifest_type_entry_links"]["records"][0]["source_manifest_sha256"],
        "group": file["manifest_type_entry_links"], "inspection": inspected["manifest_type_entry_links"],
        "snapshot": inspected["target_path_status"]["source_snapshot_status"],
        "drift": inspected["target_path_status"]["drift_check_status"],
        "existing_edit_authority_unchanged": True, "target_code_executed": False}), encoding="utf-8")
    assert staged[("MAIN", "index.ts")]["manifest_type_entry_links"] == file["manifest_type_entry_links"]
    assert store.load_atlas_staging_files(generator.atlas_staging_producer_contract(), stage_kind="atlas_file") == {}
    with monkeypatch.context() as reuse_only:
        reuse_only.setattr(subprocess, "Popen", lambda *_a, **_k: pytest.fail("Settled source must reuse its existing parser/file proof"))
        monkeypatch.setattr(generator, "load_previous_atlas", lambda: first)
        cached, _, _ = generator.generate_atlas()
        assert cached["MAIN"]["files"]["index.ts"] == file
    surgical, _, _ = generator.generate_atlas(stale_projects=["MAIN"], surgical_files=["MAIN::absent.ts"])
    assert "manifest_type_entry_links" not in surgical["MAIN"]["files"]["index.ts"]
    assert first == original
    from tools.core import atlas_integrity
    original_read = Path.read_bytes
    expected = (generator.atlas_staging_producer_contract(), atlas_integrity.generator_fingerprint())
    for helper in ("manifest_source_links.py", "package_contracts.py"):
        with monkeypatch.context() as changed:
            def changed_helper(path):
                value = original_read(path)
                return value + b"\n# changed helper" if path.name == helper else value
            changed.setattr(Path, "read_bytes", changed_helper)
            actual = (generator.atlas_staging_producer_contract(), atlas_integrity.generator_fingerprint())
            assert all(a != b for a, b in zip(expected, actual))
