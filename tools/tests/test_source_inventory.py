from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import tools.generate_source_layer_inventory as inventory
from tools.core.json_io import load_json_object_strict


class SourceInventoryTests(unittest.TestCase):
    def test_python_packaging_metadata_is_ignored(self):
        original_root = inventory.ROOT
        try:
            with tempfile.TemporaryDirectory() as tmp:
                inventory.ROOT = Path(tmp)
                metadata = inventory.ROOT / "nexora_sage.egg-info" / "PKG-INFO"
                metadata.parent.mkdir(parents=True)
                metadata.write_text("metadata", encoding="utf-8")
                self.assertTrue(inventory._is_ignored(metadata))
        finally:
            inventory.ROOT = original_root

    def test_source_role_contract_classifies_known_and_unknown_executables(self):
        original_root = inventory.ROOT
        contract = load_json_object_strict(inventory.SOURCE_ROLE_CONTRACT_PATH, label="Source role obligation contract")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                inventory.ROOT = Path(tmp)
                core_file = inventory.ROOT / "tools" / "core" / "new_helper.py"
                unknown_file = inventory.ROOT / "custom_runtime.py"
                generated_file = inventory.ROOT / "output" / "scripts" / "generated.ps1"
                self.assertEqual(inventory.classify_source_role(core_file, contract)[0], "core_service")
                self.assertEqual(inventory.classify_source_role(unknown_file, contract)[0], "undeclared_executable")
                self.assertEqual(
                    inventory.classify_source_role(generated_file, contract, "generated_runtime_artifact")[0],
                    "generated_runtime",
                )
        finally:
            inventory.ROOT = original_root

    def test_source_role_inference_cannot_activate_or_expand_claims(self):
        contract = load_json_object_strict(inventory.SOURCE_ROLE_CONTRACT_PATH, label="Source role obligation contract")
        behavior = contract.get("default_behavior", {})
        self.assertIs(behavior.get("role_inference_grants_activation"), False)
        self.assertIs(behavior.get("role_inference_expands_claims"), False)


if __name__ == "__main__":
    unittest.main()
