import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tools.core.artifact_validator import validate_payload
from tools.engines import audit as audit_engine
from tools.engines.audit import _queued_mutation_review_candidates
from tools.engines.generate_atlas import _file_parser_evidence


ROOT = Path(__file__).resolve().parents[2]
QUEUE_PREFIX = "TypeScript:ReturnedQueueCatchNoRethrow:"
MISSING_PREFIX = "TypeScript:QueuedLookupFalsyFallthrough:"


def _source(*, callback=None, catch=None, assigned=None, returned=None) -> str:
    callback = callback or (
        "const execute = async () => {\n"
        "    const row = await repo.get(id);\n"
        "    if (row) { await repo.update(row); }\n"
        "    else if (warn) { logger.warn(id); }\n"
        "  };"
    )
    catch = catch or "error => { logger.error(error); }"
    assigned = assigned or f"queue[id] = previous.then(execute).catch({catch});"
    returned = returned or "return queue[id];"
    return (
        "const queue: Record<string, Promise<void>> = {};\n"
        "const persist = async (id: string) => {\n"
        f"  {callback}\n"
        "  const previous = queue[id] || Promise.resolve();\n"
        f"  {assigned}\n"
        f"  {returned}\n"
        "};\n"
    )


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
        [feature for feature in meta["features"]
         if feature.startswith((QUEUE_PREFIX, MISSING_PREFIX))],
        _queued_mutation_review_candidates("MAIN", target.name, file_data),
    )


def test_bound_returned_queue_and_missing_record_are_review_only(tmp_path):
    features, rows = _analyze(_source(), tmp_path / "positive.ts")
    assert len(features) == len(rows) == 2
    assert {row["kind"] for row in rows} == {
        "returned_queue_catch_without_rethrow",
        "queued_lookup_falsy_fallthrough",
    }
    assert all(row["confidence"] == "needs_runtime_proof" for row in rows)
    assert all(row["actionability"] == "review" for row in rows)
    assert all(row["guard_line"] < row["sink_line"] for row in rows)
    assert all(row["source_hash"] == "bound-fixture-hash" for row in rows)
    questions = {row["kind"]: row["verification_questions"] for row in rows}
    assert all(len(items) == 2 for items in questions.values())
    assert "reject or resolve" in questions["returned_queue_catch_without_rethrow"][0]
    assert "repository-defined falsy" in questions["queued_lookup_falsy_fallthrough"][0]
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
    assert validate_payload("audit_report", {
        "meta": {"kind": "audit_report", "version": "test"},
        "summary": {"total": 0, "by_rule": {}, "audit_scope": {},
                    "rule_taxonomy": {}, "remediation_backlog": []},
        "audit_scope": {"atlas_project_count": 1, "audited_project_count": 1,
                        "audited_projects": ["MAIN"], "violation_project_count": 0},
        "atlas_project_count": 1, "audited_project_count": 1,
        "audited_projects": ["MAIN"], "violation_project_count": 0,
        "violations": [], "report_sections": [],
        "runtime_review_candidates": [{**rows[0], "verification_questions": []}],
    })


