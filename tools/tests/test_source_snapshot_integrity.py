from __future__ import annotations

import hashlib
import inspect
import json
import math
from copy import deepcopy
from contextlib import contextmanager
import os
from pathlib import Path
import time

import pytest

from tools.core.artifact_store import ArtifactStore
from tools.core.db import SQLiteManager
from tools.core import source_evidence, source_snapshot_reader
from tools.core.source_snapshot_integrity import snapshot_content_status, source_text_hash
from tools.core.unmanaged_atomic_io import native_filesystem_path
from tools.mcp import server as mcp_server

ROOT = Path(__file__).resolve().parents[2]

from tools.tests.test_state_flow_jsx_prop_callback_contract import jsx_atlas_run as wiring_view_atlas_run


def _record_snapshot_cost_property(record, case_id, key, value):
    try:
        record(f"sage_snapshot_cost_v1.{case_id}.{key}", value)
    except Exception:
        # Missing properties become explicit unavailable telemetry in the reader;
        # reporting must not replace the test's own outcome or primary exception.
        pass


@contextmanager
def _snapshot_cost_phase(record, case_id, phase):
    try:
        started = time.perf_counter()
    except Exception:
        started = None
    try:
        yield
    finally:
        try:
            elapsed = time.perf_counter() - started
            value = elapsed if math.isfinite(elapsed) and elapsed >= 0 else "unavailable"
        except Exception:
            value = "unavailable"
        _record_snapshot_cost_property(record, case_id, phase, value)


def _record_snapshot_writer_cost(record, case_id, profile):
    fields = (
        "state_payload_serialize_seconds", "state_payload_encode_seconds",
        "state_payload_hash_seconds", "state_payload_sqlite_seconds",
        "state_payload_storage_mode", "state_payload_part_count",
    )
    source = profile if type(profile) is dict else {}
    for field in fields:
        value = source.get(field)
        if field == "state_payload_storage_mode":
            valid = type(value) is str and value in {"inline_json", "partitioned_json_v1"}
        elif field == "state_payload_part_count":
            valid = type(value) is int and value >= 0
        else:
            valid = type(value) in (int, float) and math.isfinite(value) and value >= 0
        _record_snapshot_cost_property(record, case_id, "writer." + field,
                                       value if valid else "unavailable")


