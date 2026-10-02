"""Explicit rehydrate calls are source candidates, never hydration execution."""
from __future__ import annotations

import copy
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from tools.core.state_flow import project_state_flow_focus
from tools.core.path_engine import resolve_project_import
from tools.core.polyglot_imports import extract_typescript_import_evidence
from tools.engines.generate_atlas import _normalize_polyglot_symbols


def _parse(tmp_path: Path, source: str, filename: str = "store.ts",
           include_imports: bool = False):
    node = shutil.which("node")
    assert node, "Node is required for the parser contract"
    root = Path(__file__).resolve().parents[2]
    path = tmp_path / filename
    path.write_text(source, encoding="utf-8", newline="")
    parsed = subprocess.run(
        [node, str(root / "tools/engines/ast_sequencer.cjs"), str(path)],
        cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert parsed.returncode == 0, parsed.stderr
    raw = json.loads(parsed.stdout)
    assert next(row for row in raw if row["name"] == "__file_meta__")["parserStatus"] == "observed"
    symbols = _normalize_polyglot_symbols(raw, source, language="typescript")
    if include_imports:
        return symbols, extract_typescript_import_evidence(raw)["records"]
    return symbols


def test_cross_file_named_import_rehydrate_is_bounded_source_candidate(tmp_path):
    store_source = '''import { create } from "zustand";
import { persist } from "zustand/middleware";
export const useStore = create(persist((set) => ({
  update: () => set({ready: true}),
}), {name: "demo", skipHydration: true}));
'''
    caller_source = '''import { useStore as chosen } from "./store";
import { useStore as wrong } from "./other-store";
import type { useStore as TypeOnly } from "./store";
export function bootstrap() { chosen.persist.rehydrate(); wrong.persist.rehydrate(); }
export function bootstrapAlias() {
  const resume = chosen.persist.rehydrate;
  resume();
}
export function shadow(chosen: any) { chosen.persist.rehydrate(); }
export function decoys() {
  TypeOnly.persist.rehydrate();
  chosen?.persist.rehydrate();
  chosen.persist?.rehydrate();
  chosen.persist.rehydrate?.();
  chosen["persist"].rehydrate();
  chosen.persist.rehydrate(1);
  setTimeout(() => chosen.persist.rehydrate(), 0);
}
'''
    other_source = "export const useStore = {};\n"
    store_symbols = _parse(tmp_path, store_source)
    caller_symbols, caller_imports = _parse(
        tmp_path, caller_source, "caller.ts", include_imports=True)
    _parse(tmp_path, other_source, "other-store.ts")
    evidence = next(row for row in caller_symbols if row["name"] == "bootstrap")[
        "same_file_store_action_call_evidence"]
    assert evidence["imported_rehydrate_calls"] == [{
        "store": "chosen", "module_source": "./store", "imported_store": "useStore",
        "line": 4, "end_line": 4,
    }, {
        "store": "wrong", "module_source": "./other-store", "imported_store": "useStore",
        "line": 4, "end_line": 4,
    }]
    alias_evidence = next(row for row in caller_symbols if row["name"] == "bootstrapAlias")[
        "same_file_store_action_call_evidence"]
    assert alias_evidence["imported_rehydrate_calls"] == [{
        "store": "chosen", "module_source": "./store", "imported_store": "useStore",
        "line": 7, "end_line": 7, "call_form": "const_local_alias",
        "alias_name": "resume",
    }]
    imports = [dict(entry, raw_source=entry["source"], source=resolve_project_import(
        entry["source"], str(tmp_path), str(tmp_path), str(tmp_path)))
        for entry in caller_imports]
    def file_record(source, symbols, import_records=None):
        return {"workspace_rel": "store.ts" if symbols is store_symbols else "caller.ts",
                "hash": hashlib.sha256(source.encode()).hexdigest(),
                "language": "typescript", "size": len(source.encode()),
                "symbols": symbols, "import_records": import_records or []}
    atlas = {"MAIN": {"root_path": str(tmp_path), "project_type": "typescript", "files": {
        "store.ts": file_record(store_source, store_symbols),
        "caller.ts": file_record(caller_source, caller_symbols, imports),
        "other-store.ts": {"workspace_rel": "other-store.ts",
                           "hash": hashlib.sha256(other_source.encode()).hexdigest(),
                           "symbols": [], "import_records": []},
    }}}
    def focus(data=atlas):
        return project_state_flow_focus(data, project="MAIN", file="store.ts",
                                        symbol="useStore.update", max_items=5,
                                        scan_limit=1000)["cross_file_rehydrate_call_candidates"]
    current = focus()
    assert current["status"] == "observed"
    assert current["returned"] == 3 and current["omitted"] == 0
    assert current["items"][0]["caller_symbol"] == "bootstrap"
    assert current["items"][0]["local_store"] == "chosen"
    assert current["items"][1]["caller_symbol"] == "bootstrapAlias"
    assert current["items"][1]["call_form"] == "const_local_alias"
    assert current["items"][2]["caller_symbol"] == "decoys"
    assert current["items"][2]["call_context"] == "inline_callback"
    assert current["runtime_execution"] == "not_established"
    from tools.core.artifact_validator import _validate
    root = Path(__file__).resolve().parents[2]
    schema = json.loads((root / "config/schemas/atlas.schema.json").read_text(encoding="utf-8"))
    errors = []
    _validate(evidence, schema["$defs"]["same_file_store_action_call_evidence"],
              schema, ["imported_rehydrate"], errors)
    _validate(alias_evidence, schema["$defs"]["same_file_store_action_call_evidence"],
              schema, ["imported_rehydrate_alias"], errors)
    assert not errors
    wrong_module = copy.deepcopy(atlas)
    wrong_module["MAIN"]["files"]["caller.ts"]["import_records"][0]["source"] = "other-store.ts"
    assert focus(wrong_module)["items"] == []
    duplicate = copy.deepcopy(atlas)
    duplicate["MAIN"]["files"]["caller.ts"]["import_records"].append(imports[0].copy())
    assert focus(duplicate)["status"] == "ambiguous"
    capped = copy.deepcopy(atlas)
    next(row for row in capped["MAIN"]["files"]["caller.ts"]["symbols"]
         if row["name"] == "bootstrap")["same_file_store_action_call_evidence"][
             "imported_rehydrate_omitted"] = 1
    assert focus(capped)["status"] == "incomplete_scan"
    assert focus(capped)["omitted"] is None
    legacy = copy.deepcopy(atlas)
    next(row for row in legacy["MAIN"]["files"]["caller.ts"]["symbols"]
         if row["name"] == "bootstrap")["same_file_store_action_call_evidence"].pop(
             "imported_rehydrate_calls")
    assert focus(legacy)["status"] == "unavailable"
    forged = copy.deepcopy(atlas)
    next(row for row in forged["MAIN"]["files"]["caller.ts"]["symbols"]
         if row["name"] == "bootstrap")["same_file_store_action_call_evidence"][
             "imported_rehydrate_calls"][0]["line"] = -1
    assert focus(forged)["status"] == "unavailable"
    forged_null_form = copy.deepcopy(atlas)
    next(row for row in forged_null_form["MAIN"]["files"]["caller.ts"]["symbols"]
         if row["name"] == "bootstrap")["same_file_store_action_call_evidence"][
             "imported_rehydrate_calls"][0]["call_form"] = None
    assert focus(forged_null_form)["status"] == "unavailable"
    forged_context = copy.deepcopy(atlas)
    next(row for row in forged_context["MAIN"]["files"]["caller.ts"]["symbols"]
         if row["name"] == "decoys")["same_file_store_action_call_evidence"][
             "imported_rehydrate_calls"][0]["call_context"] = "executed"
    assert focus(forged_context)["status"] == "unavailable"
    assert focus()["source_binding"] == "not_checked"


def _focus(symbols: list[dict], symbol: str, *, scan_limit: int = 100) -> dict:
    atlas = {"MAIN": {"root_path": ".", "files": {
        "store.ts": {"hash": "fixture", "language": "typescript", "symbols": symbols},
    }}}
    return project_state_flow_focus(
        atlas, project="MAIN", file="store.ts", symbol=symbol,
        max_items=5, scan_limit=scan_limit,
    )


def test_const_local_rehydrate_alias_is_source_candidate_not_execution(tmp_path):
    source = '''import { create } from "zustand";
import { persist } from "zustand/middleware";
export const useStore = create(persist((set) => ({
  update: () => set({ready: true}),
}), {name: "demo", skipHydration: true}));
export function bootstrap() {
  const resume = useStore.persist.rehydrate;
  resume();
  resume(1);
  resume?.();
  let mutable = useStore.persist.rehydrate;
  mutable();
  const computed = useStore.persist["rehydrate"];
  computed();
  const optional = useStore?.persist.rehydrate;
  optional();
  late();
  const late = useStore.persist.rehydrate;
  { const resume = () => 1; resume(); }
  setTimeout(() => resume(), 0);
}
'''
    symbols = _parse(tmp_path, source)
    caller = next(row for row in symbols if row["name"] == "bootstrap")
    evidence = caller["same_file_store_action_call_evidence"]
    assert evidence["rehydrate_calls"] == [{
        "store": "useStore",
        "store_start": next(row for row in symbols if row["name"] == "useStore")["start"],
        "line": 8, "end_line": 8,
        "call_form": "const_local_alias",
        "alias_name": "resume",
    }]
    result = _focus(symbols, "useStore.update")["rehydrate_call_candidates"]
    assert result["status"] == "observed"
    assert result["returned"] == 1
    assert result["items"][0]["call_form"] == "const_local_alias"
    assert result["runtime_execution"] == "not_established"
    from tools.core.artifact_validator import _validate
    root = Path(__file__).resolve().parents[2]
    schema = json.loads((root / "config/schemas/atlas.schema.json").read_text(encoding="utf-8"))
    errors = []
    _validate(evidence, schema["$defs"]["same_file_store_action_call_evidence"],
              schema, ["rehydrate_alias"], errors)
    assert not errors
    forged = copy.deepcopy(symbols)
    next(row for row in forged if row["name"] == "bootstrap")[
        "same_file_store_action_call_evidence"]["rehydrate_calls"][0]["alias_name"] = ""
    assert _focus(forged, "useStore.update")["rehydrate_call_candidates"]["status"] == "unavailable"
    forged_null_form = copy.deepcopy(symbols)
    next(row for row in forged_null_form if row["name"] == "bootstrap")[
        "same_file_store_action_call_evidence"]["rehydrate_calls"][0]["call_form"] = None
    assert _focus(forged_null_form, "useStore.update")["rehydrate_call_candidates"][
        "status"] == "unavailable"


def test_same_file_checker_bound_rehydrate_call_is_selected_store_source_candidate(tmp_path):
    from tools.core.artifact_validator import _validate

    source = '''import { create } from "zustand";
import { persist } from "zustand/middleware";
export const useStore = create(persist((set) => ({
  update: () => set({ready: true}),
}), {name: "demo", skipHydration: true}));
export const plain = create((set) => ({update: () => set({ready: true})}));
export function bootstrap() { useStore.persist.rehydrate(); }
export function fake(useStore: any) { useStore.persist.rehydrate(); }
export function decoys() {
  plain.persist.rehydrate();
  useStore?.persist.rehydrate();
  useStore.persist?.rehydrate();
  useStore.persist.rehydrate?.();
  useStore["persist"].rehydrate();
  useStore.persist["rehydrate"]();
  useStore.persist.rehydrate(42);
  setTimeout(() => useStore.persist.rehydrate(), 0);
}
'''
    symbols = _parse(tmp_path, source)
    caller = next(row for row in symbols if row["name"] == "bootstrap")
    evidence = caller["same_file_store_action_call_evidence"]
    root = Path(__file__).resolve().parents[2]
    schema = json.loads((root / "config/schemas/atlas.schema.json").read_text(encoding="utf-8"))
    evidence_schema = schema["$defs"]["same_file_store_action_call_evidence"]
    errors = []
    _validate(evidence, evidence_schema, schema, ["rehydrate"], errors)
    assert not errors
    assert evidence["rehydrate_calls"][0]["store"] == "useStore"
    assert evidence["rehydrate_calls"][0]["store_start"] == next(
        row["start"] for row in symbols if row["name"] == "useStore")
    selected = _focus(symbols, "useStore.update")
    calls = selected["rehydrate_call_candidates"]
    assert calls["status"] == "observed"
    assert [row["caller_symbol"] for row in calls["items"]] == ["bootstrap", "decoys"]
    assert calls["items"][1]["call_context"] == "inline_callback"
    assert calls["runtime_execution"] == "not_established"
    assert calls["source_binding"] == "not_checked"
    assert not _focus(symbols, "plain.update")["rehydrate_call_candidates"]["items"]
    forged = copy.deepcopy(symbols)
    next(row for row in forged if row["name"] == "bootstrap")[
        "same_file_store_action_call_evidence"]["rehydrate_calls"][0]["store_start"] = -1
    errors = []
    _validate(next(row for row in forged if row["name"] == "bootstrap")[
        "same_file_store_action_call_evidence"], evidence_schema, schema, ["rehydrate"], errors)
    assert errors
    rejected = _focus(forged, "useStore.update")["rehydrate_call_candidates"]
    assert rejected["status"] == "unavailable" and rejected["items"] == []
    legacy = copy.deepcopy(symbols)
    next(row for row in legacy if row["name"] == "bootstrap")[
        "same_file_store_action_call_evidence"].pop("rehydrate_calls")
    assert _focus(legacy, "useStore.update")["rehydrate_call_candidates"]["status"] == "unavailable"


def test_public_focus_keeps_rehydrate_caller_and_store_source_binding_separate(monkeypatch, tmp_path):
    from tools.core import artifact_store
    from tools.core.artifact_store import ArtifactStore
    from tools.core.db import SQLiteManager
    from tools.mcp import server

    source = '''import { create } from "zustand";
import { persist } from "zustand/middleware";
export const useStore = create(persist((set) => ({
  update: () => set({ready: true}),
}), {name: "demo", skipHydration: true}));
export function bootstrap() { useStore.persist.rehydrate(); }
export function bootstrapAlias() {
  const resume = useStore.persist.rehydrate;
  resume();
}
export function bootstrapCallback() {
  useEffect(() => useStore.persist.rehydrate(), []);
}
'''
    symbols = _parse(tmp_path, source)
    caller_source = '''import { useStore as chosen } from "./store";
export function externalBootstrap() { chosen.persist.rehydrate(); }
export function externalAlias() {
  const resume = chosen.persist.rehydrate;
  resume();
}
export function externalCallback() {
  useEffect(() => chosen.persist.rehydrate(), []);
}
'''
    caller_symbols, caller_imports = _parse(
        tmp_path, caller_source, "caller.ts", include_imports=True)
    imports = [dict(entry, raw_source=entry["source"], source=resolve_project_import(
        entry["source"], str(tmp_path), str(tmp_path), str(tmp_path)))
        for entry in caller_imports]
    atlas = {"MAIN": {"root_path": ".", "project_type": "typescript", "files": {
        "store.ts": {
            "workspace_rel": "store.ts", "hash": hashlib.sha256(source.encode()).hexdigest(),
            "language": "typescript", "size": len(source.encode()), "symbols": symbols,
        },
        "caller.ts": {
            "workspace_rel": "caller.ts", "hash": hashlib.sha256(caller_source.encode()).hexdigest(),
            "language": "typescript", "size": len(caller_source.encode()),
            "symbols": caller_symbols,
            "import_records": imports,
        },
    }}}
    monkeypatch.setattr(artifact_store, "ROOT", tmp_path)
    raw_dir = tmp_path / ".raw"
    raw_dir.mkdir()
    store = ArtifactStore(raw_dir=raw_dir)
    store.use_sqlite = True
    store.backend = "hybrid_sqlite"
    store.db_manager = SQLiteManager(raw_dir / "codemaps.db")
    store._schema_initialized = False
    store._ensure_schema()
    store.save_raw("atlas", atlas)
    monkeypatch.setattr(server, "_raw_dir_for_target", lambda _target: raw_dir)
    monkeypatch.setattr(server, "_analysis_root_display", lambda _target="": str(tmp_path))

    def query():
        return json.loads(server.get_state_flow(
            file="store.ts", symbol="useStore.update",
            target_root=str(tmp_path), format="json",
        ))

    current = query()
    lane = current["rehydrate_call_candidates"]
    assert lane["status"] == "observed" and lane["returned"] == 3
    assert lane["source_binding"] == "snapshot_and_live_match"
    assert lane["items"][0]["endpoint_content_binding"] == "both_snapshot_and_live_match"
    assert lane["items"][0]["caller_symbol"] == "bootstrap"
    assert lane["items"][1]["caller_symbol"] == "bootstrapAlias"
    assert lane["items"][1]["call_form"] == "const_local_alias"
    assert lane["items"][1]["endpoint_content_binding"] == "both_snapshot_and_live_match"
    assert lane["items"][2]["caller_symbol"] == "bootstrapCallback"
    assert lane["items"][2]["call_context"] == "inline_callback"
    assert lane["items"][2]["endpoint_content_binding"] == "both_snapshot_and_live_match"
    assert lane["runtime_execution"] == "not_established"
    external = current["cross_file_rehydrate_call_candidates"]
    assert external["status"] == "observed" and external["returned"] == 3
    assert external["items"][0]["caller_symbol"] == "externalBootstrap"
    assert external["items"][1]["caller_symbol"] == "externalAlias"
    assert external["items"][1]["call_form"] == "const_local_alias"
    assert external["items"][0]["endpoint_content_binding"] == "both_snapshot_and_live_match"
    assert external["items"][2]["caller_symbol"] == "externalCallback"
    assert external["items"][2]["call_context"] == "inline_callback"
    assert external["items"][2]["endpoint_content_binding"] == "both_snapshot_and_live_match"
    brief = server.get_state_flow(
        file="store.ts", symbol="useStore.update",
        target_root=str(tmp_path), format="brief",
    )
    assert "cross_file_rehydrate_call_candidates" in brief
    assert "const_local_alias" in brief
    assert "inline_callback" in brief
    (tmp_path / "caller.ts").write_text("// caller drift\n", encoding="utf-8")
    drifted_caller = query()["cross_file_rehydrate_call_candidates"]
    assert drifted_caller["items"][0]["endpoint_content_binding"] == "not_currently_bound"
    (tmp_path / "caller.ts").write_text(caller_source, encoding="utf-8")
    (tmp_path / "store.ts").write_text("// drift\n", encoding="utf-8")
    drifted = query()["rehydrate_call_candidates"]
    assert drifted["items"][0]["endpoint_content_binding"] == "not_currently_bound"
    assert query()["cross_file_rehydrate_call_candidates"]["items"][0][
        "endpoint_content_binding"] == "not_currently_bound"


def test_rehydrate_producer_cap_and_shared_query_budget_do_not_claim_absence(tmp_path):
    calls = "\n".join(
        "  setTimeout(() => useStore.persist.rehydrate(), 0);"
        if index % 2 else "  useStore.persist.rehydrate();"
        for index in range(65))
    source = '''import { create } from "zustand";
import { persist } from "zustand/middleware";
export const useStore = create(persist((set) => ({
  update: () => set({ready: true}),
}), {name: "demo"}));
export function bootstrap() {
''' + calls + '''
}
'''
    symbols = _parse(tmp_path, source)
    evidence = next(row for row in symbols if row["name"] == "bootstrap")[
        "same_file_store_action_call_evidence"]
    assert len(evidence["rehydrate_calls"]) == 64
    assert evidence["rehydrate_omitted"] == 1
    assert any(call.get("call_context") == "inline_callback"
               for call in evidence["rehydrate_calls"])
    capped = _focus(symbols, "useStore.update", scan_limit=1000)["rehydrate_call_candidates"]
    assert capped["status"] == "incomplete_scan"
    assert capped["reason"] == "parser_rehydrate_source_cap"
    assert capped["returned"] == 5 and capped["omitted"] is None
    budgeted = _focus(symbols, "useStore.update", scan_limit=2)
    assert budgeted["status"] == "incomplete_search"


def test_imported_rehydrate_producer_cap_remains_explicit(tmp_path):
    source = ('import { useStore as chosen } from "./store";\n'
              'export function bootstrap() {\n'
              + '  chosen.persist.rehydrate();\n' * 65 + '}\n')
    symbols = _parse(tmp_path, source, "caller.ts")
    evidence = next(row for row in symbols if row["name"] == "bootstrap")[
        "same_file_store_action_call_evidence"]
    assert len(evidence["imported_rehydrate_calls"]) == 64
    assert evidence["imported_rehydrate_omitted"] == 1
    assert evidence["rehydrate_calls"] == []


def test_inline_callback_rehydrate_is_distinct_source_candidate(tmp_path):
    source = '''import { create } from "zustand";
import { persist } from "zustand/middleware";
export const useStore = create(persist((set) => ({
  update: () => set({ready: true}),
}), {name: "demo", skipHydration: true}));
export function bootstrap() {
  useEffect(() => { useStore.persist.rehydrate(); }, []);
  schedule(function () { useStore.persist.rehydrate(); });
  schedule(() => schedule(() => useStore.persist.rehydrate()));
  function later() { useStore.persist.rehydrate(); }
  schedule(() => { const useStore = {}; useStore.persist.rehydrate(); });
  schedule(() => useStore.persist.rehydrate?.());
}
'''
    symbols = _parse(tmp_path, source)
    evidence = next(row for row in symbols if row["name"] == "bootstrap")[
        "same_file_store_action_call_evidence"]
    assert [(call["line"], call.get("call_context")) for call in
            evidence["rehydrate_calls"]] == [
                (7, "inline_callback"), (8, "inline_callback")]
    from tools.core.artifact_validator import _validate
    root = Path(__file__).resolve().parents[2]
    schema = json.loads((root / "config/schemas/atlas.schema.json").read_text(encoding="utf-8"))
    errors = []
    _validate(evidence, schema["$defs"]["same_file_store_action_call_evidence"],
              schema, ["inline_callback"], errors)
    assert not errors
    focused = _focus(symbols, "useStore.update")["rehydrate_call_candidates"]
    assert focused["status"] == "observed"
    assert [(item["call_line"], item.get("call_context")) for item in focused["items"]
            if item["caller_symbol"] == "bootstrap"] == [
                (7, "inline_callback"), (8, "inline_callback")]
    assert any(item["caller_symbol"] == "later" and "call_context" not in item
               for item in focused["items"])
    assert focused["runtime_execution"] == "not_established"
    forged = copy.deepcopy(symbols)
    next(row for row in forged if row["name"] == "bootstrap")[
        "same_file_store_action_call_evidence"]["rehydrate_calls"][0][
            "call_context"] = "executed"
    assert _focus(forged, "useStore.update")["rehydrate_call_candidates"][
        "status"] == "unavailable"
