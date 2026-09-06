from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.core.source_evidence import atlas_file_paths, read_atlas_bound_source
from tools.core.doctrine_contract import require_doctrine_mapping
from tools.doctrine_compiler import _validate_exclusive_contract_owners
from tools.validate_engine_data_access import validate_engine_data_access


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
                    atlas_entry={"hash": "a" * 64},
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


if __name__ == "__main__":
    unittest.main()