@pytest.fixture(scope="module")
def safeparse_view_inputs(tmp_path_factory):
    import subprocess
    from tools.tests.test_react_contracts import _safeparse_cases, _effect_safeparse_cases, _effect_merge_cases, _safeparse_source, _effect_safeparse_source
    from tools.engines import generate_atlas as atlas
    from tools.tests.test_react_contracts import _early_return_cases, _early_return_source
    cases = _safeparse_cases()
    names = ("positive", "unicode", "empty", "record_budget", "node_budget")
    effects = _effect_safeparse_cases()
    cases.update({"effect_" + name: effects[name] for name in names})
    names += tuple("effect_" + name for name in names)
    merges = _effect_merge_cases()
    cases.update({"merge_" + name: merges[name] for name in ("positive", "unicode", "empty", "record_budget", "node_budget")})
    names += tuple("merge_" + name for name in ("positive", "unicode", "empty", "record_budget", "node_budget"))
    early = _early_return_cases()
    cases.update({"early_" + name: early[name] for name in ("positive", "unicode", "empty", "record_budget", "node_budget")})
    names += tuple("early_" + name for name in ("positive", "unicode", "empty", "record_budget", "node_budget"))
    # Distinct selected bindings, one real file containing both finite lanes.
    combined = _effect_safeparse_source().replace("Schema", "EffectOwner").replace("Panel", "EffectPanel").replace("result", "effectResult")
    cases["both"] = (_safeparse_source() + combined, 0)
    names += ("both",)
    from tools.tests.test_react_contracts import _effect_merge_source
    merge_source = _effect_merge_source()
    for before, after in (("useEffect", "mergeEffect"), ("Schema", "MergeOwner"), ("Panel", "MergePanel"),
            ("onValueChange", "mergeChange"), ("formValues", "mergeInput"), ("result", "mergeResult")):
        merge_source = merge_source.replace(before, after)
    merge_source = merge_source.replace("{mergeEffect}", "{useEffect as mergeEffect}")
    cases["triple"] = (cases["both"][0] + merge_source, 0)
    names += ("triple",)
    cases["quadruple"] = (cases["triple"][0] + _early_return_source(), 0)
    names += ("quadruple",)
    from tools.tests.test_react_contracts import _factory_return_cases
    factories = _factory_return_cases()
    cases.update({"factory_" + name: factories[name] for name in ("block", "record_budget")})
    names += ("factory_block", "factory_record_budget")
    from tools.tests.test_react_contracts import _factory_argument_cases
    arguments = _factory_argument_cases()
    cases.update({"argument_" + name:arguments[name] for name in ("positive","unicode","record_budget")})
    names += ("argument_positive","argument_unicode","argument_record_budget")
    from tools.tests.test_react_contracts import _resolver_factory_cases
    resolvers=_resolver_factory_cases()
    cases.update({"resolver_"+name:resolvers[name] for name in ("positive","unicode","empty","record_budget","node_budget")})
    names += tuple("resolver_"+name for name in ("positive","unicode","empty","record_budget","node_budget"))
    cases["five_lanes"]=(cases["quadruple"][0]+resolvers["positive"][0],0)
    names += ("five_lanes",)
    directory = tmp_path_factory.mktemp("safeparse-view")
    paths = []
    for name in names:
        path = directory / (name + ".tsx")
        path.write_bytes(cases[name][0].encode("utf-8"))
        paths.append(str(path))
    result = subprocess.run(["node", str(ROOT / "tools/engines/ast_sequencer.cjs"), "--batch-json", *paths],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stderr
    native = json.loads(result.stdout)
    output = {}
    for name, path in zip(names, paths):
        source = cases[name][0]
        rows = native[path]
        output[name] = (source, {"type": "file", "exports": [], "imports": [], "symbols": [], "workspace_rel": "Panel.tsx",
            "hash": hashlib.sha256(source.encode("utf-8")).hexdigest(), "ast_contract_version": atlas.AST_CONTRACT_VERSION,
            "parser_evidence": atlas._file_parser_evidence(rows, "typescript"),
            "client_safeparse_success_evidence": atlas._client_safeparse_success_evidence(rows, source),
            "effect_safeparse_callback_evidence": atlas._effect_safeparse_callback_evidence(rows, source),
            "effect_safeparse_merge_evidence": atlas._effect_safeparse_merge_evidence(rows, source),
            "safeparse_early_return_evidence": atlas._safeparse_early_return_evidence(rows, source),
            "factory_return_construction_evidence": atlas._factory_return_construction_evidence(rows, source),
            "factory_argument_const_evidence": atlas._factory_argument_const_evidence(rows, source),
            "resolver_factory_evidence": atlas._resolver_factory_evidence(rows, source),
            "client_local_parser_result_evidence": atlas._client_local_parser_result_evidence(rows, source),
            "client_local_parser_input_sink_evidence": atlas._client_local_parser_input_sink_evidence(rows, source),
            "client_imported_parser_evidence": atlas._client_imported_parser_evidence(rows, source),
            "response_json_return_evidence": atlas._response_json_return_evidence(rows, source),
            "wiring_source_observations": atlas.normalize_wiring_source_observations(rows, source)})
    return output


@pytest.mark.parametrize("damage", ["none", "partial", "legacy", "snapshot", "snapshot_changed", "live", "ast", "parser", "hash", "header_hash", "version", "scope", "offset", "schema_claim", "factory_claim", "argument_claim", "runtime_claim", "mutation", "mutation_zero", "extra", "count", "count_bool", "unknown_status", "partial_lie", "omitted", "type_only", "method", "span", "duplicate", "hidden_corrupt", "hidden_duplicate", "selected_name", "selected_start", "selected_end", "selected_kind"])
def test_factory_construction_view_strict_full_lane_and_selected_definition(safeparse_view_inputs, damage):
    from tools.inspect_target import project_factory_return_construction
    source, original = safeparse_view_inputs["factory_record_budget" if damage in {"partial", "hidden_corrupt", "hidden_duplicate"} else "factory_block"]
    info = deepcopy(original)
    lane = info["factory_return_construction_evidence"]
    row = lane["records"][0]
    symbol = {"name": row["factory_name"], "type": "Arrow", "exported": True, "export_kind": "named",
        "start": row["spans"]["factory_binding"]["start"], "end": row["spans"]["factory"]["end"]}
    binding = "snapshot_and_live_match"
    if damage == "legacy": info.pop("factory_return_construction_evidence")
    elif damage == "snapshot": source = None
    elif damage == "snapshot_changed": source += "\n// changed"
    elif damage == "live": binding = "snapshot_match_live_unverified"
    elif damage == "ast": info["ast_contract_version"] = "old"
    elif damage == "parser": info["parser_evidence"]["status"] = "degraded"
    elif damage == "hash": info["hash"] = "0" * 64
    elif damage == "header_hash": lane["source_hash"] = "0" * 64
    elif damage in {"version", "scope", "offset"}: lane["offset_unit" if damage == "offset" else damage] = "old"
    elif damage.endswith("_claim"): lane[{"schema_claim":"schema_authority", "factory_claim":"factory_semantics", "argument_claim":"argument_semantics", "runtime_claim":"runtime_proof"}[damage]] = "proved"
    elif damage in {"mutation", "mutation_zero"}: lane["mutation_proposed"] = True if damage == "mutation" else 0
    elif damage == "extra": lane["extra"] = True
    elif damage in {"count", "count_bool"}: lane["record_count"] = 2 if damage == "count" else True
    elif damage == "unknown_status": lane["status"] = []
    elif damage == "partial_lie": lane.update(status="partial", reason="record_budget_exceeded", omitted=1)
    elif damage == "omitted": lane["omitted"] = 1
    elif damage == "type_only": row["import_binding"]["type_only"] = True
    elif damage == "method": row["methods"][0] = "different"
    elif damage == "span": row["spans"]["returned"]["line"] += 1
    elif damage == "duplicate": lane["records"] *= 2; lane["record_count"] = 2
    elif damage == "hidden_corrupt": lane["records"][-1]["spans"]["returned"]["line"] += 1
    elif damage == "hidden_duplicate": lane["records"][-1] = deepcopy(row)
    elif damage.startswith("selected_"):
        key = {"selected_name":"name", "selected_start":"start", "selected_end":"end", "selected_kind":"type"}[damage]
        symbol[key] = symbol[key] + 1 if key in {"start", "end"} else "other"
    result = project_factory_return_construction(info, symbol, binding, snapshot_content=source, target_ref="MAIN::Factory.tsx")
    assert result["schema_authority"] == result["factory_semantics"] == result["argument_semantics"] == "not_established"
    assert result["runtime_proof"] == "not_performed" and result["edit_authority"] == "not_granted" and result["mutation_proposed"] is False
    if damage in {"none", "partial"}:
        assert result["status"] == "observed" and result["producer_omitted"] == int(damage == "partial")
        assert result["methods"] == (["string"] if damage == "partial" else ["string", "regex"])
        assert result["return_span"] == row["spans"]["returned"]
    elif damage in {"selected_name", "selected_start", "selected_end"}:
        assert result["status"] == "not_observed" and "methods" not in result
    else:
        assert result["status"] == "unavailable" and "methods" not in result


def test_factory_construction_enrichment_preserves_all_older_records_under_budget(safeparse_view_inputs):
    from tools.inspect_target import project_safeparse_early_return_evidence
    source, info = safeparse_view_inputs["early_record_budget"]
    base_candidate = {"status": "source_candidate", "source_binding": "snapshot_and_live_match", "target": {"atlas_ref": "MAIN::Factory.tsx"}}
    baseline, enriched = {}, {}
    project_safeparse_early_return_evidence(baseline, info, _local_projection_status(info), snapshot_content=source,
        source_candidate=lambda *a: deepcopy(base_candidate))
    project_safeparse_early_return_evidence(enriched, info, _local_projection_status(info), snapshot_content=source,
        source_candidate=lambda *a: {**base_candidate, "return_construction": {"status": "observed", "methods": ["x" * 10000]}})
    view = enriched["safeparse_early_return_evidence"]
    assert view["shown"] == baseline["safeparse_early_return_evidence"]["shown"] > 0
    for row in view["records"]:
        candidate = row["factory_source_candidate"]
        assert candidate.get("return_construction", {"status":"withheld_budget"}) == {"status":"withheld_budget"}
        candidate.pop("return_construction", None)
    assert enriched == baseline


@pytest.mark.parametrize("damage",["none","unicode","partial","legacy","empty","hash","version","scope","offset","ast","count","count_bool","omitted","partial_lie","status","reason","extra","argument_claim","schema_claim","type_claim","factory_claim","runtime_claim","mutation","mutation_zero","method","call_index","argument_index","kind","binding_scope","reference","initializer","span","duplicate","hidden_corrupt","hidden_duplicate","parent_partial","selected_other","snapshot","live"])
def test_factory_argument_view_full_lane_and_exact_parent(safeparse_view_inputs,monkeypatch,damage):
    from tools import inspect_target as view
    name = "factory_record_budget" if damage == "parent_partial" else "argument_record_budget" if damage in {"partial","hidden_corrupt","hidden_duplicate","selected_other"} else "argument_unicode" if damage == "unicode" else "argument_positive"
    source,original = safeparse_view_inputs[name]
    info = deepcopy(original)
    parent = info["factory_return_construction_evidence"]
    lane = info["factory_argument_const_evidence"]
    selected = parent["records"][-1 if damage == "selected_other" else 0]
    symbol = {"name":selected["factory_name"],"type":"Arrow","exported":True,"export_kind":"named","start":selected["spans"]["factory_binding"]["start"],"end":selected["spans"]["factory"]["end"]}
    binding = "snapshot_and_live_match"
    row = lane["records"][0] if lane["records"] else None
    if damage == "legacy": info.pop("factory_argument_const_evidence")
    elif damage == "empty": lane.update(records=[],record_count=0)
    elif damage == "hash": lane["source_hash"] = "0" * 64
    elif damage in {"version","scope","ast","offset"}: lane[{"ast":"ast_contract_version","offset":"offset_unit"}.get(damage,damage)] = "old"
    elif damage in {"count","count_bool"}: lane["record_count"] = True if damage == "count_bool" else 3
    elif damage == "omitted": lane["omitted"] = 1
    elif damage == "partial_lie": lane.update(status="partial",omitted=1,reason="record_budget_exceeded")
    elif damage == "status": lane["status"] = []
    elif damage == "reason": lane["reason"] = "claimed_complete"
    elif damage == "extra": lane["value"] = "proved"
    elif damage.endswith("_claim"): lane[{"argument_claim":"argument_semantics","schema_claim":"schema_authority","type_claim":"type_compatibility","factory_claim":"factory_semantics","runtime_claim":"runtime_proof"}[damage]] = "proved"
    elif damage in {"mutation","mutation_zero"}: lane["mutation_proposed"] = 0 if damage == "mutation_zero" else True
    elif damage == "method": row["method_name"] = "other"
    elif damage == "call_index": row["call_index"] = 0
    elif damage == "argument_index": row["argument_index"] = 1
    elif damage == "kind": row["kind"] = "object_shorthand"
    elif damage == "binding_scope": row["binding_scope"] = "factory_const"
    elif damage == "reference": row["spans"]["reference"] = row["spans"]["binding"]
    elif damage == "initializer": row["spans"]["initializer"] = row["spans"]["binding"]
    elif damage == "span": row["spans"]["reference"]["line"] += 1
    elif damage == "duplicate": lane["records"].append(deepcopy(row)); lane["record_count"] += 1
    elif damage == "hidden_corrupt": lane["records"][-1]["spans"]["reference"]["line"] += 1
    elif damage == "hidden_duplicate": lane["records"][-1] = deepcopy(row)
    elif damage == "parent_partial": lane.update(status="observed",reason="",source_hash=info["hash"])
    elif damage == "snapshot": source = None
    elif damage == "live": binding = "snapshot_match_live_unverified"
    batches = []
    original_validator = view._utf8_span_validator
    def once(*args):
        batches.append(True)
        return original_validator(*args)
    monkeypatch.setattr(view,"_utf8_span_validator",once)
    construction = view.project_factory_return_construction(info,symbol,binding,snapshot_content=source,target_ref="MAIN::Factory.tsx")
    assert len(batches) <= 1
    if damage in {"snapshot","live"}:
        assert construction["status"] == "unavailable" and "argument_bindings" not in construction
        return
    assert construction["status"] == "observed",construction
    result = construction["argument_bindings"]
    assert result["argument_semantics"] == "not_established" and result["runtime_proof"] == "not_performed"
    assert result["edit_authority"] == "not_granted" and result["mutation_proposed"] is False
    if damage in {"none","unicode","partial"}:
        assert result["status"] == "observed" and result["shown"] == result["record_count"] == 2
        assert result["producer_omitted"] == (2 if damage == "partial" else 0)
        assert result["records"][0]["name"] == "PATTERN" and result["records"][0]["binding_scope"] == "module_const"
        assert all(row["source_followup"]["file_path"] == "MAIN::Factory.tsx" for row in result["records"])
    elif damage in {"empty","selected_other"}:
        assert result["status"] == "not_observed" and not result["records"] and "not_absence" in result["reason"]
        if damage == "selected_other": assert result["coverage_status"] == "partial" and result["producer_omitted"] == 2
    else:
        assert result["status"] == "unavailable" and not result["records"],(damage,result)


@pytest.mark.parametrize("huge",[False,True])
def test_factory_argument_budget_keeps_old_rows_construction_and_shared_cap(safeparse_view_inputs,huge):
    from tools.inspect_target import project_safeparse_early_return_evidence,AGENT_INSPECTION_MAX_TARGET_SPANS
    source,info = safeparse_view_inputs["early_record_budget"]
    construction = {"status":"observed","methods":["string","regex"],"source_followup":{"tool":"inspect_file","file_path":"MAIN::Factory.tsx","line_start":1,"line_end":2}}
    base = {"status":"source_candidate","source_binding":"snapshot_and_live_match","return_construction":construction}
    arguments = {"status":"observed","reason":"source_const_associations_only","records":[{"name":"x" * 10000 if huge else str(i)} for i in range(64)],"record_count":64,"shown":64,"omitted":0,"producer_omitted":2}
    baseline,enriched = {},{}
    project_safeparse_early_return_evidence(baseline,info,_local_projection_status(info),snapshot_content=source,source_candidate=lambda *a:deepcopy(base))
    project_safeparse_early_return_evidence(enriched,info,_local_projection_status(info),snapshot_content=source,source_candidate=lambda *a:{**deepcopy(base),"return_construction":{**deepcopy(construction),"argument_bindings":deepcopy(arguments)}})
    section = enriched["safeparse_early_return_evidence"]
    assert section["shown"] == baseline["safeparse_early_return_evidence"]["shown"] > 0
    new_count = 0
    for row in section["records"]:
        parent = row["factory_source_candidate"].get("return_construction",{})
        group = parent.pop("argument_bindings",None)
        if group and group["status"] != "withheld_budget":
            assert group["shown"] + group["omitted"] == group["record_count"] == 64 and group["producer_omitted"] == 2
            new_count += group["shown"]
            if huge: assert group["shown"] == 0 and "budget" in group["reason"]
    assert new_count + section["shown"] <= AGENT_INSPECTION_MAX_TARGET_SPANS
    assert enriched == baseline


@pytest.mark.parametrize("name", ["positive", "unicode", "empty", "record_budget", "node_budget"])
def test_early_return_view_source_only_and_exact_followups(safeparse_view_inputs, name):
    from tools.inspect_target import project_safeparse_early_return_evidence
    from tools.core.agent_packet_budget import COMPACT_AGENT_PACKET_TOKENS, context_budget_profile
    source, info = safeparse_view_inputs["early_" + name]
    original, payload = deepcopy(info), {}
    project_safeparse_early_return_evidence(payload, info, _local_projection_status(info), snapshot_content=source)
    view = payload["safeparse_early_return_evidence"]
    assert info == original
    assert view["schema_authority"] == view["type_compatibility"] == view["factory_semantics"] == view["continuation_safety"] == "not_established"
    assert view["runtime_proof"] == "not_performed" and view["edit_authority"] == "not_granted" and view["mutation_proposed"] is False
    if name == "node_budget":
        assert view["status"] == "unavailable" and view["record_count"] is None
    else:
        assert view["status"] == ("partial" if name == "record_budget" else "observed")
        assert view["source_binding"] == "snapshot_and_live_match"
        assert view["shown"] + view["omitted"] == view["record_count"]
        assert view["producer_omitted"] == int(name == "record_budget")
        if name == "empty":
            assert view["shown"] == view["record_count"] == 0 and view["empty_result_meaning"] == "not_absence_or_clean_code"
        else:
            assert view["shown"] > 0
            encoded = source.encode("utf-8")
            for record, stored in zip(view["records"], info["safeparse_early_return_evidence"]["records"]):
                spans = stored["spans"]
                assert record["span"] == spans["guard"]
                assert encoded[record["span"]["start"]:record["span"]["end"]].decode("utf-8").startswith("if (!isValid")
                for key, role in (("source_followup", "callback_binding"), ("input_source_followup", "input_declaration"), ("factory_source_followup", "factory_import")):
                    followup = record[key]
                    assert followup["file_path"] == "MAIN::Panel.tsx" and followup["tool"] == "inspect_file"
                    assert followup["evidence_focus"] == "safeparse_early_return" and followup["line_start"] == spans[role]["line"]
                    assert followup["line_end"] == spans["guard" if key == "source_followup" else role]["end_line"]
    assert context_budget_profile(json.dumps(payload, ensure_ascii=False), budget_tokens=COMPACT_AGENT_PACKET_TOKENS)["status"] == "pass"


@pytest.mark.parametrize("damage", ["legacy", "missing_snapshot", "changed_snapshot", "drift", "truthy_indexed", "file_hash", "snapshot_hash", "ast", "header_ast", "parser", "version", "scope", "hash", "mutation", "zero_mutation", "factory_claim", "continuation_claim", "schema_claim", "runtime_claim", "type_claim", "extra", "count", "bool_count", "omitted", "records_type", "role", "bool_offset", "line", "bounds", "relation", "tokens", "factory_token", "guard_token", "return_token", "duplicate", "hidden_corrupt", "hidden_duplicate", "unknown_status", "partial_lie", "unavailable_lie"])
def test_early_return_view_refuses_unbound_corrupt_promoted(safeparse_view_inputs, damage):
    from tools.inspect_target import project_safeparse_early_return_evidence
    source, original = safeparse_view_inputs["early_record_budget" if damage.startswith("hidden_") else "early_positive"]
    info = deepcopy(original)
    status = _local_projection_status(info)
    lane = info["safeparse_early_return_evidence"]
    spans = lane["records"][0]["spans"]
    if damage == "legacy": info.pop("safeparse_early_return_evidence")
    elif damage == "missing_snapshot": source = None
    elif damage == "changed_snapshot": source += "// changed"
    elif damage == "drift": status["drift_check_status"] = "drift"
    elif damage == "truthy_indexed": status["indexed"] = 1
    elif damage == "file_hash": info["hash"] = "0" * 64
    elif damage == "snapshot_hash": status["source_snapshot_hash"] = "0" * 64
    elif damage == "ast": info["ast_contract_version"] = "old"
    elif damage == "header_ast": lane["ast_contract_version"] = "old"
    elif damage == "parser": info["parser_evidence"]["status"] = "degraded"
    elif damage == "version": lane["version"] = "old"
    elif damage == "scope": lane["scope"] = "universal"
    elif damage == "hash": lane["source_hash"] = "0" * 64
    elif damage == "mutation": lane["mutation_proposed"] = True
    elif damage == "zero_mutation": lane["mutation_proposed"] = 0
    elif damage == "factory_claim": lane["factory_semantics"] = "validated"
    elif damage == "continuation_claim": lane["continuation_safety"] = "safe"
    elif damage == "schema_claim": lane["schema_authority"] = "validated"
    elif damage == "runtime_claim": lane["runtime_proof"] = "passed"
    elif damage == "type_claim": lane["type_compatibility"] = "verified"
    elif damage == "extra": lane["validated"] = True
    elif damage == "count": lane["record_count"] = 2
    elif damage == "bool_count": lane["record_count"] = True
    elif damage == "omitted": lane["omitted"] = True
    elif damage == "records_type": lane["records"] = {}
    elif damage == "role": spans.pop("success_binding")
    elif damage == "bool_offset": spans["guard"]["start"] = True
    elif damage == "line": spans["guard"]["line"] += 1
    elif damage == "bounds": spans["guard"]["end"] = len(source) * 4
    elif damage == "relation": spans["guard_ref"] = deepcopy(spans["success_binding"])
    elif damage == "tokens": spans["success_property"]["start"] += 1
    elif damage == "factory_token": spans["factory_ref"]["end"] -= 1
    elif damage == "guard_token": spans["condition"]["start"] += 1
    elif damage == "return_token": spans["return"]["start"] += 1
    elif damage == "duplicate": lane["records"].append(deepcopy(lane["records"][0])); lane["record_count"] += 1
    elif damage == "hidden_corrupt": lane["records"][-1] = {"spans": {}}
    elif damage == "hidden_duplicate": lane["records"][-1] = deepcopy(lane["records"][0])
    elif damage == "unknown_status": lane["status"] = []
    elif damage == "partial_lie": lane.update(status="partial", omitted=1, reason="record_budget_exceeded")
    else: lane.update(status="unavailable", reason="not_observed")
    payload = {}
    project_safeparse_early_return_evidence(payload, info, status, snapshot_content=source)
    if damage == "legacy": assert "safeparse_early_return_evidence" not in payload
    else:
        view = payload["safeparse_early_return_evidence"]
        assert view["status"] == "unavailable" and view["record_count"] is None and view["records"] == []


def test_early_return_view_preserves_older_priority_and_four_lane_coordinate_batch(safeparse_view_inputs, monkeypatch):
    from tools import inspect_target as view
    calls, native = [], view._utf8_span_validator
    def counted(*args, **kwargs):
        calls.append(1)
        return native(*args, **kwargs)
    monkeypatch.setattr(view, "_utf8_span_validator", counted)
    source, info = safeparse_view_inputs["quadruple"]
    projects = (view.project_safeparse_success_evidence, view.project_effect_safeparse_callback_evidence,
        view.project_effect_safeparse_merge_evidence, view.project_safeparse_early_return_evidence)
    payload, cache = {}, {}
    for project in projects:
        previous = deepcopy(payload)
        project(payload, info, _local_projection_status(info), snapshot_content=source, coordinate_cache=cache)
        assert all(payload[key] == value for key,value in previous.items())
    assert len(calls) == 1
    for key in ("client_safeparse_success_evidence", "effect_safeparse_callback_evidence", "effect_safeparse_merge_evidence", "safeparse_early_return_evidence"):
        assert info[key]["record_count"] == 1
    source, info = safeparse_view_inputs["early_positive"]
    view.project_safeparse_early_return_evidence({}, info, _local_projection_status(info), snapshot_content=source, coordinate_cache=cache)
    assert len(calls) == 2, "A different file cannot borrow the cache"
    payload = {"effect_safeparse_merge_evidence": {"records": [{}] * view.AGENT_INSPECTION_MAX_TARGET_SPANS}}
    previous = deepcopy(payload)
    view.project_safeparse_early_return_evidence(payload, info, _local_projection_status(info), snapshot_content=source)
    assert payload["effect_safeparse_merge_evidence"] == previous["effect_safeparse_merge_evidence"]
    assert payload["safeparse_early_return_evidence"]["shown"] == 0
    for crowded, syntax in (({"older": "x" * 25000}, {}), ({}, {"older": {"reason": "x" * 6000}})):
        previous = deepcopy(crowded)
        view.project_safeparse_early_return_evidence(crowded, info, _local_projection_status(info), snapshot_content=source, syntax_sections=syntax)
        assert crowded == previous
    monkeypatch.setattr(view, "_utf8_span_validator", lambda *a, **k: pytest.fail("Empty/unavailable must not build an index"))
    for name in ("early_empty", "early_node_budget"):
        _, info = safeparse_view_inputs[name]
        view.project_safeparse_early_return_evidence({}, info, _local_projection_status(info))


@pytest.mark.parametrize("name",["positive","unicode","empty","record_budget","node_budget"])
def test_resolver_factory_view_source_and_omissions(safeparse_view_inputs,name):
    from tools import inspect_target as view
    source,info=safeparse_view_inputs["resolver_"+name]
    original=deepcopy(info); payload={}
    view.project_resolver_factory_evidence(payload,info,_local_projection_status(info),snapshot_content=source)
    section=payload["resolver_factory_evidence"]
    assert info==original
    assert section["schema_authority"]==section["form_semantics"]==section["argument_semantics"]=="not_established"
    assert section["runtime_proof"]=="not_performed" and section["edit_authority"]=="not_granted" and not section["mutation_proposed"]
    if name=="node_budget": assert section["status"]=="unavailable" and section["record_count"] is None
    else:
        assert section["status"]==("partial" if name=="record_budget" else "observed")
        assert section["record_count"]==section["shown"]+section["omitted"]
        assert section["producer_omitted"]==int(name=="record_budget")
        if name=="empty": assert section["empty_result_meaning"]=="not_absence_or_clean_code"
        for row in section["records"]:
            assert row["source_followup"]["evidence_focus"]=="resolver_factory"
            assert row["factory_source_candidate"]["status"]=="unresolved"
            assert "actual_name" not in row and "formal_name" not in row


@pytest.mark.parametrize("damage",["legacy","snapshot","drift","ast","parser","hash","header_hash","version","scope","claims","mutation","extra","count","count_bool","omitted_bool","partial","role","line","token","module","duplicate","hidden"])
def test_resolver_factory_view_refuses_full_lane(safeparse_view_inputs,damage):
    from tools.inspect_target import project_resolver_factory_evidence
    source,original=safeparse_view_inputs["resolver_record_budget" if damage=="hidden" else "resolver_positive"]
    info=deepcopy(original); status=_local_projection_status(info); lane=info["resolver_factory_evidence"]; row=lane["records"][0]
    if damage=="legacy": info.pop("resolver_factory_evidence")
    elif damage=="snapshot": source=None
    elif damage=="drift": status["drift_check_status"]="changed"
    elif damage=="ast": info["ast_contract_version"]="old"
    elif damage=="parser": info["parser_evidence"]["status"]="degraded"
    elif damage=="hash": info["hash"]="0"*64
    elif damage=="header_hash": lane["source_hash"]="0"*64
    elif damage=="version": lane["version"]="old"
    elif damage=="scope": lane["scope"]="all_sources"
    elif damage=="claims": lane["argument_semantics"]="established"
    elif damage=="mutation": lane["mutation_proposed"]=0
    elif damage=="extra": lane["inferred"]=True
    elif damage=="count": lane["record_count"]+=1
    elif damage=="count_bool": lane["record_count"]=True
    elif damage=="omitted_bool": lane["omitted"]=False
    elif damage=="partial": lane.update(status="partial",omitted=1,reason="record_budget_exceeded")
    elif damage=="role": row["spans"].pop("factory_import_binding")
    elif damage=="line": row["spans"]["resolver_call"]["line"]+=1
    elif damage=="token": row["spans"]["resolver_ref"]["end"]-=1
    elif damage=="module": row["spans"]["resolver_module"]["start"]+=1
    elif damage=="duplicate": lane["records"].append(deepcopy(row)); lane["record_count"]+=1
    elif damage=="hidden": lane["records"][-1]["factory_local_name"]="foreign"
    payload={}; project_resolver_factory_evidence(payload,info,status,snapshot_content=source)
    if damage=="legacy": assert not payload
    else: assert payload["resolver_factory_evidence"]["status"]=="unavailable" and payload["resolver_factory_evidence"]["record_count"] is None


def test_resolver_factory_five_lane_coordinates_and_older_budget(safeparse_view_inputs,monkeypatch):
    from tools import inspect_target as view
    from tools.core.agent_packet_budget import context_budget_profile,COMPACT_AGENT_PACKET_TOKENS,DEFAULT_AGENT_PACKET_BUDGET_TOKENS
    source,info=safeparse_view_inputs["five_lanes"]
    calls=[]; native=view._utf8_span_validator
    def counted(*args,**kwargs): calls.append(1); return native(*args,**kwargs)
    monkeypatch.setattr(view,"_utf8_span_validator",counted)
    payload={}; cache={}
    for project in (view.project_safeparse_success_evidence,view.project_effect_safeparse_callback_evidence,view.project_effect_safeparse_merge_evidence,view.project_safeparse_early_return_evidence,view.project_resolver_factory_evidence):
        prior=deepcopy(payload); project(payload,info,_local_projection_status(info),snapshot_content=source,coordinate_cache=cache)
        assert all(payload[k]==v for k,v in prior.items())
    assert len(calls)==1
    assert info["resolver_factory_evidence"]["record_count"]==1
    assert context_budget_profile(json.dumps(payload),budget_tokens=COMPACT_AGENT_PACKET_TOKENS)["status"]=="pass"
    source,info=safeparse_view_inputs["resolver_positive"]
    for payload,syntax in (({"safeparse_early_return_evidence":{"records":[{}]*view.AGENT_INSPECTION_MAX_TARGET_SPANS}},{}),({}, {"older":{"reason":"x"*6000}}),({"older":"x"*25000},{})):
        before=deepcopy(payload)
        view.project_resolver_factory_evidence(payload,info,_local_projection_status(info),snapshot_content=source,syntax_sections=syntax)
        assert all(payload[k]==v for k,v in before.items())
        section=payload.get("resolver_factory_evidence",{})
        assert not section.get("records")
        if not before.get("older"):
            assert context_budget_profile(json.dumps(payload),budget_tokens=DEFAULT_AGENT_PACKET_BUDGET_TOKENS)["status"]=="pass"


@pytest.mark.parametrize("file",["Resolver.tsx","ResolverFunction.tsx"])
def test_resolver_factory_public_link_and_no_parameter_inference(imported_parser_atlas_run,monkeypatch,file):
    from tools.core.agent_packet_budget import context_budget_profile,DEFAULT_AGENT_PACKET_BUDGET_TOKENS
    from tools.core.atlas_io import load_atlas_data_with_identity
    target,raw,_=imported_parser_atlas_run; primary=load_atlas_data_with_identity(raw).identity
    monkeypatch.setattr(mcp_server,"_atlas",lambda *a,**k:pytest.fail("No full Atlas/cache fallback"))
    args=dict(file_path="MAIN::"+file,target_root=str(target),evidence_focus="resolver_factory",line_start=1,line_end=2)
    serialized=mcp_server.inspect_file(format="json",**args); machine=json.loads(serialized); section=machine["resolver_factory_evidence"]
    assert section["status"]=="observed" and section["record_count"]==section["shown"]==1
    row=section["records"][0]; candidate=row["factory_source_candidate"]
    assert candidate["status"]=="source_candidate" and candidate["source_binding"]=="snapshot_and_live_match"
    assert candidate["return_construction"]["status"]=="observed" and candidate["return_construction"]["methods"]==["string","regex"]
    association=candidate.get("call_parameter_binding",{})
    assert association["status"]==("observed" if file=="ResolverFunction.tsx" else "unavailable")
    assert association["argument_value_semantics"]==association["parameter_usage"]=="not_established"
    assert "single_parameter" not in candidate["return_construction"]
    assert row["argument_count"]==1 and row["resolver_local_name"]=="resolverAdapter"
    assert section["form_semantics"]==section["argument_semantics"]==section["schema_authority"]=="not_established"
    for key in ("source_followup","factory_source_followup","resolver_source_followup"):
        follow=row[key]; result=json.loads(mcp_server.inspect_file(format="json",target_root=str(target),**{k:v for k,v in follow.items() if k!="tool"}))
        assert result["one_shot_edit_ready"]
    follow=candidate["source_followup"]
    result=json.loads(mcp_server.inspect_file(format="json",target_root=str(target),**{k:v for k,v in follow.items() if k!="tool"}))
    assert result["one_shot_edit_ready"]
    brief=mcp_server.inspect_file(**args)
    assert json.loads(next(line.split(": ",1)[1] for line in brief.splitlines() if line.startswith("resolver_factory_evidence: ")))==section
    for text in (serialized,brief): assert context_budget_profile(text,budget_tokens=DEFAULT_AGENT_PACKET_BUDGET_TOKENS)["status"]=="pass"
    enriched=json.loads(mcp_server.inspect_file("MAIN::"+file,format="json",target_root=str(target)))
    monkeypatch.setattr(mcp_server,"project_resolver_factory_evidence",lambda *a,**k:None)
    older=json.loads(mcp_server.inspect_file("MAIN::"+file,format="json",target_root=str(target)))
    assert {k:v for k,v in enriched.items() if k!="resolver_factory_evidence"}==older
    assert load_atlas_data_with_identity(raw).identity==primary


@pytest.mark.parametrize("partitioned",[False,True])
@pytest.mark.parametrize("long_path",[False,True])
@pytest.mark.parametrize("damage",["none","candidate_drift","caller_drift","missing_snapshot","bad_snapshot","corrupt_primary","lane_legacy","lane_corrupt","lane_hidden","target_ast","target_hash","missing_import","bad_import","duplicate_import","target_export"])
def test_resolver_factory_actual_primary_refusals(imported_parser_atlas_run,tmp_path,monkeypatch,partitioned,damage,long_path):
    import sqlite3
    target,_,original=imported_parser_atlas_run
    local=tmp_path/"target"
    if long_path:
        local=local/("deep-source-"*8)/("fixture-"*10)
        assert len(str(local))>260
    Path(native_filesystem_path(local)).mkdir(parents=True)
    atlas=deepcopy(original); files=atlas["MAIN"]["files"]
    for file in files: Path(native_filesystem_path(local/file)).write_bytes((target/file).read_bytes())
    atlas["MAIN"]["root_path"]=str(local)
    caller=files["Resolver.tsx"]; candidate=files["never_execute.ts"]
    if damage=="lane_legacy": caller.pop("resolver_factory_evidence")
    elif damage=="lane_corrupt": caller["resolver_factory_evidence"]["records"][0]["resolver_local_name"]="foreign"
    elif damage=="lane_hidden":
        lane=caller["resolver_factory_evidence"]; lane["records"]=[deepcopy(lane["records"][0]) for _ in range(2)]; lane["record_count"]=2; lane["records"][-1]["argument_count"]=True
    elif damage=="target_ast": candidate["ast_contract_version"]="old"
    elif damage=="target_hash": candidate["hash"]="0"*64
    elif damage=="missing_import": caller.pop("direct_import_binding_evidence")
    elif damage in {"bad_import","duplicate_import"}:
        bindings=caller["direct_import_binding_evidence"]["records"]; binding=next(r for r in bindings if r["localName"]=="resolverFactory")
        if damage=="bad_import": binding["typeOnly"]=True
        else: bindings.append(deepcopy(binding))
    elif damage=="target_export": candidate["symbols"][0]["exported"]=False
    raw=tmp_path/".raw"; store=ArtifactStore(raw_dir=raw); store.use_sqlite,store.backend=True,"hybrid_sqlite"; store._ensure_schema()
    if partitioned: store.state_payload_inline_limit_bytes=1
    store._save_payload_to_state_table("atlas",atlas); store._save_atlas_to_sqlite(atlas)
    with sqlite3.connect(raw/"codemaps.db") as conn:
        conn.execute("UPDATE projects SET path='.' WHERE project_key='MAIN'")
        if damage=="missing_snapshot": conn.execute("DELETE FROM source_snapshots WHERE rel_path='never_execute.ts'")
        elif damage=="bad_snapshot": conn.execute("UPDATE source_snapshots SET content=content || '//changed' WHERE rel_path='Resolver.tsx'")
        elif damage=="corrupt_primary":
            if partitioned: conn.execute("DELETE FROM state_payload_parts WHERE name='atlas'")
            else: conn.execute("UPDATE state_payloads SET payload='broken' WHERE name='atlas'")
    if damage in {"caller_drift","candidate_drift"}:
        path=Path(native_filesystem_path(local/("Resolver.tsx" if damage=="caller_drift" else "never_execute.ts"))); path.write_bytes(path.read_bytes()+b"//changed")
    monkeypatch.setattr(mcp_server,"_raw_dir_for_target",lambda *a:raw); monkeypatch.setattr(mcp_server,"_reports_dir_for_target",lambda *a:tmp_path/"reports")
    monkeypatch.setattr(mcp_server,"_analysis_root_display",lambda *a:str(local)); monkeypatch.setattr(mcp_server,"_atlas",lambda *a,**k:pytest.fail("No shadow/cache rescue"))
    result=json.loads(mcp_server.inspect_file("MAIN::Resolver.tsx",format="json",target_root=str(local),evidence_focus="resolver_factory"))
    view=result.get("resolver_factory_evidence")
    if damage in {"lane_legacy","corrupt_primary"}: assert view is None
    elif damage in {"lane_corrupt","lane_hidden","caller_drift","bad_snapshot"}: assert view["status"]=="unavailable" and not view["records"]
    else:
        assert view["status"]=="observed" and view["shown"]==1
        target=view["records"][0]["factory_source_candidate"]
        assert target["status"]==("source_candidate" if damage=="none" else "unresolved")
        if damage!="none": assert "target" not in target and "return_construction" not in target


@pytest.mark.parametrize("huge",[False,True])
def test_callback_guard_does_not_enrich_earlier_lane(safeparse_view_inputs,huge):
    from tools.inspect_target import project_safeparse_early_return_evidence
    source,info=safeparse_view_inputs["early_record_budget"]
    construction={"status":"observed","methods":["superRefine"],"source_followup":{"tool":"inspect_file","file_path":"MAIN::Factory.tsx","line_start":1,"line_end":2}}
    base={"status":"source_candidate","source_binding":"snapshot_and_live_match","return_construction":construction}
    callbacks={"status":"observed","records":[{"formal_name":"x"*10000 if huge else "settings"}],"shown":1,"record_count":1,"omitted":0,"producer_omitted":0}
    baseline,enriched={},{}
    project_safeparse_early_return_evidence(baseline,info,_local_projection_status(info),snapshot_content=source,source_candidate=lambda *a:deepcopy(base))
    project_safeparse_early_return_evidence(enriched,info,_local_projection_status(info),snapshot_content=source,source_candidate=lambda *a:{**deepcopy(base),"return_construction":{**deepcopy(construction),"callback_guard_reads":deepcopy(callbacks)}})
    assert enriched==baseline


def test_callback_guard_public_definition_only_and_older_budget(imported_parser_atlas_run,monkeypatch):
    import tools.inspect_target as inspection
    from tools.core.agent_packet_budget import context_budget_profile,DEFAULT_AGENT_PACKET_BUDGET_TOKENS
    from tools.core.atlas_io import load_atlas_data_with_identity
    target,raw,_=imported_parser_atlas_run; primary=load_atlas_data_with_identity(raw).identity
    args=dict(file_path="MAIN::CallbackResolver.tsx",target_root=str(target),evidence_focus="resolver_factory",line_start=1,line_end=2)
    machine=json.loads(mcp_server.inspect_file(format="json",**args)); section=machine["resolver_factory_evidence"]
    candidate=section["records"][0]["factory_source_candidate"]; construction=candidate["return_construction"]
    reads=construction["callback_guard_reads"]
    assert reads["status"]=="observed" and reads["record_count"]==reads["shown"]==1 and reads["omitted"]==reads["producer_omitted"]==0
    assert reads["association_scope"]=="factory_definition_only" and reads["parameter_effects"]==reads["callback_activation"]=="not_established"
    assert reads["runtime_proof"]=="not_performed" and reads["edit_authority"]=="not_granted" and not reads["mutation_proposed"]
    assert "single_parameter" not in construction
    association=candidate["call_parameter_binding"]
    assert association["status"]=="withheld_budget"
    row=reads["records"][0]; assert row["formal_name"]=="isUpdate" and row["method_name"]=="superRefine" and row["call_index"]==1
    for key in ("formal_source_followup","source_followup"):
        follow=row[key]; response=json.loads(mcp_server.inspect_file(format="json",target_root=str(target),**{k:v for k,v in follow.items() if k!="tool"}))
        assert response["target_path_status"]["drift_check_status"]=="match" and response["target_path_status"]["source_snapshot_status"]=="ok"
    brief=mcp_server.inspect_file(**args)
    assert json.loads(next(line.split(": ",1)[1] for line in brief.splitlines() if line.startswith("resolver_factory_evidence: ")))==section
    for text in (json.dumps(machine),brief): assert context_budget_profile(text,budget_tokens=DEFAULT_AGENT_PACKET_BUDGET_TOKENS)["status"]=="pass"
    # Excluding only the new projection must preserve every older record and
    # detail exactly. Test both normal and constrained shared allowances.
    original=inspection.project_factory_return_construction
    def without(*a,**k):
        result=original(*a,**k); result.pop("callback_guard_reads",None); return result
    for cap in (1,2,3,4,8):
        monkeypatch.setattr(inspection,"AGENT_INSPECTION_MAX_TARGET_SPANS",cap)
        current=json.loads(mcp_server.inspect_file(format="json",**args))
        monkeypatch.setattr(inspection,"project_factory_return_construction",without)
        older=json.loads(mcp_server.inspect_file(format="json",**args))
        monkeypatch.setattr(inspection,"project_factory_return_construction",original)
        construction=current.get("resolver_factory_evidence",{}).get("records",[])
        if construction: construction[0]["factory_source_candidate"].get("return_construction",{}).pop("callback_guard_reads",None)
        for response in (current,older):
            for row in response.get("resolver_factory_evidence",{}).get("records",[]): row["factory_source_candidate"].pop("call_parameter_binding",None)
        assert current==older
    assert load_atlas_data_with_identity(raw).identity==primary


@pytest.mark.parametrize("partitioned",[False,True],ids=["inline","partitioned"])
@pytest.mark.parametrize("damage",["none","caller_drift","target_drift","missing_snapshot","corrupt_primary","legacy","hidden","bool_count","omitted","span","ast"])
def test_callback_guard_actual_primary_refusals(imported_parser_atlas_run,tmp_path,monkeypatch,partitioned,damage):
    import sqlite3
    target,_,original=imported_parser_atlas_run; local=tmp_path/("deep-source-"*8)/("fixture-"*10)
    assert len(str(local))>260
    Path(native_filesystem_path(local)).mkdir(parents=True)
    atlas=deepcopy(original); files=atlas["MAIN"]["files"]
    for file in files: Path(native_filesystem_path(local/file)).write_bytes((target/file).read_bytes())
    atlas["MAIN"]["root_path"]=str(local); provider=files["callback_factory.ts"]; lane=provider["factory_return_construction_evidence"]
    row=lane["records"][0]
    if damage=="legacy": row.pop("callback_guard_reads")
    elif damage=="hidden":
        lane["records"].append(deepcopy(row)); lane["record_count"]+=1; lane["records"][-1]["callback_guard_reads_omitted"]=-1
    elif damage=="bool_count": row["callback_guard_reads"][0]["argument_count"]=True
    elif damage=="omitted": row["callback_guard_reads_omitted"]=1
    elif damage=="span": row["spans"]["guard_0_reference"]["start"]+=1
    elif damage=="ast": provider["ast_contract_version"]="old"
    raw=tmp_path/".raw"; store=ArtifactStore(raw_dir=raw); store.use_sqlite,store.backend=True,"hybrid_sqlite"; store._ensure_schema()
    if partitioned: store.state_payload_inline_limit_bytes=1
    store._save_payload_to_state_table("atlas",atlas); store._save_atlas_to_sqlite(atlas)
    with sqlite3.connect(raw/"codemaps.db") as conn:
        conn.execute("UPDATE projects SET path='.' WHERE project_key='MAIN'")
        if damage=="missing_snapshot": conn.execute("DELETE FROM source_snapshots WHERE rel_path='callback_factory.ts'")
        elif damage=="corrupt_primary":
            if partitioned: conn.execute("DELETE FROM state_payload_parts WHERE name='atlas'")
            else: conn.execute("UPDATE state_payloads SET payload='broken' WHERE name='atlas'")
    if damage in {"caller_drift","target_drift"}:
        path=Path(native_filesystem_path(local/("CallbackResolver.tsx" if damage=="caller_drift" else "callback_factory.ts"))); path.write_bytes(path.read_bytes()+b"//changed")
    monkeypatch.setattr(mcp_server,"_raw_dir_for_target",lambda *a:raw); monkeypatch.setattr(mcp_server,"_reports_dir_for_target",lambda *a:tmp_path/"reports")
    monkeypatch.setattr(mcp_server,"_analysis_root_display",lambda *a:str(local)); monkeypatch.setattr(mcp_server,"_atlas",lambda *a,**k:pytest.fail("No alternate primary/cache rescue"))
    machine=json.loads(mcp_server.inspect_file("MAIN::CallbackResolver.tsx",format="json",target_root=str(local),evidence_focus="resolver_factory"))
    view=machine.get("resolver_factory_evidence")
    if damage=="corrupt_primary": assert view is None
    elif damage=="caller_drift": assert view["status"]=="unavailable" and not view["records"]
    else:
        candidate=view["records"][0]["factory_source_candidate"]
        if damage in {"target_drift","ast","missing_snapshot"}: assert candidate["status"]=="unresolved"
        else:
            construction=candidate["return_construction"]
            if damage=="none": assert construction["callback_guard_reads"]["shown"]==1
            else: assert construction["status"]=="unavailable" and "callback_guard_reads" not in construction


@pytest.mark.parametrize("file,expected",[("CallbackResolver.tsx","observed"),("ResolverFunction.tsx","observed"),("PositionLiteral.tsx","observed"),
    ("Resolver.tsx","unavailable"),("PositionZero.tsx","unavailable"),("PositionTwo.tsx","unavailable"),
    ("PositionDefault.tsx","unavailable"),("PositionOptional.tsx","unavailable"),("PositionMultiple.tsx","unavailable")])
def test_resolver_position_public_source_join_and_refusals(imported_parser_atlas_run,monkeypatch,file,expected):
    from tools.core.atlas_io import load_atlas_data_with_identity
    from tools.core.agent_packet_budget import context_budget_profile,COMPACT_AGENT_PACKET_TOKENS,DEFAULT_AGENT_PACKET_BUDGET_TOKENS
    target,raw,atlas=imported_parser_atlas_run; primary=load_atlas_data_with_identity(raw).identity
    monkeypatch.setattr(mcp_server,"_atlas",lambda *a,**k:pytest.fail("No whole Atlas or cache rescue"))
    args=dict(file_path="MAIN::"+file,target_root=str(target),evidence_focus="resolver_parameter",line_start=1,line_end=2)
    machine=json.loads(mcp_server.inspect_file(format="json",**args)); view=machine["resolver_factory_evidence"]
    assert machine["evidence_focus"]["requested"]=="resolver_parameter" and "not_requested" in view["detail_scope"]
    assert view["shown"]==view["record_count"]==1
    candidate=view["records"][0]["factory_source_candidate"]; association=candidate["call_parameter_binding"]
    assert association["status"]==expected and "return_construction" not in candidate
    assert association["association_scope"]=="resolver_argument_position_to_factory_formal"
    assert all(association[k]=="not_established" for k in ("argument_value_semantics","type_compatibility","parameter_usage","parameter_effects","callback_activation"))
    assert association["runtime_proof"]=="not_performed" and association["edit_authority"]=="not_granted" and not association["mutation_proposed"]
    assert "actual_name" not in association and "source_uses" not in association
    if expected=="observed":
        assert association["argument_index"]==0 and association["actual_source_kind"]=="opaque_argument_expression"
        assert association["formal_name"]==("settings" if file=="ResolverFunction.tsx" else "isUpdate")
        source=(target/file).read_bytes(); span=association["actual_span"]
        assert source[span["start"]:span["end"]].decode("utf-8")==("false" if file=="PositionLiteral.tsx" else "Boolean(secretRotation)")
        for key in ("actual_source_followup","formal_source_followup"):
            follow=association[key]; response=json.loads(mcp_server.inspect_file(format="json",target_root=str(target),**{k:v for k,v in follow.items() if k!="tool"}))
            status=response["target_path_status"]
            assert status["source_snapshot_status"]=="ok" and status["drift_check_status"]=="match" and status["indexed"]
    else:
        assert "actual_span" not in association and "formal_name" not in association
        assert association["reason"]==("single_factory_argument_required" if file in {"PositionZero.tsx","PositionTwo.tsx"} else "single_required_formal_source_binding_unavailable")
    brief=mcp_server.inspect_file(**args)
    assert json.loads(next(line.split(": ",1)[1] for line in brief.splitlines() if line.startswith("resolver_factory_evidence: ")))==view
    assert context_budget_profile(json.dumps(view),budget_tokens=COMPACT_AGENT_PACKET_TOKENS)["status"]=="pass"
    for text in (json.dumps(machine),brief): assert context_budget_profile(text,budget_tokens=DEFAULT_AGENT_PACKET_BUDGET_TOKENS)["status"]=="pass"
    assert load_atlas_data_with_identity(raw).identity==primary


def test_resolver_position_per_call_not_cached_in_export_candidate(imported_parser_atlas_run,monkeypatch):
    import tools.inspect_target as view
    target,_,_=imported_parser_atlas_run; original=view._resolver_parameter_source_association; observations=[]; definitions=[]
    def capture(record,candidate,caller):
        before=deepcopy(candidate); result=original(record,candidate,caller)
        assert candidate==before and "call_parameter_binding" not in candidate
        definitions.append(candidate["return_construction"]); observations.append(deepcopy(result)); return result
    monkeypatch.setattr(view,"_resolver_parameter_source_association",capture)
    machine=json.loads(mcp_server.inspect_file("MAIN::PositionTwoSites.tsx",format="json",target_root=str(target),evidence_focus="resolver_parameter"))
    assert len(observations)==2 and definitions[0] is definitions[1]
    assert all(r["status"]=="observed" and r["formal_name"]=="isUpdate" for r in observations)
    source=(target/"PositionTwoSites.tsx").read_bytes()
    assert [source[r["actual_span"]["start"]:r["actual_span"]["end"]].decode("utf-8") for r in observations]==["Boolean(secretRotation)","unknownEffect()"]
    for row in machine["resolver_factory_evidence"]["records"]:
        association=row["factory_source_candidate"].get("call_parameter_binding",{})
        if association.get("status")=="observed": assert association in observations
        else: assert association.get("status") in (None,"withheld_budget")


@pytest.mark.parametrize("cap",[1,2,3,4,8])
@pytest.mark.parametrize("focus",["resolver_factory",""])
def test_resolver_position_last_priority_preserves_older_callback_and_fields(imported_parser_atlas_run,monkeypatch,cap,focus):
    import tools.inspect_target as view
    target,_,_=imported_parser_atlas_run
    monkeypatch.setattr(view,"AGENT_INSPECTION_MAX_TARGET_SPANS",cap)
    args=dict(file_path="MAIN::CallbackResolver.tsx",target_root=str(target),evidence_focus=focus)
    current=json.loads(mcp_server.inspect_file(format="json",**args))
    monkeypatch.setattr(view,"_resolver_parameter_source_association",lambda *a:None)
    older=json.loads(mcp_server.inspect_file(format="json",**args))
    for row in current.get("resolver_factory_evidence",{}).get("records",[]): row["factory_source_candidate"].pop("call_parameter_binding",None)
    assert current==older


@pytest.mark.parametrize("partitioned",[False,True],ids=["inline","partitioned"])
@pytest.mark.parametrize("damage",["none","caller_drift","target_drift","missing_snapshot","corrupt_primary","formal_missing","hidden","bool_count","actual_span","caller_count","ast"])
@pytest.mark.parametrize("focus",["resolver_parameter","resolver_chain"])
def test_resolver_position_actual_primary_refusals(imported_parser_atlas_run,tmp_path,monkeypatch,partitioned,damage,focus):
    import sqlite3
    target,_,original=imported_parser_atlas_run; local=tmp_path/("deep-source-"*8)/("fixture-"*10)
    assert len(str(local))>260
    Path(native_filesystem_path(local)).mkdir(parents=True)
    atlas=deepcopy(original); files=atlas["MAIN"]["files"]
    for file in files: Path(native_filesystem_path(local/file)).write_bytes((target/file).read_bytes())
    atlas["MAIN"]["root_path"]=str(local); caller=files["CallbackResolver.tsx"]; provider=files["callback_factory.ts"]
    lane=provider["factory_return_construction_evidence"]; row=lane["records"][0]
    if damage=="formal_missing": row.pop("parameter_name")
    elif damage=="hidden": lane["records"].append(deepcopy(row)); lane["record_count"]+=1; lane["records"][-1]["parameter_uses_omitted"]=-1
    elif damage=="bool_count": caller["resolver_factory_evidence"]["records"][0]["argument_count"]=True
    elif damage=="actual_span": caller["resolver_factory_evidence"]["records"][0]["spans"]["argument_0"]["start"]+=1
    elif damage=="caller_count": caller["resolver_factory_evidence"]["record_count"]+=1
    elif damage=="ast": provider["ast_contract_version"]="old"
    raw=tmp_path/".raw"; store=ArtifactStore(raw_dir=raw); store.use_sqlite,store.backend=True,"hybrid_sqlite"; store._ensure_schema()
    if partitioned: store.state_payload_inline_limit_bytes=1
    store._save_payload_to_state_table("atlas",atlas); store._save_atlas_to_sqlite(atlas)
    with sqlite3.connect(raw/"codemaps.db") as conn:
        conn.execute("UPDATE projects SET path='.' WHERE project_key='MAIN'")
        if damage=="missing_snapshot": conn.execute("DELETE FROM source_snapshots WHERE rel_path='callback_factory.ts'")
        elif damage=="corrupt_primary":
            if partitioned: conn.execute("DELETE FROM state_payload_parts WHERE name='atlas'")
            else: conn.execute("UPDATE state_payloads SET payload='broken' WHERE name='atlas'")
    if damage in {"caller_drift","target_drift"}:
        path=Path(native_filesystem_path(local/("CallbackResolver.tsx" if damage=="caller_drift" else "callback_factory.ts"))); path.write_bytes(path.read_bytes()+b"//changed")
    monkeypatch.setattr(mcp_server,"_raw_dir_for_target",lambda *a:raw); monkeypatch.setattr(mcp_server,"_reports_dir_for_target",lambda *a:tmp_path/"reports")
    monkeypatch.setattr(mcp_server,"_analysis_root_display",lambda *a:str(local)); monkeypatch.setattr(mcp_server,"_atlas",lambda *a,**k:pytest.fail("No primary/cache rescue"))
    result=json.loads(mcp_server.inspect_file("MAIN::CallbackResolver.tsx",format="json",target_root=str(local),evidence_focus=focus))
    section=result.get("resolver_factory_evidence")
    if damage=="corrupt_primary": assert section is None
    elif damage in {"caller_drift","bool_count","actual_span","caller_count"}: assert section["status"]=="unavailable" and not section["records"]
    elif focus=="resolver_chain":
        chain=section["records"][0]["source_chain"]
        assert chain["status"]==("observed" if damage=="none" else "partial")
        assert len(chain["steps"])==(4 if damage=="none" else 1 if damage in {"target_drift","missing_snapshot","ast"} else 2)
    else:
        association=section["records"][0]["factory_source_candidate"]["call_parameter_binding"]
        assert association["status"]==("observed" if damage=="none" else "unavailable")
        if damage!="none": assert "formal_name" not in association and "actual_span" not in association


@pytest.mark.parametrize("focus",["safeparse_early_return","resolver_parameter","resolver_chain"])
def test_early_return_focus_declaration_is_not_ordinary_source(monkeypatch, tmp_path,focus):
    monkeypatch.setattr(mcp_server, "_raw_dir_for_target", lambda *a: tmp_path)
    monkeypatch.setattr(mcp_server, "_reports_dir_for_target", lambda *a: tmp_path)
    monkeypatch.setattr(mcp_server, "declaration_file_inspection", lambda *a, **k: {"kind": "declaration_header", "edit_authority": "not_granted"})
    monkeypatch.setattr(mcp_server, "_sqlite_file_inspection_payload", lambda *a, **k: pytest.fail("Header must not enter ordinary metadata"))
    result = json.loads(mcp_server.inspect_file("MAIN::public.d.ts", format="json", evidence_focus=focus))
    assert result["evidence_focus"] == {"requested": focus, "status": "not_applicable_declaration_header"}
    assert "safeparse_early_return_evidence" not in result and result["edit_authority"] == "not_granted"


@pytest.mark.parametrize("file,steps,reason",[("CallbackResolver.tsx",4,"four_source_correspondences_only_not_runtime_proof"),
    ("PositionLiteral.tsx",4,"four_source_correspondences_only_not_runtime_proof"),
    ("ResolverFunction.tsx",3,"no_retained_callback_guard_read_not_absence"),
    ("Resolver.tsx",2,"single_required_formal_source_binding_unavailable"),
    ("PositionZero.tsx",2,"single_factory_argument_required"),("PositionTwo.tsx",2,"single_factory_argument_required"),
    ("PositionDefault.tsx",2,"single_required_formal_source_binding_unavailable"),("PositionOptional.tsx",2,"single_required_formal_source_binding_unavailable"),
    ("PositionMultiple.tsx",2,"single_required_formal_source_binding_unavailable"),
    ("ChainMultiple.tsx",3,"callback_guard_selection_required_or_producer_omitted"),
    ("ChainOmitted.tsx",3,"no_retained_callback_guard_read_not_absence")])
def test_resolver_chain_public_source_navigation_only(imported_parser_atlas_run,monkeypatch,file,steps,reason):
    from tools.core.atlas_io import load_atlas_data_with_identity
    from tools.core.agent_packet_budget import context_budget_profile,COMPACT_AGENT_PACKET_TOKENS,DEFAULT_AGENT_PACKET_BUDGET_TOKENS
    target,raw,_=imported_parser_atlas_run; primary=load_atlas_data_with_identity(raw).identity
    monkeypatch.setattr(mcp_server,"_atlas",lambda *a,**k:pytest.fail("No whole Atlas/cache rescue"))
    args=dict(file_path="MAIN::"+file,target_root=str(target),evidence_focus="resolver_chain",line_start=1,line_end=2)
    machine=json.loads(mcp_server.inspect_file(format="json",**args)); section=machine["resolver_factory_evidence"]
    assert machine["evidence_focus"]["requested"]=="resolver_chain" and "other_optional_details_not_requested" in section["detail_scope"]
    assert section["shown"]==section["record_count"]==1 and section["omitted"]==section["producer_omitted"]==0
    row=section["records"][0]; assert set(row)=={"source_chain"}; chain=row["source_chain"]
    assert chain["status"]==("observed" if steps==4 else "partial") and chain["reason"]==reason
    assert [s["kind"] for s in chain["steps"]]==["resolver_call","factory_definition","formal_parameter","callback_guard_read"][:steps]
    assert chain["interpretation"]=="source_correspondences_not_value_or_execution_flow"
    assert chain["argument_value_semantics"]==chain["parameter_effects"]==chain["callback_activation"]=="not_established"
    assert chain["runtime_proof"]=="not_performed" and chain["edit_authority"]=="not_granted" and not chain["mutation_proposed"]
    if steps==4:
        assert chain["steps"][2]["name"]=="isUpdate" and chain["steps"][2]["argument_index"]==0
        assert chain["steps"][3]["association_scope"]=="factory_definition_only" and chain["steps"][3]["method_name"]=="superRefine"
        source=(target/file).read_bytes(); span=chain["steps"][0]["argument_span"]
        assert source[span["start"]:span["end"]].decode("utf-8")==("false" if file=="PositionLiteral.tsx" else "Boolean(secretRotation)")
        for step in chain["steps"]:
            follow=step["source_followup"]; response=json.loads(mcp_server.inspect_file(format="json",target_root=str(target),**{k:v for k,v in follow.items() if k!="tool"}))
            assert response["target_path_status"]["source_snapshot_status"]=="ok" and response["target_path_status"]["drift_check_status"]=="match"
    if file=="ChainMultiple.tsx": assert chain["callback_guard_record_count"]==2 and chain["callback_guard_producer_omitted"]==0
    if file=="ChainOmitted.tsx": assert chain["callback_guard_record_count"]==0 and chain["callback_guard_producer_omitted"]==8
    brief=mcp_server.inspect_file(**args)
    assert json.loads(next(line.split(": ",1)[1] for line in brief.splitlines() if line.startswith("resolver_factory_evidence: ")))==section
    assert context_budget_profile(json.dumps(section),budget_tokens=COMPACT_AGENT_PACKET_TOKENS)["status"]=="pass"
    for text in (json.dumps(machine),brief): assert context_budget_profile(text,budget_tokens=DEFAULT_AGENT_PACKET_BUDGET_TOKENS)["status"]=="pass"
    assert load_atlas_data_with_identity(raw).identity==primary


@pytest.mark.parametrize("cap",[1,2,3,4,5,7,8])
@pytest.mark.parametrize("file",["CallbackResolver.tsx","PositionTwoSites.tsx"])
def test_resolver_chain_whole_navigation_and_shared_span_budget(imported_parser_atlas_run,monkeypatch,cap,file):
    import tools.inspect_target as view
    target,_,_=imported_parser_atlas_run; monkeypatch.setattr(view,"AGENT_INSPECTION_MAX_TARGET_SPANS",cap)
    result=json.loads(mcp_server.inspect_file("MAIN::"+file,format="json",target_root=str(target),evidence_focus="resolver_chain"))
    section=result["resolver_factory_evidence"]
    assert section["shown"]+section["omitted"]==section["record_count"]==(2 if file=="PositionTwoSites.tsx" else 1)
    assert sum(len(r["source_chain"]["steps"]) for r in section["records"])<=cap
    assert all(len(r["source_chain"]["steps"])==4 for r in section["records"])
    if cap<4: assert section["shown"]==0 and "budget" in section["reason"]
    elif file=="CallbackResolver.tsx": assert section["shown"]==1


@pytest.mark.parametrize("focus",["","resolver_factory","resolver_parameter"])
def test_resolver_chain_not_computed_in_older_views(imported_parser_atlas_run,monkeypatch,focus):
    import tools.inspect_target as view
    target,_,_=imported_parser_atlas_run
    monkeypatch.setattr(view,"_resolver_source_followup_chain",lambda *a:pytest.fail("Old focus must not compute or expose new chain"))
    result=json.loads(mcp_server.inspect_file("MAIN::CallbackResolver.tsx",format="json",target_root=str(target),evidence_focus=focus))
    assert "source_chain" not in json.dumps(result)


def test_resolver_chain_packet_budget_omits_whole_navigation(imported_parser_atlas_run,monkeypatch):
    import tools.inspect_target as view
    target,_,_=imported_parser_atlas_run; original=view._resolver_source_followup_chain
    def wide(*args):
        chain=original(*args)
        # Presentation-only stress: a whole path can exceed remaining budget.
        # This mock is never admitted as a real source binding or read target.
        chain["steps"][1]["source_followup"]["file_path"]="MAIN::"+"wide/"*2000+"factory.ts"
        return chain
    monkeypatch.setattr(view,"_resolver_source_followup_chain",wide)
    machine=json.loads(mcp_server.inspect_file("MAIN::CallbackResolver.tsx",format="json",target_root=str(target),evidence_focus="resolver_chain"))
    section=machine["resolver_factory_evidence"]
    assert section["record_count"]==section["omitted"]==1 and section["shown"]==0 and not section["records"]
    assert "budget" in section["reason"] and "wide/" not in json.dumps(section)


@pytest.mark.parametrize("focus",["resolver_chain_runtime","resolver_chain ",True])
def test_resolver_chain_unknown_focus_rejected_before_storage(monkeypatch,focus):
    monkeypatch.setattr(mcp_server,"_raw_dir_for_target",lambda *a:pytest.fail("Reject unsupported focus before storage"))
    result=json.loads(mcp_server.inspect_file("MAIN::Caller.tsx",format="json",evidence_focus=focus))
    assert result["status"]=="unavailable" and result["reason"]=="unsupported_evidence_focus" and result["edit_authority"]=="not_granted"


@pytest.mark.parametrize("name", ["positive", "unicode", "empty", "record_budget", "node_budget"])
@pytest.mark.parametrize("merge", [False, True])
def test_effect_safeparse_view_source_only_coverage_and_followup(safeparse_view_inputs, name, merge):
    from tools.inspect_target import project_effect_safeparse_callback_evidence, project_effect_safeparse_merge_evidence
    from tools.core.agent_packet_budget import COMPACT_AGENT_PACKET_TOKENS, context_budget_profile
    source, info = safeparse_view_inputs[("merge_" if merge else "effect_") + name]
    before = deepcopy(info)
    payload = {}
    (project_effect_safeparse_merge_evidence if merge else project_effect_safeparse_callback_evidence)(payload, info, _local_projection_status(info), snapshot_content=source)
    view = payload["effect_safeparse_merge_evidence" if merge else "effect_safeparse_callback_evidence"]
    if merge: assert view["prior_value_semantics"] == view["merge_result_semantics"] == "not_established"
    assert info == before
    assert view["mutation_proposed"] is False and view["edit_authority"] == "not_granted"
    assert view["schema_authority"] == view["type_compatibility"] == "not_established"
    assert view["runtime_proof"] == "not_performed"
    if name == "node_budget":
        assert view["status"] == "unavailable" and view["record_count"] is None
    else:
        assert view["source_binding"] == "snapshot_and_live_match"
        assert view["status"] == ("partial" if name == "record_budget" else "observed")
        assert view["producer_omitted"] == (1 if name == "record_budget" else 0)
        assert view["shown"] + view["omitted"] == view["record_count"]
        if name == "empty":
            assert view["shown"] == view["record_count"] == 0 and view["empty_result_meaning"] == "not_absence_or_clean_code"
        else:
            assert view["shown"] > 0
            encoded = source.encode("utf-8")
            for row in view["records"]:
                assert encoded[row["span"]["start"]:row["span"]["end"]].decode("utf-8").startswith("onValueChange")
                assert row["source_followup"]["file_path"] == "MAIN::Panel.tsx"
                assert row["source_followup"]["line_start"] < row["span"]["line"]
                if merge:
                    assert row["prior_source_followup"]["line_end"] < row["span"]["line"]
                    assert row["prior_source_followup"]["evidence_focus"] == "effect_safeparse_merge"
    assert context_budget_profile(json.dumps(payload, ensure_ascii=False), budget_tokens=COMPACT_AGENT_PACKET_TOKENS)["status"] == "pass"


@pytest.mark.parametrize("merge", [False, True])
@pytest.mark.parametrize("damage", ["legacy", "missing_snapshot", "changed_snapshot", "drift", "truthy_indexed",
    "file_hash", "snapshot_hash", "ast", "parser", "version", "scope", "hash", "mutation", "zero_mutation",
    "schema_claim", "runtime_claim", "type_claim", "extra", "count", "bool_count", "omitted", "role",
    "bool_offset", "line", "bounds", "relation", "tokens", "spread_token", "callback_token", "duplicate", "hidden_corrupt", "unknown_status", "partial_lie"])
def test_effect_safeparse_view_refuses_unbound_corrupt_promoted(safeparse_view_inputs, damage, merge):
    from tools.inspect_target import project_effect_safeparse_callback_evidence, project_effect_safeparse_merge_evidence
    source, original = safeparse_view_inputs["merge_positive" if merge else "effect_positive"]
    info = deepcopy(original)
    status = _local_projection_status(info)
    key = "effect_safeparse_merge_evidence" if merge else "effect_safeparse_callback_evidence"
    lane = info[key]
    row = lane["records"][0]
    if damage == "legacy": info.pop(key)
    elif damage == "missing_snapshot": source = None
    elif damage == "changed_snapshot": source += "// drift"
    elif damage == "drift": status["drift_check_status"] = "drift"
    elif damage == "truthy_indexed": status["indexed"] = 1
    elif damage == "file_hash": info["hash"] = "0" * 64
    elif damage == "snapshot_hash": status["source_snapshot_hash"] = "0" * 64
    elif damage == "ast": info["ast_contract_version"] = "old"
    elif damage == "parser": info["parser_evidence"]["status"] = "degraded"
    elif damage == "version": lane["version"] = "old"
    elif damage == "scope": lane["scope"] = "universal"
    elif damage == "hash": lane["source_hash"] = "0" * 64
    elif damage == "mutation": lane["mutation_proposed"] = True
    elif damage == "zero_mutation": lane["mutation_proposed"] = 0
    elif damage == "schema_claim": lane["schema_authority"] = "validated"
    elif damage == "runtime_claim": lane["runtime_proof"] = "passed"
    elif damage == "type_claim": lane["type_compatibility"] = "verified"
    elif damage == "extra": lane["validated"] = True
    elif damage == "count": lane["record_count"] = 2
    elif damage == "bool_count": lane["record_count"] = True
    elif damage == "omitted": lane["omitted"] = True
    elif damage == "role": row["spans"].pop("callback_binding")
    elif damage == "bool_offset": row["spans"]["sink"]["start"] = True
    elif damage == "line": row["spans"]["sink"]["line"] += 1
    elif damage == "bounds": row["spans"]["sink"]["end"] = len(source) * 4
    elif damage == "relation": row["spans"]["condition_result_ref"] = row["spans"]["result_binding"]
    elif damage == "tokens": row["spans"]["success_property"]["start"] += 1
    elif damage == "spread_token": row["spans"]["spread"]["start"] += 1
    elif damage == "callback_token": row["spans"]["callback_ref"]["end"] -= 1
    elif damage == "duplicate": lane["records"].append(deepcopy(row)); lane["record_count"] += 1
    elif damage == "hidden_corrupt":
        lane["records"] = deepcopy(lane["records"] * 7)
        lane["records"][-1] = {"spans": {}}
        lane["record_count"] = 7
    elif damage == "unknown_status": lane["status"] = []
    else: lane.update(status="partial", omitted=1, reason="record_budget_exceeded")
    payload = {}
    (project_effect_safeparse_merge_evidence if merge else project_effect_safeparse_callback_evidence)(payload, info, status, snapshot_content=source)
    if damage == "legacy": assert key not in payload
    else:
        view = payload[key]
        assert view["status"] == "unavailable" and view["records"] == [] and view["record_count"] is None


def test_effect_safeparse_view_preserves_older_sections_and_budgets(safeparse_view_inputs):
    from tools.inspect_target import project_effect_safeparse_callback_evidence, AGENT_INSPECTION_MAX_TARGET_SPANS
    source, info = safeparse_view_inputs["effect_positive"]
    older = {"client_safeparse_success_evidence": {"records": [{}] * AGENT_INSPECTION_MAX_TARGET_SPANS, "omitted": 2}}
    payload = {**deepcopy(older), "inspection_authority": "orientation_only", "one_shot_edit_ready": False}
    baseline = deepcopy(payload)
    project_effect_safeparse_callback_evidence(payload, info, _local_projection_status(info), snapshot_content=source)
    assert all(payload[key] == value for key,value in baseline.items())
    view = payload["effect_safeparse_callback_evidence"]
    assert view["shown"] == 0 and view["omitted"] == 1 and view["producer_omitted"] == 0
    for crowded, sections in (({"older": "x" * 25000}, {}), ({"older": {"details": [{}] * 3000}}, {}),
            ({}, {"older": {"records": [], "reason": "x" * 5000}})):
        baseline = deepcopy(crowded)
        project_effect_safeparse_callback_evidence(crowded, info, _local_projection_status(info), snapshot_content=source, syntax_sections=sections)
        assert crowded == baseline


def test_effect_safeparse_empty_unavailable_and_both_lane_coordinate_reuse(safeparse_view_inputs, monkeypatch):
    from tools import inspect_target as view
    native = view._utf8_span_validator
    calls = []
    def counted(*args, **kwargs):
        calls.append(1)
        return native(*args, **kwargs)
    monkeypatch.setattr(view, "_utf8_span_validator", counted)
    cache = {}
    source, info = safeparse_view_inputs["effect_unicode"]
    status = _local_projection_status(info)
    for _ in range(2):
        view.project_effect_safeparse_callback_evidence({}, info, status, snapshot_content=source, coordinate_cache=cache)
    assert len(calls) == 1
    # Another file/content cannot borrow an invocation-local coordinate authority.
    source, info = safeparse_view_inputs["effect_positive"]
    view.project_effect_safeparse_callback_evidence({}, info, _local_projection_status(info), snapshot_content=source, coordinate_cache=cache)
    assert len(calls) == 2
    source, info = safeparse_view_inputs["both"]
    payload, cache = {}, {}
    for project in (view.project_safeparse_success_evidence, view.project_effect_safeparse_callback_evidence):
        project(payload, info, _local_projection_status(info), snapshot_content=source, coordinate_cache=cache)
    assert len(calls) == 3
    assert payload["client_safeparse_success_evidence"]["shown"] == 1
    assert payload["effect_safeparse_callback_evidence"]["shown"] == 1
    for name in ("effect_empty", "effect_node_budget"):
        _, info = safeparse_view_inputs[name]
        view.project_effect_safeparse_callback_evidence({}, info, _local_projection_status(info))
    assert len(calls) == 3


@pytest.mark.parametrize("damage", ["prior_role", "prior_relation", "prior_prefix", "prior_end", "prior_claim", "merge_claim"])
def test_effect_merge_view_refuses_prior_contract_corruption(safeparse_view_inputs, damage):
    from tools.inspect_target import project_effect_safeparse_merge_evidence
    source, original = safeparse_view_inputs["merge_positive"]
    info = deepcopy(original)
    lane = info["effect_safeparse_merge_evidence"]
    spans = lane["records"][0]["spans"]
    if damage == "prior_role": spans.pop("prior_ref")
    elif damage == "prior_relation": spans["prior_binding"] = deepcopy(spans["callback_binding"])
    elif damage == "prior_prefix": spans["prior_spread"]["start"] += 1
    elif damage == "prior_end": spans["prior_ref"]["end"] -= 1
    elif damage == "prior_claim": lane["prior_value_semantics"] = "validated"
    else: lane["merge_result_semantics"] = "verified"
    payload = {}
    project_effect_safeparse_merge_evidence(payload, info, _local_projection_status(info), snapshot_content=source)
    assert payload["effect_safeparse_merge_evidence"]["status"] == "unavailable"
    assert payload["effect_safeparse_merge_evidence"]["records"] == []


def test_effect_merge_view_older_priority_and_three_lane_coordinate_reuse(safeparse_view_inputs, monkeypatch):
    from tools import inspect_target as view
    calls, native = [], view._utf8_span_validator
    def counted(*args, **kwargs):
        calls.append(1)
        return native(*args, **kwargs)
    monkeypatch.setattr(view, "_utf8_span_validator", counted)
    source, info = safeparse_view_inputs["triple"]
    for key in ("client_safeparse_success_evidence", "effect_safeparse_callback_evidence", "effect_safeparse_merge_evidence"):
        assert info[key]["record_count"] == 1, (key, info[key])
    payload, cache = {}, {}
    for project in (view.project_safeparse_success_evidence, view.project_effect_safeparse_callback_evidence,
            view.project_effect_safeparse_merge_evidence):
        prior = deepcopy(payload)
        project(payload, info, _local_projection_status(info), snapshot_content=source, coordinate_cache=cache)
        assert all(payload[key] == value for key,value in prior.items())
    assert len(calls) == 1
    source, info = safeparse_view_inputs["merge_positive"]
    prior = {"effect_safeparse_callback_evidence": {"records": [{}] * view.AGENT_INSPECTION_MAX_TARGET_SPANS}}
    payload = deepcopy(prior)
    view.project_effect_safeparse_merge_evidence(payload, info, _local_projection_status(info), snapshot_content=source)
    assert payload["effect_safeparse_callback_evidence"] == prior["effect_safeparse_callback_evidence"]
    assert payload["effect_safeparse_merge_evidence"]["shown"] == 0
    for crowded, syntax in (({"older": "x" * 25000}, {}), ({}, {"older": {"reason": "x" * 6000}})):
        before = deepcopy(crowded)
        view.project_effect_safeparse_merge_evidence(crowded, info, _local_projection_status(info), snapshot_content=source, syntax_sections=syntax)
        assert crowded == before


@pytest.mark.parametrize("name", ["positive", "unicode", "empty", "record_budget", "node_budget"])
def test_safeparse_view_finite_source_only_partial_empty_and_budget(safeparse_view_inputs, name):
    from tools.inspect_target import project_safeparse_success_evidence
    from tools.core.agent_packet_budget import COMPACT_AGENT_PACKET_TOKENS, context_budget_profile
    source, info = safeparse_view_inputs[name]
    before = deepcopy(info)
    payload = {}
    project_safeparse_success_evidence(payload, info, _local_projection_status(info), snapshot_content=source)
    view = payload["client_safeparse_success_evidence"]
    assert info == before
    assert view["mutation_proposed"] is False and view["edit_authority"] == "not_granted"
    assert view["schema_authority"] == view["type_compatibility"] == "not_established"
    assert view["runtime_proof"] == "not_performed"
    assert view["scope"] == "file_wide_finite_grammar_only"
    if name == "node_budget":
        assert view["status"] == "unavailable" and view["record_count"] is None
    else:
        assert view["source_binding"] == "snapshot_and_live_match"
        assert view["status"] == ("partial" if name == "record_budget" else "observed")
        assert view["producer_omitted"] == (1 if name == "record_budget" else 0)
        assert view["shown"] + view["omitted"] == view["record_count"]
        if name == "empty":
            assert view["shown"] == view["record_count"] == 0 and view["empty_result_meaning"] == "not_absence_or_clean_code"
        else:
            assert view["shown"] > 0
            encoded = source.encode("utf-8")
            for row in view["records"]:
                span = row["span"]
                assert encoded[span["start"]:span["end"]].decode("utf-8").startswith("setRows")
                assert row["source_followup"]["file_path"] == "MAIN::Panel.tsx"
    assert context_budget_profile(json.dumps(payload, ensure_ascii=False), budget_tokens=COMPACT_AGENT_PACKET_TOKENS)["status"] == "pass"


@pytest.mark.parametrize("damage", ["legacy", "missing_snapshot", "changed_snapshot", "drift", "not_indexed", "truthy_indexed",
    "file_hash", "snapshot_hash", "ast", "old_ast", "parser", "version", "scope", "hash", "mutation", "zero_mutation",
    "schema_claim", "runtime_claim", "type_claim", "extra", "count", "bool_count", "omitted", "offset_unit", "role",
    "bool_offset", "line", "bounds", "relation", "tokens", "duplicate", "hidden_corrupt", "unknown_status", "partial_lie"])
def test_safeparse_view_refuses_corrupt_legacy_drift_and_authority(safeparse_view_inputs, damage):
    from tools.inspect_target import project_safeparse_success_evidence
    source, original = safeparse_view_inputs["positive"]
    info = deepcopy(original)
    status = _local_projection_status(info)
    lane = info["client_safeparse_success_evidence"]
    row = lane["records"][0]
    if damage == "legacy": info.pop("client_safeparse_success_evidence")
    elif damage == "missing_snapshot": source = None
    elif damage == "changed_snapshot": source += "// drift"
    elif damage == "drift": status["drift_check_status"] = "drift"
    elif damage == "not_indexed": status["indexed"] = False
    elif damage == "truthy_indexed": status["indexed"] = 1
    elif damage == "file_hash": info["hash"] = "0" * 64
    elif damage == "snapshot_hash": status["source_snapshot_hash"] = "0" * 64
    elif damage == "ast": info["ast_contract_version"] = "old"
    elif damage == "old_ast": lane["ast_contract_version"] = "old"
    elif damage == "parser": info["parser_evidence"]["status"] = "degraded"
    elif damage == "version": lane["version"] = "old"
    elif damage == "scope": lane["scope"] = "universal"
    elif damage == "hash": lane["source_hash"] = "0" * 64
    elif damage == "mutation": lane["mutation_proposed"] = True
    elif damage == "zero_mutation": lane["mutation_proposed"] = 0
    elif damage == "schema_claim": lane["schema_authority"] = "validated"
    elif damage == "runtime_claim": lane["runtime_proof"] = "passed"
    elif damage == "type_claim": lane["type_compatibility"] = "verified"
    elif damage == "extra": lane["validated"] = True
    elif damage == "count": lane["record_count"] = 2
    elif damage == "bool_count": lane["record_count"] = True
    elif damage == "omitted": lane["omitted"] = True
    elif damage == "offset_unit": lane["offset_unit"] = "utf16_code_units"
    elif damage == "role": row["spans"].pop("result_binding")
    elif damage == "bool_offset": row["spans"]["sink"]["start"] = True
    elif damage == "line": row["spans"]["sink"]["line"] += 1
    elif damage == "bounds": row["spans"]["sink"]["end"] = len(source) * 4
    elif damage == "relation": row["spans"]["condition_result_ref"] = row["spans"]["result_binding"]
    elif damage == "tokens": row["spans"]["success_property"]["start"] += 1
    elif damage == "duplicate": lane["records"].append(deepcopy(row)); lane["record_count"] += 1
    elif damage == "hidden_corrupt":
        lane["records"] *= 7
        lane["records"] = deepcopy(lane["records"])
        lane["records"][-1] = {"spans": {}}
        lane["record_count"] = 7
    elif damage == "unknown_status": lane["status"] = []
    else: lane.update(status="partial", omitted=1, reason="record_budget_exceeded")
    payload = {}
    project_safeparse_success_evidence(payload, info, status, snapshot_content=source)
    if damage == "legacy":
        assert "client_safeparse_success_evidence" not in payload
    else:
        view = payload["client_safeparse_success_evidence"]
        assert view["status"] == "unavailable" and view["records"] == [] and view["record_count"] is None


def test_safeparse_view_respects_all_older_records_and_packet_limits(safeparse_view_inputs):
    from tools.inspect_target import project_safeparse_success_evidence, AGENT_INSPECTION_MAX_TARGET_SPANS
    source, info = safeparse_view_inputs["positive"]
    older = {"older": {"records": [{}] * AGENT_INSPECTION_MAX_TARGET_SPANS, "omitted": 2}}
    payload = {"older": deepcopy(older["older"]), "inspection_authority": "orientation_only", "one_shot_edit_ready": False}
    baseline = deepcopy(payload)
    project_safeparse_success_evidence(payload, info, _local_projection_status(info), snapshot_content=source, syntax_sections=older)
    assert all(payload[key] == value for key, value in baseline.items())
    view = payload["client_safeparse_success_evidence"]
    assert view["shown"] == 0 and view["omitted"] == 1 and view["producer_omitted"] == 0
    assert view["reason"] == "shared_compact_or_record_budget_omission"
    for crowded, sections in (({"older": "x" * 25000}, {}),
            ({"older": {"details": [{}] * 3000}}, {}),
            ({}, {"older": {"records": [], "reason": "x" * 5000}})):
        baseline = deepcopy(crowded)
        project_safeparse_success_evidence(crowded, info, _local_projection_status(info), snapshot_content=source, syntax_sections=sections)
        assert crowded == baseline, "New section must drop before any old context changes"


def test_safeparse_view_empty_and_unavailable_need_no_coordinate_index(safeparse_view_inputs, monkeypatch):
    from tools import inspect_target
    monkeypatch.setattr(inspect_target, "_utf8_span_validator", lambda *a, **k: pytest.fail("No index for absent rows"))
    for name in ("empty", "node_budget"):
        _, info = safeparse_view_inputs[name]
        payload = {}
        inspect_target.project_safeparse_success_evidence(payload, info, _local_projection_status(info))
        view = payload["client_safeparse_success_evidence"]
        assert view["status"] == ("observed" if name == "empty" else "unavailable")
        assert view["records"] == []


@pytest.mark.parametrize("partitioned", [False, True], ids=["inline", "partitioned"])
@pytest.mark.parametrize("damage", ["none", "legacy", "corrupt_primary", "missing_snapshot", "changed_snapshot", "live_drift", "warm_poison"])
@pytest.mark.parametrize("long_path", [False, True], ids=["short", "long"])
@pytest.mark.parametrize("lane_key", ["client_safeparse_success_evidence", "effect_safeparse_callback_evidence", "effect_safeparse_merge_evidence", "safeparse_early_return_evidence"])
def test_safeparse_view_actual_sqlite_machine_brief_and_primary_refusal(tmp_path, monkeypatch, safeparse_view_inputs, partitioned, damage, long_path, lane_key):
    import sqlite3
    import tools.core.json_io as json_io
    from tools.core.agent_packet_budget import COMPACT_AGENT_PACKET_TOKENS, DEFAULT_AGENT_PACKET_BUDGET_TOKENS, context_budget_profile
    source, original = safeparse_view_inputs["early_unicode" if lane_key == "safeparse_early_return_evidence" else "merge_unicode" if lane_key == "effect_safeparse_merge_evidence" else "effect_unicode" if lane_key.startswith("effect_") else "unicode"]
    target = tmp_path / "target"
    if long_path:
        target = target / ("deep-source-" * 8) / ("fixture-" * 10)
        assert len(str(target)) > 260
    Path(native_filesystem_path(target)).mkdir(parents=True)
    file = Path(native_filesystem_path(target / "Panel.tsx"))
    file.write_bytes(source.encode("utf-8"))
    info = deepcopy(original)
    if damage == "legacy": info.pop(lane_key)
    raw = tmp_path / ".raw"
    atlas = {"MAIN": {"root_path": str(target), "files": {"Panel.tsx": info}, "symbols": []}}
    store = ArtifactStore(raw_dir=raw)
    store.use_sqlite, store.backend = True, "hybrid_sqlite"
    store._ensure_schema()
    if partitioned: store.state_payload_inline_limit_bytes = 1
    store._save_payload_to_state_table("atlas", atlas)
    store._save_atlas_to_sqlite(atlas)
    with sqlite3.connect(native_filesystem_path(raw / "codemaps.db")) as conn:
        conn.execute("UPDATE projects SET path='.' WHERE project_key='MAIN'")
        if damage == "corrupt_primary":
            if partitioned: conn.execute("DELETE FROM state_payload_parts WHERE name='atlas'")
            else: conn.execute("UPDATE state_payloads SET payload='broken' WHERE name='atlas'")
        elif damage == "missing_snapshot": conn.execute("DELETE FROM source_snapshots WHERE rel_path='Panel.tsx'")
        elif damage == "changed_snapshot": conn.execute("UPDATE source_snapshots SET content=content || '//changed' WHERE rel_path='Panel.tsx'")
    if damage == "live_drift": file.write_bytes((source + "//changed").encode("utf-8"))
    mcp_server._ATLAS_CACHE.clear()
    if damage == "warm_poison":
        poisoned = deepcopy(atlas)
        poisoned["MAIN"]["files"]["Panel.tsx"][lane_key]["mutation_proposed"] = True
        mcp_server._ATLAS_CACHE[mcp_server._artifact_cache_key(raw / "atlas.json")] = poisoned
    monkeypatch.setattr(mcp_server, "_raw_dir_for_target", lambda root: raw)
    monkeypatch.setattr(mcp_server, "_reports_dir_for_target", lambda root: tmp_path / "reports")
    monkeypatch.setattr(mcp_server, "_analysis_root_display", lambda root: str(target))
    monkeypatch.setattr(mcp_server, "_atlas", lambda *a, **k: pytest.fail("No full Atlas/cache fallback"))
    native = json_io.load_json_file
    def no_shadow(path, *args, **kwargs):
        assert Path(path) != raw / "atlas.json", "No JSON shadow rescue"
        return native(path, *args, **kwargs)
    monkeypatch.setattr(json_io, "load_json_file", no_shadow)
    focus = "safeparse_early_return" if lane_key == "safeparse_early_return_evidence" else "effect_safeparse_merge" if lane_key == "effect_safeparse_merge_evidence" else "effect_safeparse" if lane_key.startswith("effect_") else ""
    serialized = mcp_server.inspect_file("MAIN::Panel.tsx", format="json", target_root=str(target), evidence_focus=focus)
    machine = json.loads(serialized)
    view = machine.get(lane_key)
    if damage in {"legacy", "corrupt_primary"}:
        assert view is None
    elif damage in {"none", "warm_poison"}:
        assert view["status"] == "observed" and view["record_count"] == 1 and view["shown"] == 1
        assert view["source_binding"] == "snapshot_and_live_match"
        assert not machine["one_shot_edit_ready"] and machine["inspection_authority"] == "orientation_only"
        brief = mcp_server.inspect_file("MAIN::Panel.tsx", target_root=str(target), evidence_focus=focus)
        text = next(line for line in brief.splitlines() if line.startswith(lane_key + ": "))
        assert json.loads(text.split(": ", 1)[1]) == view
        focused = json.loads(mcp_server.inspect_file("MAIN::Panel.tsx", format="json", target_root=str(target), line_start=1, line_end=1, evidence_focus=focus))
        assert focused[lane_key] == view and focused["one_shot_edit_ready"]
        assert context_budget_profile(brief, budget_tokens=DEFAULT_AGENT_PACKET_BUDGET_TOKENS)["status"] == "pass"
        if focus:
            assert machine["evidence_focus"] == {"requested": focus, "other_optional_sections": "not_requested"}
            assert "client_safeparse_success_evidence" not in machine
            # Default mode preserves every older field, even when only a tiny
            # budget disclosure can fit. It never invents an observed empty lane.
            default = json.loads(mcp_server.inspect_file("MAIN::Panel.tsx", format="json", target_root=str(target)))
            disclosure = default.get(lane_key)
            assert disclosure is None or disclosure == {"status": "withheld_budget"}
            priority = ["effect_safeparse_callback_evidence", "effect_safeparse_merge_evidence", "safeparse_early_return_evidence", "resolver_factory_evidence"]
            lower_priority = set(priority[priority.index(lane_key):])
            with monkeypatch.context() as isolated:
                for lower in lower_priority:
                    isolated.setattr(mcp_server, "project_" + lower, lambda *a, **k: None)
                older = json.loads(mcp_server.inspect_file("MAIN::Panel.tsx", format="json", target_root=str(target)))
            assert {key:value for key,value in default.items() if key not in lower_priority} == older
    else:
        assert view["status"] == "unavailable" and view["records"] == [] and view["record_count"] is None
    syntax = {key: value for key, value in machine.items() if key.startswith("client_") and key.endswith("_evidence")
        or key in {"response_json_return_evidence", "manifest_type_entry_links", "wiring_source_observations", "effect_safeparse_callback_evidence", "effect_safeparse_merge_evidence", "safeparse_early_return_evidence"}}
    assert context_budget_profile(json.dumps(syntax, ensure_ascii=False), budget_tokens=COMPACT_AGENT_PACKET_TOKENS)["status"] == "pass"
    assert context_budget_profile(json.dumps(machine, ensure_ascii=False), budget_tokens=DEFAULT_AGENT_PACKET_BUDGET_TOKENS)["status"] == "pass"
    assert context_budget_profile(serialized, budget_tokens=DEFAULT_AGENT_PACKET_BUDGET_TOKENS)["status"] == "pass"


@pytest.mark.parametrize("focus", ["unknown", None, 1, [], {}])
def test_effect_safeparse_focus_rejects_unknown_scope_before_target_access(monkeypatch, focus):
    monkeypatch.setattr(mcp_server, "_raw_dir_for_target", lambda *a, **k: pytest.fail("Invalid focus must fail before target/storage access"))
    result = json.loads(mcp_server.inspect_file("MAIN::Panel.tsx", evidence_focus=focus))
    assert result["status"] == "unavailable" and result["reason"] == "unsupported_evidence_focus"
    assert result["edit_authority"] == "not_granted"


def test_wiring_view_real_primary_snapshot_live_to_machine_and_brief(wiring_view_atlas_run, monkeypatch):
    target, raw, _ = wiring_view_atlas_run
    monkeypatch.setattr(mcp_server, "_atlas", lambda *a, **k: pytest.fail("No whole Atlas materialization"))
    machine = json.loads(mcp_server.inspect_file("MAIN::Review.tsx", format="json", target_root=str(target)))
    view = machine["wiring_source_observations"]
    assert view["status"] == "observed" and view["record_count"] == view["shown"] == 2
    assert view["mutation_proposed"] is False and view["correlation_status"] == "not_evaluated"
    assert view["source_binding"] == "snapshot_and_live_match"
    candidate = machine["wiring_review_candidate"]
    assert candidate["status"] == "needs_manual_intent_review" and candidate["evidence_record_indices"] == [0, 1]
    assert candidate["relation_status"] == "not_established" and candidate["mutation_proposed"] is False
    assert not any(row.get("type") == "incomplete_wiring" for row in machine.get("findings", []))
    brief = mcp_server.inspect_file("MAIN::Review.tsx", target_root=str(target))
    line = next(row for row in brief.splitlines() if row.startswith("wiring_source_observations: "))
    assert json.loads(line.split(": ", 1)[1]) == view
    label = next(row for row in brief.splitlines() if row.startswith("wiring_review_candidate: "))
    assert json.loads(label.split(": ", 1)[1]) == candidate
    narrowed = json.loads(mcp_server.inspect_file("MAIN::Review.tsx", format="json", target_root=str(target), line_start=1, line_end=1))
    assert narrowed["wiring_source_observations"] == view
    assert not machine["one_shot_edit_ready"] and machine["inspection_authority"] == "orientation_only"


@pytest.mark.parametrize("partitioned", [False, True], ids=["inline", "partitioned"])
@pytest.mark.parametrize("damage", ["none", "legacy", "corrupt_primary", "missing_snapshot", "changed_snapshot",
    "live_drift", "missing_live", "promoted", "bad_span", "file_parser", "warm_poison", "producer_unavailable"])
def test_wiring_view_primary_snapshot_and_cache_refusals(wiring_view_atlas_run, tmp_path, monkeypatch, partitioned, damage):
    import sqlite3
    target, _, original = wiring_view_atlas_run
    local = tmp_path / "target"
    local.mkdir()
    atlas = deepcopy(original)
    for file in atlas["MAIN"]["files"]:
        (local/file).write_bytes((target/file).read_bytes())
    atlas["MAIN"]["root_path"] = str(local)
    info = atlas["MAIN"]["files"]["Review.tsx"]
    if damage == "legacy": info.pop("wiring_source_observations")
    elif damage == "promoted": info["wiring_source_observations"]["integration_status"] = "incomplete"
    elif damage == "bad_span": info["wiring_source_observations"]["records"][0]["spans"]["local_binding"]["line"] = 999
    elif damage == "file_parser": info["parser_evidence"]["status"] = "degraded"
    elif damage == "producer_unavailable": info["wiring_source_observations"] = {"status": "unavailable", "reason": "node_budget_exceeded"}
    raw = tmp_path / ".raw"
    store = ArtifactStore(raw_dir=raw)
    store.use_sqlite, store.backend = True, "hybrid_sqlite"
    store._ensure_schema()
    if partitioned: store.state_payload_inline_limit_bytes = 1
    store._save_payload_to_state_table("atlas", atlas)
    store._save_atlas_to_sqlite(atlas)
    # An apparently good shadow/cache must not rescue missing primary truth.
    (raw/"atlas.json").write_text(json.dumps(original), encoding="utf-8")
    with sqlite3.connect(raw/"codemaps.db") as conn:
        conn.execute("UPDATE projects SET path='.' WHERE project_key='MAIN'")
        if damage == "corrupt_primary":
            if partitioned: conn.execute("DELETE FROM state_payload_parts WHERE name='atlas'")
            else: conn.execute("UPDATE state_payloads SET payload='broken' WHERE name='atlas'")
        elif damage == "missing_snapshot": conn.execute("DELETE FROM source_snapshots WHERE rel_path='Review.tsx'")
        elif damage == "changed_snapshot": conn.execute("UPDATE source_snapshots SET content=content || '// changed' WHERE rel_path='Review.tsx'")
    if damage == "live_drift": (local/"Review.tsx").write_bytes((local/"Review.tsx").read_bytes() + b"\n// changed")
    elif damage == "missing_live": (local/"Review.tsx").unlink()
    mcp_server._ATLAS_CACHE.clear()
    monkeypatch.setattr(mcp_server, "_raw_dir_for_target", lambda root: raw)
    monkeypatch.setattr(mcp_server, "_reports_dir_for_target", lambda root: tmp_path/"reports")
    monkeypatch.setattr(mcp_server, "_analysis_root_display", lambda root: str(local))
    monkeypatch.setattr(mcp_server, "_atlas", lambda *a, **k: pytest.fail("No whole Atlas or JSON rescue"))
    if damage == "producer_unavailable":
        monkeypatch.setattr(source_snapshot_reader, "read_source_snapshot_binding",
            lambda *a, **k: pytest.fail("Unavailable producer must not trigger an extra snapshot read"))
    if damage == "warm_poison":
        poison = deepcopy(atlas)
        poison["MAIN"]["files"]["Review.tsx"]["wiring_source_observations"]["records"][0]["local_name"] = "poisoned"
        mcp_server._ATLAS_CACHE[mcp_server._artifact_cache_key(raw/"atlas.json")] = poison
    machine = json.loads(mcp_server.inspect_file("MAIN::Review.tsx", format="json", target_root=str(local)))
    view = machine.get("wiring_source_observations")
    if damage in {"legacy", "corrupt_primary"}:
        assert view is None and "wiring_review_request" not in machine and "wiring_review_candidate" not in machine
    elif damage in {"none", "warm_poison"}:
        assert view["status"] == "observed" and view["record_count"] == 2 and view["shown"] == 2
        assert view["records"][0]["local_name"] == "selected"
        assert machine["wiring_review_candidate"]["evidence_record_indices"] == [0, 1]
    else:
        assert view["status"] == "unavailable" and view["records"] == [] and view["record_count"] is None
        assert "wiring_review_request" not in machine
        assert "wiring_review_candidate" not in machine
    if view is not None:
        assert view["integration_status"] == "unknown" and view["mutation_proposed"] is False
    from tools.core.agent_packet_budget import COMPACT_AGENT_PACKET_TOKENS, DEFAULT_AGENT_PACKET_BUDGET_TOKENS, context_budget_profile
    syntax = {key:value for key,value in machine.items() if key.startswith("client_") and key.endswith("_evidence")
        or key in {"response_json_return_evidence", "manifest_type_entry_links", "wiring_source_observations"}}
    assert context_budget_profile(json.dumps(syntax), budget_tokens=COMPACT_AGENT_PACKET_TOKENS)["status"] == "pass"
    assert context_budget_profile(json.dumps(machine), budget_tokens=DEFAULT_AGENT_PACKET_BUDGET_TOKENS)["status"] == "pass"


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
    from tools.tests.test_react_contracts import _early_return_source
    sources["Early.tsx"] = _early_return_source()
    sources["EarlyAlias.tsx"] = _early_return_source().replace("{schemaFactory}", "{\nschemaFactory as chosenFactory\n}").replace("schemaFactory()", "chosenFactory()")
    sources["never_execute.ts"] = """import { z } from 'unexecuted-schema';
export const schemaFactory = () => { const message = 'Do not execute this factory'; return z.string().regex(message); };
throw new Error('Do not execute this module');
"""
    from tools.tests.test_react_contracts import _resolver_factory_cases
    sources["Resolver.tsx"]=_resolver_factory_cases()["positive"][0].replace("{makeSchema as", "{schemaFactory as")
    sources["ResolverFunction.tsx"]=sources["Resolver.tsx"].replace("schemaFactory as","schemaFunction as").replace("./never_execute","./function_factory")
    sources["function_factory.ts"]="import {z} from 'unexecuted-schema'; export function schemaFunction(settings: unknown) { return z.string().regex(settings); }\nthrow new Error('Do not execute');"
    sources["CallbackResolver.tsx"]=sources["Resolver.tsx"].replace("schemaFactory as","callbackFactory as").replace("./never_execute","./callback_factory")
    sources["callback_factory.ts"]="import {z} from 'unexecuted-schema'; export const callbackFactory = (isUpdate: boolean) => z.string().superRefine((val,ctx) => { if(isUpdate) return; ctx.addIssue(val); });\nthrow new Error('Never execute');"
    for name,expression in (("Zero",""),("Two","Boolean(secretRotation), other"),("Literal","false")):
        sources["Position"+name+".tsx"]=sources["CallbackResolver.tsx"].replace("Boolean(secretRotation)",expression)
    sources["PositionTwoSites.tsx"]=sources["CallbackResolver.tsx"]+"export const Other = () => resolverAdapter(resolverFactory(unknownEffect()));\n"
    for name,parameters in (("Default","settings = opaque"),("Optional","settings?: unknown"),("Multiple","settings: unknown,other: unknown")):
        sources["position_"+name+".ts"]="import {z} from 'unexecuted-schema'; export const factory = ("+parameters+") => z.string();\nthrow new Error('Never execute');"
        sources["Position"+name+".tsx"]=sources["Resolver.tsx"].replace("schemaFactory as","factory as").replace("./never_execute","./position_"+name)
    sources["chain_multiple.ts"]="import {z} from 'unexecuted-schema'; export const factory = (settings) => z.superRefine(() => { if(settings) return; }).superRefine(() => { if(settings) return; });"
    sources["ChainMultiple.tsx"]=sources["Resolver.tsx"].replace("schemaFactory as","factory as").replace("./never_execute","./chain_multiple")
    sources["chain_omitted.ts"]="import {z} from 'unexecuted-schema';\n"+"\n".join(f"export const make{i} = (p{i}) => z."+".".join(f"superRefine(() => {{ if(p{i}) return; }})" for _ in range(8))+";" for i in range(9))
    sources["ChainOmitted.tsx"]=sources["Resolver.tsx"].replace("schemaFactory as","make8 as").replace("./never_execute","./chain_omitted")
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


@pytest.fixture(scope="module",params=["const_arrow","function_declaration"])
def workspace_factory_atlas_run(tmp_path_factory,request):
    """One actual multi-project producer run; target modules must never execute."""
    import subprocess
    import sys
    from tools.core.atlas_io import load_atlas_data
    from tools.core.external_target_generation import external_target_output_slug
    from tools.tests.test_react_contracts import _early_return_source
    target = tmp_path_factory.mktemp("workspace-factory")
    contents = {
        "package.json": json.dumps({"private":True,"workspaces":["apps/*","packages/*"]}),
        "apps/app/package.json": json.dumps({"name":"@test/app","version":"1.0.0"}),
        "packages/lib/package.json": json.dumps({"name":"@test/lib","version":"1.0.0"}),
        "apps/app/tsconfig.json": json.dumps({"compilerOptions":{"baseUrl":".","paths":{"@test/lib/*":["../../packages/lib/*"]}}}),
        "apps/app/Caller.ts": _early_return_source().replace("{schemaFactory} from './never_execute'", "{make as makeSchema} from '@test/lib/factory'").replace("schemaFactory()", "makeSchema()"),
        "apps/app/factory.ts": "export const make = () => 'Unrelated same-named local export';\n",
        "packages/lib/factory.ts": "import { z } from 'unexecuted-schema';\nconst pattern = /x/;\nexport const make = () => { const message = 'invalid'; return z.string().regex(pattern, {message}); };\nthrow new Error('Do not execute the factory');\n",
    }
    if request.param == "function_declaration":
        contents["packages/lib/factory.ts"] = contents["packages/lib/factory.ts"].replace("export const make = () =>", "export function make()").replace("; };", "; }")
    parameter_factory="import {z} from 'unexecuted-schema';\nexport const make = (settings: unknown) => z.string().regex(settings);\nthrow new Error('MUST_NOT_EXECUTE');"
    if request.param=="function_declaration":
        parameter_factory=parameter_factory.replace("export const make = (settings: unknown) => z.string().regex(settings);", "export function make(settings: unknown) { return z.string().regex(settings); }")
    contents["packages/lib/parameter.ts"]=parameter_factory
    parameter_caller=contents["apps/app/Caller.ts"].replace("@test/lib/factory","@test/lib/parameter").replace("const complete", "const options = acquireOpaque();\nconst complete").replace("makeSchema()","makeSchema(options)")
    contents["apps/app/ParameterSingleCaller.ts"]=parameter_caller
    first=parameter_caller.replace("const complete", "const otherOptions = otherOpaque();\nconst completeA").replace("isValid","validA")
    second=first[first.index("const completeA"):].replace("completeA","completeB").replace("validA","validB").replace("makeSchema(options)","makeSchema(otherOptions)")
    contents["apps/app/ParameterCaller.ts"]=first+second
    for name,expression in (("unused","other"),("property","settings.value")):
        contents[f"packages/lib/parameter_{name}.ts"]=parameter_factory.replace(".regex(settings)",f".regex({expression})")
        contents[f"apps/app/parameter_{name}Caller.ts"]=parameter_caller.replace("@test/lib/parameter",f"@test/lib/parameter_{name}")
    for name,signature in {"default":"settings = opaque()","optional":"settings?: unknown","multiple":"settings: unknown, other: unknown","none":""}.items():
        contents[f"packages/lib/parameter_{name}.ts"]=parameter_factory.replace("settings: unknown",signature)
        contents[f"apps/app/parameter_{name}Caller.ts"]=parameter_caller.replace("@test/lib/parameter",f"@test/lib/parameter_{name}")
    contents["packages/lib/parameter_this.ts"]="import {z} from 'unexecuted-schema'; export function make(this: unknown) { return z.string(); }"
    contents["packages/lib/parameter_escaped.ts"]=parameter_factory.replace("settings: unknown","set\\u0074ings: unknown")
    for name in ("this","escaped"):
        contents[f"apps/app/parameter_{name}Caller.ts"]=parameter_caller.replace("@test/lib/parameter",f"@test/lib/parameter_{name}")
    unsupported = {"async":"export async function make() { return z.string(); }",
        "generator":"export function* make() { return z.string(); }",
        "generic":"export function make<T>() { return z.string(); }",
        "overload":"export function make(): unknown; export function make() { return z.string(); }",
        "branch":"export function make() { if (unknown) return z.string(); return z.number(); }"}
    for name,source in unsupported.items():
        contents[f"packages/lib/{name}.ts"]="import { z } from 'unexecuted-schema';\n"+source+"\nthrow new Error('MUST_NOT_EXECUTE');"
        contents[f"apps/app/{name}Caller.ts"]=contents["apps/app/Caller.ts"].replace("@test/lib/factory",f"@test/lib/{name}")
    for rel, text in contents.items():
        path = Path(native_filesystem_path(target / rel))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    env = dict(os.environ)
    for key in ("CODEMAPS_TARGET_PREFLIGHT_RECEIPT", "CODEMAPS_TARGET_PREFLIGHT_RECEIPT_SHA256", "CODEMAPS_TARGET_PREFLIGHT_REUSE_MODE", "CODEMAPS_TARGET_PROJECTS", "CODEMAPS_EXTERNAL_RUN_ID"):
        env.pop(key, None)
    env["CODEMAPS_TARGET_ROOT"] = str(target)
    run = subprocess.run([sys.executable,"-B","-m","tools.engines.generate_atlas"], cwd=ROOT, env=env,
                         capture_output=True, text=True, encoding="utf-8", timeout=90)
    assert run.returncode == 0, run.stderr or run.stdout
    raw = ROOT / "output/external_targets" / external_target_output_slug(str(target)) / ".raw"
    return target, raw, load_atlas_data(raw), request.param


def test_workspace_factory_actual_producer_and_public_followups(workspace_factory_atlas_run, monkeypatch):
    target, raw, atlas, kind = workspace_factory_atlas_run
    monkeypatch.setattr(mcp_server,"_atlas",lambda *a,**k:pytest.fail("No whole Atlas or second resolver"))
    caller = atlas["APP"]["files"]["Caller.ts"]
    record = next(row for row in caller["import_records"] if row.get("name") == "make")
    assert record["source"] == record["raw_source"] == "@test/lib/factory"
    assert record["workspace_source_candidate"] == {"version":"v1-declared-workspace-alias-source", "source_project":"APP", "target_ref":"LIB::factory.ts", "workspace_rel":"packages/lib/factory.ts"}
    assert "LIB::factory.ts" not in atlas["APP"]["dependencies"]["Caller.ts"]
    args = dict(file_path="APP::apps/app/Caller.ts",target_root=str(target),evidence_focus="safeparse_early_return",line_start=1,line_end=2)
    machine = json.loads(mcp_server.inspect_file(format="json",**args))
    view = machine["safeparse_early_return_evidence"]
    candidate = view["records"][0]["factory_source_candidate"]
    assert candidate["status"] == "source_candidate" and candidate["source_binding"] == "snapshot_and_live_match"
    assert candidate["target"]["atlas_ref"] == "LIB::factory.ts"
    construction = candidate["return_construction"]
    assert construction["status"] == "observed" and construction["methods"] == ["string","regex"]
    assert construction["factory_kind"] == kind
    arguments = construction["argument_bindings"]
    assert arguments["status"] == "observed" and arguments["shown"] == 2
    assert [row["name"] for row in arguments["records"]] == ["pattern","message"]
    for item in [candidate,construction,*arguments["records"]]:
        follow = item["source_followup"]
        assert follow["file_path"] == "LIB::factory.ts"
        result = json.loads(mcp_server.inspect_file(format="json",target_root=str(target),**{k:v for k,v in follow.items() if k != "tool"}))
        assert result["one_shot_edit_ready"] and result["target_path_status"]["target_project"] == "LIB"
    brief = mcp_server.inspect_file(**args)
    assert json.loads(next(line.split(": ",1)[1] for line in brief.splitlines() if line.startswith("safeparse_early_return_evidence: "))) == view
    assert view["schema_authority"] == view["factory_semantics"] == view["continuation_safety"] == "not_established"
    assert view["runtime_proof"] == "not_performed" and not view["mutation_proposed"]


def test_workspace_factory_unsupported_functions_remain_unresolved(workspace_factory_atlas_run,monkeypatch):
    target,_,_,_=workspace_factory_atlas_run
    monkeypatch.setattr(mcp_server,"_atlas",lambda *a,**k:pytest.fail("No whole Atlas or function fallback"))
    for name in ("async","generator","generic","overload","branch"):
        result=json.loads(mcp_server.inspect_file(f"APP::apps/app/{name}Caller.ts",target_root=str(target),format="json",evidence_focus="safeparse_early_return",line_start=1,line_end=2))
        candidate=result["safeparse_early_return_evidence"]["records"][0]["factory_source_candidate"]
        assert candidate["status"]=="unresolved",(name,candidate)
        assert "target" not in candidate and "source_followup" not in candidate and "return_construction" not in candidate


def _assert_workspace_factory_primary_refusal(workspace_factory_atlas_run,tmp_path,monkeypatch,partitioned,damage,*,parameterized=False):
    import sqlite3
    original_target,_,original,_ = workspace_factory_atlas_run
    target = tmp_path / "target"
    atlas = deepcopy(original)
    roots = {"MAIN":".","APP":"apps/app","LIB":"packages/lib"}
    for project, data in atlas.items():
        data["root_path"] = str(target / roots[project])
        data["project"]["root"] = data["root_path"]
        for info in data["files"].values():
            rel = info["workspace_rel"]
            path = Path(native_filesystem_path(target / rel))
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(Path(native_filesystem_path(original_target / rel)).read_bytes())
    file="ParameterSingleCaller.ts" if parameterized else "Caller.ts"
    factory="parameter.ts" if parameterized else "factory.ts"
    caller = atlas["APP"]["files"][file]
    record = next(row for row in caller["import_records"] if row.get("name") == "make")
    binding = record["workspace_source_candidate"]
    if damage == "legacy": record.pop("workspace_source_candidate")
    elif damage == "version": binding["version"] = "unknown"
    elif damage == "extra": binding["runtime_proof"] = True
    elif damage == "source_project": binding["source_project"] = "OTHER"
    elif damage == "same_project": binding["target_ref"] = "APP::factory.ts"
    elif damage == "traversal": binding["target_ref"] = "LIB::../factory.ts"
    elif damage == "workspace_path": binding["workspace_rel"] = "apps/app/factory.ts"
    elif damage in {"ambiguous","mixed_legacy"}:
        other = deepcopy(record)
        if damage == "ambiguous": other["workspace_source_candidate"]["target_ref"] = "OTHER::factory.ts"
        else: other.pop("workspace_source_candidate")
        caller["import_records"].append(other)
    elif damage == "missing_target": atlas["LIB"]["files"].pop(factory)
    elif damage == "target_identity": atlas["LIB"]["files"][factory]["project_key"] = "OTHER"
    elif damage == "target_ast": atlas["LIB"]["files"][factory]["ast_contract_version"] = "old"
    elif damage == "target_parser": atlas["LIB"]["files"][factory]["parser_evidence"]["status"] = "degraded"
    elif damage.startswith("use_"):
        lane=atlas["LIB"]["files"][factory]["factory_return_construction_evidence"]
        row=lane["records"][0]
        if damage=="use_reference": row["spans"]["parameter_use_0"]=row["spans"]["parameter_binding"]
        elif damage=="use_parent": row["parameter_uses"][0]["call_index"]=0
        elif damage=="use_coverage": row["parameter_uses_omitted"]=1
        elif damage=="use_legacy": row.pop("parameter_uses")
        elif damage=="use_hidden":
            other=deepcopy(row); other["factory_name"]="unselected"; other["parameter_uses_omitted"]=-1
            lane["records"].append(other)
    raw = tmp_path / ".raw"
    store = ArtifactStore(raw_dir=raw)
    store.use_sqlite,store.backend = True,"hybrid_sqlite"
    store._ensure_schema()
    if partitioned: store.state_payload_inline_limit_bytes = 1
    store._save_payload_to_state_table("atlas",atlas)
    store._save_atlas_to_sqlite(atlas)
    with sqlite3.connect(raw / "codemaps.db") as conn:
        for key,rel in roots.items(): conn.execute("UPDATE projects SET path=? WHERE project_key=?",(rel,key))
        if damage == "snapshot": conn.execute("DELETE FROM source_snapshots WHERE file_id IN (SELECT file_id FROM files WHERE project_key='LIB')")
        elif damage == "foreign_root": conn.execute("UPDATE projects SET path='../foreign' WHERE project_key='LIB'")
        elif damage == "absolute_project_root": conn.execute("UPDATE projects SET path=? WHERE project_key='LIB'",(str(tmp_path / "foreign"),))
        elif damage == "corrupt_primary":
            if partitioned: conn.execute("DELETE FROM state_payload_parts WHERE name='atlas'")
            else: conn.execute("UPDATE state_payloads SET payload='broken' WHERE name='atlas'")
    if damage == "live":
        path = Path(native_filesystem_path(target / "packages/lib" / factory))
        path.write_bytes(path.read_bytes() + b"\n// changed")
    mcp_server._ATLAS_CACHE.clear()
    monkeypatch.setattr(mcp_server,"_atlas",lambda *a,**k:pytest.fail("No whole Atlas/JSON rescue"))
    monkeypatch.setattr(mcp_server,"_raw_dir_for_target",lambda root:raw)
    monkeypatch.setattr(mcp_server,"_reports_dir_for_target",lambda root:tmp_path / "reports")
    monkeypatch.setattr(mcp_server,"_analysis_root_display",lambda root:str(target))
    machine = json.loads(mcp_server.inspect_file("APP::apps/app/"+file,format="json",target_root=str(target),evidence_focus="safeparse_early_return"))
    view = machine.get("safeparse_early_return_evidence")
    if damage == "corrupt_primary": assert view is None
    else:
        assert view["status"] == "observed" and view["shown"] == 1
        candidate = view["records"][0]["factory_source_candidate"]
        if damage.startswith("use_"):
            assert candidate.get("call_parameter_binding",{}).get("status")!="observed",candidate
            assert candidate.get("call_parameter_binding",{}).get("source_uses",{}).get("status")!="observed",candidate
            return
        accepted = damage in {"none", "absolute_project_root"}
        assert candidate["status"] == ("source_candidate" if accepted else "unresolved"),candidate
        if not accepted: assert "target" not in candidate and "return_construction" not in candidate
        if parameterized and accepted:
            assert candidate["call_parameter_binding"]["status"]=="observed",candidate
        elif parameterized:
            assert candidate.get("call_parameter_binding",{}).get("status")!="observed",candidate


@pytest.mark.parametrize("partitioned",[False,True],ids=["inline","partitioned"])
@pytest.mark.parametrize("damage",["none","legacy","version","extra","source_project","same_project","traversal","workspace_path","ambiguous","mixed_legacy","missing_target","target_identity","target_ast","target_parser","live","snapshot","foreign_root","absolute_project_root","corrupt_primary"])
def test_workspace_factory_exact_primary_and_refusals(workspace_factory_atlas_run,tmp_path,monkeypatch,partitioned,damage):
    _assert_workspace_factory_primary_refusal(workspace_factory_atlas_run,tmp_path,monkeypatch,partitioned,damage)


@pytest.mark.parametrize("partitioned",[False,True],ids=["inline","partitioned"])
@pytest.mark.parametrize("damage",["none","legacy","version","extra","source_project","same_project","traversal","workspace_path","ambiguous","mixed_legacy","missing_target","target_identity","target_ast","target_parser","live","snapshot","foreign_root","absolute_project_root","corrupt_primary"])
def test_factory_parameter_exact_primary_refusals(workspace_factory_atlas_run,tmp_path,monkeypatch,partitioned,damage):
    _assert_workspace_factory_primary_refusal(workspace_factory_atlas_run,tmp_path,monkeypatch,partitioned,damage,parameterized=True)


def test_factory_parameter_public_source_join_and_call_cache(workspace_factory_atlas_run,monkeypatch):
    from tools.core.atlas_io import load_atlas_data_with_identity
    target,raw,_,_=workspace_factory_atlas_run
    before=load_atlas_data_with_identity(raw).identity
    seen=[]
    real=mcp_server.project_safeparse_early_return_evidence
    def spy(*args,**kwargs):
        candidate=kwargs["source_candidate"]
        def capture(row,valid_span):
            result=candidate(row,valid_span)
            seen.append(deepcopy(result.get("call_parameter_binding")))
            return result
        kwargs["source_candidate"]=capture
        return real(*args,**kwargs)
    monkeypatch.setattr(mcp_server,"project_safeparse_early_return_evidence",spy)
    monkeypatch.setattr(mcp_server,"_atlas",lambda *a,**k:pytest.fail("No whole Atlas fallback"))
    args=dict(file_path="APP::apps/app/ParameterCaller.ts",target_root=str(target),evidence_focus="safeparse_early_return",line_start=1,line_end=2)
    machine=json.loads(mcp_server.inspect_file(format="json",**args))
    assert [r["actual_name"] for r in seen]==["options","otherOptions"],seen
    assert all(r["formal_name"]=="settings" and r["status"]=="observed" for r in seen)
    view=machine["safeparse_early_return_evidence"]
    assert view["record_count"]==2 and view["shown"]==2
    shown=[r["factory_source_candidate"].get("call_parameter_binding") for r in view["records"]]
    assert all(row is None or row["status"] in {"observed","withheld_budget"} for row in shown)
    assert all("single_parameter" not in r["factory_source_candidate"].get("return_construction",{}) for r in view["records"])
    single=json.loads(mcp_server.inspect_file(format="json",**{**args,"file_path":"APP::apps/app/ParameterSingleCaller.ts"}))
    single_row=single["safeparse_early_return_evidence"]["records"][0]["factory_source_candidate"]["call_parameter_binding"]
    assert single_row["status"]=="observed" and single_row["actual_name"]=="options" and single_row["formal_name"]=="settings"
    shown.append(single_row)
    for row in shown:
        if row and row["status"]=="observed":
            assert row["argument_value_semantics"]==row["type_compatibility"]==row["parameter_usage"]=="not_established"
            assert row["runtime_proof"]=="not_performed" and row["edit_authority"]=="not_granted" and not row["mutation_proposed"]
            for field,project in (("actual_source_followup","APP"),("formal_source_followup","LIB")):
                follow=row[field]
                result=json.loads(mcp_server.inspect_file(format="json",target_root=str(target),**{k:v for k,v in follow.items() if k!="tool"}))
                assert result["one_shot_edit_ready"] and result["target_path_status"]["target_project"]==project
    brief=mcp_server.inspect_file(**args)
    assert json.loads(next(line.split(": ",1)[1] for line in brief.splitlines() if line.startswith("safeparse_early_return_evidence: ")))==view
    assert load_atlas_data_with_identity(raw).identity==before


@pytest.mark.parametrize("name",["default","optional","multiple","none","this","escaped"])
def test_factory_parameter_unsupported_formal_is_not_a_join(workspace_factory_atlas_run,monkeypatch,name):
    target,_,_,_=workspace_factory_atlas_run
    monkeypatch.setattr(mcp_server,"_atlas",lambda *a,**k:pytest.fail("No generic callable fallback"))
    result=json.loads(mcp_server.inspect_file(f"APP::apps/app/parameter_{name}Caller.ts",format="json",target_root=str(target),evidence_focus="safeparse_early_return",line_start=1,line_end=2))
    candidate=result["safeparse_early_return_evidence"]["records"][0]["factory_source_candidate"]
    association=candidate["call_parameter_binding"]
    assert association["status"]=="unavailable" and association["reason"]=="single_formal_source_binding_unavailable"
    assert not {"actual_name","formal_name","actual_source_followup","formal_source_followup"}&set(association)


@pytest.mark.parametrize("name",["observed","unused","property"])
def test_factory_parameter_use_public_source_followup(workspace_factory_atlas_run,monkeypatch,name):
    from tools.core.agent_packet_budget import context_budget_profile,COMPACT_AGENT_PACKET_TOKENS,DEFAULT_AGENT_PACKET_BUDGET_TOKENS
    target,_,_,_=workspace_factory_atlas_run
    monkeypatch.setattr(mcp_server,"_atlas",lambda *a,**k:pytest.fail("No whole Atlas or source resolver fallback"))
    file="ParameterSingleCaller.ts" if name=="observed" else f"parameter_{name}Caller.ts"
    args=dict(file_path="APP::apps/app/"+file,target_root=str(target),evidence_focus="safeparse_early_return",line_start=1,line_end=2)
    machine=json.loads(mcp_server.inspect_file(format="json",**args))
    section=machine["safeparse_early_return_evidence"]
    association=section["records"][0]["factory_source_candidate"]["call_parameter_binding"]
    assert association["status"]=="observed" and association["actual_name"]=="options" and association["formal_name"]=="settings"
    uses=association["source_uses"]
    assert uses["status"]==("observed" if name=="observed" else "not_observed"),uses
    assert uses["record_count"]==uses["shown"]==(1 if name=="observed" else 0) and uses["omitted"]==uses["producer_omitted"]==0
    assert uses["source_binding"]=="snapshot_and_live_match" and uses["parameter_effects"]=="not_established"
    assert uses["runtime_proof"]=="not_performed" and uses["edit_authority"]=="not_granted" and not uses["mutation_proposed"]
    if name=="observed":
        row=uses["records"][0]
        assert row["method_name"]=="regex" and row["call_index"]==1 and row["argument_index"]==0
        follow=row["source_followup"]
        result=json.loads(mcp_server.inspect_file(format="json",target_root=str(target),**{k:v for k,v in follow.items() if k!="tool"}))
        assert result["one_shot_edit_ready"] and result["target_path_status"]["target_project"]=="LIB"
    else: assert uses["reason"]=="no_retained_direct_identifier_use_not_absence"
    brief=mcp_server.inspect_file(**args)
    assert json.loads(next(line.split(": ",1)[1] for line in brief.splitlines() if line.startswith("safeparse_early_return_evidence: ")))==section
    assert context_budget_profile(json.dumps(machine,ensure_ascii=False),budget_tokens=DEFAULT_AGENT_PACKET_BUDGET_TOKENS)["status"]=="pass"
    assert context_budget_profile(json.dumps(section,ensure_ascii=False),budget_tokens=COMPACT_AGENT_PACKET_TOKENS)["status"]=="pass"


@pytest.mark.parametrize("partitioned",[False,True],ids=["inline","partitioned"])
@pytest.mark.parametrize("damage",["use_reference","use_parent","use_coverage","use_legacy","use_hidden"])
def test_factory_parameter_use_primary_corruption_refused(workspace_factory_atlas_run,tmp_path,monkeypatch,partitioned,damage):
    _assert_workspace_factory_primary_refusal(workspace_factory_atlas_run,tmp_path,monkeypatch,partitioned,damage,parameterized=True)


@pytest.mark.parametrize("file", ["Early.tsx", "EarlyAlias.tsx"])
def test_early_factory_link_actual_sqlite_alias_followup_and_authority(imported_parser_atlas_run, monkeypatch, file):
    from tools.core.agent_packet_budget import context_budget_profile, COMPACT_AGENT_PACKET_TOKENS, DEFAULT_AGENT_PACKET_BUDGET_TOKENS
    target, _, atlas = imported_parser_atlas_run
    monkeypatch.setattr(mcp_server, "_atlas", lambda *a, **k: pytest.fail("No whole Atlas materialization"))
    machine = json.loads(mcp_server.inspect_file("MAIN::" + file, format="json", target_root=str(target), evidence_focus="safeparse_early_return"))
    view = machine["safeparse_early_return_evidence"]
    assert view["status"] == "observed" and view["shown"] == view["record_count"] == 1
    record = view["records"][0]
    candidate = record["factory_source_candidate"]
    assert candidate["status"] == "source_candidate" and candidate["source_binding"] == "snapshot_and_live_match"
    assert candidate["target"] == {"atlas_ref": "MAIN::never_execute.ts", "target_file": "never_execute.ts",
        "source_hash": atlas["MAIN"]["files"]["never_execute.ts"]["hash"], "symbol": "schemaFactory", "start_line": 3, "end_line": 3}
    construction = candidate["return_construction"]
    assert construction["status"] == "observed" and construction["methods"] == ["string", "regex"]
    assert construction["import_binding"]["source"] == "unexecuted-schema" and construction["source_followup"]["line_start"] == 3
    arguments = construction["argument_bindings"]
    assert arguments["status"] == "observed" and arguments["shown"] == arguments["record_count"] == 1
    assert arguments["records"][0]["name"] == "message" and arguments["records"][0]["binding_scope"] == "factory_const"
    assert arguments["records"][0]["source_followup"] == {"tool":"inspect_file","file_path":"MAIN::never_execute.ts","line_start":3,"line_end":3}
    assert record["factory_source_followup"]["file_path"] == "MAIN::" + file
    followup = candidate["source_followup"]
    reread = json.loads(mcp_server.inspect_file(followup["file_path"], format="json", target_root=str(target), line_start=followup["line_start"], line_end=followup["line_end"]))
    assert reread["one_shot_edit_ready"] and "Do not execute this factory" in json.dumps(reread)
    assert view["factory_semantics"] == view["schema_authority"] == view["continuation_safety"] == "not_established"
    assert view["runtime_proof"] == "not_performed" and view["edit_authority"] == "not_granted" and not view["mutation_proposed"]
    brief = mcp_server.inspect_file("MAIN::" + file, target_root=str(target), evidence_focus="safeparse_early_return")
    assert json.loads(next(line for line in brief.splitlines() if line.startswith("safeparse_early_return_evidence: ")).split(": ", 1)[1]) == view
    assert context_budget_profile(json.dumps(view, ensure_ascii=False), budget_tokens=COMPACT_AGENT_PACKET_TOKENS)["status"] == "pass"
    for text in (json.dumps(machine, indent=2, ensure_ascii=False), brief):
        assert context_budget_profile(text, budget_tokens=DEFAULT_AGENT_PACKET_BUDGET_TOKENS)["status"] == "pass"
    original = mcp_server.project_safeparse_early_return_evidence
    enriched = json.loads(mcp_server.inspect_file("MAIN::" + file, format="json", target_root=str(target)))
    monkeypatch.setattr(mcp_server, "project_safeparse_early_return_evidence", lambda *a, **k: None)
    with monkeypatch.context() as lower:
        lower.setattr(mcp_server,"project_resolver_factory_evidence",lambda *a,**k:None)
        older = json.loads(mcp_server.inspect_file("MAIN::" + file, format="json", target_root=str(target)))
    assert {key:value for key,value in enriched.items() if key not in {"safeparse_early_return_evidence","resolver_factory_evidence"}} == older
    monkeypatch.setattr(mcp_server, "project_safeparse_early_return_evidence", original)


@pytest.mark.parametrize("partitioned", [False, True], ids=["inline", "partitioned"])
@pytest.mark.parametrize("damage", ["none", "type_only", "default_import", "missing_bindings", "duplicate_binding", "wrong_local", "wrong_imported", "binding_line", "binding_bool_line", "binding_scope", "binding_budget", "missing_module", "ambiguous_module", "cross_project", "traversal", "barrel", "function_export", "duplicate_export", "private_export", "target_hash", "target_ast", "target_parser", "target_span", "candidate_drift", "caller_drift", "missing_snapshot", "corrupt_primary", "construction_legacy", "construction_bad", "construction_empty", "construction_selector_span", "construction_exception", "argument_legacy", "argument_empty", "argument_bad", "argument_hash", "argument_ast", "argument_duplicate"])
def test_early_factory_link_primary_binding_and_refusals(imported_parser_atlas_run, tmp_path, monkeypatch, damage, partitioned, request, record_testsuite_property):
    import sqlite3
    from tools.core.agent_packet_budget import context_budget_profile, COMPACT_AGENT_PACKET_TOKENS
    _snapshot_case_id = hashlib.sha256(request.node.nodeid.encode("utf-8")).hexdigest()
    with _snapshot_cost_phase(record_testsuite_property, _snapshot_case_id, "case_target_and_copy"):
        target, _, original = imported_parser_atlas_run
        local = tmp_path / "target"
        local.mkdir()
        atlas = deepcopy(original)
        files = atlas["MAIN"]["files"]
        for file in files:
            (local / file).write_bytes((target / file).read_bytes())
        atlas["MAIN"]["root_path"] = str(local)
    caller, candidate = files["EarlyAlias.tsx"], files["never_execute.ts"]
    declared = caller["direct_import_binding_evidence"]["records"]
    assert len(declared) == 1
    binding = declared[0]
    imports = [row for row in caller["import_records"] if row["raw_source"] == "./never_execute"]
    assert len(imports) == 1
    if damage == "type_only": binding["typeOnly"] = True
    elif damage == "default_import": binding["kind"] = "default"
    elif damage == "missing_bindings": caller.pop("direct_import_binding_evidence")
    elif damage == "duplicate_binding": declared.append({**binding, "typeOnly": True})
    elif damage == "wrong_local": binding["localName"] = "otherFactory"
    elif damage == "wrong_imported": binding["importedName"] = "otherExport"
    elif damage == "binding_line": binding["line"] = binding["endLine"] = 10000
    elif damage == "binding_bool_line": binding["line"] = True
    elif damage == "binding_scope": binding["bindingScope"] = "nested"
    elif damage == "binding_budget": declared.extend([deepcopy(binding)] * mcp_server._state_flow_brief_policy()["focus_scan_limit"])
    elif damage == "missing_module": imports[0]["source"] = "absent.ts"
    elif damage == "ambiguous_module": caller["import_records"].append({**imports[0], "source": "other.ts"})
    elif damage == "cross_project": imports[0]["source"] = "OTHER::never_execute.ts"
    elif damage == "traversal": imports[0]["source"] = "../never_execute.ts"
    elif damage == "barrel": candidate["symbols"][0]["type"] = "ReExportedSymbol"
    elif damage == "function_export": candidate["symbols"][0]["type"] = "Function"
    elif damage == "duplicate_export": candidate["symbols"].append(deepcopy(candidate["symbols"][0]))
    elif damage == "private_export": candidate["symbols"][0]["exported"] = False
    elif damage == "target_hash": candidate["hash"] = "0" * 64
    elif damage == "target_ast": candidate["ast_contract_version"] = "old"
    elif damage == "target_parser": candidate["parser_evidence"]["status"] = "degraded"
    elif damage == "target_span": candidate["symbols"][0]["end_line"] = 10000
    elif damage == "construction_legacy": candidate.pop("factory_return_construction_evidence")
    elif damage == "construction_bad": candidate["factory_return_construction_evidence"]["records"][0]["methods"][0] = "other"
    elif damage == "construction_empty": candidate["factory_return_construction_evidence"].update(records=[], record_count=0)
    elif damage == "construction_selector_span": candidate["symbols"][0]["start"] += 1
    elif damage == "argument_legacy": candidate.pop("factory_argument_const_evidence")
    elif damage == "argument_empty": candidate["factory_argument_const_evidence"].update(records=[],record_count=0)
    elif damage == "argument_bad": candidate["factory_argument_const_evidence"]["records"][0]["spans"]["binding"]["line"] += 1
    elif damage == "argument_hash": candidate["factory_argument_const_evidence"]["source_hash"] = "0" * 64
    elif damage == "argument_ast": candidate["factory_argument_const_evidence"]["ast_contract_version"] = "old"
    elif damage == "argument_duplicate":
        lane = candidate["factory_argument_const_evidence"]
        lane["records"].append(deepcopy(lane["records"][0])); lane["record_count"] += 1
    with _snapshot_cost_phase(record_testsuite_property, _snapshot_case_id, "store_and_schema"):
        raw = tmp_path / ".raw"
        store = ArtifactStore(raw_dir=raw)
        store.use_sqlite, store.backend = True, "hybrid_sqlite"
        store._ensure_schema()
    if partitioned: store.state_payload_inline_limit_bytes = 1
    with _snapshot_cost_phase(record_testsuite_property, _snapshot_case_id, "canonical_primary_write"):
        _snapshot_write_profile = store._save_payload_to_state_table("atlas", atlas)
    _record_snapshot_writer_cost(record_testsuite_property, _snapshot_case_id, _snapshot_write_profile)
    with _snapshot_cost_phase(record_testsuite_property, _snapshot_case_id, "relational_and_snapshots"):
        store._save_atlas_to_sqlite(atlas)
    with _snapshot_cost_phase(record_testsuite_property, _snapshot_case_id, "damage_and_live_drift"):
        with sqlite3.connect(raw / "codemaps.db") as conn:
            conn.execute("UPDATE projects SET path = '.' WHERE project_key='MAIN'")
            if damage == "corrupt_primary":
                if partitioned: conn.execute("DELETE FROM state_payload_parts WHERE name='atlas'")
                else: conn.execute("UPDATE state_payloads SET payload='broken' WHERE name='atlas'")
            elif damage == "missing_snapshot":
                conn.execute("DELETE FROM source_snapshots WHERE file_id IN (SELECT file_id FROM files WHERE rel_path='never_execute.ts')")
        if damage in {"candidate_drift", "caller_drift"}:
            file = local / ("never_execute.ts" if damage == "candidate_drift" else "EarlyAlias.tsx")
            file.write_bytes(file.read_bytes() + b"\n// changed")
    mcp_server._ATLAS_CACHE.clear()
    monkeypatch.setattr(mcp_server, "_atlas", lambda *a, **k: pytest.fail("Whole Atlas materialized"))
    monkeypatch.setattr(mcp_server, "_raw_dir_for_target", lambda root: raw)
    monkeypatch.setattr(mcp_server, "_reports_dir_for_target", lambda root: tmp_path / "reports")
    monkeypatch.setattr(mcp_server, "_analysis_root_display", lambda root: str(local))
    if damage == "construction_exception":
        native = source_snapshot_reader.read_source_snapshot_binding
        def unavailable(*args, **kwargs):
            if inspect.currentframe().f_back.f_code.co_name == "_imported_parser_source_candidate": raise OSError("construction-only read failure")
            return native(*args, **kwargs)
        monkeypatch.setattr(source_snapshot_reader, "read_source_snapshot_binding", unavailable)
    _snapshot_primary_reader = mcp_server.load_atlas_file_metadata
    _snapshot_primary_calls = 0
    _snapshot_primary_seconds = 0.0
    _snapshot_primary_timing_available = True

    def _snapshot_observed_primary_read(*args, **kwargs):
        nonlocal _snapshot_primary_calls, _snapshot_primary_seconds, _snapshot_primary_timing_available
        _snapshot_primary_calls += 1
        try:
            started = time.perf_counter()
        except Exception:
            started = None
        try:
            return _snapshot_primary_reader(*args, **kwargs)
        finally:
            try:
                elapsed = time.perf_counter() - started
                if math.isfinite(elapsed) and elapsed >= 0:
                    _snapshot_primary_seconds += elapsed
                else:
                    _snapshot_primary_timing_available = False
            except Exception:
                _snapshot_primary_timing_available = False

    try:
        with monkeypatch.context() as _snapshot_primary_patch:
            _snapshot_primary_patch.setattr(mcp_server, "load_atlas_file_metadata", _snapshot_observed_primary_read)
            with _snapshot_cost_phase(record_testsuite_property, _snapshot_case_id, "mcp_consumer"):
                machine = json.loads(mcp_server.inspect_file("MAIN::EarlyAlias.tsx", format="json", target_root=str(local), evidence_focus="safeparse_early_return"))
    finally:
        _record_snapshot_cost_property(record_testsuite_property, _snapshot_case_id,
                                       "mcp_primary_metadata_calls", _snapshot_primary_calls)
        _record_snapshot_cost_property(record_testsuite_property, _snapshot_case_id,
                                       "mcp_primary_metadata_seconds",
                                       _snapshot_primary_seconds if _snapshot_primary_timing_available else "unavailable")
    view = machine.get("safeparse_early_return_evidence")
    if damage == "corrupt_primary":
        assert view is None, "No shadow/whole-Atlas rescue or observed-empty from corrupt primary"
    elif damage == "caller_drift":
        assert view["status"] == "unavailable" and not view["records"]
    else:
        assert view["status"] == "observed" and view["shown"] == 1
        source_candidate = view["records"][0]["factory_source_candidate"]
        assert source_candidate["status"] == ("source_candidate" if damage == "none" or damage.startswith(("construction_","argument_")) else "unresolved"), source_candidate
        if damage.startswith("construction_"):
            assert source_candidate["return_construction"]["status"] == ("not_observed" if damage in {"construction_empty", "construction_selector_span"} else "unavailable")
        elif damage.startswith("argument_"):
            construction = source_candidate["return_construction"]
            assert construction["status"] == "observed" and construction["methods"] == ["string","regex"]
            assert construction["argument_bindings"]["status"] == ("not_observed" if damage == "argument_empty" else "unavailable")
        elif damage != "none": assert "target" not in source_candidate and source_candidate["reason"]
        assert view["records"][0]["factory_source_followup"]["file_path"] == "MAIN::EarlyAlias.tsx"
        assert context_budget_profile(json.dumps(view, ensure_ascii=False), budget_tokens=COMPACT_AGENT_PACKET_TOKENS)["status"] == "pass"


def test_early_factory_link_budget_does_not_truncate_candidate_or_replay_invalid_rows(safeparse_view_inputs):
    from tools.inspect_target import project_safeparse_early_return_evidence
    source, info = safeparse_view_inputs["early_record_budget"]
    calls = []
    def oversized(row, valid_span):
        calls.append(row)
        return {"status": "unresolved", "reason": "x" * 10000, "source_binding": "unavailable"}
    payload = {}
    project_safeparse_early_return_evidence(payload, info, _local_projection_status(info), snapshot_content=source, source_candidate=oversized)
    view = payload["safeparse_early_return_evidence"]
    assert len(calls) == 1 and view["shown"] == 0 and view["omitted"] == view["record_count"] == 64
    assert view["reason"] == "shared_compact_or_record_budget_omission"
    malformed = deepcopy(info)
    malformed["safeparse_early_return_evidence"]["records"][-1]["spans"]["guard"]["line"] += 1
    calls.clear()
    payload = {}
    project_safeparse_early_return_evidence(payload, malformed, _local_projection_status(malformed), snapshot_content=source, source_candidate=oversized)
    assert not calls and payload["safeparse_early_return_evidence"]["status"] == "unavailable"


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
