from __future__ import annotations

import unittest

from tools.core.artifact_registry import ARTIFACT_SCHEMAS
from tools.core.artifact_validator import validate_against_schema


class ReactEvidenceArtifactSchemaTests(unittest.TestCase):
    def test_react_claim_artifacts_use_dedicated_schemas(self) -> None:
        expected = {
            "fixture_isolation_validation": "fixture_isolation_validation.schema.json",
            "react_fixture_family_taxonomy_report": "react_fixture_family_taxonomy_report.schema.json",
            "react_v11_contract_validation": "react_v11_contract_validation.schema.json",
            "react_universal_readiness": "react_universal_readiness.schema.json",
        }
        for artifact_id, filename in expected.items():
            self.assertEqual(ARTIFACT_SCHEMAS[artifact_id].name, filename)

    def test_universal_readiness_schema_rejects_missing_claim_boolean(self) -> None:
        payload = {"meta": {"kind": "react_universal_readiness", "version": "v1", "generated_at": "now", "taxonomy": "config/taxonomy.json", "evidence_model": "fixture_contracts"}, "summary": {"status": "PASS", "total_families": 1, "release_families": 1, "proven_release_families": 1, "included_releases": ["1.0.0"], "certification_level": "static", "allowed_claim": "claim", "release_identity_claim": "claim", "fixture_coverage_complete": True, "claim_policy_aligned": True, "missing_release_families": 0, "failed_validation_artifacts": 0}, "doctrine": {"path": "config/doctrine.json", "core_claim": "claim", "runtime_boundary": "static_only"}, "missing_release_families": [], "validation_artifacts": [], "families": []}
        errors = validate_against_schema(ARTIFACT_SCHEMAS["react_universal_readiness"], "react_universal_readiness", payload)
        self.assertTrue(any("universal_ready" in error for error in errors))

    def test_operational_parity_schema_allows_stale_baseline_after_null(self) -> None:
        payload = {"meta": {"kind": "operational_parity_validation", "version": "v1"}, "summary": {"passed": False, "changed_sections": 0}, "before": {}, "after": None, "diff": {}}
        self.assertFalse(validate_against_schema(ARTIFACT_SCHEMAS["operational_parity_validation"], "operational_parity_validation", payload))


if __name__ == "__main__":
    unittest.main()
