from __future__ import annotations

import json
import unittest
from unittest.mock import patch

import tools.core.generated_validation_commands as generated_commands
from tools.core.generated_validation_commands import generated_mutation_post_validation, generated_post_validation_command, generated_post_validation_contract
from tools.engines.large_engine_maintenance import required_large_engine_seams


class GeneratedValidationCommandsTests(unittest.TestCase):
    def test_engine_contract_smoke_has_explicit_narrow_boundary(self) -> None:
        contract = generated_post_validation_contract("engine_contract_smoke")
        self.assertEqual(contract["command"], "python -B -m unittest tools.tests.test_engine_contracts")
        self.assertEqual(contract["execution_scope"], "narrow_generated_script_regression")
        self.assertEqual(contract["full_release_step_id"], "engine_contract_tests")

    def test_large_engine_seams_share_the_canonical_smoke_command(self) -> None:
        command = generated_post_validation_command("engine_contract_smoke")
        seams = required_large_engine_seams()
        self.assertTrue(seams)
        self.assertTrue(all(row["required_regression_gate"] == command for row in seams.values()))

    def test_target_mutation_validation_does_not_leak_sage_self_tests(self) -> None:
        with patch.dict(
            generated_commands.DYNAMIC_CONFIG,
            {"_target_root_override": {"enabled": True}},
            clear=False,
        ):
            guidance = generated_mutation_post_validation()

        self.assertEqual(
            guidance["mode"],
            "mcp_target_repository_default_then_target_repository_commands",
        )
        self.assertNotIn("tools.tests", json.dumps(guidance))
        self.assertEqual(
            [row["tool"] for row in guidance["validation_tools"]],
            ["get_test_impact", "validate_patch", "get_violation_work_queue"],
        )


if __name__ == "__main__":
    unittest.main()
