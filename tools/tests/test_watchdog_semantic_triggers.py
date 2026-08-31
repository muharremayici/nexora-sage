from __future__ import annotations

import unittest

from tools.core.watchdog_semantic_triggers import evaluate_watchdog_semantic_trigger


class WatchdogSemanticTriggerTests(unittest.TestCase):
    def test_state_flow_signal_keeps_sensitive_steps(self) -> None:
        atlas = {
            "MAIN": {
                "files": {
                    "src/store.ts": {
                        "features": ["ZustandStore", "Tech:setState"],
                    }
                }
            }
        }

        result = evaluate_watchdog_semantic_trigger(
            "state_flow",
            ["MAIN::src/store.ts"],
            atlas=atlas,
        )

        self.assertTrue(result["relevant"])
        self.assertEqual(result["reason"], "atlas_feature_prefix")
        self.assertEqual(result["step_set"], "state_flow_signal_sensitive")

    def test_unrelated_changed_file_skips_sensitive_steps(self) -> None:
        atlas = {"MAIN": {"files": {"src/styles.css": {"features": ["CSS"]}}}}

        result = evaluate_watchdog_semantic_trigger(
            "state_flow",
            ["MAIN::src/styles.css"],
            atlas=atlas,
        )

        self.assertFalse(result["relevant"])
        self.assertEqual(result["reason"], "no_semantic_signal")
        self.assertEqual(result["step_set"], "state_flow_signal_sensitive")

    def test_unknown_changed_file_keeps_steps_fail_safe(self) -> None:
        result = evaluate_watchdog_semantic_trigger(
            "state_flow",
            ["MAIN::src/not-indexed-yet.ts"],
            atlas={"MAIN": {"files": {}}},
        )

        self.assertTrue(result["relevant"])
        self.assertEqual(result["reason"], "unknown_file_kept_for_safety")


if __name__ == "__main__":
    unittest.main()
