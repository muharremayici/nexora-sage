import ast
import unittest
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
from datetime import date
from pathlib import Path
from unittest.mock import patch

CODE_MAPS_DIR = Path(__file__).resolve().parents[2]
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))

from tools.core.adapter_registry import summarize_adapters, validate_adapter_manifest
from tools.core.dead_code_allowlist_policy import (
    get_allowlist_scope,
    is_valid_scope,
    scope_allows_global,
    scope_allows_project,
)
from tools.core.config import (
    _apply_target_root_override,
    _resolve_source_extensions,
    _target_output_slug,
    external_target_project_relative_path,
    external_target_scope_projection,
)
from tools.core.evidence_status import evidence_failed_checks, normalize_evidence_status
from tools.core.language_registry import is_config_or_manifest_file, language_extensions
from tools.core import config as core_config
from tools.core import artifact_store
from tools.core.artifact_store import ArtifactPrimaryWriteError, ArtifactStore, _artifact_profile_log
from tools.core.db import SQLiteManager
from tools.external_target_preflight import (
    _preflight_policy_issues,
    build_preflight,
    render_report,
    write_preflight,
)
from tools.generate_external_target_index import build_index
from tools.inspect_target import _safe_name, _under_folder
from tools.mcp import server as mcp_server
from tools.mcp.server import (
    _base_without_test_suffix,
    _bounded_source_snippets,
    _canonical_openable_path,
    _confidence_from_raw,
    _find_symbol_matches,
    _normalize_confidence_payload_for_agent,
    _render_confidence_brief,
    _is_likely_test_path,
    _narrow_source_grounding_to_inspection_target,
    _set_inspection_edit_authority,
    _operator_projection_access_denied,
    _operator_privileged_projection_allowed,
    _stable_audit_work_item_id,
    _self_governance_suspicion_state,
    get_surgical_context,
)
from tools.validate_hardcoded_decision_inventory import (
    _module_decision_tables_from_tree,
    _read_central_contract_fallbacks,
)
from tools.core.suppression import find_suppression, stable_decision_key
from tools.utils.system_purge import _is_nonblocking_test_tmp_residue, _rmtree_force
from tools.engines.live_surface_analyzer import HIGH_CONFIDENCE_ORACLE_CLASSES
from tools.engines.nuclear_processor import NanometricKernel, NanometricParser
from tools.engines.quality_gate import _optional_lte_check
from tools.engines.self_healing_generator import CodeHealer
from tools.engines.self_healing_generator import _auto_heal_generation_decision
from tools.engines.self_healing_generator import _auto_heal_script_policy
from tools.engines.self_healing_generator import _mutation_guard_bash_lines as heal_bash_guard
from tools.engines.self_healing_generator import _manifest_guard_bash_lines as heal_bash_manifest_guard
from tools.engines.self_healing_generator import _manifest_guard_ps_lines as heal_ps_manifest_guard
from tools.engines.self_healing_generator import _mutation_guard_ps_lines as heal_ps_guard
from tools.engines.merge_dependency_packager import build_dependency_package
from tools.engines.merge_dependency_packager import _dependency_variants
from tools.engines.merge_script_generator import _safe_join, _safe_relative_path
from tools.engines.merge_script_generator import _mutation_guard_bash_lines as merge_bash_guard
from tools.engines.merge_script_generator import _manifest_guard_bash_lines as merge_bash_manifest_guard
from tools.engines.merge_script_generator import _manifest_guard_ps_lines as merge_ps_manifest_guard
from tools.engines.merge_script_generator import _mutation_guard_ps_lines as merge_ps_guard
from tools.engines.merge_decision_cockpit import build_cockpit_row, confidence_for_decision
from tools.engines.merge_simulation_engine import simulate_dependency_package
from tools.engines.framework_route_analyzer import (
    _route_from_next_app_file,
    _route_from_next_pages_file,
    analyze_project_routes,
    smoke_path_for_route,
)
from tools.engines.ai_task_pack_generator import build_task_pack_record, render_task_pack
from tools.engines.ui_smoke_spec_generator import render_playwright_smoke_spec
from tools.engines.ui_smoke_execution_report import _execution_command
from tools.engines.ui_runtime_contract_analyzer import (
    _build_symbol_source_index,
    _candidate_ui_risk,
    _classify_i18n_keys,
    _smoke_plan,
    analyze_ui_runtime_contract,
)
from tools.engines.a11y_i18n_contract_analyzer import analyze_a11y_i18n_file
from tools.engines.dead_code_detector import DeadCodeDetector
from tools.engines.next_boundary_analyzer import analyze_next_boundary_file
from tools.engines.next_boundary_analyzer import _enrich_risk_evidence_with_context
from tools.engines.react_ecosystem_analyzer import analyze_react_ecosystem_file
from tools.engines.react_runtime_intelligence import analyze_runtime_intelligence_file
from tools.engines.react_runtime_intelligence import _calibrate_with_ecosystem
from tools.engines.react_runtime_intelligence import _load_runtime_policy
from tools.engines.react_runtime_intelligence import _render_mutation_lines
from tools.engines.react_compiler_readiness import _normalize_finding as normalize_compiler_finding
from tools.engines.react_frontier_intelligence import analyze_frontier_file
from tools.engines.react_frontier_intelligence import _evidence_readiness, _hot_profiler_entries, _large_assets
from tools.engines.react_frontier_intelligence import _ts_diagnostic_findings
from tools.engines import react_frontier_intelligence as react_frontier_engine
from tools.engines.release_readiness_report import build_release_readiness_payload
from tools.generate_nexora_operator_packet import _combined_release_readiness
from tools.engines.fractal_mapper import map_project
from tools.engines.state_flow_scanner import _transition_subject_file_set
from tools.engines.state_data_graph_analyzer import _client_action_has_invalidation, _source_context_for_path, _zustand_selector_risk_rows
from tools.engines.quant_engine import _input_shape_status, _rank_halo_nodes, _signal_actionability
from tools.engines.confidence_engine import evaluate_file_confidence
from tools.engines.health_score import _health_input_shape_status
from tools.engines.blast_radius_engine import get_reachable
from tools.engines.test_impact_matcher import extract_base_name, generate_test_command, is_test_file
from tools.core import test_impact_profiles as test_impact_profile_core
from tools.core.state_flow import summarize_state_flow_features
from tools.core.react_evidence import attach_react_evidence_contract
from tools.core import audit_report as audit_report_core
from tools.core.audit_rules import RULE_DEFINITIONS, RULE_PROFILE_CONTRACT, _requirement_satisfied
from tools.core.architecture_blueprints import (
    architecture_governance_context,
    blueprint_axes_valid,
    canonical_profile_id,
    effective_profile_ids,
)
from tools.core.language_registry import index_files, language_for_extension, watch_extensions
from tools.core.pipeline_registry import filter_catalog_for_system_scope
from tools.orchestrators import orchestrator as orchestrator_module
from tools.orchestrators.orchestrator import (
    PIPELINE_CACHE,
    _pending_cache_consumer_names,
    apply_capability_activation,
    pre_warm_cache,
    select_steps_smart,
)
from tools.engines.capability_activation_planner import _refresh_project_dna_profile
from tools.engines.project_dna_profiler import _dependency_names
from tools.validate_merge_intelligence_regression import build_regression_checks
from tools.engines.validation_oracle import ValidationOracle
from tools.core.package_contracts import build_package_public_contracts
from tools.validate_performance_budget import (
    _latest_atlas_persistence_profile,
    _latest_atlas_phase_profile,
    _latest_completed_forced_session,
    _latest_release_deep_session,
    _metric_rolling_config,
    _pipeline_samples_by_force_profile,
    _pipeline_samples_by_mode,
    _rolling_decision,
)
from tools.update_performance_ledger import build_trend_summary
from tools.validate_react import _react_probe


class EvidenceStatusContractTests(unittest.TestCase):
    def test_empty_pass_shell_is_unknown(self):
        verdict = normalize_evidence_status({"summary": {"status": "PASS", "failed_checks": 0}})

        self.assertEqual(verdict["status"], "UNKNOWN")
        self.assertIsNone(verdict["passed"])
        self.assertIn("empty_pass_shell", verdict["source"])

    def test_failed_checks_override_conflicting_pass_status(self):
        verdict = normalize_evidence_status({"summary": {"status": "PASS", "failed_checks": 3}})

        self.assertEqual(verdict["status"], "FAIL")
        self.assertFalse(verdict["passed"])
        self.assertEqual(verdict["source"], "summary.failed_checks")

    def test_failed_checks_payloads_have_a_central_verdict(self):
        payload = {"summary": {"failed_checks": 0, "total_checks": 12}}

        self.assertEqual(normalize_evidence_status(payload)["status"], "PASS")
        self.assertEqual(evidence_failed_checks(payload), 0)

    def test_check_rows_payloads_have_a_central_verdict(self):
        payload = {"checks": [{"name": "a", "passed": True}, {"name": "b", "passed": False}]}

        verdict = normalize_evidence_status(payload)

        self.assertEqual(verdict["status"], "FAIL")
        self.assertFalse(verdict["passed"])
        self.assertEqual(evidence_failed_checks(payload), 1)

    def test_all_passing_check_rows_do_not_become_unknown(self):
        payload = {
            "summary": {"weakest_link_status": "PASS"},
            "checks": [{"name": "a", "passed": True}, {"name": "b", "passed": True}],
        }

        verdict = normalize_evidence_status(payload)

        self.assertEqual(verdict["status"], "PASS")
        self.assertTrue(verdict["passed"])
        self.assertEqual(verdict["source"], "checks[].passed")

    def test_advisory_failed_check_does_not_override_ready_status(self):
        payload = {
            "readiness": "PRODUCTION_READY",
            "summary": {"checks": 2, "passed": 1, "failed": 0},
            "checks": [
                {"name": "required", "passed": True, "enforced": True},
                {"name": "advisory", "passed": False, "enforced": False},
            ],
        }

        verdict = normalize_evidence_status(payload)

        self.assertEqual(verdict["status"], "PASS")
        self.assertTrue(verdict["passed"])

    def test_warning_severity_does_not_impersonate_a_blocking_failure(self):
        payload = {
            "summary": {"status": "WARN", "checks": 2, "passed": 1, "failed": 0, "warnings": 1},
            "checks": [
                {"name": "required", "passed": True, "severity": "error"},
                {"name": "idle_consumer", "passed": False, "severity": "warning"},
            ],
        }

        verdict = normalize_evidence_status(payload)

        self.assertEqual(verdict["status"], "PASS")
        self.assertTrue(verdict["passed"])
        self.assertEqual(verdict["source"], "checks[].passed")

    def test_error_severity_remains_a_blocking_failure(self):
        payload = {
            "summary": {"status": "WARN", "checks": 1, "passed": 0, "failed": 1},
            "checks": [{"name": "required", "passed": False, "severity": "error"}],
        }

        verdict = normalize_evidence_status(payload)

        self.assertEqual(verdict["status"], "FAIL")
        self.assertFalse(verdict["passed"])


class PipelineCacheWarmTests(unittest.TestCase):
    def test_completed_atlas_only_plan_has_no_pending_cache_consumer(self):
        self.assertEqual(
            _pending_cache_consumer_names([{"name": "Atlas"}], {"Atlas"}),
            [],
        )
        self.assertEqual(
            _pending_cache_consumer_names(
                [{"name": "Atlas"}, {"name": "Audit"}],
                {"Atlas"},
            ),
            ["Audit"],
        )

    def test_pre_warm_uses_provided_atlas_without_reloading_storage(self):
        provided = {"MAIN": {"files": {}}}
        empty_cache = {
            "keyword": None,
            "ui": None,
            "landscape": None,
            "atlas": None,
            "fractal": None,
        }
        with (
            patch.dict(PIPELINE_CACHE, empty_cache, clear=True),
            patch.dict("tools.engines.generate_atlas.GLOBAL_ATLAS_CACHE", {}, clear=True),
            patch.object(
                orchestrator_module,
                "load_atlas_data",
                side_effect=AssertionError("redundant Atlas storage reload"),
            ),
            patch.object(orchestrator_module, "load_json_file", return_value={}),
            patch("tools.core.fractal_io.load_fractal_map_data", return_value={}),
        ):
            self.assertTrue(pre_warm_cache(atlas=provided))
            self.assertIs(PIPELINE_CACHE["atlas"], provided)
            self.assertTrue(PIPELINE_CACHE["__warmed__"])


class HardcodedDecisionInventoryTests(unittest.TestCase):
    def test_module_decision_table_detector_uses_shared_row_fields(self):
        tree = ast.parse(
            "RULES = {\n"
            "    'one': {'label': 'One', 'default_mode': 'enforced', 'requires': []},\n"
            "    'two': {'label': 'Two', 'default_mode': 'disabled', 'requires': []},\n"
            "}\n"
        )
        policy = {
            "decision_table_row_field_markers": ["label", "default_mode", "requires", "timeout"],
            "decision_table_min_rows": 2,
            "decision_table_min_matching_fields": 3,
        }

        candidates = _module_decision_tables_from_tree(tree, "tools/example.py", policy)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].assignment_name, "RULES")
        self.assertEqual(candidates[0].row_count, 2)
        self.assertEqual(candidates[0].matching_fields, ("default_mode", "label", "requires"))

    def test_module_decision_table_detector_rejects_unshared_incidental_fields(self):
        tree = ast.parse(
            "DATA = {\n"
            "    'one': {'label': 'One', 'timeout': 1},\n"
            "    'two': {'requires': [], 'default_mode': 'disabled'},\n"
            "}\n"
        )
        policy = {
            "decision_table_row_field_markers": ["label", "default_mode", "requires", "timeout"],
            "decision_table_min_rows": 2,
            "decision_table_min_matching_fields": 3,
        }

        self.assertEqual(_module_decision_tables_from_tree(tree, "tools/example.py", policy), [])


class AuditRuleProfileContractTests(unittest.TestCase):
    def test_runtime_profiles_equal_the_modular_doctrine_owner(self):
        pack_path = CODE_MAPS_DIR / "config" / "doctrines" / "governance" / "audit_rules.json"
        profiles = json.loads(pack_path.read_text(encoding="utf-8"))["audit_rule_profiles"]

        self.assertEqual(dict(RULE_DEFINITIONS), profiles)
        self.assertEqual(set(profiles), set(json.loads(pack_path.read_text(encoding="utf-8"))["violation_labels"]))

    def test_unknown_requirement_kind_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "Unsupported audit rule requirement kind"):
            _requirement_satisfied({"kind": "future_unknown_kind", "value": True}, {})

    def test_runtime_requirement_contract_equals_modular_doctrine(self):
        pack_path = CODE_MAPS_DIR / "config" / "doctrines" / "governance" / "audit_rules.json"
        profile_contract = json.loads(pack_path.read_text(encoding="utf-8"))["audit_rule_profile_contract"]

        self.assertEqual(dict(RULE_PROFILE_CONTRACT), profile_contract)


