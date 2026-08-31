from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.core.capability_activation import relevant_activation_context
from tools.core.capability_registry import build_agent_capability_map, load_capability_registry
from tools.core.config import CONFIG_DIR
from tools.core.config import CODE_MAPS_DIR
from tools.core.json_io import load_json_object_strict
from tools.core.reality_scope import (
    SAGE_DEVELOPER_PROJECTION_ID,
    SAGE_SELF_TARGET_PROFILE_ID,
    TARGET_REPOSITORY_PROJECTION_ID,
    build_reality_scope_projection,
    build_reality_target_workflow,
    capability_scope_assignments,
    projection_capability_scopes,
    system_scope_ids,
    validate_reality_target_profiles,
)


ROOT = Path(__file__).resolve().parents[2]
PUBLIC_DISTRIBUTION = (ROOT / "PUBLIC_DISTRIBUTION_MANIFEST.json").is_file()


class RealityScopeProjectionTests(unittest.TestCase):
    def test_capability_scope_assignments_are_complete_and_disjoint(self):
        registry = load_capability_registry()
        capability_ids = {
            str(row.get("id"))
            for row in registry.get("capabilities", [])
            if isinstance(row, dict) and row.get("id")
        }
        assignments = capability_scope_assignments()
        assigned_ids = set().union(*assignments.values())

        self.assertEqual(set(assignments), system_scope_ids())
        self.assertEqual(assigned_ids, capability_ids)
        for capability_id in capability_ids:
            self.assertEqual(sum(capability_id in values for values in assignments.values()), 1)

    def test_target_projection_excludes_sage_development_registries_and_capabilities(self):
        projection = build_reality_scope_projection(TARGET_REPOSITORY_PROJECTION_ID)
        capability_ids = {str(row.get("id")) for row in projection.get("capabilities", [])}

        self.assertEqual(projection.get("meta", {}).get("system_scope"), "SAGE_ON_REPOSITORY")
        self.assertNotIn("lessons", projection)
        self.assertNotIn("work_items", projection)
        self.assertNotIn("roadmap_phases", projection)
        self.assertNotIn("release_proof", capability_ids)
        self.assertNotIn("plugin_extension_foundation", capability_ids)
        self.assertNotIn("bidirectional_capability_transfer_matrix", projection)
        self.assertEqual(
            projection.get("scope_boundary", {}).get("excluded_registries"),
            [
                "audit_lesson_registry",
                "sage_work_item_registry",
                "sage_execution_wave_registry",
                "roadmap_phase_registry",
            ],
        )

    def test_developer_projection_owns_bidirectional_transfer_decisions(self):
        if PUBLIC_DISTRIBUTION:
            with self.assertRaisesRegex(ValueError, "private maintainer authority"):
                build_reality_scope_projection(SAGE_DEVELOPER_PROJECTION_ID)
            return
        projection = build_reality_scope_projection(SAGE_DEVELOPER_PROJECTION_ID)
        transfer_rows = projection.get("bidirectional_capability_transfer_matrix", [])

        self.assertTrue(transfer_rows)
        self.assertTrue(all(row.get("transfer_direction") for row in transfer_rows))
        self.assertIn("lessons", projection)
        self.assertIn("work_items", projection)
        self.assertIn("roadmap_phases", projection)
        self.assertNotIn("execution_plan", build_reality_scope_projection(TARGET_REPOSITORY_PROJECTION_ID))

    def test_developer_execution_plan_respects_active_package_lifecycle(self):
        if PUBLIC_DISTRIBUTION:
            with self.assertRaisesRegex(ValueError, "private maintainer authority"):
                build_reality_scope_projection(SAGE_DEVELOPER_PROJECTION_ID)
            return
        projection = build_reality_scope_projection(SAGE_DEVELOPER_PROJECTION_ID)
        execution_plan = projection.get("execution_plan", {})
        active_package = load_json_object_strict(
            CONFIG_DIR / "sage_active_work_package.json",
            label="SAGE active work package",
        ).get("active_package", {})

        if active_package.get("status") == "in_progress":
            self.assertEqual(execution_plan.get("selection_basis"), "active_work_package")
            self.assertEqual(execution_plan.get("current_wave"), active_package.get("execution_wave"))
        else:
            self.assertEqual(execution_plan.get("selection_basis"), "first_open_delivery_wave")
            self.assertEqual(execution_plan.get("current_wave"), execution_plan.get("next_open_delivery_wave"))

    def test_target_agent_capability_map_applies_scope_filter(self):
        target_scopes = projection_capability_scopes(TARGET_REPOSITORY_PROJECTION_ID)
        capability_map = build_agent_capability_map(
            load_capability_registry(),
            allowed_system_scopes=target_scopes,
        )
        capability_ids = {str(row.get("id")) for row in capability_map.get("capabilities", [])}

        self.assertEqual(capability_map.get("summary", {}).get("system_scope_filter"), sorted(target_scopes))
        self.assertIn("agent_surface", capability_ids)
        self.assertNotIn("release_proof", capability_ids)

    def test_target_activation_projection_filters_summary_and_project_rows(self):
        target_scopes = projection_capability_scopes(TARGET_REPOSITORY_PROJECTION_ID)
        plan = {
            "summary": {
                "status": "PASS",
                "enabled_capability_ids": ["agent_surface", "release_proof"],
                "disabled_capability_ids": ["third_party_plugin_runtime"],
            },
            "projects": [
                {
                    "project": "MAIN",
                    "enabled_capabilities": [
                        {"id": "agent_surface", "status": "enabled"},
                        {"id": "release_proof", "status": "enabled"},
                    ],
                    "disabled_capabilities": [],
                }
            ],
        }
        projection = relevant_activation_context(
            plan,
            include_disabled=True,
            allowed_system_scopes=target_scopes,
        )

        self.assertEqual(projection["summary"]["enabled_capability_ids"], ["agent_surface"])
        self.assertEqual(projection["summary"]["disabled_capability_ids"], [])
        self.assertEqual([row["id"] for row in projection["projects"][0]["capabilities"]], ["agent_surface"])

    def test_sage_self_target_workflow_resolves_explicit_root_and_preserves_proof_boundary(self):
        if PUBLIC_DISTRIBUTION:
            with self.assertRaisesRegex(ValueError, "private maintainer authority"):
                build_reality_target_workflow(SAGE_SELF_TARGET_PROFILE_ID)
            return
        workflow = build_reality_target_workflow(SAGE_SELF_TARGET_PROFILE_ID)
        profile = workflow["profile"]

        self.assertEqual(profile["system_scope"], "SAGE_ON_SAGE")
        self.assertEqual(profile["actor_surface"], "sage_maintainer_agent")
        self.assertEqual(profile["analysis_root_token"], workflow["analysis_root"])
        self.assertEqual(profile["watchdog_support"]["status"], "supported_for_isolated_self_target")
        self.assertEqual(profile["watchdog_support"]["session_debt_field"], "sage_self_release_proof_debt")
        self.assertEqual(profile["watchdog_support"]["release_proof_behavior"], "explicit_only_non_recursive")
        self.assertIn("SAGE release readiness", profile["proof_boundary"]["does_not_prove"])
        for row in profile["bootstrap_tools"]:
            self.assertEqual(row["arguments"]["target_root"], workflow["analysis_root"])
        analysis_call = next(row for row in profile["bootstrap_tools"] if row["tool"] == "run_external_target_analysis")
        self.assertTrue(analysis_call["arguments"]["refresh"])
        self.assertTrue(all("\\" not in command for command in profile["validation_commands"]))

    def test_public_distribution_cannot_escalate_target_analysis_to_sage_self(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary) / "PUBLIC_DISTRIBUTION_MANIFEST.json"
            manifest.write_text("{}\n", encoding="utf-8")
            with (
                patch(
                    "tools.core.reality_scope.PUBLIC_DISTRIBUTION_MANIFEST_PATH",
                    manifest,
                ),
                self.assertRaisesRegex(ValueError, "private maintainer authority"),
            ):
                build_reality_target_workflow(SAGE_SELF_TARGET_PROFILE_ID)

    def test_sage_self_target_profile_tools_exist(self):
        if PUBLIC_DISTRIBUTION:
            with self.assertRaisesRegex(ValueError, "private maintainer authority"):
                validate_reality_target_profiles()
            return
        validation = validate_reality_target_profiles()

        self.assertEqual(validation["issues"], [])
        self.assertIn("inspect_file", validation["sage_self_tools"])

    def test_sage_self_watchdog_descriptor_and_mcp_scope_are_isolated(self):
        if PUBLIC_DISTRIBUTION:
            with self.assertRaisesRegex(ValueError, "private maintainer authority"):
                build_reality_target_workflow(SAGE_SELF_TARGET_PROFILE_ID)
            return
        from tools.core.watchdog_target_context import current_watchdog_target_descriptor
        from tools.mcp.server import _watchdog_session_roots

        isolated_raw_dir = (
            CODE_MAPS_DIR / "output" / "external_targets" / "sage_self_fixture" / ".raw"
        )
        with (
            patch(
                "tools.core.watchdog_target_context.DYNAMIC_CONFIG",
                {"_target_root_override": {"enabled": True, "target_root": str(CODE_MAPS_DIR)}},
            ),
            patch("tools.core.watchdog_target_context.RAW_DIR", isolated_raw_dir),
            patch.dict(
                "tools.core.watchdog_target_context.os.environ",
                {
                    "SAGE_ACTOR_PROFILE": "sage_operator_debug",
                    "SAGE_REALITY_TARGET_PROFILE": "sage_self",
                },
                clear=False,
            ),
        ):
            descriptor = current_watchdog_target_descriptor()

        self.assertEqual(descriptor["system_scope"], "SAGE_ON_SAGE")
        self.assertEqual(descriptor["profile_id"], "sage_self")
        self.assertEqual(descriptor["proof_debt_field"], "sage_self_release_proof_debt")
        self.assertEqual(descriptor["release_proof_behavior"], "explicit_only_non_recursive")
        self.assertEqual(
            Path(descriptor["artifact_root"]).resolve(),
            isolated_raw_dir.resolve(),
        )
        self.assertEqual(
            Path(descriptor["proof_authority_root"]).resolve(),
            (CODE_MAPS_DIR / "output" / ".raw").resolve(),
        )
        self.assertNotEqual(
            Path(descriptor["artifact_root"]).resolve(),
            Path(descriptor["proof_authority_root"]).resolve(),
        )

        with (
            patch("tools.mcp.server._ACTIVE_MCP_PROFILE", "sage_operator_debug"),
            patch.dict(
                "tools.mcp.server.os.environ",
                {
                    "SAGE_ACTOR_PROFILE": "sage_operator_debug",
                    "SAGE_REALITY_TARGET_PROFILE": "sage_self",
                },
                clear=False,
            ),
        ):
            raw_dir, reports_dir, resolved_root = _watchdog_session_roots(
                str(CODE_MAPS_DIR), "sage_self"
            )
        self.assertEqual(resolved_root, str(CODE_MAPS_DIR.resolve()))
        self.assertIn("external_targets", str(raw_dir))
        self.assertIn("external_targets", str(reports_dir))
        with self.assertRaises(ValueError):
            _watchdog_session_roots(str(CODE_MAPS_DIR), "")

    def test_installation_root_equality_does_not_auto_escalate_watchdog_authority(self):
        from tools.core.watchdog_target_context import current_watchdog_target_descriptor

        with (
            patch(
                "tools.core.watchdog_target_context.DYNAMIC_CONFIG",
                {"_target_root_override": {"enabled": True, "target_root": str(CODE_MAPS_DIR)}},
            ),
            patch.dict("tools.core.watchdog_target_context.os.environ", {}, clear=True),
        ):
            descriptor = current_watchdog_target_descriptor()

        self.assertEqual(descriptor["system_scope"], "SAGE_ON_REPOSITORY")
        self.assertEqual(descriptor["acquisition_mode"], "EXPLICIT_TARGET")
        self.assertEqual(descriptor["profile_id"], "target_repository_default")
        self.assertFalse(descriptor["proof_debt_field"] == "sage_self_release_proof_debt")

    def test_default_repository_watchdog_descriptor_has_canonical_execution_identity(self):
        from tools.core.watchdog_target_context import current_watchdog_target_descriptor

        with patch("tools.core.watchdog_target_context.DYNAMIC_CONFIG", {}):
            descriptor = current_watchdog_target_descriptor()

        self.assertEqual(descriptor["system_scope"], "SAGE_ON_REPOSITORY")
        self.assertEqual(descriptor["acquisition_mode"], "DEFAULT_WORKSPACE")
        self.assertEqual(descriptor["profile_id"], "target_repository_default")
        self.assertEqual(descriptor["artifact_strategy"], "DEFAULT_WORKSPACE_NAMESPACE")
        self.assertTrue(descriptor["subject_root"])


if __name__ == "__main__":
    unittest.main()
