"""Source-bound schema/cast advisory calibration, not target-native runtime proof."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tools.core.artifact_validator import validate_payload
from tools.engines import audit as audit_engine
from tools.engines.audit import _schema_broader_cast_review_candidates
from tools.engines.generate_atlas import _file_parser_evidence


ROOT = Path(__file__).resolve().parents[2]
CASES = json.loads(
    (ROOT / "config/target_runtime_schema_mutation_advisory_fixtures.json").read_text(
        encoding="utf-8"
    )
)["schema_strip_cases"]
FEATURE = "TypeScript:ZodObjectBroaderCast:"


@pytest.mark.parametrize("case", CASES, ids=lambda row: row["id"])
def test_schema_broader_cast_requires_direct_source_bound_pair(case, tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available")
    target = tmp_path / f"{case['id']}.ts"
    target.write_text(case["source"], encoding="utf-8")
    run = subprocess.run(
        [node, str(ROOT / "tools/engines/ast_sequencer.cjs"), str(target)],
        cwd=ROOT, capture_output=True, text=True, timeout=20, check=False,
    )
    assert run.returncode == 0, run.stderr
    rows = json.loads(run.stdout)
    meta = next(row for row in rows if row["name"] == "__file_meta__")
    features = [feature for feature in meta["features"] if feature.startswith(FEATURE)]
    atlas_file = {
        "language": "typescript",
        "features": meta["features"],
        "parser_evidence": _file_parser_evidence(rows, "typescript"),
        "project_key": "MAIN",
        "atlas_rel_path": target.name,
        "loc": len(case["source"].splitlines()),
        "hash": "source-bound-fixture-hash",
        "target_ref": f"MAIN::{target.name}",
    }
    candidates = _schema_broader_cast_review_candidates("MAIN", target.name, atlas_file)
    if case["expected"] == "advisory":
        assert len(features) == len(candidates) == 1
        candidate = candidates[0]
        assert candidate["kind"] == "zod_object_broader_cast"
        assert candidate["subject"] == "history"
        assert candidate["confidence"] == "needs_runtime_proof"
        assert candidate["guard_line"] < candidate["sink_line"]
    else:
        assert features == []
        assert candidates == []


def test_schema_broader_cast_rejects_wrong_source_identity_and_spans():
    feature = f"{FEATURE}Schema:Domain:history:3:4"
    file_data = {
        "language": "typescript", "features": [feature],
        "parser_evidence": {"status": "observed", "parser_kind": "typescript_compiler_api"},
        "project_key": "MAIN", "atlas_rel_path": "src/parse.ts", "loc": 4,
        "hash": "source-bound-fixture-hash", "target_ref": "MAIN::src/parse.ts",
    }
    assert len(_schema_broader_cast_review_candidates("MAIN", "src/parse.ts", file_data)) == 1
    for changed in (
        {"project_key": "OTHER"}, {"atlas_rel_path": "src/other.ts"},
        {"target_ref": "OTHER::src/parse.ts"}, {"hash": ""}, {"loc": 3},
        {"parser_evidence": {"status": "degraded", "parser_kind": "typescript_compiler_api"}},
        {"features": [f"{FEATURE}Schema:Domain:history:4:3"]},
    ):
        assert _schema_broader_cast_review_candidates(
            "MAIN", "src/parse.ts", {**file_data, **changed}
        ) == []


def test_schema_broader_cast_report_stays_review_only(monkeypatch):
    candidate = _schema_broader_cast_review_candidates(
        "MAIN", "src/parse.ts", {
            "language": "typescript",
            "features": [f"{FEATURE}Schema:Domain:history:3:4"],
            "parser_evidence": {"status": "observed", "parser_kind": "typescript_compiler_api"},
            "loc": 4, "hash": "source-bound-hash",
        },
    )[0]
    written = {}
    monkeypatch.setattr(audit_engine, "_load_structural_contract_health", lambda: {
        "status": "not_available_in_audit_phase", "reason": "fixture",
        "atlas_contract_file_ratio": 0, "member_detail_contract_ratio": 0,
        "genome_occurrence_contract_ratio": 0, "atlas_current_version_ratio": 0,
        "genome_current_version_ratio": 0,
    })
    monkeypatch.setattr(audit_engine, "build_rule_taxonomy", lambda **_kwargs: {
        "summary": {"by_layer": {}, "by_mode": {}}, "profiles": {},
    })
    monkeypatch.setattr(audit_engine, "get_module_root_name", lambda: "src")
    monkeypatch.setattr(audit_engine, "_canonical_audit_artifact_identity", lambda _atlas: {
        "status": "INCOMPLETE",
    })
    monkeypatch.setattr(
        audit_engine, "save_json_atomic", lambda _path, payload: written.update(payload=payload)
    )
    monkeypatch.setattr(
        audit_engine, "save_text_atomic", lambda path, body: written.update({path.name: body})
    )
    monkeypatch.setattr(audit_engine, "write_current_atlas_lineage", lambda **_kwargs: None)
    monkeypatch.setattr(audit_engine, "flush_shadow_writes", lambda **_kwargs: True)
    monkeypatch.setattr(audit_engine, "invalidate_audit_report_cache", lambda: None)
    audit_engine._write_outputs(
        {}, [], {"total": 0, "by_rule": {}},
        atlas={}, runtime_review_candidates=[candidate],
    )
    payload = written["payload"]
    assert payload["summary"]["total"] == 0
    assert payload["violations"] == []
    assert payload["runtime_review_candidates"] == [candidate]
    assert validate_payload("audit_report", payload) == []
    assert validate_payload("audit_report", {
        **payload,
        "runtime_review_candidates": [
            {key: value for key, value in candidate.items() if key != "subject"}
        ],
    })
    assert "RUNTIME REVIEW CANDIDATES REMAIN UNPROVEN" in written["audit_report.txt"]
    assert "100% SEALED" not in written["audit_report.txt"]


def test_audit_scan_routes_schema_feature_only_to_review_candidates(monkeypatch, tmp_path):
    captured = {}
    atlas = {"MAIN": {"files": {"src/parse.ts": {
        "language": "typescript", "features": [f"{FEATURE}Schema:Domain:history:3:4"],
        "parser_evidence": {"status": "observed", "parser_kind": "typescript_compiler_api"},
        "loc": 4, "hash": "source-bound-hash", "imports": [], "symbols": [],
    }}}}
    monkeypatch.setattr(audit_engine, "resolve_atlas_data", lambda source: (source, "fixture"))
    monkeypatch.setattr(audit_engine, "resolve_runtime_projects", lambda _root: {"MAIN": tmp_path})
    monkeypatch.setattr(audit_engine, "load_effective_architecture_policy_context", lambda _raw: ({}, ""))
    monkeypatch.setattr(audit_engine, "build_effective_project_rule_taxonomy", lambda *a, **k: {})
    monkeypatch.setattr(
        audit_engine, "filter_violations_by_project_taxonomy",
        lambda violations, _taxonomies: (violations, {}),
    )
    monkeypatch.setattr(audit_engine, "get_audit_report_sections", lambda: [])
    monkeypatch.setattr(audit_engine, "get_module_root_name", lambda: "src")
    monkeypatch.setattr(
        audit_engine, "load_scope_authority_for_consumer",
        lambda _raw: ({}, {"evidence_status": "BOUNDED_PROJECT_SELECTION"}),
    )
    monkeypatch.setattr(audit_engine, "bind_consumer_projects", lambda authority, **_kwargs: authority)
    monkeypatch.setattr(
        audit_engine, "_write_outputs",
        lambda violations, _sections, summary, **kwargs: captured.update(
            violations=violations, total=summary["total"],
            candidates=kwargs["runtime_review_candidates"],
        ),
    )
    assert audit_engine.analyze_project(atlas=atlas) is True
    assert captured["total"] == 0
    assert len(captured["candidates"]) == 1
    assert captured["candidates"][0]["kind"] == "zod_object_broader_cast"
    assert all(not rows for rows in captured["violations"].values())
