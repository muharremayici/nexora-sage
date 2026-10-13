from __future__ import annotations

import hashlib
import inspect
import json
from copy import deepcopy
import os
from pathlib import Path

import pytest

from tools.core.artifact_store import ArtifactStore
from tools.core.db import SQLiteManager
from tools.core import source_evidence, source_snapshot_reader
from tools.core.source_snapshot_integrity import snapshot_content_status, source_text_hash
from tools.core.unmanaged_atomic_io import native_filesystem_path
from tools.mcp import server as mcp_server

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def local_projection_inputs(tmp_path_factory):
    from tools.engines.generate_atlas import AST_CONTRACT_VERSION, _client_local_parser_result_evidence, _client_local_parser_input_sink_evidence
    from tools.tests.test_react_contracts import _local_parser_result_symbols
    import hashlib

    fixture = json.loads((ROOT / "config/target_runtime_schema_mutation_advisory_fixtures.json").read_text(encoding="utf-8"))
    result = {}
    for name in ("result_field", "local_passthrough_parse_name", "discarded_result_raw_sink"):
        source = next(row["source"] for row in fixture["local_parser_result_cases"] if row["id"] == name)
        source = "\ufeff// \U0001f680 source identity\r\n" + source.replace("\n", "\r\n")
        target = tmp_path_factory.mktemp("local-projection") / "Panel.tsx"
        symbols = _local_parser_result_symbols(source, target)
        evidence = _client_local_parser_result_evidence(symbols, source)
        input_evidence = _client_local_parser_input_sink_evidence(symbols, source)
        identity = hashlib.sha256(source.encode("utf-8")).hexdigest()
        result[name] = (source, {"hash": identity, "ast_contract_version": AST_CONTRACT_VERSION,
            "client_local_parser_result_evidence": evidence, "client_local_parser_input_sink_evidence": input_evidence})
    return result


