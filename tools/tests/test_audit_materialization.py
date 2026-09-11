from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from tools.core import pipeline_policy
from tools.engines import audit


class AuditMaterializationTests(unittest.TestCase):
    def test_malformed_symbol_range_is_reported_as_analysis_gap(self):
        atlas = {
            "MAIN": {
                "files": {
                    "src/Broken.ts": {
                        "loc": 20,
                        "features": [],
                        "imports": [],
                        "symbols": [
                            {
                                "name": "Broken",
                                "type": "function",
                                "source_lines": "Lx-L20",
                                "features": [],
                            }
                        ],
                    }
                }
            }
        }
        with (
            patch.object(audit, "resolve_atlas_data", return_value=(atlas, "fixture")),
            patch.object(audit, "resolve_runtime_projects", return_value={"MAIN": "."}),
            patch.object(audit, "load_effective_architecture_policy_context", return_value=({}, "")),
            patch.object(audit, "build_effective_project_rule_taxonomy", return_value={}),
            patch.object(
                audit,
                "filter_violations_by_project_taxonomy",
                side_effect=lambda violations, _taxonomies: (violations, {}),
            ),
            patch.object(audit, "load_scope_authority_for_consumer", return_value=({}, {})),
            patch.object(audit, "bind_consumer_projects", side_effect=lambda authority, **_kwargs: authority),
            patch.object(audit, "_write_outputs") as write,
        ):
            result = audit.analyze_project(atlas=atlas)

        self.assertTrue(result)
        self.assertEqual(
            write.call_args.kwargs["analysis_gaps"],
            [
                {
                    "project": "MAIN",
                    "file": "src/Broken.ts",
                    "symbol": "Broken",
                    "stage": "symbol_loc_evaluation",
                    "error_type": "ValueError",
                }
            ],
        )

    def test_audit_uses_canonical_function_type_for_uppercase_non_component(self):
        atlas = {
            "MAIN": {
                "files": {
                    "src/DataProvider.ts": {
                        "loc": 160,
                        "features": [],
                        "imports": [],
                        "symbols": [
                            {
                                "name": "DataProvider",
                                "type": "Arrow",
                                "canonical_symbol_type": "function",
                                "source_lines": "L1-L160",
                                "features": [],
                            }
                        ],
                    }
                }
            }
        }
        with (
            patch.object(audit, "resolve_atlas_data", return_value=(atlas, "fixture")),
            patch.object(audit, "resolve_runtime_projects", return_value={"MAIN": "."}),
            patch.object(audit, "load_effective_architecture_policy_context", return_value=({}, "")),
            patch.object(audit, "build_effective_project_rule_taxonomy", return_value={}),
            patch.object(
                audit,
                "filter_violations_by_project_taxonomy",
                side_effect=lambda violations, _taxonomies: (violations, {}),
            ),
            patch.object(audit, "load_scope_authority_for_consumer", return_value=({}, {})),
            patch.object(audit, "bind_consumer_projects", side_effect=lambda authority, **_kwargs: authority),
            patch.object(audit, "_write_outputs") as write,
        ):
            result = audit.analyze_project(atlas=atlas)

        self.assertTrue(result)
        violations = write.call_args.args[0]
        finding = violations["loc_limits_symbol"][0]
        self.assertEqual(finding["symbol_kind"], "function")
        self.assertEqual(finding["threshold_kind"], "function")
        self.assertEqual(finding["limit"], 150)

    def test_audit_materializes_literal_target_loc_limit_for_exact_project_and_file(self):
        effective_policy = {
            "projects": {
                "MAIN": {
                    "tools": [
                        {
                            "id": "biome",
                            "config_files": [
                                {
                                    "path": "biome.json",
                                    "sha256": "b" * 64,
                                    "static_projection": {
                                        "status": "partial_literal_projection",
                                        "extends_unresolved": False,
                                        "rules_truncated": False,
                                        "rules": [
                                            {
                                                "id": "complexity/noExcessiveLinesPerFunction",
                                                "state": "advisory",
                                                "scope": {"includes": ["src/**/*.tsx"], "excludes": []},
                                                "numeric_options": {"maxLines": 100},
                                            }
                                        ],
                                    },
                                }
                            ],
                        }
                    ]
                }
            }
        }
        atlas = {
            "MAIN": {
                "files": {
                    "src/Calendar.tsx": {
                        "loc": 120,
                        "features": [],
                        "imports": [],
                        "symbols": [
                            {
                                "name": "Calendar",
                                "type": "Component",
                                "canonical_symbol_type": "component",
                                "source_lines": "L1-L120",
                                "features": [],
                            }
                        ],
                    }
                }
            }
        }
        with (
            patch.dict(
                pipeline_policy.DYNAMIC_CONFIG,
                {"effective_target_policy": effective_policy},
                clear=False,
            ),
            patch.object(audit, "resolve_atlas_data", return_value=(atlas, "fixture")),
            patch.object(audit, "resolve_runtime_projects", return_value={"MAIN": "."}),
            patch.object(audit, "load_effective_architecture_policy_context", return_value=({}, "")),
            patch.object(audit, "build_effective_project_rule_taxonomy", return_value={}),
            patch.object(
                audit,
                "filter_violations_by_project_taxonomy",
                side_effect=lambda violations, _taxonomies: (violations, {}),
            ),
            patch.object(audit, "load_scope_authority_for_consumer", return_value=({}, {})),
            patch.object(audit, "bind_consumer_projects", side_effect=lambda authority, **_kwargs: authority),
            patch.object(audit, "_write_outputs") as write,
        ):
            result = audit.analyze_project(atlas=atlas)

        self.assertTrue(result)
        violations = write.call_args.args[0]
        finding = violations["loc_limits_component"][0]
        self.assertEqual(finding["limit"], 100)
        self.assertEqual(
            finding["limit_authority"],
            "declared_literal_policy_not_native_execution_proof",
        )
        self.assertEqual(
            finding["target_policy_resolution"]["status"],
            "resolved_declared_literal_policy",
        )

    def test_empty_project_is_covered_but_missing_project_is_not(self):
        for present in (True, False):
            with self.subTest(present=present):
                atlas = {'EMPTY': {'files': {}}} if present else {'OTHER': {'files': {}}}
                authority = {'scope_authority_id': 'fixture', 'effective_runtime_projects': {'EMPTY': '.'}, 'incomplete_reasons': []}
                with (
                    patch.object(audit, 'resolve_atlas_data', return_value=(atlas, 'fixture')),
                    patch.object(audit, 'resolve_runtime_projects', return_value={'EMPTY': '.'}),
                    patch.object(audit, 'load_scope_authority_for_consumer', return_value=({}, authority)),
                    patch.object(audit, '_write_outputs') as write,
                ):
                    result = audit.analyze_project(atlas=atlas)
                self.assertEqual(result, present)
                if present:
                    self.assertEqual(write.call_args.kwargs['audited_projects'], ['EMPTY'])
                    self.assertEqual(write.call_args.kwargs['scope_authority']['layer_consistency'], 'CONSISTENT')
                else:
                    write.assert_not_called()

    def test_scoped_empty_project_does_not_claim_requested_file_audited(self):
        atlas = {'EMPTY': {'files': {}}}
        with (
            patch.object(audit, 'resolve_atlas_data', return_value=(atlas, 'fixture')),
            patch.object(audit, 'resolve_runtime_projects', return_value={'EMPTY': '.'}),
            patch.object(audit, 'load_scope_authority_for_consumer', return_value=({}, {})),
            patch.object(audit, '_write_outputs') as write,
        ):
            audit.analyze_project(changed_files=['EMPTY::missing.ts'], atlas=atlas)
        self.assertEqual(write.call_args.kwargs['audited_projects'], [])
        self.assertEqual(write.call_args.kwargs['scoped_audit']['scope_status'], 'empty')
        self.assertEqual(write.call_args.kwargs['scoped_audit']['unresolved_requested_files'], ['EMPTY::missing.ts'])

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

    def test_audit_report_preserves_loc_policy_provenance(self):
        saved_payloads: list[dict] = []
        resolution = {
            "status": "resolved_declared_literal_policy",
            "limit": 100,
            "limit_authority": "declared_literal_policy_not_native_execution_proof",
            "matched_rules": ["biome:noExcessiveLinesPerFunction"],
            "unresolved_reasons": [],
            "native_execution": "not_run",
        }
        violations = {
            "loc_limits_component": [
                {
                    "project": "MAIN",
                    "file": "src/Panel.tsx",
                    "detail": "src/Panel.tsx symbol:Panel (120 lines)",
                    "recommended_action": "Split only along demonstrated responsibilities.",
                    "symbol_name": "Panel",
                    "symbol_kind": "component",
                    "threshold_kind": "component",
                    "start_line": 1,
                    "end_line": 120,
                    "observed_loc": 120,
                    "limit": 100,
                    "limit_authority": "declared_literal_policy_not_native_execution_proof",
                    "target_policy_resolution": resolution,
                }
            ]
        }
        taxonomy = {"summary": {"by_layer": {}, "by_mode": {}}, "profiles": {}}

        with (
            patch.object(
                audit,
                "_load_structural_contract_health",
                return_value={
                    "status": "not_available_in_audit_phase",
                    "reason": "fixture",
                    "atlas_contract_file_ratio": 1.0,
                    "member_detail_contract_ratio": 1.0,
                    "genome_occurrence_contract_ratio": 1.0,
                    "atlas_current_version_ratio": 1.0,
                    "genome_current_version_ratio": 1.0,
                },
            ),
            patch.object(audit, "build_rule_taxonomy", return_value=taxonomy),
            patch.object(audit, "get_module_root_name", return_value="modules"),
            patch.object(audit, "save_json_atomic", side_effect=lambda _path, payload: saved_payloads.append(copy.deepcopy(payload))),
            patch.object(audit, "save_text_atomic"),
            patch.object(audit, "write_current_atlas_lineage"),
            patch.object(audit, "flush_shadow_writes", return_value=True),
            patch.object(audit, "invalidate_audit_report_cache"),
        ):
            audit._write_outputs(
                violations,
                [],
                {"total": 1, "by_rule": {"loc_limits_component": 1}},
                audited_projects=["MAIN"],
                atlas_project_count=1,
                atlas={"MAIN": {"files": {}}},
            )

        persisted = saved_payloads[0]["violations"][0]
        self.assertEqual(
            persisted["limit_authority"],
            "declared_literal_policy_not_native_execution_proof",
        )
        self.assertEqual(persisted["target_policy_resolution"], resolution)


if __name__ == "__main__":
    unittest.main()
