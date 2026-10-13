"""Direct call-result property bindings are source facts, never callback/runtime proof."""
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from tools.engines.generate_atlas import AST_CONTRACT_VERSION, _normalize_polyglot_symbols

ROOT = Path(__file__).resolve().parents[2]
KEY = "call_result_property_binding_evidence"
FOCUS = "call_result_property_bindings"
PLAIN = "const { refetch } = useUser();"
CASES = {
    "plain": PLAIN,
    "renamed": PLAIN.replace("refetch }", "refetch: refetchUser }"),
    "same_rename": PLAIN.replace("refetch }", "refetch: refetch }"),
    "alias": PLAIN.replace("useUser()", "getUser()"),
    "unicode": PLAIN.replace("refetch }", "refetch: çağrı }"),
    "multiple_keys": PLAIN.replace("refetch }", "refetch, data: userData }"),
    "shadow_nested": PLAIN + " function child(refetch: any) { refetch = other; refetch(); }",
    "invocation_not_joined": PLAIN + " refetch();",
    "let": PLAIN.replace("const", "let"),
    "multi_declaration": PLAIN.replace("();", "(), other = 1;"),
    "default": PLAIN.replace("refetch }", "refetch = fallback }"),
    "computed": PLAIN.replace("refetch }", "[key]: refetch }"),
    "quoted": PLAIN.replace("refetch }", "'refetch': refetch }"),
    "nested": PLAIN.replace("refetch }", "user: { refetch } }"),
    "rest": PLAIN.replace("refetch }", "refetch, ...rest }"),
    "duplicate_key": PLAIN.replace("refetch }", "refetch, refetch: other }"),
    "duplicate_local": PLAIN.replace("refetch }", "refetch, other: refetch }"),
    "optional": PLAIN.replace("useUser()", "useUser?.()"),
    "generic": PLAIN.replace("useUser()", "useUser<any>()"),
    "awaited": PLAIN.replace("useUser()", "await useUser()"),
    "parenthesized": PLAIN.replace("useUser()", "(useUser())"),
    "property_call": PLAIN.replace("useUser()", "Hooks.useUser()"),
    "default_import": PLAIN.replace("useUser()", "defaultUser()"),
    "type_clause": PLAIN.replace("useUser()", "typeUser()"),
    "type_specifier": PLAIN.replace("useUser()", "typeSpecifier()"),
    "escaped_import": PLAIN.replace("useUser()", "escapedUser()"),
    "local_alias": "const local = useUser; " + PLAIN.replace("useUser()", "local()"),
    "branch": "if (ready) { " + PLAIN + " }",
    "deferred": "later(() => { " + PLAIN + " });",
    "reassign": PLAIN + " refetch = other;",
    "array_write": PLAIN + " [refetch] = values;",
    "object_write": PLAIN + " ({ refetch } = other);",
    "nested_write": PLAIN + " function child() { refetch = other; }",
    "default_write": PLAIN + " function child(value = (refetch = other)) {}",
    "import_write": PLAIN + " useUser = other;",
    "duplicate_binding": PLAIN + " const refetch = other;",
    "one_of_multiple_written": "const { refetch, data } = useUser(); data = other;",
    "escaped_binding": PLAIN.replace("refetch", r"ref\u0065tch"),
}
POSITIVES = {"plain", "renamed", "same_rename", "alias", "unicode", "multiple_keys",
             "shadow_nested", "invocation_not_joined", "arrow"}


def _native(path):
    node = shutil.which("node")
    assert node, "Native contract requires the configured Node runtime"
    run = subprocess.run([node, str(ROOT / "tools/engines/ast_sequencer.cjs"), str(path)],
                         cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=40)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