@pytest.fixture(scope="module")
def imported_parser_atlas_run(tmp_path_factory):
    """One shared real AST/Atlas run; never execute the target modules."""
    import subprocess
    import sys
    from tools.core.atlas_io import load_atlas_data
    from tools.core.external_target_generation import external_target_output_slug

    fixture = json.loads((ROOT / "config/target_runtime_schema_mutation_advisory_fixtures.json").read_text(encoding="utf-8"))
    target = tmp_path_factory.mktemp("imported-parser-e2e")
    sources = {"Panel.tsx": fixture["imported_parser_cases"][0]["source"],
               "Input.tsx": fixture["imported_parser_cases"][1]["source"],
               "schema.ts": "export const Schema = {parse(value: unknown){ return value; }};\n",
               "adapter.ts": fixture["response_json_return_cases"][2]["source"]}
    sources["Caller.tsx"] = """import { load as readTables } from './adapter';
import { loadFn, useArrow, useFunction } from './functions';
export function Outer() {
  const handleTable = async () => { const tables = await readTables(); return tables; };
  return null;
}
export function Direct() { return readTables(); }
export function DirectFn() { return loadFn(); }
export function DirectHookArrow() { return useArrow(); }
export function DirectHookFunction() { return useFunction(); }
export function Guarded() {
  const setTables = (value: unknown) => {};
  const setRows = (value: unknown) => {};
  const handleTable = async () => { const data = await readTables(); if(data.tables) { setTables(data.tables); } };
  const handleRows = async () => { const result = await readTables(); if(result.rows) { setRows(result.rows); } };
  return null;
}
throw new Error('Source orientation must never execute this module');
"""
    sources["functions.ts"] = """export async function loadFn() { const response = await fetch('/items'); const raw = await response.json(); return raw.rows; }
export async function useFunction() { const response = await fetch('/items'); const raw = await response.json(); return raw.items; }
export const useArrow = async () => { const response = await fetch('/items'); const raw = await response.json(); return raw; };
"""
    sources["multi.ts"] = "\n".join(
        f"export const load{i} = async () => {{ const response = await fetch('/items'); const raw = await response.json(); return raw.slot{i}; }};"
        for i in range(8))
    for file, source in sources.items():
        (target / file).write_bytes(("\ufeff// \U0001f680\r\n" + source.replace("\n", "\r\n")).encode("utf-8"))
    env = dict(os.environ)
    for key in ("CODEMAPS_TARGET_PREFLIGHT_RECEIPT", "CODEMAPS_TARGET_PREFLIGHT_RECEIPT_SHA256",
                "CODEMAPS_TARGET_PREFLIGHT_REUSE_MODE", "CODEMAPS_TARGET_PROJECTS"):
        env.pop(key, None)
    env["CODEMAPS_TARGET_ROOT"] = str(target)
    run = subprocess.run([sys.executable, "-B", "-m", "tools.engines.generate_atlas"],
        cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", timeout=90)
    assert run.returncode == 0, run.stderr or run.stdout
    raw = ROOT / "output/external_targets" / external_target_output_slug(str(target)) / ".raw"
    return target, raw, load_atlas_data(raw)


@pytest.mark.parametrize(("file", "origin"), [("Panel.tsx", "parser_call_result"), ("Input.tsx", "json_input")])
def test_imported_parser_real_atlas_sqlite_inspect_machine_brief_and_capped_packet(imported_parser_atlas_run, monkeypatch, file, origin):
    from tools.core.agent_packet_budget import COMPACT_AGENT_PACKET_TOKENS, context_budget_profile
    target, raw, atlas = imported_parser_atlas_run
    mcp_server._ATLAS_CACHE.clear()
    monkeypatch.setattr(mcp_server, "_atlas", lambda *a, **k: pytest.fail("Imported parser inspect must not materialize Atlas"))
    machine = json.loads(mcp_server.inspect_file(f"MAIN::{file}", format="json", target_root=str(target)))
    evidence = machine["client_imported_parser_evidence"]
    assert evidence["status"] == "observed" and evidence["record_count"] == evidence["shown"] == 1
    row = evidence["records"][0]
    assert row["selected_value_origin"] == origin
    candidate = row["parser_source_candidate"]
    assert candidate["status"] == "source_candidate" and candidate["source_binding"] == "snapshot_and_live_match"
    assert candidate["target"]["atlas_ref"] == "MAIN::schema.ts"
    assert candidate["target"]["source_hash"] == atlas["MAIN"]["files"]["schema.ts"]["hash"]
    assert candidate["target"]["symbol"] == "Schema" and candidate["source_followup"]["tool"] == "inspect_file"
    assert evidence["schema_authority"] == evidence["parser_effect_on_input"] == "not_established"
    assert evidence["runtime_proof"] == "not_performed" and evidence["edit_authority"] == "not_granted"
    sections = {key: value for key, value in machine.items() if key.startswith("client_") and key.endswith("_evidence")}
    assert context_budget_profile(json.dumps(sections, ensure_ascii=False), budget_tokens=COMPACT_AGENT_PACKET_TOKENS)["status"] == "pass"
    brief = mcp_server.inspect_file(f"MAIN::{file}", target_root=str(target))
    line = next(line for line in brief.splitlines() if line.startswith("client_imported_parser_evidence: "))
    assert json.loads(line.split(": ", 1)[1]) == evidence
    assert not machine["one_shot_edit_ready"] and machine["inspection_authority"] == "orientation_only"
    focused = json.loads(mcp_server.inspect_file(f"MAIN::{file}", format="json", target_root=str(target), line_start=1, line_end=1))
    assert focused["client_imported_parser_evidence"] == evidence


@pytest.mark.parametrize("damage", ["none", "wrong_name", "missing_module", "cross_project", "traversal", "ambiguous_module",
    "barrel", "duplicate_export", "private_export", "target_hash", "target_ast", "target_parser", "target_span", "candidate_drift",
    "caller_drift", "corrupt_primary"])
@pytest.mark.parametrize("partitioned", [False, True], ids=["inline", "partitioned"])
def test_imported_parser_candidate_and_primary_source_fail_closed(imported_parser_atlas_run, tmp_path, monkeypatch, damage, partitioned):
    from tools.core.agent_packet_budget import COMPACT_AGENT_PACKET_TOKENS, context_budget_profile
    import sqlite3
    target, _, original = imported_parser_atlas_run
    local = tmp_path / "target"
    local.mkdir()
    atlas = deepcopy(original)
    files = atlas["MAIN"]["files"]
    for file in files:
        (local / file).write_bytes((target / file).read_bytes())
    atlas["MAIN"]["root_path"] = str(local)
    caller, candidate = files["Panel.tsx"], files["schema.ts"]
    imports = [row for row in caller["import_records"] if row["raw_source"] == "./schema"]
    assert len(imports) == 1
    if damage == "wrong_name": imports[0]["name"] = "Other"
    elif damage == "missing_module": imports[0]["source"] = "absent.ts"
    elif damage == "cross_project": imports[0]["source"] = "OTHER::schema.ts"
    elif damage == "traversal": imports[0]["source"] = "../schema.ts"
    elif damage == "ambiguous_module": caller["import_records"].append({**imports[0], "source": "other.ts"})
    elif damage == "barrel": candidate["symbols"][0]["type"] = "ReExportedSymbol"
    elif damage == "duplicate_export": candidate["symbols"].append(deepcopy(candidate["symbols"][0]))
    elif damage == "private_export": candidate["symbols"][0]["exported"] = False
    elif damage == "target_hash": candidate["hash"] = "0" * 64
    elif damage == "target_ast": candidate["ast_contract_version"] = "old"
    elif damage == "target_parser": candidate["parser_evidence"]["status"] = "degraded"
    elif damage == "target_span": candidate["symbols"][0]["end_line"] = 10000
    raw = tmp_path / ".raw"
    store = ArtifactStore(raw_dir=raw)
    store.use_sqlite, store.backend = True, "hybrid_sqlite"
    store._ensure_schema()
    if partitioned: store.state_payload_inline_limit_bytes = 1
    store._save_payload_to_state_table("atlas", atlas)
    store._save_atlas_to_sqlite(atlas)
    with sqlite3.connect(raw / "codemaps.db") as conn:
        conn.execute("UPDATE projects SET path = '.' WHERE project_key='MAIN'")
        if damage == "corrupt_primary":
            if partitioned: conn.execute("DELETE FROM state_payload_parts WHERE name='atlas'")
            else: conn.execute("UPDATE state_payloads SET payload='broken' WHERE name='atlas'")
    if damage in {"candidate_drift", "caller_drift"}:
        file = local / ("schema.ts" if damage == "candidate_drift" else "Panel.tsx")
        file.write_bytes(file.read_bytes() + b"\n// changed")
    mcp_server._ATLAS_CACHE.clear()
    monkeypatch.setattr(mcp_server, "_atlas", lambda *a, **k: pytest.fail("Whole Atlas materialized"))
    monkeypatch.setattr(mcp_server, "_raw_dir_for_target", lambda root: raw)
    monkeypatch.setattr(mcp_server, "_reports_dir_for_target", lambda root: tmp_path / "reports")
    monkeypatch.setattr(mcp_server, "_analysis_root_display", lambda root: str(local))
    machine = json.loads(mcp_server.inspect_file("MAIN::Panel.tsx", format="json", target_root=str(local)))
    evidence = machine.get("client_imported_parser_evidence")
    if damage == "corrupt_primary":
        assert evidence is None, "Missing new metadata is not observed-empty or rescued from JSON"
        assert machine["client_local_parser_result_evidence"]["reason"] == "primary_file_metadata_read_failed"
    elif damage == "caller_drift":
        assert evidence["status"] == "unavailable" and evidence["records"] == []
    else:
        assert evidence["status"] == "observed" and evidence["shown"] == 1
        source_candidate = evidence["records"][0]["parser_source_candidate"]
        assert source_candidate["status"] == ("source_candidate" if damage == "none" else "unresolved")
        if damage != "none": assert "target" not in source_candidate and source_candidate["reason"]
    sections = {key: value for key, value in machine.items() if key.startswith("client_") and key.endswith("_evidence")}
    assert context_budget_profile(json.dumps(sections, ensure_ascii=False), budget_tokens=COMPACT_AGENT_PACKET_TOKENS)["status"] == "pass"


@pytest.mark.parametrize("damage", ["origin", "origin_list", "type_only", "source", "missing_bindings", "duplicate_bindings", "schema_authority", "ast_contract_version", "runtime_proof", "offset_unit"])
def test_imported_parser_projection_refuses_corrupt_stored_binding_or_promoted_authority(imported_parser_atlas_run, damage):
    from tools.inspect_target import project_client_local_parser_evidence
    _, _, atlas = imported_parser_atlas_run
    info = deepcopy(atlas["MAIN"]["files"]["Panel.tsx"])
    evidence = info["client_imported_parser_evidence"]
    if damage == "origin": evidence["records"][0]["selected_value_origin"] = "validated"
    elif damage == "origin_list": evidence["records"][0]["selected_value_origin"] = []
    elif damage == "type_only": evidence["records"][0]["import_binding"]["type_only"] = True
    elif damage == "source": evidence["records"][0]["import_binding"]["source"] = "./other"
    elif damage == "missing_bindings": info.pop("direct_import_binding_evidence")
    elif damage == "duplicate_bindings": info["direct_import_binding_evidence"]["records"] *= 2
    else: evidence[damage] = "established"
    projected = project_client_local_parser_evidence(info, _local_projection_status(info))["client_imported_parser_evidence"]
    assert projected["status"] == "unavailable" and projected["records"] == [] and projected["record_count"] is None
    assert projected["schema_authority"] == "not_established" and projected["runtime_proof"] == "not_performed"


def test_imported_parser_projection_shares_cap_and_does_not_cut_paths_or_enriched_candidates(imported_parser_atlas_run):
    from tools.inspect_target import project_client_local_parser_evidence, AGENT_INSPECTION_MAX_TARGET_SPANS
    from tools.core.agent_packet_budget import COMPACT_AGENT_PACKET_TOKENS, context_budget_profile
    _, _, atlas = imported_parser_atlas_run
    info = deepcopy(atlas["MAIN"]["files"]["Panel.tsx"])
    info["client_imported_parser_evidence"]["records"] *= 10
    sections = project_client_local_parser_evidence(info, _local_projection_status(info))
    evidence = sections["client_imported_parser_evidence"]
    assert 0 < evidence["shown"] <= AGENT_INSPECTION_MAX_TARGET_SPANS
    assert evidence["omitted"] == evidence["record_count"] - evidence["shown"] > 0
    assert context_budget_profile(json.dumps(sections, ensure_ascii=False), budget_tokens=COMPACT_AGENT_PACKET_TOKENS)["status"] == "pass"
    oversized = lambda record: {"status": "unresolved", "reason": "x" * (COMPACT_AGENT_PACKET_TOKENS * 4)}
    sections = project_client_local_parser_evidence(info, _local_projection_status(info), source_candidate=oversized)
    evidence = sections["client_imported_parser_evidence"]
    assert evidence["shown"] == 0 and evidence["omitted"] == evidence["record_count"] == 10
    assert evidence["reason"] == "compact_budget_omission"
    assert context_budget_profile(json.dumps(sections, ensure_ascii=False), budget_tokens=COMPACT_AGENT_PACKET_TOKENS)["status"] == "pass"


def test_response_json_return_real_atlas_sqlite_machine_brief_and_focus(imported_parser_atlas_run, monkeypatch):
    from tools.core.agent_packet_budget import COMPACT_AGENT_PACKET_TOKENS, context_budget_profile
    target, _, atlas = imported_parser_atlas_run
    mcp_server._ATLAS_CACHE.clear()
    monkeypatch.setattr(mcp_server, "_atlas", lambda *a, **k: pytest.fail("Whole Atlas materialized"))
    machine = json.loads(mcp_server.inspect_file("MAIN::adapter.ts", format="json", target_root=str(target)))
    evidence = machine["response_json_return_evidence"]
    assert evidence["status"] == "observed" and evidence["shown"] == evidence["record_count"] == 1
    row = evidence["records"][0]
    assert row["callable_name"] == "load" and row["field_path"] == ["data"] and row["return_type_assertion"] is True
    span = row["spans"]["selected_expression"]
    assert (target / "adapter.ts").read_bytes()[span["start"]:span["end"]] == b"raw.data"
    assert evidence["source_hash"] == atlas["MAIN"]["files"]["adapter.ts"]["hash"]
    assert evidence["promise_resolution"] == evidence["caller_state_binding"] == evidence["schema_authority"] == "not_established"
    assert evidence["source_binding"] == "snapshot_and_live_match" and evidence["runtime_proof"] == "not_performed"
    assert evidence["edit_authority"] == "not_granted" and not machine["one_shot_edit_ready"]
    sections = {key: value for key, value in machine.items() if key.endswith("_evidence") and
                (key.startswith("client_") or key == "response_json_return_evidence")}
    assert context_budget_profile(json.dumps(sections, ensure_ascii=False), budget_tokens=COMPACT_AGENT_PACKET_TOKENS)["status"] == "pass"
    brief = mcp_server.inspect_file("MAIN::adapter.ts", target_root=str(target))
    line = next(line for line in brief.splitlines() if line.startswith("response_json_return_evidence: "))
    assert json.loads(line.split(": ", 1)[1]) == evidence
    focused = json.loads(mcp_server.inspect_file("MAIN::adapter.ts", format="json", target_root=str(target), line_start=1, line_end=1))
    assert focused["response_json_return_evidence"] == evidence


@pytest.mark.parametrize("damage", ["none", "legacy", "hash", "ast", "producer", "schema_authority",
    "promise_resolution", "caller_state_binding", "runtime_proof", "span", "relation", "source_drift", "corrupt_primary"])
@pytest.mark.parametrize("partitioned", [False, True], ids=["inline", "partitioned"])
def test_response_json_return_primary_sqlite_and_source_fail_closed(imported_parser_atlas_run, tmp_path, monkeypatch, damage, partitioned):
    import sqlite3
    target, _, original = imported_parser_atlas_run
    local = tmp_path / "target"
    local.mkdir()
    atlas = deepcopy(original)
    for file in atlas["MAIN"]["files"]:
        (local / file).write_bytes((target / file).read_bytes())
    atlas["MAIN"]["root_path"] = str(local)
    info = atlas["MAIN"]["files"]["adapter.ts"]
    evidence = info["response_json_return_evidence"]
    if damage == "legacy": info.pop("response_json_return_evidence")
    elif damage == "hash": evidence["source_hash"] = "0" * 64
    elif damage == "ast": evidence["ast_contract_version"] = "old"
    elif damage == "producer": evidence["status"] = "degraded"
    elif damage in {"schema_authority", "promise_resolution", "caller_state_binding", "runtime_proof"}: evidence[damage] = "proven"
    elif damage == "span": evidence["records"][0]["spans"]["json_ref"]["start"] = True
    elif damage == "relation": evidence["records"][0]["spans"]["selected_expression"] = deepcopy(evidence["records"][0]["spans"]["body"])
    raw = tmp_path / ".raw"
    store = ArtifactStore(raw_dir=raw)
    store.use_sqlite, store.backend = True, "hybrid_sqlite"
    store._ensure_schema()
    if partitioned: store.state_payload_inline_limit_bytes = 1
    store._save_payload_to_state_table("atlas", atlas)
    store._save_atlas_to_sqlite(atlas)
    with sqlite3.connect(raw / "codemaps.db") as conn:
        conn.execute("UPDATE projects SET path = '.' WHERE project_key='MAIN'")
        if damage == "corrupt_primary":
            if partitioned: conn.execute("DELETE FROM state_payload_parts WHERE name='atlas'")
            else: conn.execute("UPDATE state_payloads SET payload='broken' WHERE name='atlas'")
    if damage == "source_drift":
        file = local / "adapter.ts"
        file.write_bytes(file.read_bytes() + b"\n// changed")
    mcp_server._ATLAS_CACHE.clear()
    monkeypatch.setattr(mcp_server, "_atlas", lambda *a, **k: pytest.fail("Whole Atlas materialized"))
    monkeypatch.setattr(mcp_server, "_raw_dir_for_target", lambda root: raw)
    monkeypatch.setattr(mcp_server, "_reports_dir_for_target", lambda root: tmp_path / "reports")
    monkeypatch.setattr(mcp_server, "_analysis_root_display", lambda root: str(local))
    machine = json.loads(mcp_server.inspect_file("MAIN::adapter.ts", format="json", target_root=str(local)))
    result = machine.get("response_json_return_evidence")
    if damage in {"legacy", "corrupt_primary"}:
        assert result is None, "Unknown producer metadata is not observed-empty or rescued from JSON"
    elif damage == "none":
        assert result["status"] == "observed" and result["shown"] == 1
    else:
        assert result["status"] == "unavailable" and result["records"] == []
        assert result["record_count"] is None and result["schema_authority"] == "not_established"


def test_response_json_return_shared_budget_never_truncates_selected_path(imported_parser_atlas_run):
    from tools.inspect_target import project_client_local_parser_evidence, AGENT_INSPECTION_MAX_TARGET_SPANS
    from tools.core.agent_packet_budget import COMPACT_AGENT_PACKET_TOKENS, context_budget_profile
    _, _, atlas = imported_parser_atlas_run
    info = deepcopy(atlas["MAIN"]["files"]["adapter.ts"])
    info["response_json_return_evidence"]["records"] *= 10
    sections = project_client_local_parser_evidence(info, _local_projection_status(info))
    result = sections["response_json_return_evidence"]
    assert 0 < result["shown"] <= AGENT_INSPECTION_MAX_TARGET_SPANS
    assert result["omitted"] == result["record_count"] - result["shown"] > 0
    assert context_budget_profile(json.dumps(sections, ensure_ascii=False), budget_tokens=COMPACT_AGENT_PACKET_TOKENS)["status"] == "pass"
    for row in info["response_json_return_evidence"]["records"]:
        row["field_path"] = ["x" * (COMPACT_AGENT_PACKET_TOKENS * 4)]
    sections = project_client_local_parser_evidence(info, _local_projection_status(info))
    assert sections["response_json_return_evidence"]["shown"] == 0
    assert sections["response_json_return_evidence"]["omitted"] == 10
    assert context_budget_profile(json.dumps(sections, ensure_ascii=False), budget_tokens=COMPACT_AGENT_PACKET_TOKENS)["status"] == "pass"


def test_guarded_caller_result_use_real_atlas_sqlite_and_machine_brief(imported_parser_atlas_run, monkeypatch):
    target, _, atlas = imported_parser_atlas_run
    mcp_server._ATLAS_CACHE.clear()
    original_read = mcp_server._atlas_read
    reads = []
    def counted(*args, **kwargs):
        reads.append(kwargs)
        return original_read(*args, **kwargs)
    monkeypatch.setattr(mcp_server, "_atlas_read", counted)
    machine = json.loads(mcp_server.get_state_flow(file="Caller.tsx", symbol="Guarded", format="json", target_root=str(target)))
    assert len(reads) == 1
    lane = machine["symbol_context"]["local_arrow_result_uses"]
    assert lane["status"] == "observed" and lane["returned"] == 2 and lane["omitted"] == 0
    assert lane["source_binding"] == "snapshot_and_live_match"
    for item, name, prop, sink in zip(lane["items"], ("data", "result"), ("tables", "rows"), ("setTables", "setRows")):
        assert (item["result_name"], item["selected_property"], item["sink_name"]) == (name, prop, sink)
        association = item["callee_orientation"]
        assert association["status"] == "target_candidate"
        assert association["endpoint_content_binding"] == "both_snapshot_and_live_match"
        context = machine["local_arrow_import_callees"]["target_symbol_contexts"][association["target_context_index"]]
        assert context["response_json_return_evidence"]["records"][0]["field_path"] == ["data"]
        for role in ("condition", "argument"):
            span = item["spans"][role]
            assert (target / "Caller.tsx").read_bytes()[span["start"]:span["end"]].decode() == f"{name}.{prop}"
        assert item["spans"]["sink_binding"]["end"] < item["spans"]["owner"]["start"]
        assert "field_path" not in item, "Do not concatenate caller property with adapter's JSON field"
    assert all(lane[key] == "not_established" for key in ("promise_resolution", "caller_state_binding", "schema_authority", "runtime_execution"))
    assert machine["direct_callees"]["items"] == []
    assert lane["source_hash"] == atlas["MAIN"]["files"]["Caller.tsx"]["hash"]
    brief = mcp_server.get_state_flow(file="Caller.tsx", symbol="Guarded", target_root=str(target))
    assert '"selected_property": "tables"' in brief and '"selected_property": "rows"' in brief


@pytest.mark.parametrize("damage", ["none", "caller_drift", "adapter_drift", "caller_snapshot", "adapter_snapshot",
    "hash", "ast", "coherent_old_ast", "bool", "sink_binding", "argument_order", "duplicate", "authority", "missing", "incomplete",
    "old_call_missing", "old_call_identity", "old_call_span", "corrupt_primary"])
def test_guarded_caller_result_use_source_identity_and_independent_failure(imported_parser_atlas_run, tmp_path, monkeypatch, damage):
    import sqlite3
    target, _, original = imported_parser_atlas_run
    local = tmp_path / "target"
    local.mkdir()
    atlas = deepcopy(original)
    files = atlas["MAIN"]["files"]
    for file in files:
        (local / file).write_bytes((target / file).read_bytes())
    atlas["MAIN"]["root_path"] = str(local)
    caller = files["Caller.tsx"]
    owner = next(row for row in caller["symbols"] if row["name"] == "Guarded")
    lane = owner["local_arrow_result_use_evidence"]
    if damage == "hash": lane["source_hash"] = "0" * 64
    elif damage == "ast": lane["ast_contract_version"] = "old"
    elif damage == "coherent_old_ast": lane["ast_contract_version"] = caller["ast_contract_version"] = "old"
    elif damage == "bool": lane["records"][0]["spans"]["condition"]["start"] = True
    elif damage == "sink_binding": lane["records"][0]["spans"]["sink_binding"]["end"] = caller["size"] + 1
    elif damage == "argument_order": lane["records"][0]["spans"]["argument"] = deepcopy(lane["records"][0]["spans"]["sink_callee"])
    elif damage == "duplicate": lane["records"].append(deepcopy(lane["records"][0]))
    elif damage == "authority": lane["runtime_execution"] = "confirmed"
    elif damage == "missing": owner.pop("local_arrow_result_use_evidence")
    elif damage == "incomplete": lane.update(status="incomplete_scan", omitted=1)
    elif damage == "old_call_missing": owner.pop("local_arrow_import_call_evidence")
    elif damage == "old_call_identity": owner["local_arrow_import_call_evidence"]["records"][0]["source"] = "./other"
    elif damage == "old_call_span": owner["local_arrow_import_call_evidence"]["records"][0]["spans"]["callee"]["end"] -= 1
    raw = tmp_path / ".raw"
    store = ArtifactStore(raw_dir=raw)
    store.use_sqlite, store.backend = True, "hybrid_sqlite"
    store._ensure_schema()
    store._save_payload_to_state_table("atlas", atlas)
    store._save_atlas_to_sqlite(atlas)
    with sqlite3.connect(raw / "codemaps.db") as conn:
        conn.execute("UPDATE projects SET path='.' WHERE project_key='MAIN'")
        if damage == "corrupt_primary": conn.execute("UPDATE state_payloads SET payload='broken' WHERE name='atlas'")
        if damage in {"caller_snapshot", "adapter_snapshot"}:
            file = "Caller.tsx" if damage == "caller_snapshot" else "adapter.ts"
            conn.execute("UPDATE source_snapshots SET content='corrupt' WHERE project_key='MAIN' AND rel_path=?", (file,))
    if damage in {"caller_drift", "adapter_drift"}:
        file = local / ("Caller.tsx" if damage == "caller_drift" else "adapter.ts")
        file.write_bytes(file.read_bytes() + b"\n// changed")
    mcp_server._ATLAS_CACHE.clear()
    monkeypatch.setattr(mcp_server, "_raw_dir_for_target", lambda root: raw)
    monkeypatch.setattr(mcp_server, "_analysis_root_display", lambda root: str(local))
    result = json.loads(mcp_server.get_state_flow(file="Caller.tsx", symbol="Guarded", format="json", target_root=str(local)))
    projected = result.get("symbol_context", {}).get("local_arrow_result_uses")
    if damage == "none": assert projected["status"] == "observed" and projected["returned"] == 2
    elif damage in {"adapter_drift", "adapter_snapshot", "old_call_missing", "old_call_identity", "old_call_span"}:
        assert projected["status"] == "observed" and projected["returned"] == 2
        assert projected["items"][0]["callee_orientation"]["status"] == "unresolved"
    else: assert projected is None or (projected["status"] != "observed" and projected["items"] == [])
    if damage in {"hash", "ast", "bool", "sink_binding", "argument_order", "duplicate", "authority", "missing", "incomplete"}:
        assert result["local_arrow_import_callees"]["target_symbol_contexts"][0]["response_json_return_evidence"]["status"] == "observed"


@pytest.mark.parametrize("symbol", ["Outer", "Direct"])
def test_caller_return_orientation_real_single_atlas_sqlite_and_live_endpoints(imported_parser_atlas_run, monkeypatch, symbol):
    target, _, atlas = imported_parser_atlas_run
    mcp_server._ATLAS_CACHE.clear()
    original_read = mcp_server._atlas_read
    reads = []
    def counted(*args, **kwargs):
        reads.append(kwargs)
        return original_read(*args, **kwargs)
    monkeypatch.setattr(mcp_server, "_atlas_read", counted)
    result = json.loads(mcp_server.get_state_flow(file="Caller.tsx", symbol=symbol,
        format="json", target_root=str(target)))
    assert len(reads) == 1
    assert result["source_binding"] == "snapshot_and_live_match"
    lane = result["local_arrow_import_callees" if symbol == "Outer" else "direct_callees"]
    item = lane["items"][0]
    assert item["endpoint_content_binding"] == "both_snapshot_and_live_match"
    context = lane["target_symbol_contexts"][item["target_context_index"]]
    evidence = context["response_json_return_evidence"]
    assert evidence["status"] == "observed" and evidence["shown"] == 1
    assert evidence["projection_scope"] == "exact_selected_callable_return_expression"
    row = evidence["records"][0]
    assert row["callable_name"] == "load" and row["callable_kind"] == "const_async_arrow"
    assert row["field_path"] == ["data"] and row["return_type_assertion"] is True
    span = row["spans"]["selected_expression"]
    assert (target / "adapter.ts").read_bytes()[span["start"]:span["end"]] == b"raw.data"
    assert all(evidence[key] == "not_established" for key in
        ("promise_resolution", "caller_state_binding", "schema_authority"))
    assert evidence["runtime_proof"] == "not_performed" and evidence["edit_authority"] == "not_granted"
    if symbol == "Outer":
        assert result["direct_callees"]["items"] == []
        assert item["lexical_owner"] == "handleTable" and item["outer_callable_execution"] == "not_established"
        call = item["caller_spans"]["call"]
        assert (target / "Caller.tsx").read_bytes()[call["start"]:call["end"]] == b"readTables()"
    assert evidence["source_hash"] == atlas["MAIN"]["files"]["adapter.ts"]["hash"]


@pytest.mark.parametrize("damage", ["none", "caller_drift", "adapter_drift", "caller_snapshot", "adapter_snapshot",
    "legacy_call", "call_hash", "call_ast", "call_span", "call_incomplete", "duplicate_export", "private_export",
    "missing_kind", "wrong_kind", "symbol_span", "legacy_return", "return_hash", "return_ast", "coherent_old_ast",
    "return_span", "duplicate_return", "return_kind", "return_parser", "return_budget", "corrupt_primary"])
def test_caller_return_orientation_fail_closed_actual_primary_and_snapshots(imported_parser_atlas_run, tmp_path, monkeypatch, damage):
    import sqlite3
    target, _, original = imported_parser_atlas_run
    local = tmp_path / "target"
    local.mkdir()
    atlas = deepcopy(original)
    files = atlas["MAIN"]["files"]
    for file in files:
        (local / file).write_bytes((target / file).read_bytes())
    atlas["MAIN"]["root_path"] = str(local)
    caller, adapter = files["Caller.tsx"], files["adapter.ts"]
    owner = next(row for row in caller["symbols"] if row["name"] == "Outer")
    call = owner["local_arrow_import_call_evidence"]
    returns = adapter["response_json_return_evidence"]
    selected = adapter["symbols"][0]
    if damage == "legacy_call": owner.pop("local_arrow_import_call_evidence")
    elif damage == "call_hash": call["source_hash"] = "0" * 64
    elif damage == "call_ast": call["ast_contract_version"] = "old"
    elif damage == "call_span": call["records"][0]["spans"]["callee"]["start"] = True
    elif damage == "call_incomplete": call.update(status="incomplete_scan", omitted=1)
    elif damage == "duplicate_export": adapter["symbols"].append(deepcopy(selected))
    elif damage == "private_export": selected["exported"] = False
    elif damage == "missing_kind": selected.pop("response_json_callable_kind")
    elif damage == "wrong_kind": selected["response_json_callable_kind"] = "async_function"
    elif damage == "symbol_span": selected["start"] += 1
    elif damage == "legacy_return": adapter.pop("response_json_return_evidence")
    elif damage == "return_hash": returns["source_hash"] = "0" * 64
    elif damage == "return_ast": returns["ast_contract_version"] = "old"
    elif damage == "coherent_old_ast":
        returns["ast_contract_version"] = adapter["ast_contract_version"] = "old"
    elif damage == "return_span": returns["records"][0]["spans"]["selected_expression"]["start"] = True
    elif damage == "duplicate_return": returns["records"] *= 2
    elif damage == "return_kind": returns["records"][0]["callable_kind"] = "async_function"
    elif damage == "return_parser": adapter["parser_evidence"]["status"] = "degraded"
    elif damage == "return_budget": returns["records"][0]["field_path"] = ["x" * 30000]
    raw = tmp_path / ".raw"
    store = ArtifactStore(raw_dir=raw)
    store.use_sqlite, store.backend = True, "hybrid_sqlite"
    store._ensure_schema()
    store._save_payload_to_state_table("atlas", atlas)
    store._save_atlas_to_sqlite(atlas)
    with sqlite3.connect(raw / "codemaps.db") as conn:
        conn.execute("UPDATE projects SET path='.' WHERE project_key='MAIN'")
        if damage == "corrupt_primary": conn.execute("UPDATE state_payloads SET payload='broken' WHERE name='atlas'")
        if damage in {"caller_snapshot", "adapter_snapshot"}:
            file = "Caller.tsx" if damage == "caller_snapshot" else "adapter.ts"
            conn.execute("UPDATE source_snapshots SET content='corrupt' WHERE project_key='MAIN' AND rel_path=?", (file,))
    if damage in {"caller_drift", "adapter_drift"}:
        file = local / ("Caller.tsx" if damage == "caller_drift" else "adapter.ts")
        file.write_bytes(file.read_bytes() + b"\n// changed")
    mcp_server._ATLAS_CACHE.clear()
    monkeypatch.setattr(mcp_server, "_raw_dir_for_target", lambda root: raw)
    monkeypatch.setattr(mcp_server, "_analysis_root_display", lambda root: str(local))
    result = json.loads(mcp_server.get_state_flow(file="Caller.tsx", symbol="Outer", format="json", target_root=str(local)))
    contexts = result.get("local_arrow_import_callees", {}).get("target_symbol_contexts", [])
    evidence = contexts[0]["response_json_return_evidence"] if contexts else None
    if damage == "none":
        assert evidence["status"] == "observed" and evidence["shown"] == 1
    else:
        assert evidence is None or (evidence["status"] == "unavailable" and evidence["records"] == [])
    if damage in {"caller_drift", "caller_snapshot"}:
        assert result["symbol_context"]["local_arrow_import_calls"]["items"] == []


@pytest.mark.parametrize(("symbol", "kind", "path"), [("DirectFn", "async_function", ["rows"]),
    ("DirectHookFunction", "async_function", ["items"]), ("DirectHookArrow", "const_async_arrow", [])])
def test_caller_return_orientation_kind_comes_from_exact_native_callable(imported_parser_atlas_run, symbol, kind, path):
    target, _, _ = imported_parser_atlas_run
    mcp_server._ATLAS_CACHE.clear()
    result = json.loads(mcp_server.get_state_flow(file="Caller.tsx", symbol=symbol, format="json", target_root=str(target)))
    context = result["direct_callees"]["target_symbol_contexts"][0]
    evidence = context["response_json_return_evidence"]
    assert evidence["status"] == "observed" and evidence["shown"] == 1
    assert evidence["records"][0]["callable_kind"] == kind
    assert evidence["records"][0]["field_path"] == path


@pytest.mark.parametrize("damage", ["none", "wrong_name", "wrong_kind", "wrong_span", "bool_span", "duplicate", "budget"])
def test_selected_return_uses_exact_callable_beyond_file_sample_cap(imported_parser_atlas_run, damage):
    from tools.inspect_target import _project_response_json_return_evidence
    _, _, atlas = imported_parser_atlas_run
    info = deepcopy(atlas["MAIN"]["files"]["multi.ts"])
    symbol = next(row for row in info["symbols"] if row["name"] == "load7")
    target = {"symbol": symbol["name"], "source_hash": info["hash"], "ast_contract_version": info["ast_contract_version"],
              "callable_kind": symbol["response_json_callable_kind"],
              "callable_span": {key: symbol[key] for key in ("start", "end", "line", "end_line")}}
    if damage == "wrong_name": target["symbol"] = "absent"
    elif damage == "wrong_kind": target["callable_kind"] = "async_function"
    elif damage == "wrong_span": target["callable_span"]["start"] += 1
    elif damage == "bool_span": target["callable_span"]["line"] = True
    elif damage == "duplicate": info["response_json_return_evidence"]["records"].append(deepcopy(info["response_json_return_evidence"]["records"][-1]))
    projected = _project_response_json_return_evidence(info, _local_projection_status(info), selected_target=target,
        scan_limit=7 if damage == "budget" else 20)
    if damage == "none":
        assert projected["status"] == "observed" and projected["shown"] == 1
        assert projected["records"][0]["field_path"] == ["slot7"]
        assert projected["omitted"] == 7
    else:
        assert projected["status"] == "unavailable" and projected["records"] == []


def _local_projection_status(info):
    return {"source_snapshot_hash": info["hash"], "source_snapshot_status": "ok",
            "drift_check_status": "match", "exists": True, "inside_root": True, "indexed": True,
            "target_ref": "MAIN::Panel.tsx"}


@pytest.mark.parametrize("case", ["result_field", "local_passthrough_parse_name", "discarded_result_raw_sink"])
def test_local_projection_preserves_syntax_empty_and_passthrough_boundaries(local_projection_inputs, case):
    from tools.inspect_target import project_client_local_parser_result_evidence
    source, info = local_projection_inputs[case]
    before = deepcopy(info)
    result = project_client_local_parser_result_evidence(info, _local_projection_status(info))
    count = 0 if case == "discarded_result_raw_sink" else 1
    assert result["status"] == "observed" and result["record_count"] == result["shown"] == count
    assert result["source_binding"] == "snapshot_and_live_match" and result["omitted"] == 0
    assert result["schema_authority"] == "not_established" and result["runtime_proof"] == "not_performed"
    assert result["edit_authority"] == "not_granted" and "not proof" in result["empty_result_meaning"]
    for row in result["records"]:
        span = row["spans"]["argument"]
        assert source.encode("utf-8")[span["start"]:span["end"]].decode("utf-8") == "parsed.image"
        assert row["source_followup"]["tool"] == "inspect_file"
        inspect.signature(mcp_server.inspect_file).bind(**{key: value for key, value in row["source_followup"].items() if key != "tool"})
    assert info == before


@pytest.mark.parametrize("damage", ["missing", "unavailable", "version", "unit", "metadata_hash", "atlas_hash",
    "ast_version", "schema_claim", "runtime_claim", "records", "field_path", "span_bool", "span_order",
    "span_line", "snapshot_hash", "snapshot_status", "live_drift", "inside_root", "exists", "indexed"])
@pytest.mark.parametrize("input_sink", [False, True], ids=["result", "input"])
def test_local_projection_fails_closed_without_clean_or_authority_claim(local_projection_inputs, damage, input_sink):
    from tools.inspect_target import project_client_local_parser_result_evidence, project_client_local_parser_evidence
    _, original = local_projection_inputs["discarded_result_raw_sink" if input_sink else "result_field"]
    metadata_key = "client_local_parser_input_sink_evidence" if input_sink else "client_local_parser_result_evidence"
    info = deepcopy(original)
    status = _local_projection_status(info)
    evidence = info[metadata_key]
    if damage == "missing":
        info.pop(metadata_key)
    elif damage == "unavailable":
        evidence["status"] = "unavailable"
    elif damage == "version":
        evidence["version"] = "future"
    elif damage == "unit":
        evidence["offset_unit"] = "utf16_code_units"
    elif damage == "metadata_hash":
        evidence["source_hash"] = "0" * 64
    elif damage == "atlas_hash":
        info["hash"] = "0" * 64
    elif damage == "ast_version":
        info["ast_contract_version"] = "old"
    elif damage == "schema_claim":
        evidence["schema_authority"] = "established"
    elif damage == "runtime_claim":
        evidence["runtime_proof"] = "performed"
    elif damage == "records":
        evidence["records"] = {}
    elif damage == "field_path":
        evidence["records"][0]["field_path"] = [3]
    elif damage.startswith("span_"):
        span = evidence["records"][0]["spans"]["sink"]
        if damage == "span_bool":
            span["line"] = True
        elif damage == "span_order":
            span["end"] = span["start"]
        else:
            span["line"] = 0
    elif damage == "snapshot_hash":
        status["source_snapshot_hash"] = "0" * 64
    elif damage == "snapshot_status":
        status["source_snapshot_status"] = "content_mismatch"
    elif damage == "live_drift":
        status["drift_check_status"] = "mismatch"
    else:
        status[damage] = False
    result = project_client_local_parser_evidence(info, status)[metadata_key] if input_sink else project_client_local_parser_result_evidence(info, status)
    assert result["status"] == "unavailable" and result["records"] == []
    assert result["record_count"] is None and result["omitted"] is None and result["reason"]
    assert result["schema_authority"] == "not_established" and result["runtime_proof"] == "not_performed"


def test_local_projection_reuses_caps_and_discloses_budget_omissions(local_projection_inputs):
    from tools.inspect_target import AGENT_INSPECTION_MAX_TARGET_SPANS, project_client_local_parser_result_evidence
    from tools.core.agent_packet_budget import COMPACT_AGENT_PACKET_TOKENS, context_budget_profile
    _, original = local_projection_inputs["result_field"]
    info = deepcopy(original)
    evidence = info["client_local_parser_result_evidence"]
    evidence["records"] *= AGENT_INSPECTION_MAX_TARGET_SPANS + 3
    result = project_client_local_parser_result_evidence(info, _local_projection_status(info))
    assert 0 < result["shown"] <= AGENT_INSPECTION_MAX_TARGET_SPANS
    assert result["omitted"] == result["record_count"] - result["shown"] > 0
    assert context_budget_profile(json.dumps(result, ensure_ascii=False), budget_tokens=COMPACT_AGENT_PACKET_TOKENS)["status"] == "pass"
    evidence["records"][0]["field_path"] = ["x" * (COMPACT_AGENT_PACKET_TOKENS * 4)]
    result = project_client_local_parser_result_evidence(info, _local_projection_status(info))
    assert result["status"] == "observed" and result["shown"] == 0
    assert result["reason"] == "compact_budget_omission" and result["omitted"] == result["record_count"]


@pytest.mark.parametrize("damage", ["selected_value_origin", "call_result_use", "parser_effect_on_input"])
def test_local_input_projection_rejects_promoted_semantic_contract(local_projection_inputs, damage):
    from tools.inspect_target import project_client_local_parser_evidence
    _, original = local_projection_inputs["discarded_result_raw_sink"]
    info = deepcopy(original)
    info["client_local_parser_input_sink_evidence"][damage] = "proved"
    result = project_client_local_parser_evidence(info, _local_projection_status(info))["client_local_parser_input_sink_evidence"]
    assert result["status"] == "unavailable" and result["records"] == [] and result["record_count"] is None
    assert result["parser_effect_on_input"] == "not_established" and result["edit_authority"] == "not_granted"


def test_local_input_projection_shares_result_budget_and_reports_every_omission(local_projection_inputs):
    from tools.inspect_target import project_client_local_parser_evidence, AGENT_INSPECTION_MAX_TARGET_SPANS
    from tools.core.agent_packet_budget import COMPACT_AGENT_PACKET_TOKENS, context_budget_profile
    _, result_info = local_projection_inputs["result_field"]
    _, input_info = local_projection_inputs["discarded_result_raw_sink"]
    # Stress the projection's shared allowance only; producer/source validity is
    # tested separately. This artificial mixed envelope is not runtime evidence.
    info = deepcopy(result_info)
    extra = deepcopy(input_info["client_local_parser_input_sink_evidence"])
    extra["source_hash"] = info["hash"]
    info["client_local_parser_input_sink_evidence"] = extra
    for key in ("client_local_parser_result_evidence", "client_local_parser_input_sink_evidence"):
        info[key]["records"] *= AGENT_INSPECTION_MAX_TARGET_SPANS + 3
    sections = project_client_local_parser_evidence(info, _local_projection_status(info))
    assert 0 < sum(row["shown"] for row in sections.values()) <= AGENT_INSPECTION_MAX_TARGET_SPANS
    assert context_budget_profile(json.dumps(sections, ensure_ascii=False), budget_tokens=COMPACT_AGENT_PACKET_TOKENS)["status"] == "pass"
    for section in sections.values():
        assert section["status"] == "observed" and section["omitted"] == section["record_count"] - section["shown"] > 0
    info["client_local_parser_input_sink_evidence"]["records"][0]["field_path"] = ["x" * (COMPACT_AGENT_PACKET_TOKENS * 4)]
    section = project_client_local_parser_evidence(info, _local_projection_status(info))["client_local_parser_input_sink_evidence"]
    assert section["shown"] == 0 and section["omitted"] == section["record_count"]
    assert section["reason"] == "compact_budget_omission"
    info["ast_contract_version"] = "x" * (COMPACT_AGENT_PACKET_TOKENS * 4)
    for key in ("client_local_parser_result_evidence", "client_local_parser_input_sink_evidence"):
        info[key]["ast_contract_version"] = info["ast_contract_version"]
    sections = project_client_local_parser_evidence(info, _local_projection_status(info))
    assert all(section["status"] == "unavailable" and section["reason"] == "compact_projection_header_over_budget"
               for section in sections.values())
    assert context_budget_profile(json.dumps(sections, ensure_ascii=False), budget_tokens=COMPACT_AGENT_PACKET_TOKENS)["status"] == "pass"


@pytest.mark.parametrize("case", ["result_field", "local_passthrough_parse_name", "discarded_result_raw_sink"])
def test_local_projection_inspect_file_sqlite_machine_brief_and_stale_source(tmp_path, monkeypatch, local_projection_inputs, case):
    from tools.core.artifact_store import ArtifactStore
    import tools.core.json_io as json_io
    source, original = local_projection_inputs[case]
    target = tmp_path / "target"
    target.mkdir()
    file = target / "Panel.tsx"
    file.write_bytes(source.encode("utf-8"))
    info = {**deepcopy(original), "workspace_rel": "Panel.tsx", "symbols": []}
    raw = tmp_path / "analysis" / ".raw"
    store = ArtifactStore(raw_dir=raw)
    store.use_sqlite, store.backend = True, "hybrid_sqlite"
    store._ensure_schema()
    atlas = {"MAIN": {"root_path": str(target), "files": {"Panel.tsx": info}, "symbols": []},
             "OTHER": {"root_path": str(target), "files": {"Panel.tsx": {**info, "workspace_rel": "other/Panel.tsx"}}, "symbols": []}}
    store._save_payload_to_state_table("atlas", atlas)
    store._save_atlas_to_sqlite(atlas)
    # Normal installed-repository path identity, while fixture snapshot capture
    # above deliberately used an absolute root independent of the live repo.
    import sqlite3
    with sqlite3.connect(native_filesystem_path(raw / "codemaps.db")) as conn:
        conn.execute("UPDATE projects SET path = '.' WHERE project_key = 'MAIN'")
    mcp_server._ATLAS_CACHE.clear()
    monkeypatch.setattr(mcp_server, "_atlas", lambda *a, **k: pytest.fail("New file context materialized the whole Atlas"))
    monkeypatch.setattr(mcp_server, "_raw_dir_for_target", lambda root: raw)
    monkeypatch.setattr(mcp_server, "_reports_dir_for_target", lambda root: tmp_path / "reports")
    monkeypatch.setattr(mcp_server, "_analysis_root_display", lambda root: str(target))
    native = json_io.load_json_file
    def no_shadow(path, *args, **kwargs):
        assert Path(path) != raw / "atlas.json", "Existing SQLite payload must own this projection"
        return native(path, *args, **kwargs)
    monkeypatch.setattr(json_io, "load_json_file", no_shadow)
    monkeypatch.setattr(mcp_server, "write_inspection", lambda *a, **k: pytest.fail("Unexpected inspection fallback"))
    machine = json.loads(mcp_server.inspect_file("main::Panel.tsx", format="json", target_root=str(target)))
    projected = machine["client_local_parser_result_evidence"]
    assert machine["target_file_context"][0]["atlas_node"] == "MAIN::Panel.tsx"
    assert projected["status"] == "observed"
    assert projected["record_count"] == (0 if case == "discarded_result_raw_sink" else 1)
    input_projected = machine["client_local_parser_input_sink_evidence"]
    assert input_projected["status"] == "observed" and input_projected["record_count"] == (1 if case == "discarded_result_raw_sink" else 0)
    assert input_projected["shown"] == input_projected["record_count"]
    assert input_projected["parser_effect_on_input"] == "not_established" and input_projected["edit_authority"] == "not_granted"
    assert not machine["one_shot_edit_ready"] and machine["inspection_authority"] == "orientation_only"
    brief = mcp_server.inspect_file("MAIN::Panel.tsx", target_root=str(target))
    section = next(line for line in brief.splitlines() if line.startswith("client_local_parser_result_evidence: "))
    assert json.loads(section.split(": ", 1)[1]) == projected
    input_section = next(line for line in brief.splitlines() if line.startswith("client_local_parser_input_sink_evidence: "))
    assert json.loads(input_section.split(": ", 1)[1]) == input_projected
    lines = brief.splitlines()
    assert lines.index("source_grounding:") < lines.index(section) < lines.index("path_contract:")
    assert '  inspection_authority: "orientation_only"' in lines
    focused = json.loads(mcp_server.inspect_file("MAIN::Panel.tsx", format="json", target_root=str(target), line_start=1, line_end=1))
    assert focused["client_local_parser_result_evidence"] == projected
    assert focused["client_local_parser_input_sink_evidence"] == input_projected
    assert focused["one_shot_edit_ready"], "Existing explicit source range retains independent edit-context rules"
    file.write_bytes((source + "\r\n// changed").encode("utf-8"))
    stale = json.loads(mcp_server.inspect_file("MAIN::Panel.tsx", format="json", target_root=str(target), line_start=1, line_end=1))
    assert stale["client_local_parser_result_evidence"]["status"] == "unavailable"
    assert stale["client_local_parser_result_evidence"]["reason"] == "source_binding_unavailable"
    assert stale["client_local_parser_input_sink_evidence"]["status"] == "unavailable"
    assert not stale["one_shot_edit_ready"]


@pytest.mark.parametrize("case", ["result_field", "discarded_result_raw_sink"], ids=["result", "input"])
def test_local_projection_cold_partitioned_metadata_is_read_without_atlas_materialization(tmp_path, monkeypatch, local_projection_inputs, case):
    from tools.core.artifact_store import ArtifactStore
    import sqlite3
    source, original = local_projection_inputs[case]
    metadata_key = "client_local_parser_input_sink_evidence" if case == "discarded_result_raw_sink" else "client_local_parser_result_evidence"
    file = tmp_path / "Panel.tsx"
    file.write_bytes(source.encode("utf-8"))
    raw = tmp_path / ".raw"
    store = ArtifactStore(raw_dir=raw)
    store.use_sqlite, store.backend = True, "hybrid_sqlite"
    store._ensure_schema()
    store.state_payload_inline_limit_bytes = 1
    atlas = {"MAIN": {"root_path": str(tmp_path), "files": {"Panel.tsx": {**original, "workspace_rel": "Panel.tsx", "symbols": []}}, "symbols": []}}
    store._save_payload_to_state_table("atlas", atlas)
    store._save_atlas_to_sqlite(atlas)
    with sqlite3.connect(raw / "codemaps.db") as conn:
        conn.execute("UPDATE projects SET path = '.' WHERE project_key = 'MAIN'")
    mcp_server._ATLAS_CACHE.clear()
    monkeypatch.setattr(mcp_server, "_atlas", lambda *a, **k: pytest.fail("Cold partitioned Atlas loaded"))
    monkeypatch.setattr(mcp_server, "_raw_dir_for_target", lambda root: raw)
    monkeypatch.setattr(mcp_server, "_reports_dir_for_target", lambda root: tmp_path / "reports")
    monkeypatch.setattr(mcp_server, "_analysis_root_display", lambda root: str(tmp_path))
    result = json.loads(mcp_server.inspect_file("MAIN::Panel.tsx", format="json", target_root=str(tmp_path)))
    evidence = result[metadata_key]
    assert evidence["status"] == "observed" and evidence["record_count"] == 1
    assert evidence["schema_authority"] == "not_established"
    assert not result["one_shot_edit_ready"]
    # Existing fingerprint-bound in-process transport is safe to reuse without
    # a second cold load. It does not certify runtime or schema authority.
    mcp_server._ATLAS_CACHE[mcp_server._artifact_cache_key(raw / "atlas.json")] = deepcopy(atlas)
    cached = json.loads(mcp_server.inspect_file("MAIN::Panel.tsx", format="json", target_root=str(tmp_path)))
    assert cached[metadata_key]["status"] == "observed"
    mcp_server._ATLAS_CACHE.clear()
    with sqlite3.connect(raw / "codemaps.db") as conn:
        conn.execute("DELETE FROM state_payload_parts WHERE name='atlas'")
    corrupt = json.loads(mcp_server.inspect_file("MAIN::Panel.tsx", format="json", target_root=str(tmp_path)))
    assert corrupt[metadata_key]["status"] == "unavailable"
    assert corrupt[metadata_key]["record_count"] is None
    assert corrupt[metadata_key]["reason"] == "primary_file_metadata_read_failed"
    assert corrupt["source_grounded_for_inspection"], "Optional metadata failure does not erase existing source grounding"
    assert not corrupt["one_shot_edit_ready"]
    def unavailable(*a, **k):
        raise RuntimeError("isolated metadata identity unavailable")
    monkeypatch.setattr(mcp_server, "_artifact_cache_key", unavailable)
    failed = json.loads(mcp_server.inspect_file("MAIN::Panel.tsx", format="json", target_root=str(tmp_path)))
    assert failed[metadata_key]["status"] == "unavailable"
    assert not failed["one_shot_edit_ready"]


def persist(tmp_path, content, *, expected=None, algorithm="sha256"):
    root = tmp_path / "repo"
    root.mkdir()
    source = root / "a.ts"
    source.write_bytes(content.encode("utf-8"))
    expected = content if expected is None else expected
    digest = hashlib.new(algorithm, expected.encode("utf-8")).hexdigest()
    atlas = {"MAIN": {"root_path": str(root), "project_type": "typescript", "files": {
        "a.ts": {"language": "typescript", "hash": digest, "size": source.stat().st_size, "symbols": []}},
        "dependencies": {}, "symbols": []}}
    store = ArtifactStore(raw_dir=tmp_path)
    store.use_sqlite = True
    store.backend = "hybrid_sqlite"
    store.db_manager = SQLiteManager(tmp_path / "codemaps.db")
    store._schema_initialized = False
    store._ensure_schema()
    store._save_atlas_to_sqlite(atlas)
    return store, source, atlas


def row_for(store):
    with store.db_manager.get_connection() as conn:
        return dict(conn.execute("SELECT * FROM source_snapshots WHERE project_key='MAIN' AND rel_path='a.ts'").fetchone())


def wire_reader(monkeypatch, tmp_path):
    monkeypatch.setattr(source_snapshot_reader, "DB_PATH", tmp_path / "codemaps.db")
    monkeypatch.setattr(source_snapshot_reader, "record_honesty_event", lambda **kw: None)
    monkeypatch.setattr(source_evidence, "record_honesty_event", lambda **kw: None)


@pytest.mark.parametrize("algorithm", ["md5", "sha256"])
@pytest.mark.parametrize("content", ["", "\ufeffexport const a = 1;\r\n", "export const a = 'ç';\n"])
def test_writer_preserves_exact_text_and_reader_accepts_verified_empty_content(tmp_path, monkeypatch, algorithm, content):
    store, source, atlas = persist(tmp_path, content, algorithm=algorithm)
    row = row_for(store)
    assert row["content"] == content
    assert row["status"] == "ok"
    assert row["content_hash"] == hashlib.new(algorithm, content.encode("utf-8")).hexdigest()
    source.unlink()  # A valid immutable row remains usable without live source.
    wire_reader(monkeypatch, tmp_path)
    assert source_snapshot_reader.load_source_text("MAIN", "a.ts", allow_live_fallback=False) == content


def test_writer_does_not_copy_old_atlas_hash_onto_new_content(tmp_path, monkeypatch):
    store, source, atlas = persist(tmp_path, "export const a = 2;", expected="export const a = 1;")
    row = row_for(store)
    assert row["status"] == "content_mismatch"
    assert row["content_hash"] == hashlib.sha256(row["content"].encode()).hexdigest()
    assert row["content_hash"] != atlas["MAIN"]["files"]["a.ts"]["hash"]
    wire_reader(monkeypatch, tmp_path)
    assert source_snapshot_reader.load_source_text("MAIN", "a.ts", allow_live_fallback=False) is None


@pytest.mark.parametrize("mutation", ["content", "atlas", "missing_identity"])
def test_legacy_bad_rows_are_rejected_by_reader_and_mcp(tmp_path, monkeypatch, mutation):
    from tools.mcp import server

    store, source, atlas = persist(tmp_path, "export const a = 1;")
    wire_reader(monkeypatch, tmp_path)
    row = row_for(store)
    context = {"file_id": row["file_id"], "atlas_node": "MAIN::a.ts", "atlas_relative_path": "a.ts"}
    monkeypatch.setattr(server, "_sqlite_file_context_from_raw", lambda *a: ("MAIN::a.ts", context))
    assert server._source_snapshot_content_for_ref(tmp_path, "MAIN::a.ts") == source.read_text()
    assert server._source_grounding_status(tmp_path, context, source)["drift_check_status"] == "match"
    with store.db_manager.get_connection() as conn:
        if mutation == "content":
            conn.execute("UPDATE source_snapshots SET content = 'wrong old snapshot';")
        elif mutation == "atlas":
            conn.execute("UPDATE files SET hash = ?;", ("a" * 64,))
        else:
            conn.execute("UPDATE source_snapshots SET content_hash = '';")
    assert source_snapshot_reader.load_source_text("MAIN", "a.ts", allow_live_fallback=False) is None
    assert server._source_snapshot_content_for_ref(tmp_path, "MAIN::a.ts") == ""
    status = server._source_grounding_status(tmp_path, context, source)
    assert status["source_snapshot_status"] != "ok"
    assert status["drift_check_status"] == "not_available"
    assert status["target_source_snippets"] == []


def test_mcp_live_check_does_not_normalize_changed_newlines(tmp_path):
    from tools.mcp import server

    store, source, _ = persist(tmp_path, "export const a = 1;\n")
    row = row_for(store)
    context = {"file_id": row["file_id"], "atlas_node": "MAIN::a.ts", "atlas_relative_path": "a.ts"}
    source.write_bytes(b"export const a = 1;\r\n")
    status = server._source_grounding_status(tmp_path, context, source)
    assert status["source_snapshot_status"] == "ok"
    assert status["drift_check_status"] == "mismatch"


def test_matching_metadata_does_not_authorize_changed_live_source(tmp_path, monkeypatch):
    source = tmp_path / "a.ts"
    old = "export const a = 1;"
    source.write_text(old)
    stat = source.stat()
    entry = {"hash": hashlib.sha256(old.encode()).hexdigest(), "size": stat.st_size, "mtime": stat.st_mtime}
    source.write_text("export const a = 2;")
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    monkeypatch.setattr(source_evidence, "load_source_text", lambda *a, **kw: None)
    monkeypatch.setattr(source_evidence, "record_honesty_event", lambda **kw: None)
    assert source_evidence.read_atlas_bound_source(component="test", project="MAIN", project_root=tmp_path,
        rel_path="a.ts", atlas_entry=entry, reason="same size and mtime") is None


def test_writer_refuses_escape_before_native_source_read(tmp_path, monkeypatch):
    store, source, atlas = persist(tmp_path, "export const a = 1;")
    outside = source.parent.parent / "outside.ts"
    outside.write_bytes(b"outside source must not be read")
    metadata = dict(atlas["MAIN"]["files"]["a.ts"])
    metadata["hash"] = hashlib.sha256(outside.read_bytes()).hexdigest()
    atlas["MAIN"]["files"] = {"../outside.ts": metadata}
    original_open, outside_reads = Path.open, []
    def track(path, *args, **kwargs):
        if path == Path(native_filesystem_path(outside)):
            outside_reads.append(path)
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", track)
    store._save_atlas_to_sqlite(atlas)
    with store.db_manager.get_connection() as conn:
        row = conn.execute(
            "SELECT status, content, content_hash FROM source_snapshots "
            "WHERE project_key='MAIN' AND rel_path='../outside.ts'"
        ).fetchone()
    assert tuple(row) == ("error", "", "")
    assert outside_reads == []


def test_snapshot_from_different_atlas_does_not_satisfy_callers_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(source_evidence, "load_source_text", lambda *a, **kw: "new snapshot")
    monkeypatch.setattr(source_evidence, "record_honesty_event", lambda **kw: None)
    assert source_evidence.read_atlas_bound_source(component="test", project="MAIN", project_root=tmp_path,
        rel_path="a.ts", atlas_entry={"hash": hashlib.sha256(b"old snapshot").hexdigest()}, reason="stale caller") is None


@pytest.mark.parametrize("state", ["missing", "too_large", "error"])
def test_writer_failure_never_copies_atlas_identity_without_content(tmp_path, monkeypatch, state):
    from tools.core import artifact_store, honesty_telemetry

    store, source, atlas = persist(tmp_path, "export const a = 1;")
    monkeypatch.setattr(honesty_telemetry, "record_honesty_event", lambda **kw: None)
    if state == "missing":
        source.unlink()
    elif state == "too_large":
        monkeypatch.setitem(artifact_store.DYNAMIC_CONFIG, "source_snapshot_max_bytes", 1)
    else:
        original_open = Path.open
        def unavailable(path, *args, **kwargs):
            if path == Path(native_filesystem_path(source)):
                raise PermissionError("fixture read denied")
            return original_open(path, *args, **kwargs)
        monkeypatch.setattr(Path, "open", unavailable)
    store._save_atlas_to_sqlite(atlas)
    row = row_for(store)
    assert row["status"] == state
    assert row["content_hash"] == ""


@pytest.mark.parametrize("tamper", [False, True])
def test_symbol_span_validator_cannot_validate_lines_against_unbound_text(tmp_path, monkeypatch, tamper):
    from tools import validate_symbol_span_integrity as validator

    store, _, _ = persist(tmp_path, "export const a = 1;\n")
    row = row_for(store)
    with store.db_manager.get_connection() as conn:
        conn.execute("INSERT INTO symbols (file_id, name, type, line, char, end_line, source_lines, export_status) "
                     "VALUES (?, 'a', 'variable', 1, 0, 1, 'L1-L1', 'exported');", (row["file_id"],))
        conn.execute("INSERT INTO symbols (file_id, name, type, line, char, end_line, source_lines, export_status) "
                     "VALUES (?, 'b', 'variable', 1, 0, 1, 'L1-L1', 'exported');", (row["file_id"],))
        if tamper:
            conn.execute("UPDATE source_snapshots SET content = 'different same-line text';")
    monkeypatch.setattr(validator, "DB_PATH", tmp_path / "codemaps.db")
    monkeypatch.setattr(validator, "save_json_atomic", lambda *a: None)
    monkeypatch.setattr(validator, "save_text_atomic", lambda *a: None)
    checked = []
    original = validator.snapshot_content_status
    def observed(*args, **kwargs):
        checked.append(args)
        return original(*args, **kwargs)
    monkeypatch.setattr(validator, "snapshot_content_status", observed)
    result = validator.validate_symbol_span_integrity()
    assert len(checked) == 1  # Hash once per file, not once per symbol.
    assert result["summary"]["status"] == ("FAIL" if tamper else "PASS")


@pytest.mark.parametrize("bad_hash", ["", "hash-fixture", "err", "a" * 40])
def test_unknown_content_identity_never_counts_as_a_match(bad_hash):
    assert source_text_hash("source", bad_hash) is None
    assert snapshot_content_status("source", bad_hash, bad_hash) == "identity_unavailable"


@pytest.mark.parametrize("tamper", [False, True])
def test_validator_checks_actual_text_not_just_equal_hash_columns(tmp_path, monkeypatch, tamper):
    from tools import validate_source_snapshot_store as validator

    store, _, atlas = persist(tmp_path, "")
    if tamper:
        with store.db_manager.get_connection() as conn:
            conn.execute("UPDATE source_snapshots SET content = 'tampered';")
    monkeypatch.setattr(validator, "DB_PATH", tmp_path / "codemaps.db")
    monkeypatch.setattr(validator, "load_atlas_data", lambda: atlas)
    monkeypatch.setattr(validator, "set_runtime_project_filter", lambda *a: None)
    monkeypatch.setattr(validator, "project_runtime_atlas", lambda value: (value, {"analyzed_projects": ["MAIN"]}))
    monkeypatch.setattr(validator, "save_json_atomic", lambda *a: None)
    monkeypatch.setattr(validator, "save_text_atomic", lambda *a: None)
    result = validator.validate_source_snapshot_store(["MAIN"])
    assert result["summary"]["status"] == ("FAIL" if tamper else "PASS")
    content_check = next(row for row in result["checks"] if row["name"] == "stored_text_matches_content_identity")
    assert content_check["passed"] is not tamper
