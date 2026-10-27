"""Opt-in declaration header orientation, never implementation/type/runtime proof."""
from contextlib import closing
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess

import pytest

from tools.core import artifact_store, atlas_io, atlas_typescript_inputs as inputs
from tools.core import declaration_contract_view as view
from tools.core.agent_packet_budget import COMPACT_AGENT_PACKET_TOKENS, context_budget_profile
from tools.mcp import server
from tools.tests.test_atlas_declaration_inspection import route, store_plane


@pytest.fixture(scope="module")
def plane(tmp_path_factory):
    root = tmp_path_factory.mktemp("declaration-search")
    text = ("\ufeff// ç 🌍\r\nexport interface ContractPrefix {}\r\nexport type ContractAlias = string;\r\n"
        "export interface Contract { value: string }\r\nexport declare function run(): void;\r\n"
        "export declare function run(value: string): void;\r\nexport interface Çorba {}\r\n"
        "export interface Straße {}\r\nexport { Contract as PublicContract } from './types';\r\n"
        "declare global { interface Window { extra: string } }\r\n")
    names = ["index.d.ts", "index.d.mts", "index.d.cts"]
    for name in names:
        (root / name).write_bytes(text.encode("utf-8"))
    capture = inputs.capture_auxiliary_inputs(root, [(str(root / n), n, n) for n in names])
    response = subprocess.run(["node", str(Path(__file__).resolve().parents[2] / "tools/engines/ast_sequencer.cjs"),
        "--batch-json", *inputs.declaration_input_paths(root, capture)], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert response.returncode == 0, response.stderr
    result = inputs.build_declaration_contract_evidence(root, capture, json.loads(response.stdout))
    assert result["status"] == "partial" and result["selected_files"] == 3
    assert all(v["parser_input_sha256"] == hashlib.sha256(text.encode("utf-8")).hexdigest() for v in result["files"].values())
    return result


NORMAL = ("_find_symbol_matches", "_find_class_method_search_matches", "_find_store_action_search_matches",
          "_find_direct_import_binding_search_matches", "_find_qualified_import_call_search_matches")


def mount(tmp_path, monkeypatch, plane, mode="inline", project="MAIN", escaped=False):
    store = store_plane(tmp_path, plane, partitioned=mode == "partitioned", project=project)
    content = None
    if mode == "json":
        store.use_sqlite = False
        monkeypatch.setattr(atlas_io, "RAW_DIR", store._raw_dir)
        monkeypatch.setattr(artifact_store, "STORE", store)
        content = ("\ufeff" + json.dumps({project: {"declaration_contract_evidence": plane}},
            ensure_ascii=escaped, indent=2).replace("\n", "\r\n")).encode("utf-8")
        (store._raw_dir / "atlas.json").write_bytes(content)
        monkeypatch.setattr(store, "load_sqlite_atlas_declaration_metadata", lambda *_a: pytest.fail("No residual SQLite borrow"))
    route(monkeypatch, store)
    for name in NORMAL:
        monkeypatch.setattr(server, name, lambda *_a, **_k: pytest.fail("No normal/derived union"))
    return store, content


def search(query="Contract", project="MAIN", **kwargs):
    return json.loads(server.search_symbols(query, project, evidence_plane="declaration", format="json", **kwargs))


def settings(monkeypatch, **changes):
    contract = server._agent_surface_contract()
    policy = {**contract["symbol_search_policy"], **changes}
    monkeypatch.setattr(server, "_agent_surface_contract", lambda: {**contract, "symbol_search_policy": policy})
    return policy


@pytest.mark.parametrize("mode", ["inline", "partitioned", "json"])
@pytest.mark.parametrize("project", ["MAIN", 'LibÇ"🌍'])
def test_real_primary_single_read_metadata_and_followup(tmp_path, monkeypatch, plane, mode, project):
    store, content = mount(tmp_path, monkeypatch, plane, mode, project)
    original, reads = view.load_atlas_declaration_metadata, []
    def observed(raw, key):
        result = original(raw, key)
        reads.append(result)
        return result
    monkeypatch.setattr(view, "load_atlas_declaration_metadata", observed)
    result = search(project=project, target_root="selected-target")
    assert len(reads) == 1 and result["stored_atlas_identity"] == reads[0].identity
    if content:
        assert result["stored_atlas_identity"] == "json:" + hashlib.sha256(content).hexdigest()
    assert result["status"] == "partial" and result["coverage"]["valid_files"] == 3
    assert result["coverage"]["known_uninspected_module_bodies"] == 3
    assert result["source_binding"] == "stored_declaration_metadata_only"
    assert result["live_source_binding"] == "not_checked" and result["target_files"] == []
    assert result["runtime_execution"] == result["semantic_type_safety"] == "not_established"
    assert result["conditional_resolution"] == "not_evaluated"
    assert all(result[k] is False for k in ("source_grounded_for_inspection", "one_shot_edit_ready", "mutation_proposed"))
    assert result["candidate_count"] == result["shown"] + result["omitted"] > 0
    assert result["matches"][0]["name"] == "Contract"
    for row in result["matches"]:
        rel = row["declaration_ref"].split("::", 1)[1]
        assert row["captured_input_sha256"] == plane["files"][rel]["parser_input_sha256"]
        assert row["start_byte"] >= len("\ufeff// ç 🌍\r\n".encode("utf-8"))
        assert row["followup"] == {"tool": "inspect_file", "arguments": {
            "file_path": project + "::" + rel, "target_root": "selected-target"}}
        follow = json.loads(server.inspect_file(**row["followup"]["arguments"], format="json"))
        assert follow["source_binding"] == "stored_declaration_metadata_only"
        assert follow["one_shot_edit_ready"] is False
        assert not set(row) & {"file", "repo_relative_path", "source", "line", "snippet", "target_ref"}
    assert context_budget_profile(json.dumps(result, ensure_ascii=False), budget_tokens=COMPACT_AGENT_PACKET_TOKENS)["status"] == "pass"


@pytest.mark.parametrize("query,expected", [("çorba", 3), ("straẞe", 3), ("STRASSE", 0),
    ("%", 0), ("*", 0), ("[", 0), ("Contract$", 0), ("MAIN", 0), (".d.mts", 8)])
def test_literal_lowercase_name_and_indexed_path_only(tmp_path, monkeypatch, plane, query, expected):
    mount(tmp_path, monkeypatch, plane)
    out = search(query)
    # Module/global headers may have no names: only retained named occurrences count.
    actual = sum(1 for rel, v in plane["files"].items() for r in v["records"] for n in r["names"]
                 if query.lower() in n.lower() or query.lower() in rel.lower())
    assert out["candidate_count"] == actual
    if query != ".d.mts":
        assert actual == expected
    assert out["status"] == "partial" and out["search_truncated"] is False


@pytest.mark.parametrize("project", ["", " ", " MAIN", "MAIN ", "*", "all", "ALL", "any", "MAIN::x", "x\x00y", "\ud800"])
def test_nonexact_project_refused_before_primary(monkeypatch, project):
    monkeypatch.setattr(view, "load_atlas_declaration_metadata", lambda *_a: pytest.fail("Scope rejected before read"))
    out = search(project=project)
    assert out["status"] == "unavailable" and out["candidate_count"] is None and out["stored_atlas_identity"] is None


@pytest.mark.parametrize("query", ["", " ", None, 0, "\ud800", "x" * 20000])
def test_invalid_or_over_budget_query_refused_before_primary(monkeypatch, query):
    monkeypatch.setattr(view, "load_atlas_declaration_metadata", lambda *_a: pytest.fail("Query rejected before read"))
    out = search(query)
    assert out["status"] == "unavailable" and out["matches"] == [] and out["candidate_count"] is None
    assert view._fits(out)


@pytest.mark.parametrize("mode", ["partitioned", "json"])
@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_selected_primary_failure_never_falls_back(tmp_path, monkeypatch, plane, mode, damage):
    store, _ = mount(tmp_path, monkeypatch, plane, mode)
    if mode == "json":
        path = store._raw_dir / "atlas.json"
        path.unlink() if damage == "missing" else path.write_bytes(b'{"MAIN":{}')
    else:
        (store._raw_dir / "atlas.json").write_text(json.dumps({"MAIN": {"declaration_contract_evidence": plane}}), encoding="utf-8")
        with closing(sqlite3.connect(store.db_manager.db_path)) as conn, conn:
            if damage == "missing": conn.execute("DELETE FROM state_payloads WHERE name='atlas'")
            else: conn.execute("UPDATE state_payloads SET payload_sha=? WHERE name='atlas'", ("0" * 64,))
    out = search()
    assert out["status"] == "unavailable" and out["candidate_count"] is None and out["matches"] == []


@pytest.mark.parametrize("damage", ["hash", "span", "counter", "policy", "size", "parser", "extra", "name"])
@pytest.mark.parametrize("all_files", [False, True])
def test_invalid_files_are_not_candidates_or_clean_empty(tmp_path, monkeypatch, plane, damage, all_files):
    value = deepcopy(plane)
    for rel, row in value["files"].items():
        if not all_files and rel != "index.d.ts": continue
        if damage == "hash": row["parser_input_sha256"] = "not-a-hash"
        if damage == "span": row["records"][0]["end_byte"] = row["size_bytes"] + 1
        if damage == "counter": row["record_count"] = True
        if damage == "policy": row["policy_sha256"] = "0" * 64
        if damage == "size": row["size_bytes"] = inputs.auxiliary_input_policy()["max_file_bytes"] + 1
        if damage == "parser": row["parser_kind"] = "regex"
        if damage == "extra": row["runtime_proven"] = True
        if damage == "name": row["records"][0]["names"] = ["\ud800"]
    # The legacy writer replaces unpaired surrogates while UTF-8 encoding.
    # Preserve this adversarial input as escaped JSON primary instead of testing
    # the writer's normalized '?' and calling it the original invalid name.
    mount(tmp_path, monkeypatch, value, mode="json" if damage == "name" else "inline", escaped=damage == "name")
    out = search()
    assert out["coverage"]["invalid_files"] == (3 if all_files else 1)
    if all_files:
        assert out["status"] == "unavailable" and out["candidate_count"] is None
        assert "candidate_count_semantics" not in out
    else:
        assert out["status"] == "partial" and out["candidate_count"] > 0
        assert all(r["declaration_ref"] != "MAIN::index.d.ts" for r in out["matches"])


@pytest.mark.parametrize("damage", ["policy", "count", "runtime", "version", "missing"])
def test_plane_refusal_has_no_absence_claim(tmp_path, monkeypatch, plane, damage):
    value = deepcopy(plane)
    if damage == "policy": value["policy_sha256"] = "0" * 64
    if damage == "count": value["selected_files"] = True
    if damage == "runtime": value["runtime_execution"] = "proven"
    if damage == "version": value["version"] = "legacy"
    if damage == "missing": value = {}
    mount(tmp_path, monkeypatch, value)
    out = search()
    assert out["status"] == "unavailable" and out["candidate_count"] is None


def test_best_rank_precedes_cap_and_overloads_preserve_occurrences(tmp_path, monkeypatch, plane):
    mount(tmp_path, monkeypatch, plane)
    settings(monkeypatch, max_candidate_rows_per_source=1)
    out = search()
    assert out["candidate_count"] == 1 and out["search_truncated"] is True
    assert out["matches"][0]["name"] == "Contract"  # producer emitted weak prefix/type earlier
    assert out["coverage"]["matching_name_occurrences"] > 1
    assert out["candidate_count_semantics"] == "lower_bound"
    settings(monkeypatch, max_candidate_rows_per_source=500, machine_max_visible_items=1)
    out = search("run")
    assert out["candidate_count"] == 6 and out["search_truncated"] is False
    assert out["shown"] == 1 and out["omitted"] == 5 and out["display_truncated"] is True
    # Overloads are distinct spans, not a deduplicated name or resolved implementation.
    rows = plane["files"]["index.d.ts"]["records"]
    assert len({r["start_byte"] for r in rows if r["names"] == ["run"]}) == 2


@pytest.mark.parametrize("case", ["no_match", "empty", "unavailable", "omitted_files"])
def test_empty_and_producer_omissions_stay_distinct(tmp_path, monkeypatch, plane, case):
    value = deepcopy(plane)
    if case in {"empty", "unavailable"}:
        value.update(files={}, selected_files=0, omitted_files=0, status="observed" if case == "empty" else "unavailable")
    if case == "omitted_files": value["omitted_files"] = 7
    mount(tmp_path, monkeypatch, value)
    out = search("NotRecorded")
    if case == "unavailable":
        assert out["candidate_count"] is None and out["status"] == "unavailable"
    else:
        assert out["candidate_count"] == out["shown"] == out["omitted"] == 0
        assert out["reason"] == "no_matching_retained_names"
        assert out["status"] == ("available" if case == "empty" else "partial")
        assert out["coverage"]["omitted_files"] == (7 if case == "omitted_files" else 0)
    assert out["runtime_execution"] == "not_established" and out["one_shot_edit_ready"] is False


def test_overload_rows_and_shared_display_limits_are_independent(tmp_path, monkeypatch, plane):
    value = deepcopy(plane)
    value["files"] = {"index.d.ts": value["files"]["index.d.ts"]}
    value["selected_files"] = 1
    mount(tmp_path, monkeypatch, value)
    settings(monkeypatch, brief_max_visible_items=1, machine_max_visible_items=2)
    brief = server.search_symbols("run", evidence_plane="declaration")
    brief_out = json.loads(brief.split("```json\n", 1)[1].rsplit("\n```", 1)[0])
    machine = search("run")
    assert brief_out["shown"] == 1 and machine["shown"] == 2
    assert machine["candidate_count"] == 2 and not machine["search_truncated"]
    assert len({r["start_byte"] for r in machine["matches"]}) == 2
    assert all(r["name"] == "run" for r in machine["matches"])


@pytest.mark.parametrize("format", ["brief", "json", "machine"])
def test_final_render_budget_and_read_only_target_switch(tmp_path, monkeypatch, plane, format):
    a, _ = mount(tmp_path / "a", monkeypatch, plane)
    original = view.load_atlas_declaration_metadata(a._raw_dir, "MAIN").identity
    first = server.search_symbols(".d.", format=format, evidence_plane="declaration")
    assert context_budget_profile(first, budget_tokens=COMPACT_AGENT_PACKET_TOKENS)["status"] == "pass"
    if format != "brief":
        out = json.loads(first)
        assert out["stored_atlas_identity"] == original and out["shown"] + out["omitted"] == out["candidate_count"]
    assert view.load_atlas_declaration_metadata(a._raw_dir, "MAIN").identity == original
    second = deepcopy(plane)
    second["files"]["index.d.ts"]["records"][2]["names"] = ["OtherTarget"]
    mount(tmp_path / "b", monkeypatch, second)
    out = search("OtherTarget")
    assert out["candidate_count"] == 1 and out["stored_atlas_identity"] != original
    assert search(project="Missing")["status"] == "unavailable"


@pytest.mark.parametrize("format", ["brief", "json", "machine"])
@pytest.mark.parametrize("empty", [False, True])
def test_default_and_explicit_indexed_are_identical(monkeypatch, tmp_path, format, empty):
    monkeypatch.setattr(server, "declaration_symbol_search", lambda *_a, **_k: pytest.fail("Indexed must not read declarations"))
    monkeypatch.setattr(server, "_raw_dir_for_target", lambda _a: tmp_path)
    rows = [] if empty else [{"name": "Contract", "type": "Function", "project": "MAIN", "file": "src/a.ts"}]
    monkeypatch.setattr(server, NORMAL[0], lambda *_a, **_k: rows)
    for name in NORMAL[1:]:
        monkeypatch.setattr(server, name, lambda *_a, **_k: ([], {"status": "not_evaluated"}))
    implicit = server.search_symbols("Contract", "MAIN", "", format)
    assert implicit == server.search_symbols("Contract", "MAIN", "", format, evidence_plane="indexed")
    if not empty and format != "brief": assert isinstance(json.loads(implicit), list)


def test_unknown_mode_and_invalid_target_are_bounded_refusals(monkeypatch):
    monkeypatch.setattr(view, "load_atlas_declaration_metadata", lambda *_a: pytest.fail("Invalid request must not read"))
    out = json.loads(server.search_symbols("x", evidence_plane="all", format="json"))
    assert out["reason"] == "unsupported_evidence_plane"
    def invalid(_a): raise ValueError("private host detail")
    monkeypatch.setattr(server, "_raw_dir_for_target", invalid)
    out = search()
    assert out["reason"] == "invalid_external_target" and "private host detail" not in json.dumps(out)


@pytest.mark.parametrize("change", [{"declaration_search": {}}, {"max_candidate_rows_per_source": True},
    {"machine_max_visible_items": 0}, {"brief_max_visible_items": -1}])
def test_policy_errors_fail_closed_without_fabricated_defaults(monkeypatch, change):
    settings(monkeypatch, **change)
    with pytest.raises(ValueError): search()
