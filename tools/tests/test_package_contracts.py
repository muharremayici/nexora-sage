from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.core.agent_command_contracts import target_package_script_command
from tools.core.package_contracts import build_package_public_contracts
from tools.engines.dead_code_detector import DeadCodeDetector


class PackageContractAtlasTests(unittest.TestCase):
    def test_target_package_commands_preserve_declared_package_manager(self):
        self.assertEqual(
            target_package_script_command({"packageManager": "pnpm@10.26.0"}, ".", "test"),
            'pnpm --dir "." run test',
        )
        self.assertEqual(
            target_package_script_command({"packageManager": "yarn@4.0.0"}, "packages/ui", "lint"),
            'yarn --cwd "packages/ui" run lint',
        )
        self.assertEqual(
            target_package_script_command({}, ".", "build"),
            'npm --prefix "." run build',
        )

    def test_package_entrypoints_are_normalized_for_atlas_storage(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            package_dir = root / "packages" / "ui"
            package_dir.mkdir(parents=True)
            manifest = package_dir / "package.json"
            manifest.write_text(
                json.dumps(
                    {
                        "name": "@example/ui",
                        "exports": {".": "./src/index.ts", "./theme": "./src/theme.ts"},
                        "typesVersions": {"*": {"theme": ["src/theme-augmentation.d.ts"]}},
                        "sideEffects": ["src/register.ts"],
                    }
                ),
                encoding="utf-8",
            )

            contracts = build_package_public_contracts(root, [manifest])

            self.assertEqual(contracts["source"], "package_manifests")
            self.assertEqual(contracts["packages"][0]["manifest"], "packages/ui/package.json")
            self.assertIn("packages/ui/src/index.ts", contracts["entry_patterns"])
            self.assertIn("packages/ui/src/theme-augmentation.d.ts", contracts["entry_patterns"])
            self.assertIn("packages/ui/src/register.ts", contracts["entry_patterns"])

    def test_dead_code_consumes_atlas_contract_without_filesystem_scan(self):
        detector = DeadCodeDetector()
        project_data = {
            "public_contracts": {
                "source": "package_manifests",
                "entry_patterns": ["src/index.ts"],
                "packages": [{"manifest": "package.json"}],
            }
        }

        self.assertTrue(detector._is_package_public_export_surface("MISSING_PROJECT", "src/index.ts", project_data))
        self.assertEqual(detector._package_scan_stats["MISSING_PROJECT"]["source"], "atlas_public_contracts")


if __name__ == "__main__":
    unittest.main()
