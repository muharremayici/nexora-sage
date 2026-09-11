from __future__ import annotations

import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tools import validate_operational_parity as parity


class OperationalParityContractTests(unittest.TestCase):
    def _run_with(self, lifecycle_passed: bool) -> dict:
        with (
            patch.object(parity, "_run_cached_full_pipeline") as cached_run,
            patch.object(parity, "_semantic_snapshot", side_effect=[{"ai_context": {"audit_total": 7}}, {"ai_context": {"audit_total": 7}}]),
            patch.object(
                parity,
                "_refresh_lifecycle_evidence",
                side_effect=[
                    {"stage": "before_cached_run", "passed": lifecycle_passed, "returncode": 0 if lifecycle_passed else 1, "total_checks": 48, "failed_checks": 0 if lifecycle_passed else 1},
                    {"stage": "after_cached_run", "passed": lifecycle_passed, "returncode": 0 if lifecycle_passed else 1, "total_checks": 48, "failed_checks": 0 if lifecycle_passed else 1},
                ],
            ),
        ):
            payload = parity.run_validation()
            payload["_cached_replay_called"] = cached_run.called
            return payload

    def test_stable_replay_with_stale_lifecycle_cannot_pass(self) -> None:
        payload = self._run_with(lifecycle_passed=False)
        self.assertFalse(payload["summary"]["passed"])
        self.assertEqual("NOT_RUN_STALE_BASELINE", payload["summary"]["semantic_parity_status"])
        self.assertEqual("FAIL", payload["summary"]["semantic_freshness_status"])
        self.assertEqual("not_run_stale_baseline", payload["summary"]["cached_replay_status"])
        self.assertFalse(payload["_cached_replay_called"])
        self.assertIn("execution_timing_guidance", payload)

    @patch.object(parity, "local_duration_guidance")
    def test_unavailable_timing_guidance_does_not_change_parity_semantics(self, duration_guidance) -> None:
        duration_guidance.return_value = {"status": "unavailable", "missing_phases": ["cached_replay"]}
        payload = self._run_with(lifecycle_passed=True)
        self.assertTrue(payload["summary"]["passed"])
        self.assertEqual("unavailable", payload["execution_timing_guidance"]["status"])

    def test_stable_replay_with_fresh_lifecycle_passes(self) -> None:
        payload = self._run_with(lifecycle_passed=True)
        self.assertTrue(payload["summary"]["passed"])
        self.assertEqual("PASS", payload["summary"]["semantic_parity_status"])
        self.assertEqual("PASS", payload["summary"]["semantic_freshness_status"])
        self.assertTrue(payload["_cached_replay_called"])

    @patch.object(parity, "load_json_file", return_value={"summary": {"total_checks": 48, "failed_checks": 0}})
    @patch.object(parity, "run_observed_subprocess")
    def test_lifecycle_evidence_treats_zero_failed_checks_as_pass(self, observed_subprocess, _load_json) -> None:
        observed_subprocess.return_value = (type("Result", (), {"returncode": 0})(), 0.25)
        evidence = parity._refresh_lifecycle_evidence("unit")
        self.assertTrue(evidence["passed"])
        self.assertEqual(0, evidence["failed_checks"])


@pytest.mark.parametrize("version", ["1.0.4", "1.0.5"])
def test_release_replay_reuses_central_commands_independent_of_version(version, monkeypatch):
    scope = parity.load_release_proof_scope_contract()
    configured_steps = parity.load_release_proof_steps()
    for step in configured_steps:
        step["command"] = [f"C:/release-{version}/sage.py" if arg.endswith("sage.py") else arg
                           for arg in step["command"]]
    monkeypatch.setattr(parity, "load_release_proof_steps", lambda: configured_steps)
    commands = parity._release_replay_commands(scope)
    live = scope["live_repository_execution"]
    assert "--force" not in commands[0]
    assert f"C:/release-{version}/sage.py" in commands[0]
    assert commands[0][commands[0].index("--profile") + 1] == live["required_execution_profile"]
    assert all(command[command.index("--projects") + 1] == live["required_project_filter"][0]
               for command in commands)
    steps = {row["id"]: row for row in parity.load_release_proof_steps()}
    assert commands[0] == [arg for arg in steps[live["step_id"]]["command"] if arg != "--force"]
    assert commands[1] == steps[scope["final_governance_execution"]["step_id"]]["command"]
    assert "--release-scope" in steps["operational_parity"]["command"]


