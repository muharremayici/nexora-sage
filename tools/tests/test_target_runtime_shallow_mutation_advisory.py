"""Bounded syntax evidence; no target-native persistence or ownership verdict."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tools.core.artifact_validator import validate_payload
from tools.core.json_io import load_json_file
from tools.core.external_target_generation import external_target_output_slug
from tools.engines import audit as audit_engine
from tools.engines.generate_atlas import AST_CONTRACT_VERSION, _file_parser_evidence, file_contract_is_current

ROOT = Path(__file__).resolve().parents[2]
PREFIX = "TypeScript:ShallowCopyNestedIndexWriteBeforeAwait:"
FIXTURE = json.loads((ROOT / "config/target_runtime_schema_mutation_advisory_fixtures.json").read_text(encoding="utf-8"))


def _source(body, *, arrow=False):
    declaration = (
        "export const update = async (base: { items: string[] }, other: { items: string[] }, value: string) => {"
        if arrow else
        "export async function update(base: { items: string[] }, other: { items: string[] }, value: string) {"
    )
    return "declare function persist(value: unknown): Promise<void>;\n" + declaration + "\n  " + body + "\n}\n"


def _analyze(source, path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available")
    path.write_text(source, encoding="utf-8")
    result = subprocess.run([node, str(ROOT / "tools/engines/ast_sequencer.cjs"), str(path)],
                            cwd=ROOT, capture_output=True, text=True, timeout=20, check=False)
    assert result.returncode == 0, result.stderr
    symbols = json.loads(result.stdout)
    meta = next(row for row in symbols if row["name"] == "__file_meta__")
    file_data = {"language": "typescript", "features": meta["features"],
                 "parser_evidence": _file_parser_evidence(symbols, "typescript"),
                 "loc": len(source.splitlines()), "hash": "source-bound-fixture",
                 "target_ref": f"MAIN::{path.name}"}
    return [value for value in meta["features"] if value.startswith(PREFIX)], file_data


@pytest.mark.parametrize("case", FIXTURE["shallow_mutation_cases"], ids=lambda case: case["id"])
def test_shallow_mutation_relation_uses_exact_syntax_and_contrary_controls(case, tmp_path):
    features, file_data = _analyze(_source(case["body"]), tmp_path / "store.ts")
    if case["expected"] == "advisory":
        assert len(features) == 1
        candidates = audit_engine._shallow_copy_nested_write_review_candidates("MAIN", "store.ts", file_data)
        assert len(candidates) == 1
        candidate = candidates[0]
        assert candidate["kind"] == "shallow_copy_nested_index_write_before_await"
        assert candidate["subject"] == "copy" and candidate["property"] == "items"
        assert candidate["guard_line"] < candidate["sink_line"] < candidate["await_line"]
        assert candidate["confidence"] == "needs_runtime_proof" and candidate["actionability"] == "review"
        assert "not a validation guard" in candidate["claim_boundary"]
        assert len(candidate["verification_questions"]) == 3
    else:
        assert features == []


def test_async_arrow_uses_lexical_copy_binding(tmp_path):
    body = FIXTURE["shallow_mutation_cases"][0]["body"].replace("copy", "draft").replace("items[0]", "items[1]")
    features, file_data = _analyze(_source(body, arrow=True), tmp_path / "arrow.ts")
    assert len(features) == 1
    candidates = audit_engine._shallow_copy_nested_write_review_candidates("MAIN", "arrow.ts", file_data)
    assert candidates[0]["subject"] == "draft"


@pytest.mark.parametrize("change", [
    {"hash": ""}, {"loc": 4}, {"language": "python"}, {"project_key": "OTHER"},
    {"atlas_rel_path": "other.ts"}, {"target_ref": "OTHER::store.ts"},
    {"parser_evidence": {"status": "degraded", "parser_kind": "typescript_compiler_api"}},
    {"parser_evidence": {"status": "observed", "parser_kind": "regex"}},
    {"features": [PREFIX + "copy:items:3:5:4"]},
    {"features": [PREFIX + "copy:items:3:3:5"]}, {"features": [42]},
])
def test_consumer_rejects_unbound_or_invalid_evidence(change):
    data = {"language": "typescript", "features": [PREFIX + "copy:items:3:4:5"],
            "parser_evidence": {"status": "observed", "parser_kind": "typescript_compiler_api"},
            "loc": 6, "hash": "bound"}
    assert len(audit_engine._shallow_copy_nested_write_review_candidates("MAIN", "store.ts", data)) == 1
    assert not audit_engine._shallow_copy_nested_write_review_candidates("MAIN", "store.ts", {**data, **change})


@pytest.mark.parametrize("source", [
    _source(FIXTURE["shallow_mutation_cases"][0]["body"]).replace("value;\n", "value;\n  return;\n"),
    _source(FIXTURE["shallow_mutation_cases"][0]["body"])[:-3],
    _source(FIXTURE["shallow_mutation_cases"][0]["body"]).replace("await persist(copy)", "await missing(copy)"),
    _source(FIXTURE["shallow_mutation_cases"][0]["body"]).replace("copy.items[0]", "copy['items'][0]"),
])
def test_unsupported_or_degraded_paths_do_not_emit(source, tmp_path):
    features, _data = _analyze(source, tmp_path / "unsupported.ts")
    assert features == []


def _payload(candidates):
    return {"meta": {"kind": "audit_report", "version": "test"},
            "summary": {"total": 0, "by_rule": {}, "audit_scope": {}, "rule_taxonomy": {}, "remediation_backlog": []},
            "audit_scope": {"atlas_project_count": 1, "audited_project_count": 1, "audited_projects": ["MAIN"], "violation_project_count": 0},
            "atlas_project_count": 1, "audited_project_count": 1, "audited_projects": ["MAIN"],
            "violation_project_count": 0, "violations": [], "report_sections": [], "runtime_review_candidates": candidates}


def test_review_candidate_schema_keeps_await_span_and_unproven_status():
    data = {"language": "typescript", "features": [PREFIX + "copy:items:3:4:5"],
            "parser_evidence": {"status": "observed", "parser_kind": "typescript_compiler_api"},
            "loc": 6, "hash": "bound"}
    candidate = audit_engine._shallow_copy_nested_write_review_candidates("MAIN", "store.ts", data)[0]
    assert validate_payload("audit_report", _payload([candidate])) == []
    for field in ("subject", "property", "await_line", "verification_questions"):
        assert validate_payload("audit_report", _payload([{key: value for key, value in candidate.items() if key != field}]))
    assert validate_payload("audit_report", _payload([{**candidate, "confidence": "confirmed_runtime_defect"}]))


def test_fact_survives_real_atlas_and_rejects_old_cache_contract(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "store.ts").write_text(_source(FIXTURE["shallow_mutation_cases"][0]["body"]), encoding="utf-8")
    env = dict(os.environ)
    for name in ("CODEMAPS_TARGET_PREFLIGHT_RECEIPT", "CODEMAPS_TARGET_PREFLIGHT_RECEIPT_SHA256",
                 "CODEMAPS_TARGET_PREFLIGHT_REUSE_MODE", "CODEMAPS_TARGET_PROJECTS"):
        env.pop(name, None)
    env["CODEMAPS_TARGET_ROOT"] = str(target)
    result = subprocess.run([sys.executable, "-m", "tools.engines.generate_atlas"],
                            cwd=ROOT, env=env, capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode == 0, result.stderr or result.stdout
    atlas_path = ROOT / "output/external_targets" / external_target_output_slug(str(target)) / ".raw/atlas.json"
    atlas = load_json_file(atlas_path)
    data = atlas["MAIN"]["files"]["store.ts"]
    assert data["ast_contract_version"] == AST_CONTRACT_VERSION and file_contract_is_current(data)
    assert not file_contract_is_current({**data, "features": [], "ast_contract_version": "v18.30-response-field-state-review"})
    candidates = audit_engine._shallow_copy_nested_write_review_candidates("MAIN", "store.ts", data)
    assert len(candidates) == 1 and candidates[0]["source_hash"] == data["hash"]
    assert validate_payload("audit_report", _payload(candidates)) == []


def test_audit_scan_routes_fact_outside_violation_lane(monkeypatch, tmp_path):
    data = {"language": "typescript", "features": [PREFIX + "copy:items:3:4:5"],
            "parser_evidence": {"status": "observed", "parser_kind": "typescript_compiler_api"},
            "loc": 6, "hash": "bound", "imports": [], "symbols": []}
    captured = {}
    monkeypatch.setattr(audit_engine, "resolve_atlas_data", lambda atlas: (atlas, "fixture"))
    monkeypatch.setattr(audit_engine, "resolve_runtime_projects", lambda _root: {"MAIN": tmp_path})
    monkeypatch.setattr(audit_engine, "load_effective_architecture_policy_context", lambda _raw: ({}, ""))
    monkeypatch.setattr(audit_engine, "build_effective_project_rule_taxonomy", lambda *args, **kwargs: {})
    monkeypatch.setattr(audit_engine, "filter_violations_by_project_taxonomy", lambda rows, _taxonomies: (rows, {}))
    monkeypatch.setattr(audit_engine, "get_audit_report_sections", lambda: [])
    monkeypatch.setattr(audit_engine, "get_module_root_name", lambda: "src")
    monkeypatch.setattr(audit_engine, "load_scope_authority_for_consumer", lambda _raw: ({}, {"evidence_status": "BOUNDED_PROJECT_SELECTION"}))
    monkeypatch.setattr(audit_engine, "bind_consumer_projects", lambda authority, **kwargs: authority)
    monkeypatch.setattr(audit_engine, "_write_outputs", lambda violations, _sections, summary, **kwargs:
                        captured.update(violations=violations, total=summary["total"], candidates=kwargs["runtime_review_candidates"]))
    assert audit_engine.analyze_project(atlas={"MAIN": {"files": {"src/store.ts": data}}}) is True
    assert captured["total"] == 0 and all(not rows for rows in captured["violations"].values())
    assert len(captured["candidates"]) == 1 and captured["candidates"][0]["confidence"] == "needs_runtime_proof"


def test_audit_text_and_json_preserve_unverified_await_context(monkeypatch):
    data = {"language": "typescript", "features": [PREFIX + "copy:items:3:4:5"],
            "parser_evidence": {"status": "observed", "parser_kind": "typescript_compiler_api"},
            "loc": 6, "hash": "bound"}
    candidate = audit_engine._shallow_copy_nested_write_review_candidates("MAIN", "store.ts", data)[0]
    written = {}
    monkeypatch.setattr(audit_engine, "_load_structural_contract_health", lambda: {
        "status": "not_available_in_audit_phase", "reason": "fixture", "atlas_contract_file_ratio": 0,
        "member_detail_contract_ratio": 0, "genome_occurrence_contract_ratio": 0, "atlas_current_version_ratio": 0,
        "genome_current_version_ratio": 0})
    monkeypatch.setattr(audit_engine, "build_rule_taxonomy", lambda **kwargs: {"summary": {"by_layer": {}, "by_mode": {}}, "profiles": {}})
    monkeypatch.setattr(audit_engine, "get_module_root_name", lambda: "src")
    monkeypatch.setattr(audit_engine, "_canonical_audit_artifact_identity", lambda _atlas: {"status": "INCOMPLETE"})
    monkeypatch.setattr(audit_engine, "save_json_atomic", lambda _path, payload: written.update(payload=payload))
    monkeypatch.setattr(audit_engine, "save_text_atomic", lambda path, body: written.update({path.name: body}))
    monkeypatch.setattr(audit_engine, "write_current_atlas_lineage", lambda **kwargs: None)
    monkeypatch.setattr(audit_engine, "flush_shadow_writes", lambda **kwargs: True)
    monkeypatch.setattr(audit_engine, "invalidate_audit_report_cache", lambda: None)
    audit_engine._write_outputs({}, [], {"total": 0, "by_rule": {}}, atlas={}, runtime_review_candidates=[candidate])
    assert written["payload"]["summary"]["total"] == 0
    assert written["payload"]["violations"] == []
    assert written["payload"]["runtime_review_candidates"] == [candidate]
    assert validate_payload("audit_report", written["payload"]) == []
    assert "awaited_call_line=5 (purpose unverified)" in written["audit_report.txt"]
    assert "RUNTIME REVIEW CANDIDATES REMAIN UNPROVEN" in written["audit_report.txt"]
    assert "100% SEALED" not in written["audit_report.txt"]
