"""Atlas declaration-only MCP orientation, never implementation/edit proof."""
from contextlib import closing
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess

import pytest

from tools.core import artifact_store, atlas_io, atlas_typescript_inputs as inputs
from tools.core.agent_packet_budget import COMPACT_AGENT_PACKET_TOKENS, context_budget_profile
from tools.core.artifact_store import ArtifactStore
from tools.core.declaration_contract_view import declaration_file_inspection
from tools.mcp import server


@pytest.fixture(scope="module")
def native_plane(tmp_path_factory):
    root = tmp_path_factory.mktemp("declaration-inspection")
    names = ["index.d.ts", "index.d.mts", "index.d.cts"]
    for name in names:
        (root / name).write_bytes(("\ufeff// ç 🌍\r\nexport interface Contract { value: string }\r\n"
            "export type Alias = Contract;\r\ndeclare const value: number;\r\n"
            "export declare function run(): void;\r\nexport declare class Client {}\r\n"
            "export { Contract as PublicContract } from './types';\r\n"
            "declare global { interface Window { extra: string } }\r\n").encode("utf-8"))
    capture = inputs.capture_auxiliary_inputs(root, [(str(root / name), name, name) for name in names])
    command = ["node", str(Path(__file__).resolve().parents[2] / "tools/engines/ast_sequencer.cjs"),
        "--batch-json", *inputs.declaration_input_paths(root, capture)]
    response = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert response.returncode == 0, response.stderr
    return inputs.build_declaration_contract_evidence(root, capture, json.loads(response.stdout))


def store_plane(root, plane, *, partitioned=False, project="MAIN"):
    store = ArtifactStore(raw_dir=root / ".raw")
    store.use_sqlite, store.backend = True, "hybrid_sqlite"
    store._ensure_schema()
    if partitioned:
        store.state_payload_inline_limit_bytes = 1
    store._save_payload_to_state_table("atlas", {project: {"declaration_contract_evidence": plane,
        "files": {"implementation.ts": {"hash": "ordinary"}}}, "OTHER": {"declaration_contract_evidence": {"poisoned": True}}})
    return store


def route(monkeypatch, store):
    monkeypatch.setattr(server, "_raw_dir_for_target", lambda _target: store._raw_dir)
    monkeypatch.setattr(server, "_reports_dir_for_target", lambda _target: store._raw_dir.parent / "reports")
    for name in ("_sqlite_file_inspection_payload", "write_inspection", "_add_inspection_grounding_status"):
        monkeypatch.setattr(server, name, lambda *_a, **_k: pytest.fail("Declaration must not use implementation fallback or grounding"))
    monkeypatch.setattr(artifact_store, "ArtifactStore", lambda **_kw: store)
    monkeypatch.setattr(atlas_io, "load_atlas_data_with_identity", lambda *_a: pytest.fail("Do not materialize full Atlas"))


def inspect(ref="MAIN::index.d.ts", **kwargs):
    return json.loads(server.inspect_file(ref, format="json", **kwargs))


@pytest.mark.parametrize("mode", ["inline", "partitioned", "json"])
@pytest.mark.parametrize("suffix", [".d.ts", ".d.mts", ".d.cts"])
def test_native_headers_reach_inspect_file_without_source_or_edit_authority(tmp_path, monkeypatch, native_plane, mode, suffix):
    store = store_plane(tmp_path, native_plane, partitioned=mode == "partitioned")
    if mode == "json":
        store.use_sqlite = False
        monkeypatch.setattr(atlas_io, "RAW_DIR", store._raw_dir)
        monkeypatch.setattr(artifact_store, "STORE", store)
        (store._raw_dir / "atlas.json").write_bytes(("\ufeff" + json.dumps(
            {"MAIN": {"declaration_contract_evidence": native_plane}}, ensure_ascii=False, indent=2).replace("\n", "\r\n")).encode("utf-8"))
    route(monkeypatch, store)
    out = inspect("MAIN::index" + suffix)
    view, source = out["declaration_contract"], native_plane["files"]["index" + suffix]
    assert view["status"] == "partial" and view["project_status"] == "partial"
    assert view["captured_input_sha256"] == source["parser_input_sha256"] and view["records"][0] == source["records"][0]
    assert view["shown"] + view["omitted"] == source["record_count"] == 7
    assert view["uninspected_module_bodies"] == 1 and view["offset_unit"] == "utf8_bytes"
    assert out["stored_atlas_identity"].startswith("json:" if mode == "json" else "sqlite:")
    assert out["source_binding"] == "stored_declaration_metadata_only" and out["live_source_binding"] == "not_checked"
    assert out["target_files"] == [] and out["source_grounded_for_inspection"] is False
    assert out["one_shot_edit_ready"] is False and out["mutation_proposed"] is False
    assert out["runtime_execution"] == out["semantic_type_safety"] == "not_established"
    assert not any(key in out for key in ("summary", "symbols", "dependencies", "loc", "target_file", "target_ref", "snippets"))
    assert out["declaration_ref"] == "MAIN::index" + suffix
    for format in ("brief", "brief_debug", "md"):
        brief = server.inspect_file("MAIN::index" + suffix, format=format)
        assert "stored_declaration_metadata_only" in brief and "Contract" in brief
        assert "dead_code_matches" not in brief and "open_files_with" not in brief
        assert context_budget_profile(brief, budget_tokens=COMPACT_AGENT_PACKET_TOKENS)["status"] == "pass"