@pytest.fixture(scope="module")
def binding_inputs(tmp_path_factory):
    declarations = [f"export function Case_{name}() {{ {body} }}" for name, body in CASES.items()]
    declarations += [
        f"export const Case_arrow = () => {{ {PLAIN} }};",
        f"export function Case_shadow(useUser: any) {{ {PLAIN} }}",
        f"export function Case_parameter_collision(refetch: any) {{ {PLAIN} }}",
        f"export async function Case_async() {{ {PLAIN} }}",
        f"export function* Case_generator() {{ {PLAIN} }}",
        "export function olderLanes() { const first = () => {}; const second = () => { first(); };"
        " const cb = useCallback(() => {}, []); return { cb }; }",
        "export function jsxLane() { const done = () => {}; return <Child onDone={done} />; }",
        # Imports after owners are valid; use the shared native identity, not a second parser.
        "import { useUser, useUser as getUser } from './users';",
        "import type { useUser as typeUser } from './users';",
        "import { type useUser as typeSpecifier } from './users';",
        "import * as Hooks from './users'; import defaultUser from './users';",
        r"import { useUser as escapedUser } from './us\u0065rs';",
        "import { useCallback } from 'react'; import { Child } from './child';",
    ]
    source = "\ufeff// \U0001f680 CRLF byte identity\r\n" + "\r\n".join(declarations)
    path = tmp_path_factory.mktemp("call-result-binding") / "Cases.tsx"
    path.write_bytes(source.encode("utf-8"))
    raw = _native(path)
    rows = {row["name"]: row for row in _normalize_polyglot_symbols(raw, source, "typescript")}
    return source, raw, rows


@pytest.mark.parametrize("name", sorted(POSITIVES))
def test_native_positive(binding_inputs, name):
    source, _, rows = binding_inputs
    lane = rows["Case_" + name][KEY]
    assert lane["status"] == "observed", lane
    assert lane["omitted"] == 0 and len(lane["records"]) == (2 if name == "multiple_keys" else 1)
    assert lane["source_hash"] == hashlib.sha256(source.encode("utf-8")).hexdigest()
    assert lane["ast_contract_version"] == AST_CONTRACT_VERSION
    row = lane["records"][0]
    assert len(row["spans"]) == 14 and row["source"] == "./users"
    for role, text in (("property_name", row["property_name"]), ("local_binding", row["local_name"]),
                       ("callee", row["localName"]), ("imported_binding", row["importedName"])):
        span = row["spans"][role]
        assert source.encode("utf-8")[span["start"]:span["end"]].decode("utf-8") == text
    assert lane["runtime_execution"] == "not_established"
    assert "calls" not in row and "target" not in row


@pytest.mark.parametrize("name", sorted(set(CASES) - POSITIVES) + ["shadow", "parameter_collision", "async", "generator"])
def test_native_negative(binding_inputs, name):
    lane = binding_inputs[2]["Case_" + name][KEY]
    assert lane["records"] == [], lane


@pytest.mark.parametrize("damage", ["hash", "version", "authority", "offset", "bool_span", "lines",
                                  "missing_role", "extra_role", "order", "duplicate", "status",
                                  "omitted", "parser", "imports", "token"])
def test_normalizer_refuses_damage(binding_inputs, damage):
    source, original, _ = binding_inputs
    raw = deepcopy(original)
    owner = next(row for row in raw if row["name"] == "Case_plain")
    lane = owner[KEY]
    if damage == "hash": lane["source_hash"] = "0" * 64
    elif damage == "version": lane["version"] = "old"
    elif damage == "authority": lane["runtime_execution"] = "proven"
    elif damage == "offset": lane["offset_unit"] = "utf8_bytes"
    elif damage == "bool_span": lane["records"][0]["spans"]["local_binding"]["start"] = True
    elif damage == "lines": lane["records"][0]["spans"]["local_binding"]["line"] += 1
    elif damage == "missing_role": lane["records"][0]["spans"].pop("pattern")
    elif damage == "extra_role": lane["records"][0]["spans"]["other"] = lane["records"][0]["spans"]["callee"]
    elif damage == "order": lane["records"][0]["spans"]["callee"] = lane["records"][0]["spans"]["local_binding"]
    elif damage == "duplicate": lane["records"].append(deepcopy(lane["records"][0]))
    elif damage == "status": lane["status"] = []
    elif damage == "omitted": lane["omitted"] = -1
    elif damage == "parser": owner["parserKind"] = "fallback"
    elif damage == "imports": next(row for row in raw if row["name"] == "__file_meta__")["directImportBindings"] = []
    elif damage == "token": lane["records"][0]["local_name"] = "other"
    result = next(row for row in _normalize_polyglot_symbols(raw, source, "typescript")
                  if row["name"] == "Case_plain")[KEY]
    assert result["status"] == "unavailable" and result["records"] == []


