from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.core.artifact_validator import ArtifactValidationError
from tools.core.text_surface_policy import POLICY_SCHEMA_PATH, load_text_surface_policy
from tools.validate_text_surface_integrity import _scan_file


class TextSurfacePolicyTests(unittest.TestCase):
    @staticmethod
    def _policy() -> dict:
        return {
            "_meta": {
                "kind": "text_surface_integrity_policy",
                "version": "1.0.0",
                "purpose": "test",
            },
            "source_clean_scope": {
                "include_roots": ["sample.txt"],
                "include_suffixes": [".txt"],
                "exclude_path_prefixes": [],
                "exclude_path_parts": [],
            },
            "release_language_scope": {
                "include_roots": ["sample.txt"],
                "include_suffixes": [".txt"],
                "exclude_path_prefixes": [],
                "disallowed_character_sets": {"sample": ["x"]},
            },
            "checks": {
                "strict_utf8_decode": True,
                "replacement_character": True,
                "mojibake_marker_visibility": True,
            },
            "mojibake_markers": ["sample-marker"],
            "mojibake_marker_allowed_files": {},
            "terminal_rendering_note": "test",
        }

    def test_strict_utf8_false_is_rejected_as_unsupported_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            policy_path = Path(temp) / "policy.json"
            policy = self._policy()
            policy["checks"]["strict_utf8_decode"] = False
            policy_path.write_text(json.dumps(policy), encoding="utf-8")

            with self.assertRaises(ArtifactValidationError):
                load_text_surface_policy(policy_path, POLICY_SCHEMA_PATH)

    def test_invalid_utf8_bytes_are_reported_with_offset(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "invalid.txt"
            path.write_bytes(b"valid\n\xffbroken")

            findings = _scan_file(path, self._policy(), relative_path="invalid.txt")

            self.assertEqual(findings[0]["kind"], "invalid_utf8")
            self.assertEqual(findings[0]["byte_offset"], 6)


if __name__ == "__main__":
    unittest.main()
