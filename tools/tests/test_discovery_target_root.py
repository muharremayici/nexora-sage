from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.orchestrators.discovery import resolve_discovery_root, workspace_root_reference
from tools import validate_discovery_universality as validator


class DiscoveryTargetRootTests(unittest.TestCase):
    def test_bounded_presence_keeps_unknown_and_path_safety_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            installation = root / "sage"
            installation.mkdir()
            config = {
                "variations": {"MAIN": "src", "ABSENT": "missing"},
                "project_roles": {"MAIN": "host"},
            }
            with (
                patch.object(validator, "ANALYSIS_ROOT", root),
                patch.object(validator, "CODE_MAPS_DIR", installation),
                patch.object(validator, "DYNAMIC_CONFIG", config),
                patch.object(validator, "save_json_atomic"),
                patch.object(validator, "save_text_atomic"),
            ):
                bounded = validator._declared_project_root_details("MAIN")
                self.assertEqual(bounded["missing"], [])
                self.assertEqual(bounded["checked_project_keys"], ["MAIN"])
                self.assertEqual(bounded["excluded_from_presence_check"], ["ABSENT"])
                self.assertEqual(validator.run_validation("MAIN")["summary"]["failed_checks"], 0)
                self.assertEqual(validator.run_validation()["summary"]["failed_checks"], 1)
                self.assertEqual(validator._declared_project_root_details()["missing"][0]["project"], "ABSENT")
                unknown = validator._declared_project_root_details("MAIN,TYPO")
                self.assertEqual(unknown["unavailable_requested_projects"], ["TYPO"])
                self.assertEqual(validator.run_validation("MAIN,TYPO")["summary"]["failed_checks"], 1)
                config["variations"]["ESCAPE"] = "../outside"
                config["variations"]["INTERNAL"] = "sage"
                unsafe = validator._declared_project_root_details("MAIN")
                self.assertEqual(unsafe["escaping"][0]["project"], "ESCAPE")
                self.assertEqual(unsafe["inside_sage_workspace"][0]["project"], "INTERNAL")
                self.assertEqual(validator.run_validation("MAIN")["summary"]["failed_checks"], 1)


    def test_explicit_target_root_overrides_installation_parent(self):
        with tempfile.TemporaryDirectory() as install_tmp, tempfile.TemporaryDirectory() as target_tmp:
            install_root = Path(install_tmp)
            target_root = Path(target_tmp)

            resolved = resolve_discovery_root(
                code_maps_dir=install_root,
                environment={"CODEMAPS_TARGET_ROOT": str(target_root)},
            )

        self.assertEqual(resolved, target_root.resolve())

    def test_public_projection_requires_explicit_target_root(self):
        with tempfile.TemporaryDirectory() as install_tmp:
            install_root = Path(install_tmp)
            (install_root / "PUBLIC_DISTRIBUTION_MANIFEST.json").write_text("{}", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "requires an explicit target repository"):
                resolve_discovery_root(code_maps_dir=install_root, environment={})

    def test_internal_legacy_layout_retains_parent_default_without_public_marker(self):
        with tempfile.TemporaryDirectory() as parent_tmp:
            install_root = Path(parent_tmp) / "sage-install"
            install_root.mkdir()

            resolved = resolve_discovery_root(code_maps_dir=install_root, environment={})

        self.assertEqual(resolved, install_root.parent.resolve())

    def test_workspace_reference_is_relative_to_installation(self):
        with tempfile.TemporaryDirectory() as parent_tmp:
            parent = Path(parent_tmp)
            install_root = parent / "sage-install"
            target_root = parent / "target-repository"
            install_root.mkdir()
            target_root.mkdir()

            reference = workspace_root_reference(target_root, code_maps_dir=install_root)

        self.assertEqual(reference, "../target-repository")


if __name__ == "__main__":
    unittest.main()