class AstSequencerStateFlowContractTests(unittest.TestCase):
    def _run_ast_sequencer(self, source):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not available")
        sequencer = Path(__file__).resolve().parents[1] / "engines" / "ast_sequencer.cjs"
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "sample.tsx"
            target.write_text(source, encoding="utf-8")
            result = subprocess.run(
                [node, str(sequencer), str(target)],
                cwd=Path(__file__).resolve().parents[2],
                capture_output=True,
                text=True,
                timeout=20,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_jotai_store_named_hooks_are_not_zustand_consumers(self):
        symbols = self._run_ast_sequencer(
            """
            import { useCallback } from 'react';
            import { useStore } from 'jotai';
            import { useUpsertRecordsInStore } from '@/object-record/record-store/hooks/useUpsertRecordsInStore';

            export const useLocalRecordStore = () => {
              const store = useStore();
              const { upsertRecordsInStore } = useUpsertRecordsInStore();
              return useCallback(() => upsertRecordsInStore({ partialRecords: [] }), [store, upsertRecordsInStore]);
            };
            """
        )
        meta = next(symbol for symbol in symbols if symbol["name"] == "__file_meta__")
        features = set(meta["features"])

        self.assertNotIn("Zustand:NoSelector:useStore", features)
        self.assertNotIn("Zustand:NoSelector:useUpsertRecordsInStore", features)

    def test_real_zustand_store_hook_still_reports_selectorless_call(self):
        symbols = self._run_ast_sequencer(
            """
            import { create } from 'zustand';

            const useBoundStore = create(() => ({ count: 0 }));

            export function Counter() {
              const state = useBoundStore();
              return <div>{state.count}</div>;
            }
            """
        )
        meta = next(symbol for symbol in symbols if symbol["name"] == "__file_meta__")
        features = set(meta["features"])

        self.assertIn("ZustandStore", features)
        self.assertIn("Zustand:NoSelector:useBoundStore", features)

    def test_local_react_component_default_export_is_symbol_with_span(self):
        symbols = self._run_ast_sequencer(
            """
            import React from 'react';

            const AppLayout: React.FC = () => {
              return <main />;
            };

            export default AppLayout;
            """
        )
        component = next(symbol for symbol in symbols if symbol["name"] == "AppLayout")

        self.assertEqual(component["type"], "Component")
        self.assertEqual(component["canonicalSymbolType"], "component")
        self.assertGreater(component["start"], 0)
        self.assertGreater(component["end"], component["start"])

    def test_imported_domain_store_hook_reports_selectorless_call(self):
        symbols = self._run_ast_sequencer(
            """
            import { useOnboardingStore } from '../../store/onboarding-store';

            export function PersonalSettingsView() {
              const { personalDetails, setPersonalDetails } = useOnboardingStore();
              return <div>{personalDetails.name}</div>;
            }
            """
        )
        meta = next(symbol for symbol in symbols if symbol["name"] == "__file_meta__")
        features = set(meta["features"])

        self.assertIn("Zustand:NoSelector:useOnboardingStore", features)

    def test_legacy_and_instore_helpers_are_not_zustand_store_hooks(self):
        symbols = self._run_ast_sequencer(
            """
            import { useLegacyStore } from 'sentry/stores/useLegacyStore';
            import { useUpsertRecordsInStore } from '@/object-record/record-store/hooks/useUpsertRecordsInStore';

            export function Billing() {
              const legacy = useLegacyStore(SubscriptionStore);
              const { upsertRecordsInStore } = useUpsertRecordsInStore();
              return <div>{legacy?.id}</div>;
            }
            """
        )
        meta = next(symbol for symbol in symbols if symbol["name"] == "__file_meta__")
        features = set(meta["features"])

        self.assertNotIn("Zustand:NoSelector:useLegacyStore", features)
        self.assertNotIn("Tech:selector:useLegacyStore", features)
        self.assertNotIn("Zustand:NoSelector:useUpsertRecordsInStore", features)

    def test_zustand_use_shallow_wrapper_counts_as_stabilized_selector(self):
        symbols = self._run_ast_sequencer(
            """
            import { create } from 'zustand';
            import { useShallow } from 'zustand/shallow';

            const useMyStore = create(() => ({ a: 1, b: 2, c: 3 }));
            const selector = (state) => Object.keys(state).sort();

            export function StableKeys() {
              const output = useMyStore(useShallow(selector));
              return <div>{output.join(',')}</div>;
            }
            """
        )
        meta = next(symbol for symbol in symbols if symbol["name"] == "__file_meta__")
        features = set(meta["features"])

        self.assertIn("Tech:selector:useMyStore", features)
        self.assertIn("Tech:selector_shallow", features)
        self.assertNotIn("Zustand:BroadSelector:useMyStore", features)

    def test_zustand_broad_selector_remains_risky_even_with_shallow(self):
        symbols = self._run_ast_sequencer(
            """
            import { create } from 'zustand';
            import { shallow } from 'zustand/shallow';

            const useMyStore = create(() => ({ a: 1, b: 2, c: 3 }));

            export function BroadState() {
              const state = useMyStore((state) => ({ ...state }), shallow);
              return <div>{state.a}</div>;
            }
            """
        )
        meta = next(symbol for symbol in symbols if symbol["name"] == "__file_meta__")
        features = set(meta["features"])

        self.assertIn("Tech:selector:useMyStore", features)
        self.assertIn("Tech:selector_shallow", features)
        self.assertIn("Tech:selector_equality_fn", features)
        self.assertIn("Zustand:BroadSelector:useMyStore", features)

    def test_tanstack_query_client_action_preserves_dynamic_key_parts(self):
        symbols = self._run_ast_sequencer(
            """
            import { useQueryClient, useSuspenseQuery } from '@tanstack/react-query';

            export function Projects({ project }) {
              const queryClient = useQueryClient();
              useSuspenseQuery({ queryKey: ['projects'], queryFn: async () => [] });
              return <button onClick={() => queryClient.prefetchQuery({
                queryKey: ['project', project.full_name],
                queryFn: async () => project,
              })}>Load</button>;
            }
            """
        )
        meta = next(symbol for symbol in symbols if symbol["name"] == "__file_meta__")
        features = set(meta["features"])

        self.assertIn("QueryKey:projects", features)
        self.assertIn("QueryKey:project", features)
        self.assertIn("QueryKeyDynamic:project.full_name", features)
        self.assertIn("QueryClientAction:prefetchQuery", features)

    def test_tanstack_options_helpers_preserve_query_and_mutation_keys(self):
        symbols = self._run_ast_sequencer(
            """
            import { queryOptions, mutationOptions, useQuery, useMutation } from '@tanstack/react-query';

            const groupOptions = (orgSlug) => queryOptions({
              queryKey: ['group', orgSlug],
              queryFn: async () => null,
            });
            const projectMutationOptions = (projectSlug) => mutationOptions({
              mutationKey: ['project', projectSlug],
              mutationFn: async () => null,
            });

            export function Dashboard({ orgSlug, projectSlug }) {
              useQuery(groupOptions(orgSlug));
              useMutation(projectMutationOptions(projectSlug));
              return null;
            }
            """
        )
        meta = next(symbol for symbol in symbols if symbol["name"] == "__file_meta__")
        features = set(meta["features"])

        self.assertIn("QueryKey:group", features)
        self.assertIn("QueryKeyRef:orgSlug", features)
        self.assertIn("QueryKeyDynamic:groupOptions(orgSlug)", features)
        self.assertIn("MutationKey:project", features)
        self.assertIn("MutationKeyRef:projectSlug", features)
        self.assertIn("MutationKeyDynamic:projectMutationOptions(projectSlug)", features)

    def test_tanstack_query_client_direct_key_args_are_preserved(self):
        symbols = self._run_ast_sequencer(
            """
            export function Actions({ queryClient, organization, debugFilesApiOptions }) {
              const queryKey = ['projects', organization.slug];
              queryClient.setQueryData(queryKey, previous => previous);
              queryClient.invalidateQueries(debugFilesApiOptions.queryKey);
              queryClient.removeQueries({ queryKey: makePluginQuery(organization) });
            }
            """
        )
        meta = next(symbol for symbol in symbols if symbol["name"] == "__file_meta__")
        features = set(meta["features"])

        self.assertIn("QueryClientAction:setQueryData", features)
        self.assertIn("QueryKeyRef:queryKey", features)
        self.assertIn("QueryClientAction:invalidateQueries", features)
        self.assertIn("QueryKeyDynamic:debugFilesApiOptions.queryKey", features)
        self.assertIn("QueryClientAction:removeQueries", features)
        self.assertIn("QueryKeyDynamic:makePluginQuery(organization)", features)

    def test_tanstack_use_queries_preserves_nested_query_keys(self):
        symbols = self._run_ast_sequencer(
            """
            import { useQueries, useSuspenseQueries } from '@tanstack/react-query';

            export function Dashboard({ organization, optionFactory }) {
              const sharedOptions = optionFactory(organization.slug);
              useQueries({
                queries: [
                  { queryKey: ['projects', organization.slug], queryFn: async () => [] },
                  sharedOptions,
                  makeIssueQuery(organization),
                ],
              });
              useSuspenseQueries({
                queries: [
                  { queryKey: ['teams'], queryFn: async () => [] },
                ],
              });
              return null;
            }
            """
        )
        meta = next(symbol for symbol in symbols if symbol["name"] == "__file_meta__")
        features = set(meta["features"])

        self.assertIn("QueryKey:projects", features)
        self.assertIn("QueryKeyDynamic:organization.slug", features)
        self.assertIn("QueryKeyRef:sharedOptions", features)
        self.assertIn("QueryKeyDynamic:makeIssueQuery(organization)", features)
        self.assertIn("QueryKey:teams", features)

    def test_keyless_tanstack_mutation_remains_surface_without_fake_key(self):
        symbols = self._run_ast_sequencer(
            """
            import { useMutation } from '@tanstack/react-query';

            export function SaveButton() {
              const mutation = useMutation({
                mutationFn: async (payload) => fetch('/api/save', {
                  method: 'POST',
                  body: JSON.stringify(payload),
                }),
              });
              return <button onClick={() => mutation.mutate({ id: 1 })}>Save</button>;
            }
            """
        )
        meta = next(symbol for symbol in symbols if symbol["name"] == "__file_meta__")
        features = set(meta["features"])

        self.assertIn("TanStackMutation", features)
        self.assertFalse(any(feature.startswith("MutationKeyDynamic:{") for feature in features))

    def test_state_flow_summarizes_keyless_tanstack_mutation_surface(self):
        summary = summarize_state_flow_features(["TanStackMutation"])

        self.assertTrue(summary["has_tanstack_mutation"])
        self.assertIn("tanstack-query", summary["technologies"])
        self.assertEqual(summary["mutation_keys"], [])

    def test_state_data_graph_treats_cache_writes_as_visible_reconciliation(self):
        self.assertTrue(_client_action_has_invalidation(["setQueryData"]))
        self.assertTrue(_client_action_has_invalidation(["invalidateQueries"]))
        self.assertTrue(_client_action_has_invalidation(["removeQueries"]))
        self.assertTrue(_client_action_has_invalidation(["resetQueries"]))
        self.assertFalse(_client_action_has_invalidation(["getQueryData"]))

    def test_react_hook_form_resolver_and_controller_surfaces_are_ast_features(self):
        symbols = self._run_ast_sequencer(
            """
            import { Controller, FormProvider, useFieldArray, useForm, useWatch } from 'react-hook-form';
            import { zodResolver } from '@hookform/resolvers/zod';
            import { z } from 'zod';

            const schema = z.object({ name: z.string() });

            export function ProfileForm() {
              const form = useForm({ resolver: zodResolver(schema) });
              const fields = useFieldArray({ control: form.control, name: 'items' });
              const name = useWatch({ control: form.control, name: 'name' });
              return <FormProvider {...form}>
                <Controller control={form.control} name="name" render={({ field }) => <input {...field} />} />
                {form.formState.errors.name && <span>Error</span>}
                <button onClick={form.handleSubmit(() => null)}>{name}{fields.fields.length}</button>
              </FormProvider>;
            }
            """
        )
        meta = next(symbol for symbol in symbols if symbol["name"] == "__file_meta__")
        features = set(meta["features"])

        self.assertIn("ReactForm", features)
        self.assertIn("FormResolver", features)
        self.assertIn("ValidationSchema", features)
        self.assertIn("ZodSchema", features)
        self.assertIn("FormProvider", features)
        self.assertIn("FormController", features)
        self.assertIn("FormFieldArray", features)
        self.assertIn("FormWatch", features)
        self.assertIn("FormErrorState", features)
        self.assertIn("FormSubmitHandler", features)

    def test_react_hook_form_generic_resolver_imports_are_ast_features(self):
        symbols = self._run_ast_sequencer(
            """
            import { useForm } from 'react-hook-form';
            import { yupResolver } from '@hookform/resolvers/yup';

            export function SettingsForm({ schema }) {
              const { register, handleSubmit, formState } = useForm({ resolver: yupResolver(schema) });
              return <form onSubmit={handleSubmit(() => null)}>
                <input {...register('email')} />
                {formState.errors.email && <span>Error</span>}
              </form>;
            }
            """
        )
        meta = next(symbol for symbol in symbols if symbol["name"] == "__file_meta__")
        features = set(meta["features"])

        self.assertIn("ReactForm", features)
        self.assertIn("FormResolver", features)
        self.assertIn("ValidationSchema", features)
        self.assertIn("FormFieldRegister", features)
        self.assertIn("FormSubmitHandler", features)
        self.assertIn("FormErrorState", features)
        self.assertNotIn("ZodSchema", features)

    def test_plain_action_object_is_not_react_router_action(self):
        symbols = self._run_ast_sequencer(
            """
            export function CommandPalette({ useShortcut }) {
              useShortcut({
                id: 'global-command-palette',
                keys: ['meta+k', 'ctrl+k'],
                action: () => console.log('open'),
                description: 'Open Global Command Menu',
              });
            }
            """
        )
        meta = next(symbol for symbol in symbols if symbol["name"] == "__file_meta__")
        features = set(meta["features"])

        self.assertNotIn("RouteAction", features)

    def test_react_router_route_object_preserves_action_and_loader_features(self):
        symbols = self._run_ast_sequencer(
            """
            import { createBrowserRouter } from 'react-router-dom';

            export const router = createBrowserRouter([
              {
                path: '/projects',
                loader: async () => null,
                action: async () => null,
                element: <div />,
              },
            ]);
            """
        )
        meta = next(symbol for symbol in symbols if symbol["name"] == "__file_meta__")
        features = set(meta["features"])

        self.assertIn("RouterConfig", features)
        self.assertIn("RouteLoader", features)
        self.assertIn("RouteAction", features)

    def test_configured_framework_rules_cover_tanstack_router_headless_and_optimistic_surfaces(self):
        symbols = self._run_ast_sequencer(
            """
            import { useOptimistic } from 'react';
            import { Dialog } from '@headlessui/react';
            import { useMutation } from '@tanstack/react-query';
            import { createFileRoute } from '@tanstack/react-router';

            export const Route = createFileRoute('/projects/$projectId')({ component: Project });
            export function Project() {
              const [items, addItem] = useOptimistic([]);
              useMutation({ mutationFn: async () => true, onMutate: addItem, onError: () => null });
              return <Dialog open={false} onClose={() => null}>{items.length}</Dialog>;
            }
            """
        )
        meta = next(symbol for symbol in symbols if symbol["name"] == "__file_meta__")
        features = set(meta["features"])

        self.assertIn("TanStackRouterFileRoute", features)
        self.assertIn("HeadlessComponentSurface", features)
        self.assertIn("ReactOptimisticState", features)
        self.assertIn("TanStackOptimisticMutation", features)
        self.assertIn("TanStackMutationRollback", features)

    def test_state_data_graph_marks_fixture_surfaces_as_reference_context(self):
        self.assertEqual(
            _source_context_for_path("packages/query-codemods/src/v4/__testfixtures__/parameter-is-object-expression.input.tsx"),
            "reference_or_test_surface",
        )
        self.assertEqual(
            _source_context_for_path("examples/react/basic/src/index.tsx"),
            "reference_or_test_surface",
        )
        self.assertEqual(
            _source_context_for_path("src/features/projects/useSaveProject.tsx"),
            "production_surface",
        )

    def test_state_data_graph_surfaces_zustand_selector_risks_with_actionability(self):
        rows = _zustand_selector_risk_rows(
            {
                "MAIN::src/features/auth/AuthPanel.tsx": {
                    "zustand_no_selector_calls": ["useAuthStore"],
                    "zustand_broad_selector_calls": [],
                },
                "MAIN::tests/basic.test.tsx": {
                    "zustand_no_selector_calls": ["useBoundStore"],
                    "zustand_broad_selector_calls": ["useBoundStore"],
                },
            }
        )

        production = next(row for row in rows if row["file"] == "src/features/auth/AuthPanel.tsx")
        reference = next(row for row in rows if row["file"] == "tests/basic.test.tsx")

        self.assertEqual(production["actionability"], "review")
        self.assertIn("zustand_selectorless_store_read", production["risks"])
        self.assertEqual(reference["actionability"], "reference_only")
        self.assertIn("zustand_selectorless_store_read", reference["risks"])
        self.assertIn("zustand_broad_selector_returns_store", reference["risks"])

    def test_use_sync_external_store_is_not_classified_as_zustand_consumer(self):
        summary = summarize_state_flow_features(
            [
                "Tech:selector:useSyncExternalStore",
                "Tech:selector_equality_fn",
                "Tech:useSyncExternalStore",
            ]
        )

        self.assertEqual(summary["zustand_consumers"], [])
        self.assertNotIn("zustand", summary["technologies"])
        self.assertEqual(summary["react_external_store_consumers"], ["useSyncExternalStore"])
        self.assertIn("react-external-store", summary["technologies"])


class QuantEngineActionabilityTests(unittest.TestCase):
    def test_partial_input_evidence_blocks_low_risk_classification(self):
        actionability = _signal_actionability(
            entry_kind="source",
            impact_score=0,
            direct_count=0,
            transitive_count=0,
            violation_count=0,
            cycle_count=0,
            evidence_status="PARTIAL",
            unavailable_inputs=("circular_deps", "audit_report"),
        )

        self.assertEqual(actionability["lane"], "evidence_refresh_required")
        self.assertEqual(actionability["priority"], "high")
        self.assertIn("circular_deps", actionability["reason"])

    def test_quant_input_shape_rejects_missing_required_collections(self):
        self.assertEqual(_input_shape_status("circular_deps", {"nodes": {}}), "invalid")
        self.assertEqual(_input_shape_status("blast_radius", {"blast_radius": []}), "valid")
        self.assertEqual(_input_shape_status("audit_report", {"violations": []}), "valid")

    def test_halo_ranking_is_deterministic_for_score_ties(self):
        nodes = ["MAIN::src/a.ts", "MAIN::src/b.ts", "MAIN::src/c.ts"]
        blast = [
            {"file": "MAIN::src/a.ts", "total_impact_score": 4},
            {"file": "MAIN::src/b.ts", "total_impact_score": 9},
            {"file": "MAIN::src/c.ts", "total_impact_score": 9},
        ]

        first = _rank_halo_nodes(nodes, blast, limit=3)
        second = _rank_halo_nodes(list(reversed(nodes)), blast, limit=3)

        self.assertEqual(first, second)
        self.assertEqual([row["node_key"] for row in first], ["MAIN::src/c.ts", "MAIN::src/b.ts", "MAIN::src/a.ts"])

    def test_cycle_or_violation_signal_is_act_now(self):
        actionability = _signal_actionability(
            entry_kind="source",
            impact_score=0,
            direct_count=0,
            transitive_count=0,
            violation_count=1,
            cycle_count=0,
        )

        self.assertEqual(actionability["lane"], "act_now")
        self.assertEqual(actionability["priority"], "high")

    def test_wide_blast_radius_signal_requires_review_before_edit(self):
        actionability = _signal_actionability(
            entry_kind="source",
            impact_score=12,
            direct_count=2,
            transitive_count=21,
            violation_count=0,
            cycle_count=0,
        )

        self.assertEqual(actionability["lane"], "review_before_edit")
        self.assertEqual(actionability["priority"], "medium")

    def test_config_signal_forces_governance_recheck(self):
        actionability = _signal_actionability(
            entry_kind="config",
            impact_score=0,
            direct_count=0,
            transitive_count=0,
            violation_count=0,
            cycle_count=0,
        )

        self.assertEqual(actionability["lane"], "governance_recheck")
        self.assertEqual(actionability["priority"], "high")


class ConfidenceEngineInputHonestyTests(unittest.TestCase):
    @patch(
        "tools.engines.confidence_engine._dependency_graph_evidence",
        return_value=({}, {"status": "UNKNOWN", "source": "missing", "shape_status": "invalid"}),
    )
    @patch("tools.engines.confidence_engine.load_atlas_data", return_value={})
    def test_missing_dependency_graph_cannot_be_reported_safe(self, _atlas, _evidence):
        result = evaluate_file_confidence("MAIN::src/example.ts", content="export const value = 1;")

        self.assertEqual(result["confidence_matrix"]["merge_safety"], "UNKNOWN")
        self.assertIn("unavailable", result["verdict"].lower())
        self.assertEqual(result["input_evidence"]["circular_deps"]["status"], "UNKNOWN")

    def test_confidence_brief_routes_unknown_evidence_to_refresh(self):
        brief = _render_confidence_brief(
            {
                "target": "MAIN::src/example.ts",
                "target_file": "src/example.ts",
                "target_ref": "MAIN::src/example.ts",
                "target_exists": True,
                "target_indexed": True,
                "confidence_matrix": {"merge_safety": "UNKNOWN"},
                "input_evidence": {
                    "circular_deps": {"status": "UNKNOWN", "source": "missing", "shape_status": "invalid"}
                },
            }
        )

        self.assertIn("refresh_dependency_evidence_before_confidence_decision", brief)
        self.assertNotIn("small_patch_allowed_after_file_inspection", brief)
        self.assertIn("status: \"UNKNOWN\"", brief)

    def test_confidence_aggregate_risk_keeps_high_architecture_risk_above_safe_blast_dimension(self):
        payload = {
            "target": "MAIN::src/example.ts",
            "target_file": "src/example.ts",
            "target_ref": "MAIN::src/example.ts",
            "target_exists": True,
            "target_indexed": True,
            "confidence_matrix": {
                "merge_safety": "SAFE",
                "architecture_drift_certainty": 0.95,
                "dead_code_confidence": 0.97,
            },
            "input_evidence": {
                "circular_deps": {"status": "PASS", "source": "sqlite", "shape_status": "valid"}
            },
        }

        normalized = _normalize_confidence_payload_for_agent(payload)
        brief = _render_confidence_brief(payload)

        self.assertEqual(normalized["confidence_matrix"]["aggregate_risk"], "HIGH")
        self.assertEqual(normalized["recommended_action"], "inspect_architecture_impact_and_tests_before_editing")
        self.assertIn('aggregate_risk: "HIGH"', brief)
        self.assertIn("SAFE cannot override architecture", brief)
        self.assertNotIn("small_patch_allowed_after_file_inspection", brief)

    def test_confidence_payload_without_explicit_target_grounding_fails_closed(self):
        normalized = _normalize_confidence_payload_for_agent(
            {
                "target": "MAIN::src/example.ts",
                "confidence_matrix": {"merge_safety": "SAFE"},
                "input_evidence": {
                    "circular_deps": {"status": "PASS", "source": "sqlite", "shape_status": "valid"}
                },
            }
        )
        brief = _render_confidence_brief(normalized)

        self.assertEqual(normalized["confidence_matrix"]["aggregate_risk"], "UNKNOWN")
        self.assertEqual(
            normalized["recommended_action"],
            "refresh_target_analysis_before_confidence_decision",
        )
        self.assertIn("target_exists: false", brief)
        self.assertIn("target_indexed: false", brief)
        self.assertIn("refresh_target_analysis_before_confidence_decision", brief)

    @patch("tools.mcp.server.load_atlas_data", return_value={"MAIN": {"files": {}}})
    @patch("tools.mcp.server._resolve_target_node_from_raw", return_value=("MAIN::src/example.ts", {"repo_relative_path": "src/example.ts"}))
    @patch("tools.mcp.server._target_path_status", return_value={"exists": True, "indexed": True})
    @patch("tools.mcp.server._load_json", return_value={})
    @patch("tools.mcp.server.artifact_state_meta", return_value={"exists": False, "source": "missing"})
    @patch("tools.mcp.server._target_ref_from_node", return_value="MAIN::src/example.ts")
    def test_external_target_confidence_does_not_report_safe_without_graph(
        self, _target_ref, _meta, _load, _target_status, _resolve, _atlas
    ):
        result = _confidence_from_raw(Path("unused"), "src/example.ts")

        self.assertEqual(result["confidence_matrix"]["merge_safety"], "UNKNOWN")
        self.assertEqual(result["input_evidence"]["circular_deps"]["status"], "UNKNOWN")


class HealthScoreInputHonestyTests(unittest.TestCase):
    def test_empty_or_malformed_health_inputs_are_not_valid_evidence(self):
        self.assertEqual(_health_input_shape_status("genome", {}), "invalid")
        self.assertEqual(_health_input_shape_status("circular_deps", {"cycles": []}), "invalid")
        self.assertEqual(_health_input_shape_status("dead_code", {"items": []}), "valid")
        self.assertEqual(_health_input_shape_status("surgical_discovery", []), "valid")


class BlastRadiusReachabilityTests(unittest.TestCase):
    def test_reachable_uses_memo_without_changing_transitive_dependents(self):
        reverse_graph = {
            "MAIN::src/core.ts": ["MAIN::src/widget.tsx", "MAIN::src/page.tsx"],
            "MAIN::src/widget.tsx": ["MAIN::src/shell.tsx"],
            "MAIN::src/page.tsx": [],
            "MAIN::src/shell.tsx": [],
        }
        memo = {}

        reachable = get_reachable("MAIN::src/core.ts", reverse_graph, memo)
        repeated = get_reachable("MAIN::src/core.ts", reverse_graph, memo)

        self.assertEqual(
            reachable,
            {"MAIN::src/widget.tsx", "MAIN::src/page.tsx", "MAIN::src/shell.tsx"},
        )
        self.assertEqual(repeated, reachable)
        self.assertIn("MAIN::src/core.ts", memo)


class AuditReportCacheTests(unittest.TestCase):
    def test_audit_report_cache_can_be_invalidated_after_regeneration(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw_dir = Path(tmp)
            report_path = raw_dir / "audit_report.json"
            report_path.write_text(json.dumps({"summary": {"total": 1}}), encoding="utf-8")
            audit_report_core._cached_audit_report.cache_clear()
            with patch.object(audit_report_core, "RAW_DIR", raw_dir):
                self.assertEqual(audit_report_core.get_total_violations(), 1)
                report_path.write_text(json.dumps({"summary": {"total": 7}}), encoding="utf-8")
                self.assertEqual(audit_report_core.get_total_violations(), 1)
                audit_report_core.invalidate_audit_report_cache()
                self.assertEqual(audit_report_core.get_total_violations(), 7)


class TestImpactProfileTests(unittest.TestCase):
    def test_typescript_tests_folder_file_without_spec_suffix_is_test_surface(self):
        test_path = "packages/ui/src/Button/__tests__/Button.tsx"

        self.assertTrue(is_test_file(test_path))
        self.assertEqual(extract_base_name(test_path), "Button")
        self.assertIn(test_path, generate_test_command(test_path))

    def test_ignored_path_fragments_still_override_test_folder_detection(self):
        self.assertFalse(is_test_file("node_modules/pkg/__tests__/Button.tsx"))

    def test_mcp_test_impact_fallback_uses_canonical_polyglot_profile(self):
        test_path = "tools/tests/test_reality_scope.py"

        self.assertTrue(_is_likely_test_path(test_path))
        self.assertEqual(_base_without_test_suffix(test_path), "reality_scope")
        self.assertEqual(test_impact_profile_core.command_for_test(test_path), f"pytest {test_path}")

    def test_incomplete_profile_fails_closed_without_local_profile_fallback(self):
        from tools.core.json_io import clear_strict_json_content_cache

        with tempfile.TemporaryDirectory() as temp_dir:
            profile_path = Path(temp_dir) / "test_impact_profiles.json"
            profile_path.write_text('{"languages": {}}', encoding="utf-8")
            clear_strict_json_content_cache()
            with patch.object(test_impact_profile_core, "PROFILE_PATH", profile_path):
                with self.assertRaisesRegex(ValueError, "mandatory non-empty field"):
                    test_impact_profile_core.load_test_impact_profiles()
            clear_strict_json_content_cache()


class LanguageRegistrySmokeTests(unittest.TestCase):
    def test_modern_node_module_extensions_are_registry_owned(self):
        self.assertEqual(language_for_extension(".mts"), "typescript")
        self.assertEqual(language_for_extension(".cts"), "typescript")
        self.assertEqual(language_for_extension(".mjs"), "javascript")
        self.assertEqual(language_for_extension(".cjs"), "javascript")
        self.assertIn(".mts", watch_extensions())
        self.assertIn(".mjs", watch_extensions())
        self.assertIn("index.mts", index_files())
        self.assertIn("index.cjs", index_files())

    def test_unknown_and_observation_only_extensions_do_not_inherit_typescript(self):
        self.assertEqual(language_for_extension(".rb"), "unknown")
        self.assertEqual(language_for_extension(".vue"), "unknown")

    def test_modern_node_module_test_files_follow_test_impact_profile(self):
        self.assertTrue(is_test_file("packages/config/__tests__/vite.config.mts"))
        self.assertTrue(is_test_file("packages/config/vite.config.spec.mjs"))
        self.assertEqual(extract_base_name("packages/config/vite.config.spec.mjs"), "vite.config")


class ReactEvidenceContractTests(unittest.TestCase):
    def test_needs_runtime_proof_kind_caps_confidence_until_runtime_evidence_exists(self):
        item = attach_react_evidence_contract(
            {
                "project": "APP",
                "file": "src/components/HydratedPanel.tsx",
                "dimension": "hydration_contract",
                "risk": "hydration_mismatch_requires_runtime_probe",
                "score": 9,
                "confidence": "confirmed",
            },
            evidence_kinds={"static_regex", "needs_runtime_proof"},
        )

        self.assertEqual(item["confidence"], "needs_runtime_proof")
        self.assertEqual(item["runtime_proof_status"], "needs_runtime_proof")
        self.assertIn("runtime_proof_required", item["evidence_ladder"])


class ReactRuntimeSurfaceContextTests(unittest.TestCase):
    def test_render_mutation_detector_ignores_event_handler_ref_mutations(self):
        source = """
export function usePanZoom() {
  const lastMousePos = useRef(null);
  const handleMouseMove = useCallback((event) => {
    lastMousePos.current = { x: event.clientX, y: event.clientY };
  }, []);
  return { handleMouseMove };
}
"""

        self.assertEqual(_render_mutation_lines(source), [])

    def test_render_mutation_detector_reports_top_level_render_mutation(self):
        source = """
export function BadComponent(props) {
  props.value = 1;
  return null;
}
"""

        self.assertEqual(_render_mutation_lines(source), [3])

    def test_docs_demo_runtime_finding_keeps_signal_but_lowers_actionability(self):
        calibrated = _calibrate_with_ecosystem(
            [
                {
                    "project": "MAIN",
                    "file": "docs/data/material/components/autocomplete/AutocompleteHint.tsx",
                    "line": 7,
                    "dimension": "react_compiler_readiness",
                    "risk": "compiler_or_memoization_contract_risk",
                    "risk_tier": "medium",
                    "confidence": "probable",
                    "score": 6,
                    "evidence_kinds": ["atlas_feature"],
                    "runtime_proof_status": "static_correlated",
                }
            ],
            [],
        )

        finding = calibrated[0]
        self.assertEqual(finding["source_context"], "docs_demo")
        self.assertEqual(finding["actionability"], "reference_only")
        self.assertEqual(finding["calibration_lane"], "observe")
        self.assertEqual(finding["false_positive_risk"], "medium")

    def test_compiler_readiness_observes_reference_only_surfaces(self):
        normalized = normalize_compiler_finding(
            {
                "project": "MAIN",
                "file": "docs/data/material/components/autocomplete/AutocompleteHint.tsx",
                "dimension": "react_compiler_readiness",
                "risk": "compiler_or_memoization_contract_risk",
                "risk_tier": "medium",
                "confidence": "probable",
                "score": 6,
                "actionability": "reference_only",
                "source_context": "docs_demo",
            },
            "react_runtime_intelligence",
        )

        self.assertEqual(normalized["readiness_status"], "observe")
        self.assertEqual(normalized["source_context"], "docs_demo")


class ValidationOracleContractTests(unittest.TestCase):
    def test_syntax_and_resolution_errors_are_summarized_separately(self):
        oracle = ValidationOracle(".")

        summary = oracle._summarize_errors(
            [
                {
                    "classification": "malformed_jsx_token",
                    "reason": "Malformed JSX token fragment detected.",
                    "severity": "high",
                },
                {
                    "classification": "relocated_alias_candidate",
                    "reason": "Alias target appears relocated.",
                    "severity": "medium",
                },
            ]
        )

        self.assertEqual(summary["syntax_errors"], 1)
        self.assertEqual(summary["resolution_errors"], 1)
        self.assertEqual(summary["alias_errors"], 1)
        self.assertEqual(summary["deep_tsc_errors"], 0)
        self.assertEqual(summary["classifications"]["malformed_jsx_token"], 1)
        self.assertEqual(summary["classifications"]["relocated_alias_candidate"], 1)


class CorePolicyContractTests(unittest.TestCase):
    def test_dead_code_allowlist_scope_accepts_legacy_project_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            golden_dir = config_dir / "golden"
            golden_dir.mkdir()
            (golden_dir / "dead_code_policy.json").write_text(
                json.dumps({"include_project_allowlists": True}),
                encoding="utf-8",
            )

            scope = get_allowlist_scope(config_dir)

        self.assertEqual(scope, "all")
        self.assertTrue(scope_allows_global(scope))
        self.assertTrue(scope_allows_project(scope))
        self.assertTrue(is_valid_scope(scope))

    def test_dead_code_allowlist_scope_defaults_to_global_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            scope = get_allowlist_scope(Path(tmp))

        self.assertEqual(scope, "global_only")
        self.assertTrue(scope_allows_global(scope))
        self.assertFalse(scope_allows_project(scope))


class ArtifactStoreSQLiteContractTests(unittest.TestCase):
    def test_json_primary_store_is_readable_when_save_returns(self):
        store = ArtifactStore()
        store.use_sqlite = False
        with tempfile.TemporaryDirectory() as tmp:
            raw_path = Path(tmp) / "sample.json"
            with (
                patch.dict("os.environ", {}, clear=False),
                patch.object(store, "raw_path", return_value=raw_path),
            ):
                os.environ.pop("SAGE_SYNC_SHADOW_WRITES", None)
                profile = store.save_raw("sample", {"ok": True})
                loaded = store.load_raw("sample", {})

        self.assertEqual(loaded, {"ok": True})
        self.assertEqual(profile["shadow_write_mode"], "synchronous_primary_json")
        self.assertFalse(profile["shadow_thread_started"])

    def test_json_primary_store_write_failure_is_not_hidden(self):
        store = ArtifactStore()
        store.use_sqlite = False
        with tempfile.TemporaryDirectory() as tmp:
            raw_path = Path(tmp) / "sample.json"
            with (
                patch.object(store, "raw_path", return_value=raw_path),
                patch.object(artifact_store, "save_json_atomic", side_effect=OSError("disk unavailable")),
            ):
                with self.assertRaisesRegex(ArtifactPrimaryWriteError, "JSON primary artifact write failed"):
                    store.save_raw("sample", {"ok": True})

    def test_managed_primary_write_failure_does_not_downgrade_to_direct_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw_dir = Path(tmp)
            raw_path = raw_dir / "sample.json"
            with (
                patch.object(core_config, "RAW_DIR", raw_dir),
                patch.object(artifact_store, "RAW_DIR", raw_dir),
                patch.object(
                    artifact_store.STORE,
                    "save_raw",
                    side_effect=ArtifactPrimaryWriteError("primary unavailable"),
                ),
                patch.object(core_config, "save_text_atomic") as direct_write,
            ):
                with self.assertRaisesRegex(ArtifactPrimaryWriteError, "primary unavailable"):
                    core_config.save_json_atomic(raw_path, {"ok": True})

        direct_write.assert_not_called()

    def test_sqlite_primary_failure_does_not_start_shadow_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ArtifactStore(raw_dir=Path(tmp))
            store.use_sqlite = True
            store.backend = "hybrid_sqlite"
            store._schema_initialized = False
            with (
                patch.object(store, "_save_payload_to_state_table", side_effect=OSError("database unavailable")),
                patch.object(artifact_store, "_bg_write_worker") as worker,
            ):
                with self.assertRaisesRegex(ArtifactPrimaryWriteError, "SQLite primary artifact write failed"):
                    store.save_raw("sample", {"ok": True})

        worker.assert_not_called()

    def test_sqlite_primary_store_keeps_shadow_write_asynchronous(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ArtifactStore(raw_dir=Path(tmp))
            store.use_sqlite = True
            store.backend = "hybrid_sqlite"
            store._schema_initialized = False
            with patch.dict("os.environ", {}, clear=False):
                os.environ.pop("SAGE_SYNC_SHADOW_WRITES", None)
                profile = store.save_raw("sample", {"ok": True})
                loaded = store.load_raw("sample", {})
                self.assertTrue(artifact_store.flush_shadow_writes())

        self.assertEqual(loaded, {"ok": True})
        self.assertEqual(profile["shadow_write_mode"], "asynchronous")
        self.assertTrue(profile["shadow_thread_started"])

    def test_sqlite_shadow_worker_lifecycle_is_run_owned_and_content_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ArtifactStore(raw_dir=Path(tmp))
            store.use_sqlite = True
            store.backend = "hybrid_sqlite"
            store._schema_initialized = False
            worker_started = threading.Event()
            allow_completion = threading.Event()
            original_save = artifact_store.save_json_atomic

            def delayed_shadow_write(*args, **kwargs):
                worker_started.set()
                self.assertTrue(allow_completion.wait(timeout=2.0))
                return original_save(*args, **kwargs)

            artifact_store.activate_shadow_write_run("sage-run-shadow-owned")
            try:
                with patch.object(artifact_store, "save_json_atomic", side_effect=delayed_shadow_write):
                    profile = store.save_raw("private_artifact", {"secret": "must-not-appear"})
                    self.assertTrue(worker_started.wait(timeout=1.0))
                    observed = artifact_store.shadow_write_lifecycle_snapshot("sage-run-shadow-owned")
                    short_flush = artifact_store.flush_shadow_writes_report(
                        timeout=0.01,
                        run_id="sage-run-shadow-owned",
                    )
                    allow_completion.set()
                    final_flush = artifact_store.flush_shadow_writes_report(
                        timeout=1.0,
                        run_id="sage-run-shadow-owned",
                    )

                released = artifact_store.release_shadow_write_run("sage-run-shadow-owned")
            finally:
                allow_completion.set()
                artifact_store.flush_shadow_writes(timeout=1.0)
                if artifact_store.current_shadow_write_run_id() == "sage-run-shadow-owned":
                    artifact_store.release_shadow_write_run("sage-run-shadow-owned")

        self.assertEqual(profile["shadow_run_id"], "sage-run-shadow-owned")
        self.assertEqual(observed["started_count"], 1)
        self.assertEqual(observed["pending_count"], 1)
        self.assertEqual(observed["artifacts"], ["private_artifact"])
        self.assertEqual(observed["identity_limit"], 50)
        self.assertFalse(observed["identities_truncated"])
        self.assertNotIn("must-not-appear", json.dumps(observed))
        self.assertNotIn(str(Path(tmp)), json.dumps(observed))
        self.assertFalse(short_flush["complete"])
        self.assertTrue(short_flush["timed_out"])
        self.assertTrue(final_flush["complete"])
        self.assertEqual(final_flush["completed_count"], 1)
        self.assertEqual(released["pending_count"], 0)
        self.assertEqual(artifact_store.current_shadow_write_run_id(), "")

    def test_bounded_process_shadow_mode_is_synchronous(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ArtifactStore(raw_dir=Path(tmp))
            store.use_sqlite = True
            store.backend = "hybrid_sqlite"
            store._schema_initialized = False
            raw_path = Path(tmp) / "sample.json"
            with (
                patch.dict("os.environ", {"SAGE_SYNC_SHADOW_WRITES": "1"}),
                patch.object(store, "raw_path", return_value=raw_path),
                patch.object(artifact_store, "_bg_write_worker", wraps=artifact_store._bg_write_worker) as worker,
            ):
                profile = store.save_raw("sample", {"ok": True})
                loaded = store.load_raw("sample", {})

        self.assertEqual(loaded, {"ok": True})
        self.assertEqual(profile["shadow_write_mode"], "synchronous_bounded_process")
        self.assertFalse(profile["shadow_thread_started"])
        worker.assert_called_once()

    def test_logger_and_profile_diagnostics_do_not_write_to_stdout(self):
        logger_source = (CODE_MAPS_DIR / "tools" / "core" / "logger.py").read_text(encoding="utf-8")
        store_source = (CODE_MAPS_DIR / "tools" / "core" / "artifact_store.py").read_text(encoding="utf-8")

        self.assertIn("logging.StreamHandler(sys.stderr)", logger_source)
        profile_body = store_source.split("def _artifact_profile_log", 1)[1].split(
            "def _record_store_degradation", 1
        )[0]
        self.assertNotIn("print(", profile_body)

    def test_post_commit_profile_console_failure_does_not_escape_as_storage_failure(self):
        with patch("builtins.print", side_effect=OSError(22, "Invalid argument")):
            _artifact_profile_log({"artifact": "proof", "sqlite_enabled": True})

    def test_inflight_progress_console_failure_does_not_escape_as_validation_failure(self):
        from tools.validate_react_support import _log

        with patch("builtins.print", side_effect=OSError(22, "Invalid argument")):
            _log("still-running")

    def test_load_raw_initializes_schema_before_first_select(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = ArtifactStore()
            store.use_sqlite = True
            store.backend = "hybrid_sqlite"
            store.db_manager = SQLiteManager(root / "codemaps.db")
            store._schema_initialized = False

            self.assertEqual(store.load_raw("missing_payload", {"ok": True}), {"ok": True})

            with store.db_manager.get_connection() as conn:
                row = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='state_payloads';"
                ).fetchone()
            self.assertIsNotNone(row)
            self.assertTrue(store._schema_initialized)

    def test_raw_metadata_prefers_sqlite_source_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = ArtifactStore()
            store.use_sqlite = True
            store.backend = "hybrid_sqlite"
            store.db_manager = SQLiteManager(root / "codemaps.db")
            store._schema_initialized = False
            store._ensure_schema()
            store._save_payload_to_state_table("proof", {"ok": True}, source_mtime=123.5)

            metadata = store.raw_metadata("proof")

            self.assertEqual(metadata["truth_source"], "sqlite_state_payloads")
            self.assertEqual(metadata["source_mtime"], 123.5)
            self.assertTrue(metadata["payload_sha"])

    def test_state_payload_write_reports_serialization_hash_and_sqlite_phases(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = ArtifactStore()
            store.use_sqlite = True
            store.backend = "hybrid_sqlite"
            store.db_manager = SQLiteManager(root / "codemaps.db")
            store._schema_initialized = False
            store._ensure_schema()

            profile = store._save_payload_to_state_table("proof", {"value": "cafe"}, source_mtime=123.5)

            self.assertGreater(profile["state_payload_bytes"], 0)
            self.assertGreater(profile["state_payload_chars"], 0)
            self.assertGreaterEqual(profile["state_payload_serialize_seconds"], 0.0)
            self.assertGreaterEqual(profile["state_payload_encode_seconds"], 0.0)
            self.assertGreaterEqual(profile["state_payload_hash_seconds"], 0.0)
            self.assertGreaterEqual(profile["state_payload_sqlite_seconds"], 0.0)

    def test_same_payload_rewrite_preserves_content_change_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = ArtifactStore()
            store.use_sqlite = True
            store.backend = "hybrid_sqlite"
            store.db_manager = SQLiteManager(root / "codemaps.db")
            store._schema_initialized = False
            store._ensure_schema()

            store._save_payload_to_state_table("proof", {"value": 1}, source_mtime=100.0)
            store._save_payload_to_state_table("proof", {"value": 1}, source_mtime=200.0)
            self.assertEqual(store.raw_metadata("proof")["source_mtime"], 100.0)

            store._save_payload_to_state_table("proof", {"value": 2}, source_mtime=300.0)
            self.assertEqual(store.raw_metadata("proof")["source_mtime"], 300.0)

    def test_failure_drill_cleanup_is_confined_to_reserved_isolated_namespace(self):
        from tools import validate_entrypoints_and_failures as entrypoint_validation

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            external_targets = root / "output" / "external_targets"
            allowed = external_targets / "sage_entrypoint_failure_drill_test_123"
            refused = external_targets / "customer_repo_123"
            allowed.mkdir(parents=True)
            refused.mkdir(parents=True)
            (allowed / "proof.json").write_text("{}", encoding="utf-8")

            with patch.object(entrypoint_validation, "CODE_MAPS_DIR", root):
                entrypoint_validation._cleanup_isolated_output(allowed)
                self.assertFalse(allowed.exists())
                with self.assertRaises(ValueError):
                    entrypoint_validation._cleanup_isolated_output(refused)

            self.assertTrue(refused.exists())

    def test_failure_drill_startup_cleanup_removes_only_stale_reserved_outputs(self):
        from tools import validate_entrypoints_and_failures as entrypoint_validation

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            external_targets = root / "output" / "external_targets"
            stale_a = external_targets / "sage_entrypoint_failure_drill_old_a"
            stale_b = external_targets / "sage_entrypoint_failure_drill_old_b"
            customer = external_targets / "customer_repo_123"
            for path in (stale_a, stale_b, customer):
                path.mkdir(parents=True)
            stale_after_seconds = (
                (2 * entrypoint_validation.entrypoint_failure_dead_code_drill_timeout_seconds())
                + entrypoint_validation.entrypoint_failure_default_timeout_seconds()
                + entrypoint_validation.ENTRYPOINT_HEARTBEAT_SECONDS
            )
            old_timestamp = __import__("time").time() - stale_after_seconds - 10
            for path in (stale_a, stale_b):
                os.utime(path, (old_timestamp, old_timestamp))

            with patch.object(entrypoint_validation, "CODE_MAPS_DIR", root):
                cleaned = entrypoint_validation._cleanup_stale_isolated_outputs()

            self.assertEqual(
                cleaned,
                ["sage_entrypoint_failure_drill_old_a", "sage_entrypoint_failure_drill_old_b"],
            )
            self.assertFalse(stale_a.exists())
            self.assertFalse(stale_b.exists())
            self.assertTrue(customer.exists())

    def test_failure_drill_startup_cleanup_preserves_recent_concurrent_output(self):
        from tools import validate_entrypoints_and_failures as entrypoint_validation

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            active = (
                root
                / "output"
                / "external_targets"
                / "sage_entrypoint_failure_drill_active_123"
            )
            active.mkdir(parents=True)
            (active / "heartbeat.json").write_text("{}", encoding="utf-8")

            with patch.object(entrypoint_validation, "CODE_MAPS_DIR", root):
                cleaned = entrypoint_validation._cleanup_stale_isolated_outputs()

            self.assertEqual(cleaned, [])
            self.assertTrue(active.exists())

    def test_failure_drill_missing_subprocess_output_is_structured(self):
        from types import SimpleNamespace

        from tools import validate_entrypoints_and_failures as entrypoint_validation

        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "quality_gate.json"
            payload, diagnostic = entrypoint_validation._load_subprocess_json_output(
                missing,
                SimpleNamespace(returncode=1, stdout="quality stdout", stderr="quality stderr"),
                label="quality_gate_missing_health_stays_controlled",
            )

        self.assertEqual(payload, {})
        self.assertEqual(diagnostic["read_error"], "expected_output_missing")
        self.assertFalse(diagnostic["output_exists"])
        self.assertEqual(diagnostic["returncode"], 1)
        self.assertEqual(diagnostic["stdout"], "quality stdout")
        self.assertEqual(diagnostic["stderr"], "quality stderr")

    def test_python_subprocess_entrypoints_are_platform_neutral_and_failure_observable(self):
        launcher_paths = [
            Path("tools/validate_entrypoints_and_failures.py"),
            Path("tools/validate_operational_parity.py"),
            Path("tools/validate_step_isolation.py"),
            Path("tools/ci_release_check.py"),
        ]
        for relative_path in launcher_paths:
            text = (CODE_MAPS_DIR / relative_path).read_text(encoding="utf-8")
            self.assertNotIn('".\\\\sage.py"', text, str(relative_path))
            self.assertNotIn('".\\\\tools\\\\', text, str(relative_path))
            self.assertNotIn('"tools/run_release_proof_bundle.py"', text, str(relative_path))
            self.assertIn("sys.executable", text, str(relative_path))

        entrypoint_text = (CODE_MAPS_DIR / launcher_paths[0]).read_text(encoding="utf-8")
        self.assertIn(
            "preview, preview_diagnostic = _load_subprocess_json_output(",
            entrypoint_text,
        )

    def test_import_cycle_no_worsening_rejects_new_topology_and_requires_immediate_baseline_shrink(self):
        from tools.validate_layer_import_boundaries import (
            _cycle_baseline_drift,
            _cycle_baseline_contract_errors,
            _unexpected_cycle_components,
        )

        baseline = [
            {
                "modules": ["tools.a", "tools.b", "tools.c"],
                "maximum_internal_edges": 4,
                "edges": ["tools.a->tools.b", "tools.b->tools.a", "tools.b->tools.c", "tools.c->tools.b"],
            }
        ]
        shrunk = [
            {
                "modules": ["tools.a", "tools.b"],
                "internal_edges": 2,
                "edges": ["tools.a->tools.b", "tools.b->tools.a"],
            }
        ]
        self.assertEqual(_unexpected_cycle_components(shrunk, baseline), [])
        shrink_drift = _cycle_baseline_drift(shrunk, baseline)
        self.assertTrue(any(row["reason"] == "current_component_not_exactly_baselined" for row in shrink_drift))
        self.assertTrue(any(row["reason"] == "baseline_component_no_longer_current" for row in shrink_drift))
        self.assertEqual(_cycle_baseline_drift(baseline, baseline), [])
        self.assertEqual(
            _unexpected_cycle_components(
                [
                    {
                        "modules": ["tools.x", "tools.y"],
                        "internal_edges": 2,
                        "edges": ["tools.x->tools.y", "tools.y->tools.x"],
                    }
                ],
                baseline,
            )[0]["reason"],
            "new_cycle_family",
        )
        self.assertEqual(
            _unexpected_cycle_components(
                [
                    {
                        "modules": ["tools.a", "tools.b", "tools.c"],
                        "internal_edges": 4,
                        "edges": ["tools.a->tools.b", "tools.b->tools.a", "tools.a->tools.c", "tools.c->tools.b"],
                    }
                ],
                baseline,
            )[0]["reason"],
            "known_cycle_contains_new_internal_edges",
        )
        self.assertEqual(_cycle_baseline_contract_errors(baseline), [])
        malformed = [
            {
                "modules": ["tools.a", "tools.b"],
                "maximum_internal_edges": 1,
                "edges": ["tools.a->tools.b", "tools.a->tools.missing"],
            }
        ]
        malformed_errors = _cycle_baseline_contract_errors(malformed)
        self.assertTrue(any("edge_count_mismatch" in error for error in malformed_errors))
        self.assertTrue(any("invalid_edge" in error for error in malformed_errors))

    def test_repository_import_graph_keeps_every_module_in_multi_import_nodes(self):
        from tools import validate_layer_import_boundaries as import_validation

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "tools" / "source.py"
            module_a = root / "tools" / "a.py"
            module_b = root / "tools" / "b.py"
            source.parent.mkdir(parents=True)
            package = root / "tools" / "__init__.py"
            package.write_text("", encoding="utf-8")
            source.write_text("from tools import a, b\n", encoding="utf-8")
            module_a.write_text("", encoding="utf-8")
            module_b.write_text("", encoding="utf-8")
            module_by_path = {
                package: "tools",
                source: "tools.source",
                module_a: "tools.a",
                module_b: "tools.b",
            }
            path_by_module = {module: path for path, module in module_by_path.items()}

            with patch.object(import_validation, "CODE_MAPS_DIR", root):
                targets, errors = import_validation._internal_import_targets(
                    source,
                    module_by_path=module_by_path,
                    path_by_module=path_by_module,
                )

            self.assertEqual(errors, [])
            self.assertEqual(targets, {"tools", "tools.a", "tools.b"})

    def test_layer_import_schema_rejects_missing_cycle_assurance_projection(self):
        from tools.core.artifact_validator import validate_payload

        errors = validate_payload(
            "layer_import_boundary_validation",
            {
                "meta": {"kind": "layer_import_boundary_validation", "version": "v1"},
                "summary": {
                    "status": "PASS",
                    "protected_files": 1,
                    "imports_checked": 1,
                    "allowed_imports": 1,
                    "violations": 0,
                    "scope_errors": 0,
                },
                "validation_scope": {},
                "scope_errors": [],
                "violations": [],
                "findings": [],
            },
        )

        self.assertTrue(any("repository_cycle_no_worsening" in error for error in errors))

    def test_scoped_atlas_projection_preserves_unrelated_proof_and_updates_changed_rows(self):
        from tools.core import artifact_store as artifact_store_module

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.ts").write_text("export const A = 1;\n", encoding="utf-8")
            (root / "b.ts").write_text("export const B = 1;\n", encoding="utf-8")
            store = ArtifactStore()
            store.use_sqlite = True
            store.backend = "hybrid_sqlite"
            store.db_manager = SQLiteManager(root / "codemaps.db")
            store._schema_initialized = False
            store._ensure_schema()
            atlas = {
                "MAIN": {
                    "root_path": str(root),
                    "project_type": "typescript",
                    "files": {
                        "a.ts": {
                            "language": "typescript",
                            "size": 20,
                            "hash": "hash-a1",
                            "symbols": [{"name": "A", "type": "variable", "line": 1, "end_line": 1}],
                        },
                        "b.ts": {
                            "language": "typescript",
                            "size": 20,
                            "hash": "hash-b1",
                            "symbols": [{"name": "B", "type": "variable", "line": 1, "end_line": 1}],
                        },
                    },
                    "dependencies": {"a.ts": ["b.ts"], "b.ts": []},
                    "symbols": [],
                }
            }
            store._save_atlas_to_sqlite(atlas)

            with store.db_manager.get_connection() as conn:
                ids_before = {
                    str(row["rel_path"]): int(row["file_id"])
                    for row in conn.execute("SELECT file_id, rel_path FROM files;").fetchall()
                }
                b_snapshot_before = dict(
                    conn.execute(
                        "SELECT content, content_hash, source_mtime, updated_at FROM source_snapshots WHERE project_key = ? AND rel_path = ?;",
                        ("MAIN", "b.ts"),
                    ).fetchone()
                )
                conn.execute(
                    "INSERT INTO findings (engine_name, file_id, severity, code, message) VALUES (?, ?, ?, ?, ?);",
                    ("audit_report", ids_before["b.ts"], "warning", "proof", "preserve me"),
                )

            updated = json.loads(json.dumps(atlas))
            updated["MAIN"]["files"]["a.ts"].update(
                {
                    "hash": "hash-a2",
                    "symbols": [{"name": "A2", "type": "variable", "line": 1, "end_line": 1}],
                }
            )
            updated["MAIN"]["dependencies"]["a.ts"] = []
            (root / "a.ts").write_text("export const A2 = 2;\n", encoding="utf-8")

            with patch.dict(
                artifact_store_module.DYNAMIC_CONFIG,
                {"_source_snapshot_projection_scope": {"MAIN": ["a.ts"]}},
                clear=False,
            ):
                store._save_atlas_to_sqlite(updated)

            with store.db_manager.get_connection() as conn:
                ids_after = {
                    str(row["rel_path"]): int(row["file_id"])
                    for row in conn.execute("SELECT file_id, rel_path FROM files;").fetchall()
                }
                b_snapshot_after = dict(
                    conn.execute(
                        "SELECT content, content_hash, source_mtime, updated_at FROM source_snapshots WHERE project_key = ? AND rel_path = ?;",
                        ("MAIN", "b.ts"),
                    ).fetchone()
                )
                symbol_names = {
                    str(row["name"])
                    for row in conn.execute(
                        "SELECT name FROM symbols WHERE file_id = ?;",
                        (ids_after["a.ts"],),
                    ).fetchall()
                }
                dependency_count = int(
                    conn.execute(
                        "SELECT COUNT(1) FROM dependencies WHERE source_file_id = ?;",
                        (ids_after["a.ts"],),
                    ).fetchone()[0]
                )
                finding_count = int(
                    conn.execute(
                        "SELECT COUNT(1) FROM findings WHERE engine_name = ?;",
                        ("audit_report",),
                    ).fetchone()[0]
                )

            self.assertEqual(ids_after, ids_before)
            self.assertEqual(b_snapshot_after, b_snapshot_before)
            self.assertEqual(symbol_names, {"A2"})
            self.assertEqual(dependency_count, 0)
            self.assertEqual(finding_count, 1)

    def test_scoped_atlas_projection_full_fallback_restores_canonical_audit_findings(self):
        from tools.core import artifact_store as artifact_store_module

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.ts").write_text("export const A = 1;\n", encoding="utf-8")
            (root / "b.ts").write_text("export const B = 1;\n", encoding="utf-8")
            store = ArtifactStore()
            store.use_sqlite = True
            store.backend = "hybrid_sqlite"
            store.db_manager = SQLiteManager(root / "codemaps.db")
            store._schema_initialized = False
            store._ensure_schema()
            atlas = {
                "MAIN": {
                    "root_path": str(root),
                    "project_type": "typescript",
                    "files": {
                        "a.ts": {"language": "typescript", "size": 20, "hash": "hash-a", "symbols": []},
                        "b.ts": {"language": "typescript", "size": 20, "hash": "hash-b", "symbols": []},
                    },
                    "dependencies": {"a.ts": [], "b.ts": []},
                    "symbols": [],
                }
            }
            store._save_atlas_to_sqlite(atlas)
            store._save_payload_to_state_table(
                "audit_report",
                {
                    "violations": [
                        {
                            "project": "MAIN",
                            "project_key": "MAIN",
                            "file": "b.ts",
                            "rule": "proof",
                            "detail": "preserve through fallback",
                        }
                    ]
                },
            )
            with store.db_manager.get_connection() as conn:
                a_file_id = int(
                    conn.execute(
                        "SELECT file_id FROM files WHERE project_key = ? AND rel_path = ?;",
                        ("MAIN", "a.ts"),
                    ).fetchone()[0]
                )
                conn.execute(
                    "INSERT INTO findings (engine_name, file_id, severity, code, message) VALUES (?, ?, ?, ?, ?);",
                    ("future_engine", a_file_id, "info", "future-proof", "preserve generic owner"),
                )
                conn.execute("DELETE FROM files WHERE project_key = ? AND rel_path = ?;", ("MAIN", "b.ts"))

            with patch.dict(
                artifact_store_module.DYNAMIC_CONFIG,
                {"_source_snapshot_projection_scope": {"MAIN": ["a.ts"]}},
                clear=False,
            ):
                profile = store._save_atlas_to_sqlite(atlas)

            with store.db_manager.get_connection() as conn:
                file_count = int(conn.execute("SELECT COUNT(1) FROM files;").fetchone()[0])
                finding_count = int(
                    conn.execute(
                        "SELECT COUNT(1) FROM findings WHERE engine_name = ?;",
                        ("audit_report",),
                    ).fetchone()[0]
                )
                future_finding_count = int(
                    conn.execute(
                        "SELECT COUNT(1) FROM findings WHERE engine_name = ?;",
                        ("future_engine",),
                    ).fetchone()[0]
                )

            self.assertEqual(profile["atlas_relational_mode"], "full_fallback")
            self.assertTrue(profile["atlas_scoped_fallback_audit_findings_restored"])
            self.assertEqual(file_count, 2)
            self.assertEqual(finding_count, 1)
            self.assertEqual(future_finding_count, 1)


class ProofObligationsContractTests(unittest.TestCase):
    def test_quality_gate_does_not_imply_target_native_policy_enforcement(self):
        from tools.engines import quality_gate

        authority = quality_gate._audit_enforcement_authority()

        self.assertEqual(authority["native_enforcement"], "sage_native_audit_taxonomy")
        self.assertEqual(authority["target_native_enforcement"], "not_ingested")
        self.assertEqual(authority["combined_verdict"], "not_available")
        self.assertIn("Target repository", authority["claim_boundary"])

    def test_empty_denominators_do_not_become_full_coverage(self):
        from tools.engines import proof_obligations_engine as proof_engine
        from tools.engines import quality_gate
        from tools.engines import quality_review_engine

        self.assertIsNone(proof_engine._ratio(0, 0))
        self.assertEqual(quality_gate._ratio(0, 0), 0.0)
        self.assertEqual(quality_review_engine._ratio(0, 0), 0.0)

    def test_release_cycle_pressure_excludes_non_release_variants(self):
        from tools.engines import proof_obligations_engine as proof_engine

        atlas = {
            "MAIN": {"files": {"src/App.tsx": {}}},
            "VARIANT": {"files": {f"src/{index}.tsx": {} for index in range(100)}},
        }
        circular = {
            "cycles": [[f"VARIANT::src/{index}.tsx"] for index in range(100)],
            "by_project": {
                "MAIN": {"cycle_count": 0},
                "VARIANT": {"cycle_count": 100},
            },
        }
        with patch.dict(
            "tools.core.projects_registry.DYNAMIC_CONFIG",
            {"project_roles": {"MAIN": "host", "VARIANT": "variant"}},
            clear=False,
        ):
            pressure = proof_engine._release_cycle_pressure(atlas, circular)

        self.assertEqual(pressure["projects"], ["MAIN"])
        self.assertEqual(pressure["cycles"], 0)
        self.assertEqual(pressure["files"], 1)
        self.assertEqual(pressure["workspace_cycles"], 100)
        self.assertTrue(pressure["scope_complete"])
        self.assertEqual(pressure["missing_project_summaries"], [])

    def test_release_cycle_pressure_fails_closed_without_release_project_summary(self):
        from tools.engines import proof_obligations_engine as proof_engine

        with patch.dict(
            "tools.core.projects_registry.DYNAMIC_CONFIG",
            {"project_roles": {"MAIN": "host"}},
            clear=False,
        ):
            pressure = proof_engine._release_cycle_pressure(
                {"MAIN": {"files": {"src/App.tsx": {}}}},
                {"cycles": [], "by_project": {}},
            )

        self.assertFalse(pressure["scope_complete"])
        self.assertEqual(pressure["missing_project_summaries"], ["MAIN"])

    def test_quality_gate_coverage_uses_real_atlas_files_and_bounded_overlap(self):
        from tools.engines import quality_gate

        atlas = {
            "MAIN": {
                "project": {"name": "MAIN"},
                "files": {
                    "src/a.ts": {"state_flow": {"has_zustand_store": True}},
                    "src/b.ts": {},
                },
            }
        }
        genome = {
            "a": [{"project": "MAIN", "file": "src/a.ts"}],
            "outside": [{"project": "OTHER", "file": "src/outside.ts"}],
        }
        symbol_coverage = quality_gate._atlas_symbol_coverage(atlas=atlas, genome=genome)
        self.assertEqual(symbol_coverage["totals"]["files"], 2)
        self.assertEqual(symbol_coverage["totals"]["symbol_rich_files"], 1)
        self.assertEqual(symbol_coverage["atlas_symbol_file_ratio"], 0.5)

        state_flow = {
            "zustand_stores": {
                "MAIN::src/a.ts": ["store"],
                "OTHER::src/outside.ts": ["store"],
            },
            "zustand_consumers": {},
            "tanstack_queries": {},
            "tanstack_mutations": {},
            "boundary_signals": {},
        }
        state_coverage = quality_gate._state_flow_coverage(atlas=atlas, state_flow=state_flow)
        self.assertEqual(state_coverage["state_flow_detection_ratio"], 1.0)
        self.assertEqual(state_coverage["totals"]["detected_atlas_stateful_files"], 1)
        self.assertEqual(state_coverage["totals"]["scanner_only_files"], 1)

    def _run_proof_with_state_flow(self, state_flow):
        from tools.engines import proof_obligations_engine as proof_engine

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw_dir = root / ".raw"
            reports_dir = root / "reports"
            raw_dir.mkdir()
            reports_dir.mkdir()
            for name in (
                "atlas.json",
                "genome.json",
                "health_score.json",
                "dead_code.json",
                "circular_deps.json",
                "react_support_matrix.json",
                "state_flow.json",
            ):
                (raw_dir / name).write_text("{}", encoding="utf-8")

            payloads = {
                "atlas.json": {},
                "genome.json": {},
                "health_score.json": {"overall": 100},
                "dead_code.json": {"summary": {"high": 0}},
                "circular_deps.json": {"cycles": []},
                "react_support_matrix.json": {"summary": {"repo_present": 0, "detected": 0}},
                "state_flow.json": state_flow,
                "project_dna_profile.json": {
                    "summary": {"frameworks": ["react"]},
                    "projects": [],
                },
            }
            saved = {}

            def fake_load(path, default=None):
                return payloads.get(Path(path).name, default if default is not None else {})

            def fake_save(path, payload):
                saved[Path(path).name] = payload

            with patch.object(proof_engine, "RAW_DIR", raw_dir), \
                patch.object(proof_engine, "REPORTS_DIR", reports_dir), \
                patch.object(proof_engine, "load_json_file", side_effect=fake_load), \
                patch.object(proof_engine, "load_atlas_data", return_value=payloads["atlas.json"]), \
                patch.object(proof_engine, "load_genome_data", return_value=payloads["genome.json"]), \
                patch.object(proof_engine, "save_json_atomic", side_effect=fake_save), \
                patch.object(proof_engine, "save_text_atomic"), \
                patch.object(proof_engine, "ensure_valid_payload"):
                proof_engine.run_proof_obligations()

        return saved["proof_obligations.json"]

    def test_explicit_zero_transition_subjects_is_not_promoted_to_stateful_total(self):
        proof_payload = self._run_proof_with_state_flow(
            {
                    "boundary_signals": {
                        "MAIN::src/store/index.test-d.ts": {
                            "technologies": ["zustand"],
                            "transition_markers": [],
                            "property_markers": ["Tech:selector"],
                            "property_markers_strong": ["Tech:selector"],
                        }
                    },
                    "zustand_consumers": {"MAIN::src/store/index.test-d.ts": ["useStatusStore"]},
                    "zustand_stores": {},
                    "tanstack_queries": {},
                    "tanstack_mutations": {},
                    "state_proof": {
                        "stateful_files": 1,
                        "transition_subject_files": 0,
                        "transition_files": 0,
                        "property_files": 1,
                        "property_files_strong": 1,
                        "property_files_strict": 1,
                    },
            }
        )

        po_009 = next(item for item in proof_payload["obligations"] if item["id"] == "PO_009")
        self.assertFalse(po_009["passed"])
        self.assertFalse(po_009["required"])
        self.assertEqual(po_009["status"], "NOT_APPLICABLE")
        self.assertEqual(po_009["evidence"]["transition_subject_files"], 0)
        self.assertIsNone(po_009["evidence"]["transition_ratio"])

    def test_empty_required_structural_scopes_cannot_pass(self):
        proof_payload = self._run_proof_with_state_flow({})

        po_002 = next(item for item in proof_payload["obligations"] if item["id"] == "PO_002")
        po_003 = next(item for item in proof_payload["obligations"] if item["id"] == "PO_003")
        for obligation in (po_002, po_003):
            self.assertTrue(obligation["required"])
            self.assertFalse(obligation["passed"])
            self.assertEqual(obligation["status"], "UNKNOWN")
            self.assertIsNone(obligation["evidence"]["ratio"])
            self.assertEqual(obligation["evidence"]["measurement_status"], "empty_scope")

        self.assertFalse(proof_payload["summary"]["passed"])

    def test_project_transition_envelope_uses_state_flow_project_proof(self):
        proof_payload = self._run_proof_with_state_flow(
            {
                "boundary_signals": {
                    "MAIN::src/state/a.ts": {"transition_markers": ["Tech:setState"]},
                    "MAIN::src/state/b.ts": {"transition_markers": []},
                    "MAIN::src/state/c.ts": {"transition_markers": []},
                },
                "zustand_stores": {
                    "MAIN::src/state/a.ts": ["zustand_store"],
                    "MAIN::src/state/b.ts": ["zustand_store"],
                    "MAIN::src/state/c.ts": ["zustand_store"],
                },
                "tanstack_queries": {},
                "tanstack_mutations": {},
                "state_proof": {
                    "stateful_files": 13,
                    "transition_subject_files": 12,
                    "transition_files": 11,
                    "property_files": 10,
                    "property_files_strong": 10,
                    "property_files_strict": 5,
                },
                "state_proof_by_project": {
                    "MAIN": {
                        "stateful_files": 13,
                        "transition_subject_files": 12,
                        "transition_files": 11,
                        "property_files": 10,
                        "property_files_strong": 10,
                        "property_files_strict": 5,
                        "transition_ratio": 0.917,
                        "property_ratio": 0.769,
                        "property_ratio_strong": 0.769,
                    }
                },
            }
        )

        po_011 = next(item for item in proof_payload["obligations"] if item["id"] == "PO_011")
        self.assertTrue(po_011["passed"])
        self.assertEqual(po_011["evidence"]["by_project"], {"MAIN": 0.917})


class StateFlowScannerContractTests(unittest.TestCase):
    def test_query_key_refs_and_dynamic_parts_survive_state_flow_summary(self):
        summary = summarize_state_flow_features(
            [
                "QueryKey:project",
                "QueryKeyRef:projectId",
                "QueryKeyDynamic:project.full_name",
                "QueryClientAction:prefetchQuery",
            ]
        )

        self.assertEqual(summary["query_keys"], ["project"])
        self.assertEqual(summary["query_key_refs"], ["projectId"])
        self.assertEqual(summary["query_key_dynamic"], ["project.full_name"])
        self.assertEqual(summary["client_actions"], ["prefetchQuery"])

    def test_read_only_query_files_are_not_transition_subjects(self):
        subjects = _transition_subject_file_set(
            stores={},
            queries={
                "MAIN::src/useReadOnlyQuery.ts": ["users"],
                "MAIN::src/useInvalidatingQuery.ts": ["users"],
            },
            mutations={"MAIN::src/useSaveUser.ts": ["saveUser"]},
            boundary_signals={
                "MAIN::src/useReadOnlyQuery.ts": {
                    "technologies": ["tanstack-query"],
                    "client_actions": [],
                },
                "MAIN::src/useInvalidatingQuery.ts": {
                    "technologies": ["tanstack-query"],
                    "client_actions": ["invalidateQueries"],
                },
            },
        )

        self.assertNotIn("MAIN::src/useReadOnlyQuery.ts", subjects)
        self.assertIn("MAIN::src/useInvalidatingQuery.ts", subjects)
        self.assertIn("MAIN::src/useSaveUser.ts", subjects)


class FractalMapperContractTests(unittest.TestCase):
    def test_disappeared_source_file_is_profiled_not_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rel = "src/disappeared.ts"
            result = map_project(
                root,
                "MAIN",
                {},
                prev_data={"files": [{"path": rel, "symbols": {"classes": [], "functions": [], "hooks": [], "interfaces": [], "types": []}}]},
                changed_files=[f"MAIN::{rel}"],
                atlas_files={rel: {"loc": 10}},
            )

        self.assertEqual(result["_profile"]["missing_files"], 1)
        self.assertEqual(result["_profile"]["missing_file_sample"], [rel])


class TargetRootOverrideContractTests(unittest.TestCase):
    def test_missing_runtime_config_uses_central_language_registry_extensions(self):
        self.assertEqual(_resolve_source_extensions({}), language_extensions())
        self.assertEqual(
            _resolve_source_extensions({"source_extensions": [".PY", ".tsx", "invalid"]}),
            {".py", ".tsx"},
        )
        self.assertEqual(_resolve_source_extensions({"source_extensions": []}), set())

    def test_runtime_installation_identity_is_not_derived_from_legacy_directory_names(self):
        for relative_path in (
            "tools/core/config.py",
            "tools/orchestrators/discovery.py",
        ):
            source = (CODE_MAPS_DIR / relative_path).read_text(encoding="utf-8").lower()
            self.assertNotIn('startswith("legacy product")', source)
            self.assertNotIn('{"legacy product", "legacy product suite"}', source)
        distribution_projection = any(
            (CODE_MAPS_DIR / marker).is_file()
            for marker in ("PUBLIC_DISTRIBUTION_MANIFEST.json", "CLEAN_INSTALL_DELIVERY.json")
        )
        for relative_path in (
            "config/codemaps.discovery.json",
            "config/codemaps.overrides.json",
            "config/codemaps.config.json",
        ):
            config_path = CODE_MAPS_DIR / relative_path
            if distribution_projection and not config_path.exists():
                # Projection validators own package-time cleanliness. A
                # supported init run may regenerate this workspace-local truth.
                continue
            payload = json.loads(
                config_path.read_text(encoding="utf-8")
            )
            skip_names = {
                str(name).strip().lower()
                for name in payload.get("skip_dirs", [])
            }
            self.assertFalse(
                any(name.startswith("code maps") for name in skip_names),
                f"{relative_path} retains legacy installation-name identity",
            )

    def test_target_root_override_uses_canonical_single_project_topology_when_only_one_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"CODEMAPS_TARGET_ROOT": tmp}):
                payload = _apply_target_root_override(
                    {
                        "workspace_root": "..",
                        "variations": {"MAIN": "src", "OTHER": "Variations/Other"},
                        "project_roles": {"MAIN": "host", "OTHER": "variant"},
                    }
                )

        self.assertEqual(payload["workspace_root"], str(Path(tmp).resolve()))
        self.assertEqual(payload["variations"], {"MAIN": "."})
        self.assertEqual(payload["project_roles"], {"MAIN": "host"})
        self.assertTrue(payload["_target_root_override"]["enabled"])
        self.assertEqual(
            payload["_target_root_override"]["ontology_contract"],
            "canonical_repository_topology_v1",
        )
        self.assertEqual(
            payload["_target_root_override"]["discovered_topology"],
            "single_project",
        )

    def test_target_root_override_bounds_src_layout_to_project_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "main.ts").write_text("export const value = 1\n", encoding="utf-8")
            unrelated = root / "Variations" / "Legacy"
            unrelated.mkdir(parents=True)
            (unrelated / "legacy.ts").write_text("export const legacy = true\n", encoding="utf-8")
            with patch.dict(os.environ, {"CODEMAPS_TARGET_ROOT": tmp}):
                payload = _apply_target_root_override({})

        self.assertEqual(payload["variations"], {"MAIN": "src"})
        self.assertEqual(
            payload["_target_root_override"]["analysis_scope"]["project_relative_path"],
            "src",
        )

    def test_external_target_scope_projection_rejects_scope_outside_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "target"
            root.mkdir()
            projection = external_target_scope_projection(
                root,
                {"module_root": "../outside"},
            )

        self.assertEqual(projection["status"], "invalid")
        self.assertEqual(
            projection["resolution_basis"],
            "unsafe_inferred_scope_outside_repository",
        )

    def test_external_target_project_path_uses_bounded_project_namespace(self):
        scope = {"status": "resolved", "project_relative_path": "src"}
        self.assertEqual(
            external_target_project_relative_path("src/App.tsx", scope),
            "App.tsx",
        )
        self.assertIsNone(
            external_target_project_relative_path("Variations/Legacy/App.tsx", scope)
        )

    def test_external_target_project_path_preserves_root_project_namespace(self):
        self.assertEqual(
            external_target_project_relative_path(
                "src/App.tsx",
                {"status": "resolved", "project_relative_path": "."},
            ),
            "src/App.tsx",
        )
        self.assertIsNone(
            external_target_project_relative_path(
                "../outside.ts",
                {"status": "resolved", "project_relative_path": "."},
            )
        )
        self.assertIsNone(
            external_target_project_relative_path(
                "src/App.tsx",
                {"status": "invalid", "project_relative_path": "."},
            )
        )
        self.assertIsNone(
            external_target_project_relative_path(
                "",
                {"status": "resolved", "project_relative_path": "."},
            )
        )

    def test_target_root_override_discovers_workspace_projects_from_same_canonical_topology(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "packages" / "web").mkdir(parents=True)
            (root / "package.json").write_text(
                json.dumps({"workspaces": ["packages/*"]}),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"CODEMAPS_TARGET_ROOT": tmp}):
                payload = _apply_target_root_override({})

        self.assertEqual(payload["variations"], {"MAIN": ".", "WEB": "packages/web"})
        self.assertEqual(payload["project_roles"], {"MAIN": "host", "WEB": "companion"})
        self.assertEqual(payload["architecture"]["detected_profile"], "MONOREPO_TURBOREPO")
        self.assertEqual(
            payload["_target_root_override"]["discovered_topology"],
            "multi_project",
        )
        self.assertEqual(
            payload["_target_root_override"]["analysis_projection"],
            "multi_project",
        )
        self.assertFalse(
            payload["_target_root_override"]["comparative_analysis_enabled"]
        )

    def test_single_project_projection_preserves_discovered_multi_project_reality(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "packages" / "web").mkdir(parents=True)
            (root / "package.json").write_text(
                json.dumps({"workspaces": ["packages/*"]}),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {
                    "CODEMAPS_TARGET_ROOT": tmp,
                    "CODEMAPS_TOPOLOGY_MODE": "single_project",
                },
            ):
                payload = _apply_target_root_override({})

        override = payload["_target_root_override"]
        self.assertEqual(payload["variations"], {"MAIN": "."})
        self.assertEqual(override["discovered_topology"], "multi_project")
        self.assertEqual(override["analysis_projection"], "single_project")
        self.assertEqual(
            override["project_candidates"],
            {"MAIN": ".", "WEB": "packages/web"},
        )
        self.assertEqual(override["excluded_projects"], {"WEB": "packages/web"})
        self.assertFalse(override["comparative_analysis_enabled"])

    def test_target_root_override_does_not_inherit_self_audit_rule_switches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tsconfig.json").write_text("{}", encoding="utf-8")
            (root / "lib").mkdir()
            (root / "lib" / "tsconfig.json").write_text(
                json.dumps({"compilerOptions": {"paths": {"@/*": ["./src/*"]}}}),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"CODEMAPS_TARGET_ROOT": tmp}):
                payload = _apply_target_root_override(
                    {
                        "audit": {
                            "rules": {
                                "relative_imports_no_alias": {
                                    "enabled": False,
                                    "source": "sage_self_discovery",
                                }
                            }
                        }
                    }
                )

        self.assertEqual(payload["audit"]["rules"], {})
        self.assertEqual(payload["environment"]["path_aliases"], {})
        self.assertEqual(
            payload["environment"]["scoped_path_aliases"][0]["scope_root"],
            "lib",
        )
        self.assertEqual(payload["_target_root_override"]["observed_path_aliases"], ["@/*"])
        self.assertEqual(
            payload["_target_root_override"]["path_alias_evidence_files"],
            ["lib/tsconfig.json"],
        )
        self.assertEqual(
            payload["_target_root_override"]["audit_rule_source"],
            "canonical_doctrine_plus_external_target_evidence",
        )

    def test_external_target_alias_policy_applies_only_inside_declared_workspace_scope(self):
        from tools.core import audit_rules

        scoped_environment = {
            "path_aliases": {},
            "scoped_path_aliases": [
                {
                    "scope_root": "lib",
                    "path_aliases": {"@/*": ["./src/*"]},
                }
            ],
        }
        target_override = {"enabled": True, "observed_path_aliases": ["@/*"]}
        with (
            patch.object(audit_rules, "ENVIRONMENT", scoped_environment),
            patch.object(audit_rules, "DYNAMIC_CONFIG", {"_target_root_override": target_override}),
        ):
            self.assertTrue(audit_rules.path_alias_applies_to_file("lib/src/Card/index.tsx"))
            self.assertFalse(audit_rules.path_alias_applies_to_file("scripts/migrate.mjs"))

    def test_target_output_slug_is_stable_and_path_derived(self):
        slug_a = _target_output_slug("C:/Users/example/Downloads/react-app")
        slug_b = _target_output_slug("C:/Users/example/Downloads/react-app")
        slug_c = _target_output_slug("C:/tmp/react-app")

        self.assertEqual(slug_a, slug_b)
        self.assertNotEqual(slug_a, slug_c)
        self.assertTrue(slug_a.startswith("react_app_"))

    def test_external_target_preflight_reports_unknown_repo_without_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = build_preflight(tmp)

        self.assertEqual(payload["summary"]["status"], "ATTENTION")
        self.assertFalse(payload["summary"]["react_signal"])
        self.assertTrue(payload["target"]["exists"])

    def test_external_target_preflight_detects_react_package_signal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text(
                json.dumps({"dependencies": {"react": "latest"}}),
                encoding="utf-8",
            )
            payload = build_preflight(root)

        self.assertEqual(payload["summary"]["status"], "PASS")
        self.assertTrue(payload["summary"]["react_signal"])
        self.assertEqual(payload["summary"]["analysis_depth"], "react_typescript_deep")
        self.assertEqual(payload["summary"]["analysis_authority"]["effective_claim_level"], "deep_specialist")

    def test_external_target_preflight_reports_python_as_ast_strong(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "service.py").write_text("def run():\n    return 1\n", encoding="utf-8")
            payload = build_preflight(root)

        self.assertEqual(payload["summary"]["status"], "PASS")
        self.assertFalse(payload["summary"]["react_signal"])
        self.assertEqual(payload["summary"]["language_counts"], {"python": 1})
        self.assertEqual(payload["summary"]["analysis_depth"], "ast_strong_polyglot")
        self.assertEqual(payload["summary"]["analysis_authority"]["effective_claim_level"], "ast_strong")

    def test_external_target_preflight_reduces_non_react_typescript_to_ast_strong(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "library.ts").write_text("export const value = 1\n", encoding="utf-8")
            (root / "tsconfig.dist.json").write_text("{}\n", encoding="utf-8")
            payload = build_preflight(root)

        authority = payload["summary"]["analysis_authority"]
        self.assertEqual(payload["summary"]["analysis_depth"], "ast_strong_polyglot")
        self.assertEqual(authority["ceiling_claim_level"], "deep_specialist")
        self.assertEqual(authority["effective_claim_level"], "ast_strong")
        self.assertTrue(authority["claim_level_is_ceiling_not_entitlement"])
        self.assertEqual(payload["summary"]["config_files"], ["tsconfig.dist.json"])

    def test_external_target_preflight_preserves_structural_language_ceiling(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "main.go").write_text("package main\n\nfunc main() {}\n", encoding="utf-8")
            payload = build_preflight(root)

        authority = payload["summary"]["analysis_authority"]
        self.assertEqual(payload["summary"]["analysis_depth"], "structural_polyglot")
        self.assertEqual(authority["ceiling_claim_level"], "structural")
        self.assertEqual(authority["effective_claim_level"], "structural")

    def test_external_target_preflight_separates_node_dependency_sections(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text(
                json.dumps({
                    "dependencies": {},
                    "devDependencies": {"typescript": "latest"},
                    "peerDependencies": {"react": "latest"},
                    "optionalDependencies": {"native-helper": "latest"},
                }),
                encoding="utf-8",
            )
            payload = build_preflight(root)

        evidence = payload["summary"]["dependency_evidence"]["javascript_node"]
        self.assertEqual(
            evidence["dependency_counts_by_section"],
            {"runtime": 0, "development": 1, "peer": 1, "optional": 1},
        )
        self.assertEqual(evidence["all_declared_dependency_name_count"], 3)
        self.assertNotIn("direct_dependency_count", evidence)

    def test_external_target_preflight_policy_contract_fails_closed(self):
        policy = json.loads(
            (CODE_MAPS_DIR / "config" / "external_target_preflight_policy.json").read_text(encoding="utf-8")
        )
        capabilities = json.loads(
            (CODE_MAPS_DIR / "config" / "polyglot_capabilities.json").read_text(encoding="utf-8")
        )
        self.assertEqual(_preflight_policy_issues(policy, capabilities), [])
        self.assertEqual(
            policy["installation_root_exclusion"],
            {
                "mode": "resolved_runtime_installation_root",
                "directory_name_matching": False,
                "self_target_behavior": "include",
            },
        )
        self.assertNotIn("additional_skip_dirs", policy)

        invalid_policy = dict(policy)
        invalid_policy.pop("node_dependency_sections")
        self.assertIn(
            "invalid_node_dependency_sections",
            _preflight_policy_issues(invalid_policy, capabilities),
        )

        invalid_status_policy = dict(policy)
        invalid_status_policy["status_policy"] = dict(policy["status_policy"])
        invalid_status_policy["status_policy"]["invalid_target"] = "PASS"
        self.assertIn(
            "invalid_status_policy",
            _preflight_policy_issues(invalid_status_policy, capabilities),
        )

        name_based_policy = dict(policy)
        name_based_policy["additional_skip_dirs"] = ["SAGE"]
        self.assertIn(
            "directory_name_based_installation_exclusion_forbidden",
            _preflight_policy_issues(name_based_policy, capabilities),
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "service.py").write_text("def run():\n    return 1\n", encoding="utf-8")
            with patch(
                "tools.external_target_preflight.load_json_object_strict",
                side_effect=[invalid_policy, capabilities],
            ):
                payload = build_preflight(root)

        authority = payload["summary"]["analysis_authority"]
        self.assertEqual(payload["summary"]["status"], "FAIL")
        self.assertEqual(authority["status"], "not_available")
        self.assertEqual(authority["resolution_basis"], "invalid_external_target_preflight_policy")
        self.assertIn("invalid_node_dependency_sections", authority["policy_issues"])

    def test_external_target_preflight_report_exposes_claim_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "library.ts").write_text("export const value = 1\n", encoding="utf-8")
            report = render_report(build_preflight(root))

        self.assertIn("ceiling_claim_level: `deep_specialist`", report)
        self.assertIn("effective_claim_level: `ast_strong`", report)
        self.assertIn("authority_resolution_basis:", report)

    def test_external_target_preflight_preserves_unparsed_python_manifest_truth(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "service.py").write_text("def run():\n    return 1\n", encoding="utf-8")
            (root / "pyproject.toml").write_text(
                '[project]\nname = "sample"\ndependencies = ["flask>=3"]\n',
                encoding="utf-8",
            )
            (root / "uv.lock").write_text("version = 1\n", encoding="utf-8")
            payload = build_preflight(root)

        python_evidence = payload["summary"]["dependency_evidence"]["python"]
        self.assertEqual(payload["summary"]["manifest_files"]["python"], ["pyproject.toml", "uv.lock"])
        self.assertEqual(python_evidence["status"], "not_evaluated")
        self.assertIsNone(python_evidence["parser"])
        self.assertNotIn("dependency_count", payload["summary"])

    def test_external_target_preflight_keeps_absent_manifest_distinct_from_zero_dependencies(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "service.py").write_text("def run():\n    return 1\n", encoding="utf-8")
            payload = build_preflight(root)

        node_evidence = payload["summary"]["dependency_evidence"]["javascript_node"]
        self.assertEqual(node_evidence["status"], "not_present")
        self.assertNotIn("direct_dependency_count", node_evidence)
        self.assertNotIn("python", payload["summary"]["dependency_evidence"])

    def test_external_target_preflight_fails_closed_on_language_registry_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "service.py").write_text("def run():\n    return 1\n", encoding="utf-8")
            with patch(
                "tools.external_target_preflight.language_registry_provenance",
                return_value={"source": "fallback", "identity": "fallback:registry_missing", "path": "missing"},
            ):
                payload = build_preflight(root)

        self.assertEqual(payload["summary"]["status"], "FAIL")
        self.assertEqual(payload["meta"]["language_registry"]["source"], "fallback")

    def test_external_target_preflight_uses_polyglot_manifest_taxonomy(self):
        samples = {
            "java": ("pom.xml", "<project />\n"),
            "go": ("go.mod", "module example.test/sample\n"),
            "csharp": ("Sample.csproj", "<Project />\n"),
        }
        for ecosystem, (filename, content) in samples.items():
            with self.subTest(ecosystem=ecosystem), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / filename).write_text(content, encoding="utf-8")
                payload = build_preflight(root)

            evidence = payload["summary"]["dependency_evidence"][ecosystem]
            self.assertEqual(payload["summary"]["manifest_files"][ecosystem], [filename])
            self.assertEqual(evidence["status"], "not_evaluated")
            self.assertNotIn("direct_dependency_count", evidence)

    def test_external_target_preflight_recursively_inventories_polyglot_manifests_and_configs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nested_files = {
                "services/api/go.mod": "module example.test/api\n",
                "services/worker/pyproject.toml": "[project]\nname = 'worker'\n",
                "services/jvm/pom.xml": "<project />\n",
                "services/jvm/.mvn/jvm.config": "-Xmx1g\n",
                "services/dotnet/Sample.csproj": "<Project />\n",
                "services/dotnet/Sample.slnx": "<Solution />\n",
                "services/dotnet/NuGet.Config": "<configuration />\n",
                "apps/web/tsconfig.json": "{}\n",
            }
            for relative, content in nested_files.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            (root / "main.go").write_text("package main\n", encoding="utf-8")
            payload = build_preflight(root)

        self.assertEqual(payload["summary"]["manifest_files"]["go"], ["services/api/go.mod"])
        self.assertEqual(payload["summary"]["manifest_files"]["python"], ["services/worker/pyproject.toml"])
        self.assertEqual(payload["summary"]["manifest_files"]["java"], ["services/jvm/pom.xml"])
        self.assertEqual(
            payload["summary"]["manifest_files"]["csharp"],
            ["services/dotnet/NuGet.Config", "services/dotnet/Sample.csproj", "services/dotnet/Sample.slnx"],
        )
        self.assertEqual(
            payload["summary"]["config_files"],
            ["apps/web/tsconfig.json", "services/jvm/.mvn/jvm.config"],
        )
        self.assertEqual(payload["summary"]["inventory_file_count"], 9)
        self.assertFalse(payload["summary"]["inventory_truncated"])
        self.assertNotIn("source_file_sample_count", payload["summary"])
        self.assertEqual(payload["summary"]["inventory_evidence"]["traversal_status"], "complete")
        self.assertEqual(
            payload["summary"]["inventory_evidence"]["traversal_scope"],
            "all_non_skipped_files_up_to_limit",
        )
        self.assertEqual(
            payload["summary"]["inventory_evidence"]["config_taxonomy_scope"]["status"],
            "configured_markers_only",
        )
        self.assertEqual(
            payload["summary"]["inventory_evidence"]["absence_semantics"],
            "not_observed_in_configured_taxonomy_not_proven_absent",
        )
        self.assertIn("inventory_evidence", render_report(payload))

    def test_external_target_preflight_does_not_call_nested_node_manifest_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nested = root / "examples" / "web"
            nested.mkdir(parents=True)
            (nested / "package.json").write_text(
                json.dumps({"dependencies": {"react": "latest"}}),
                encoding="utf-8",
            )
            (root / "service.py").write_text("def run():\n    return 1\n", encoding="utf-8")
            payload = build_preflight(root)

        node_evidence = payload["summary"]["dependency_evidence"]["javascript_node"]
        self.assertEqual(payload["summary"]["manifest_files"]["javascript_node"], ["examples/web/package.json"])
        self.assertEqual(node_evidence["status"], "not_evaluated")
        self.assertEqual(node_evidence["reason"], "root_package_json_not_present_nested_node_manifests_not_evaluated")
        self.assertNotIn("direct_dependency_count", node_evidence)

    def test_config_manifest_matcher_supports_exact_and_glob_markers(self):
        self.assertTrue(is_config_or_manifest_file("pyproject.toml"))
        self.assertTrue(is_config_or_manifest_file("Sample.csproj"))
        self.assertTrue(is_config_or_manifest_file("Sample.slnx"))
        self.assertTrue(is_config_or_manifest_file("NuGet.Config"))
        self.assertTrue(is_config_or_manifest_file("TSConfig.json"))
        self.assertTrue(is_config_or_manifest_file("tsconfig.dist.json"))
        self.assertTrue(is_config_or_manifest_file("jvm.config"))
        self.assertFalse(is_config_or_manifest_file("service.py"))

    def test_external_target_preflight_does_not_infer_react_from_python_src_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "src"
            source.mkdir()
            (source / "service.py").write_text("def run():\n    return 1\n", encoding="utf-8")
            payload = build_preflight(root)

        self.assertEqual(payload["summary"]["status"], "PASS")
        self.assertFalse(payload["summary"]["react_signal"])
        self.assertEqual(payload["summary"]["react_source_file_count"], 0)

    def test_external_target_preflight_separates_repository_inventory_from_src_analysis_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "App.tsx").write_text("export const App = () => null\n", encoding="utf-8")
            (root / "package.json").write_text(
                json.dumps({"dependencies": {"react": "latest"}}),
                encoding="utf-8",
            )
            unrelated = root / "Variations" / "Legacy"
            unrelated.mkdir(parents=True)
            (unrelated / "legacy.py").write_text("value = 1\n", encoding="utf-8")
            payload = build_preflight(root)

        summary = payload["summary"]
        self.assertEqual(summary["analysis_scope"]["project_relative_path"], "src")
        self.assertEqual(summary["language_counts"], {"typescript": 1})
        self.assertEqual(summary["repository_language_counts"], {"python": 1, "typescript": 1})
        self.assertEqual(summary["analysis_authority"]["language_claim_level_ceilings"], {"typescript": "deep_specialist"})

    def test_external_target_preflight_invalid_target_has_no_attention_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = build_preflight(Path(tmp) / "missing")

        self.assertEqual(payload["summary"]["status"], "FAIL")
        self.assertEqual(payload["summary"]["attention_reasons"], [])

    def test_external_target_preflight_skip_directories_do_not_hide_same_named_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scripts = root / "scripts"
            scripts.mkdir()
            for name in ("build", "coverage", "output"):
                (scripts / name).write_text("#!/bin/sh\n", encoding="utf-8")
            skipped = root / "build"
            skipped.mkdir()
            (skipped / "ignored.py").write_text("value = 1\n", encoding="utf-8")

            payload = build_preflight(root)

        self.assertEqual(payload["summary"]["inventory_file_count"], 3)
        self.assertEqual(payload["summary"]["language_counts"], {})

    def test_external_target_preflight_ambiguous_skip_directory_is_root_scoped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            root_venv = root / "venv"
            root_venv.mkdir()
            (root_venv / "ignored.py").write_text("value = 1\n", encoding="utf-8")
            stdlib_venv = root / "stdlib" / "venv"
            stdlib_venv.mkdir(parents=True)
            (stdlib_venv / "__init__.pyi").write_text("class EnvBuilder: ...\n", encoding="utf-8")

            payload = build_preflight(root)

        self.assertEqual(payload["summary"]["inventory_file_count"], 1)
        self.assertEqual(payload["summary"]["language_counts"], {"python": 1})
        self.assertEqual(payload["summary"]["analysis_language_counts"], {})
        self.assertIn("venv", payload["summary"]["inventory_evidence"]["root_only_skipped_directory_names"])

    def test_external_target_preflight_separates_observed_files_from_atlas_analysis_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text("{}", encoding="utf-8")
            (root / "main.ts").write_text("export const value = 1\n", encoding="utf-8")
            (root / "empty.ts").write_text("", encoding="utf-8")
            (root / "globals.d.ts").write_text("declare const version: string\n", encoding="utf-8")
            generated = root / "tooling" / "dist"
            generated.mkdir(parents=True)
            (generated / "bundle.js").write_text("module.exports = 1\n", encoding="utf-8")

            payload = build_preflight(root)

        summary = payload["summary"]
        scope = summary["analysis_scope"]["scope_authority"]
        self.assertEqual(summary["language_counts"], {"javascript": 1, "typescript": 3})
        self.assertEqual(summary["analysis_language_counts"], {"typescript": 2})
        self.assertEqual(scope["effective_observed_source_file_count"], 4)
        self.assertEqual(scope["effective_supported_source_file_count"], 2)
        self.assertEqual(scope["evidence_status"], "COMPLETE_REPOSITORY")

    def test_external_target_preflight_observes_rust_without_granting_semantic_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "src"
            source.mkdir()
            (source / "main.rs").write_text("fn main() {}\n", encoding="utf-8")
            (root / "Cargo.toml").write_text('[package]\nname = "sample"\n', encoding="utf-8")

            payload = build_preflight(root)

        summary = payload["summary"]
        authority = summary["analysis_authority"]
        self.assertEqual(summary["status"], "ATTENTION")
        self.assertEqual(summary["attention_reasons"], ["unsupported_language_families_observed"])
        self.assertEqual(summary["language_counts"], {"rust": 1})
        self.assertEqual(summary["manifest_files"], {"rust": ["Cargo.toml"]})
        self.assertEqual(authority["effective_claim_level"], "not_available")
        self.assertEqual(authority["unsupported_language_families"], ["rust"])
        self.assertEqual(
            summary["inventory_evidence"]["language_taxonomy_scope"]["observation_only_families"],
            ["rust", "svelte", "vue"],
        )
        self.assertEqual(summary["dependency_evidence"]["rust"]["status"], "not_evaluated")

    def test_external_target_preflight_preserves_immutable_runs_and_latest_projection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "target"
            output_root = Path(tmp) / "outputs"
            root.mkdir()
            (root / "main.rs").write_text("fn main() {}\n", encoding="utf-8")

            with patch("tools.external_target_preflight.EXTERNAL_TARGETS_DIR", output_root):
                first = write_preflight(root)
                (root / "lib.rs").write_text("pub fn value() {}\n", encoding="utf-8")
                second = write_preflight(root)

            run_dir = Path(second["target"]["output_dir"]) / ".raw" / "runs"
            run_files = sorted(run_dir.glob("preflight-*.json"))
            latest = json.loads(
                (Path(second["target"]["output_dir"]) / ".raw" / "external_target_preflight.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertNotEqual(first["meta"]["run_id"], second["meta"]["run_id"])
        self.assertEqual(len(run_files), 2)
        self.assertEqual(latest["meta"]["run_id"], second["meta"]["run_id"])
        self.assertEqual(latest["summary"]["language_counts"], {"rust": 2})

    def test_external_target_preflight_does_not_label_unowned_fixture_tsx_as_react(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = root / "tests" / "fixtures"
            fixture.mkdir(parents=True)
            (fixture / "Example.tsx").write_text("export const Example = () => null\n", encoding="utf-8")
            (root / "service.py").write_text("def run():\n    return 1\n", encoding="utf-8")
            payload = build_preflight(root)

        self.assertEqual(payload["summary"]["status"], "PASS")
        self.assertFalse(payload["summary"]["react_signal"])
        self.assertEqual(payload["summary"]["react_source_file_count"], 0)
        self.assertEqual(payload["summary"]["react_fixture_source_file_count"], 0)
        self.assertEqual(payload["summary"]["language_counts"], {"python": 1, "typescript": 1})
        self.assertEqual(payload["summary"]["analysis_depth"], "ast_strong_polyglot")

    def test_external_target_preflight_detects_workspace_react_signal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = root / "apps" / "web"
            app.mkdir(parents=True)
            (root / "package.json").write_text(
                json.dumps({"workspaces": ["apps/*"]}),
                encoding="utf-8",
            )
            (app / "package.json").write_text(
                json.dumps({"dependencies": {"react": "latest"}}),
                encoding="utf-8",
            )
            payload = build_preflight(root)

        self.assertEqual(payload["summary"]["status"], "PASS")
        self.assertTrue(payload["summary"]["react_signal"])
        node_evidence = payload["summary"]["dependency_evidence"]["javascript_node"]
        self.assertEqual(node_evidence["workspace_manifest_count"], 1)

    def test_external_target_preflight_uses_canonical_multi_project_topology(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = root / "packages" / "web"
            embedded = root / "Embedded Tool"
            app.mkdir(parents=True)
            embedded.mkdir()
            (root / "package.json").write_text(
                json.dumps({"workspaces": ["packages/*"]}),
                encoding="utf-8",
            )
            (root / "root.ts").write_text("export const rootValue = 1\n", encoding="utf-8")
            (app / "package.json").write_text(
                json.dumps({"dependencies": {"react": "latest"}}),
                encoding="utf-8",
            )
            (app / "App.tsx").write_text("export const App = () => null\n", encoding="utf-8")
            (embedded / "package.json").write_text("{}", encoding="utf-8")
            (embedded / "private.py").write_text("print('not selected')\n", encoding="utf-8")

            payload = build_preflight(root)

        scope = payload["summary"]["analysis_scope"]
        self.assertEqual(scope["source_mode"], "external_target")
        self.assertEqual(scope["ontology_contract"], "canonical_repository_topology_v1")
        self.assertEqual(scope["discovered_topology"], "multi_project")
        self.assertEqual(scope["analysis_projection"], "multi_project")
        self.assertEqual(scope["selection_mode"], "evidence_backed_auto")
        self.assertEqual(
            scope["selected_projects"],
            {"MAIN": ".", "EMBEDDED_TOOL": "Embedded Tool", "WEB": "packages/web"},
        )
        self.assertEqual(
            scope["project_candidate_roles"],
            {"MAIN": "host", "WEB": "companion", "EMBEDDED_TOOL": "unresolved"},
        )
        self.assertEqual(scope["excluded_projects"], {})
        self.assertEqual(scope["coverage_only_projects"], {"EMBEDDED_TOOL": "Embedded Tool"})
        self.assertEqual(
            scope["relationship_operation_projects"],
            {"MAIN": ".", "WEB": "packages/web"},
        )
        self.assertEqual(
            scope["project_ownership_exclusions"],
            {"MAIN": ["Embedded Tool", "packages/web"], "EMBEDDED_TOOL": [], "WEB": []},
        )
        self.assertEqual(
            scope["file_ownership_contract"],
            "nearest_discovered_project_root_v1",
        )
        self.assertEqual(
            scope["project_file_counts"],
            {"MAIN": 2, "EMBEDDED_TOOL": 2, "WEB": 2},
        )
        self.assertEqual(payload["summary"]["language_counts"]["python"], 1)
        self.assertEqual(payload["summary"]["repository_language_counts"]["python"], 1)
        self.assertFalse(scope["comparative_analysis_enabled"])

    def test_external_target_preflight_separates_topology_selection_from_runtime_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = root / "packages" / "web"
            app.mkdir(parents=True)
            (root / "package.json").write_text(
                json.dumps({"workspaces": ["packages/*"]}),
                encoding="utf-8",
            )
            (root / "root.ts").write_text("export const rootValue = 1\n", encoding="utf-8")
            (app / "package.json").write_text(
                json.dumps({"dependencies": {"react": "latest"}}),
                encoding="utf-8",
            )
            (app / "App.tsx").write_text("export const App = () => null\n", encoding="utf-8")

            payload = build_preflight(root, projects="MAIN")

        scope = payload["summary"]["analysis_scope"]
        self.assertEqual(scope["selected_projects"], {"MAIN": ".", "WEB": "packages/web"})
        self.assertEqual(scope["auto_selected_projects"], scope["selected_projects"])
        self.assertEqual(scope["requested_project_filter"], ["MAIN"])
        self.assertEqual(scope["effective_runtime_projects"], {"MAIN": "."})
        self.assertEqual(scope["unavailable_requested_projects"], [])
        self.assertEqual(
            scope["scope_field_semantics"],
            {
                "selected_projects": "topology_auto_selection_before_runtime_filter",
                "effective_runtime_projects": "project_set_authorized_for_this_requested_execution",
                "language_and_framework_inventory": "effective_runtime_projects",
            },
        )
        self.assertEqual(scope["inventory_project_scope"], "effective_runtime_projects")
        self.assertEqual(scope["runtime_claim_project_scope"], "effective_runtime_projects")
        self.assertEqual(scope["scope_authority"]["evidence_status"], "BOUNDED_PROJECT_SELECTION")
        self.assertEqual(scope["project_file_counts"], {"MAIN": 2})

    def test_external_target_preflight_exposes_unavailable_runtime_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text("{}", encoding="utf-8")
            (root / "root.ts").write_text("export const rootValue = 1\n", encoding="utf-8")

            payload = build_preflight(root, projects="UNKNOWN")

        summary = payload["summary"]
        scope = summary["analysis_scope"]
        self.assertEqual(scope["requested_project_filter"], ["UNKNOWN"])
        self.assertEqual(scope["effective_runtime_projects"], {})
        self.assertEqual(scope["unavailable_requested_projects"], ["UNKNOWN"])
        self.assertEqual(summary["status"], "FAIL")
        self.assertIn("requested_project_filter_not_in_topology", summary["attention_reasons"])

    def test_external_target_preflight_scopes_react_and_exposes_unsupported_framework_languages(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text(
                json.dumps({"workspaces": ["packages/*"]}),
                encoding="utf-8",
            )
            packages = {
                "react": ({"dependencies": {"react": "latest"}}, "Component.tsx"),
                "solid": ({"dependencies": {"solid-js": "latest"}}, "Component.tsx"),
                "vue": ({"dependencies": {"vue": "latest"}}, "Component.vue"),
                "svelte": ({"dependencies": {"svelte": "latest"}}, "Component.svelte"),
            }
            for package_name, (manifest, source_name) in packages.items():
                package_root = root / "packages" / package_name
                package_root.mkdir(parents=True)
                (package_root / "package.json").write_text(json.dumps(manifest), encoding="utf-8")
                (package_root / source_name).write_text("export const value = 1\n", encoding="utf-8")

            payload = build_preflight(root)

        summary = payload["summary"]
        authority = summary["analysis_authority"]
        self.assertEqual(summary["status"], "ATTENTION")
        self.assertEqual(
            summary["attention_reasons"],
            ["unsupported_language_families_observed", "unsupported_framework_families_observed"],
        )
        self.assertTrue(summary["react_signal"])
        self.assertEqual(summary["react_source_file_count"], 1)
        self.assertEqual(
            summary["language_counts"],
            {"svelte": 1, "typescript": 2, "vue": 1},
        )
        self.assertEqual(authority["effective_claim_scope"], "react_typescript_static_only")
        self.assertEqual(authority["unsupported_language_families"], ["svelte", "vue"])
        self.assertEqual(authority["unsupported_framework_families"], ["solid", "svelte", "vue"])
        self.assertEqual(authority["framework_authority"]["react"]["effective_claim_level"], "deep_specialist")
        self.assertEqual(authority["framework_authority"]["solid"]["effective_claim_level"], "not_available")
        self.assertIn(".git", summary["inventory_evidence"]["skipped_directory_names"])

    def test_external_target_preflight_uses_root_package_identity_as_framework_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text(
                json.dumps({"name": "fastify", "dependencies": {"pino": "latest"}}),
                encoding="utf-8",
            )
            (root / "fastify.js").write_text("module.exports = function fastify () {}\n", encoding="utf-8")

            payload = build_preflight(root)

        summary = payload["summary"]
        authority = summary["analysis_authority"]
        self.assertEqual(summary["status"], "ATTENTION")
        self.assertEqual(summary["attention_reasons"], ["unsupported_framework_families_observed"])
        self.assertEqual(authority["unsupported_framework_families"], ["fastify"])
        self.assertEqual(
            authority["framework_authority"]["fastify"]["matched_package_sources"],
            {"fastify": ["package.json#name"]},
        )

    def test_external_target_preflight_counts_python_typing_stubs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("[project]\nname = 'stubbed'\n", encoding="utf-8")
            (root / "model.py").write_text("class Model: pass\n", encoding="utf-8")
            (root / "model.pyi").write_text("class Model: ...\n", encoding="utf-8")

            payload = build_preflight(root)

        self.assertEqual(payload["summary"]["language_counts"], {"python": 2})
        self.assertEqual(payload["summary"]["analysis_authority"]["effective_claim_level"], "ast_strong")

    def test_external_target_index_handles_empty_registry(self):
        with patch("tools.generate_external_target_index.EXTERNAL_TARGETS_DIR", Path("__missing_external_targets__")):
            payload = build_index()

        self.assertEqual(payload["summary"]["targets"], 0)

    def test_inspect_helpers_match_folder_and_build_safe_names(self):
        row = {"file": "src/lifecycle-modules/03-writing/pages/WritingWorkspace.tsx"}

        self.assertTrue(_under_folder(row, "src/lifecycle-modules/03-writing"))
        self.assertFalse(_under_folder(row, "src/lifecycle-modules/04-translation"))
        self.assertTrue(_safe_name("file", "src/Foo Bar.tsx").startswith("file_src_foo_bar_tsx"))


class ReactProbeContractTests(unittest.TestCase):
    def test_load_discovery_reads_config_dir_not_codemaps_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "config"
            config_dir.mkdir()
            (config_dir / "codemaps.discovery.json").write_text(
                json.dumps({"workspace_root": "from-config"}),
                encoding="utf-8",
            )

            with patch.object(_react_probe, "CONFIG_DIR", config_dir):
                discovery = _react_probe._load_discovery()

        self.assertEqual(discovery["workspace_root"], "from-config")

    def test_jsx_pre_scan_and_tsc_output_dedupe_to_one_logical_finding(self):
        root = Path(__file__).resolve().parent / "fixtures"
        oracle = ValidationOracle(str(root))
        pre_scan = oracle._scan_for_suspicious_syntax(str(root))
        tsc = oracle._parse_tsc_output(
            "src/Broken.tsx(2,31): error TS1003: Identifier expected.\n"
            "src/Broken.tsx(2,31): error TS1382: Unexpected token. Did you mean `{'>'}` or `&gt;`?",
            str(root),
            "FIXTURE",
        )
        deduped = oracle._dedupe_errors(pre_scan + tsc)

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["classification"], "malformed_jsx_token")
        self.assertEqual(deduped[0]["code"], "PRE_JSX,TS1003,TS1382")

    def test_declared_missing_module_is_classified_as_sanctuary_context(self):
        root = Path(__file__).resolve().parent / "fixtures"
        oracle = ValidationOracle(str(root))
        with patch.object(oracle, "_declared_dependencies_for_project", return_value={"react-hook-form"}):
            errors = oracle._parse_tsc_output(
                "src/Form.tsx(1,22): error TS2307: Cannot find module 'react-hook-form' or its corresponding type declarations.",
                str(root),
                "FIXTURE",
            )

        self.assertEqual(errors[0]["classification"], "missing_declared_dependency_in_sanctuary")
        self.assertEqual(errors[0]["dependency_context"]["module"], "react-hook-form")
        self.assertTrue(errors[0]["dependency_context"]["declared_in_package_json"])

    def test_tsc_classifier_separates_environment_and_type_contract_errors(self):
        oracle = ValidationOracle(".")

        self.assertEqual(
            oracle._classify_tsc_issue("TS2688", "Cannot find type definition file for 'node'.", "")[1],
            "environment_type_error",
        )
        self.assertEqual(
            oracle._classify_tsc_issue("TS2339", "Property 'foo' does not exist on type 'Bar'.", "")[1],
            "type_contract_error",
        )
        self.assertEqual(
            oracle._classify_tsc_issue("TS2664", "Invalid module name in augmentation.", "")[1],
            "module_augmentation_error",
        )


class LiveSurfaceContractTests(unittest.TestCase):
    def test_new_oracle_classes_are_high_confidence_inputs(self):
        self.assertIn("deep_tsc_parser_error", HIGH_CONFIDENCE_ORACLE_CLASSES)
        self.assertIn("relocated_alias_candidate", HIGH_CONFIDENCE_ORACLE_CLASSES)
        self.assertIn("malformed_jsx_token", HIGH_CONFIDENCE_ORACLE_CLASSES)
        self.assertIn("missing_declared_dependency_in_sanctuary", HIGH_CONFIDENCE_ORACLE_CLASSES)
        self.assertIn("missing_package_declaration", HIGH_CONFIDENCE_ORACLE_CLASSES)
        self.assertIn("environment_type_error", HIGH_CONFIDENCE_ORACLE_CLASSES)
        self.assertIn("type_contract_error", HIGH_CONFIDENCE_ORACLE_CLASSES)


class QualityGateContractTests(unittest.TestCase):
    def test_optional_lte_check_uses_policy_default_when_missing(self):
        check = _optional_lte_check({}, "max_high_risk_candidates", 12, 20)

        self.assertEqual(check["expected"], 20)
        self.assertTrue(check["passed"])
        self.assertEqual(check["details"], ["policy_auto_default"])


class SelfHealingContractTests(unittest.TestCase):
    def test_relative_import_rewrite_does_not_touch_jsx_attributes(self):
        healer = CodeHealer(project_name="TEST", workspace_root=".")
        content = (
            "import Foo from './Foo';\n"
            "import './side-effect';\n"
            "const Lazy = import('../Lazy');\n\n"
            "export function Toolbar() {\n"
            "  return <BookOpen className=\"w-4 h-4 mr-2\" />;\n"
            "}\n"
        )

        healed = healer.heal_content(
            content,
            [],
            target_path="src/components/Toolbar.tsx",
            original_path="src/components/Toolbar.tsx",
        )

        self.assertIn('className="w-4 h-4 mr-2" />', healed)
        self.assertNotIn("@/>", healed)
        self.assertIn("from '@/components/Foo'", healed)
        self.assertIn("import '@/components/side-effect'", healed)
        self.assertIn("import('@/Lazy')", healed)

    def test_generated_heal_scripts_are_blocked_by_default(self):
        ps_guard = heal_ps_guard("auto_heal.ps1")
        bash_guard = heal_bash_guard("auto_heal.sh")

        self.assertTrue(any("CODEMAPS_ALLOW_MUTATION_SCRIPTS" in line for line in ps_guard))
        self.assertTrue(any("exit 64" in line for line in ps_guard))
        self.assertTrue(any("CODEMAPS_ALLOW_MUTATION_SCRIPTS" in line for line in bash_guard))
        self.assertTrue(any("exit 64" in line for line in bash_guard))

    def test_generated_heal_scripts_require_manifest(self):
        ps_guard = heal_ps_manifest_guard("auto_heal_manifest.json")
        bash_guard = heal_bash_manifest_guard("auto_heal_manifest.json")

        self.assertTrue(any("auto_heal_manifest.json" in line for line in ps_guard))
        self.assertTrue(any("exit 65" in line for line in ps_guard))
        self.assertTrue(any("sha256" in line or "Get-FileHash" in line for line in ps_guard))
        self.assertTrue(any("auto_heal_manifest.json" in line for line in bash_guard))
        self.assertTrue(any("exit 65" in line for line in bash_guard))
        self.assertTrue(any("sha256sum" in line for line in bash_guard))

    def test_generated_heal_scripts_filter_low_trust_operations(self):
        policy = _auto_heal_script_policy()
        base_violation = {
            "project": "MAIN",
            "project_key": "MAIN",
            "file": "src/App.tsx",
            "target_ref": "MAIN::src/App.tsx",
            "rule": "relative_imports_no_alias",
            "detail": "src/App.tsx imports ./ui/Button",
        }

        allowed, reason = _auto_heal_generation_decision(base_violation, policy, {}, 0)
        self.assertFalse(allowed)
        self.assertEqual(reason, "missing_severity")

        trusted = dict(base_violation, severity="LOW")
        allowed, reason = _auto_heal_generation_decision(trusted, policy, {}, 0)
        self.assertTrue(allowed)
        self.assertEqual(reason, "script_allowed")

        variation = dict(trusted, project="VARIATION", project_key="VARIATION", target_ref="VARIATION::src/App.tsx")
        allowed, reason = _auto_heal_generation_decision(variation, policy, {}, 0)
        self.assertFalse(allowed)
        self.assertEqual(reason, "project_not_allowed")

        missing_ref = dict(trusted)
        missing_ref.pop("target_ref")
        allowed, reason = _auto_heal_generation_decision(missing_ref, policy, {}, 0)
        self.assertFalse(allowed)
        self.assertEqual(reason, "missing_target_ref")


class GeneratedMutationScriptContractTests(unittest.TestCase):
    def test_generated_merge_scripts_are_blocked_by_default(self):
        ps_guard = merge_ps_guard("auto_merge.ps1")
        bash_guard = merge_bash_guard("auto_merge.sh")

        self.assertTrue(any("CODEMAPS_ALLOW_MUTATION_SCRIPTS" in line for line in ps_guard))
        self.assertTrue(any("exit 64" in line for line in ps_guard))
        self.assertTrue(any("CODEMAPS_ALLOW_MUTATION_SCRIPTS" in line for line in bash_guard))
        self.assertTrue(any("exit 64" in line for line in bash_guard))

    def test_generated_merge_scripts_require_manifest(self):
        ps_guard = merge_ps_manifest_guard("auto_merge_manifest.json")
        bash_guard = merge_bash_manifest_guard("auto_merge_manifest.json")

        self.assertTrue(any("auto_merge_manifest.json" in line for line in ps_guard))
        self.assertTrue(any("exit 65" in line for line in ps_guard))
        self.assertTrue(any("sha256" in line or "Get-FileHash" in line for line in ps_guard))
        self.assertTrue(any("auto_merge_manifest.json" in line for line in bash_guard))
        self.assertTrue(any("exit 65" in line for line in bash_guard))
        self.assertTrue(any("sha256sum" in line for line in bash_guard))


class SystemPurgeContractTests(unittest.TestCase):
    def test_rmtree_force_removes_readonly_nested_output(self):
        tmp_root = Path("scratch") / f"codemaps-purge-test-{uuid.uuid4().hex}"
        root = tmp_root / "output"
        try:
            nested = root / "nested"
            nested.mkdir(parents=True)
            target = nested / "artifact.json"
            target.write_text("{}", encoding="utf-8")
            target.chmod(0o444)

            removed, remaining = _rmtree_force(root)

            self.assertTrue(removed, remaining)
            self.assertFalse(root.exists())
        finally:
            if tmp_root.exists():
                _rmtree_force(tmp_root)

    def test_test_tmp_residue_is_classified_as_nonblocking_only(self):
        self.assertTrue(
            _is_nonblocking_test_tmp_residue(
                Path("output"),
                [".test_tmp", ".test_tmp\\stale-dir"],
            )
        )
        self.assertFalse(
            _is_nonblocking_test_tmp_residue(
                Path("output"),
                [".raw\\atlas.json"],
            )
        )


class OrchestratorContractTests(unittest.TestCase):
    def test_external_target_scope_excludes_sage_product_governance(self):
        catalog = [
            {"name": "Atlas", "depends_on": []},
            {"name": "Release Readiness", "depends_on": ["Atlas"]},
        ]
        policy = {
            "step_system_scope_policy": {
                "unclassified_behavior": "fail_closed",
                "groups": {
                    "shared": {
                        "system_scopes": ["SAGE_ON_SAGE", "SAGE_ON_REPOSITORY"],
                        "step_slugs": ["atlas"],
                    },
                    "internal": {
                        "system_scopes": ["SAGE_ON_SAGE"],
                        "step_slugs": ["releasereadiness"],
                    },
                },
            }
        }

        selected, report = filter_catalog_for_system_scope(
            catalog,
            "SAGE_ON_REPOSITORY",
            policy,
        )

        self.assertEqual([step["name"] for step in selected], ["Atlas"])
        self.assertEqual([row["name"] for row in report["excluded_steps"]], ["Release Readiness"])

    def test_external_target_scope_fails_closed_for_unclassified_step(self):
        catalog = [{"name": "New Unclassified Step", "depends_on": []}]
        selected, report = filter_catalog_for_system_scope(
            catalog,
            "SAGE_ON_REPOSITORY",
            {"step_system_scope_policy": {"unclassified_behavior": "fail_closed", "groups": {}}},
        )

        self.assertEqual(selected, [])
        self.assertEqual(report["excluded_steps"][0]["classification"], "unclassified")

    def test_scope_filter_propagates_excluded_dependency_chain(self):
        catalog = [
            {"name": "Internal Root", "depends_on": []},
            {"name": "Target Consumer", "depends_on": ["Internal Root"]},
            {"name": "Target Finalizer", "depends_on": ["Target Consumer"]},
        ]
        policy = {
            "step_system_scope_policy": {
                "unclassified_behavior": "fail_closed",
                "groups": {
                    "internal": {
                        "system_scopes": ["SAGE_ON_SAGE"],
                        "step_slugs": ["internalroot"],
                    },
                    "shared": {
                        "system_scopes": ["SAGE_ON_SAGE", "SAGE_ON_REPOSITORY"],
                        "step_slugs": ["targetconsumer", "targetfinalizer"],
                    },
                },
            }
        }

        selected, report = filter_catalog_for_system_scope(catalog, "SAGE_ON_REPOSITORY", policy)

        self.assertEqual(selected, [])
        self.assertEqual(
            [row["name"] for row in report["invalid_dependencies"]],
            ["Target Consumer", "Target Finalizer"],
        )

    def test_missing_mode_artifacts_bypass_absolute_zero(self):
        class Args:
            step = None
            from_step = None
            force = False
            smart_trigger = True
            profile = "full"
            scope = None

        catalog = [
            {"name": "Atlas", "depends_on": [], "category": "core"},
            {"name": "Dead Code", "depends_on": ["Atlas"], "category": "core"},
        ]

        with patch("tools.engines.capability_activation_planner.build_capability_activation_plan", return_value={"summary": {"status": "PASS"}, "projects": []}):
            selected = select_steps_smart(
                catalog,
                Args(),
                changed_files=[],
                dna_changed_files=[],
                required_artifacts_missing=["dead_code"],
            )

        self.assertEqual([step["name"] for step in selected], ["Atlas", "Dead Code"])

    def test_from_step_selects_catalog_tail(self):
        class Args:
            step = None
            from_step = "Beta"
            force = False
            smart_trigger = False

        catalog = [
            {"name": "Alpha", "depends_on": []},
            {"name": "Beta", "depends_on": ["Alpha"]},
            {"name": "Gamma", "depends_on": ["Beta"]},
        ]

        selected = select_steps_smart(catalog, Args(), changed_files=None, dna_changed_files=None)

        self.assertEqual([step["name"] for step in selected], ["Beta", "Gamma"])

    def test_capability_activation_filters_irrelevant_framework_steps(self):
        class Args:
            step = None
            from_step = None
            force = False
            profile = "full"

        catalog = [
            {"name": "Atlas", "depends_on": [], "category": "core"},
            {"name": "Framework Route Analyzer", "depends_on": ["Atlas"], "category": "derived"},
        ]
        plan = {
            "summary": {"status": "PASS"},
            "projects": [
                {
                    "enabled_capabilities": [
                        {"id": "atlas_sequencing", "status": "enabled"},
                    ]
                }
            ]
        }

        with patch("tools.engines.capability_activation_planner.build_capability_activation_plan", return_value=plan) as build_plan:
            selected = apply_capability_activation(catalog, catalog, Args())

        self.assertEqual([step["name"] for step in selected], ["Atlas"])
        build_plan.assert_called_once_with(refresh_dna=True)

    def test_capability_activation_project_dna_refresh_persists_raw_and_report_pair(self):
        payload = {"meta": {"kind": "project_dna_profile"}, "projects": []}
        with (
            patch(
                "tools.engines.capability_activation_planner.build_project_dna_profile",
                return_value=payload,
            ),
            patch(
                "tools.engines.capability_activation_planner.render_project_dna_report",
                return_value="# Project DNA Profile\n",
            ) as render_report,
            patch(
                "tools.engines.capability_activation_planner.save_json_atomic"
            ) as save_json,
            patch(
                "tools.engines.capability_activation_planner.save_text_atomic"
            ) as save_text,
        ):
            result = _refresh_project_dna_profile()

        self.assertIs(result, payload)
        save_json.assert_called_once()
        render_report.assert_called_once_with(payload)
        save_text.assert_called_once()
        self.assertEqual(save_json.call_args.args[0].name, "project_dna_profile.json")
        self.assertEqual(save_text.call_args.args[0].name, "project_dna_profile.md")

    def test_release_deep_bypasses_capability_filter(self):
        class Args:
            step = None
            from_step = None
            force = False
            profile = "release-deep"

        catalog = [{"name": "Framework Route Analyzer", "depends_on": [], "category": "derived"}]

        self.assertEqual(apply_capability_activation(catalog, catalog, Args()), catalog)

    def test_empty_capability_plan_fails_open(self):
        class Args:
            step = None
            from_step = None
            force = False
            profile = "full"

        catalog = [{"name": "Framework Route Analyzer", "depends_on": [], "category": "derived"}]
        with patch(
            "tools.engines.capability_activation_planner.build_capability_activation_plan",
            return_value={"summary": {"status": "PASS"}, "projects": []},
        ):
            self.assertEqual(apply_capability_activation(catalog, catalog, Args()), catalog)

    def test_dependency_manifest_change_bypasses_stale_capability_filter(self):
        class Args:
            step = None
            from_step = None
            force = False
            profile = "full"

        catalog = [{"name": "Framework Route Analyzer", "depends_on": [], "category": "derived"}]
        selected = apply_capability_activation(
            catalog,
            catalog,
            Args(),
            changed_files=["MAIN::packages/web/package.json"],
        )
        self.assertEqual(selected, catalog)

    def test_project_dna_aggregates_declared_workspace_dependencies(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "packages" / "web").mkdir(parents=True)
            (root / "package.json").write_text(
                json.dumps({"workspaces": {"packages": ["packages/*"]}}),
                encoding="utf-8",
            )
            (root / "packages" / "web" / "package.json").write_text(
                json.dumps({"dependencies": {"react": "^19.0.0", "vite": "^7.0.0"}}),
                encoding="utf-8",
            )

            dependencies = _dependency_names([root])

        self.assertIn("react", dependencies)
        self.assertIn("vite", dependencies)


class ProjectRegistryContractTests(unittest.TestCase):
    def test_load_overrides_uses_configured_overrides_path(self):
        import tools.core.projects_registry as registry

        tmp_root = Path("scratch") / f"codemaps-overrides-test-{uuid.uuid4().hex}"
        try:
            tmp_root.mkdir(parents=True)
            overrides_path = tmp_root / "codemaps.overrides.json"
            overrides_path.write_text(
                json.dumps({"variation_aliases": {"VARIANT_APP": {"path": "../variant-app"}}}),
                encoding="utf-8",
            )

            with patch.object(registry, "OVERRIDES_PATH", overrides_path):
                overrides = registry._load_overrides()

            self.assertIn("VARIANT_APP", overrides.get("variation_aliases", {}))
        finally:
            if tmp_root.exists():
                _rmtree_force(tmp_root)


class MergeScriptPathContractTests(unittest.TestCase):
    def test_safe_relative_path_rejects_workspace_escape_shapes(self):
        self.assertEqual(
            _safe_relative_path("src/components/Card.tsx"),
            str(Path("src/components/Card.tsx")),
        )
        self.assertIsNone(_safe_relative_path("../outside.ts"))
        self.assertIsNone(_safe_relative_path("/tmp/outside.ts"))
        self.assertIsNone(_safe_relative_path("C:\\tmp\\outside.ts"))

    def test_safe_join_keeps_candidates_under_root(self):
        tmp_root = Path("scratch") / f"codemaps-safe-join-test-{uuid.uuid4().hex}"
        try:
            tmp_root.mkdir(parents=True)

            safe = _safe_join(tmp_root, "src/components/Card.tsx")
            unsafe = _safe_join(tmp_root, "../outside.ts")

            self.assertIsNotNone(safe)
            self.assertEqual(safe.relative_to(tmp_root.resolve()), Path("src/components/Card.tsx"))
            self.assertIsNone(unsafe)
        finally:
            if tmp_root.exists():
                _rmtree_force(tmp_root)


class UIRuntimeContractTests(unittest.TestCase):
    def test_ui_contract_detects_hidden_runtime_dependencies(self):
        contract = analyze_ui_runtime_contract(
            """
            import './panel.css';
            import { useProjectStore } from '@/platform/core/stores/projectStore';
            export function Page() {
              const { id } = useParams();
              const ctx = useProjectContext();
              const project = useProjectStore(s => s.project);
              const { t } = useTranslation('publish');
              window.localStorage.setItem('lastProject', id || '');
              return <ProjectProvider><section className="panel shell">{t('missingKey')}{t('otherMissingKey')}</section></ProjectProvider>;
            }
            """,
            known_locale_keys=set(),
        )

        self.assertIn("ProjectProvider", contract["provider_tags"])
        self.assertIn("useProjectContext", contract["context_hooks"])
        self.assertIn("useProjectStore", contract["store_hooks"])
        self.assertIn("useParams", contract["router_hooks"])
        self.assertIn("missingKey", contract["missing_i18n_keys"])
        self.assertEqual(contract["risk_tier"], "high")

    def test_ui_contract_extracts_class_tokens_from_configured_composition_helpers(self):
        contract = analyze_ui_runtime_contract(
            """
            import { clsx } from 'clsx';
            import { cn } from '@/lib/cn';
            export function Toolbar({ active }) {
              return <div className={cn('grid gap-2 md:grid-cols-2', active && clsx('bg-primary text-white'))}>Tools</div>;
            }
            """,
            known_locale_keys=set(),
        )

        tokens = set(contract["class_tokens_sample"])
        self.assertIn("grid", tokens)
        self.assertIn("md:grid-cols-2", tokens)
        self.assertIn("bg-primary", tokens)

    def test_ui_candidate_requires_browser_smoke_when_surface_is_medium_risk(self):
        gate = _candidate_ui_risk(
            {
                "name": "TranslationStudioPage",
                "target_layer": "features",
                "target_path": "src/./04-translation/features/TranslationStudioPage.tsx",
                "merge_readiness": "manual_review",
                "risk": "MEDIUM",
            },
            {},
        )

        self.assertTrue(gate["ui_surface"])
        self.assertEqual(gate["recommended_gate"], "browser_smoke_required")
        self.assertTrue(gate["smoke_plan"]["required"])
        self.assertEqual(gate["smoke_plan"]["suggested_route"], "/")
        self.assertEqual(gate["smoke_plan"]["route_source"], "root_fallback_not_target_route")

    def test_i18n_default_value_reduces_missing_key_false_positive(self):
        contract = analyze_ui_runtime_contract(
            "export function Label({ t }) { return <span>{t('runtime.dynamic', { defaultValue: 'Fallback' })}</span>; }",
            known_locale_keys=set(),
        )

        self.assertIn("runtime.dynamic", contract["i18n_defaulted_keys"])
        self.assertNotIn("runtime.dynamic", contract["missing_i18n_keys"])

    def test_i18n_namespace_suffix_match_is_unresolved_not_missing(self):
        missing, unresolved = _classify_i18n_keys(
            ["title", "definitelyMissing"],
            ["publish"],
            {"common.publish.title"},
        )

        self.assertIn("title", unresolved)
        self.assertIn("definitelyMissing", missing)

    def test_candidate_uses_source_symbol_contract_when_target_file_is_future_path(self):
        source_contract = analyze_ui_runtime_contract(
            """
            import { useProjectStore } from '@/stores/projectStore';
            export function ProjectAcademyPage() {
              const params = useParams();
              const project = useProjectStore(s => s.project);
              return <main>{params.projectId}{project.title}</main>;
            }
            """,
            known_locale_keys=set(),
        )
        source_index = _build_symbol_source_index(
            {
                "LINGUASCRIBE_MASTER": {
                    "symbols": [
                        {
                            "name": "ProjectAcademyPage",
                            "file": "src/app/academy/[projectId]/page.tsx",
                        }
                    ]
                }
            },
            {"LINGUASCRIBE_MASTER::src/app/academy/[projectId]/page.tsx": source_contract},
        )

        gate = _candidate_ui_risk(
            {
                "name": "ProjectAcademyPage",
                "source": "LINGUASCRIBE_MASTER",
                "target_layer": "features",
                "target_path": "src/./11-academy/features/ProjectAcademyPage.tsx",
                "merge_readiness": "manual_review",
                "risk": "MEDIUM",
            },
            {},
            source_index,
        )

        self.assertEqual(gate["contract_origin"], "source_symbol")
        self.assertEqual(
            gate["source_contract_file"],
            "LINGUASCRIBE_MASTER::src/app/academy/[projectId]/page.tsx",
        )
        self.assertIn("source_runtime_contract", gate["risk_reasons"])
        self.assertIn("store_shape", gate["dependency_closure_plan"]["required_contracts"])

    def test_duplicate_symbol_names_do_not_select_an_arbitrary_source_contract(self):
        source_index = _build_symbol_source_index(
            {
                "MAIN": {
                    "symbols": [
                        {"name": "cli", "file": "src/first.py"},
                        {"name": "cli", "file": "tests/second.py"},
                    ]
                }
            },
            {
                "MAIN::src/first.py": {"risk_tier": "high"},
                "MAIN::tests/second.py": {"risk_tier": "low"},
            },
        )

        self.assertNotIn(("MAIN", "cli"), source_index)

    def test_symbol_source_index_rejects_invalid_atlas_payload(self):
        self.assertEqual(_build_symbol_source_index([], {}), {})

    def test_candidate_hook_is_not_promoted_to_browser_smoke_surface(self):
        gate = _candidate_ui_risk(
            {
                "name": "useMarketingStudio",
                "source": "VARIANT",
                "target_layer": "features",
                "target_path": "src/./08-marketing/features/useMarketingStudio.ts",
                "merge_readiness": "manual_review",
                "risk": "MEDIUM",
            },
            {},
            {},
        )

        self.assertFalse(gate["ui_surface"])
        self.assertEqual(gate["recommended_gate"], "static_gate_sufficient")

    def test_smoke_plan_prefers_framework_route_contract(self):
        plan = _smoke_plan(
            {"target_path": "src/./04-translation/features/TranslationStudioPage.tsx", "ui_surface": True},
            "high",
            [],
            {
                "framework": "next_app_router",
                "route": "/translate/:projectId",
                "smoke_path": "/translate/__projectId__",
                "source": "filesystem_route",
            },
        )

        self.assertEqual(plan["suggested_route"], "/translate/__projectId__")
        self.assertEqual(plan["route_framework"], "next_app_router")
        self.assertEqual(plan["route_source"], "filesystem_route")


class FrameworkRouteAnalyzerTests(unittest.TestCase):
    def test_next_app_dynamic_route_to_smoke_path(self):
        route, framework = _route_from_next_app_file("src/app/translate/[projectId]/page.tsx")

        self.assertEqual(framework, "next_app_router")
        self.assertEqual(route, "/translate/:projectId")
        self.assertEqual(smoke_path_for_route(route), "/translate/__projectId__")

    def test_next_app_route_groups_parallel_and_intercepting_segments_are_normalized(self):
        grouped = _route_from_next_app_file("src/app/(marketing)/pricing/[plan]/page.tsx")
        parallel = _route_from_next_app_file("src/app/@modal/(.)photo/[id]/page.tsx")
        intercepted_parent = _route_from_next_app_file("src/app/feed/(..)settings/page.tsx")

        self.assertEqual(grouped, ("/pricing/:plan", "next_app_router"))
        self.assertEqual(parallel, ("/photo/:id", "next_app_router"))
        self.assertEqual(intercepted_parent, ("/feed/settings", "next_app_router"))

    def test_next_pages_optional_catch_all_is_normalized(self):
        self.assertEqual(
            _route_from_next_pages_file("src/pages/docs/[[...slug]].tsx"),
            ("/docs/:slug*", "next_pages_router"),
        )

    def test_v11_fixture_exposes_hybrid_and_tanstack_route_contracts(self):
        fixture_root = Path(__file__).resolve().parent / "fixtures" / "react_v11"
        from tools.validate_react_v11_contracts import build_fixture_atlas
        fixture_atlas = build_fixture_atlas(fixture_root)
        payload = analyze_project_routes("REACT_V11", fixture_root, fixture_atlas)

        self.assertTrue(payload["hybrid_next_app_pages"])
        self.assertGreaterEqual(payload["route_counts"].get("tanstack_router", 0), 2)
        self.assertGreaterEqual(payload["segment_kind_counts"].get("route_group", 0), 1)
        self.assertGreaterEqual(payload["segment_kind_counts"].get("optional_catch_all", 0), 1)

    def test_fixture_byte_identity_accepts_crlf_and_rejects_stale_source(self):
        from tools.validate_react_v11_contracts import build_fixture_atlas
        from tools.core import source_evidence
        import tempfile
        import hashlib
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "route.tsx"
            source.write_bytes(b"export const route = '/hello';\r\n")
            entry = build_fixture_atlas(root)["files"]["route.tsx"]
            self.assertEqual(entry["hash"], hashlib.sha256(source.read_bytes()).hexdigest())
            with (
                patch.object(source_evidence, "load_source_text", return_value=None),
                patch.object(source_evidence, "record_honesty_event") as record,
            ):
                args = dict(component="fixture_test", project="ISOLATED", project_root=root,
                            rel_path="route.tsx", atlas_entry=entry, reason="fixture byte identity")
                self.assertEqual(source_evidence.read_atlas_bound_source(**args), source.read_bytes().decode("utf-8"))
                record.assert_not_called()
                source.write_bytes(b"export const route = '/changed';\r\n")
                self.assertIsNone(source_evidence.read_atlas_bound_source(**args))
                self.assertEqual(record.call_args.kwargs["category"], "stale_evidence")


class UISmokeSpecGeneratorTests(unittest.TestCase):
    def test_smoke_spec_contains_route_assertions_and_contract_metadata(self):
        spec = render_playwright_smoke_spec(
            {
                "name": "TranslationStudioPage",
                "source": "LINGUASCRIBE_MASTER",
                "target_path": "src/./04-translation/features/TranslationStudioPage.tsx",
                "recommended_gate": "browser_smoke_required",
                "source_contract_file": "LINGUASCRIBE_MASTER::src/app/translate/[projectId]/page.tsx",
                "dependency_closure_plan": {"required_contracts": ["i18n_keys", "service_ports"]},
                "smoke_plan": {
                    "suggested_route": "/translation",
                    "assertions": [
                        "no_missing_provider_or_context_error",
                        "no_console_error_on_initial_render",
                        "no_visible_raw_i18n_keys",
                    ],
                },
            }
        )

        self.assertIn("await page.goto(\"/translation\")", spec)
        self.assertIn("consoleErrors", spec)
        self.assertIn("providerErrorPattern", spec)
        self.assertIn("source_contract_file: LINGUASCRIBE_MASTER::src/app/translate/[projectId]/page.tsx", spec)
        self.assertIn("required_contracts: i18n_keys, service_ports", spec)

    def test_smoke_execution_command_is_explicit_for_generated_specs(self):
        self.assertEqual(
            _execution_command("output/scripts/ui_smoke_specs/candidate.spec.ts"),
            "npx playwright test output/scripts/ui_smoke_specs/candidate.spec.ts",
        )


class PerformanceBudgetContractTests(unittest.TestCase):
    def test_watchdog_atlas_subprofiles_are_parsed_as_physical_evidence(self):
        config = {
            "log_parsing": {
                "atlas_phase_pattern": (
                    r"Atlas phases \| pre_build=([0-9.]+)s build=([0-9.]+)s bridge=([0-9.]+)s "
                    r"validate=([0-9.]+)s persist=([0-9.]+)s commit=([0-9.]+)s "
                    r"workload=([0-9.]+)s ram_cache=([0-9.]+)s"
                ),
                "atlas_persistence_pattern": (
                    r"Atlas persistence \| chars=(\d+) bytes=(\d+) serialize=([0-9.]+)s "
                    r"encode=([0-9.]+)s hash=([0-9.]+)s sqlite=([0-9.]+)s "
                    r"relational=([0-9.]+)s total=([0-9.]+)s"
                ),
            }
        }
        text = (
            "[PROFILE] Atlas phases | pre_build=1.095s build=1.513s bridge=0.046s "
            "validate=3.687s persist=1.689s commit=3.425s workload=0.107s ram_cache=0.000s\n"
            "[PROFILE] Atlas persistence | chars=44715717 bytes=44729565 serialize=0.985s "
            "encode=0.063s hash=0.128s sqlite=0.270s relational=0.213s total=1.689s\n"
        )

        phases = _latest_atlas_phase_profile(text, config)
        payload_profile = _latest_atlas_persistence_profile(text, config)

        self.assertEqual(phases["validate"], 3.687)
        self.assertEqual(phases["commit"], 3.425)
        self.assertEqual(payload_profile["state_payload_bytes"], 44729565)
        self.assertEqual(payload_profile["state_payload_sqlite_seconds"], 0.27)

    def test_force_and_normal_pipeline_samples_are_not_mixed(self):
        log = """
PIPELINE ANALYSIS SESSION STARTED
[PIPELINE] Force mode active; downstream engines will run in full mode.
PIPELINE ANALYSIS COMPLETED in 1000.00s
PIPELINE ANALYSIS SESSION STARTED
PIPELINE ANALYSIS COMPLETED in 500.00s
"""

        self.assertEqual(_pipeline_samples_by_force_profile(log, True), [1000.0])
        self.assertEqual(_pipeline_samples_by_force_profile(log, False), [500.0])

    def test_release_deep_samples_are_not_hidden_by_later_daily_session(self):
        log = """
PIPELINE ANALYSIS SESSION STARTED
[MODE] release_deep active
PIPELINE ANALYSIS COMPLETED in 1555.43s
PIPELINE ANALYSIS SESSION STARTED
[MODE] daily active
PIPELINE ANALYSIS COMPLETED in 38.71s
"""

        latest = _latest_release_deep_session(log)

        self.assertIsNotNone(latest)
        self.assertEqual(latest[1], "release_deep")
        self.assertEqual(latest[2], 1555.43)
        self.assertEqual(_pipeline_samples_by_mode(log, "release_deep"), [1555.43])

    def test_forced_full_sample_is_not_hidden_by_later_cached_release_deep(self):
        log = """
PIPELINE ANALYSIS SESSION STARTED
[PIPELINE] Force mode active; downstream engines will run in full mode.
[MODE] release_deep active
PIPELINE ANALYSIS COMPLETED in 1555.43s
PIPELINE ANALYSIS SESSION STARTED
[MODE] release_deep active
[SMART] No file changes detected. Absolute Zero speed active.
PIPELINE ANALYSIS COMPLETED in 25.42s
"""

        latest_forced = _latest_completed_forced_session(log)
        latest_release_deep = _latest_release_deep_session(log)

        self.assertIsNotNone(latest_forced)
        self.assertEqual(latest_forced[2], 1555.43)
        self.assertIsNotNone(latest_release_deep)
        self.assertEqual(latest_release_deep[2], 25.42)
        self.assertEqual(_pipeline_samples_by_force_profile(log, True), [1555.43])

    def test_rolling_budget_grace_passes_small_latest_spike_when_median_is_green(self):
        decision = _rolling_decision(
            258.0,
            [210.0, 207.0, 258.0],
            256.91,
            {"rolling_policy": {"window": 5, "min_samples": 3, "hard_fail_multiplier": 1.2}},
        )

        self.assertTrue(decision["passed"])
        self.assertEqual(decision["policy"], "rolling_median_grace")

    def test_rolling_budget_hard_fails_large_spike(self):
        decision = _rolling_decision(
            400.0,
            [210.0, 207.0, 400.0],
            256.91,
            {"rolling_policy": {"window": 5, "min_samples": 3, "hard_fail_multiplier": 1.2}},
        )

        self.assertFalse(decision["passed"])
        self.assertEqual(decision["policy"], "hard_fail_multiplier")

    def test_metric_rolling_config_supports_atlas_override(self):
        config = {
            "rolling_policy": {
                "window": 5,
                "hard_fail_multiplier": 1.2,
                "metric_overrides": {"atlas_total_budget": {"hard_fail_multiplier": 2.0}},
            }
        }

        metric_config = _metric_rolling_config(config, "atlas_total_budget")

        self.assertEqual(metric_config["rolling_policy"]["hard_fail_multiplier"], 2.0)
        self.assertNotIn("metric_overrides", metric_config["rolling_policy"])

    def test_performance_ledger_trend_summary_tracks_band_median_and_delta(self):
        summary = build_trend_summary(
            [
                {"repo_band": "L", "pipeline_total_seconds": 90.0, "atlas_total_seconds": 20.0},
                {"repo_band": "L", "pipeline_total_seconds": 100.0, "atlas_total_seconds": 25.0},
                {"repo_band": "L", "pipeline_total_seconds": 120.0, "atlas_total_seconds": 30.0},
            ]
        )

        pipeline = summary["by_repo_band"]["L"]["pipeline_total_seconds"]
        self.assertEqual(pipeline["samples"], 3)
        self.assertEqual(pipeline["median"], 100.0)
        self.assertEqual(pipeline["delta_from_previous"], -10.0)


class NextBoundaryAnalyzerTests(unittest.TestCase):
    def test_rsc_serialization_evidence_observes_client_parent_boundary(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/dashboard/client-chart.tsx",
            """
            'use client';
            export function ClientChart({ selectedAt }: { selectedAt: Date }) {
              return <div>{selectedAt.toISOString()}</div>;
            }
            """,
        )
        self.assertIsNotNone(row)
        _enrich_risk_evidence_with_context(
            [row],
            {
                "APP": {
                    "src/app/dashboard/client-chart.tsx": "'use client';",
                    "src/app/dashboard/client-shell.tsx": "'use client';\nimport { ClientChart } from './client-chart';",
                }
            },
            {"APP": {"src/app/dashboard/client-shell.tsx": ["src/app/dashboard/client-chart.tsx"]}},
        )

        evidence = row["risk_evidence"]["client_boundary_has_serialization_sensitive_prop_contract"]
        self.assertEqual(evidence["status"], "client_parent_boundary_observed")
        self.assertIn("client-shell.tsx", evidence["proof"])

    def test_rsc_serialization_evidence_keeps_server_parent_proof_required(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/dashboard/client-chart.tsx",
            """
            'use client';
            export function ClientChart({ selectedAt }: { selectedAt: Date }) {
              return <div>{selectedAt.toISOString()}</div>;
            }
            """,
        )
        self.assertIsNotNone(row)
        _enrich_risk_evidence_with_context(
            [row],
            {
                "APP": {
                    "src/app/dashboard/client-chart.tsx": "'use client';",
                    "src/app/dashboard/page.tsx": "import { ClientChart } from './client-chart';",
                }
            },
            {"APP": {"src/app/dashboard/page.tsx": ["src/app/dashboard/client-chart.tsx"]}},
        )

        evidence = row["risk_evidence"]["client_boundary_has_serialization_sensitive_prop_contract"]
        self.assertEqual(evidence["status"], "needs_parent_prop_flow_proof")
        self.assertIn("page.tsx", evidence["proof"])

    def test_route_input_evidence_observes_relative_helper_validation_contract(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/api/account/complete/route.ts",
            """
            import { completeAccountDeletion } from './complete';
            export async function GET(request: Request) {
              const callbackUrl = new URL(request.url).searchParams.get('callbackUrl');
              return completeAccountDeletion({ callbackUrl });
            }
            """,
        )
        self.assertIsNotNone(row)
        self.assertEqual(
            row["risk_evidence"]["route_input_without_visible_validation_contract"]["status"],
            "needs_transitive_helper_proof",
        )
        _enrich_risk_evidence_with_context(
            [row],
            {
                "APP": {
                    "src/app/api/account/complete/route.ts": """
                    import { completeAccountDeletion } from './complete';
                    export async function GET(request: Request) {
                      const callbackUrl = new URL(request.url).searchParams.get('callbackUrl');
                      return completeAccountDeletion({ callbackUrl });
                    }
                    """,
                    "src/app/api/account/complete/complete.ts": """
                    export function completeAccountDeletion(input) {
                      const callbackUrl = getValidatedCallbackUrl(input.callbackUrl);
                      verifyAccountDeletionIntent(input.intent);
                      return Response.redirect(callbackUrl);
                    }
                    """,
                }
            },
            {"APP": {}},
        )

        evidence = row["risk_evidence"]["route_input_without_visible_validation_contract"]
        self.assertEqual(evidence["status"], "transitive_helper_contract_observed")
        self.assertIn("complete.ts", evidence["proof"])

    def test_client_boundary_with_server_action_is_high_risk(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/project/page.tsx",
            "'use client';\nexport async function save() { 'use server'; }\nexport const metadata = {};",
        )

        self.assertIsNotNone(row)
        self.assertEqual(row["risk_tier"], "high")
        self.assertIn("client_server_boundary_mixed", row["risks"])
        self.assertIn("metadata_inside_client_boundary", row["risks"])

    def test_global_error_with_public_env_is_not_client_server_mixed(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/global-error.tsx",
            """
            'use client';
            export default function GlobalError({ error }) {
              if (process.env.NEXT_PUBLIC_SENTRY_DSN || process.env.NODE_ENV === 'development') {
                console.error(error);
              }
              return <html><body>Error</body></html>;
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("client_component", row["signals"])
        self.assertIn("public_env_boundary", row["signals"])
        self.assertNotIn("env_boundary", row["signals"])
        self.assertNotIn("client_server_boundary_mixed", row["risks"])

    def test_client_boundary_with_private_env_remains_mixed(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/settings/page.tsx",
            """
            'use client';
            export default function Settings() {
              return <div>{process.env.SECRET_TOKEN}</div>;
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("env_boundary", row["signals"])
        self.assertIn("client_server_boundary_mixed", row["risks"])
        self.assertEqual(
            row["risk_evidence"]["client_server_boundary_mixed"]["status"],
            "confirmed_local_boundary_mismatch",
        )

    def test_client_runtime_date_usage_is_not_rsc_prop_contract(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/dashboard/page.tsx",
            """
            'use client';
            import { useCallback } from 'react';
            export default function DashboardPage() {
              const onSelect = useCallback((value: string) => value, []);
              const chartData = [{ date: new Date(), values: { total: 1 } }];
              return <Chart data={chartData} onSelect={onSelect} />;
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("client_component", row["signals"])
        self.assertNotIn("rsc_serialization_sensitive_props", row["signals"])
        self.assertNotIn("client_boundary_has_serialization_sensitive_prop_contract", row["risks"])

    def test_client_data_interface_date_field_is_not_rsc_prop_contract(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/dashboard/payouts/page.tsx",
            """
            'use client';
            interface TimeseriesData {
              date: Date;
              payouts: number;
            }
            export default function PayoutsPage() {
              const data: TimeseriesData[] = [];
              return <Chart data={data} />;
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("client_component", row["signals"])
        self.assertNotIn("rsc_serialization_sensitive_props", row["signals"])
        self.assertNotIn("client_boundary_has_serialization_sensitive_prop_contract", row["risks"])

    def test_client_export_with_non_serializable_prop_contract_is_detected(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/dashboard/client-chart.tsx",
            """
            'use client';
            export function ClientChart({
              selectedAt,
              onSelect,
            }: {
              selectedAt: Date;
              onSelect: (value: string) => void;
            }) {
              return <div>{selectedAt.toISOString()}</div>;
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("rsc_serialization_sensitive_props", row["signals"])
        self.assertIn("client_boundary_has_serialization_sensitive_prop_contract", row["risks"])
        self.assertEqual(
            row["risk_evidence"]["client_boundary_has_serialization_sensitive_prop_contract"]["status"],
            "needs_parent_boundary_proof",
        )

    def test_export_const_component_with_callback_prop_requires_parent_boundary_proof(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/dashboard/client-sheet.tsx",
            """
            'use client';
            export const ClientSheet = ({
              onClose,
            }: {
              onClose: () => void;
            }) => {
              return <button onClick={onClose}>Close</button>;
            };
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("rsc_serialization_parent_boundary_proof_required", row["signals"])
        self.assertNotIn("rsc_serialization_sensitive_props", row["signals"])
        self.assertNotIn("client_boundary_has_serialization_sensitive_prop_contract", row["risks"])

    def test_mutation_without_revalidation_contract_is_detected(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/projects/actions.ts",
            """
            'use server';
            export async function saveProject(input) {
              await db.project.update({ data: input });
            }
            export const revalidate = 3600;
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("mutation_surface", row["signals"])
        self.assertIn("static_cache_contract", row["signals"])
        self.assertIn("mutation_without_visible_revalidation_contract", row["risks"])
        self.assertIn("static_cache_mutation_without_tag_or_path_revalidation", row["risks"])

    def test_mutation_with_revalidation_contract_is_not_flagged(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/projects/actions.ts",
            """
            'use server';
            export async function saveProject(input) {
              await db.project.update({ data: input });
              revalidatePath('/projects');
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("revalidation_contract", row["signals"])
        self.assertNotIn("mutation_without_visible_revalidation_contract", row["risks"])

    def test_server_action_mutation_without_cache_contract_is_not_forced_to_revalidate(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/projects/actions.ts",
            """
            'use server';
            export async function saveProject(input) {
              await db.project.update({ data: input });
              return { success: true };
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("server_action", row["signals"])
        self.assertIn("mutation_surface", row["signals"])
        self.assertNotIn("static_cache_contract", row["signals"])
        self.assertNotIn("mutation_without_visible_revalidation_contract", row["risks"])

    def test_api_route_mutation_is_not_forced_to_revalidate_without_cache_contract(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/api/admin/ban/route.ts",
            """
            export async function DELETE(request: NextRequest) {
              const body = await request.json();
              await prisma.user.delete({ where: { id: body.id } });
              return Response.json({ ok: true });
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("mutation_surface", row["signals"])
        self.assertIn("api_route_mutation_surface", row["signals"])
        self.assertNotIn("mutation_without_visible_revalidation_contract", row["risks"])

    def test_cached_api_route_mutation_still_requires_revalidation_contract(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/api/admin/rebuild/route.ts",
            """
            export const revalidate = 3600;
            export async function POST() {
              await db.project.update({ data: { rebuilt: true } });
              return Response.json({ ok: true });
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("static_cache_contract", row["signals"])
        self.assertIn("mutation_without_visible_revalidation_contract", row["risks"])

    def test_force_dynamic_api_route_mutation_does_not_require_revalidation(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/api/cron/cleanup/expired-tokens/route.ts",
            """
            export const dynamic = "force-dynamic";
            export async function POST(req: Request) {
              const rawBody = await req.text();
              await verifyQstashSignature({ req, rawBody });
              await prisma.verificationToken.deleteMany({ where: { expires: { lt: new Date() } } });
              return Response.json({ ok: true });
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("dynamic_no_static_cache", row["signals"])
        self.assertIn("mutation_surface", row["signals"])
        self.assertNotIn("cache_or_dynamic_segment", row["signals"])
        self.assertNotIn("mutation_without_visible_revalidation_contract", row["risks"])

    def test_force_no_store_fetch_cache_is_not_static_cache_mutation_risk(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/api/mcp/route.ts",
            """
            export const runtime = "nodejs";
            export const fetchCache = "force-no-store";
            export async function POST(request: NextRequest) {
              await handleAuthenticatedMcpRequest(request, mcpHandler);
              return Response.json({ ok: true });
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("dynamic_no_static_cache", row["signals"])
        self.assertNotIn("static_cache_contract", row["signals"])
        self.assertNotIn("mutation_without_visible_revalidation_contract", row["risks"])

    def test_response_cache_control_revalidate_string_is_not_segment_cache_contract(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/api/logo/route.ts",
            """
            export async function GET(request: NextRequest) {
              const response = await fetch("https://example.com/logo.png");
              const imageResponse = new Response(await response.arrayBuffer());
              imageResponse.headers.set("Cache-Control", "s-maxage=86400, stale-while-revalidate=60");
              return imageResponse;
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertNotIn("static_cache_contract", row["signals"])
        self.assertNotIn("cache_or_dynamic_segment", row["signals"])
        self.assertNotIn("mutation_without_visible_revalidation_contract", row["risks"])

    def test_force_static_api_route_mutation_requires_revalidation(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/api/admin/rebuild/route.ts",
            """
            export const dynamic = "force-static";
            export async function POST() {
              await db.project.update({ data: { rebuilt: true } });
              return Response.json({ ok: true });
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("cache_or_dynamic_segment", row["signals"])
        self.assertIn("mutation_without_visible_revalidation_contract", row["risks"])

    def test_framework_server_function_contract_is_not_revalidation_obligation(self):
        row = analyze_next_boundary_file(
            "APP",
            "app/(payload)/layout.tsx",
            """
            'use server';
            import { ServerFunctionClient } from "payload";
            export default async function RootLayout() {
              await handleServerFunctions();
              return <ServerFunctionClient />;
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("framework_server_function_contract", row["signals"])
        self.assertNotIn("mutation_surface", row["signals"])
        self.assertNotIn("mutation_without_visible_revalidation_contract", row["risks"])

    def test_reference_mutation_surface_is_not_revalidation_obligation(self):
        row = analyze_next_boundary_file(
            "APP",
            "examples/nextjs/src/app/actions.ts",
            """
            'use server';
            import { NextResponse } from "next/server";
            export async function saveExample(input) {
              await db.example.update({ data: input });
              return NextResponse.json({ ok: true });
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("reference_or_mock_route_surface", row["signals"])
        self.assertIn("mutation_surface", row["signals"])
        self.assertNotIn("mutation_without_visible_revalidation_contract", row["risks"])

    def test_string_form_action_is_navigation_not_server_action_binding(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/components/cancel-dialog.tsx",
            """
            import { Form } from "@remix-run/react";
            export function CancelDialog({ runFriendlyId }) {
              return (
                <Form action={`/resources/taskruns/${runFriendlyId}/cancel`} method="post">
                  <button type="submit">Cancel</button>
                </Form>
              );
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("form_navigation_action", row["signals"])
        self.assertNotIn("form_action_binding", row["signals"])
        self.assertNotIn("form_action_without_visible_pending_contract", row["risks"])
        self.assertNotIn("server_action_without_visible_error_contract", row["risks"])

    def test_remix_form_action_is_navigation_not_next_server_action_binding(self):
        row = analyze_next_boundary_file(
            "APP",
            "apps/webapp/app/components/sessions/v1/CloseSessionDialog.tsx",
            """
            import { Form, useNavigation } from "@remix-run/react";
            export function CloseSessionDialog({ formAction }) {
              const navigation = useNavigation();
              return <Form action={formAction} method="post"><button disabled={navigation.state === "submitting"}>Close</button></Form>;
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("form_navigation_action", row["signals"])
        self.assertNotIn("form_action_binding", row["signals"])
        self.assertNotIn("server_action_without_visible_error_contract", row["risks"])

    def test_integration_template_form_action_is_reference_surface(self):
        row = analyze_next_boundary_file(
            "APP",
            "integrations/react-next-15/app/page.tsx",
            """
            import { headers } from "next/headers";
            import { queryExampleAction } from "./_action";
            export default function Home() {
              void headers();
              return <form action={queryExampleAction}><button>Increment</button></form>;
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("reference_or_mock_route_surface", row["signals"])
        self.assertIn("form_action_binding", row["signals"])
        self.assertNotIn("form_action_without_visible_pending_contract", row["risks"])
        self.assertNotIn("server_action_without_visible_error_contract", row["risks"])

    def test_storybook_template_inline_form_action_is_reference_surface(self):
        row = analyze_next_boundary_file(
            "APP",
            "code/frameworks/nextjs/template/stories_nextjs-default-ts/NextHeader.tsx",
            """
            import { cookies } from "next/headers";
            export function NextHeader() {
              async function handleClick() {
                "use server";
                cookies();
              }
              return <form action={handleClick}><button>Run</button></form>;
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("reference_or_mock_route_surface", row["signals"])
        self.assertIn("form_action_binding", row["signals"])
        self.assertNotIn("form_action_without_visible_pending_contract", row["risks"])
        self.assertNotIn("server_action_without_visible_error_contract", row["risks"])

    def test_function_form_action_remains_server_action_binding(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/page.tsx",
            """
            import { queryExampleAction } from './_action';
            export default function Home() {
              return <form action={queryExampleAction}><button type="submit">Increment</button></form>;
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("form_action_binding", row["signals"])
        self.assertIn("form_action_without_visible_pending_contract", row["risks"])

    def test_domain_metadata_field_is_not_next_metadata_contract(self):
        row = analyze_next_boundary_file(
            "APP",
            "apps/web/ui/analytics/events/events-table.tsx",
            """
            "use client";
            import { useParams } from "next/navigation";
            type EventData = { metadata?: Record<string, string> };
            export function EventsTable({ row }: { row: { original: EventData } }) {
              useParams();
              const metadata = row.original.metadata || {};
              return <pre>{metadata.source}</pre>;
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("client_component", row["signals"])
        self.assertNotIn("metadata_contract", row["signals"])
        self.assertNotIn("metadata_inside_client_boundary", row["risks"])

    def test_exported_metadata_inside_client_boundary_remains_risk(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/page.tsx",
            """
            "use client";
            export const metadata = { title: "Dashboard" };
            export default function Page() {
              return <main>Dashboard</main>;
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("metadata_contract", row["signals"])
        self.assertIn("metadata_inside_client_boundary", row["risks"])

    def test_docs_embedded_client_env_example_is_reference_surface(self):
        row = analyze_next_boundary_file(
            "APP",
            "docs/onboarding/product-analytics/nextjs.tsx",
            """
            import { CodeBlock } from "./code-block";
            export function NextJSDocs() {
              return <CodeBlock code={`"use client";
                import { usePathname } from "next/navigation";
                posthog.init(process.env.NEXT_PUBLIC_POSTHOG_PROJECT_TOKEN);
              `} />;
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("reference_or_mock_route_surface", row["signals"])
        self.assertNotIn("client_server_boundary_mixed", row["risks"])

    def test_private_env_inside_client_boundary_remains_risk(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/providers.tsx",
            """
            "use client";
            export function Providers() {
              return <div>{process.env.INTERNAL_API_KEY}</div>;
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("env_boundary", row["signals"])
        self.assertIn("client_server_boundary_mixed", row["risks"])

    def test_safe_action_client_counts_as_server_action_error_contract(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/actions/update-program.ts",
            """
            'use server';
            export const updateProgramAction = authActionClient
              .inputSchema(schema)
              .action(async ({ parsedInput }) => {
                await prisma.program.update({ data: parsedInput });
                return { success: true };
              });
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("server_action_client_contract", row["signals"])
        self.assertIn("server_action_error_contract", row["signals"])
        self.assertNotIn("server_action_without_visible_error_contract", row["risks"])

    def test_custom_named_action_client_counts_as_server_action_error_contract(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/actions/onboard-partner.ts",
            """
            'use server';
            export const onboardPartnerAction = authUserActionClient
              .inputSchema(schema)
              .action(async ({ ctx, parsedInput }) => {
                await prisma.partner.create({ data: parsedInput });
              });
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("server_action_client_contract", row["signals"])
        self.assertIn("server_action_error_contract", row["signals"])
        self.assertNotIn("server_action_without_visible_error_contract", row["risks"])

    def test_framework_server_function_layout_counts_as_error_contract(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/(payload)/layout.tsx",
            """
            import type { ServerFunctionClient } from 'payload';
            import { handleServerFunctions, RootLayout } from '@payloadcms/next/layouts';
            const serverFunction: ServerFunctionClient = async function (args) {
              'use server';
              return handleServerFunctions({ ...args, config, importMap });
            };
            export default function Layout({ children }) {
              return <RootLayout serverFunction={serverFunction}>{children}</RootLayout>;
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("framework_server_function_contract", row["signals"])
        self.assertIn("server_action_error_contract", row["signals"])
        self.assertNotIn("server_action_without_visible_error_contract", row["risks"])

    def test_success_false_message_return_counts_as_error_contract(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/actions/logout.ts",
            """
            'use server';
            export async function logout() {
              if (!logoutResult) {
                return { message: 'Logout failed', success: false };
              }
              return { message: 'Logged out', success: true };
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("server_action_error_contract", row["signals"])
        self.assertNotIn("server_action_without_visible_error_contract", row["risks"])

    def test_client_action_state_error_render_counts_as_error_contract(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/share/[dashboardId]/form.tsx",
            """
            "use client";
            import { useActionState } from "react";
            import { verifyPassword } from "./action";
            export function DashboardPasswordForm() {
              const [state, formAction] = useActionState(verifyPassword, { error: null });
              return <form action={formAction}>{state.error && <p>{state.error}</p>}</form>;
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("server_action_error_contract", row["signals"])
        self.assertNotIn("server_action_without_visible_error_contract", row["risks"])

    def test_revalidate_only_server_action_is_error_contract_exempt(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/availability/actions.ts",
            """
            "use server";
            import { revalidatePath } from "next/cache";
            export async function revalidateAvailabilityList() {
              revalidatePath("/availability");
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("revalidation_contract", row["signals"])
        self.assertNotIn("server_action_without_visible_error_contract", row["risks"])

    def test_raw_form_action_without_error_contract_remains_risk(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/components/DeleteDialog.tsx",
            """
            export function DeleteDialog({ deleteAction }) {
              return <form action={deleteAction}><button>Delete</button></form>;
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("form_action_binding", row["signals"])
        self.assertNotIn("server_action_error_contract", row["signals"])
        self.assertIn("server_action_without_visible_error_contract", row["risks"])

    def test_server_module_page_component_is_not_automatically_server_action(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/settings/app-connection/page.tsx",
            """
            "use server";
            export const AppConnectionPage = async ({ params }) => {
              const { workspaceId } = await params;
              const { workspace } = await getWorkspaceAuth(workspaceId);
              return <div>{workspace.id}</div>;
            };
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("server_module", row["signals"])
        self.assertNotIn("server_action", row["signals"])
        self.assertNotIn("server_action_without_visible_error_contract", row["risks"])

    def test_inline_server_action_inside_component_is_detected(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/cookies/page.tsx",
            """
            export default async function Component() {
              async function handleClick() {
                "use server";
                (await cookies()).set("user-id", "encrypted-id");
              }
              return <form action={handleClick}><button>Save</button></form>;
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("server_action", row["signals"])

    def test_remix_action_data_counts_as_form_error_contract(self):
        row = analyze_next_boundary_file(
            "APP",
            "apps/webapp/app/components/runs/v3/ReplayRunDialog.tsx",
            """
            import { Form, useActionData } from "@remix-run/react";
            import { FormError } from "~/components/primitives/FormError";
            export function ReplayRunDialog() {
              const lastSubmission = useActionData();
              return <Form action="/resources/taskruns/1/replay" method="post"><FormError>{lastSubmission?.error}</FormError></Form>;
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("server_action_error_contract", row["signals"])
        self.assertNotIn("server_action_without_visible_error_contract", row["risks"])

    def test_edge_runtime_node_only_api_is_high_risk(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/api/export/route.ts",
            """
            export const runtime = 'edge';
            import fs from 'fs';
            export async function GET() {
              return Response.json({ ok: fs.existsSync('.') });
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertEqual(row["risk_tier"], "high")
        self.assertIn("edge_runtime", row["signals"])
        self.assertIn("node_only_runtime_api", row["signals"])
        self.assertIn("edge_runtime_imports_node_only_api", row["risks"])

    def test_route_input_without_validation_contract_is_detected(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/api/projects/route.ts",
            """
            export async function POST(request: NextRequest) {
              const body = await request.json();
              return Response.json(await saveProject(body.projectId));
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("route_input_surface", row["signals"])
        self.assertIn("route_input_without_visible_validation_contract", row["risks"])

    def test_route_input_with_validation_contract_is_not_flagged(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/api/projects/route.ts",
            """
            const ProjectSchema = z.object({ projectId: z.string() });
            export async function POST(request: NextRequest) {
              const body = ProjectSchema.safeParse(await request.json());
              return Response.json(body.success);
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("input_validation_contract", row["signals"])
        self.assertNotIn("route_input_without_visible_validation_contract", row["risks"])

    def test_route_context_params_without_validation_contract_is_detected(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/api/projects/[projectId]/route.ts",
            """
            export async function GET(_request: Request, { params }: { params: { projectId: string } }) {
              return Response.json({ id: params.projectId });
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("route_context_params_surface", row["signals"])
        self.assertIn("route_input_surface", row["signals"])
        self.assertIn("route_input_without_visible_validation_contract", row["risks"])

    def test_route_context_params_with_validation_contract_is_not_flagged(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/api/projects/[projectId]/route.ts",
            """
            const ParamsSchema = z.object({ projectId: z.string().uuid() });
            export async function GET(_request: Request, { params }: { params: { projectId: string } }) {
              const parsed = ParamsSchema.parse(params);
              return Response.json({ id: parsed.projectId });
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("route_context_params_surface", row["signals"])
        self.assertIn("input_validation_contract", row["signals"])
        self.assertNotIn("route_input_without_visible_validation_contract", row["risks"])

    def test_typed_next_request_without_input_read_is_not_input_surface(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/api/open/route.ts",
            """
            import type { NextRequest } from "next/server";
            import cors from "@/lib/cors";
            export function GET(request: NextRequest) {
              const url = request.nextUrl.toString();
              return cors(request, new Response(JSON.stringify({ url })));
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertNotIn("route_input_surface", row["signals"])
        self.assertNotIn("route_input_without_visible_validation_contract", row["risks"])

    def test_next_url_search_params_are_input_surface(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/api/search/route.ts",
            """
            import type { NextRequest } from "next/server";
            export function GET(request: NextRequest) {
              const q = request.nextUrl.searchParams.get("q");
              return Response.json({ q });
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("route_input_surface", row["signals"])
        self.assertIn("route_input_without_visible_validation_contract", row["risks"])

    def test_nextauth_framework_handler_counts_as_input_contract(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/api/auth/[...nextauth]/route.ts",
            """
            import NextAuth from "next-auth";
            const handler = async (req: Request, ctx: any) => {
              const eventId = req.headers.get("x-request-id") ?? undefined;
              return NextAuth({ callbacks: { session(params: any) { return params.session; } } })(req, ctx);
            };
            export { handler as GET, handler as POST };
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("input_validation_contract", row["signals"])
        self.assertNotIn("route_input_without_visible_validation_contract", row["risks"])

    def test_manual_typeof_guard_counts_as_input_contract(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/api/oauth/callback/route.ts",
            """
            import { NextResponse } from "next/server";
            export async function GET(req: Request) {
              const url = new URL(req.url);
              const code = url.searchParams.get("code");
              if (code && typeof code !== "string") {
                return NextResponse.json({ error: "bad code" }, { status: 400 });
              }
              return NextResponse.json({ code });
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("input_validation_contract", row["signals"])
        self.assertNotIn("route_input_without_visible_validation_contract", row["risks"])

    def test_oauth_state_consumer_counts_as_input_contract(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/api/integrations/notion/callback/route.ts",
            """
            import { NextResponse } from "next/server";
            export async function GET(req: Request) {
              const url = new URL(req.url);
              const state = url.searchParams.get("state");
              const oauthState = await consumeIntegrationOAuthState({ provider: "notion", state });
              return NextResponse.json({ workspaceId: oauthState.workspaceId });
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("input_validation_contract", row["signals"])
        self.assertNotIn("route_input_without_visible_validation_contract", row["risks"])

    def test_required_field_guard_counts_as_input_contract(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/agents/feedback/route.ts",
            """
            export async function POST(req: Request) {
              const { agent, path, feedback } = await req.json();
              if (!agent || !path || !feedback) {
                return new Response("Missing required fields", { status: 400 });
              }
              return new Response("ok");
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("input_validation_contract", row["signals"])
        self.assertNotIn("route_input_without_visible_validation_contract", row["risks"])

    def test_path_shape_guard_counts_as_input_contract(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/preview/route.ts",
            """
            export async function GET(req: Request) {
              const { searchParams } = new URL(req.url);
              const path = searchParams.get("path");
              if (!path) return new Response("missing", { status: 404 });
              if (!path.startsWith("/")) return new Response("bad path", { status: 400 });
              return Response.redirect(path);
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("input_validation_contract", row["signals"])
        self.assertNotIn("route_input_without_visible_validation_contract", row["risks"])

    def test_schema_parse_counts_as_input_contract(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/api/providers/route.ts",
            """
            import { getUrlQuerySchema } from "@/lib/zod/schemas/links";
            export async function GET(req: NextRequest) {
              const { url } = getUrlQuerySchema.parse({
                url: req.nextUrl.searchParams.get("url"),
              });
              return Response.json({ url });
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("input_validation_contract", row["signals"])
        self.assertNotIn("route_input_without_visible_validation_contract", row["risks"])

    def test_json_parse_alone_does_not_count_as_input_contract(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/api/import/route.ts",
            """
            export async function POST(req: Request) {
              const body = JSON.parse(await req.text());
              return Response.json(body);
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertNotIn("input_validation_contract", row["signals"])
        self.assertIn("route_input_without_visible_validation_contract", row["risks"])

    def test_env_secret_comparison_counts_as_input_contract(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/api/cron/webhookTriggers/route.ts",
            """
            export async function POST(req: NextRequest) {
              const apiKey = req.headers.get("authorization") || req.nextUrl.searchParams.get("apiKey");
              if (process.env.CRON_API_KEY !== apiKey) {
                return Response.json({ message: "Not authenticated" }, { status: 401 });
              }
              return Response.json({ ok: true });
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("input_validation_contract", row["signals"])
        self.assertNotIn("route_input_without_visible_validation_contract", row["risks"])

    def test_domain_or_throw_helper_counts_as_input_contract(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/api/domains/[domain]/primary/route.ts",
            """
            export const POST = withWorkspace(async ({ workspace, params }) => {
              const { slug: domain } = await getDomainOrThrow({
                workspace,
                domain: params.domain,
                dubDomainChecks: true,
              });
              return Response.json({ domain });
            });
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("input_validation_contract", row["signals"])
        self.assertNotIn("route_input_without_visible_validation_contract", row["risks"])

    def test_prisma_find_unique_or_throw_alone_does_not_count_as_input_contract(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/api/admin/ban/route.ts",
            """
            export async function POST(req: Request) {
              const { email } = await req.json();
              const user = await prisma.user.findUniqueOrThrow({ where: { email } });
              return Response.json({ user });
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertNotIn("input_validation_contract", row["signals"])
        self.assertIn("route_input_without_visible_validation_contract", row["risks"])

    def test_literal_allowlist_comparison_counts_as_input_contract(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/api/tags/route.ts",
            """
            export async function GET(request: Request) {
              const { searchParams } = new URL(request.url);
              const area = searchParams.get("area");
              if (area !== "admin" && area !== "store") {
                return Response.json({ error: "bad area" }, { status: 400 });
              }
              return Response.json({ area });
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("input_validation_contract", row["signals"])
        self.assertNotIn("route_input_without_visible_validation_contract", row["risks"])

    def test_mock_api_route_is_not_production_input_validation_risk(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/api/mock/rewardful/affiliates/route.ts",
            """
            export async function GET(request: NextRequest) {
              const page = parseInt(request.nextUrl.searchParams.get("page") || "1");
              return Response.json({ page, data: [] });
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("reference_or_mock_route_surface", row["signals"])
        self.assertNotIn("route_input_without_visible_validation_contract", row["risks"])

    def test_example_route_is_not_production_input_validation_risk(self):
        row = analyze_next_boundary_file(
            "APP",
            "examples/react/nextjs-suspense-streaming/src/app/api/wait/route.ts",
            """
            import { NextResponse } from "next/server";
            export async function GET(request: Request) {
              const { searchParams } = new URL(request.url);
              const wait = Number(searchParams.get("wait"));
              return NextResponse.json({ wait });
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("reference_or_mock_route_surface", row["signals"])
        self.assertNotIn("route_input_without_visible_validation_contract", row["risks"])

    def test_numeric_query_without_guard_remains_input_risk(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/api/wait/route.ts",
            """
            export async function GET(request: Request) {
              const { searchParams } = new URL(request.url);
              const wait = Number(searchParams.get("wait"));
              await new Promise((resolve) => setTimeout(resolve, wait));
              return Response.json({ wait });
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertNotIn("input_validation_contract", row["signals"])
        self.assertIn("route_input_without_visible_validation_contract", row["risks"])

    def test_raw_json_body_without_validation_remains_input_risk(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/api/admin/ban/route.ts",
            """
            import { NextResponse } from "next/server";
            export async function POST(req: Request) {
              const { email } = await req.json();
              await banUser(email);
              return NextResponse.json({ ok: true });
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("route_input_surface", row["signals"])
        self.assertNotIn("input_validation_contract", row["signals"])
        self.assertIn("route_input_without_visible_validation_contract", row["risks"])
        self.assertEqual(
            row["risk_evidence"]["route_input_without_visible_validation_contract"]["status"],
            "confirmed_local_contract_missing",
        )

    def test_read_only_route_input_risk_requires_transitive_helper_proof(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/auth/account-deletion/sso/complete/route.ts",
            """
            export const GET = async (request: NextRequest) => {
              const intent = request.nextUrl.searchParams.getAll("intent");
              return completeAccountDeletionSsoIdentityConfirmationAndGetRedirectPath({ intent });
            };
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("route_input_surface", row["signals"])
        self.assertIn("route_input_without_visible_validation_contract", row["risks"])
        self.assertEqual(
            row["risk_evidence"]["route_input_without_visible_validation_contract"]["status"],
            "needs_transitive_helper_proof",
        )

    def test_route_guard_contract_satisfies_explicit_boundary_guard(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/api/admin/analytics/route.ts",
            """
            import { withAdmin } from "@/lib/auth";
            import { NextResponse } from "next/server";
            export const GET = withAdmin(async ({ searchParams }) => {
              return NextResponse.json(await getAnalytics(searchParams));
            });
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("route_guard_contract", row["signals"])
        self.assertNotIn("route_handler_without_explicit_env_boundary", row["risks"])

    def test_cors_route_guard_contract_satisfies_explicit_boundary_guard(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/api/open/route.ts",
            """
            import type { NextRequest } from "next/server";
            import cors from "@/lib/cors";
            export function GET(request: NextRequest) {
              return cors(request, new Response(JSON.stringify({ ok: true })));
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("route_guard_contract", row["signals"])
        self.assertNotIn("route_handler_without_explicit_env_boundary", row["risks"])

    def test_qstash_signature_counts_as_signed_request_contract(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/api/cron/cleanup/expired-tokens/route.ts",
            """
            export const dynamic = "force-dynamic";
            export async function POST(req: Request) {
              const rawBody = await req.text();
              await verifyQstashSignature({ req, rawBody });
              await prisma.verificationToken.deleteMany({});
              return Response.json({ ok: true });
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("route_guard_contract", row["signals"])
        self.assertIn("input_validation_contract", row["signals"])
        self.assertNotIn("route_handler_without_explicit_env_boundary", row["risks"])
        self.assertNotIn("route_input_without_visible_validation_contract", row["risks"])

    def test_with_cron_counts_as_route_guard_contract(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/api/cron/cleanup/orphaned-rewards/route.ts",
            """
            export const dynamic = "force-dynamic";
            export const POST = withCron(async () => {
              await prisma.reward.deleteMany({});
              return Response.json({ ok: true });
            });
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("route_guard_contract", row["signals"])
        self.assertNotIn("route_handler_without_explicit_env_boundary", row["risks"])

    def test_oauth_controller_token_counts_as_framework_input_contract(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/api/auth/saml/token/route.ts",
            """
            export async function POST(req: Request) {
              const { oauthController } = await jackson();
              const formData = await req.formData();
              const body = Object.fromEntries(formData.entries());
              const token = await oauthController.token(body as any);
              return Response.json(token);
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("input_validation_contract", row["signals"])
        self.assertNotIn("route_input_without_visible_validation_contract", row["risks"])

    def test_read_only_query_route_does_not_require_env_boundary(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/auth/account-deletion/sso/complete/route.ts",
            """
            export const GET = async (request: NextRequest) => {
              const intent = request.nextUrl.searchParams.getAll("intent");
              return NextResponse.redirect(new URL(`/done?intent=${intent[0]}`, request.url));
            };
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("route_input_surface", row["signals"])
        self.assertNotIn("route_handler_without_explicit_env_boundary", row["risks"])

    def test_v3_api_wrapper_schema_counts_as_route_and_input_contract(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/api/v3/surveys/route.ts",
            """
            export const POST = withV3ApiWrapper({
              auth: "both",
              schemas: { body: ZV3CreateSurveyBody },
              handler: async ({ parsedInput }) => Response.json(parsedInput.body),
            });
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("route_guard_contract", row["signals"])
        self.assertIn("input_validation_contract", row["signals"])
        self.assertNotIn("route_handler_without_explicit_env_boundary", row["risks"])
        self.assertNotIn("route_input_without_visible_validation_contract", row["risks"])

    def test_client_component_browser_fetch_is_not_next_cache_policy_risk(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/admin/components/ban-link.tsx",
            """
            'use client';
            export function BanLink() {
              async function handleSubmit() {
                await fetch('/api/admin/links/ban', { method: 'DELETE' });
              }
              return <button onClick={handleSubmit}>Ban</button>;
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("browser_fetch_surface", row["signals"])
        self.assertNotIn("fetch_without_explicit_cache_policy", row["risks"])

    def test_server_fetch_without_cache_policy_is_observed_without_static_context(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/projects/page.tsx",
            """
            export default async function ProjectsPage() {
              const res = await fetch('https://api.example.com/projects');
              return <pre>{await res.text()}</pre>;
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("next_server_fetch_cache_scope", row["signals"])
        self.assertIn("server_fetch_policy_unspecified", row["signals"])
        self.assertNotIn("fetch_without_explicit_cache_policy", row["risks"])

    def test_static_segment_fetch_without_cache_policy_remains_detected(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/projects/page.tsx",
            """
            export const dynamic = "force-static";
            export default async function ProjectsPage() {
              const res = await fetch("https://example.com/projects");
              return <pre>{await res.text()}</pre>;
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("next_server_fetch_cache_scope", row["signals"])
        self.assertIn("cache_or_dynamic_segment", row["signals"])
        self.assertIn("fetch_without_explicit_cache_policy", row["risks"])

    def test_next_route_fetch_without_cache_policy_is_observed_by_default(self):
        row = analyze_next_boundary_file(
            "APP",
            "apps/web/app/api/domains/[domain]/validate/route.ts",
            """
            import { NextResponse } from "next/server";
            export async function GET() {
              const response = await fetch("https://example.com", { method: "HEAD" });
              return NextResponse.json({ ok: response.ok });
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("route_handler", row["signals"])
        self.assertIn("next_server_fetch_cache_scope", row["signals"])
        self.assertIn("server_fetch_policy_unspecified", row["signals"])
        self.assertNotIn("fetch_without_explicit_cache_policy", row["risks"])

    def test_non_next_remix_fetch_is_not_next_cache_policy_risk(self):
        row = analyze_next_boundary_file(
            "APP",
            "apps/remix/app/components/general/pdf-viewer/pdf-viewer.tsx",
            """
            import { useEffect, useState } from "react";
            export function PdfViewer({ data }: { data: string }) {
              const [ok, setOk] = useState(false);
              useEffect(() => {
                void fetch(data).then((response) => setOk(response.ok));
              }, [data]);
              return <div>{String(ok)}</div>;
            }
            """,
        )

        self.assertIsNone(row)

    def test_example_page_fetch_is_not_next_cache_policy_risk(self):
        row = analyze_next_boundary_file(
            "APP",
            "examples/react/pagination/src/pages/index.tsx",
            """
            import React from "react";
            export default function Example() {
              React.useEffect(() => {
                void fetch("/api/projects");
              }, []);
              return <div />;
            }
            """,
        )

        self.assertIsNone(row)

    def test_test_file_fetch_is_not_next_cache_policy_risk(self):
        row = analyze_next_boundary_file(
            "APP",
            "apps/web/tests/redirects/index.test.ts",
            """
            import { expect, test } from "vitest";
            test("redirect", async () => {
              const response = await fetch("/api/redirects");
              expect(response.ok).toBe(true);
            });
            """,
        )

        self.assertIsNone(row)

    def test_next_imported_ui_browser_fetch_is_not_server_cache_policy_risk(self):
        row = analyze_next_boundary_file(
            "APP",
            "apps/web/ui/modals/delete-account-modal.tsx",
            """
            import { useRouter } from "next/navigation";
            import { useState } from "react";
            export function DeleteAccountModal() {
              const router = useRouter();
              const [deleting, setDeleting] = useState(false);
              async function deleteAccount() {
                setDeleting(true);
                await fetch("/api/user", { method: "DELETE" });
                router.push("/register");
              }
              return <button onClick={deleteAccount}>{String(deleting)}</button>;
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("browser_runtime_surface", row["signals"])
        self.assertIn("browser_fetch_surface", row["signals"])
        self.assertNotIn("fetch_without_explicit_cache_policy", row["risks"])

    def test_remix_app_model_fetch_is_not_next_cache_policy_risk(self):
        row = analyze_next_boundary_file(
            "APP",
            "apps/webapp/app/models/vercelIntegration.server.ts",
            """
            import { z } from "zod";
            import { env } from "~/env.server";
            export async function listProjects() {
              const response = await fetch("https://api.vercel.com/v9/projects", {
                headers: { Authorization: `Bearer ${env.VERCEL_TOKEN}` },
              });
              return z.array(z.object({ id: z.string() })).parse(await response.json());
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("non_next_fetch_surface", row["signals"])
        self.assertNotIn("fetch_without_explicit_cache_policy", row["risks"])

    def test_spa_src_pages_fetch_is_not_next_cache_policy_risk_without_next_data_function(self):
        row = analyze_next_boundary_file(
            "APP",
            "frontend/src/pages/secret-manager/SecretApprovalRequest.tsx",
            """
            import { useState } from "react";
            export function SecretApprovalRequest() {
              const [open, setOpen] = useState(false);
              async function approve() {
                await fetch("/api/approvals", { method: "POST" });
                setOpen(false);
              }
              return <button onClick={approve}>{String(open)}</button>;
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("browser_fetch_surface", row["signals"])
        self.assertNotIn("fetch_without_explicit_cache_policy", row["risks"])

    def test_pages_router_get_server_side_props_fetch_is_observed_by_default(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/pages/projects.tsx",
            """
            export async function getServerSideProps() {
              const response = await fetch("https://api.example.com/projects");
              return { props: { text: await response.text() } };
            }
            export default function Projects() {
              return <div />;
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("next_server_fetch_cache_scope", row["signals"])
        self.assertIn("server_fetch_policy_unspecified", row["signals"])
        self.assertNotIn("fetch_without_explicit_cache_policy", row["risks"])

    def test_remix_app_routes_route_tsx_is_not_next_route_handler(self):
        row = analyze_next_boundary_file(
            "APP",
            "apps/webapp/app/routes/storybook.streamdown/route.tsx",
            """
            export async function loader() {
              const response = await fetch("https://example.com/storybook");
              return new Response(await response.text());
            }
            """,
        )

        self.assertIsNone(row)

    def test_static_cache_contract_satisfies_fetch_cache_policy(self):
        row = analyze_next_boundary_file(
            "APP",
            "packages/twenty-website/src/lib/community/fetch-github-star-count.ts",
            """
            import { unstable_cache } from "next/cache";
            export const fetchGithubStarCount = unstable_cache(async () => {
              const response = await fetch("https://api.github.com/repos/twentyhq/twenty");
              return response.json();
            }, ["github-stars"]);
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("static_cache_contract", row["signals"])
        self.assertNotIn("fetch_without_explicit_cache_policy", row["risks"])

    def test_response_cache_control_satisfies_server_fetch_cache_policy(self):
        row = analyze_next_boundary_file(
            "APP",
            "apps/openpage-api/app/community/total-stars/route.ts",
            """
            export async function GET(request: Request) {
              const res = await fetch("https://example.com/stats");
              const data = await res.json();
              return new Response(JSON.stringify(data), {
                headers: {
                  "Cache-Control": "public, s-maxage=3600, stale-while-revalidate=7200",
                },
              });
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("next_server_fetch_cache_scope", row["signals"])
        self.assertIn("fetch_cache_policy", row["signals"])
        self.assertNotIn("fetch_without_explicit_cache_policy", row["risks"])

    def test_force_dynamic_route_satisfies_server_fetch_cache_policy(self):
        row = analyze_next_boundary_file(
            "APP",
            "apps/web/app/api/cron/disposable-emails/route.ts",
            """
            export const dynamic = "force-dynamic";
            export async function POST() {
              const res = await fetch("https://example.com/list.txt");
              return Response.json({ ok: res.ok });
            }
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("next_server_fetch_cache_scope", row["signals"])
        self.assertIn("dynamic_no_static_cache", row["signals"])
        self.assertNotIn("fetch_without_explicit_cache_policy", row["risks"])

    def test_read_only_route_factory_is_not_treated_as_mutation_surface(self):
        row = analyze_next_boundary_file(
            "APP",
            "src/app/api/search/route.ts",
            """
            import { createFromSource } from '@/lib/source';
            const source = { docs: true };
            export const { GET } = createFromSource(source);
            """,
        )

        self.assertIsNotNone(row)
        self.assertIn("route_handler", row["signals"])
        self.assertIn("read_only_route_helper", row["signals"])
        self.assertNotIn("mutation_surface", row["signals"])
        self.assertNotIn("route_handler_without_explicit_env_boundary", row["risks"])


class DeadCodeDynamicImportTests(unittest.TestCase):
    def test_unreferenced_file_export_requires_manual_intent_decision(self):
        profile = DeadCodeDetector._actionability_profile("HIGH", "unreferenced_file_export")

        self.assertEqual(profile["unusedness_confidence"], "high")
        self.assertEqual(profile["remediation_confidence"], "unknown")
        self.assertEqual(profile["level"], "manual_intent_decision")
        self.assertTrue(profile["intent_decision_required"])
        self.assertFalse(profile["mutation_proposed"])
        self.assertEqual(
            profile["allowed_outcomes"],
            ["delete", "complete_integration", "retain_contract", "unknown"],
        )

    def test_deprecated_empty_export_remains_conditional_control_case(self):
        profile = DeadCodeDetector._actionability_profile("HIGH", "deprecated_empty_export_surface")

        self.assertEqual(profile["level"], "actionable")
        self.assertEqual(profile["remediation_confidence"], "conditional")
        self.assertTrue(profile["intent_decision_required"])
        self.assertFalse(profile["mutation_proposed"])
        self.assertNotIn("complete_integration", profile["allowed_outcomes"])

    def test_dynamic_import_destructuring_consumes_named_exports(self):
        imports = DeadCodeDetector._parse_dynamic_named_imports(
            """
            afterEach(async () => {
              const { resetJobsWorkerRegistrationForTests } = await import("./instrumentation-jobs");
              await import("./flags").then(({ getPostHogClientFeatureFlag }) => getPostHogClientFeatureFlag());
            });
            """
        )

        self.assertIn(("./instrumentation-jobs", ["resetJobsWorkerRegistrationForTests"]), imports)
        self.assertIn(("./flags", ["getPostHogClientFeatureFlag"]), imports)

    def test_dynamic_import_destructuring_resolves_extensionless_relative_target(self):
        target = DeadCodeDetector._resolve_dynamic_import_target(
            "apps/web/instrumentation-jobs.test.ts",
            "./instrumentation-jobs",
            {"apps/web/instrumentation-jobs.ts"},
        )

        self.assertEqual(target, "apps/web/instrumentation-jobs.ts")

    def test_codemod_actual_expected_fixture_paths_are_test_support_surfaces(self):
        self.assertTrue(
            DeadCodeDetector._is_test_support_surface(
                "packages/mui-codemod/src/v5.0.0/adapter-v4.test/core-import.actual.js"
            )
        )
        self.assertTrue(
            DeadCodeDetector._is_test_support_surface(
                "packages/mui-codemod/src/v5.0.0/adapter-v4.test/core-import.expected.js"
            )
        )

    def test_storybook_mockdata_and_test_files_are_test_support_surfaces(self):
        self.assertTrue(
            DeadCodeDetector._is_test_support_surface(
                "code/core/src/core-server/utils/__mockdata__/src/stories.ts"
            )
        )
        self.assertTrue(
            DeadCodeDetector._is_test_support_surface(
                "code/core/src/core-server/utils/save-story/duplicate-story-with-new-name.test.ts"
            )
        )

    def test_examples_and_demo_paths_are_reference_surfaces(self):
        self.assertTrue(
            DeadCodeDetector._is_reference_example_surface(
                "examples/react/eslint-plugin-demo/src/allowlist-demo.tsx"
            )
        )
        self.assertTrue(
            DeadCodeDetector._is_reference_example_surface(
                "examples/lit/ssr/src/api.ts"
            )
        )
        self.assertFalse(
            DeadCodeDetector._is_reference_example_surface(
                "packages/react-query/src/index.ts"
            )
        )

    def test_framework_runtime_contract_surfaces_are_registry_matched(self):
        detector = DeadCodeDetector()

        migration = detector._match_contract_registry(
            "MAIN",
            "packages/core/src/migrations/Migration20250805184935.ts",
            "Migration20250805184935",
        )
        fixture = detector._match_contract_registry(
            "MAIN",
            "integration-tests/http/__fixtures__/feature-flag/src/workflows/test-workflow.ts",
            "testWorkflow",
        )
        workflow = detector._match_contract_registry(
            "MAIN",
            "packages/core/core-flows/src/order/workflows/create-order.ts",
            "createOrdersWorkflow",
        )
        step = detector._match_contract_registry(
            "MAIN",
            "packages/core/core-flows/src/cart/steps/retrieve-cart.ts",
            "retrieveCartStep",
        )

        self.assertEqual(migration["rule_id"], "migration_contracts")
        self.assertEqual(fixture["rule_id"], "test_fixture_contracts")
        self.assertEqual(workflow["rule_id"], "workflow_step_contracts")
        self.assertEqual(step["rule_id"], "workflow_step_contracts")

    def test_locale_exports_with_three_letter_region_are_registry_matched(self):
        detector = DeadCodeDetector()

        locale = detector._match_contract_registry(
            "MAIN",
            "packages/mui-material/src/locale/kuCKB.ts",
            "kuCKB",
        )

        self.assertIsNotNone(locale)
        self.assertEqual(locale["rule_id"], "i18n_locale_code_contracts")

    def test_generated_api_client_surfaces_are_registry_matched(self):
        detector = DeadCodeDetector()

        api_client = detector._match_contract_registry(
            "MAIN",
            "frontend/src/generated/core/api.ts",
            "dashboardTemplatesRetrieve",
        )
        task_client = detector._match_contract_registry(
            "MAIN",
            "products/tasks/frontend/generated/api.ts",
            "tasksCreate",
        )

        self.assertEqual(api_client["rule_id"], "generated_api_client_contracts")
        self.assertEqual(task_client["rule_id"], "generated_api_client_contracts")

    def test_package_types_versions_and_side_effects_are_public_surfaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package_dir = root / "packages" / "ui"
            package_dir.mkdir(parents=True)
            (package_dir / "package.json").write_text(
                json.dumps(
                    {
                        "name": "@acme/ui",
                        "typesVersions": {"*": {"theme": ["src/themeAugmentation/index.ts"]}},
                        "sideEffects": ["src/register-theme-side-effects.ts"],
                    }
                ),
                encoding="utf-8",
            )

            detector = DeadCodeDetector()
            detector.projects = {"MAIN": root}
            public_contracts = build_package_public_contracts(root, [package_dir / "package.json"])
            project_data = {"public_contracts": public_contracts}

            self.assertTrue(
                detector._is_package_public_export_surface(
                    "MAIN",
                    "packages/ui/src/themeAugmentation/index.ts",
                    project_data,
                )
            )
            self.assertTrue(
                detector._is_package_public_export_surface(
                    "MAIN",
                    "packages/ui/src/register-theme-side-effects.ts",
                    project_data,
                )
            )

    def test_star_barrel_chain_from_package_entry_marks_deep_exports_public(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text(json.dumps({"exports": "./src/index.ts"}), encoding="utf-8")

            detector = DeadCodeDetector()
            detector.projects = {"MAIN": root}
            files = {
                "src/index.ts": {
                    "exports": [
                        {
                            "name": "proxy:./components",
                            "type": "ProxyExport",
                            "moduleSpecifier": "./components",
                            "exportedNames": [],
                        }
                    ]
                },
                "src/components/index.ts": {
                    "exports": [
                        {
                            "name": "proxy:./Button",
                            "type": "ProxyExport",
                            "moduleSpecifier": "./Button",
                            "exportedNames": [],
                        }
                    ]
                },
                "src/components/Button.ts": {
                    "exports": [{"name": "buttonUtils", "type": "Function"}],
                    "symbols": [{"name": "buttonUtils", "exported": True, "type": "Function"}],
                },
            }

            public_contracts = build_package_public_contracts(root, [root / "package.json"])
            public_files, public_symbols = detector._public_export_chain_surfaces("MAIN", files, public_contracts)

            self.assertIn("src/components/index.ts", public_files)
            self.assertIn("src/components/Button.ts", public_files)
            self.assertEqual(public_symbols, set())

    def test_named_reexport_chain_from_package_entry_marks_symbol_public(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text(json.dumps({"exports": "./src/index.ts"}), encoding="utf-8")

            detector = DeadCodeDetector()
            detector.projects = {"MAIN": root}
            files = {
                "src/index.ts": {
                    "exports": [
                        {
                            "name": "buttonUtils",
                            "type": "ReExportedSymbol",
                            "moduleSpecifier": "./components/Button",
                            "exportedNames": ["buttonUtils"],
                            "dependencies": ["buttonUtils"],
                        }
                    ]
                },
                "src/components/Button.ts": {
                    "exports": [
                        {"name": "buttonUtils", "type": "Function"},
                        {"name": "privateHelper", "type": "Function"},
                    ],
                    "symbols": [
                        {"name": "buttonUtils", "exported": True, "type": "Function"},
                        {"name": "privateHelper", "exported": True, "type": "Function"},
                    ],
                },
            }

            public_contracts = build_package_public_contracts(root, [root / "package.json"])
            public_files, public_symbols = detector._public_export_chain_surfaces("MAIN", files, public_contracts)

            self.assertNotIn("src/components/Button.ts", public_files)
            self.assertIn(("src/components/Button.ts", "buttonUtils"), public_symbols)
            self.assertNotIn(("src/components/Button.ts", "privateHelper"), public_symbols)

    def test_active_public_export_chain_is_excluded_from_dead_code_classification(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "src" / "components" / "Button.ts"
            source.parent.mkdir(parents=True)
            source.write_text("export function buttonUtils() { return true; }\n", encoding="utf-8")

            detector = DeadCodeDetector()
            detector.projects = {"MAIN": root}
            project_data = {
                "files": {
                    "src/components/Button.ts": {
                        "exports": [{"name": "buttonUtils", "type": "Function"}],
                        "symbols": [{"name": "buttonUtils", "exported": True, "type": "Function"}],
                    }
                }
            }

            analysis = detector._analyze_project_exports(
                project="MAIN",
                project_data=project_data,
                imported_files_for_project=set(),
                namespace_imported_files_for_project=set(),
                propagated_consumed_for_project=set(),
                public_export_chain_files={"src/components/Button.ts"},
                public_export_chain_symbols=set(),
                all_imported_names=set(),
                symbol_usage_files_for_project={},
            )

            self.assertEqual(analysis["dead"], [])
            self.assertEqual(analysis["compatibility_exclusions"][0]["reason"], "active_public_export_chain")

    def test_typescript_declaration_and_augmentation_surfaces_are_registry_matched(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            declaration = root / "code" / "frameworks" / "nextjs" / "src" / "globals.d.ts"
            augmentation = root / "packages" / "mui-material" / "src" / "themeCssVarsAugmentation" / "index.ts"
            declaration.parent.mkdir(parents=True)
            augmentation.parent.mkdir(parents=True)
            declaration.write_text("declare module 'next/dist/compiled';\nexport interface NextCompiledShim {}", encoding="utf-8")
            augmentation.write_text(
                "declare module '@mui/material/styles' { interface ThemeCssVarsOverrides {} }\nexport interface ThemeCssVarsOverrides {}",
                encoding="utf-8",
            )

            detector = DeadCodeDetector()
            detector.projects = {"MAIN": root}

            declaration_match = detector._match_contract_registry(
                "MAIN",
                "code/frameworks/nextjs/src/globals.d.ts",
                "NextCompiledShim",
            )
            augmentation_match = detector._match_contract_registry(
                "MAIN",
                "packages/mui-material/src/themeCssVarsAugmentation/index.ts",
                "ThemeCssVarsOverrides",
            )

            self.assertEqual(declaration_match["rule_id"], "typescript_declaration_contracts")
            self.assertEqual(augmentation_match["rule_id"], "typescript_augmentation_contracts")


class A11yI18nContractAnalyzerTests(unittest.TestCase):
    def test_dialog_without_focus_contract_is_flagged(self):
        row = analyze_a11y_i18n_file(
            "APP",
            "src/components/PublishModal.tsx",
            "export function PublishModal(){ return <Dialog><button>Publish</button><button>Cancel</button><button>More Options</button></Dialog>; }",
        )

        self.assertIsNotNone(row)
        self.assertIn("dialog_without_visible_focus_contract", row["risks"])
        self.assertIn("interactive_surface_without_aria_contract", row["risks"])

    def test_role_dialog_without_focus_contract_is_flagged(self):
        row = analyze_a11y_i18n_file(
            "APP",
            "src/components/HeadlessDialog.tsx",
            "export function HeadlessDialog(){ return <section role='dialog' aria-modal='true'><button>Close</button></section>; }",
        )

        self.assertIsNotNone(row)
        self.assertIn("dialog_or_overlay", row["signals"])
        self.assertIn("dialog_without_visible_focus_contract", row["risks"])

    def test_aria_relation_with_spaced_id_attribute_is_not_flagged(self):
        row = analyze_a11y_i18n_file(
            "APP",
            "src/components/Field.tsx",
            "export function Field(){ return <><label id = 'name-label'>Name</label><input aria-labelledby='name-label' /></>; }",
        )

        self.assertIsNotNone(row)
        self.assertIn("aria_relation_contract", row["signals"])
        self.assertNotIn("aria_relation_without_visible_id_contract", row["risks"])

    def test_raw_image_without_alt_or_loading_contract_is_flagged(self):
        row = analyze_a11y_i18n_file(
            "APP",
            "src/components/Hero.tsx",
            "export function Hero(){ return <section><img src='/hero.png' /></section>; }",
        )

        self.assertIsNotNone(row)
        self.assertIn("image_media_contract", row["signals"])
        self.assertIn("image_without_alt_contract", row["risks"])
        self.assertIn("raw_img_without_loading_or_optimization_contract", row["risks"])

    def test_next_image_with_alt_is_not_flagged_as_raw_image_risk(self):
        row = analyze_a11y_i18n_file(
            "APP",
            "src/components/Hero.tsx",
            "import Image from 'next/image'; export function Hero(){ return <Image src='/hero.png' alt='Hero' priority sizes='100vw' />; }",
        )

        self.assertIsNotNone(row)
        self.assertIn("next_image_optimization", row["signals"])
        self.assertNotIn("image_without_alt_contract", row["risks"])
        self.assertNotIn("raw_img_without_loading_or_optimization_contract", row["risks"])

    def test_icon_only_control_without_accessible_name_is_flagged(self):
        row = analyze_a11y_i18n_file(
            "APP",
            "src/components/IconButton.tsx",
            "export function IconButton(){ return <button><TrashIcon /></button>; }",
        )

        self.assertIsNotNone(row)
        self.assertIn("icon_control_contract", row["signals"])
        self.assertIn("icon_control_without_accessible_name", row["risks"])

    def test_icon_only_control_with_aria_label_is_not_flagged(self):
        row = analyze_a11y_i18n_file(
            "APP",
            "src/components/IconButton.tsx",
            "export function IconButton(){ return <button aria-label='Delete'><TrashIcon /></button>; }",
        )

        self.assertIsNotNone(row)
        self.assertNotIn("icon_control_without_accessible_name", row["risks"])

    def test_blank_target_link_without_noopener_is_flagged(self):
        row = analyze_a11y_i18n_file(
            "APP",
            "src/components/ExternalLink.tsx",
            "export function ExternalLink(){ return <a href='https://example.com' target='_blank'>Open</a>; }",
        )

        self.assertIsNotNone(row)
        self.assertIn("external_link_security_contract", row["signals"])
        self.assertIn("blank_target_link_without_noopener_contract", row["risks"])

    def test_blank_target_link_with_safe_rel_is_not_flagged(self):
        row = analyze_a11y_i18n_file(
            "APP",
            "src/components/ExternalLink.tsx",
            "export function ExternalLink(){ return <a href='https://example.com' target='_blank' rel='noopener noreferrer'>Open</a>; }",
        )

        self.assertIsNotNone(row)
        self.assertNotIn("blank_target_link_without_noopener_contract", row["risks"])

    def test_dynamic_i18n_key_without_fallback_is_flagged(self):
        row = analyze_a11y_i18n_file(
            "APP",
            "src/components/Status.tsx",
            "export function Status({ status }){ return <p>{t(`status.${status}`)}</p>; }",
        )

        self.assertIsNotNone(row)
        self.assertIn("i18n_dynamic_key_contract", row["signals"])
        self.assertIn("i18n_dynamic_key_without_fallback_contract", row["risks"])

    def test_dynamic_i18n_key_with_default_value_is_not_flagged(self):
        row = analyze_a11y_i18n_file(
            "APP",
            "src/components/Status.tsx",
            "export function Status({ keyName }){ return <p>{t(keyName, { defaultValue: 'Pending' })}</p>; }",
        )

        self.assertIsNotNone(row)
        self.assertIn("i18n_fallback_contract", row["signals"])
        self.assertNotIn("i18n_dynamic_key_without_fallback_contract", row["risks"])


class ReactEcosystemAnalyzerTests(unittest.TestCase):
    def test_render_hook_state_and_query_risks_are_separated(self):
        row = analyze_react_ecosystem_file(
            "APP",
            "src/app/projects/[projectId]/page.tsx",
            """
            'use client';
            export function ProjectPage({ project }) {
              const [items, setItems] = useState(project.items.map(x => x));
              const [open, setOpen] = useState(false);
              const [draft, setDraft] = useState('');
              const [filter, setFilter] = useState('');
              const [selected, setSelected] = useState(null);
              const mutation = useMutation({ mutationFn: saveProject });
              useEffect(() => { fetch('/api/project/' + project.id).then(r => setItems([])); }, []);
              return <ProjectProvider><ThemeProvider><Panel onSave={() => saveProject(draft)} opts={{ open }} /></ThemeProvider></ProjectProvider>;
            }
            """,
        )

        self.assertIsNotNone(row)
        dimensions = {item["dimension"] for item in row["findings"]}
        self.assertIn("react_render_risk", dimensions)
        self.assertIn("hook_contract", dimensions)
        self.assertIn("state_ownership", dimensions)
        self.assertIn("data_cache_flow", dimensions)
        self.assertIn("concurrent_ux_contract", dimensions)

    def test_form_design_and_a11y_risks_are_detected(self):
        row = analyze_react_ecosystem_file(
            "APP",
            "src/components/ProfileForm.tsx",
            """
            export function ProfileForm() {
              const form = useForm();
              return <form action={saveProfile}>
                <div onClick={() => form.setValue('name', 'Ada')}>Pick</div>
                <input className="bg-[#fff] px-[13px]" />
                {form.formState.errors.name && <span>Error</span>}
              </form>;
            }
            """,
        )

        self.assertIsNotNone(row)
        dimensions = {item["dimension"] for item in row["findings"]}
        self.assertIn("form_validation", dimensions)
        self.assertIn("design_system_drift", dimensions)
        self.assertIn("accessibility_semantics", dimensions)
        self.assertIn("concurrent_ux_contract", dimensions)

    def test_form_control_contract_detects_controlled_uncontrolled_risks(self):
        row = analyze_react_ecosystem_file(
            "APP",
            "src/components/ProfileForm.tsx",
            """
            export function ProfileForm({ name, enabled }) {
              return <form>
                <input value={name} defaultValue="Ada" />
                <input type="checkbox" checked={enabled} defaultChecked />
              </form>;
            }
            """,
        )

        self.assertIsNotNone(row)
        control = [item for item in row["findings"] if item["dimension"] == "form_control_contract"]
        self.assertEqual(len(control), 1)
        self.assertEqual(control[0]["risk"], "controlled_uncontrolled_input_contract_risk")

    def test_form_control_contract_accepts_explicit_control_ownership(self):
        row = analyze_react_ecosystem_file(
            "APP",
            "src/components/ProfileForm.tsx",
            """
            export function ProfileForm({ name, setName }) {
              return <form>
                <input value={name} onChange={event => setName(event.target.value)} />
                <input defaultValue="Ada" />
              </form>;
            }
            """,
        )

        self.assertIsNotNone(row)
        dimensions = {item["dimension"] for item in row["findings"]}
        self.assertNotIn("form_control_contract", dimensions)

    def test_ecosystem_design_system_reference_surface_keeps_signal_but_lowers_actionability(self):
        row = analyze_react_ecosystem_file(
            "APP",
            "docs/data/material/components/buttons/LoadingIconButton.tsx",
            """
            export function LoadingIconButton() {
              return <button className="bg-[#fff] px-[13px]" style={{ color: 'red' }}>Demo</button>;
            }
            """,
        )

        self.assertIsNotNone(row)
        design = [item for item in row["findings"] if item["dimension"] == "design_system_drift"]
        self.assertEqual(len(design), 1)
        self.assertEqual(design[0]["source_context"], "docs_demo")
        self.assertEqual(design[0]["actionability"], "reference_only")
        self.assertEqual(design[0]["production_relevance"], "reference_or_fixture_surface")

    def test_ecosystem_design_system_production_surface_stays_actionable(self):
        row = analyze_react_ecosystem_file(
            "APP",
            "src/components/LoadingIconButton.tsx",
            """
            export function LoadingIconButton() {
              return <button className="bg-[#fff] px-[13px]" style={{ color: 'red' }}>Demo</button>;
            }
            """,
        )

        self.assertIsNotNone(row)
        design = [item for item in row["findings"] if item["dimension"] == "design_system_drift"]
        self.assertEqual(len(design), 1)
        self.assertEqual(design[0]["source_context"], "production_source")
        self.assertEqual(design[0]["actionability"], "production_actionable")

    def test_form_validation_uses_atlas_resolver_evidence(self):
        row = analyze_react_ecosystem_file(
            "APP",
            "src/components/ProfileForm.tsx",
            """
            export function ProfileForm() {
              const form = useForm();
              return <form onSubmit={form.handleSubmit(saveProfile)}>
                <input {...form.register('name')} />
              </form>;
            }
            """,
            {"features": ["ReactForm", "FormResolver", "ValidationSchema"]},
        )

        self.assertIsNotNone(row)
        form_findings = [item for item in row["findings"] if item["dimension"] == "form_validation"]
        self.assertFalse(any("form hook without resolver/schema contract" in item["evidence"] for item in form_findings))

    def test_form_error_state_from_atlas_requires_accessible_mapping(self):
        row = analyze_react_ecosystem_file(
            "APP",
            "src/components/ProfileForm.tsx",
            """
            export function ProfileForm() {
              return <form><input name="email" /></form>;
            }
            """,
            {"features": ["ReactForm", "FormErrorState", "FormResolver"]},
        )

        self.assertIsNotNone(row)
        form_findings = [item for item in row["findings"] if item["dimension"] == "form_validation"]
        self.assertTrue(any("form errors without visible aria error mapping" in item["evidence"] for item in form_findings))

    def test_concurrent_ux_contract_accepts_action_state_and_pending_feedback(self):
        row = analyze_react_ecosystem_file(
            "APP",
            "src/components/ProfileForm.tsx",
            """
            export function ProfileForm() {
              const [state, action, isPending] = useActionState(saveProfile, {});
              return <form action={action} aria-busy={isPending}>
                <button disabled={isPending}>Save</button>
              </form>;
            }
            """,
        )

        self.assertIsNotNone(row)
        dimensions = {item["dimension"] for item in row["findings"]}
        self.assertNotIn("concurrent_ux_contract", dimensions)

    def test_list_identity_contract_detects_missing_and_index_keys(self):
        row = analyze_react_ecosystem_file(
            "APP",
            "src/components/ProjectList.tsx",
            """
            export function ProjectList({ projects, tasks }) {
              return <section>
                {projects.map((project, index) => <ProjectCard key={index} project={project} />)}
                {tasks.map(task => <TaskRow task={task} />)}
              </section>;
            }
            """,
        )

        self.assertIsNotNone(row)
        list_findings = [item for item in row["findings"] if item["dimension"] == "list_identity_contract"]
        self.assertEqual(len(list_findings), 1)
        self.assertIn("unstable_or_unbounded_list_render_contract", list_findings[0]["risk"])

    def test_list_identity_contract_accepts_stable_virtualized_keys(self):
        row = analyze_react_ecosystem_file(
            "APP",
            "src/components/ProjectList.tsx",
            """
            import { FixedSizeList } from 'react-window';
            export function ProjectList({ projects }) {
              return <FixedSizeList>
                {projects.map(project => <ProjectCard key={project.id} project={project} />)}
              </FixedSizeList>;
            }
            """,
        )

        self.assertIsNotNone(row)
        dimensions = {item["dimension"] for item in row["findings"]}
        self.assertNotIn("list_identity_contract", dimensions)

    def test_effect_cleanup_contract_detects_resource_without_cleanup(self):
        row = analyze_react_ecosystem_file(
            "APP",
            "src/components/Clock.tsx",
            """
            export function Clock() {
              useEffect(() => {
                setInterval(() => console.log('tick'), 1000);
                window.addEventListener('resize', () => console.log(window.innerWidth));
              }, []);
              return <div>Clock</div>;
            }
            """,
        )

        self.assertIsNotNone(row)
        cleanup = [item for item in row["findings"] if item["dimension"] == "effect_cleanup_contract"]
        self.assertEqual(len(cleanup), 1)
        self.assertEqual(cleanup[0]["risk"], "subscription_or_timer_without_cleanup_contract")

    def test_effect_cleanup_contract_accepts_explicit_cleanup(self):
        row = analyze_react_ecosystem_file(
            "APP",
            "src/components/Clock.tsx",
            """
            export function Clock() {
              useEffect(() => {
                const id = setInterval(() => console.log('tick'), 1000);
                const onResize = () => console.log(window.innerWidth);
                window.addEventListener('resize', onResize);
                return () => {
                  clearInterval(id);
                  window.removeEventListener('resize', onResize);
                };
              }, []);
              return <div>Clock</div>;
            }
            """,
        )

        self.assertIsNotNone(row)
        dimensions = {item["dimension"] for item in row["findings"]}
        self.assertNotIn("effect_cleanup_contract", dimensions)

    def test_lazy_boundary_contract_detects_missing_fallback_and_error_boundary(self):
        row = analyze_react_ecosystem_file(
            "APP",
            "src/components/EditorShell.tsx",
            """
            import dynamic from 'next/dynamic';
            const HeavyEditor = dynamic(() => import('./HeavyEditor'));
            export function EditorShell() {
              return <HeavyEditor />;
            }
            """,
        )

        self.assertIsNotNone(row)
        lazy = [item for item in row["findings"] if item["dimension"] == "lazy_boundary_contract"]
        self.assertEqual(len(lazy), 1)
        self.assertEqual(lazy[0]["risk"], "lazy_component_without_loading_or_error_boundary")

    def test_lazy_boundary_contract_accepts_fallback_and_error_boundary(self):
        row = analyze_react_ecosystem_file(
            "APP",
            "src/components/EditorShell.tsx",
            """
            const HeavyEditor = React.lazy(() => import('./HeavyEditor'));
            export function EditorShell() {
              return <ErrorBoundary>
                <Suspense fallback={<Spinner />}>
                  <HeavyEditor />
                </Suspense>
              </ErrorBoundary>;
            }
            """,
        )

        self.assertIsNotNone(row)
        dimensions = {item["dimension"] for item in row["findings"]}
        self.assertNotIn("lazy_boundary_contract", dimensions)

    def test_context_value_contract_detects_inline_provider_value(self):
        row = analyze_react_ecosystem_file(
            "APP",
            "src/components/ProjectProvider.tsx",
            """
            export function ProjectProvider({ children, project, saveProject }) {
              return <ProjectContext.Provider value={{ project, saveProject, selected: project.id }}>
                {children}
              </ProjectContext.Provider>;
            }
            """,
        )

        self.assertIsNotNone(row)
        context = [item for item in row["findings"] if item["dimension"] == "context_value_contract"]
        self.assertEqual(len(context), 1)
        self.assertEqual(context[0]["risk"], "provider_topology_or_value_stability_risk")

    def test_context_value_contract_accepts_named_provider_value(self):
        row = analyze_react_ecosystem_file(
            "APP",
            "src/components/ProjectProvider.tsx",
            """
            export function ProjectProvider({ children, project, saveProject }) {
              const value = useMemo(() => ({ project, saveProject }), [project, saveProject]);
              return <ProjectContext.Provider value={value}>
                {children}
              </ProjectContext.Provider>;
            }
            """,
        )

        self.assertIsNotNone(row)
        dimensions = {item["dimension"] for item in row["findings"]}
        self.assertNotIn("context_value_contract", dimensions)

    def test_memo_dependency_contract_detects_stale_dependency_risk(self):
        row = analyze_react_ecosystem_file(
            "APP",
            "src/components/ProjectPanel.tsx",
            """
            export function ProjectPanel({ project, items }) {
              const visible = useMemo(() => items.filter(item => item.projectId === project.id), []);
              const save = useCallback(() => saveProject(project.id));
              return <ProjectList items={visible} onSave={save} />;
            }
            """,
        )

        self.assertIsNotNone(row)
        memo = [item for item in row["findings"] if item["dimension"] == "memo_dependency_contract"]
        self.assertEqual(len(memo), 1)
        self.assertEqual(memo[0]["risk"], "memo_or_callback_dependency_contract_risk")

    def test_memo_dependency_contract_accepts_explicit_dependencies(self):
        row = analyze_react_ecosystem_file(
            "APP",
            "src/components/ProjectPanel.tsx",
            """
            export function ProjectPanel({ project, items }) {
              const visible = useMemo(() => items.filter(item => item.projectId === project.id), [items, project.id]);
              const save = useCallback(() => saveProject(project.id), [project.id]);
              return <ProjectList items={visible} onSave={save} />;
            }
            """,
        )

        self.assertIsNotNone(row)
        dimensions = {item["dimension"] for item in row["findings"]}
        self.assertNotIn("memo_dependency_contract", dimensions)

    def test_ref_imperative_contract_detects_unstable_imperative_surface(self):
        row = analyze_react_ecosystem_file(
            "APP",
            "src/components/EditorHandle.tsx",
            """
            export const EditorHandle = forwardRef(function EditorHandle({ project, onSave }, ref) {
              useImperativeHandle(ref, () => ({
                save: () => onSave(project.id),
              }), []);
              return <input ref={node => node && node.focus()} />;
            });
            """,
        )

        self.assertIsNotNone(row)
        refs = [item for item in row["findings"] if item["dimension"] == "ref_imperative_contract"]
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0]["risk"], "unstable_imperative_ref_contract")
        self.assertEqual(refs[0]["evidence_scope"], "file_level_ref_contract")
        self.assertEqual(refs[0]["evidence_spans"][0]["scope"], "file_level_ref_contract")

    def test_ref_imperative_contract_accepts_stable_dependencies_and_ref_callback(self):
        row = analyze_react_ecosystem_file(
            "APP",
            "src/components/EditorHandle.tsx",
            """
            export const EditorHandle = forwardRef(function EditorHandle({ project, onSave }, ref) {
              useImperativeHandle(ref, () => ({
                save: () => onSave(project.id),
              }), [project.id, onSave]);
              const inputRef = useCallback(node => node && node.focus(), []);
              return <input ref={inputRef} />;
            });
            """,
        )

        self.assertIsNotNone(row)
        dimensions = {item["dimension"] for item in row["findings"]}
        self.assertNotIn("ref_imperative_contract", dimensions)

    def test_nested_component_contract_detects_render_scoped_component_definition(self):
        row = analyze_react_ecosystem_file(
            "APP",
            "src/components/ProjectPanel.tsx",
            """
            export function ProjectPanel({ project }) {
              function ProjectBadge() {
                return <span>{project.name}</span>;
              }
              const ProjectActions = () => <button>Save</button>;
              return <ProjectBadge />;
            }
            """,
        )

        self.assertIsNotNone(row)
        nested = [item for item in row["findings"] if item["dimension"] == "nested_component_contract"]
        self.assertEqual(len(nested), 1)
        self.assertEqual(nested[0]["risk"], "render_scoped_component_definition_contract")

    def test_nested_component_contract_accepts_module_scoped_components(self):
        row = analyze_react_ecosystem_file(
            "APP",
            "src/components/ProjectPanel.tsx",
            """
            function ProjectBadge({ project }) {
              return <span>{project.name}</span>;
            }
            const ProjectActions = () => <button>Save</button>;
            export function ProjectPanel({ project }) {
              return <ProjectBadge project={project} />;
            }
            """,
        )

        self.assertIsNotNone(row)
        dimensions = {item["dimension"] for item in row["findings"]}
        self.assertNotIn("nested_component_contract", dimensions)

    def test_async_event_contract_detects_missing_error_and_pending_feedback(self):
        row = analyze_react_ecosystem_file(
            "APP",
            "src/components/PublishButton.tsx",
            """
            export function PublishButton({ publish }) {
              return <button onClick={async () => publish()}>Publish</button>;
            }
            """,
        )

        self.assertIsNotNone(row)
        async_events = [item for item in row["findings"] if item["dimension"] == "async_event_contract"]
        self.assertEqual(len(async_events), 1)
        self.assertEqual(async_events[0]["risk"], "async_user_event_without_error_or_pending_contract")

    def test_async_event_contract_accepts_error_and_pending_feedback(self):
        row = analyze_react_ecosystem_file(
            "APP",
            "src/components/PublishButton.tsx",
            """
            export function PublishButton({ publish }) {
              const [isPending, setPending] = useState(false);
              return <button disabled={isPending} aria-busy={isPending} onClick={async () => {
                try {
                  setPending(true);
                  await publish();
                } catch (error) {
                  toast.error(String(error));
                } finally {
                  setPending(false);
                }
              }}>Publish</button>;
            }
            """,
        )

        self.assertIsNotNone(row)
        dimensions = {item["dimension"] for item in row["findings"]}
        self.assertNotIn("async_event_contract", dimensions)


class ReactRuntimeIntelligenceTests(unittest.TestCase):
    def test_client_bundle_server_import_and_compiler_risks_are_detected(self):
        row = analyze_runtime_intelligence_file(
            "APP",
            "src/app/editor/page.tsx",
            """
            'use client';
            import fs from 'fs';
            import { EditorContent } from '@tiptap/react';
            export function EditorPage(props) {
              props.count = 1;
              const a = useMemo(() => props.count, [props.count]);
              const b = useMemo(() => props.count + 1, [props.count]);
              const c = useMemo(() => props.count + 2, [props.count]);
              const d = useMemo(() => props.count + 3, [props.count]);
              const e = useMemo(() => props.count + 4, [props.count]);
              const f = useMemo(() => props.count + 5, [props.count]);
              return <EditorContent editor={null} />;
            }
            """,
        )

        self.assertIsNotNone(row)
        dimensions = {item["dimension"] for item in row["findings"]}
        risks = {item["risk"] for item in row["findings"]}
        self.assertIn("bundle_boundary", dimensions)
        self.assertIn("react_compiler_readiness", dimensions)
        self.assertIn("client_boundary_imports_server_only_module", risks)
        self.assertIn("client_boundary_pulls_heavy_dependency", risks)
        server_only = next(item for item in row["findings"] if item["risk"] == "client_boundary_imports_server_only_module")
        self.assertEqual(server_only["calibration_lane"], "act_now")
        self.assertEqual(server_only["false_positive_risk"], "low")

    def test_route_auth_error_mutation_and_test_intent_are_detected(self):
        row = analyze_runtime_intelligence_file(
            "APP",
            "src/app/admin/page.tsx",
            """
            'use client';
            export function AdminPage() {
              const data = useQuery({ queryKey: ['admin'], queryFn: loadAdmin });
              const mutation = useMutation({ mutationFn: saveAdmin });
              return <Dialog><button onClick={() => mutation.mutate()}>Save</button><div className="p-1 m-1 text-sm bg-white rounded shadow border flex gap-1 items-center justify-center w-full h-full overflow-auto">Admin</div></Dialog>;
            }
            """,
            runtime_routes={"src/app/admin/page.tsx"},
        )

        self.assertIsNotNone(row)
        dimensions = {item["dimension"] for item in row["findings"]}
        self.assertIn("auth_permission_boundary", dimensions)
        self.assertIn("data_mutation_blast_radius", dimensions)
        self.assertIn("error_recovery_map", dimensions)
        self.assertIn("accessibility_journey", dimensions)
        self.assertIn("test_coverage_intent", dimensions)

    def test_runtime_css_token_intelligence_reads_configured_class_composition_helpers(self):
        row = analyze_runtime_intelligence_file(
            "APP",
            "src/components/AdminToolbar.tsx",
            """
            'use client';
            import { cn } from '@/lib/cn';
            export function AdminToolbar({ active }) {
              return <section className={cn(
                'p-4 m-2 text-sm bg-white border rounded shadow flex gap-2 items-center justify-center w-full h-full overflow-auto ring-1 ring-slate-200 hover:bg-slate-50 focus:outline-none focus:ring-2 transition duration-200 ease-out min-h-screen max-w-4xl mx-auto grid grid-cols-2 col-span-1 row-span-1 px-6 py-8 min-w-0 shrink-0 basis-full',
                active && 'bg-primary text-white'
              )}>Admin</section>;
            }
            """,
        )

        self.assertIsNotNone(row)
        dimensions = {item["dimension"] for item in row["findings"]}
        self.assertIn("css_token_intelligence", dimensions)
        css = next(item for item in row["findings"] if item["dimension"] == "css_token_intelligence")
        self.assertEqual(css["evidence_scope"], "file_level_css_token_aggregate")

    def test_browser_permission_api_without_fallback_is_detected(self):
        row = analyze_runtime_intelligence_file(
            "APP",
            "src/components/ShareButton.tsx",
            """
            'use client';
            export function ShareButton() {
              return <button onClick={() => navigator.clipboard.writeText('copy')}>Copy</button>;
            }
            """,
        )

        self.assertIsNotNone(row)
        dimensions = {item["dimension"] for item in row["findings"]}
        self.assertIn("browser_permission_contract", dimensions)

    def test_browser_permission_api_with_fallback_is_not_flagged(self):
        row = analyze_runtime_intelligence_file(
            "APP",
            "src/components/ShareButton.tsx",
            """
            'use client';
            export function ShareButton() {
              return <button onClick={async () => {
                try {
                  await navigator.clipboard.writeText('copy');
                } catch (error) {
                  toast.error('Clipboard permission denied');
                }
              }}>Copy</button>;
            }
            """,
        )

        self.assertIsNotNone(row)
        dimensions = {item["dimension"] for item in row["findings"]}
        self.assertNotIn("browser_permission_contract", dimensions)

    def test_ecosystem_corroboration_promotes_probable_runtime_findings(self):
        runtime = [
            {
                "project": "APP",
                "file": "src/components/Card.tsx",
                "dimension": "css_token_intelligence",
                "risk": "responsive_or_theme_drift_risk",
                "risk_tier": "medium",
                "confidence": "probable",
                "score": 4,
                "evidence_kinds": ["static", "design_system"],
            }
        ]
        ecosystem = [{"project": "APP", "file": "src/components/Card.tsx", "dimension": "design_system_drift"}]

        calibrated = _calibrate_with_ecosystem(runtime, ecosystem)

        self.assertEqual(calibrated[0]["confidence"], "likely")
        self.assertEqual(calibrated[0]["calibration_lane"], "review_next")
        self.assertEqual(calibrated[0]["false_positive_risk"], "low")
        self.assertIn("react_ecosystem_corroboration", calibrated[0]["evidence_kinds"])

    def test_runtime_policy_is_loaded_from_config(self):
        policy = _load_runtime_policy(force=True)

        self.assertIn("react_runtime_policy.json", policy["policy_source"])
        self.assertIn("bundle_boundary", policy["actionable_dimensions"])
        self.assertIn("clsx", policy["class_composition_functions"])
        self.assertEqual(policy["calibration_lanes"]["act_now"]["min_confidence"], "likely")
        self.assertEqual(
            policy["false_positive_policy"]["dimension_overrides"]["test_coverage_intent"]["probable"],
            "high",
        )


class ReactFrontierIntelligenceTests(unittest.TestCase):
    def test_type_escape_at_first_line_carries_explicit_evidence_scope(self):
        row = analyze_frontier_file(
            "APP",
            "src/unsafe.ts",
            "export const unsafe: any = value;\n",
        )

        self.assertIsNotNone(row)
        finding = next(item for item in row["findings"] if item["risk"] == "type_contract_escape_or_any_leak")
        self.assertEqual(finding["line"], 1)
        self.assertEqual(finding["evidence_scope"], "exact_type_escape_pattern")
        self.assertEqual(finding["evidence_spans"][0]["scope"], "exact_type_escape_pattern")

    def test_typescript_runtime_output_is_ingested_into_managed_truth(self):
        result = type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        payload = {"meta": {"kind": "ts_diagnostics"}, "summary": {"total_diagnostics": 0}}
        with (
            patch.object(react_frontier_engine, "TS_COLLECTOR", Path(__file__)),
            patch.object(react_frontier_engine, "run_observed_subprocess", return_value=(result, 0.1)),
            patch.object(react_frontier_engine, "load_json_file", return_value=payload) as load_payload,
            patch.object(react_frontier_engine, "save_json_atomic") as save_payload,
        ):
            collected = react_frontier_engine._collect_ts_diagnostics()

        load_payload.assert_called_once_with(react_frontier_engine.TS_DIAGNOSTICS_PATH, {}, bypass_proxy=True)
        save_payload.assert_called_once_with(react_frontier_engine.TS_DIAGNOSTICS_PATH, collected)
        self.assertEqual(collected["collector_status"], "OK")

    def test_type_security_and_design_mining_findings_are_detected(self):
        row = analyze_frontier_file(
            "APP",
            "src/app/admin/page.tsx",
            """
            'use client';
            // @ts-ignore
            type AdminProps = { payload: any };
            export function AdminPage(props: AdminProps) {
              localStorage.setItem('token', props.payload.token);
              return <section className="p-4 m-2 text-sm bg-white border rounded shadow flex gap-2 items-center justify-center w-full h-full overflow-auto ring-1 ring-slate-200 hover:bg-slate-50 focus:outline-none focus:ring-2 transition duration-200 ease-out min-h-screen max-w-4xl mx-auto grid grid-cols-2 col-span-1 row-span-1 px-6 py-8">
                <div dangerouslySetInnerHTML={{ __html: props.payload.html }} />
              </section>;
            }
            """,
        )

        self.assertIsNotNone(row)
        dimensions = {item["dimension"] for item in row["findings"]}
        self.assertIn("typescript_type_aware", dimensions)
        self.assertIn("react_security", dimensions)
        self.assertIn("design_system_mining", dimensions)

    def test_hydration_determinism_risks_are_detected(self):
        row = analyze_frontier_file(
            "APP",
            "src/app/dashboard/page.tsx",
            """
            export function DashboardPage() {
              const id = Math.random();
              const now = new Date();
              const width = window.innerWidth;
              return <main>{id}{now.toISOString()}{width}</main>;
            }
            """,
        )

        self.assertIsNotNone(row)
        hydration = [item for item in row["findings"] if item["dimension"] == "hydration_determinism"]
        self.assertEqual(len(hydration), 1)
        self.assertEqual(hydration[0]["risk"], "possible_ssr_client_hydration_mismatch")

    def test_rsc_serialization_contract_risks_are_detected(self):
        row = analyze_frontier_file(
            "APP",
            "src/components/ClientCard.tsx",
            """
            'use client';
            type ClientCardProps = {
              createdAt: Date;
              items: Map<string, number>;
              onSave?: () => void;
            };
            export function ClientCard(props: ClientCardProps) {
              return <button onClick={props.onSave}>{props.createdAt.toISOString()}</button>;
            }
            """,
        )

        self.assertIsNotNone(row)
        serialization = [item for item in row["findings"] if item["dimension"] == "rsc_serialization_contract"]
        self.assertEqual(len(serialization), 1)
        self.assertEqual(serialization[0]["risk"], "client_component_props_may_not_be_rsc_serializable")
        self.assertEqual(serialization[0]["confidence"], "probable")

    def test_bundle_and_profiler_evidence_helpers_detect_hotspots(self):
        assets = _large_assets(
            {
                "assets": [{"name": "admin.js", "size": 410000}, {"name": "tiny.js", "size": 1000}],
                "chunks": [{"label": "vendor", "renderedLength": 320000}],
            }
        )
        hot = _hot_profiler_entries({"commits": [{"componentName": "Editor", "actualDuration": 32.5}, {"displayName": "Canvas", "treeBaseDuration": 21.25}]})

        self.assertEqual(assets, ["admin.js=410000B", "vendor=320000B"])
        self.assertEqual(hot, ["Editor=32.5ms", "Canvas=21.2ms"])

    def test_typescript_diagnostics_become_frontier_findings(self):
        findings = _ts_diagnostic_findings(
            {
                "projects": {
                    "APP": {
                        "diagnostics": [
                            {"file": "src/App.tsx", "code": "TS2322", "category": "Error"},
                            {"file": "src/App.tsx", "code": "TS2339", "category": "Error"},
                        ]
                    }
                }
            }
        )

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["dimension"], "typescript_compiler_diagnostics")
        self.assertEqual(findings[0]["confidence"], "likely")

    def test_frontier_evidence_readiness_reports_missing_runtime_artifacts(self):
        readiness = _evidence_readiness(
            ["APP", "VARIANT"],
            [{"project": "APP", "path": ".next/stats.json"}],
            [{"project": "APP", "path": "reports/react-profiler.json"}],
            {"collector_status": "OK", "summary": {"total_diagnostics": 2}},
        )

        self.assertEqual(readiness["summary"]["ready_projects"], 1)
        self.assertEqual(readiness["summary"]["needs_evidence_projects"], 1)
        self.assertEqual(readiness["projects"][1]["status"], "NEEDS_EVIDENCE")
        self.assertEqual(readiness["projects"][1]["missing"], ["bundle_stats", "react_profiler"])


class MergeDependencyPackagerTests(unittest.TestCase):
    def test_dependency_variants_apply_prefix_and_shared_type_repairs_together(self):
        self.assertIn(
            "src/types/project.ts",
            _dependency_variants("src/src/shared/types/project.ts"),
        )

    def test_package_walks_atlas_dependencies_and_separates_external_deps(self):
        package = build_dependency_package(
            {
                "name": "TranslationStudioPage",
                "source": "LINGUASCRIBE_MASTER",
                "target_path": "src/./04-translation/features/TranslationStudioPage.tsx",
                "source_contract_file": "LINGUASCRIBE_MASTER::src/app/translate/page.tsx",
                "risk_points": 30,
                "risk_tier": "medium",
                "recommended_gate": "browser_smoke_required",
                "dependency_closure_plan": {"required_contracts": ["i18n_keys"]},
                "smoke_plan": {"suggested_route": "/translation"},
            },
            {
                "LINGUASCRIBE_MASTER": {
                    "dependencies": {
                        "src/app/translate/page.tsx": [
                            "src/components/Panel.tsx",
                            "@radix-ui/react-dialog",
                        ],
                        "src/components/Panel.tsx": ["src/lib/missing.ts"],
                    }
                }
            },
        )

        self.assertEqual(package["entry_file"], "src/app/translate/page.tsx")
        self.assertIn("src/components/Panel.tsx", package["files"])
        self.assertIn("@radix-ui/react-dialog", package["external_deps"])
        self.assertIn("src/lib/missing.ts", package["unresolved_internal_deps"])
        self.assertEqual(package["transfer_plan"]["run_browser_smoke"], True)

    def test_package_does_not_mark_seen_dependencies_as_unresolved(self):
        package = build_dependency_package(
            {
                "name": "SeenDependencyPage",
                "source": "VARIANT",
                "source_contract_file": "VARIANT::src/Page.tsx",
                "risk_points": 0,
            },
            {
                "VARIANT": {
                    "dependencies": {
                        "src/Page.tsx": ["src/Button.tsx", "src/Card.tsx"],
                        "src/Button.tsx": ["src/Card.tsx"],
                        "src/Card.tsx": [],
                    }
                }
            },
        )

        self.assertIn("src/Card.tsx", package["files"])
        self.assertNotIn("src/Card.tsx", package["unresolved_internal_deps"])

    def test_package_resolves_disk_files_missing_from_graph(self):
        tmp_root = Path("scratch") / f"codemaps-package-test-{uuid.uuid4().hex}"
        try:
            asset = tmp_root / "src" / "locales" / "en" / "common.json"
            asset.parent.mkdir(parents=True)
            asset.write_text("{}", encoding="utf-8")

            package = build_dependency_package(
                {
                    "name": "LocalePage",
                    "source": "VARIANT",
                    "source_contract_file": "VARIANT::src/Page.tsx",
                    "risk_points": 0,
                },
                {
                    "VARIANT": {
                        "dependencies": {
                            "src/Page.tsx": ["src/locales/en/common.json"],
                        }
                    }
                },
                project_root=tmp_root,
            )

            self.assertIn("src/locales/en/common.json", package["files"])
            self.assertIn("src/locales/en/common.json", package["resolved_file_only_deps"])
            self.assertNotIn("src/locales/en/common.json", package["unresolved_internal_deps"])
        finally:
            if tmp_root.exists():
                _rmtree_force(tmp_root)


class MergeSimulationEngineTests(unittest.TestCase):
    def test_simulation_blocks_missing_source_files(self):
        tmp_root = Path("scratch") / f"codemaps-sim-test-{uuid.uuid4().hex}"
        source_root = tmp_root / "variant"
        main_root = tmp_root / "main"
        try:
            source_root.mkdir(parents=True)
            main_root.mkdir(parents=True)
            result = simulate_dependency_package(
                {
                    "candidate": "CandidatePage",
                    "source": "VARIANT",
                    "target_path": "src/feature/CandidatePage.tsx",
                    "package_tier": "direct_package",
                    "files": ["src/feature/CandidatePage.tsx"],
                    "closure_size": 1,
                    "recommended_gate": "static_gate_sufficient",
                    "unresolved_internal_deps": [],
                    "external_deps": [],
                },
                {"VARIANT": source_root},
                main_root=main_root,
            )

            self.assertEqual(result["decision"], "DO_NOT_IMPORT_YET")
            self.assertEqual(result["signals"]["missing_source_files"], 1)
            self.assertIn("repair_source_closure", result["required_actions"])
        finally:
            if tmp_root.exists():
                _rmtree_force(tmp_root)

    def test_simulation_allows_clean_direct_package(self):
        tmp_root = Path("scratch") / f"codemaps-sim-test-{uuid.uuid4().hex}"
        source_root = tmp_root / "variant"
        main_root = tmp_root / "main"
        try:
            source_file = source_root / "src" / "feature" / "CandidatePage.tsx"
            source_file.parent.mkdir(parents=True)
            source_file.write_text("export function CandidatePage() { return null; }", encoding="utf-8")
            main_root.mkdir(parents=True)

            result = simulate_dependency_package(
                {
                    "candidate": "CandidatePage",
                    "source": "VARIANT",
                    "target_path": "src/feature/CandidatePage.tsx",
                    "package_tier": "direct_package",
                    "files": ["src/feature/CandidatePage.tsx"],
                    "closure_size": 1,
                    "recommended_gate": "static_gate_sufficient",
                    "unresolved_internal_deps": [],
                    "external_deps": [],
                },
                {"VARIANT": source_root},
                main_root=main_root,
            )

            self.assertEqual(result["decision"], "SAFE_TO_IMPORT")
            self.assertEqual(result["signals"]["missing_source_files"], 0)
        finally:
            if tmp_root.exists():
                _rmtree_force(tmp_root)


class MergeDecisionCockpitTests(unittest.TestCase):
    def test_cockpit_maps_safe_simulation_to_import_now(self):
        row = build_cockpit_row(
            {
                "candidate": "CandidatePage",
                "source": "VARIANT",
                "target_path": "src/feature/CandidatePage.tsx",
                "package_tier": "direct_package",
                "decision": "SAFE_TO_IMPORT",
                "signals": {
                    "closure_size": 1,
                    "missing_source_files": 0,
                    "target_conflicts": 0,
                    "unresolved_internal_deps": 0,
                    "external_deps": 0,
                },
                "required_actions": ["run_static_gate"],
                "copy_plan": {"copy_files": ["src/feature/CandidatePage.tsx"]},
            }
        )

        self.assertEqual(row["action"], "Import Now")
        self.assertIn("static_dry_run_clean", row["reasons"])
        self.assertEqual(row["confidence"]["tier"], "high")
        self.assertTrue(row["stable_key"].startswith("merge_decision:VARIANT:CandidatePage:"))

    def test_cockpit_surfaces_do_not_import_reasons(self):
        row = build_cockpit_row(
            {
                "candidate": "CandidatePage",
                "source": "VARIANT",
                "target_path": "src/feature/CandidatePage.tsx",
                "package_tier": "manual_package_review",
                "decision": "DO_NOT_IMPORT_YET",
                "signals": {
                    "closure_size": 80,
                    "missing_source_files": 1,
                    "target_conflicts": 0,
                    "unresolved_internal_deps": 30,
                    "external_deps": 2,
                },
                "required_actions": ["repair_source_closure"],
                "missing_source_files": ["src/missing.ts"],
                "unresolved_internal_deps": ["src/unresolved.ts"],
                "external_deps": ["some-package"],
                "copy_plan": {"copy_files": []},
            },
            ui_candidate={"recommended_gate": "browser_smoke_required", "risk_tier": "high"},
            package={"closure_truncated": True, "source_contract_file": "VARIANT::src/feature/CandidatePage.tsx"},
        )

        self.assertEqual(row["action"], "Do Not Import Yet")
        self.assertIn("missing_source_files", row["reasons"])
        self.assertIn("browser_smoke_required", row["reasons"])
        self.assertEqual(row["evidence"]["source_contract_file"], "VARIANT::src/feature/CandidatePage.tsx")
        self.assertEqual(row["confidence"]["tier"], "high")

    def test_cockpit_confidence_marks_assisted_boundaries_as_medium(self):
        confidence = confidence_for_decision(
            {
                "decision": "ASSISTED_IMPORT",
                "signals": {
                    "closure_size": 4,
                    "missing_source_files": 0,
                    "target_conflicts": 0,
                    "unresolved_internal_deps": 1,
                    "external_deps": 1,
                },
                "unresolved_internal_deps": ["src/missing.ts"],
                "external_deps": ["some-package"],
            }
        )

        self.assertEqual(confidence["tier"], "medium")
        self.assertIn("assisted_review_boundary", confidence["factors"])


class SuppressionContractTests(unittest.TestCase):
    def test_suppression_matches_stable_key_and_expiry(self):
        row = {
            "source": "VARIANT",
            "candidate": "CandidatePage",
            "target_path": "src/feature/CandidatePage.tsx",
            "action": "Do Not Import Yet",
            "decision": "DO_NOT_IMPORT_YET",
        }
        entry = {
            "artifact": "merge_decision_cockpit",
            "key": stable_decision_key(row),
            "reason": "tracked_existing_gap",
            "expires_on": "2026-12-31",
        }

        suppression = find_suppression("merge_decision_cockpit", row, [entry], today=date(2026, 5, 7))

        self.assertIsNotNone(suppression)
        self.assertEqual(suppression["reason"], "tracked_existing_gap")

    def test_expired_suppression_does_not_match(self):
        row = {
            "source": "VARIANT",
            "candidate": "CandidatePage",
            "target_path": "src/feature/CandidatePage.tsx",
            "action": "Do Not Import Yet",
            "decision": "DO_NOT_IMPORT_YET",
        }
        entry = {
            "artifact": "merge_decision_cockpit",
            "source": "VARIANT",
            "candidate": "CandidatePage",
            "expires_on": "2026-01-01",
        }

        self.assertIsNone(find_suppression("merge_decision_cockpit", row, [entry], today=date(2026, 5, 7)))


class AITaskPackGeneratorTests(unittest.TestCase):
    def test_task_pack_contains_guardrails_verification_and_blockers(self):
        task = render_task_pack(
            {
                "candidate": "CandidatePage",
                "source": "VARIANT",
                "target_path": "src/feature/CandidatePage.tsx",
                "action": "Import With Review",
                "closure_size": 12,
                "required_actions": ["run_static_gate", "run_browser_smoke", "verify_external_packages"],
                "reasons": ["browser_smoke_required", "external_package_verification"],
                "route": {"smoke_path": "/candidate", "framework": "react_router"},
                "evidence": {
                    "source_contract_file": "VARIANT::src/feature/CandidatePage.tsx",
                    "smoke_spec_path": "output/scripts/ui_smoke_specs/candidate.spec.ts",
                    "external_deps": ["some-package"],
                },
            }
        )

        self.assertIn("## Guardrails", task)
        self.assertIn("Run generated UI smoke spec for route `/candidate`.", task)
        self.assertIn("external dependency: `some-package`", task)
        self.assertIn("source contract: `VARIANT::src/feature/CandidatePage.tsx`", task)

    def test_task_pack_record_uses_stable_output_path(self):
        record, path = build_task_pack_record(
            {
                "candidate": "Candidate Page",
                "source": "VARIANT",
                "target_path": "src/feature/CandidatePage.tsx",
                "action": "Import Now",
                "required_actions": [],
                "reasons": [],
            }
        )

        self.assertEqual(record["intent"], "safe_import")
        self.assertTrue(record["taskpack_path"].endswith("import-now-variant-candidate-page.md"))
        self.assertTrue(path.endswith("import-now-variant-candidate-page.md"))


class MergeIntelligenceRegressionTests(unittest.TestCase):
    def test_regression_checks_pass_for_consistent_artifacts(self):
        artifacts = {
            "framework_routes": {"summary": {"routes": 1}},
            "ui_runtime_contracts": {
                "merge_candidates": [
                    {
                        "name": "CandidatePage",
                        "recommended_gate": "browser_smoke_required",
                    }
                ]
            },
            "ui_smoke_specs": {"specs": [{"candidate": "CandidatePage"}]},
            "merge_dependency_packages": {"packages": [{"candidate": "CandidatePage"}]},
            "merge_simulation": {
                "simulations": [
                    {
                        "candidate": "CandidatePage",
                        "required_actions": ["run_browser_smoke"],
                    }
                ]
            },
            "merge_decision_cockpit": {
                "summary": {
                    "actions": {"Import Now": 1},
                    "effective_actions": {"Import Now": 1},
                    "confidence_tiers": {"high": 1},
                },
                "decisions": [
                    {
                        "candidate": "CandidatePage",
                        "action": "Import Now",
                        "stable_key": "merge_decision:VARIANT:CandidatePage:src/CandidatePage.tsx",
                        "confidence": {"tier": "high", "score": 0.9},
                    }
                ],
            },
            "ai_task_packs": {
                "taskpacks": [
                    {
                        "candidate": "CandidatePage",
                        "action": "Import Now",
                        "target_path": "src/CandidatePage.tsx",
                        "source_contract_file": "VARIANT::src/CandidatePage.tsx",
                        "copy_files_sample": ["src/CandidatePage.tsx"],
                        "copy_files_omitted": 0,
                    }
                ]
            },
        }

        checks = build_regression_checks(artifacts)

        self.assertTrue(all(check["passed"] for check in checks), checks)

    def test_regression_checks_fail_low_confidence_import_now(self):
        artifacts = {
            "framework_routes": {"summary": {"routes": 1}},
            "ui_runtime_contracts": {"merge_candidates": []},
            "ui_smoke_specs": {"specs": []},
            "merge_dependency_packages": {"packages": [{"candidate": "CandidatePage"}]},
            "merge_simulation": {"simulations": [{"candidate": "CandidatePage"}]},
            "merge_decision_cockpit": {
                "summary": {
                    "actions": {"Import Now": 1},
                    "effective_actions": {"Import Now": 1},
                    "confidence_tiers": {"low": 1},
                },
                "decisions": [
                    {
                        "candidate": "CandidatePage",
                        "action": "Import Now",
                        "stable_key": "merge_decision:VARIANT:CandidatePage:src/CandidatePage.tsx",
                        "confidence": {"tier": "low", "score": 0.4},
                    }
                ],
            },
            "ai_task_packs": {"taskpacks": [{"candidate": "CandidatePage", "action": "Import Now"}]},
        }

        checks = {check["name"]: check for check in build_regression_checks(artifacts)}

        self.assertFalse(checks["import_now_never_low_confidence"]["passed"])


class AdapterRegistryContractTests(unittest.TestCase):
    def test_adapter_manifest_requires_explicit_capabilities(self):
        errors = validate_adapter_manifest(
            {
                "id": "react-universal",
                "enabled": True,
                "ecosystem": "react",
                "frameworks": ["react", "next_app_router"],
                "capabilities": ["route_discovery", "ui_runtime_contracts"],
                "engines": ["Framework Route Analyzer"],
                "maturity": "production_candidate",
            }
        )

        self.assertEqual(errors, [])

    def test_adapter_summary_counts_ecosystem_and_capabilities(self):
        summary = summarize_adapters(
            {
                "adapters": [
                    {
                        "id": "react-universal",
                        "enabled": True,
                        "ecosystem": "react",
                        "frameworks": ["react"],
                        "capabilities": ["route_discovery", "merge_simulation"],
                        "engines": ["Framework Route Analyzer"],
                        "maturity": "production_candidate",
                    }
                ]
            }
        )

        self.assertEqual(summary["enabled"], 1)
        self.assertEqual(summary["valid"], 1)
        self.assertEqual(summary["ecosystems"]["react"], 1)
        self.assertEqual(summary["capabilities"]["merge_simulation"], 1)


class ReleaseReadinessContractTests(unittest.TestCase):
    def setUp(self):
        # Unit evidence must not depend on a previous repository analysis.
        self.enterContext(patch("tools.engines.release_readiness_report.load_atlas_data", return_value={"fixture": True}))
        self.enterContext(patch("tools.engines.release_readiness_report.load_genome_data", return_value={"fixture": True}))
        self.enterContext(patch("tools.engines.release_readiness_report._manual_validation_confidence", return_value={"status": "not_available"}))

    def _current_claim(self):
        identity = json.loads((CODE_MAPS_DIR / "config/release_identity.json").read_text(encoding="utf-8"))
        return identity["release_claim"]["allowed"]

    def test_release_claim_must_match_current_identity(self):
        if self._public_maintainer_boundary_is_closed():
            return
        for claim, expected in ((self._current_claim(), True), ("different_fixture_claim", False), ("", False)):
            with self.subTest(claim=claim):
                payload = build_release_readiness_payload({
                    "react_universal_readiness": {"summary": {"universal_ready": True, "allowed_claim": claim}}
                })
                check = next(row for row in payload["checks"] if row["name"] == "react_universal_readiness_pass")
                self.assertEqual(check["passed"], expected)
                self.assertTrue(check["enforced"])
                if not expected:
                    self.assertEqual(payload["readiness"], "NOT_READY")

    def _public_maintainer_boundary_is_closed(self) -> bool:
        if not (CODE_MAPS_DIR / "PUBLIC_DISTRIBUTION_MANIFEST.json").is_file():
            return False
        for relative_path in (
            "config/release_proof_scope_contract.json",
            "config/release_proof_steps_contract.json",
            "tools/run_release_proof_bundle.py",
        ):
            self.assertFalse((CODE_MAPS_DIR / relative_path).exists())
        return True

    def _ready_artifacts(self):
        return {
                "quality_gate": {
                    "release_gate_status": "PASS",
                    "ecosystem_signal_status": "ATTENTION",
                },
                "merge_intelligence_regression": {
                    "summary": {"failed_checks": 0, "total_checks": 1},
                },
                "adapter_registry": {
                    "summary": {"total": 1, "enabled": 1, "valid": 1},
                },
                "merge_decision_cockpit": {
                    "summary": {"candidates": 2, "confidence_tiers": {"high": 1, "medium": 1}},
                },
                "ai_task_packs": {
                    "summary": {"generated": 2},
                },
                "distribution_hardening_validation": {
                    "summary": {"failed_checks": 0, "total_checks": 1},
                },
                "entrypoint_failure_validation": {
                    "summary": {"failed_checks": 0, "total_checks": 1},
                },
                "operational_parity_validation": {
                    "summary": {"passed": True, "changed_sections": 0},
                },
                "performance_budget_validation": {
                    "summary": {"failed_checks": 0, "total_checks": 1},
                },
                "performance_ledger": {
                    "runs": [{"run_id": "test", "repo_band": "L"}],
                },
                "react_fixture_matrix_validation": {
                    "summary": {"failed_checks": 0, "total_checks": 1},
                },
                "react_universal_readiness": {
                    "summary": {
                        "universal_ready": True,
                        "allowed_claim": self._current_claim(),
                    },
                },
                "universal_proof_validation": {
                    "summary": {"failed_checks": 0, "total_checks": 1},
                },
                "mcp_agent_surface_validation": {
                    "summary": {"status": "PASS", "missing_required_tools": []},
                },
                "codemaps_suppressions": {
                    "suppressions": [],
                },
        }

    def test_release_readiness_requires_quality_artifacts_and_regression(self):
        if self._public_maintainer_boundary_is_closed():
            return
        payload = build_release_readiness_payload(self._ready_artifacts(), artifact_errors={})
        self.assertEqual(payload["readiness"], "PRODUCTION_READY")
        self.assertEqual(payload["summary"]["failed"], 0)
        self.assertEqual(payload["summary"]["platform_readiness"], "PRODUCTION_READY")
        self.assertTrue(payload["summary"]["ecosystem_attention"])

    def test_target_repository_debt_does_not_become_sage_release_debt(self):
        if self._public_maintainer_boundary_is_closed():
            return
        artifacts = self._ready_artifacts()
        artifacts["quality_gate"]["release_gate_status"] = "FAIL"
        artifacts["quality_gate"]["scope_gate_status"] = "PASS"
        payload = build_release_readiness_payload(artifacts)
        self.assertEqual(payload["readiness"], "PRODUCTION_READY")
        self.assertEqual(payload["evidence"]["quality_gate"]["release_gate_status"], "FAIL")
        target_check = next(row for row in payload["checks"] if row["name"] == "quality_gate_pass")
        self.assertFalse(target_check["passed"])
        self.assertFalse(target_check["enforced"])

        invalid = build_release_readiness_payload(artifacts, artifact_errors={"quality_gate": ["invalid fixture evidence"]})
        self.assertEqual(invalid["readiness"], "NOT_READY")
        artifacts["mcp_agent_surface_validation"]["summary"]["status"] = "FAIL"
        broken_product = build_release_readiness_payload(artifacts)
        self.assertEqual(broken_product["readiness"], "NOT_READY")

    def test_release_readiness_blocks_missing_mcp_agent_surface(self):
        if self._public_maintainer_boundary_is_closed():
            return
        payload = build_release_readiness_payload(
            {
                "quality_gate": {"release_gate_status": "PASS"},
                "merge_intelligence_regression": {"summary": {"failed_checks": 0, "total_checks": 1}},
                "adapter_registry": {"summary": {"total": 1, "enabled": 1, "valid": 1}},
                "merge_decision_cockpit": {"summary": {"candidates": 0, "confidence_tiers": {}}},
                "ai_task_packs": {"summary": {"generated": 1}},
                "distribution_hardening_validation": {"summary": {"failed_checks": 0, "total_checks": 1}},
                "entrypoint_failure_validation": {"summary": {"failed_checks": 0, "total_checks": 1}},
                "operational_parity_validation": {"summary": {"passed": True, "changed_sections": 0}},
                "performance_budget_validation": {"summary": {"failed_checks": 0}},
                "performance_ledger": {"runs": [{"run_id": "test", "repo_band": "L"}]},
                "react_fixture_matrix_validation": {"summary": {"failed_checks": 0, "total_checks": 1}},
                "react_universal_readiness": {"summary": {"universal_ready": True}},
                "universal_proof_validation": {"summary": {"failed_checks": 0, "total_checks": 1}},
                "mcp_agent_surface_validation": {"summary": {"status": "FAIL", "missing_required_tools": ["get_nexora_brief"]}},
                "codemaps_suppressions": {"suppressions": []},
            },
            artifact_errors={},
        )

        self.assertEqual(payload["readiness"], "NOT_READY")

    def test_release_readiness_blocks_react_universal_failure(self):
        if self._public_maintainer_boundary_is_closed():
            return
        payload = build_release_readiness_payload(
            {
                "quality_gate": {"release_gate_status": "PASS"},
                "merge_intelligence_regression": {"summary": {"failed_checks": 0, "total_checks": 1}},
                "adapter_registry": {"summary": {"total": 1, "enabled": 1, "valid": 1}},
                "merge_decision_cockpit": {"summary": {"candidates": 0, "confidence_tiers": {}}},
                "ai_task_packs": {"summary": {"generated": 1}},
                "distribution_hardening_validation": {"summary": {"failed_checks": 0, "total_checks": 1}},
                "operational_parity_validation": {"summary": {"passed": True, "changed_sections": 0}},
                "performance_budget_validation": {"summary": {"failed_checks": 0}},
                "performance_ledger": {"runs": [{"run_id": "test", "repo_band": "L"}]},
                "react_fixture_matrix_validation": {"summary": {"failed_checks": 0, "total_checks": 1}},
                "react_universal_readiness": {"summary": {"universal_ready": False}},
                "universal_proof_validation": {"summary": {"failed_checks": 0, "total_checks": 1}},
                "codemaps_suppressions": {"suppressions": []},
            },
            artifact_errors={},
        )

        self.assertEqual(payload["readiness"], "NOT_READY")

    def test_release_readiness_blocks_failed_regression(self):
        if self._public_maintainer_boundary_is_closed():
            return
        payload = build_release_readiness_payload(
            {
                "quality_gate": {"release_gate_status": "PASS"},
                "merge_intelligence_regression": {"summary": {"failed_checks": 1}},
                "adapter_registry": {"summary": {"total": 1, "enabled": 1, "valid": 1}},
                "merge_decision_cockpit": {"summary": {"candidates": 0, "confidence_tiers": {}}},
                "ai_task_packs": {"summary": {"generated": 1}},
                "distribution_hardening_validation": {"summary": {"failed_checks": 0}},
                "entrypoint_failure_validation": {"summary": {"failed_checks": 0}},
                "operational_parity_validation": {"summary": {"passed": True, "changed_sections": 0}},
                "performance_budget_validation": {"summary": {"failed_checks": 0}},
                "performance_ledger": {"runs": [{"run_id": "test", "repo_band": "L"}]},
                "react_fixture_matrix_validation": {"summary": {"failed_checks": 0}},
                "react_universal_readiness": {
                    "summary": {
                        "universal_ready": True,
                        "external_fixture_pool_required": False,
                        "allowed_claim": self._current_claim(),
                    }
                },
                "universal_proof_validation": {"summary": {"failed_checks": 0}},
                "mcp_agent_surface_validation": {"summary": {"status": "PASS", "missing_required_tools": []}},
                "codemaps_suppressions": {"suppressions": []},
            },
            artifact_errors={},
        )

        self.assertEqual(payload["readiness"], "NOT_READY")

    def test_release_readiness_blocks_distribution_failure(self):
        if self._public_maintainer_boundary_is_closed():
            return
        payload = build_release_readiness_payload(
            {
                "quality_gate": {"release_gate_status": "PASS"},
                "merge_intelligence_regression": {"summary": {"failed_checks": 0}},
                "adapter_registry": {"summary": {"total": 1, "enabled": 1, "valid": 1}},
                "merge_decision_cockpit": {"summary": {"candidates": 0, "confidence_tiers": {}}},
                "ai_task_packs": {"summary": {"generated": 1}},
                "distribution_hardening_validation": {"summary": {"failed_checks": 1}},
                "entrypoint_failure_validation": {"summary": {"failed_checks": 0}},
                "operational_parity_validation": {"summary": {"passed": True, "changed_sections": 0}},
                "performance_budget_validation": {"summary": {"failed_checks": 0}},
                "performance_ledger": {"runs": [{"run_id": "test", "repo_band": "L"}]},
                "react_fixture_matrix_validation": {"summary": {"failed_checks": 0}},
                "universal_proof_validation": {"summary": {"failed_checks": 0}},
                "codemaps_suppressions": {"suppressions": []},
            },
            artifact_errors={},
        )

        self.assertEqual(payload["readiness"], "NOT_READY")

    def test_release_readiness_blocks_entrypoint_failure(self):
        if self._public_maintainer_boundary_is_closed():
            return
        payload = build_release_readiness_payload(
            {
                "quality_gate": {"release_gate_status": "PASS"},
                "merge_intelligence_regression": {"summary": {"failed_checks": 0}},
                "adapter_registry": {"summary": {"total": 1, "enabled": 1, "valid": 1}},
                "merge_decision_cockpit": {"summary": {"candidates": 0, "confidence_tiers": {}}},
                "ai_task_packs": {"summary": {"generated": 1}},
                "distribution_hardening_validation": {"summary": {"failed_checks": 0}},
                "entrypoint_failure_validation": {"summary": {"failed_checks": 1}},
                "operational_parity_validation": {"summary": {"passed": True, "changed_sections": 0}},
                "performance_budget_validation": {"summary": {"failed_checks": 0}},
                "performance_ledger": {"runs": [{"run_id": "test", "repo_band": "L"}]},
                "react_fixture_matrix_validation": {"summary": {"failed_checks": 0}},
                "universal_proof_validation": {"summary": {"failed_checks": 0}},
                "codemaps_suppressions": {"suppressions": []},
            },
            artifact_errors={},
        )

        self.assertEqual(payload["readiness"], "NOT_READY")

    def test_release_readiness_blocks_failed_operational_parity_when_present(self):
        if self._public_maintainer_boundary_is_closed():
            return
        payload = build_release_readiness_payload(
            {
                "quality_gate": {"release_gate_status": "PASS"},
                "merge_intelligence_regression": {"summary": {"failed_checks": 0}},
                "adapter_registry": {"summary": {"total": 1, "enabled": 1, "valid": 1}},
                "merge_decision_cockpit": {"summary": {"candidates": 0, "confidence_tiers": {}}},
                "ai_task_packs": {"summary": {"generated": 1}},
                "distribution_hardening_validation": {"summary": {"failed_checks": 0}},
                "entrypoint_failure_validation": {"summary": {"failed_checks": 0}},
                "operational_parity_validation": {"summary": {"passed": False, "changed_sections": 1}},
                "performance_budget_validation": {"summary": {"failed_checks": 0}},
                "performance_ledger": {"runs": [{"run_id": "test", "repo_band": "L"}]},
                "react_fixture_matrix_validation": {"summary": {"failed_checks": 0}},
                "universal_proof_validation": {"summary": {"failed_checks": 0}},
                "codemaps_suppressions": {"suppressions": []},
            },
            artifact_errors={},
        )

        self.assertEqual(payload["readiness"], "NOT_READY")

    def test_release_readiness_reports_performance_budget_failure_without_blocking_install(self):
        if self._public_maintainer_boundary_is_closed():
            return
        payload = build_release_readiness_payload(
            {
                "quality_gate": {"release_gate_status": "PASS"},
                "merge_intelligence_regression": {"summary": {"failed_checks": 0, "total_checks": 1}},
                "adapter_registry": {"summary": {"total": 1, "enabled": 1, "valid": 1}},
                "merge_decision_cockpit": {"summary": {"candidates": 0, "confidence_tiers": {}}},
                "ai_task_packs": {"summary": {"generated": 1}},
                "distribution_hardening_validation": {"summary": {"failed_checks": 0, "total_checks": 1}},
                "entrypoint_failure_validation": {"summary": {"failed_checks": 0, "total_checks": 1}},
                "operational_parity_validation": {"summary": {"passed": True, "changed_sections": 0}},
                "performance_budget_validation": {"summary": {"failed_checks": 1}},
                "performance_ledger": {"runs": [{"run_id": "test", "repo_band": "L"}]},
                "react_fixture_matrix_validation": {"summary": {"failed_checks": 0}},
                "react_universal_readiness": {
                    "summary": {
                        "universal_ready": True,
                        "external_fixture_pool_required": False,
                        "allowed_claim": self._current_claim(),
                    }
                },
                "universal_proof_validation": {"summary": {"failed_checks": 0}},
                "mcp_agent_surface_validation": {"summary": {"status": "PASS", "missing_required_tools": []}},
                "codemaps_suppressions": {"suppressions": []},
            },
            artifact_errors={},
        )

        self.assertEqual(payload["readiness"], "PRODUCTION_READY")
        perf_check = next(check for check in payload["checks"] if check["name"] == "performance_budget_pass")
        self.assertFalse(perf_check["enforced"])
        self.assertFalse(perf_check["passed"])

    def test_release_readiness_reports_missing_performance_ledger_without_blocking_install(self):
        if self._public_maintainer_boundary_is_closed():
            return
        payload = build_release_readiness_payload(
            {
                "quality_gate": {"release_gate_status": "PASS"},
                "merge_intelligence_regression": {"summary": {"failed_checks": 0, "total_checks": 1}},
                "adapter_registry": {"summary": {"total": 1, "enabled": 1, "valid": 1}},
                "merge_decision_cockpit": {"summary": {"candidates": 0, "confidence_tiers": {}}},
                "ai_task_packs": {"summary": {"generated": 1}},
                "distribution_hardening_validation": {"summary": {"failed_checks": 0, "total_checks": 1}},
                "entrypoint_failure_validation": {"summary": {"failed_checks": 0, "total_checks": 1}},
                "operational_parity_validation": {"summary": {"passed": True, "changed_sections": 0}},
                "performance_budget_validation": {"summary": {"failed_checks": 0}},
                "performance_ledger": {"runs": []},
                "react_fixture_matrix_validation": {"summary": {"failed_checks": 0}},
                "react_universal_readiness": {
                    "summary": {
                        "universal_ready": True,
                        "external_fixture_pool_required": False,
                        "allowed_claim": self._current_claim(),
                    }
                },
                "universal_proof_validation": {"summary": {"failed_checks": 0}},
                "mcp_agent_surface_validation": {"summary": {"status": "PASS", "missing_required_tools": []}},
                "codemaps_suppressions": {"suppressions": []},
            },
            artifact_errors={},
        )

        self.assertEqual(payload["readiness"], "PRODUCTION_READY")
        ledger_check = next(check for check in payload["checks"] if check["name"] == "performance_ledger_present")
        self.assertFalse(ledger_check["enforced"])
        self.assertFalse(ledger_check["passed"])

    def test_operator_readiness_fails_closed_when_target_quality_gate_fails(self):
        self.assertEqual(
            _combined_release_readiness("PRODUCTION_READY", "FAIL", False),
            "NOT_READY",
        )
        self.assertEqual(
            _combined_release_readiness("PRODUCTION_READY", "PASS", True),
            "PRODUCTION_READY",
        )
        self.assertEqual(
            _combined_release_readiness(None, None, None),
            "INCOMPLETE_EVIDENCE",
        )
        self.assertEqual(
            _combined_release_readiness("PRODUCTION_READY", "FAIL", True),
            "NOT_READY",
        )


class WatchdogOpenablePathContractTests(unittest.TestCase):
    def test_openable_path_is_canonical_and_cannot_escape_analysis_root(self):
        with tempfile.TemporaryDirectory(prefix="watchdog_openable_") as tmp:
            root = Path(tmp)
            target = root / "src" / "platform" / "SecretService.ts"
            target.parent.mkdir(parents=True)
            target.write_text("export {}\n", encoding="utf-8")

            normalized = _canonical_openable_path(
                "src/../src/platform/SecretService.ts",
                root,
            )
            escaped = _canonical_openable_path("../outside.ts", root)

        self.assertEqual(normalized["openable_path"], "src/platform/SecretService.ts")
        self.assertEqual(normalized["path_status"], "normalized_to_openable_path")
        self.assertEqual(escaped["path_status"], "not_openable")


class SelfGovernanceSuspicionStateTests(unittest.TestCase):
    def _source(self) -> dict:
        return {
            "id": "fixture_detector",
            "artifact": "output/.raw/fixture.json",
            "release_proof_step": "fixture_detector",
            "status_path": "summary.status",
            "accepted_values": ["PASS"],
            "freshness_sources": ["tools/**/*.py"],
            "purpose": "test fixture",
        }

    def test_reports_current_then_stale_when_upstream_is_newer(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifact = root / "output" / ".raw" / "fixture.json"
            source = root / "tools" / "engine.py"
            artifact.parent.mkdir(parents=True)
            source.parent.mkdir(parents=True)
            source.write_text("first\n", encoding="utf-8")
            artifact.write_text('{"summary":{"status":"PASS"}}', encoding="utf-8")

            current = _self_governance_suspicion_state([self._source()], base_dir=root)
            self.assertEqual(current[0]["evidence_status"], "current")

            newer_time = artifact.stat().st_mtime + 2
            os.utime(source, (newer_time, newer_time))
            stale = _self_governance_suspicion_state([self._source()], base_dir=root)
            self.assertEqual(stale[0]["evidence_status"], "stale")
            self.assertEqual(stale[0]["newer_sources"], ["tools/**/*.py"])

    def test_reports_missing_source_and_attention_without_inventing_pass(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifact = root / "output" / ".raw" / "fixture.json"
            artifact.parent.mkdir(parents=True)
            artifact.write_text('{"summary":{"status":"FAIL"}}', encoding="utf-8")

            source_missing = _self_governance_suspicion_state([self._source()], base_dir=root)
            self.assertEqual(source_missing[0]["evidence_status"], "source_missing")

            source = root / "tools" / "engine.py"
            source.parent.mkdir(parents=True)
            source.write_text("current\n", encoding="utf-8")
            older_time = artifact.stat().st_mtime - 2
            os.utime(source, (older_time, older_time))
            attention = _self_governance_suspicion_state([self._source()], base_dir=root)
            self.assertEqual(attention[0]["evidence_status"], "attention")
            self.assertEqual(attention[0]["observed_status"], "FAIL")

            artifact.unlink()
            missing = _self_governance_suspicion_state([self._source()], base_dir=root)
            self.assertEqual(missing[0]["evidence_status"], "missing")


class HardcodedDecisionInventoryContractTests(unittest.TestCase):
    def test_doctrine_alias_fallback_detection_is_scope_aware(self):
        with tempfile.TemporaryDirectory(dir=CODE_MAPS_DIR / "tools") as temp_dir:
            source = Path(temp_dir) / "fixture.py"
            source.write_text(
                "from tools.core.config import DOCTRINE\n"
                "def governed():\n"
                "    policy = DOCTRINE.get('example', {})\n"
                "    return policy.get('threshold', 7)\n"
                "def unrelated(policy, self):\n"
                "    return policy.get('threshold', 9), self.get('value', {'local': True})\n",
                encoding="utf-8",
            )
            rows = _read_central_contract_fallbacks(source)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].contract, "DOCTRINE")
        self.assertEqual(rows[0].section, "threshold")
        self.assertEqual(rows[0].fallback, 7)


class ArchitectureBlueprintRegistryContractTests(unittest.TestCase):
    def test_legacy_profile_aliases_resolve_to_canonical_ids(self):
        self.assertEqual(canonical_profile_id("FSD_STANDARD"), "FSD_STRICT")
        self.assertEqual(canonical_profile_id("HEXAGONAL_PURE"), "CLEAN_ARCHITECTURE")
        self.assertEqual(canonical_profile_id("NEXTJS_APP"), "NEXTJS_APP_ROUTER")
        self.assertEqual(canonical_profile_id("FRACTAL_SOVEREIGN_MONOLITH"), "SOVEREIGN_ELITE")

    def test_sovereign_profile_preserves_composed_rule_families(self):
        effective = effective_profile_ids("SOVEREIGN_ELITE")
        self.assertTrue({"SOVEREIGN_ELITE", "FSD_STRICT", "FSD_STANDARD", "CLEAN_ARCHITECTURE", "HEXAGONAL_PURE"} <= effective)

    def test_canonical_profiles_have_valid_blueprint_axes(self):
        for profile in ("MINIMAL", "MODULAR_FLAT", "FSD_STRICT", "CLEAN_ARCHITECTURE", "SOVEREIGN_ELITE", "MIXED_ARCHITECTURE"):
            self.assertTrue(blueprint_axes_valid(profile), profile)

    def test_governance_context_distinguishes_proposal_from_human_seal(self):
        oracle = {
            "summary": {"status": "PROPOSE_SEAL", "top_recommended_profile": "FSD_STRICT"},
            "projects": [{
                "project": "MAIN",
                "recommended_profile": "FSD_STRICT",
                "blueprint": {"topology": "feature_sliced", "runtime": "react_spa", "repository_shape": "single_app"},
                "confidence": 0.91,
                "seal_ready": True,
                "seal_proposal": {"status": "PROPOSED"},
            }],
        }
        proposal = architecture_governance_context(oracle, {"entries": []}, project_ids={"MAIN"})
        scope_missing = architecture_governance_context(oracle, {"entries": [{
            "gate": "architecture_doctrine_seal", "decision": "approved", "revoked_by": ""
        }]})
        sealed = architecture_governance_context(oracle, {"entries": [{
            "gate": "architecture_doctrine_seal", "scope": "MAIN", "decision": "approved", "revoked_by": ""
        }]})

        self.assertEqual(proposal["seal_state"], "PROPOSAL_ONLY")
        self.assertEqual(scope_missing["seal_state"], "PROPOSAL_ONLY")
        self.assertEqual(sealed["seal_state"], "HUMAN_SEALED")
        self.assertEqual(proposal["projects"][0]["topology"], "feature_sliced")
        self.assertEqual(proposal["projects"][0]["composition_model"], "vertical_slices")

    def test_governance_context_filters_unrelated_projects(self):
        oracle = {
            "projects": [
                {"project": "MAIN", "blueprint": {}},
                {"project": "OTHER", "blueprint": {}},
            ]
        }
        context = architecture_governance_context(oracle, {}, project_ids={"OTHER"})
        self.assertEqual([item["project"] for item in context["projects"]], ["OTHER"])

    def test_symbol_grounding_drops_unrelated_snippets_when_requested_target_is_absent(self):
        status = {
            "target_spans": [
                {"symbol": "Neighbor", "source_lines": "L3-L3"},
            ],
            "target_source_snippets": [
                {"symbol": "Neighbor", "source_lines": "L3-L3", "code": "3: Neighbor = 1"},
            ],
        }
        narrowed = _narrow_source_grounding_to_inspection_target(
            status,
            {"target_spans": []},
            "MAIN::RequestedSymbol",
        )

        self.assertEqual(narrowed["target_source_snippets"], [])

    def test_bounded_source_snippets_deduplicate_identical_symbol_coordinates(self):
        content = "first\nsecond\nthird\n"
        snippets = _bounded_source_snippets(
            content,
            [
                {"symbol": "FirstExport", "start_line": 2, "end_line": 2, "source_lines": "L2-L2"},
                {"symbol": "SecondExport", "start_line": 2, "end_line": 2, "source_lines": "L2-L2"},
            ],
        )

        self.assertEqual(len(snippets), 1)
        self.assertEqual(snippets[0]["source_lines"], "L2-L2")

    def test_partial_symbol_inspection_is_grounded_but_orientation_only(self):
        payload = {
            "target_path_status": {
                "target_source_snippets": [
                    {
                        "snippet_status": "partial_included_span_too_large",
                        "one_shot_edit_ready": False,
                    }
                ]
            }
        }

        _set_inspection_edit_authority(payload, grounded=True, kind="symbol")

        self.assertTrue(payload["source_grounded_for_inspection"])
        self.assertFalse(payload["one_shot_edit_ready"])
        self.assertFalse(payload["safe_to_edit_from_inspection"])
        self.assertEqual(payload["inspection_authority"], "orientation_only")
        self.assertEqual(payload["edit_readiness_reason"], "source_context_partial_or_omitted")

    def test_complete_symbol_inspection_is_bounded_edit_context(self):
        payload = {
            "target": {"kind": "symbol", "value": "MAIN::ready"},
            "atlas_symbols": [
                {
                    "project": "MAIN",
                    "symbol": "ready",
                    "target_ref": "MAIN::src/ready.py",
                    "line": 1,
                    "end_line": 1,
                }
            ],
            "target_path_status": {
                "target_source_snippets": [
                    {"symbol": "ready", "snippet_status": "included", "code": "1: def ready(): pass"}
                ]
            }
        }

        _set_inspection_edit_authority(payload, grounded=True, kind="symbol")

        self.assertTrue(payload["one_shot_edit_ready"])
        self.assertTrue(payload["safe_to_edit_from_inspection"])
        self.assertEqual(payload["inspection_authority"], "bounded_edit_context")

    def test_filename_only_symbol_match_is_orientation_only(self):
        payload = {
            "target": {"kind": "symbol", "value": "MAIN::index"},
            "atlas_symbols": [
                {
                    "project": "MAIN",
                    "symbol": "Character",
                    "target_ref": "MAIN::src/index.ts",
                    "line": 10,
                    "end_line": 20,
                }
            ],
            "target_path_status": {
                "target_source_snippets": [
                    {"snippet_status": "included", "code": "10: export type Character = {}"}
                ]
            },
        }

        _set_inspection_edit_authority(payload, grounded=True, kind="symbol")

        self.assertFalse(payload["one_shot_edit_ready"])
        self.assertFalse(payload["safe_to_edit_from_inspection"])
        self.assertEqual(payload["inspection_authority"], "orientation_only")
        self.assertEqual(payload["edit_readiness_reason"], "symbol_query_requires_unique_exact_match")

    def test_exact_symbol_without_matching_included_snippet_is_orientation_only(self):
        payload = {
            "target": {"kind": "symbol", "value": "MAIN::ready"},
            "atlas_symbols": [
                {
                    "project": "MAIN",
                    "symbol": "ready",
                    "target_ref": "MAIN::src/ready.py",
                    "line": 1,
                    "end_line": 1,
                }
            ],
            "target_path_status": {
                "target_source_snippets": [
                    {"symbol": "other", "snippet_status": "included", "code": "1: def other(): pass"}
                ]
            },
        }

        _set_inspection_edit_authority(payload, grounded=True, kind="symbol")

        self.assertFalse(payload["one_shot_edit_ready"])
        self.assertEqual(payload["edit_readiness_reason"], "symbol_query_requires_unique_exact_match")

    def test_duplicate_exact_symbol_matches_are_orientation_only(self):
        payload = {
            "target": {"kind": "symbol", "value": "MAIN::ready"},
            "atlas_symbols": [
                {"project": "MAIN", "symbol": "ready", "target_ref": "MAIN::src/a.py", "line": 1},
                {"project": "MAIN", "symbol": "ready", "target_ref": "MAIN::src/b.py", "line": 1},
            ],
            "target_path_status": {
                "target_source_snippets": [{"snippet_status": "included", "code": "1: def ready(): pass"}]
            },
        }

        _set_inspection_edit_authority(payload, grounded=True, kind="symbol")

        self.assertFalse(payload["one_shot_edit_ready"])
        self.assertEqual(payload["edit_readiness_reason"], "symbol_query_requires_unique_exact_match")

    def test_file_inspection_requires_explicit_complete_line_range_for_edit_readiness(self):
        payload = {
            "target_path_status": {
                "target_source_snippets": [{"snippet_status": "included", "code": "1: const ready = true"}]
            }
        }

        _set_inspection_edit_authority(payload, grounded=True, kind="file")
        self.assertFalse(payload["one_shot_edit_ready"])
        self.assertEqual(payload["edit_readiness_reason"], "file_inspection_requires_explicit_complete_line_range")

        payload["target_path_status"]["requested_line_range"] = {
            "line_start": 1,
            "line_end": 1,
            "bounded_line_start": 1,
            "bounded_line_end": 1,
            "line_status": "available",
        }
        _set_inspection_edit_authority(payload, grounded=True, kind="file")
        self.assertTrue(payload["one_shot_edit_ready"])
        self.assertEqual(payload["inspection_authority"], "bounded_edit_context")

    def test_surgical_context_reports_ambiguous_symbol_instead_of_selecting_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw_dir = Path(tmp)
            (raw_dir / "atlas.json").write_text("{}", encoding="utf-8")
            matches = [
                {"name": "cli", "project": "MAIN", "file": "src/first.py", "line": 10, "type": "Function"},
                {"name": "cli", "project": "MAIN", "file": "tests/second.py", "line": 20, "type": "Function"},
            ]
            with patch.object(mcp_server, "_raw_dir_for_target", return_value=raw_dir):
                with patch.object(mcp_server, "_find_symbol_matches", return_value=matches):
                    payload = json.loads(get_surgical_context("cli", format="json"))

        self.assertEqual(payload["status"], "ambiguous_symbol")
        self.assertEqual(payload["candidate_count"], 2)
        self.assertEqual(payload["candidate_count_semantics"], "complete_match_set")
        self.assertFalse(payload["search_truncated"])
        self.assertEqual(
            {item["target_file"] for item in payload["candidates"]},
            {"src/first.py", "tests/second.py"},
        )
        self.assertEqual(
            {item["target_ref"] for item in payload["candidates"]},
            {"MAIN::src/first.py", "MAIN::tests/second.py"},
        )
        self.assertTrue(all(item["target_status"] == "indexed_symbol_candidate" for item in payload["candidates"]))

    def test_symbol_search_orders_exact_names_before_fuzzy_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel_path in ("noise.py", "first.py", "second.py"):
                (root / rel_path).write_text("# source\n", encoding="utf-8")
            noise_symbols = [
                {"name": f"cli_noise_{index:03d}", "type": "Variable", "line": index + 1, "end_line": index + 1}
                for index in range(520)
            ]
            atlas = {
                "MAIN": {
                    "root_path": str(root),
                    "project_type": "python",
                    "files": {
                        "noise.py": {"language": "python", "size": 9, "hash": "noise", "symbols": noise_symbols},
                        "first.py": {"language": "python", "size": 9, "hash": "first", "symbols": [{"name": "cli", "type": "Function", "line": 1, "end_line": 1}]},
                        "second.py": {"language": "python", "size": 9, "hash": "second", "symbols": [{"name": "cli", "type": "Function", "line": 2, "end_line": 2}]},
                    },
                    "dependencies": {"noise.py": [], "first.py": [], "second.py": []},
                    "symbols": [],
                }
            }
            store = ArtifactStore()
            store.use_sqlite = True
            store.backend = "hybrid_sqlite"
            store.db_manager = SQLiteManager(root / "codemaps.db")
            store._schema_initialized = False
            store._ensure_schema()
            store._save_atlas_to_sqlite(atlas)

            matches = _find_symbol_matches("cli", project="MAIN", raw_dir=root)

        exact_files = [row["file"] for row in matches if row.get("name") == "cli"]
        self.assertEqual(exact_files, ["first.py", "second.py"])
        self.assertEqual([row["name"] for row in matches[:2]], ["cli", "cli"])
        self.assertTrue(all(row.get("search_truncated") is True for row in matches))

    def test_surgical_context_discloses_bounded_candidate_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw_dir = Path(tmp)
            (raw_dir / "atlas.json").write_text("{}", encoding="utf-8")
            matches = [
                {
                    "name": "shared",
                    "project": "MAIN",
                    "file": f"src/item_{index}.py",
                    "line": index + 1,
                    "type": "Function",
                    "search_truncated": True,
                }
                for index in range(500)
            ]
            with patch.object(mcp_server, "_raw_dir_for_target", return_value=raw_dir):
                with patch.object(mcp_server, "_find_symbol_matches", return_value=matches):
                    payload = json.loads(get_surgical_context("shared", format="json"))

        self.assertEqual(payload["candidate_count"], 500)
        self.assertEqual(payload["candidate_count_semantics"], "lower_bound")
        self.assertTrue(payload["search_truncated"])
        self.assertEqual(len(payload["candidates"]), 12)

    def test_atlas_fallback_bounds_symbol_and_file_sources_independently(self):
        atlas = {
            "MAIN": {
                "root_path": ".",
                "symbols": [
                    {"name": f"shared_{index}", "type": "Function", "file": f"src/shared_{index}.py"}
                    for index in range(3)
                ],
                "files": {
                    f"src/shared_{index}.py": {"workspace_rel": f"src/shared_{index}.py", "imports": []}
                    for index in range(3)
                },
            }
        }
        with patch.object(mcp_server, "_atlas", return_value=atlas):
            with patch.object(
                mcp_server,
                "_symbol_search_policy",
                return_value={"max_candidate_rows_per_source": 2, "ambiguity_preview_items": 1, "agent_rule": "test"},
            ):
                matches = _find_symbol_matches("shared", project="MAIN", raw_dir=Path("missing"))

        self.assertEqual(len([row for row in matches if row["type"] != "File"]), 2)
        self.assertEqual(len([row for row in matches if row["type"] == "File"]), 2)
        self.assertTrue(all(row["search_truncated"] for row in matches))

    @patch("tools.mcp.server._ACTIVE_MCP_PROFILE", "target_repository_default")
    @patch.dict(
        os.environ,
        {
            "SAGE_ACTOR_PROFILE": "target_repository_default",
            "SAGE_REALITY_TARGET_PROFILE": "",
        },
    )
    def test_operator_privileged_projection_is_blocked_for_target_repository_profile(self):
        self.assertFalse(_operator_privileged_projection_allowed())
        denied = json.loads(_operator_projection_access_denied(requested_format="json"))
        self.assertEqual(denied["status"], "BLOCKED")
        self.assertEqual(denied["reason"], "operator_projection_requires_sage_on_sage_profile")

    @patch("tools.mcp.server._ACTIVE_MCP_PROFILE", "sage_operator_debug")
    @patch.dict(
        os.environ,
        {
            "SAGE_ACTOR_PROFILE": "sage_operator_debug",
            "SAGE_REALITY_TARGET_PROFILE": "sage_self",
        },
    )
    def test_operator_privileged_projection_is_available_only_for_sage_profile(self):
        if (CODE_MAPS_DIR / "PUBLIC_DISTRIBUTION_MANIFEST.json").is_file():
            self.assertFalse(_operator_privileged_projection_allowed())
            return
        self.assertTrue(_operator_privileged_projection_allowed())

    @patch("tools.mcp.server._ACTIVE_MCP_PROFILE", "sage_operator_debug")
    @patch.dict(
        os.environ,
        {
            "SAGE_ACTOR_PROFILE": "sage_operator_debug",
            "SAGE_REALITY_TARGET_PROFILE": "",
        },
    )
    def test_operator_privileged_projection_rejects_partial_sage_profile_pair(self):
        self.assertFalse(_operator_privileged_projection_allowed())

    def test_audit_work_item_identity_ignores_storage_row_identity(self):
        first = _stable_audit_work_item_id(
            "MAIN",
            "src/layouts/AppLayout.tsx",
            "relative_imports_no_alias",
            "layouts/AppLayout.tsx imports ./CommandPalette",
        )
        repeated = _stable_audit_work_item_id(
            "MAIN",
            "src/layouts/AppLayout.tsx",
            "relative_imports_no_alias",
            "layouts/AppLayout.tsx imports ./CommandPalette",
        )
        changed_evidence = _stable_audit_work_item_id(
            "MAIN",
            "src/layouts/AppLayout.tsx",
            "relative_imports_no_alias",
            "layouts/AppLayout.tsx imports ./OtherPalette",
        )
        equivalent_path = _stable_audit_work_item_id(
            "MAIN",
            ".\\src\\layouts\\AppLayout.tsx",
            "relative_imports_no_alias",
            "layouts/AppLayout.tsx imports ./CommandPalette",
        )
        unicode_composed = _stable_audit_work_item_id(
            "MAIN",
            "Variations/ESKİ/stores.ts",
            "domain_ui_leaks",
            "İhlal",
        )
        unicode_decomposed = _stable_audit_work_item_id(
            "MAIN",
            "Variations/ESKI\u0307/stores.ts",
            "domain_ui_leaks",
            "I\u0307hlal",
        )

        self.assertEqual(first, repeated)
        self.assertEqual(first, equivalent_path)
        self.assertEqual(unicode_composed, unicode_decomposed)
        self.assertNotEqual(first, changed_evidence)
        self.assertRegex(first, r"^audit_debt_[0-9a-f]{16}$")


class NuclearFallbackContractTests(unittest.TestCase):
    def test_frozen_project_without_genome_cache_requires_rebuild(self):
        kernel = NanometricKernel.__new__(NanometricKernel)
        entries, gap = kernel._project_cache_contract("MAIN", {})

        self.assertEqual(entries, [])
        self.assertEqual(gap, "missing_project_cache_entries")

    def test_regex_fallback_blocks_preserve_genome_cache_contract_fields(self):
        blocks = NanometricParser().sequence_nanometric_blocks(
            "export function meaningfulWork() { return 1; }",
            "src/meaningful-work.ts",
        )
        self.assertTrue(blocks)
        required = {
            "export_kind",
            "runtime_contract",
            "runtime_contract_kind",
            "modifiers",
            "member_side_effect_calls",
            "parser_version",
        }
        self.assertTrue(required.issubset(blocks[0]))


if __name__ == "__main__":
    unittest.main()

