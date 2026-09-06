from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tools.core.watchdog_semantic_triggers import evaluate_watchdog_semantic_trigger


class WatchdogSemanticTriggerTests(unittest.TestCase):
    def test_surgical_selection_uses_explicit_positive_and_negative_atlas(self) -> None:
        from tools.orchestrators.orchestrator import build_step_catalog, select_steps_smart

        args = SimpleNamespace(step=None, from_step=None, skip_audit=False,
                               full=True, force=False, projects=None, scope=None,
                               target=None, ai_context=False, smart_trigger=True,
                               watchdog_profile="live")
        catalog = build_step_catalog(args, stale_projects=["MAIN"])
        with patch("tools.orchestrators.orchestrator.apply_capability_activation",
                   side_effect=lambda selected, *a, **k: selected), patch(
            "tools.core.watchdog_semantic_triggers.load_atlas_data",
            side_effect=AssertionError("Fixture must not read live Atlas"),
        ):
            for features, expected in [(["ZustandStore"], True), ([], False)]:
                with self.subTest(features=features):
                    selected = select_steps_smart(
                        catalog, args, changed_files=["MAIN::App.tsx"],
                        dna_changed_files=["MAIN::App.tsx"], should_run_heavy=True,
                        atlas={"MAIN": {"files": {"App.tsx": {"features": features}}}},
                    )
                    self.assertEqual("State Flow Scanner" in {s["name"] for s in selected}, expected)

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
