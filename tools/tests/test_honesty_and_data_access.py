from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.core.source_evidence import atlas_file_paths, read_atlas_bound_source
from tools.core.doctrine_contract import require_doctrine_mapping
from tools.doctrine_compiler import _validate_exclusive_contract_owners
from tools.validate_engine_data_access import validate_engine_data_access
from tools.validate_engine_data_access import POLICY_PATH


class SourceEvidenceTests(unittest.TestCase):
    def test_doctrine_compiler_rejects_exclusive_contract_in_non_owner_pack(self):
        manifest = {
            "validation_contract": {"exclusive_contract_owners": {"shared_rule": "owner"}},
        }
        pack_payloads = [
            ("owner", {"shared_rule": {"enabled": True}}),
            ("intruder", {"nested": {"shared_rule": {"enabled": False}}}),
        ]

        with self.assertRaisesRegex(ValueError, "ownership violation for shared_rule"):
            _validate_exclusive_contract_owners(manifest, pack_payloads)

    def test_required_doctrine_mapping_is_recursive_and_fail_closed(self):
        confidence = require_doctrine_mapping("confidence_policy")
        self.assertEqual(confidence.get("base_dead_confidence", 0.1), 0.97)
        with self.assertRaisesRegex(ValueError, "confidence_policy.missing_threshold"):
            confidence.get("missing_threshold", 0.1)
        waves = require_doctrine_mapping("audit_remediation_policy").get("waves")
        self.assertEqual(
            waves.get_or_contract_default("unknown_rule").get("wave"),
            waves.get("default").get("wave"),
        )
        source_role_policy = require_doctrine_mapping("dead_code_heuristics").get("assembly_governance").get("source_role_policy")
        self.assertEqual(source_role_policy.get("allow_companion_donor_projects"), [])

    def test_atlas_bound_source_prefers_sqlite_snapshot_without_live_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with patch("tools.core.source_evidence.load_source_text", return_value="snapshot truth") as load:
                result = read_atlas_bound_source(
                    component="test",
                    project="MAIN",
                    project_root=root,
                    rel_path="src/missing.ts",
                    atlas_entry={"hash": hashlib.sha256(b"snapshot truth").hexdigest()},
                    reason="fixture",
                )

            self.assertEqual(result, "snapshot truth")
            load.assert_called_once_with(
                "MAIN",
                "src/missing.ts",
                fallback_path=(root / "src" / "missing.ts").resolve(),
                component="test",
                allow_live_fallback=False,
            )

    def test_atlas_file_universe_and_hash_guard(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "src" / "route.tsx"
            source.parent.mkdir(parents=True)
            source.write_text("export const route = '/ok'", encoding="utf-8")
            content = source.read_text(encoding="utf-8")
            atlas = {"files": {"src/route.tsx": {"hash": hashlib.sha256(source.read_bytes()).hexdigest()}}}
            self.assertEqual(atlas_file_paths(atlas, {".tsx"}), ["src/route.tsx"])
            self.assertEqual(
                read_atlas_bound_source(
                    component="test",
                    project="TEST",
                    project_root=root,
                    rel_path="src/route.tsx",
                    atlas_entry=atlas["files"]["src/route.tsx"],
                    reason="fixture",
                ),
                content,
            )
            source.write_text("changed", encoding="utf-8")
            self.assertIsNone(
                read_atlas_bound_source(
                    component="test",
                    project="TEST",
                    project_root=root,
                    rel_path="src/route.tsx",
                    atlas_entry=atlas["files"]["src/route.tsx"],
                    reason="fixture",
                )
            )

    def test_repository_traversal_is_declared(self):
        result = validate_engine_data_access()
        self.assertEqual(result["summary"]["status"], "PASS")
        self.assertEqual(result["summary"]["undeclared"], 0)

    def _runtime_inventory_fixture(self, *, remove_declaration=False, unknown_sibling=False):
        relative = "tools/core/distribution_test_unit_pilot.py"
        policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
        source = (POLICY_PATH.parents[1] / relative).read_text(encoding="utf-8")
        if remove_declaration:
            policy["filesystem_inventory_allowlist"].pop(relative, None)
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / relative
            target.parent.mkdir(parents=True)
            target.write_text(source, encoding="utf-8")
            if unknown_sibling:
                (target.parent / "undeclared_inventory.py").write_text(
                    "from pathlib import Path\nPath('.').rglob('*')\n", encoding="utf-8"
                )
            policy_path = root / "policy.json"
            policy_path.write_text(json.dumps(policy), encoding="utf-8")
            with patch("tools.validate_engine_data_access.CODE_MAPS_DIR", root), \
                    patch("tools.validate_engine_data_access.POLICY_PATH", policy_path), \
                    patch("tools.validate_engine_data_access.save_json_atomic"), \
                    patch("tools.validate_engine_data_access.save_text_atomic"):
                return validate_engine_data_access()

    def test_diagnostic_runtime_inventory_has_explicit_identity_role(self):
        result = self._runtime_inventory_fixture()
        self.assertEqual(result["summary"]["status"], "PASS")
        self.assertEqual(len(result["findings"]), 3)
        self.assertEqual({row["kind"] for row in result["findings"]},
                         {"filesystem_walk", "filesystem_glob", "filesystem_rglob"})
        self.assertTrue(all(row["declared"] and row["access_class"] == "source_governance_identity"
                            for row in result["findings"]))

    def test_missing_runtime_inventory_declaration_still_fails_closed(self):
        result = self._runtime_inventory_fixture(remove_declaration=True)
        self.assertEqual(result["summary"]["status"], "FAIL")
        self.assertEqual(result["summary"]["undeclared"], 3)
        self.assertTrue(all(not row["declared"] for row in result["findings"]))

    def test_runtime_inventory_declaration_does_not_exempt_other_core_files(self):
        result = self._runtime_inventory_fixture(unknown_sibling=True)
        self.assertEqual(result["summary"]["status"], "FAIL")
        undeclared = [row for row in result["findings"] if not row["declared"]]
        self.assertEqual([row["file"] for row in undeclared], ["tools/core/undeclared_inventory.py"])


if __name__ == "__main__":
    unittest.main()