@pytest.mark.parametrize("ref", ["index.d.ts", "::index.d.ts", "MAIN::../index.d.ts", "MAIN::/index.d.ts",
    "MAIN::C:/index.d.ts", "MAIN::types\\index.d.ts", "MAIN::types//index.d.ts", "MAIN::./index.d.ts",
    "\ud800::index.d.ts", "MAIN::\ud800.d.ts"])
def test_invalid_declaration_scope_never_reads_or_falls_back(tmp_path, monkeypatch, ref):
    monkeypatch.setattr("tools.core.declaration_contract_view.load_atlas_declaration_metadata",
        lambda *_a: pytest.fail("Rejected scope must not read artifacts"))
    out = declaration_file_inspection(tmp_path, ref, record_limit=6)
    assert out["declaration_contract"]["status"] == "unavailable" and out["declaration_contract"]["record_count"] is None


@pytest.mark.parametrize("line_start,line_end", [(1, 0), (0, 3), (-1, 0), (1, 2)])
def test_line_requests_do_not_turn_bytes_into_live_snippets(tmp_path, monkeypatch, native_plane, line_start, line_end):
    store = store_plane(tmp_path, native_plane)
    route(monkeypatch, store)
    monkeypatch.setattr("tools.core.declaration_contract_view.load_atlas_declaration_metadata",
        lambda *_a: pytest.fail("Metadata line range is unsupported"))
    out = inspect(line_start=line_start, line_end=line_end)
    assert out["declaration_contract"]["reason"] == "source_line_range_not_available_for_declaration_metadata"
    assert out["one_shot_edit_ready"] is False


@pytest.mark.parametrize("damage", ["missing", "legacy", "corrupt", "budget"])
def test_primary_refusal_cannot_promote_shadow_or_host(tmp_path, monkeypatch, native_plane, damage):
    store = store_plane(tmp_path, native_plane, partitioned=True)
    route(monkeypatch, store)
    (store._raw_dir / "atlas.json").write_text(json.dumps({"MAIN": {"declaration_contract_evidence": native_plane}}), encoding="utf-8")
    with closing(sqlite3.connect(store.db_manager.db_path)) as conn, conn:
        if damage == "missing": conn.execute("DELETE FROM state_payloads WHERE name='atlas'")
        if damage == "legacy": conn.execute("UPDATE state_payloads SET payload_sha=NULL WHERE name='atlas'")
        if damage == "corrupt": conn.execute("UPDATE state_payloads SET payload_sha=? WHERE name='atlas'", ("0" * 64,))
    if damage == "budget": store.atlas_staging_file_aggregate_limit_bytes = 1
    out = inspect()
    assert out["declaration_contract"]["status"] == "unavailable" and out["stored_atlas_identity"] is None
    assert out["declaration_contract"]["records"] == []


def test_explicit_default_json_does_not_borrow_residual_database(tmp_path, monkeypatch, native_plane):
    store = store_plane(tmp_path, native_plane)
    store.use_sqlite = False
    plane = deepcopy(native_plane)
    plane["files"]["index.d.ts"]["records"][0]["names"] = ["JSONPrimary"]
    content = json.dumps({"MAIN": {"declaration_contract_evidence": plane}}, ensure_ascii=False).encode("utf-8")
    (store._raw_dir / "atlas.json").write_bytes(content)
    monkeypatch.setattr(atlas_io, "RAW_DIR", store._raw_dir)
    monkeypatch.setattr(artifact_store, "STORE", store)
    monkeypatch.setattr(store, "load_sqlite_atlas_declaration_metadata", lambda *_a: pytest.fail("Explicit JSON primary"))
    result = declaration_file_inspection(store._raw_dir, "MAIN::index.d.ts", record_limit=6)
    assert result["declaration_contract"]["records"][0]["names"] == ["JSONPrimary"]
    assert result["stored_atlas_identity"] == "json:" + hashlib.sha256(content).hexdigest()


