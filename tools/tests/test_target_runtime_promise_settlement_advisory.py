import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tools.core.artifact_validator import validate_payload
from tools.engines import audit as audit_engine
from tools.engines.audit import _unawaited_async_helper_review_candidates
from tools.engines.generate_atlas import _file_parser_evidence


ROOT = Path(__file__).resolve().parents[2]
PREFIX = "TypeScript:UnawaitedAsyncHelperCall:"
HELPER = """
const persist = async (id: string) => {
  await db.update(id);
};
"""


@pytest.mark.parametrize(
    ("case", "caller", "expected"),
    [
        ("unawaited", "persist(id);", True),
        ("awaited", "await persist(id);", False),
        ("returned", "return persist(id);", False),
        ("detached", "void persist(id);", False),
        ("handled", "persist(id).catch(log);", False),
        ("then_handled", "persist(id).then(onSaved);", False),
        ("shadowed", "const persist = (value: string) => value; persist(id);", False),
        ("nested_callback", "queue(() => persist(id));", False),
    ],
)
def test_promise_settlement_advisory_is_bound_to_direct_same_file_async_call(
    case, caller, expected, tmp_path
):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available")
    source = HELPER + f"""
export const actions = {{
  updateProject: async (id: string) => {{
    {caller}
  }},
}};
"""
    target = tmp_path / f"{case}.ts"
    target.write_text(source, encoding="utf-8")
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
    features = [feature for feature in meta["features"] if feature.startswith(PREFIX)]
    atlas_file = {
        "language": "typescript",
        "features": meta["features"],
        "parser_evidence": _file_parser_evidence(symbols, "typescript"),
        "loc": len(source.splitlines()),
        "hash": "source-bound-fixture-hash",
        "target_ref": f"MAIN::{target.name}",
    }
    candidates = _unawaited_async_helper_review_candidates("MAIN", target.name, atlas_file)
    if expected:
        assert len(features) == len(candidates) == 1
        assert candidates[0]["kind"] == "unawaited_async_helper_call"
        assert candidates[0]["confidence"] == "needs_runtime_proof"
        assert candidates[0]["actionability"] == "review"
        assert candidates[0]["source_hash"] == atlas_file["hash"]
        assert candidates[0]["guard_line"] < candidates[0]["sink_line"]
        assert validate_payload("audit_report", {
            "meta": {"kind": "audit_report", "version": "test"},
            "summary": {
                "total": 0, "by_rule": {}, "audit_scope": {},
                "rule_taxonomy": {}, "remediation_backlog": [],
            },
            "audit_scope": {
                "atlas_project_count": 1, "audited_project_count": 1,
                "audited_projects": ["MAIN"], "violation_project_count": 0,
            },
            "atlas_project_count": 1,
            "audited_project_count": 1,
            "audited_projects": ["MAIN"],
            "violation_project_count": 0,
            "violations": [],
            "report_sections": [],
            "runtime_review_candidates": candidates,
        }) == []
    else:
        assert not features
        assert not candidates


def test_promise_settlement_rejects_unbound_or_invalid_atlas_spans():
    file_data = {
        "language": "typescript",
        "features": [f"{PREFIX}persist:2:5"],
        "parser_evidence": {"status": "observed", "parser_kind": "typescript_compiler_api"},
        "loc": 5,
        "hash": "bound-hash",
    }
    assert len(_unawaited_async_helper_review_candidates("MAIN", "src/store.ts", file_data)) == 1
    for changed in (
        {"hash": ""},
        {"loc": 4},
        {"parser_evidence": {"status": "degraded", "parser_kind": "typescript_compiler_api"}},
        {"parser_evidence": {"status": "observed", "parser_kind": "regex"}},
        {"language": "python"},
        {"target_ref": "OTHER::src/store.ts"},
        {"features": [f"{PREFIX}persist:5:2"]},
        {"features": ["TypeScript:UnawaitedAsyncHelperCall:bad-name:2:5"]},
    ):
        assert not _unawaited_async_helper_review_candidates("MAIN", "src/store.ts", {**file_data, **changed})


@pytest.mark.parametrize(
    ("helper", "caller"),
    [
        ("const persist = (id: string) => db.update(id);", "async"),
        ("const persist = async (id: string) => id;", "async"),
        ("const persist = async (id: string) => { await db.update(id); };", ""),
        ("const persist = async (id: string) => { queue(async () => { await db.update(id); }); };", "async"),
    ],
)
def test_async_helper_candidate_requires_own_await_and_async_caller(helper, caller, tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available")
    target = tmp_path / "control.ts"
    target.write_text(
        f"{helper}\nexport const actions = {{ updateProject: {caller} (id: string) => {{ persist(id); }} }};",
        encoding="utf-8",
    )
    result = subprocess.run(
        [node, str(ROOT / "tools" / "engines" / "ast_sequencer.cjs"), str(target)],
        cwd=ROOT, capture_output=True, text=True, timeout=20, check=False,
    )
    assert result.returncode == 0, result.stderr
    meta = next(row for row in json.loads(result.stdout) if row["name"] == "__file_meta__")
    assert not [feature for feature in meta["features"] if feature.startswith(PREFIX)]


def test_audit_routes_unawaited_helper_to_review_only(monkeypatch, tmp_path):
    captured = {}
    file_data = {
        "language": "typescript",
        "features": [f"{PREFIX}persist:2:5"],
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
    assert len(captured["candidates"]) == 1
    assert captured["candidates"][0]["target_ref"] == "MAIN::src/store.ts"
    assert all(not rows for rows in captured["violations"].values())
