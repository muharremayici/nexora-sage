import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tools.core import fractal_policy
from tools.engines.fractal_mapper import assess_merge_readiness, deduplicate_candidates


class FractalMergeSafetyTests(unittest.TestCase):
    def tearDown(self):
        fractal_policy.get_module_container.cache_clear()

    def _row(self, **overrides):
        row = {
            "name": "CandidateService",
            "confidence": 0.9,
            "risk": "LOW",
            "target_studio": "writing",
            "target_layer": "application",
            "source_path": "services/CandidateService.ts",
            "source_project": "VARIANT",
            "target_path_suggestion": "src/03-writing/application/CandidateService.ts",
            "fractal_contract_ok": True,
            "delta": 20.0,
            "duplicate_group_id": "",
        }
        row.update(overrides)
        return row

    def test_high_risk_candidate_requires_review_even_with_strong_target_contract(self):
        readiness, reasons = assess_merge_readiness(self._row(risk="HIGH"))

        self.assertEqual(readiness, "manual_review")
        self.assertIn("risk_tier_requires_review", reasons)

    def test_low_confidence_candidate_requires_review(self):
        readiness, reasons = assess_merge_readiness(self._row(confidence=0.4))

        self.assertEqual(readiness, "manual_review")
        self.assertIn("low_target_confidence", reasons)

    def test_platform_contract_exception_cannot_remove_minimum_confidence_floor(self):
        readiness, reasons = assess_merge_readiness(
            self._row(
                confidence=0.4,
                target_studio="platform_core",
                target_layer="application",
                target_path_suggestion="src/platform/analysis/CandidateService.ts",
            )
        )

        self.assertEqual(readiness, "manual_review")
        self.assertIn("low_target_confidence", reasons)

    def test_deduplication_prefers_lower_risk_before_delta(self):
        common = {
            "name": "CandidateService",
            "target_studio": "writing",
            "target_layer": "application",
            "confidence": 0.9,
        }
        low = {**common, "risk": "LOW", "delta": 10.0, "source_project": "LOW_SOURCE"}
        high = {**common, "risk": "HIGH", "delta": 100.0, "source_project": "HIGH_SOURCE"}

        winners, suppressed = deduplicate_candidates([high, low])

        self.assertEqual(winners[0]["source_project"], "LOW_SOURCE")
        self.assertEqual(suppressed[0]["source_project"], "HIGH_SOURCE")

    def test_module_container_is_discovered_below_registered_main_source_root(self):
        with TemporaryDirectory() as temp_dir:
            main_root = Path(temp_dir) / "src"
            (main_root / "domain-modules" / "03-writing").mkdir(parents=True)
            (main_root / "domain-modules" / "04-translation").mkdir(parents=True)
            dynamic = {
                "variations": {"MAIN": "src"},
                "architecture": {"module_root": "."},
            }
            doctrine = {
                "studio_to_module_map": {
                    "writing": "03-writing",
                    "translation": "04-translation",
                }
            }

            with patch.object(fractal_policy, "MAIN_PROJECT_ROOT", main_root), patch.object(
                fractal_policy, "DYNAMIC_CONFIG", dynamic
            ), patch.object(fractal_policy, "DOCTRINE", doctrine):
                fractal_policy.get_module_container.cache_clear()
                self.assertEqual(fractal_policy.get_module_container(), "domain-modules")
                self.assertEqual(
                    fractal_policy.get_main_module_base_prefix(),
                    "src/domain-modules",
                )

    def test_flat_module_layout_does_not_duplicate_main_project_prefix(self):
        with TemporaryDirectory() as temp_dir:
            main_root = Path(temp_dir) / "src"
            (main_root / "03-writing").mkdir(parents=True)
            (main_root / "04-translation").mkdir(parents=True)
            dynamic = {
                "variations": {"MAIN": "src"},
                "architecture": {"module_root": "."},
            }
            doctrine = {
                "studio_to_module_map": {
                    "writing": "03-writing",
                    "translation": "04-translation",
                }
            }

            with patch.object(fractal_policy, "MAIN_PROJECT_ROOT", main_root), patch.object(
                fractal_policy, "DYNAMIC_CONFIG", dynamic
            ), patch.object(fractal_policy, "DOCTRINE", doctrine):
                fractal_policy.get_module_container.cache_clear()
                self.assertEqual(fractal_policy.get_module_container(), ".")
                self.assertEqual(fractal_policy.get_main_module_base_prefix(), "src")


if __name__ == "__main__":
    unittest.main()