@pytest.mark.parametrize("mode", ["sqlite", "json"])
@pytest.mark.parametrize("mutation", ["plane_version", "plane_policy", "plane_runtime", "selected_bool", "selected_count",
    "omitted_bool", "observed_omissions", "unavailable_files", "plane_missing", "file_missing", "version", "policy",
    "hash", "size", "counter", "bool_span", "overlap", "kind", "extra_record", "extra_file", "surrogate", "runtime",
    "status_list", "reason_code", "observed_partial", "name_count", "parser_version_surrogate", "root_surrogate"])
def test_stored_metadata_requires_current_strict_shape(tmp_path, monkeypatch, native_plane, mutation, mode):
    plane = deepcopy(native_plane)
    value = plane["files"]["index.d.ts"]
    if mutation == "plane_version": plane["version"] = "old"
    elif mutation == "plane_policy": plane["policy_sha256"] = "0" * 64
    elif mutation == "plane_runtime": plane["runtime_execution"] = "confirmed"
    elif mutation == "selected_bool": plane["selected_files"] = True
    elif mutation == "selected_count": plane["selected_files"] += 1
    elif mutation == "omitted_bool": plane["omitted_files"] = True
    elif mutation == "observed_omissions": plane.update(status="observed", omitted_files=1)
    elif mutation == "unavailable_files": plane["status"] = "unavailable"
    elif mutation == "plane_missing": plane = None
    elif mutation == "file_missing": plane["files"].pop("index.d.ts"); plane["selected_files"] -= 1
    elif mutation == "version": value["version"] = "old"
    elif mutation == "policy": value["policy_sha256"] = "0" * 64
    elif mutation == "hash": value["parser_input_sha256"] = 12
    elif mutation == "size": value["size_bytes"] = -1
    elif mutation == "counter": value["record_count"] += 1
    elif mutation == "bool_span": value["records"][0]["start_byte"] = True
    elif mutation == "overlap": value["records"][1]["start_byte"] = 0
    elif mutation == "kind": value["records"][0]["syntax_kind"] = "RuntimeImplementation"
    elif mutation == "extra_record": value["records"][0]["secret"] = "must_not_leak"
    elif mutation == "extra_file": value["source_text"] = "must_not_leak"
    elif mutation == "surrogate": value["records"][0]["names"] = ["\ud800"]
    elif mutation == "runtime": value["runtime_execution"] = "confirmed"
    elif mutation == "status_list": value["status"] = []
    elif mutation == "reason_code": value["reasons"] = {"unknown": 1}
    elif mutation == "observed_partial": value["status"] = "observed"
    elif mutation == "name_count": value["records"][0]["names"] = ["x"] * 100
    elif mutation == "parser_version_surrogate": value["parser_version"] = "\ud800"
    elif mutation == "root_surrogate": plane["project_root"] = "\ud800"
    store = store_plane(tmp_path, plane)
    if mutation in {"surrogate", "parser_version_surrogate", "root_surrogate"}:
        # The ordinary writer replaces non-UTF8 Python text. Preserve the
        # escaped malformed string in actual primary JSON to test the reader.
        content = json.dumps({"MAIN": {"declaration_contract_evidence": plane}}, ensure_ascii=True)
        with closing(sqlite3.connect(store.db_manager.db_path)) as conn, conn:
            conn.execute("UPDATE state_payloads SET payload=?,payload_sha=?,payload_bytes=?,storage_mode='inline_json' WHERE name='atlas'",
                (content, hashlib.sha256(content.encode("utf-8")).hexdigest(), len(content.encode("utf-8"))))
    if mode == "json":
        store.use_sqlite = False
        monkeypatch.setattr(atlas_io, "RAW_DIR", store._raw_dir)
        monkeypatch.setattr(artifact_store, "STORE", store)
        (store._raw_dir / "atlas.json").write_bytes(json.dumps(
            {"MAIN": {"declaration_contract_evidence": plane}}, ensure_ascii=True).encode("ascii"))
    route(monkeypatch, store)
    out = inspect()
    assert out["declaration_contract"]["status"] == "unavailable" and out["declaration_contract"]["records"] == []
    assert "must_not_leak" not in json.dumps(out)