def test_controlled_queue_settlement_is_a_mechanism_not_target_proof(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available")
    fixture = tmp_path / "queue_settlement.js"
    fixture.write_text(
        "async function probe(found, rejectWrite, rejectCatch, rejectAbsent) {\n"
        "  const events = [];\n"
        "  const repo = {\n"
        "    getById: async () => found ? { id: 'p' } : undefined,\n"
        "    update: async () => { events.push('write'); if (rejectWrite) throw Error('write'); },\n"
        "  };\n"
        "  const execute = async () => {\n"
        "    const row = await repo.getById();\n"
        "    if (row) await repo.update(row);\n"
        "    else if (rejectAbsent) throw Error('absent');\n"
        "  };\n"
        "  const queue = {};\n"
        "  queue.p = Promise.resolve().then(execute).catch(error => {\n"
        "    events.push('caught');\n"
        "    if (rejectCatch) throw error;\n"
        "  });\n"
        "  try { await queue.p; return { settlement: 'resolved', events }; }\n"
        "  catch { return { settlement: 'rejected', events }; }\n"
        "}\n"
        "(async () => console.log(JSON.stringify([\n"
        "  await probe(true, false, false, false),\n"
        "  await probe(true, true, false, false),\n"
        "  await probe(true, true, true, false),\n"
        "  await probe(false, false, false, false),\n"
        "  await probe(false, false, true, true),\n"
        "])))();\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [node, str(fixture)], capture_output=True, text=True, timeout=10, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [
        {"settlement": "resolved", "events": ["write"]},
        {"settlement": "resolved", "events": ["write", "caught"]},
        {"settlement": "rejected", "events": ["write", "caught"]},
        {"settlement": "resolved", "events": []},
        {"settlement": "rejected", "events": ["caught"]},
    ]


def test_audit_report_exposes_native_questions_without_claim_upgrade(monkeypatch):
    file_data = {
        "language": "typescript",
        "features": [f"{QUEUE_PREFIX}persist:4:5", f"{MISSING_PREFIX}persist:2:3"],
        "parser_evidence": {"status": "observed", "parser_kind": "typescript_compiler_api"},
        "loc": 5, "hash": "bound-hash", "target_ref": "MAIN::src/store.ts",
    }
    candidates = _queued_mutation_review_candidates("MAIN", "src/store.ts", file_data)
    written = {}
    monkeypatch.setattr(audit_engine, "_load_structural_contract_health", lambda: {
        "status": "not_available_in_audit_phase", "reason": "fixture",
        "atlas_contract_file_ratio": 0, "member_detail_contract_ratio": 0,
        "genome_occurrence_contract_ratio": 0, "atlas_current_version_ratio": 0,
        "genome_current_version_ratio": 0,
    })
    monkeypatch.setattr(audit_engine, "build_rule_taxonomy",
                        lambda **_kwargs: {"summary": {"by_layer": {}, "by_mode": {}}, "profiles": {}})
    monkeypatch.setattr(audit_engine, "get_module_root_name", lambda: "src")
    monkeypatch.setattr(audit_engine, "_canonical_audit_artifact_identity",
                        lambda _atlas: {"status": "INCOMPLETE"})
    monkeypatch.setattr(audit_engine, "save_json_atomic",
                        lambda _path, payload: written.update(payload=payload))
    monkeypatch.setattr(audit_engine, "save_text_atomic",
                        lambda path, body: written.update({path.name: body}))
    monkeypatch.setattr(audit_engine, "write_current_atlas_lineage", lambda **_kwargs: None)
    monkeypatch.setattr(audit_engine, "flush_shadow_writes", lambda **_kwargs: True)
    monkeypatch.setattr(audit_engine, "invalidate_audit_report_cache", lambda: None)
    audit_engine._write_outputs(
        {}, [], {"total": 0, "by_rule": {}}, atlas={},
        runtime_review_candidates=candidates,
    )
    assert written["payload"]["summary"]["total"] == 0
    assert validate_payload("audit_report", written["payload"]) == []
    report = written["audit_report.txt"]
    assert report.count("Target-native question (unverified):") == 4
    assert "Static source/guard/sink evidence does not prove" in report


def test_direct_log_only_missing_record_branch_is_also_a_source_fact(tmp_path):
    callback = (
        "const execute = async () => {\n"
        "    const row = await repo.get(id);\n"
        "    if (row) { await repo.update(row); }\n"
        "    else { logger.warn(id); }\n"
        "  };"
    )
    _features, rows = _analyze(_source(callback=callback), tmp_path / "direct_else.ts")
    assert {row["kind"] for row in rows} == {
        "returned_queue_catch_without_rethrow",
        "queued_lookup_falsy_fallthrough",
    }


@pytest.mark.parametrize(
    ("case", "changes"),
    [
        ("rethrown", {"catch": "error => { logger.error(error); throw error; }"}),
        ("fallback", {"catch": "error => { return fallback(error); }"}),
        ("catch_mutation", {"catch": "error => { state.failed = true; }"}),
        ("other_return", {"returned": "return otherQueue[id];"}),
        ("other_key", {"returned": "return queue[otherId];"}),
        ("no_return", {"returned": "return undefined;"}),
        ("unbound_then", {"assigned": "queue[id] = previous.then(other).catch(error => { logger.error(error); });"}),
        ("not_a_chain", {"assigned": "queue[id] = previous.catch(error => { logger.error(error); });"}),
        ("no_own_await", {"callback": "const execute = async () => { repo.update(id); };"}),
        ("sync_callback", {"callback": "const execute = () => { return repo.update(id); };"}),
        ("handled_auth_sibling", {"assigned": "await repo.update(id).catch(error => { logger.error(error); });"}),
    ],
)
def test_queue_candidate_rejects_lookalikes(case, changes, tmp_path):
    features, rows = _analyze(_source(**changes), tmp_path / f"{case}.ts")
    assert not features and not rows


@pytest.mark.parametrize(
    "callback",
    [
        "const execute = async () => { const row = await repo.get(id); if (row) { await repo.update(row); } else { throw new Error('missing'); } };",
        "const execute = async () => { const row = await repo.get(id); if (row !== undefined) { await repo.update(row); } };",
        "const execute = async () => { const row = await repo.get(id); if (row) { repo.update(row); } };",
        "const execute = async () => { const row = repo.get(id); if (row) { await repo.update(row); } };",
        "const execute = async () => { const row: unknown; if (row) { await repo.update(row); } };",
        "const execute = async () => { const row = await repo.get(id); if (row) { await repo.update(row); } else if (warn) { return fallback(); } };",
        "const execute = async () => { const row = await repo.get(id); if (row) { await repo.update(row); } await notify(); };",
    ],
)
def test_missing_record_requires_exact_fallthrough_but_queue_remains(callback, tmp_path):
    features, rows = _analyze(_source(callback=callback), tmp_path / "control.ts")
    assert len(features) == len(rows) == 1
    assert rows[0]["kind"] == "returned_queue_catch_without_rethrow"


def test_queued_review_rejects_unbound_or_degraded_atlas_evidence():
    file_data = {
        "language": "typescript",
        "features": [f"{QUEUE_PREFIX}persist:4:5", f"{MISSING_PREFIX}persist:2:3"],
        "parser_evidence": {"status": "observed", "parser_kind": "typescript_compiler_api"},
        "loc": 5, "hash": "bound-hash", "target_ref": "MAIN::src/store.ts",
    }
    assert len(_queued_mutation_review_candidates("MAIN", "src/store.ts", file_data)) == 2
    for changed in (
        {"hash": ""}, {"loc": 2}, {"target_ref": "OTHER::src/store.ts"},
        {"language": "go"},
        {"parser_evidence": {"status": "degraded", "parser_kind": "typescript_compiler_api"}},
        {"features": [f"{QUEUE_PREFIX}bad-name:4:5"]},
        {"features": [f"{MISSING_PREFIX}persist:5:2"]},
    ):
        assert not _queued_mutation_review_candidates(
            "MAIN", "src/store.ts", {**file_data, **changed}
        )


def test_audit_routes_queued_candidates_without_violations(monkeypatch, tmp_path):
    captured = {}
    file_data = {
        "language": "typescript",
        "features": [f"{QUEUE_PREFIX}persist:4:5", f"{MISSING_PREFIX}persist:2:3"],
        "parser_evidence": {"status": "observed", "parser_kind": "typescript_compiler_api"},
        "loc": 5, "hash": "bound-hash", "imports": [], "symbols": [],
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
    assert len(captured["candidates"]) == 2
    assert all(not rows for rows in captured["violations"].values())
