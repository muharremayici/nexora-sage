from unittest.mock import Mock
import pytest

from tools.validate_react_fixtures import _fixture_validation_passed


def test_empty_fixture_validation_shell_is_not_evidence():
    assert _fixture_validation_passed({"summary": {"failed_checks": 0}}) is False


def test_fixture_validation_requires_observed_checks():
    payload = {
        "summary": {"failed_checks": 0, "total_checks": 1},
        "checks": [{"name": "fixture_contract", "passed": True}],
    }

    assert _fixture_validation_passed(payload) is True


def test_fixture_validation_rejects_conflicting_pass_label():
    payload = {
        "summary": {"status": "PASS", "failed_checks": 1, "total_checks": 1},
        "checks": [{"name": "fixture_contract", "passed": False}],
    }

    assert _fixture_validation_passed(payload) is False


def test_fresh_react_producers_preserve_primary_full_and_missing_evidence(monkeypatch, tmp_path):
    """Exercise real producers without a live repository, Atlas or smoke execution."""
    from tools.engines import react_ecosystem_analyzer as ecosystem
    from tools.engines import react_runtime_intelligence as runtime
    from tools.engines import react_compiler_readiness as compiler
    from tools import validate_react_analysis_chain_integrity as chain
    from tools.core.config import save_json_atomic
    from tools.core.json_io import load_json_file

    raw = tmp_path / "raw"
    reports = tmp_path / "reports"
    source = """'use client';
import React, { useMemo } from 'react';
import fs from 'fs';
export function EditorPage(props) {
  props.count = 1;
  const value = useMemo(() => props.count, []);
  return <button>{value}</button>;
}
"""
    atlas = {"FIXTURE": {"files": {"src/app/editor/page.tsx": {}}}}
    for module in (ecosystem, runtime, compiler):
        monkeypatch.setattr(module, "RAW_DIR", raw)
        monkeypatch.setattr(module, "REPORTS_DIR", reports)
        monkeypatch.setattr(module, "_POLICY_CACHE", None)
    for module in (ecosystem, runtime):
        monkeypatch.setattr(module, "load_atlas_data", lambda: atlas)
        monkeypatch.setattr(module, "project_runtime_atlas", lambda value: (value, {}))
        monkeypatch.setattr(module, "_read_project_file", lambda *args: source)
    monkeypatch.setattr(ecosystem, "EngineProgress", Mock())
    ecosystem_result = ecosystem.run_react_ecosystem_analyzer()
    runtime_result = runtime.run_react_runtime_intelligence()
    compiler_result = compiler.run_react_compiler_readiness()
    for result in (ecosystem_result, runtime_result, compiler_result):
        assert result["findings"], "An empty producer shell is not this fixture's proof"
    assert not (raw / "ui_smoke_execution.json").exists()
    assert any(row["runtime_proof_status"] == "needs_runtime_proof"
               for row in runtime_result["findings"])

    monkeypatch.setattr(chain, "RAW_DIR", raw)
    artifacts = chain._react_primary_artifacts(chain._load_contract(), raw)
    # Frontier is a different producer; do not claim coverage for it here.
    artifacts = {key: value for key, value in artifacts.items()
                 if key in {"react_ecosystem_analysis", "react_runtime_intelligence",
                            "react_compiler_readiness"}}
    assert len(artifacts) == 3
    assert chain._primary_full_contract_check(artifacts)["passed"]
    assert chain._evidence_contract_check(artifacts)["passed"]
    assert chain._runtime_sensitive_proof_check(
        artifacts, chain._runtime_sensitive_dimensions(chain._load_contract())
    )["passed"]

    missing = {key: dict(value) for key, value in artifacts.items()}
    missing["react_compiler_readiness"]["full"] = raw / "never-produced.json"
    assert not chain._primary_full_contract_check(missing)["passed"]
    primary = artifacts["react_runtime_intelligence"]["primary"]
    payload = load_json_file(primary, {})
    del payload["findings"][0]["evidence_ladder"]
    save_json_atomic(primary, payload)
    assert not chain._evidence_contract_check(artifacts)["passed"]


