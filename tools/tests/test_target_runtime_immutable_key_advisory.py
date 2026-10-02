import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tools.core.artifact_validator import validate_payload
from tools.engines import audit as audit_engine
from tools.engines.audit import _immutable_key_review_candidates
from tools.engines.generate_atlas import _file_parser_evidence


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads(
    (ROOT / "config" / "target_runtime_validator_advisory_fixtures.json").read_text(
        encoding="utf-8"
    )
)


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda case: case["id"])
def test_immutable_key_advisory_uses_ast_and_keeps_safe_controls_negative(case, tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available")
    target = tmp_path / f"{case['id']}.ts"
    target.write_text(case["source"], encoding="utf-8")
    result = subprocess.run(
        [node, str(ROOT / "tools" / "engines" / "ast_sequencer.cjs"), str(target)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    symbols = json.loads(result.stdout)
    meta = next(row for row in symbols if row["name"] == "__file_meta__")
    features = [
        item for item in meta["features"]
        if item.startswith("TypeScript:ImmutableKeyTruthinessGuard:")
    ]
    atlas_file = {
        "language": "typescript",
        "features": meta["features"],
        "parser_evidence": _file_parser_evidence(symbols, "typescript"),
        "loc": len(case["source"].splitlines()),
        "hash": "source-bound-fixture-hash",
        "target_ref": f"MAIN::{target.name}",
    }
    candidates = _immutable_key_review_candidates("MAIN", target.name, atlas_file)
    if case["expected"] == "advisory":
        assert len(features) == len(candidates) == 1
        assert candidates[0]["kind"] == "immutable_key_truthiness_guard_bypass"
        assert candidates[0]["confidence"] == "needs_runtime_proof"
        assert candidates[0]["guard_line"] < candidates[0]["sink_line"]
        assert candidates[0]["source_hash"] == atlas_file["hash"]
    else:
        assert not features
        assert not candidates


def test_immutable_key_candidate_rejects_unbound_or_invalid_atlas_spans():
    file_data = {
        "language": "typescript",
        "features": ["TypeScript:ImmutableKeyTruthinessGuard:id:2:3"],
        "parser_evidence": {
            "status": "observed",
            "parser_kind": "typescript_compiler_api",
        },
        "loc": 3,
        "hash": "bound-hash",
    }
    assert len(_immutable_key_review_candidates("MAIN", "src/doc.ts", file_data)) == 1
    for changed in (
        {"hash": ""},
        {"loc": 2},
        {"parser_evidence": {"status": "degraded", "parser_kind": "typescript_compiler_api"}},
        {"parser_evidence": {"status": "observed", "parser_kind": "regex"}},
        {"language": "python"},
        {"target_ref": "OTHER::src/doc.ts"},
        {"features": ["TypeScript:ImmutableKeyTruthinessGuard:id:3:2"]},
        {"features": [42, {"not": "a_feature"}]},
    ):
        assert not _immutable_key_review_candidates("MAIN", "src/doc.ts", {**file_data, **changed})


def test_runtime_candidate_is_in_audit_report_but_not_violation_total(monkeypatch):
    candidate = _immutable_key_review_candidates(
        "MAIN",
        "src/doc.ts",
        {
            "language": "typescript",
            "features": ["TypeScript:ImmutableKeyTruthinessGuard:id:2:3"],
            "parser_evidence": {
                "status": "observed",
                "parser_kind": "typescript_compiler_api",
            },
            "loc": 3,
            "hash": "source-bound-hash",
        },
    )[0]
    written = {}
    monkeypatch.setattr(
        audit_engine,
        "_load_structural_contract_health",
        lambda: {
            "status": "not_available_in_audit_phase",
            "reason": "fixture",
            "atlas_contract_file_ratio": 0,
            "member_detail_contract_ratio": 0,
            "genome_occurrence_contract_ratio": 0,
            "atlas_current_version_ratio": 0,
            "genome_current_version_ratio": 0,
        },
    )
    monkeypatch.setattr(
        audit_engine,
        "build_rule_taxonomy",
        lambda **_kwargs: {"summary": {"by_layer": {}, "by_mode": {}}, "profiles": {}},
    )
    monkeypatch.setattr(audit_engine, "get_module_root_name", lambda: "src")
    monkeypatch.setattr(audit_engine, "_canonical_audit_artifact_identity", lambda _atlas: {"status": "INCOMPLETE"})
    monkeypatch.setattr(audit_engine, "save_json_atomic", lambda _path, payload: written.update(payload=payload))
    monkeypatch.setattr(
        audit_engine,
        "save_text_atomic",
        lambda path, body: written.update({path.name: body}),
    )
    monkeypatch.setattr(audit_engine, "write_current_atlas_lineage", lambda **_kwargs: None)
    monkeypatch.setattr(audit_engine, "flush_shadow_writes", lambda **_kwargs: True)
    monkeypatch.setattr(audit_engine, "invalidate_audit_report_cache", lambda: None)
    audit_engine._write_outputs(
        {},
        [],
        {"total": 0, "by_rule": {}},
        atlas={},
        runtime_review_candidates=[candidate],
    )
    assert written["payload"]["summary"]["total"] == 0
    assert written["payload"]["violations"] == []
    assert written["payload"]["summary"]["runtime_review_candidate_count"] == 1
    assert written["payload"]["runtime_review_candidates"] == [candidate]
    assert validate_payload("audit_report", written["payload"]) == []
    assert "RUNTIME REVIEW CANDIDATES REMAIN UNPROVEN" in written["audit_report.txt"]
    assert "100% SEALED" not in written["audit_report.txt"]


def test_audit_scan_routes_file_feature_to_review_only_consumer(monkeypatch, tmp_path):
    captured = {}
    file_data = {
        "language": "typescript",
        "features": ["TypeScript:ImmutableKeyTruthinessGuard:id:2:3"],
        "parser_evidence": {
            "status": "observed",
            "parser_kind": "typescript_compiler_api",
        },
        "loc": 3,
        "hash": "source-bound-hash",
        "imports": [],
        "symbols": [],
    }
    atlas = {"MAIN": {"files": {"src/doc.ts": file_data}}}
    monkeypatch.setattr(audit_engine, "resolve_atlas_data", lambda source: (source, "fixture"))
    monkeypatch.setattr(audit_engine, "resolve_runtime_projects", lambda _root: {"MAIN": tmp_path})
    monkeypatch.setattr(
        audit_engine,
        "load_effective_architecture_policy_context",
        lambda _raw: ({}, ""),
    )
    monkeypatch.setattr(audit_engine, "build_effective_project_rule_taxonomy", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        audit_engine,
        "filter_violations_by_project_taxonomy",
        lambda violations, _taxonomies: (violations, {}),
    )
    monkeypatch.setattr(audit_engine, "get_audit_report_sections", lambda: [])
    monkeypatch.setattr(audit_engine, "get_module_root_name", lambda: "src")
    monkeypatch.setattr(
        audit_engine,
        "load_scope_authority_for_consumer",
        lambda _raw: ({}, {"evidence_status": "BOUNDED_PROJECT_SELECTION"}),
    )
    monkeypatch.setattr(audit_engine, "bind_consumer_projects", lambda authority, **_kwargs: authority)
    monkeypatch.setattr(
        audit_engine,
        "_write_outputs",
        lambda violations, _sections, summary, **kwargs:
            captured.update(violations=violations, total=summary["total"], candidates=kwargs["runtime_review_candidates"]),
    )
    assert audit_engine.analyze_project(atlas=atlas) is True
    assert captured["total"] == 0
    assert len(captured["candidates"]) == 1
    assert captured["candidates"][0]["target_ref"] == "MAIN::src/doc.ts"
    assert all(not rows for rows in captured["violations"].values())