def test_one_shared_pass_and_unchanged_siblings(binding_inputs, monkeypatch):
    from tools.engines import generate_atlas
    source, raw, _ = binding_inputs
    original, calls = generate_atlas._utf16_offsets_to_utf8_byte_offsets, []
    def track(content, offsets):
        calls.append(1)
        return original(content, offsets)
    monkeypatch.setattr(generate_atlas, "_utf16_offsets_to_utf8_byte_offsets", track)
    rows = {row["name"]: row for row in generate_atlas._normalize_polyglot_symbols(raw, source, "typescript")}
    assert len(calls) == 1 and rows["Case_unicode"][KEY]["records"]
    assert len(rows["olderLanes"]["local_arrow_sibling_call_evidence"]["records"]) == 1
    assert len(rows["olderLanes"]["returned_local_callback_evidence"]["records"]) == 1
    assert len(rows["jsxLane"]["jsx_prop_callback_evidence"]["records"]) == 1
    assert rows["Case_plain"]["import_call_evidence"]["calls"]


@pytest.mark.parametrize("damage", ["none", "missing", "hash", "ast", "parser", "authority", "bool_span",
                                  "outer", "duplicate", "omitted", "incomplete", "budget", "roles"])
def test_focused_integrity(binding_inputs, damage):
    from tools.core.state_flow import _focus_call_result_property_bindings
    source, _, original = binding_inputs
    row = deepcopy(original["Case_plain"])
    record = {"hash": hashlib.sha256(source.encode("utf-8")).hexdigest(), "size": len(source.encode("utf-8")),
              "loc": len(source.splitlines()), "parser_evidence": {"status": "observed"},
              "ast_contract_version": AST_CONTRACT_VERSION}
    lane = row[KEY]
    if damage == "missing": row.pop(KEY)
    elif damage == "hash": lane["source_hash"] = "0" * 64
    elif damage == "ast": record["ast_contract_version"] = "old"
    elif damage == "parser": record["parser_evidence"]["status"] = "degraded"
    elif damage == "authority": lane["callability"] = "proven"
    elif damage == "bool_span": lane["records"][0]["spans"]["owner"]["end"] = True
    elif damage == "outer": row["start"] += 1
    elif damage == "duplicate": lane["records"].append(deepcopy(lane["records"][0]))
    elif damage == "omitted": lane["omitted"] = -1
    elif damage == "incomplete": lane.update(status="incomplete_scan", omitted=1)
    elif damage == "roles": lane["records"][0]["spans"].pop("callee")
    result = _focus_call_result_property_bindings(record, row, 3, 0 if damage == "budget" else 64)
    if damage == "none": assert result["status"] == "observed" and result["returned"] == 1
    else: assert result["status"] in {"unavailable", "incomplete_scan"} and result["items"] == []
    for field in ("value_transfer", "runtime_execution", "callability", "callback_activation", "schema_validity"):
        assert result[field] == "not_established"


@pytest.mark.parametrize("damage", ["none", "authority", "extra", "role", "bool_span", "kind"])
def test_schema_guard(binding_inputs, tmp_path, damage):
    from tools.core.artifact_validator import validate_against_schema
    schema = json.loads((ROOT / "config/schemas/atlas.schema.json").read_text(encoding="utf-8"))
    path = tmp_path / "schema.json"
    path.write_text(json.dumps({"$defs": schema["$defs"], "$ref": "#/$defs/" + KEY}), encoding="utf-8")
    lane = deepcopy(binding_inputs[2]["Case_plain"][KEY])
    if damage == "authority": lane["runtime_execution"] = "proven"
    elif damage == "extra": lane["callability"] = "proven"
    elif damage == "role": lane["records"][0]["spans"].pop("pattern")
    elif damage == "bool_span": lane["records"][0]["spans"]["local_binding"]["start"] = True
    elif damage == "kind": lane["records"][0]["kind"] = "namespace"
    assert (not validate_against_schema(path, "binding", lane)) == (damage == "none")


