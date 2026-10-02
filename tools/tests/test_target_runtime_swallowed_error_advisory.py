import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tools.core.artifact_validator import validate_payload
from tools.engines import audit as audit_engine
from tools.engines.audit import _caught_async_await_review_candidates
from tools.engines.generate_atlas import _file_parser_evidence


ROOT = Path(__file__).resolve().parents[2]
PREFIX = "TypeScript:CaughtAsyncAwaitNoRethrow:"


def _analyze(source: str, target: Path) -> tuple[list[str], list[dict]]:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available")
    target.write_text(source, encoding="utf-8")
    result = subprocess.run(
        [node, str(ROOT / "tools/engines/ast_sequencer.cjs"), str(target)],
        cwd=ROOT, capture_output=True, text=True, timeout=20, check=False,
    )
    assert result.returncode == 0, result.stderr
    symbols = json.loads(result.stdout)
    meta = next(row for row in symbols if row["name"] == "__file_meta__")
    file_data = {
        "language": "typescript", "features": meta["features"],
        "parser_evidence": _file_parser_evidence(symbols, "typescript"),
        "loc": len(source.splitlines()), "hash": "bound-fixture-hash",
        "target_ref": f"MAIN::{target.name}",
    }
    return (
        [feature for feature in meta["features"] if feature.startswith(PREFIX)],
        _caught_async_await_review_candidates("MAIN", target.name, file_data),
    )


@pytest.mark.parametrize(
    ("case", "body", "expected"),
    [
        ("logging_only", "try { await db.update(id); } catch (error) { service.logError(error); }", True),
        ("rethrow", "try { await db.update(id); } catch (error) { service.logError(error); throw error; }", False),
        ("reject", "try { await db.update(id); } catch (error) { return Promise.reject(error); }", False),
        ("fallback", "try { await db.update(id); } catch (error) { return fallback(id); }", False),
        ("state_write", "try { await db.update(id); } catch (error) { state.failed = true; }", False),
        ("no_await", "try { db.update(id); } catch (error) { service.logError(error); }", False),
        ("nested_await", "try { queue(async () => await db.update(id)); } catch (error) { service.logError(error); }", False),
        ("outer_catch", "try { await db.update(id); } catch (error) { if (error) service.logError(error); }", False),
    ],
)
def test_caught_await_candidate_has_narrow_positive_and_negative_controls(case, body, expected, tmp_path):
    body_lines = body.replace("} catch", "}\n  catch")
    source = f"const persist = async (id: string) => {{\n  {body_lines}\n}};\n"
    features, rows = _analyze(source, tmp_path / f"{case}.ts")
    if expected:
        assert len(features) == len(rows) == 1
        row = rows[0]
        assert row["kind"] == "caught_async_await_without_rethrow"
        assert row["subject"] == "persist"
        assert row["confidence"] == "needs_runtime_proof"
        assert row["actionability"] == "review"
        assert "data loss are unproven" in row["claim_boundary"]
        assert validate_payload("audit_report", {
            "meta": {"kind": "audit_report", "version": "test"},
            "summary": {"total": 0, "by_rule": {}, "audit_scope": {},
                        "rule_taxonomy": {}, "remediation_backlog": []},
            "audit_scope": {"atlas_project_count": 1, "audited_project_count": 1,
                            "audited_projects": ["MAIN"], "violation_project_count": 0},
            "atlas_project_count": 1, "audited_project_count": 1,
            "audited_projects": ["MAIN"], "violation_project_count": 0,
            "violations": [], "report_sections": [], "runtime_review_candidates": rows,
        }) == []
    else:
        assert not features
        assert not rows


def test_caught_await_candidate_rejects_degraded_or_unbound_evidence():
    file_data = {
        "language": "typescript", "features": [f"{PREFIX}persist:1:3"],
        "parser_evidence": {"status": "observed", "parser_kind": "typescript_compiler_api"},
        "loc": 3, "hash": "bound-hash", "target_ref": "MAIN::src/store.ts",
    }
    assert len(_caught_async_await_review_candidates("MAIN", "src/store.ts", file_data)) == 1
    for change in (
        {"hash": ""}, {"loc": 2}, {"target_ref": "OTHER::src/store.ts"},
        {"language": "go"},
        {"parser_evidence": {"status": "degraded", "parser_kind": "typescript_compiler_api"}},
        {"features": [f"{PREFIX}bad-name:1:3"]},
        {"features": [f"{PREFIX}persist:3:1"]},
    ):
        assert not _caught_async_await_review_candidates(
            "MAIN", "src/store.ts", {**file_data, **change}
        )


@pytest.mark.parametrize("suffix", ["throw new Error('failed');", "return fallback();"])
def test_caught_await_candidate_does_not_claim_settlement_with_later_exit(suffix, tmp_path):
    source = (
        "const persist = async (id: string) => {\n"
        "  try { await db.update(id); }\n"
        "  catch (error) { service.logError(error); }\n"
        f"  {suffix}\n"
        "};\n"
    )
    features, rows = _analyze(source, tmp_path / "later_exit.ts")
    assert not features and not rows


def test_caught_await_candidate_does_not_claim_settlement_with_finally_throw(tmp_path):
    source = (
        "const persist = async (id: string) => {\n"
        "  try { await db.update(id); }\n"
        "  catch (error) { service.logError(error); }\n"
        "  finally { throw new Error('failed'); }\n"
        "};\n"
    )
    features, rows = _analyze(source, tmp_path / "finally_throw.ts")
    assert not features and not rows


def test_audit_routes_caught_await_only_to_review_lane(monkeypatch, tmp_path):
    captured = {}
    file_data = {
        "language": "typescript", "features": [f"{PREFIX}persist:1:3"],
        "parser_evidence": {"status": "observed", "parser_kind": "typescript_compiler_api"},
        "loc": 3, "hash": "bound-hash", "imports": [], "symbols": [],
    }
    monkeypatch.setattr(audit_engine, "resolve_atlas_data", lambda source: (source, "fixture"))
    monkeypatch.setattr(audit_engine, "resolve_runtime_projects", lambda _root: {"MAIN": tmp_path})
    monkeypatch.setattr(audit_engine, "load_effective_architecture_policy_context", lambda _raw: ({}, ""))
    monkeypatch.setattr(audit_engine, "build_effective_project_rule_taxonomy", lambda *args, **kwargs: {})
    monkeypatch.setattr(audit_engine, "filter_violations_by_project_taxonomy",
                        lambda violations, _taxonomies: (violations, {}))
    monkeypatch.setattr(audit_engine, "get_audit_report_sections", lambda: [])
    monkeypatch.setattr(audit_engine, "get_module_root_name", lambda: "src")
    monkeypatch.setattr(audit_engine, "load_scope_authority_for_consumer",
                        lambda _raw: ({}, {"evidence_status": "BOUNDED_PROJECT_SELECTION"}))
    monkeypatch.setattr(audit_engine, "bind_consumer_projects", lambda authority, **_kwargs: authority)
    monkeypatch.setattr(
        audit_engine, "_write_outputs",
        lambda violations, _sections, summary, **kwargs:
            captured.update(violations=violations, total=summary["total"],
                            candidates=kwargs["runtime_review_candidates"]),
    )
    assert audit_engine.analyze_project(atlas={"MAIN": {"files": {"src/store.ts": file_data}}}) is True
    assert captured["total"] == 0
    assert len(captured["candidates"]) == 1
    assert captured["candidates"][0]["kind"] == "caught_async_await_without_rethrow"
    assert all(not rows for rows in captured["violations"].values())
