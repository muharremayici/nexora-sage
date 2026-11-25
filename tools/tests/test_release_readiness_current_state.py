"""Current release-readiness assertions, retaining the public target profile."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.engines.release_readiness_report import build_release_readiness_payload
from tools.generate_nexora_operator_packet import _combined_release_readiness


CODE_MAPS_DIR = Path(__file__).resolve().parents[2]


class ReleaseReadinessContractTests(unittest.TestCase):
    def setUp(self):
        # Unit evidence must not depend on a previous repository analysis.
        self.enterContext(patch("tools.engines.release_readiness_report.load_atlas_data", return_value={"fixture": True}))
        self.enterContext(patch("tools.engines.release_readiness_report.load_genome_data", return_value={"fixture": True}))
        self.enterContext(patch("tools.engines.release_readiness_report._manual_validation_confidence", return_value={"status": "not_available"}))

    def _current_claim(self):
        identity = json.loads((CODE_MAPS_DIR / "config/release_identity.json").read_text(encoding="utf-8"))
        return identity["release_claim"]["allowed"]

    def test_release_claim_must_match_current_identity(self):
        if self._public_maintainer_boundary_is_closed():
            return
        for claim, expected in ((self._current_claim(), True), ("different_fixture_claim", False), ("", False)):
            with self.subTest(claim=claim):
                payload = build_release_readiness_payload({
                    "react_universal_readiness": {"summary": {"universal_ready": True, "allowed_claim": claim}}
                })
                check = next(row for row in payload["checks"] if row["name"] == "react_universal_readiness_pass")
                self.assertEqual(check["passed"], expected)
                self.assertTrue(check["enforced"])
                if not expected:
                    self.assertEqual(payload["readiness"], "NOT_READY")

    def _public_maintainer_boundary_is_closed(self) -> bool:
        if not (CODE_MAPS_DIR / "PUBLIC_DISTRIBUTION_MANIFEST.json").is_file():
            return False
        for relative_path in (
            "config/release_proof_scope_contract.json",
            "config/release_proof_steps_contract.json",
            "tools/run_release_proof_bundle.py",
        ):
            self.assertFalse((CODE_MAPS_DIR / relative_path).exists())
        return True

    def _ready_artifacts(self):
        return {
                "quality_gate": {
                    "release_gate_status": "PASS",
                    "ecosystem_signal_status": "ATTENTION",
                },
                "merge_intelligence_regression": {
                    "summary": {"failed_checks": 0, "total_checks": 1},
                },
                "adapter_registry": {
                    "summary": {"total": 1, "enabled": 1, "valid": 1},
                },
                "merge_decision_cockpit": {
                    "summary": {"candidates": 2, "confidence_tiers": {"high": 1, "medium": 1}},
                },
                "ai_task_packs": {
                    "summary": {"generated": 2},
                },
                "distribution_hardening_validation": {
                    "summary": {"failed_checks": 0, "total_checks": 1},
                },
                "entrypoint_failure_validation": {
                    "summary": {"failed_checks": 0, "total_checks": 1},
                },
                "operational_parity_validation": {
                    "summary": {"passed": True, "changed_sections": 0},
                },
                "performance_budget_validation": {
                    "summary": {"failed_checks": 0, "total_checks": 1},
                },
                "performance_ledger": {
                    "runs": [{"run_id": "test", "repo_band": "L"}],
                },
                "react_fixture_matrix_validation": {
                    "summary": {"failed_checks": 0, "total_checks": 1},
                },
                "react_universal_readiness": {
                    "summary": {
                        "universal_ready": True,
                        "allowed_claim": self._current_claim(),
                    },
                },
                "universal_proof_validation": {
                    "summary": {"failed_checks": 0, "total_checks": 1},
                },
                "mcp_agent_surface_validation": {
                    "summary": {"status": "PASS", "missing_required_tools": []},
                },
                "codemaps_suppressions": {
                    "suppressions": [],
                },
        }

    def test_release_readiness_requires_quality_artifacts_and_regression(self):
        if self._public_maintainer_boundary_is_closed():
            return
        payload = build_release_readiness_payload(self._ready_artifacts(), artifact_errors={})
        self.assertEqual(payload["readiness"], "PRODUCTION_READY")
        self.assertEqual(payload["summary"]["failed"], 0)
        self.assertEqual(payload["summary"]["platform_readiness"], "PRODUCTION_READY")
        self.assertTrue(payload["summary"]["ecosystem_attention"])

    def test_target_repository_debt_does_not_become_sage_release_debt(self):
        if self._public_maintainer_boundary_is_closed():
            return
        artifacts = self._ready_artifacts()
        artifacts["quality_gate"]["release_gate_status"] = "FAIL"
        artifacts["quality_gate"]["scope_gate_status"] = "PASS"
        payload = build_release_readiness_payload(artifacts)
        self.assertEqual(payload["readiness"], "PRODUCTION_READY")
        self.assertEqual(payload["evidence"]["quality_gate"]["release_gate_status"], "FAIL")
        target_check = next(row for row in payload["checks"] if row["name"] == "quality_gate_pass")
        self.assertFalse(target_check["passed"])
        self.assertFalse(target_check["enforced"])

        invalid = build_release_readiness_payload(artifacts, artifact_errors={"quality_gate": ["invalid fixture evidence"]})
        self.assertEqual(invalid["readiness"], "NOT_READY")
        artifacts["mcp_agent_surface_validation"]["summary"]["status"] = "FAIL"
        broken_product = build_release_readiness_payload(artifacts)
        self.assertEqual(broken_product["readiness"], "NOT_READY")

    def test_release_readiness_blocks_missing_mcp_agent_surface(self):
        if self._public_maintainer_boundary_is_closed():
            return
        payload = build_release_readiness_payload(
            {
                "quality_gate": {"release_gate_status": "PASS"},
                "merge_intelligence_regression": {"summary": {"failed_checks": 0, "total_checks": 1}},
                "adapter_registry": {"summary": {"total": 1, "enabled": 1, "valid": 1}},
                "merge_decision_cockpit": {"summary": {"candidates": 0, "confidence_tiers": {}}},
                "ai_task_packs": {"summary": {"generated": 1}},
                "distribution_hardening_validation": {"summary": {"failed_checks": 0, "total_checks": 1}},
                "entrypoint_failure_validation": {"summary": {"failed_checks": 0, "total_checks": 1}},
                "operational_parity_validation": {"summary": {"passed": True, "changed_sections": 0}},
                "performance_budget_validation": {"summary": {"failed_checks": 0}},
                "performance_ledger": {"runs": [{"run_id": "test", "repo_band": "L"}]},
                "react_fixture_matrix_validation": {"summary": {"failed_checks": 0, "total_checks": 1}},
                "react_universal_readiness": {"summary": {"universal_ready": True}},
                "universal_proof_validation": {"summary": {"failed_checks": 0, "total_checks": 1}},
                "mcp_agent_surface_validation": {"summary": {"status": "FAIL", "missing_required_tools": ["get_nexora_brief"]}},
                "codemaps_suppressions": {"suppressions": []},
            },
            artifact_errors={},
        )

        self.assertEqual(payload["readiness"], "NOT_READY")

    def test_release_readiness_blocks_react_universal_failure(self):
        if self._public_maintainer_boundary_is_closed():
            return
        payload = build_release_readiness_payload(
            {
                "quality_gate": {"release_gate_status": "PASS"},
                "merge_intelligence_regression": {"summary": {"failed_checks": 0, "total_checks": 1}},
                "adapter_registry": {"summary": {"total": 1, "enabled": 1, "valid": 1}},
                "merge_decision_cockpit": {"summary": {"candidates": 0, "confidence_tiers": {}}},
                "ai_task_packs": {"summary": {"generated": 1}},
                "distribution_hardening_validation": {"summary": {"failed_checks": 0, "total_checks": 1}},
                "operational_parity_validation": {"summary": {"passed": True, "changed_sections": 0}},
                "performance_budget_validation": {"summary": {"failed_checks": 0}},
                "performance_ledger": {"runs": [{"run_id": "test", "repo_band": "L"}]},
                "react_fixture_matrix_validation": {"summary": {"failed_checks": 0, "total_checks": 1}},
                "react_universal_readiness": {"summary": {"universal_ready": False}},
                "universal_proof_validation": {"summary": {"failed_checks": 0, "total_checks": 1}},
                "codemaps_suppressions": {"suppressions": []},
            },
            artifact_errors={},
        )

        self.assertEqual(payload["readiness"], "NOT_READY")

    def test_release_readiness_blocks_failed_regression(self):
        if self._public_maintainer_boundary_is_closed():
            return
        payload = build_release_readiness_payload(
            {
                "quality_gate": {"release_gate_status": "PASS"},
                "merge_intelligence_regression": {"summary": {"failed_checks": 1}},
                "adapter_registry": {"summary": {"total": 1, "enabled": 1, "valid": 1}},
                "merge_decision_cockpit": {"summary": {"candidates": 0, "confidence_tiers": {}}},
                "ai_task_packs": {"summary": {"generated": 1}},
                "distribution_hardening_validation": {"summary": {"failed_checks": 0}},
                "entrypoint_failure_validation": {"summary": {"failed_checks": 0}},
                "operational_parity_validation": {"summary": {"passed": True, "changed_sections": 0}},
                "performance_budget_validation": {"summary": {"failed_checks": 0}},
                "performance_ledger": {"runs": [{"run_id": "test", "repo_band": "L"}]},
                "react_fixture_matrix_validation": {"summary": {"failed_checks": 0}},
                "react_universal_readiness": {
                    "summary": {
                        "universal_ready": True,
                        "external_fixture_pool_required": False,
                        "allowed_claim": self._current_claim(),
                    }
                },
                "universal_proof_validation": {"summary": {"failed_checks": 0}},
                "mcp_agent_surface_validation": {"summary": {"status": "PASS", "missing_required_tools": []}},
                "codemaps_suppressions": {"suppressions": []},
            },
            artifact_errors={},
        )

        self.assertEqual(payload["readiness"], "NOT_READY")

    def test_release_readiness_blocks_distribution_failure(self):
        if self._public_maintainer_boundary_is_closed():
            return
        payload = build_release_readiness_payload(
            {
                "quality_gate": {"release_gate_status": "PASS"},
                "merge_intelligence_regression": {"summary": {"failed_checks": 0}},
                "adapter_registry": {"summary": {"total": 1, "enabled": 1, "valid": 1}},
                "merge_decision_cockpit": {"summary": {"candidates": 0, "confidence_tiers": {}}},
                "ai_task_packs": {"summary": {"generated": 1}},
                "distribution_hardening_validation": {"summary": {"failed_checks": 1}},
                "entrypoint_failure_validation": {"summary": {"failed_checks": 0}},
                "operational_parity_validation": {"summary": {"passed": True, "changed_sections": 0}},
                "performance_budget_validation": {"summary": {"failed_checks": 0}},
                "performance_ledger": {"runs": [{"run_id": "test", "repo_band": "L"}]},
                "react_fixture_matrix_validation": {"summary": {"failed_checks": 0}},
                "universal_proof_validation": {"summary": {"failed_checks": 0}},
                "codemaps_suppressions": {"suppressions": []},
            },
            artifact_errors={},
        )

        self.assertEqual(payload["readiness"], "NOT_READY")

    def test_release_readiness_blocks_entrypoint_failure(self):
        if self._public_maintainer_boundary_is_closed():
            return
        payload = build_release_readiness_payload(
            {
                "quality_gate": {"release_gate_status": "PASS"},
                "merge_intelligence_regression": {"summary": {"failed_checks": 0}},
                "adapter_registry": {"summary": {"total": 1, "enabled": 1, "valid": 1}},
                "merge_decision_cockpit": {"summary": {"candidates": 0, "confidence_tiers": {}}},
                "ai_task_packs": {"summary": {"generated": 1}},
                "distribution_hardening_validation": {"summary": {"failed_checks": 0}},
                "entrypoint_failure_validation": {"summary": {"failed_checks": 1}},
                "operational_parity_validation": {"summary": {"passed": True, "changed_sections": 0}},
                "performance_budget_validation": {"summary": {"failed_checks": 0}},
                "performance_ledger": {"runs": [{"run_id": "test", "repo_band": "L"}]},
                "react_fixture_matrix_validation": {"summary": {"failed_checks": 0}},
                "universal_proof_validation": {"summary": {"failed_checks": 0}},
                "codemaps_suppressions": {"suppressions": []},
            },
            artifact_errors={},
        )

        self.assertEqual(payload["readiness"], "NOT_READY")

    def test_release_readiness_blocks_failed_operational_parity_when_present(self):
        if self._public_maintainer_boundary_is_closed():
            return
        payload = build_release_readiness_payload(
            {
                "quality_gate": {"release_gate_status": "PASS"},
                "merge_intelligence_regression": {"summary": {"failed_checks": 0}},
                "adapter_registry": {"summary": {"total": 1, "enabled": 1, "valid": 1}},
                "merge_decision_cockpit": {"summary": {"candidates": 0, "confidence_tiers": {}}},
                "ai_task_packs": {"summary": {"generated": 1}},
                "distribution_hardening_validation": {"summary": {"failed_checks": 0}},
                "entrypoint_failure_validation": {"summary": {"failed_checks": 0}},
                "operational_parity_validation": {"summary": {"passed": False, "changed_sections": 1}},
                "performance_budget_validation": {"summary": {"failed_checks": 0}},
                "performance_ledger": {"runs": [{"run_id": "test", "repo_band": "L"}]},
                "react_fixture_matrix_validation": {"summary": {"failed_checks": 0}},
                "universal_proof_validation": {"summary": {"failed_checks": 0}},
                "codemaps_suppressions": {"suppressions": []},
            },
            artifact_errors={},
        )

        self.assertEqual(payload["readiness"], "NOT_READY")

    def test_release_readiness_reports_performance_budget_failure_without_blocking_install(self):
        if self._public_maintainer_boundary_is_closed():
            return
        payload = build_release_readiness_payload(
            {
                "quality_gate": {"release_gate_status": "PASS"},
                "merge_intelligence_regression": {"summary": {"failed_checks": 0, "total_checks": 1}},
                "adapter_registry": {"summary": {"total": 1, "enabled": 1, "valid": 1}},
                "merge_decision_cockpit": {"summary": {"candidates": 0, "confidence_tiers": {}}},
                "ai_task_packs": {"summary": {"generated": 1}},
                "distribution_hardening_validation": {"summary": {"failed_checks": 0, "total_checks": 1}},
                "entrypoint_failure_validation": {"summary": {"failed_checks": 0, "total_checks": 1}},
                "operational_parity_validation": {"summary": {"passed": True, "changed_sections": 0}},
                "performance_budget_validation": {"summary": {"failed_checks": 1}},
                "performance_ledger": {"runs": [{"run_id": "test", "repo_band": "L"}]},
                "react_fixture_matrix_validation": {"summary": {"failed_checks": 0}},
                "react_universal_readiness": {
                    "summary": {
                        "universal_ready": True,
                        "external_fixture_pool_required": False,
                        "allowed_claim": self._current_claim(),
                    }
                },
                "universal_proof_validation": {"summary": {"failed_checks": 0}},
                "mcp_agent_surface_validation": {"summary": {"status": "PASS", "missing_required_tools": []}},
                "codemaps_suppressions": {"suppressions": []},
            },
            artifact_errors={},
        )

        self.assertEqual(payload["readiness"], "PRODUCTION_READY")
        perf_check = next(check for check in payload["checks"] if check["name"] == "performance_budget_pass")
        self.assertFalse(perf_check["enforced"])
        self.assertFalse(perf_check["passed"])

    def test_release_readiness_reports_missing_performance_ledger_without_blocking_install(self):
        if self._public_maintainer_boundary_is_closed():
            return
        payload = build_release_readiness_payload(
            {
                "quality_gate": {"release_gate_status": "PASS"},
                "merge_intelligence_regression": {"summary": {"failed_checks": 0, "total_checks": 1}},
                "adapter_registry": {"summary": {"total": 1, "enabled": 1, "valid": 1}},
                "merge_decision_cockpit": {"summary": {"candidates": 0, "confidence_tiers": {}}},
                "ai_task_packs": {"summary": {"generated": 1}},
                "distribution_hardening_validation": {"summary": {"failed_checks": 0, "total_checks": 1}},
                "entrypoint_failure_validation": {"summary": {"failed_checks": 0, "total_checks": 1}},
                "operational_parity_validation": {"summary": {"passed": True, "changed_sections": 0}},
                "performance_budget_validation": {"summary": {"failed_checks": 0}},
                "performance_ledger": {"runs": []},
                "react_fixture_matrix_validation": {"summary": {"failed_checks": 0}},
                "react_universal_readiness": {
                    "summary": {
                        "universal_ready": True,
                        "external_fixture_pool_required": False,
                        "allowed_claim": self._current_claim(),
                    }
                },
                "universal_proof_validation": {"summary": {"failed_checks": 0}},
                "mcp_agent_surface_validation": {"summary": {"status": "PASS", "missing_required_tools": []}},
                "codemaps_suppressions": {"suppressions": []},
            },
            artifact_errors={},
        )

        self.assertEqual(payload["readiness"], "PRODUCTION_READY")
        ledger_check = next(check for check in payload["checks"] if check["name"] == "performance_ledger_present")
        self.assertFalse(ledger_check["enforced"])
        self.assertFalse(ledger_check["passed"])

    def test_operator_readiness_fails_closed_when_target_quality_gate_fails(self):
        self.assertEqual(
            _combined_release_readiness("PRODUCTION_READY", "FAIL", False),
            "NOT_READY",
        )
        self.assertEqual(
            _combined_release_readiness("PRODUCTION_READY", "PASS", True),
            "PRODUCTION_READY",
        )
        self.assertEqual(
            _combined_release_readiness(None, None, None),
            "INCOMPLETE_EVIDENCE",
        )
        self.assertEqual(
            _combined_release_readiness("PRODUCTION_READY", "FAIL", True),
            "NOT_READY",
        )