@pytest.mark.parametrize("case", ["row_cap", "scan_cap", "parse", "ambiguous"])
def test_native_failure_and_caps(tmp_path, case):
    if case == "row_cap":
        body = "const { " + ", ".join(f"key{i}" for i in range(65)) + " } = useUser();"
    elif case == "scan_cap":
        body = "const padding = [" + ",".join("1" for _ in range(10001)) + "]; " + PLAIN
    else: body = PLAIN + (" const broken = ;" if case == "parse" else "")
    source = "import { useUser } from './users'; "
    if case == "ambiguous": source += "import { useUser } from './another'; "
    source += "export function owner() { " + body + " }"
    path = tmp_path / "Caps.ts"
    path.write_text(source, encoding="utf-8")
    lane = next(row for row in _native(path) if row["name"] == "owner")[KEY]
    if case == "row_cap": assert lane["status"] == "incomplete_scan" and len(lane["records"]) == 64 and lane["omitted"] == 1
    else: assert lane["records"] == [] and (case == "ambiguous" or lane["status"] == "unavailable")


@pytest.fixture(scope="module")
def binding_atlas_run(tmp_path_factory):
    from tools.core.atlas_io import load_atlas_data
    from tools.core.external_target_generation import external_target_output_slug
    target = tmp_path_factory.mktemp("binding-atlas")
    source = """import { useCallback } from 'react';
import { useUser } from './users';
export function owner() {
  const { refetch: refetchUser, data } = useUser();
  const cb = useCallback(() => {}, []);
  return { cb };
}
throw new Error('Target source must never execute');
"""
    (target / "Panel.ts").write_bytes(("\ufeff// \U0001f680\r\n" + source.replace("\n", "\r\n")).encode("utf-8"))
    env = dict(os.environ)
    for key in ("CODEMAPS_TARGET_PREFLIGHT_RECEIPT", "CODEMAPS_TARGET_PREFLIGHT_RECEIPT_SHA256",
                "CODEMAPS_TARGET_PREFLIGHT_REUSE_MODE", "CODEMAPS_TARGET_PROJECTS"):
        env.pop(key, None)
    env["CODEMAPS_TARGET_ROOT"] = str(target)
    run = subprocess.run([sys.executable, "-B", "-m", "tools.engines.generate_atlas"], cwd=ROOT,
                         env=env, capture_output=True, text=True, encoding="utf-8", timeout=90)
    assert run.returncode == 0, run.stderr or run.stdout
    raw = ROOT / "output/external_targets" / external_target_output_slug(str(target)) / ".raw"
    return target, raw, load_atlas_data(raw)


def test_real_sqlite_mcp_cold_warm_and_item_cap(binding_atlas_run, record_property):
    from tools.mcp import server
    target, _, atlas = binding_atlas_run
    owner = next(row for row in atlas["MAIN"]["files"]["Panel.ts"]["symbols"] if row["name"] == "owner")
    assert len(owner[KEY]["records"]) == 2
    server._ATLAS_CACHE.clear()
    cold = json.loads(server.get_state_flow(file="Panel.ts", symbol="owner", target_root=str(target), format="json", max_items=1))
    warm = json.loads(server.get_state_flow(file="Panel.ts", symbol="owner", target_root=str(target), format="json", max_items=1))
    assert cold == warm
    record_property("observed_focused_state_flow", json.dumps(cold, ensure_ascii=False))
    lane = cold["symbol_context"][FOCUS]
    assert lane["status"] == "observed" and lane["returned"] == lane["omitted"] == 1
    assert lane["source_binding"] == "snapshot_and_live_match"
    assert lane["items"][0]["local_name"] == "refetchUser"
    assert "target" not in lane["items"][0]
    assert FOCUS in server.get_state_flow(file="Panel.ts", symbol="owner", target_root=str(target), max_items=1)


