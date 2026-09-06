from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import generate_decision_domain_classification as module


class DecisionDomainClassificationTests(unittest.TestCase):
    def test_raw_connectivity_pass_is_not_authoritative_without_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            raw_dir = root / "raw"
            reports_dir = root / "reports"
            raw_dir.mkdir()
            reports_dir.mkdir()
            (raw_dir / "system_connectivity_map.json").write_text(
                json.dumps(
                    {
                        "summary": {"status": "PASS", "unowned_consumed_artifacts": 0},
                        "connectivity": {"source_layers": {"rows": [{"layer": "example"}]}},
                    }
                ),
                encoding="utf-8",
            )

            with (
                patch.object(module, "RAW_DIR", raw_dir),
                patch.object(module, "RAW_OUTPUT_PATH", raw_dir / "decision_domain_classification.json"),
                patch.object(module, "REPORT_OUTPUT_PATH", reports_dir / "decision_domain_classification.md"),
            ):
                payload = module.build_classification()

            self.assertEqual(payload["summary"]["status"], "FAIL")
            self.assertFalse(payload["summary"]["connectivity_authorized"])
            self.assertEqual(payload["summary"]["connectivity_map_reported_status"], "PASS")

    def test_stale_connectivity_validation_cannot_authorize_changed_map(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            raw_dir = root / "raw"
            reports_dir = root / "reports"
            raw_dir.mkdir()
            reports_dir.mkdir()
            map_path = raw_dir / "system_connectivity_map.json"
            map_path.write_text(
                json.dumps({"summary": {"status": "PASS"}, "connectivity": {"source_layers": []}}),
                encoding="utf-8",
            )
            (raw_dir / "system_connectivity_map_validation.json").write_text(
                json.dumps(
                    {
                        "summary": {"status": "PASS"},
                        "source_evidence": {"sha256": "0" * 64},
                    }
                ),
                encoding="utf-8",
            )

            with (
                patch.object(module, "RAW_DIR", raw_dir),
                patch.object(module, "RAW_OUTPUT_PATH", raw_dir / "decision_domain_classification.json"),
                patch.object(module, "REPORT_OUTPUT_PATH", reports_dir / "decision_domain_classification.md"),
            ):
                payload = module.build_classification()

            self.assertEqual(payload["summary"]["status"], "FAIL")
            self.assertFalse(payload["summary"]["connectivity_validation_matches_current_map"])

    def test_matching_connectivity_validation_authorizes_current_map(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            raw_dir = root / "raw"
            reports_dir = root / "reports"
            raw_dir.mkdir()
            reports_dir.mkdir()
            map_path = raw_dir / "system_connectivity_map.json"
            map_path.write_text(
                json.dumps({"summary": {"status": "PASS"}, "connectivity": {"source_layers": []}}),
                encoding="utf-8",
            )
            map_sha256 = hashlib.sha256(map_path.read_bytes()).hexdigest()
            (raw_dir / "system_connectivity_map_validation.json").write_text(
                json.dumps(
                    {
                        "summary": {"status": "PASS"},
                        "source_evidence": {"sha256": map_sha256},
                    }
                ),
                encoding="utf-8",
            )

            with (
                patch.object(module, "RAW_DIR", raw_dir),
                patch.object(module, "RAW_OUTPUT_PATH", raw_dir / "decision_domain_classification.json"),
                patch.object(module, "REPORT_OUTPUT_PATH", reports_dir / "decision_domain_classification.md"),
            ):
                payload = module.build_classification()

            self.assertEqual(payload["summary"]["status"], "PASS")
            self.assertTrue(payload["summary"]["connectivity_validation_matches_current_map"])


if __name__ == "__main__":
    unittest.main()
