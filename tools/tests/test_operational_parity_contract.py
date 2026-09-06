from __future__ import annotations

import unittest
from unittest.mock import patch

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




def test_not_run_parity_report_never_labels_snapshot_pass(monkeypatch):
    written = []
    monkeypatch.setattr(parity, "save_text_atomic", lambda path, text: written.append(text))
    parity._write_report({"summary": {"passed": False, "semantic_parity_status": "NOT_RUN"}, "diff": {}})
    assert "| `semantic_snapshot` | NOT_RUN |" in written[0]
    assert "| `semantic_snapshot` | PASS |" not in written[0]


if __name__ == "__main__":
    unittest.main()
