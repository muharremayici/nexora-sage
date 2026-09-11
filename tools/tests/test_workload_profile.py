from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.core.workload_profile import (
    adaptive_timeout_seconds,
    ast_batch_strategy,
    atlas_project_worker_count,
    build_workload_profile,
    dead_code_budget_seconds,
    manual_review_budget_selection,
    oracle_worker_count,
    pipeline_worker_count,
)
from tools.engines.validation_oracle import ValidationOracle
from tools import update_performance_ledger


def _atlas(file_count: int, symbol_count: int, edge_count: int) -> dict:
    files = {
        f"src/file_{index}.ts": {
            "symbols": [{"name": f"symbol_{index}_{item}"} for item in range(symbol_count // max(1, file_count))]
        }
        for index in range(file_count)
    }
    dependencies = {
        f"src/file_{index}.ts": [f"src/dep_{item}.ts" for item in range(edge_count // max(1, file_count))]
        for index in range(file_count)
    }
    return {"MAIN": {"files": files, "dependencies": dependencies}}


class WorkloadProfileTests(unittest.TestCase):
    @staticmethod
    def _manual_review_policy() -> dict:
        return {
            "manual_review_budget": {
                "applicable_project_roles": ["variant"],
                "base_items": 10,
                "per_indexed_project_items": 10,
                "file_bucket": 10,
                "per_file_bucket_items": 2,
                "symbol_bucket": 20,
                "per_symbol_bucket_items": 2,
                "dependency_edge_bucket": 30,
                "per_dependency_edge_bucket_items": 2,
                "per_applicable_analyzer_family_items": 5,
                "analyzer_families": {
                    "target_contract": {
                        "meta_field": "manual_review_target_contract_weak",
                        "applicable_project_roles": ["variant"],
                    }
                },
                "baseline_trend_headroom_ratio": 0.1,
                "baseline_minimum_headroom_items": 5,
                "hard_upper_bound_items": 100,
            }
        }

    def test_profile_is_deterministic_and_scales_from_atlas_dimensions(self):
        small = build_workload_profile(_atlas(10, 20, 30), snapshot_id="one")
        large = build_workload_profile(_atlas(5001, 20000, 35000), snapshot_id="two")

        self.assertEqual(small, build_workload_profile(_atlas(10, 20, 30), snapshot_id="one"))
        self.assertEqual(small["band"], "S")
        self.assertEqual(large["band"], "L")
        self.assertGreater(dead_code_budget_seconds(large), dead_code_budget_seconds(small))
        self.assertGreaterEqual(adaptive_timeout_seconds(large, 180), 180)

    def test_manual_review_budget_scales_with_indexed_role_filtered_workload(self):
        roles = {"MAIN": "host", "VARIANT_A": "variant", "COMPANION": "companion"}
        one_variant = {"VARIANT_A": _atlas(10, 20, 30)["MAIN"]}
        two_variants = {
            **one_variant,
            "VARIANT_B": _atlas(20, 40, 60)["MAIN"],
        }

        small = manual_review_budget_selection(
            one_variant,
            roles,
            {"manual_review_target_contract_weak": 1},
            {},
            policy=self._manual_review_policy(),
        )
        large = manual_review_budget_selection(
            two_variants,
            {**roles, "VARIANT_B": "variant"},
            {"manual_review_target_contract_weak": 1},
            {},
            policy=self._manual_review_policy(),
        )

        self.assertEqual(small["indexed_projects"], ["VARIANT_A"])
        self.assertNotIn("COMPANION", small["applicable_projects"])
        self.assertGreater(large["workload_budget"], small["workload_budget"])
        self.assertIn("sum(workload_components)", small["formula"])

    def test_manual_review_budget_uses_reviewed_trend_envelope_without_accepting_findings(self):
        baseline = {
            "manual_review_budget_baseline": {
                "observed_items": 70,
                "reviewed_suppressions": 10,
                "review_scope": "aggregate_queue_volume_only",
            }
        }
        selection = manual_review_budget_selection(
            {"VARIANT_A": _atlas(1, 1, 1)["MAIN"]},
            {"VARIANT_A": "variant"},
            {},
            baseline,
            policy=self._manual_review_policy(),
        )

        self.assertEqual(selection["baseline_net_items"], 60)
        self.assertEqual(selection["baseline_trend_headroom_items"], 6)
        self.assertEqual(selection["selected_budget"], 66)
        self.assertEqual(selection["baseline_status"], "reviewed_operational_envelope")

    def test_manual_review_budget_hard_ceiling_exposes_and_blocks_genuine_spike(self):
        selection = manual_review_budget_selection(
            {"VARIANT_A": _atlas(1000, 2000, 3000)["MAIN"]},
            {"VARIANT_A": "variant"},
            {"manual_review_target_contract_weak": 200},
            {"manual_review_budget_baseline": {"observed_items": 200, "reviewed_suppressions": 0}},
            policy=self._manual_review_policy(),
        )

        self.assertEqual(selection["selected_budget"], 100)
        self.assertTrue(selection["hard_upper_bound_applied"])
        self.assertGreater(selection["uncapped_budget"], selection["selected_budget"])
        self.assertFalse(101 <= selection["selected_budget"])

    def test_oracle_worker_count_is_bounded_by_policy_and_project_count(self):
        profile = {"counts": {"files": 7000}}
        policy = {
            "oracle": {
                "windows_max_workers": 2,
                "default_max_workers": 4,
                "files_per_worker": 2500,
            }
        }
        workers = oracle_worker_count(profile, 7, policy)
        self.assertGreaterEqual(workers, 1)
        self.assertLessEqual(workers, 4)
        self.assertLessEqual(workers, 7)

    def test_pipeline_worker_count_is_policy_and_cpu_bounded(self):
        policy = {
            "pipeline_workers": {
                "min_workers": 2,
                "windows_max_workers": 4,
                "default_max_workers": 6,
                "cpu_reserve": 1,
            }
        }

        self.assertEqual(pipeline_worker_count(policy, cpu_count=1), 1)
        self.assertEqual(pipeline_worker_count(policy, cpu_count=2), 2)
        self.assertLessEqual(pipeline_worker_count(policy, cpu_count=16), 6)

    def test_atlas_project_workers_are_policy_bounded_and_off_by_default(self):
        disabled_policy = {"atlas": {"project_workers": {"enabled_by_default": False}}}
        self.assertEqual(atlas_project_worker_count(7, total_files=5000, policy=disabled_policy, cpu_count=16), 1)

        enabled_policy = {
            "atlas": {
                "project_workers": {
                    "enabled_by_default": True,
                    "min_projects_for_parallel": 3,
                    "files_per_worker": 2000,
                    "windows_max_workers": 2,
                    "default_max_workers": 3,
                    "cpu_reserve": 1,
                }
            }
        }
        workers = atlas_project_worker_count(7, total_files=5000, policy=enabled_policy, cpu_count=8)
        self.assertGreaterEqual(workers, 1)
        self.assertLessEqual(workers, 3)
        self.assertLessEqual(workers, 7)

    def test_ast_batch_strategy_is_policy_driven_and_override_clamped(self):
        policy = {
            "atlas": {
                "ast_batch": {
                    "adaptive_by_default": True,
                    "safe_worker_cap": 4,
                    "cpu_reserve": 0,
                    "default_chunk_size": 24,
                    "session_pool_enabled_by_default": True,
                    "session_pool_min_files": 48,
                    "session_worker_max_requests": 200,
                    "session_worker_max_rss_bytes": 805306368,
                    "session_worker_estimated_rss_bytes": 536870912,
                    "session_pool_memory_fraction": 0.30,
                    "session_pool_host_memory_reserve_bytes": 1073741824,
                    "large_file_threshold": 1200,
                    "large_chunk_size": 12,
                    "large_cpu_divisor": 2,
                    "medium_file_threshold": 800,
                    "medium_chunk_size": 14,
                    "medium_cpu_divisor": 2,
                    "small_file_threshold": 400,
                    "small_chunk_size": 18,
                    "small_cpu_divisor": 2,
                    "tiny_file_threshold": 160,
                    "tiny_chunk_size": 20,
                    "tiny_cpu_divisor": 2,
                }
            }
        }
        strategy = ast_batch_strategy(
            900,
            1,
            policy=policy,
            cpu_count=8,
            physical_memory_bytes=16 * 1024**3,
        )
        self.assertEqual(strategy["chunk_size"], 14)
        self.assertEqual(strategy["workers"], 4)
        self.assertTrue(strategy["adaptive_mode"])
        self.assertTrue(strategy["session_pool_enabled"])
        self.assertEqual(strategy["session_worker_max_requests"], 200)

        project_parallel_strategy = ast_batch_strategy(
            900,
            2,
            policy=policy,
            cpu_count=8,
            physical_memory_bytes=16 * 1024**3,
        )
        self.assertEqual(project_parallel_strategy["workers"], 1)

        override_strategy = ast_batch_strategy(
            900,
            1,
            policy=policy,
            cpu_count=8,
            physical_memory_bytes=16 * 1024**3,
            env_workers=12,
        )
        self.assertEqual(override_strategy["workers"], 4)
        self.assertTrue(override_strategy["worker_override_clamped"])

        legacy_strategy = ast_batch_strategy(
            900,
            1,
            policy=policy,
            cpu_count=8,
            physical_memory_bytes=16 * 1024**3,
            force_legacy=True,
        )
        self.assertFalse(legacy_strategy["session_pool_enabled"])

        low_memory_strategy = ast_batch_strategy(
            900,
            1,
            policy=policy,
            cpu_count=8,
            physical_memory_bytes=4 * 1024**3,
        )
        self.assertEqual(low_memory_strategy["workers"], 1)
        self.assertTrue(low_memory_strategy["session_pool_memory_clamped"])


class ValidationOraclePreflightTests(unittest.TestCase):
    def test_missing_local_compiler_skips_hydration(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            oracle = ValidationOracle(temp_dir)
            oracle.sanctuary_root = Path(temp_dir)
            (Path(temp_dir) / "EXAMPLE").mkdir()
            preflight = {
                "applicable": False,
                "reason": "local TypeScript executable not found",
                "source_root": temp_dir,
                "node_modules_root": "",
                "command": [],
            }
            with (
                patch.object(oracle, "preflight_project", return_value=preflight),
                patch.object(oracle, "_hydrate_sanctuary_from_variation") as hydrate,
                patch.object(oracle, "_save_report"),
            ):
                report = oracle.validate_project("EXAMPLE")

            self.assertEqual(report["status"], "NOT_APPLICABLE")
            self.assertTrue(report["sanctuary_hydration"]["skipped"])
            hydrate.assert_not_called()


class PerformanceLedgerFailureTests(unittest.TestCase):
    def test_performance_ledger_preserves_physical_atlas_subprofiles(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            perf_path = root / "performance_budget_validation.json"
            ledger_path = root / "performance_ledger.json"
            report_path = root / "performance_ledger.md"
            perf_path.write_text(
                json.dumps(
                    {
                        "summary": {"failed_checks": 0},
                        "budgets": {"selected_session_mode": "normal_full"},
                        "metrics": {
                            "pipeline_total_seconds": 10.0,
                            "physical_latest_session_mode": "watchdog_save_pulse",
                            "performance_evidence_status": "stale_after_non_release_activity",
                            "physical_atlas_phase_timings": {"validate": 3.687, "commit": 3.425},
                            "physical_atlas_state_payload_profile": {"state_payload_bytes": 44729565},
                            "physical_atlas_ast_lifecycle_profile": {
                                "process_starts": 1670,
                                "fallback_process_starts": 3,
                            },
                        },
                        "checks": [],
                    }
                ),
                encoding="utf-8",
            )
            with (
                patch.object(update_performance_ledger, "PERF_VALIDATION_PATH", perf_path),
                patch.object(update_performance_ledger, "LEDGER_JSON_PATH", ledger_path),
                patch.object(update_performance_ledger, "LEDGER_REPORT_PATH", report_path),
            ):
                result = update_performance_ledger.run_update("L", "watchdog-profile", "profile-run")
                update_performance_ledger.run_update("L", "watchdog-profile-rerun", "profile-run")

            self.assertTrue(result["ok"])
            row = json.loads(ledger_path.read_text(encoding="utf-8"))["runs"][0]
            self.assertEqual(row["physical_atlas_phase_timings"]["validate"], 3.687)
            self.assertEqual(row["physical_atlas_state_payload_profile"]["state_payload_bytes"], 44729565)
            self.assertEqual(row["physical_atlas_ast_lifecycle_profile"]["process_starts"], 1670)
            self.assertEqual(row["budget_authority_session_mode"], "normal_full")
            self.assertEqual(row["physical_latest_session_mode"], "watchdog_save_pulse")
            self.assertFalse(row["physical_profile_is_budget_authority"])
            self.assertEqual(row["performance_evidence_status"], "stale_after_non_release_activity")

            rerun_rows = json.loads(ledger_path.read_text(encoding="utf-8"))["runs"]
            self.assertEqual([item["run_id"] for item in rerun_rows].count("profile-run"), 1)

    def test_failed_budget_run_is_recorded_instead_of_discarded(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            perf_path = root / "performance_budget_validation.json"
            ledger_path = root / "performance_ledger.json"
            report_path = root / "performance_ledger.md"
            perf_path.write_text(
                json.dumps(
                    {
                        "summary": {"failed_checks": 1},
                        "metrics": {"pipeline_total_seconds": 10.0, "dead_code_step_seconds": 8.0},
                        "checks": [{"name": "dead_code_step_budget", "passed": False}],
                    }
                ),
                encoding="utf-8",
            )
            with (
                patch.object(update_performance_ledger, "PERF_VALIDATION_PATH", perf_path),
                patch.object(update_performance_ledger, "LEDGER_JSON_PATH", ledger_path),
                patch.object(update_performance_ledger, "LEDGER_REPORT_PATH", report_path),
            ):
                result = update_performance_ledger.run_update("L", "regression", "failed-run")

            self.assertTrue(result["ok"])
            self.assertTrue(result["recorded_with_budget_failure"])
            payload = json.loads(ledger_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["runs"][0]["budget_status"], "FAIL")
            self.assertEqual(payload["runs"][0]["failed_check_names"], ["dead_code_step_budget"])
            self.assertEqual(payload["summary"]["failed_runs"], 1)


if __name__ == "__main__":
    unittest.main()
