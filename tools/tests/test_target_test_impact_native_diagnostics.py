from __future__ import annotations

import json
import pytest

from tools.mcp import server
from tools.tests.test_target_test_impact_evidence_boundary import (
    _assert_static_boundary, _canonical_static_parity_sample,
)


@pytest.mark.parametrize("native_result", [
    {"exit_code": 0, "stdout": "tests passed", "stderr": "RuntimeError: storage unavailable"},
    {"exit_code": 0, "stdout": "RuntimeError: storage unavailable", "stderr": ""},
    {"exit_code": 0, "stdout": "tests passed", "stderr": "informational progress"},
    {"exit_code": 1, "stdout": "", "stderr": ""},
])
def test_native_diagnostics_unbound_streams_never_become_static_runtime_proof(native_result):
    from copy import deepcopy
    from tools.core.test_impact_profiles import static_candidate_evidence

    row = {"file": "load.test.ts", "project": "MAIN", "type": "Direct Static Import",
           "confidence": 1.0, "run_command": "pnpm test load.test.ts",
           "candidate_evidence": {"native_diagnostics": "clean", "test_execution": "passed",
                                  "changed_behavior": "confirmed", "native_result": native_result}}
    before = deepcopy(row)
    evidence = static_candidate_evidence(row)
    assert evidence["native_diagnostics"] == "not_ingested"
    _assert_static_boundary({"candidate_evidence": evidence}, "direct_import")
    normalized = server._normalize_test_impact_payload_for_agent({"target": "MAIN::load.ts", "impacted_tests": [row]})
    assert normalized["impacted_tests"][0]["candidate_evidence"] == evidence
    assert server._normalize_test_impact_payload_for_agent(normalized) == normalized
    assert row == before
    assert "Native test stdout/stderr is not ingested here" in normalized["evidence_boundary"]
    brief = server._render_test_impact_brief(normalized)
    assert '"native_diagnostics": "not_ingested"' in brief
    assert '"native_result"' not in brief


@pytest.mark.parametrize("external", [False, True])
def test_native_diagnostics_boundary_reaches_public_sqlite_consumers(tmp_path, monkeypatch, external):
    from tools.core.artifact_validator import validate_payload
    from tools.engines import test_impact_matcher as matcher
    original = matcher.find_impacted_tests

    def legacy_projection(*args, **kwargs):
        result = original(*args, **kwargs)
        for row in result["impacted_tests"]:
            row["candidate_evidence"] = {"native_diagnostics": "clean", "test_execution": "passed",
                                        "changed_behavior": "confirmed"}
        return result

    monkeypatch.setattr(matcher, "find_impacted_tests", legacy_projection)
    payload, brief = _canonical_static_parity_sample(tmp_path, monkeypatch, external)
    assert len(payload["impacted_tests"]) == 3
    assert payload["target_path_status"]["source_snapshot_status"] == "ok"
    assert validate_payload("test_impact_report", payload) == []
    for row in payload["impacted_tests"]:
        assert row["candidate_evidence"]["native_diagnostics"] == "not_ingested"
        assert row["candidate_evidence"]["test_execution"] == "not_run_by_sage"
    assert '"native_diagnostics": "not_ingested"' in brief
    assert "Native test stdout/stderr is not ingested here" in brief


@pytest.mark.parametrize("mutation", ["missing", "clean", "confirmed_error"])
def test_native_diagnostics_artifact_schema_rejects_missing_or_borrowed_status(mutation):
    from tools.core.artifact_validator import validate_payload
    from tools.core.test_impact_profiles import static_candidate_evidence

    row = {"file": "load.test.ts", "project": "MAIN", "type": "Direct Static Import",
           "confidence": 1.0, "run_command": "pnpm test load.test.ts"}
    row["candidate_evidence"] = static_candidate_evidence(row)
    payload = {"target": "MAIN::load.ts", "impacted_tests": [row]}
    assert validate_payload("test_impact_report", payload) == []
    if mutation == "missing":
        row["candidate_evidence"].pop("native_diagnostics")
    else:
        row["candidate_evidence"]["native_diagnostics"] = mutation
    assert validate_payload("test_impact_report", payload)


@pytest.mark.parametrize("format", ["machine", "brief"])
@pytest.mark.parametrize("mutation", ["missing", "clean"])
def test_native_diagnostics_invalid_policy_fails_before_target_lookup(monkeypatch, format, mutation):
    from copy import deepcopy
    from tools.core import test_impact_profiles

    profile = deepcopy(test_impact_profiles.load_test_impact_profiles())
    evidence = profile["evidence_policy"]["unexecuted_evidence"]
    if mutation == "missing":
        evidence.pop("native_diagnostics")
    else:
        evidence["native_diagnostics"] = mutation
    monkeypatch.setattr(test_impact_profiles, "load_test_impact_profiles", lambda: profile)
    def forbidden(*_, **__):
        raise AssertionError("Invalid diagnostic policy must stop before target lookup")
    monkeypatch.setattr(server, "_raw_dir_for_target", forbidden)
    result = json.loads(server.get_test_impact("MAIN::load.ts", format=format))
    assert result["impacted_tests"] == []
    assert "Invalid test impact static evidence policy" in result["error"]