def test_inspection_never_reads_target_text_or_executes_parser(tmp_path, monkeypatch, native_plane):
    store = store_plane(tmp_path, native_plane)
    route(monkeypatch, store)
    original = Path.read_bytes
    source_root = Path(native_plane["project_root"])
    def read_bytes(path):
        assert not path.is_relative_to(source_root), "Stored metadata is not a live source read"
        return original(path)
    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_kw: pytest.fail("Do not execute target/parser"))
    out = inspect()
    assert out["source_binding"] == "stored_declaration_metadata_only" and out["live_source_binding"] == "not_checked"


def test_header_budget_refuses_oversized_query_without_echo_or_read(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.core.declaration_contract_view.load_atlas_declaration_metadata",
        lambda *_a: pytest.fail("Over-budget query must not read"))
    out = declaration_file_inspection(tmp_path, "X" * 10000 + "::index.d.ts", record_limit=6)
    assert out["declaration_ref"] == "" and out["declaration_contract"]["reason"] == "query_header_over_budget"


def test_exact_unicode_project_and_target_routing_reloads_each_request(tmp_path, monkeypatch, native_plane):
    key = 'LibÇ"🚀'
    a = store_plane(tmp_path / "a", native_plane, project=key)
    changed = deepcopy(native_plane)
    changed["files"]["index.d.ts"]["records"][0]["names"] = ["OtherTarget"]
    b = store_plane(tmp_path / "b", changed, project=key)
    stores = {"target-a": a, "target-b": b}
    monkeypatch.setattr(server, "_raw_dir_for_target", lambda target: stores[target]._raw_dir)
    monkeypatch.setattr(server, "_reports_dir_for_target", lambda target: stores[target]._raw_dir.parent / "reports")
    outputs = [inspect(key + "::index.d.ts", target_root=target) for target in ("target-a", "target-b", "target-a")]
    assert [o["declaration_contract"]["records"][0]["names"] for o in outputs] == [["Contract"], ["OtherTarget"], ["Contract"]]
    assert outputs[0]["stored_atlas_identity"] != outputs[1]["stored_atlas_identity"]
    assert inspect(key.lower() + "::index.d.ts", target_root="target-a")["declaration_contract"]["status"] == "unavailable"
    a._save_payload_to_state_table("atlas", {key: {"declaration_contract_evidence": {**native_plane,
        "status": "unavailable", "files": {}, "selected_files": 0, "reason": "auxiliary_scope_unavailable"}}})
    assert inspect(key + "::index.d.ts", target_root="target-a")["declaration_contract"]["reason"] == "declaration_plane_unavailable"


def test_empty_finite_scope_and_partial_project_stay_distinct(tmp_path, monkeypatch, native_plane):
    plane = deepcopy(native_plane)
    file = plane["files"]["index.d.ts"]
    file.update(status="observed", records=[], record_count=0, omitted_statements=0, omitted_name_nodes=0,
        uninspected_module_bodies=0, reasons={})
    plane["omitted_files"] = None
    store = store_plane(tmp_path, plane)
    route(monkeypatch, store)
    view = inspect()["declaration_contract"]
    assert view["status"] == "observed" and view["project_status"] == "partial"
    assert view["omitted_files"] is None and view["record_count"] == 0 and view["records"] == []


def test_large_header_display_is_bounded_and_keeps_omission_units(tmp_path, monkeypatch, native_plane):
    plane = deepcopy(native_plane)
    plane["files"]["index.d.ts"]["records"][0]["names"] = ["x" * 512] * 16
    store = store_plane(tmp_path, plane)
    route(monkeypatch, store)
    view = inspect()["declaration_contract"]
    assert view["shown"] == 0 and view["omitted"] == 7 and view["uninspected_module_bodies"] == 1
    assert context_budget_profile(server.inspect_file("MAIN::index.d.ts"), budget_tokens=COMPACT_AGENT_PACKET_TOKENS)["status"] == "pass"


def test_ordinary_file_keeps_existing_inspection_route(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(server, "_raw_dir_for_target", lambda *_a: tmp_path)
    monkeypatch.setattr(server, "_reports_dir_for_target", lambda *_a: tmp_path)
    monkeypatch.setattr(server, "_sqlite_file_inspection_payload", lambda *_a, **_kw: calls.append("ordinary") or {})
    monkeypatch.setattr(server, "_add_agent_inspection_path_fields", lambda *_a: None)
    monkeypatch.setattr(server, "_add_inspection_grounding_status", lambda *_a, **_kw: None)
    out = inspect("MAIN::implementation.ts")
    assert calls == ["ordinary"] and "inspection_plane" not in out