@pytest.mark.parametrize("mode", ["stable_target_fail", "stale_before", "stale_after", "changed", "replay_failure"])
def test_bounded_release_parity_preserves_failure_authority(monkeypatch, mode):
    before = {"atlas_snapshot_id": "snapshot-a", "quality_gate": {"passed": False}}
    after = deepcopy(before)
    if mode == "changed":
        after["atlas_snapshot_id"] = "snapshot-b"
    with (
        patch.object(parity, "_release_snapshot", side_effect=[
            (before, {"passed": mode != "stale_before"}),
            (after, {"passed": mode != "stale_after"}),
        ]),
        patch.object(parity, "run_observed_subprocess", return_value=(
            SimpleNamespace(returncode=1 if mode == "replay_failure" else 0, stdout="", stderr="failure"), 0.1
        )) as run,
        patch.object(parity, "_refresh_lifecycle_evidence") as legacy,
    ):
        monkeypatch.delenv("CODEMAPS_INSIDE_PIPELINE", raising=False)
        payload = parity.run_validation(release_scope=True)
    assert payload["summary"]["passed"] == (mode == "stable_target_fail")
    assert payload["before"]["quality_gate"]["passed"] is False
    assert run.call_count == (0 if mode == "stale_before" else 1 if mode == "replay_failure" else 2)
    legacy.assert_not_called()
    from tools.core.artifact_registry import ARTIFACT_SCHEMAS
    from tools.core.artifact_validator import validate_against_schema
    assert not validate_against_schema(ARTIFACT_SCHEMAS["operational_parity_validation"], "operational_parity_validation", payload)


@pytest.mark.parametrize("failure", [None, "stale", "schema", "missing", "scope"])
def test_release_snapshot_checks_lineage_schema_presence_and_scope(monkeypatch, failure):
    scope = parity.load_release_proof_scope_contract()
    paths = scope["operational_parity_execution"]["semantic_paths"]
    payloads = {}
    for artifact, fields in paths.items():
        payload = {}
        for field in fields:
            cursor = payload
            parts = field.split(".")
            for part in parts[:-1]:
                cursor = cursor.setdefault(part, {})
            cursor[parts[-1]] = 0
        payloads[artifact] = payload
    if failure == "missing":
        payloads["quality_gate"].pop("passed")
    monkeypatch.setattr(parity, "load_atlas_commit", lambda root: {
        "snapshot_id": "current", "state": "complete",
        "projects": ["WRONG"] if failure == "scope" else scope["live_repository_execution"]["required_project_filter"],
    })
    monkeypatch.setattr(parity, "load_json_file", lambda path, default: payloads[path.stem])
    monkeypatch.setattr(parity, "validate_against_schema", lambda *args: ["invalid"] if failure == "schema" else [])
    monkeypatch.setattr(parity, "receipt_binding", lambda **kwargs: (
        "MISMATCH" if failure == "stale" else "BOUND", "current", []
    ))
    snapshot, evidence = parity._release_snapshot(scope, "unit")
    assert evidence["passed"] == (failure is None)
    assert snapshot["atlas_snapshot_id"] == "current"


def test_release_replay_refuses_unscoped_producer(monkeypatch):
    scope = parity.load_release_proof_scope_contract()
    steps = parity.load_release_proof_steps()
    next(step for step in steps if step["id"] == scope["live_repository_execution"]["step_id"])["command"] = ["python", "sage.py", "run", "--full"]
    monkeypatch.setattr(parity, "load_release_proof_steps", lambda: steps)
    with pytest.raises(ValueError, match="project scope"):
        parity._release_replay_commands(scope)


def test_not_run_parity_report_never_labels_snapshot_pass(monkeypatch):
    written = []
    monkeypatch.setattr(parity, "save_text_atomic", lambda path, text: written.append(text))
    parity._write_report({"summary": {"passed": False, "semantic_parity_status": "NOT_RUN"}, "diff": {}})
    assert "| `semantic_snapshot` | NOT_RUN |" in written[0]
    assert "| `semantic_snapshot` | PASS |" not in written[0]


if __name__ == "__main__":
    unittest.main()
