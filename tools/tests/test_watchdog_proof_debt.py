from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tools.core.watchdog_proof_debt import advance_watchdog_proof_debt, validate_watchdog_proof_debt_policy


class WatchdogProofDebtTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = {
            "pulse_warning_threshold": 5,
            "changed_file_warning_threshold": 20,
            "violation_warning_threshold": 1,
            "reset_artifact_scope": "current_target_output",
            "reset_artifact_ids": ["dead_code", "circular_deps"],
            "missing_reset_artifact_behavior": "debt_due",
            "repo_wide_artifacts_not_proven_by_watchdog": ["dead_code", "circular_deps"],
            "recommended_refresh_modes": ["auto_full"],
            "auto_refresh": {
                "command_by_mode": {"auto_full": ["run", "--full"]},
            },
        }

    def _write_artifacts(self, raw_dir: Path, timestamp: float) -> None:
        for artifact_id in self.policy["reset_artifact_ids"]:
            path = raw_dir / f"{artifact_id}.json"
            path.write_text(json.dumps({"artifact": artifact_id}), encoding="utf-8")
            os.utime(path, (timestamp, timestamp))

    def test_pulse_threshold_accumulates_without_live_violations(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            raw_dir = Path(temp_dir)
            now = datetime.now(timezone.utc)
            self._write_artifacts(raw_dir, (now - timedelta(minutes=5)).timestamp())
            state = None
            for pulse in range(1, 6):
                state = advance_watchdog_proof_debt(
                    state,
                    self.policy,
                    raw_dir=raw_dir,
                    generated_at=(now + timedelta(seconds=pulse)).isoformat(),
                    changed_file_count=1,
                    violation_count=0,
                )
            self.assertEqual(state["status"], "due")
            self.assertIn("pulse_threshold", state["due_reasons"])

    def test_changed_file_threshold_and_violation_threshold_are_independent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            raw_dir = Path(temp_dir)
            now = datetime.now(timezone.utc)
            self._write_artifacts(raw_dir, (now - timedelta(minutes=5)).timestamp())
            changed_due = advance_watchdog_proof_debt(
                None,
                self.policy,
                raw_dir=raw_dir,
                generated_at=now.isoformat(),
                changed_file_count=20,
                violation_count=0,
            )
            violation_due = advance_watchdog_proof_debt(
                None,
                self.policy,
                raw_dir=raw_dir,
                generated_at=now.isoformat(),
                changed_file_count=1,
                violation_count=1,
            )
            self.assertIn("changed_file_threshold", changed_due["due_reasons"])
            self.assertIn("live_violation_threshold", violation_due["due_reasons"])

    def test_fresh_broad_artifacts_reset_previous_debt_window(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            raw_dir = Path(temp_dir)
            now = datetime.now(timezone.utc)
            window_start = now - timedelta(minutes=2)
            self._write_artifacts(raw_dir, (now - timedelta(minutes=1)).timestamp())
            state = advance_watchdog_proof_debt(
                {
                    "pulses_since_broad_refresh": 9,
                    "changed_files_since_broad_refresh": 40,
                    "window_started_at": window_start.isoformat(),
                    "window_started_epoch": window_start.timestamp(),
                },
                self.policy,
                raw_dir=raw_dir,
                generated_at=now.isoformat(),
                changed_file_count=1,
                violation_count=0,
            )
            self.assertTrue(state["broad_refresh_detected"])
            self.assertEqual(state["pulses_since_broad_refresh"], 1)
            self.assertEqual(state["changed_files_since_broad_refresh"], 1)
            self.assertEqual(state["status"], "not_due")

    def test_missing_artifact_and_incomplete_policy_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            raw_dir = Path(temp_dir)
            now = datetime.now(timezone.utc).isoformat()
            missing = advance_watchdog_proof_debt(
                None,
                self.policy,
                raw_dir=raw_dir,
                generated_at=now,
                changed_file_count=1,
                violation_count=0,
            )
            incomplete = advance_watchdog_proof_debt(
                None,
                {},
                raw_dir=raw_dir,
                generated_at=now,
                changed_file_count=1,
                violation_count=0,
            )
            self.assertIn("broad_proof_artifact_missing", missing["due_reasons"])
            self.assertEqual(incomplete["due_reasons"], ["proof_debt_policy_incomplete"])

    def test_explicit_manual_refresh_sequence_is_valid_without_auto_modes(self) -> None:
        policy = dict(self.policy)
        policy.pop("recommended_refresh_modes")
        policy["recommended_refresh_sequence"] = [
            "python -B tools/validate_source_contracts.py",
            "python sage.py run --profile release-deep",
        ]
        policy["auto_refresh"] = {
            "mode": "advisory",
            "allowed_modes": ["advisory"],
            "command_by_mode": {},
        }

        result = validate_watchdog_proof_debt_policy(policy)

        self.assertTrue(result["valid"])
        self.assertEqual(result["invalid_lists"], [])
        self.assertEqual(result["invalid_recommendations"], [])

    def test_refresh_policy_without_mode_or_manual_sequence_fails_closed(self) -> None:
        policy = dict(self.policy)
        policy.pop("recommended_refresh_modes")

        result = validate_watchdog_proof_debt_policy(policy)

        self.assertFalse(result["valid"])
        self.assertIn("recommended_refresh_modes_or_sequence", result["invalid_lists"])


if __name__ == "__main__":
    unittest.main()