def test_packet_omission_preserves_older_lanes(binding_atlas_run, monkeypatch):
    from tools.mcp import server
    from tools.core.agent_packet_budget import context_budget_profile
    target, _, _ = binding_atlas_run
    baseline = json.loads(server.get_state_flow(file="Panel.ts", symbol="owner", target_root=str(target), format="json"))
    # Keep exactly the preceding settled lanes, including their omissions;
    # later additive metadata must not inflate this lane's test allowance.
    keys = list(baseline["symbol_context"])
    for key in keys[keys.index(FOCUS):]:
        baseline["symbol_context"].pop(key)
    budget = context_budget_profile(json.dumps(baseline, ensure_ascii=False))["estimated_tokens"]
    monkeypatch.setattr(server, "DEFAULT_AGENT_PACKET_BUDGET_TOKENS", budget)
    limited = json.loads(server.get_state_flow(file="Panel.ts", symbol="owner", target_root=str(target), format="json"))
    assert FOCUS not in limited["symbol_context"]
    assert limited["symbol_context"] == baseline["symbol_context"]
    assert context_budget_profile(json.dumps(limited, ensure_ascii=False), budget_tokens=budget)["status"] == "pass"
    assert "binding" in limited["next_action"] and "absence" in limited["next_action"]


@pytest.mark.parametrize("damage", ["none", "legacy", "source_drift", "corrupt_primary", "poison_cache", "scan_budget"])
def test_real_primary_source_refusal(binding_atlas_run, tmp_path, monkeypatch, damage):
    from tools.core.artifact_store import ArtifactStore
    from tools.mcp import server
    target, _, original = binding_atlas_run
    local = tmp_path / "target"
    local.mkdir()
    (local / "Panel.ts").write_bytes((target / "Panel.ts").read_bytes())
    atlas = deepcopy(original)
    atlas["MAIN"]["root_path"] = str(local)
    if damage == "legacy":
        for row in atlas["MAIN"]["files"]["Panel.ts"]["symbols"]: row.pop(KEY, None)
    raw = tmp_path / ".raw"
    store = ArtifactStore(raw_dir=raw)
    store.use_sqlite, store.backend = True, "hybrid_sqlite"
    store._ensure_schema()
    store.state_payload_inline_limit_bytes = 1
    store._save_payload_to_state_table("atlas", atlas)
    store._save_atlas_to_sqlite(atlas)
    with sqlite3.connect(raw / "codemaps.db") as connection:
        connection.execute("UPDATE projects SET path='.' WHERE project_key='MAIN'")
        if damage == "corrupt_primary": connection.execute("DELETE FROM state_payload_parts WHERE name='atlas'")
    (raw / "atlas.json").write_text(json.dumps(original), encoding="utf-8")
    if damage == "source_drift": (local / "Panel.ts").write_bytes((local / "Panel.ts").read_bytes() + b"\n// drift")
    if damage == "scan_budget":
        policy = dict(server._state_flow_brief_policy())
        policy["focus_scan_limit"] = 7
        monkeypatch.setattr(server, "_state_flow_brief_policy", lambda: policy)
    server._ATLAS_CACHE.clear()
    monkeypatch.setattr(server, "_raw_dir_for_target", lambda root: raw)
    monkeypatch.setattr(server, "_analysis_root_display", lambda root: str(local))
    if damage == "poison_cache":
        cached = server._atlas_read(raw).data
        next(row for row in cached["MAIN"]["files"]["Panel.ts"]["symbols"] if row["name"] == "owner")[KEY]["records"][0]["local_name"] = "poison"
    result = json.loads(server.get_state_flow(file="Panel.ts", symbol="owner", target_root=str(local), format="json"))
    lane = result.get("symbol_context", {}).get(FOCUS)
    if damage in {"none", "poison_cache"}:
        assert lane["status"] == "observed" and lane["returned"] == 2 and lane["items"][0]["local_name"] == "refetchUser"
    elif damage == "legacy": assert lane is None
    else: assert lane is None or (lane["status"] in {"unavailable", "incomplete_scan"} and lane["returned"] == 0)
    if damage == "scan_budget": assert result["visited_records"] <= 7