def test_release_fixture_harness_produces_and_binds_advanced_contracts(monkeypatch, tmp_path):
    from tools import validate_react_fixtures as fixtures
    from tools import validate_react_analysis_chain_integrity as chain
    from tools.engines import ai_task_pack_generator as taskpacks
    from tools.core.json_io import load_json_file
    from tools.core.config import save_json_atomic

    original_raw = taskpacks.RAW_DIR
    config = load_json_file(fixtures.FIXTURE_CONFIG_PATH, {})
    bounded = config["bounded_fixture"]["artifacts"]
    selected = {"artifact_contract_checks": [row for row in config["artifact_contract_checks"]
                if row["artifact"] in bounded]}
    monkeypatch.setattr(fixtures, "RAW_DIR", tmp_path / "live")
    monkeypatch.setattr(chain, "RAW_DIR", tmp_path / "live")
    with fixtures.bounded_react_fixture() as raw:
        assert raw != fixtures.RAW_DIR
        checks = fixtures._artifact_contract_checks(selected, raw)
        assert len(checks) == 5
        assert all(row["passed"] for row in checks), checks
        artifacts = chain._react_primary_artifacts(chain._load_contract(), raw)
        advanced = {key: paths for key, paths in artifacts.items()
                    if paths["primary"].name in bounded}
        assert len(advanced) == 4
        assert chain._primary_full_contract_check(advanced)["passed"]
        assert chain._evidence_contract_check(advanced)["passed"]
        assert chain._line_grounding_scope_check(advanced)["passed"]
        assert artifacts["react_frontier_intelligence"]["primary"].parent == raw
        frontier = load_json_file(raw / "react_frontier_intelligence.json", {})
        assert frontier["findings"], "Frontier fixture must exercise analysis, not an empty artifact shell"
        assert not fixtures.RAW_DIR.exists()
        packs = load_json_file(raw / "ai_task_packs.json", {})
        assert packs["summary"]["generated"] == 1
        assert packs["taskpacks"][0]["action"] == "Do Not Import Yet"
        assert not (raw / "ui_smoke_execution.json").exists()
        save_json_atomic(raw / "ai_task_packs.json", {"summary": {}})
        assert not all(row["passed"] for row in fixtures._artifact_contract_checks(selected, raw))
    assert taskpacks.RAW_DIR == original_raw
    assert not raw.exists()
    assert not fixtures.RAW_DIR.exists()
    with pytest.raises(ValueError, match="was not produced"):
        fixtures.react_proof_artifact_path("ai_task_packs.json", tmp_path, None)


def test_release_fixture_producer_failure_restores_boundaries(monkeypatch):
    from tools import validate_react_fixtures as fixtures
    from tools.engines import react_ecosystem_analyzer as ecosystem
    original_raw = ecosystem.RAW_DIR
    monkeypatch.setattr(ecosystem, "run_react_ecosystem_analyzer",
                        Mock(side_effect=RuntimeError("fixture producer failure")))
    with pytest.raises(RuntimeError, match="fixture producer failure"):
        with fixtures.bounded_react_fixture():
            pytest.fail("A failed producer must never yield evidence")
    assert ecosystem.RAW_DIR == original_raw


def test_release_fixture_missing_output_is_not_replaced_by_live_output(monkeypatch, tmp_path):
    from tools import validate_react_fixtures as fixtures
    from tools.engines import ai_task_pack_generator as taskpacks
    from tools.core.config import save_json_atomic
    monkeypatch.setattr(fixtures, "RAW_DIR", tmp_path)
    save_json_atomic(tmp_path / "ai_task_packs.json", {"summary": {"generated": 99}})
    monkeypatch.setattr(taskpacks, "run_ai_task_pack_generator", lambda: {})
    with pytest.raises(ValueError, match="omitted artifacts"):
        with fixtures.bounded_react_fixture():
            pytest.fail("A stale live artifact must not rescue a missing fixture output")


@pytest.mark.parametrize("filename", ["../escape.json", "sub/file.json", "C:\\file.json"])
def test_release_fixture_rejects_unconfined_output_names(monkeypatch, filename):
    from tools import validate_react_fixtures as fixtures
    config = fixtures.load_json_object_strict(fixtures.FIXTURE_CONFIG_PATH, label="fixture test")
    config["bounded_fixture"]["artifacts"] = [filename]
    monkeypatch.setattr(fixtures, "load_json_object_strict", lambda *args, **kwargs: config)
    with pytest.raises(ValueError, match="plain JSON filenames"):
        fixtures._bounded_fixture_config()
