from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from tools.engines import audit


class AuditMaterializationTests(unittest.TestCase):
    def test_audit_report_is_materialized_once_and_post_write_timings_stay_in_telemetry(self):
        saved_payloads: list[dict] = []
        write_events: list[str] = []
        profile_timings = {"scan_seconds": 0.1}

        def capture_payload(_path, payload):
            write_events.append("canonical_json")
            saved_payloads.append(copy.deepcopy(payload))

        def capture_text(path, _content):
            write_events.append("human_report" if path == audit.AUDIT_REPORT_TEXT_PATH else "supporting_report")

        def capture_flush(*_args, **_kwargs):
            write_events.append("shadow_flush")
            return True

        structural_health = {
            "status": "not_available_in_audit_phase",
            "reason": "phase boundary",
            "atlas_contract_file_ratio": 1.0,
            "member_detail_contract_ratio": 1.0,
            "genome_occurrence_contract_ratio": 1.0,
            "atlas_current_version_ratio": 1.0,
            "genome_current_version_ratio": 1.0,
        }
        taxonomy = {"summary": {"by_layer": {}, "by_mode": {}}, "profiles": {}}

        with (
            patch.object(audit, "_load_structural_contract_health", return_value=structural_health),
            patch.object(audit, "build_rule_taxonomy", return_value=taxonomy),
            patch.object(audit, "get_module_root_name", return_value="modules"),
            patch.object(audit, "save_json_atomic", side_effect=capture_payload) as save_json,
            patch.object(audit, "save_text_atomic", side_effect=capture_text) as save_text,
            patch.object(audit, "write_current_atlas_lineage") as lineage,
            patch.object(audit, "flush_shadow_writes", side_effect=capture_flush) as flush_shadow,
            patch.object(audit, "invalidate_audit_report_cache") as invalidate_cache,
        ):
            audit._write_outputs(
                {},
                [],
                {"total": 0, "by_rule": {}},
                audited_projects=["MAIN"],
                atlas_project_count=1,
                atlas={"MAIN": {"files": {}}},
                profile_timings=profile_timings,
            )

        self.assertEqual(save_json.call_count, 1)
        lineage.assert_called_once()
        self.assertEqual(lineage.call_args.kwargs["artifact_id"], "audit_report")
        self.assertEqual(lineage.call_args.kwargs["artifact_payload"], saved_payloads[0])
        self.assertEqual(lineage.call_args.kwargs["atlas"], {"MAIN": {"files": {}}})
        self.assertEqual(lineage.call_args.kwargs["dependency_payloads"], {"analysis_scope_authority": {}})
        self.assertEqual(flush_shadow.call_count, 1)
        self.assertEqual(save_text.call_count, 2)
        self.assertEqual(
            write_events,
            ["supporting_report", "canonical_json", "shadow_flush", "human_report"],
        )
        self.assertEqual(len(saved_payloads), 1)
        persisted_timings = saved_payloads[0]["summary"]["profile_timings"]
        self.assertNotIn("report_text_save_seconds", persisted_timings)
        self.assertNotIn("canonical_artifact_save_seconds", persisted_timings)
        self.assertNotIn("shadow_flush_seconds", persisted_timings)
        self.assertIn("canonical_artifact_save_seconds", profile_timings)
        self.assertIn("report_text_save_seconds", profile_timings)
        self.assertTrue(profile_timings["shadow_flush_complete"])
        self.assertIn("output_materialize_seconds", profile_timings)
        invalidate_cache.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
