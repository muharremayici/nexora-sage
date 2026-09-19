import os
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


CODE_MAPS_DIR = Path(__file__).resolve().parents[2]
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))


from tools.core.watchdog_runtime_contract import (
    load_watchdog_runtime_contract,
    validate_watchdog_audit_scope,
    watchdog_artifact_path,
    watchdog_integrity_state,
)
from tools.core import atlas_io
from tools.core import changed_file_scope
from tools.core.contextos_mcp import render_active_signals
from tools.engines import audit as audit_engine
from tools.engines import quant_engine
from tools.orchestrators import watchdog
from tools.orchestrators.watchdog import (
    collect_smoke_file_selection,
    collect_smoke_files,
)
from tools.core.config import ROOT


class WatchdogRuntimeContractTests(unittest.TestCase):
    def _scoped_payload(self):
        return {
            "meta": {"kind": "watchdog_audit_report", "version": "test"},
            "audit_scope": {
                "scope_kind": "scoped_change",
                "full_repository_claim": False,
                "requested_file_count": 1,
                "requested_files": ["MAIN::src/App.tsx"],
                "audited_file_count": 1,
                "audited_files": ["MAIN::src/App.tsx"],
                "unresolved_requested_files": [],
                "scope_status": "complete",
            },
            "violations": [],
        }

    def test_watchdog_once_uses_bounded_synchronous_shadow_closeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "sample.ts"
            source.write_text("export const sample = true;\n", encoding="utf-8")
            handler = Mock()
            previous = os.environ.get("SAGE_SYNC_SHADOW_WRITES")
            try:
                with (
                    patch.object(
                        watchdog,
                        "collect_smoke_file_selection",
                        return_value={
                            "mode": "directory_sample",
                            "selected_files": [str(source)],
                            "candidate_count": 1,
                            "omitted_count": 0,
                        },
                    ),
                    patch.object(watchdog, "CodeMapsHandler", return_value=handler),
                    patch.object(watchdog, "ROOT", root),
                    patch.object(watchdog, "CODE_MAPS_DIR", root),
                    patch("tools.core.artifact_store.flush_shadow_writes", return_value=True) as flush,
                ):
                    result = watchdog.run_watchdog_once(root, 0.0)
            finally:
                if previous is None:
                    os.environ.pop("SAGE_SYNC_SHADOW_WRITES", None)
                else:
                    os.environ["SAGE_SYNC_SHADOW_WRITES"] = previous

        self.assertEqual(result, 0)
        self.assertEqual(os.environ.get("SAGE_SYNC_SHADOW_WRITES"), previous)
        handler.run_analysis.assert_called_once()
        self.assertEqual(handler.run_analysis.call_args.args[0], [str(source)])
        self.assertEqual(handler.run_analysis.call_args.kwargs["watchdog_profile"], "smoke")
        self.assertEqual(handler.run_analysis.call_args.kwargs["input_origin"], "smoke_sample")
        acquisition = handler.run_analysis.call_args.kwargs["acquisition"]
        self.assertEqual(acquisition["mode"], "directory_sample")
        self.assertEqual(acquisition["selected_files"], [str(source)])
        self.assertEqual(acquisition["requested_path_count"], 1)
        self.assertEqual(acquisition["duplicate_path_count"], 0)
        self.assertEqual(acquisition["rejected_path_count"], 0)
        flush.assert_called_once_with(timeout=10.0)

    def test_smoke_session_separates_samples_from_real_changed_files(self):
        handler = watchdog.CodeMapsHandler(debounce_seconds=0.0)
        pulse = {
            "status": "CLEAR",
            "unresolved_unread_count": 0,
            "proof_debt_state": {
                "status": "current",
                "thresholds": {},
                "due_reasons": [],
            }
        }
        descriptor = {
            "system_scope": "SAGE_ON_REPOSITORY",
            "acquisition_mode": "DEFAULT_WORKSPACE",
            "profile_id": "target_repository_default",
            "subject_root": str(CODE_MAPS_DIR.parent),
            "analysis_root": str(CODE_MAPS_DIR.parent),
            "artifact_strategy": "DEFAULT_WORKSPACE_NAMESPACE",
            "proof_debt_field": "target_repository_deep_proof_debt",
        }
        producer_command = ["python", "sage.py", "watch", "--once", "--path", "src"]
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("tools.core.config.RAW_DIR", Path(tmp)),
            patch("tools.core.config.REPORTS_DIR", Path(tmp)),
            patch.object(watchdog, "_update_watchdog_pulse_ledger", return_value=pulse) as ledger,
            patch.object(
                watchdog,
                "_watchdog_deep_proof_debt_policy",
                return_value={
                    "recommended_refresh_modes": ["auto_daily"],
                    "auto_refresh": {
                        "command_by_mode": {"auto_daily": ["run", "--profile", "daily"]},
                    },
                },
            ),
            patch("tools.core.config.save_json_atomic"),
            patch(
                "tools.core.watchdog_target_context.current_watchdog_target_descriptor",
                return_value=descriptor,
            ),
            patch("tools.core.governance_trace.record_watchdog_session_trace", side_effect=RuntimeError("test")),
            patch.dict(
                os.environ,
                {"SAGE_WATCHDOG_PRODUCER_COMMAND": json.dumps(producer_command)},
            ),
        ):
            session = handler.write_session_report(
                ["src/App.tsx"],
                1.0,
                [],
                {},
                "CLEAN_IN_SCOPE",
                input_origin="smoke_sample",
            )

        self.assertEqual(session["summary"]["input_origin"], "smoke_sample")
        self.assertEqual(session["summary"]["input_files"], 1)
        self.assertEqual(session["summary"]["changed_files"], 0)
        self.assertEqual(session["summary"]["sample_files"], 1)
        self.assertEqual(session["input_files"], ["src/App.tsx"])
        self.assertEqual(session["changed_files"], [])
        self.assertEqual(session["sample_files"], ["src/App.tsx"])
        self.assertEqual(session["target_descriptor"]["system_scope"], "SAGE_ON_REPOSITORY")
        self.assertEqual(session["target_descriptor"]["acquisition_mode"], "DEFAULT_WORKSPACE")
        self.assertEqual(session["target_descriptor"]["profile_id"], "target_repository_default")
        self.assertEqual(session["producer"]["kind"], "synthetic_smoke_validation")
        self.assertEqual(session["producer"]["command"], producer_command)
        self.assertEqual(
            session["proof_boundary"]["recommended_deep_proof"],
            "python sage.py run --profile daily",
        )
        self.assertEqual(ledger.call_args.args[1], [])

    def test_patched_raw_dir_fails_closed_before_live_store_singleton(self):
        from tools.core import artifact_store, config

        with tempfile.TemporaryDirectory() as tmp:
            isolated_raw = Path(tmp)
            isolated_path = isolated_raw / "watchdog_session.json"
            with (
                patch.object(config, "RAW_DIR", isolated_raw),
                patch.object(artifact_store.STORE, "save_raw") as live_store_write,
            ):
                with self.assertRaisesRegex(RuntimeError, "ArtifactStore binding"):
                    config.save_json_atomic(isolated_path, {"scope": "isolated_test"})

            live_store_write.assert_not_called()
            self.assertFalse(isolated_path.exists())
            config.save_json_atomic(
                isolated_path,
                {"scope": "isolated_test"},
                bypass_proxy=True,
            )
            self.assertEqual(
                json.loads(isolated_path.read_text(encoding="utf-8")),
                {"scope": "isolated_test"},
            )

    def test_session_path_resolves_parent_relative_watch_input_against_repository_root(self):
        from tools.core.config import ROOT

        relative_from_installation = os.path.relpath(ROOT / "src" / "App.tsx", CODE_MAPS_DIR)

        self.assertEqual(
            watchdog.CodeMapsHandler._session_repo_relative_path(relative_from_installation),
            "src/App.tsx",
        )

    def test_contract_resolves_distinct_scoped_audit_artifact(self):
        contract = load_watchdog_runtime_contract()

        self.assertEqual(contract["scope_transport"], "direct_pipeline_argument")
        self.assertEqual(watchdog_artifact_path("audit").name, "watchdog_audit_report.json")
        self.assertEqual(
            contract["artifacts"]["audit"]["canonical_overwrite_policy"],
            "forbidden",
        )
        self.assertEqual(watchdog_artifact_path("graph_advisories").name, "watchdog_graph_advisories.json")
        self.assertEqual(
            contract["artifacts"]["graph_advisories"]["canonical_overwrite_policy"],
            "forbidden",
        )

    def test_current_pulse_atlas_transport_preserves_sqlite_authority(self):
        transport = load_watchdog_runtime_contract()["current_pulse_atlas_transport"]

        self.assertEqual(transport["authority"], "sqlite_persisted_atlas")
        self.assertEqual(transport["lifetime"], "single_pipeline_execution")
        self.assertEqual(transport["consumer_access"], "read_only")
        self.assertEqual(transport["fallback"], "sqlite_first_artifact_store")

    def test_atlas_resolver_returns_same_runtime_payload_without_reload(self):
        atlas = {"MAIN": {"files": {}}}

        with patch.object(atlas_io, "load_atlas_data", side_effect=AssertionError("unexpected reload")):
            resolved, source = atlas_io.resolve_atlas_data(atlas)

        self.assertIs(resolved, atlas)
        self.assertEqual(source, "provided_runtime_payload")

    def test_atlas_resolver_falls_back_to_sqlite_first_store(self):
        stored = {"MAIN": {"files": {}}}

        with patch.object(atlas_io, "load_atlas_data", return_value=stored) as loader:
            resolved, source = atlas_io.resolve_atlas_data()

        self.assertIs(resolved, stored)
        self.assertEqual(source, "sqlite_first_artifact_store")
        loader.assert_called_once_with(None)

    def test_atlas_resolver_does_not_replace_provided_empty_truth_with_stale_store(self):
        atlas = {}

        with patch.object(atlas_io, "load_atlas_data", side_effect=AssertionError("stale fallback")):
            resolved, source = atlas_io.resolve_atlas_data(atlas)

        self.assertIs(resolved, atlas)
        self.assertEqual(source, "provided_runtime_payload")

    def test_watchdog_smoke_prioritizes_atlas_auditable_source_files(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "vite-env.d.ts").write_text("/// <reference types='vite/client' />", encoding="utf-8")
            (root / "package.json").write_text("{}", encoding="utf-8")
            (root / "App.tsx").write_text("export const App = () => null;", encoding="utf-8")

            selected = collect_smoke_files(root, limit=1)

        self.assertEqual([Path(item).name for item in selected], ["App.tsx"])

    def test_watchdog_smoke_sample_is_deterministic(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "z-last.ts").write_text("export const z = 1;", encoding="utf-8")
            (root / "a-first.ts").write_text("export const a = 1;", encoding="utf-8")

            selected = collect_smoke_files(root, limit=1)

        self.assertEqual([Path(item).name for item in selected], ["a-first.ts"])

    def test_watchdog_exact_file_is_selected_without_directory_sampling(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "z-target.ts"
            target.write_text("export const target = true;", encoding="utf-8")

            selection = collect_smoke_file_selection(target, limit=1)

        self.assertEqual(selection["mode"], "exact_file")
        self.assertEqual(selection["selected_files"], [str(target)])
        self.assertEqual(selection["candidate_count"], 1)
        self.assertEqual(selection["omitted_count"], 0)

    def test_watchdog_directory_selection_discloses_omitted_candidates(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for index in range(7):
                (root / f"{index}.ts").write_text(
                    f"export const value{index} = {index};",
                    encoding="utf-8",
                )

            selection = collect_smoke_file_selection(root, limit=5)

        self.assertEqual(selection["mode"], "directory_sample")
        self.assertEqual(len(selection["selected_files"]), 5)
        self.assertEqual(selection["candidate_count"], 7)
        self.assertEqual(selection["omitted_count"], 2)

    def test_watchdog_once_treats_exact_file_as_explicit_scope(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "target.ts"
            target.write_text("export const target = true;", encoding="utf-8")
            handler = Mock()
            with (
                patch.object(watchdog, "CodeMapsHandler", return_value=handler) as handler_factory,
                patch.object(watchdog, "ROOT", target.parent),
                patch.object(watchdog, "CODE_MAPS_DIR", target.parent),
                patch("tools.core.artifact_store.flush_shadow_writes", return_value=True),
            ):
                result = watchdog.run_watchdog_once(target, 0.0)

        self.assertEqual(result, 0)
        handler_factory.assert_called_once_with(
            debounce_seconds=0.0,
            display_root=target.parent,
            mode="ADVISE",
        )
        handler.run_analysis.assert_called_once_with(
            [str(target)],
            watchdog_profile="smoke",
            input_origin="explicit_scope",
            acquisition=handler.run_analysis.call_args.kwargs["acquisition"],
        )
        acquisition = handler.run_analysis.call_args.kwargs["acquisition"]
        self.assertEqual(acquisition["mode"], "exact_file")
        self.assertEqual(acquisition["change_events"][0]["event_kind"], "create")

    def test_watch_path_resolves_relative_to_analyzed_repository(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repository_root = Path(temp_dir) / "repository"
            installation_root = repository_root / "SAGE"
            target = repository_root / "src" / "target.ts"
            installation_root.mkdir(parents=True)
            target.parent.mkdir()
            target.write_text("export const target = true;", encoding="utf-8")

            with (
                patch.object(watchdog, "ROOT", repository_root),
                patch.object(watchdog, "CODE_MAPS_DIR", installation_root),
            ):
                resolved = watchdog.resolve_watch_input_path("src/target.ts")

        self.assertEqual(resolved, target.resolve())

    def test_watch_path_preserves_safe_legacy_installation_relative_form(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repository_root = Path(temp_dir) / "repository"
            installation_root = repository_root / "SAGE"
            target = repository_root / "src" / "target.ts"
            installation_root.mkdir(parents=True)
            target.parent.mkdir()
            target.write_text("export const target = true;", encoding="utf-8")

            with (
                patch.object(watchdog, "ROOT", repository_root),
                patch.object(watchdog, "CODE_MAPS_DIR", installation_root),
            ):
                resolved = watchdog.resolve_watch_input_path("../src/target.ts")

        self.assertEqual(resolved, target.resolve())

    def test_watch_path_rejects_repository_escape(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repository_root = Path(temp_dir) / "repository"
            installation_root = repository_root / "SAGE"
            installation_root.mkdir(parents=True)

            with (
                patch.object(watchdog, "ROOT", repository_root),
                patch.object(watchdog, "CODE_MAPS_DIR", installation_root),
            ):
                with self.assertRaisesRegex(ValueError, "inside the analyzed repository root"):
                    watchdog.resolve_watch_input_path("../../outside.ts")

    def test_scope_validation_rejects_previous_pulse_identity(self):
        validation = validate_watchdog_audit_scope(
            self._scoped_payload(),
            ["MAIN::src/Other.tsx"],
        )

        self.assertEqual(validation["status"], "valid")
        self.assertFalse(validation["request_match"])
        self.assertFalse(validation["scope_match"])
        self.assertEqual(watchdog_integrity_state([], validation), "UNKNOWN")
        self.assertEqual(
            watchdog_integrity_state([{"rule": "stale-example"}], validation),
            "UNKNOWN",
        )

    def test_scope_validation_distinguishes_clean_and_known_violation(self):
        validation = validate_watchdog_audit_scope(
            self._scoped_payload(),
            ["MAIN::src/App.tsx"],
        )

        self.assertTrue(validation["request_match"])
        self.assertTrue(validation["scope_match"])
        self.assertEqual(watchdog_integrity_state([], validation), "CLEAN_IN_SCOPE")
        self.assertEqual(
            watchdog_integrity_state([{"rule": "example"}], validation),
            "VIOLATIONS_IN_SCOPE",
        )

    def test_scope_validation_cannot_claim_clean_with_omitted_indexed_tombstone(self):
        validation = validate_watchdog_audit_scope(
            self._scoped_payload(),
            ["MAIN::src/App.tsx"],
        )
        validation["acquisition"] = {
            "omitted_tombstone_count": 1,
            "tombstone_coverage_complete": False,
        }

        self.assertEqual(watchdog_integrity_state([], validation), "UNKNOWN")

    def test_current_but_partial_scope_is_not_reported_as_complete_match(self):
        payload = self._scoped_payload()
        payload["audit_scope"].update(
            {
                "requested_file_count": 2,
                "requested_files": ["MAIN::src/App.tsx", "MAIN::src/vite-env.d.ts"],
                "audited_file_count": 1,
                "audited_files": ["MAIN::src/App.tsx"],
                "unresolved_requested_files": ["MAIN::src/vite-env.d.ts"],
                "scope_status": "partial",
            }
        )

        validation = validate_watchdog_audit_scope(
            payload,
            ["MAIN::src/App.tsx", "MAIN::src/vite-env.d.ts"],
        )

        self.assertTrue(validation["request_match"])
        self.assertFalse(validation["scope_match"])
        self.assertEqual(validation["reason"], "scope_incomplete")
        self.assertEqual(watchdog_integrity_state([], validation), "UNKNOWN")
        self.assertEqual(
            watchdog_integrity_state([{"rule": "partial-example"}], validation),
            "UNKNOWN",
        )

    def test_malformed_scope_counts_fail_closed_without_exception(self):
        payload = self._scoped_payload()
        payload["audit_scope"]["requested_file_count"] = "1"

        validation = validate_watchdog_audit_scope(payload, ["MAIN::src/App.tsx"])

        self.assertEqual(validation["status"], "invalid")
        self.assertFalse(validation["scope_match"])
        self.assertEqual(watchdog_integrity_state([], validation), "UNKNOWN")

    def test_agent_renderer_explains_excluded_managed_projections_without_actionable_signals(self):
        signals = {
            "analysis_root": str(CODE_MAPS_DIR.parent),
            "summary": {"rejected_files": {"managed_projection": 4}},
            "active_signals": [],
        }

        rendered = render_active_signals(
            signals,
            output_format="markdown",
            resolve_absolute_path=lambda rel_path, project_key: CODE_MAPS_DIR / rel_path,
        )

        self.assertIn("managed_projection_changes_excluded: 4", rendered)
        self.assertIn("No actionable modified files remain after managed projections were excluded.", rendered)
        self.assertIn("Do not edit excluded managed projection files directly.", rendered)

    def test_agent_renderer_keeps_managed_projection_exclusion_visible_with_source_targets(self):
        signals = {
            "analysis_root": str(CODE_MAPS_DIR.parent),
            "summary": {"rejected_files": {"managed_projection": 3}},
            "active_signals": [
                {
                    "node_key": "MAIN::Embedded-SAGE-Install/tools/core/config.py",
                    "relative_path": "Embedded-SAGE-Install/tools/core/config.py",
                    "target_ref": "MAIN::Embedded-SAGE-Install/tools/core/config.py",
                    "signal_kind": "source",
                    "direct_dependents": [],
                    "transitive_dependents": [],
                }
            ],
        }

        rendered = render_active_signals(
            signals,
            output_format="markdown",
            resolve_absolute_path=lambda rel_path, project_key: CODE_MAPS_DIR.parent / rel_path,
        )

        self.assertIn("Managed Projection Changes Excluded:** 3", rendered)
        self.assertIn("managed_projection_changes_excluded: 3", rendered)
        self.assertIn("Do not edit excluded managed projection files directly", rendered)

    def test_scoped_audit_does_not_flush_or_overwrite_full_reports(self):
        profile = {}
        scoped = {
            "requested_file_count": 1,
            "requested_files": ["MAIN::src/App.tsx"],
            "audited_file_count": 1,
            "audited_files": ["MAIN::src/App.tsx"],
            "unresolved_requested_files": [],
            "scope_status": "complete",
        }
        with (
            patch.object(audit_engine, "save_json_atomic") as save_json,
            patch.object(audit_engine, "save_text_atomic") as save_text,
            patch.object(audit_engine, "flush_shadow_writes") as flush_shadow,
            patch.object(audit_engine, "invalidate_audit_report_cache") as invalidate,
            patch.object(audit_engine, "watchdog_artifact_path", return_value=Path("watchdog_audit_report.json")),
        ):
            output = audit_engine._write_outputs(
                {},
                [],
                {"total": 0, "by_rule": {}},
                audited_projects=["MAIN"],
                atlas_project_count=1,
                atlas={"MAIN": {"files": {}}},
                profile_timings=profile,
                scoped_audit=scoped,
            )

        self.assertEqual(output, Path("watchdog_audit_report.json"))
        save_json.assert_called_once()
        save_text.assert_not_called()
        flush_shadow.assert_not_called()
        invalidate.assert_not_called()
        self.assertEqual(profile["shadow_flush_policy"], "deferred_nonblocking_scoped_artifact")

    def test_quant_direct_scope_never_rediscovers_previous_watchdog_or_git_scope(self):
        atlas = {
            "MAIN": {
                "project": {"root": str(ROOT)},
                "files": {"src/App.tsx": {}},
                "dependencies": {"src/App.tsx": []},
            }
        }
        audit_payload = {"violations": []}
        evidence = {
            "atlas": {
                "status": "available",
                "source": "sqlite_state_payloads",
                "shape_status": "valid",
                "payload_bytes": 1,
                "updated_at": "now",
            },
            "watchdog_audit_report": {
                "status": "available",
                "source": "sqlite_state_payloads",
                "shape_status": "valid",
                "payload_bytes": 1,
                "updated_at": "now",
            },
            "circular_deps": {
                "status": "deferred",
                "source": "watchdog_deep_proof_debt",
                "shape_status": "not_evaluated",
                "payload_bytes": 0,
                "updated_at": "",
            },
            "blast_radius": {
                "status": "deferred",
                "source": "watchdog_deep_proof_debt",
                "shape_status": "not_evaluated",
                "payload_bytes": 0,
                "updated_at": "",
            },
        }
        saved = {}
        saved_by_name = {}
        external_raw = Path("C:/sage/output/external_targets/hedef/.raw")
        bound_identity = {
            "status": "BOUND",
            "artifact_root": external_raw.as_posix(),
            "atlas_snapshot_id": "snapshot-test",
            "target_descriptor": {
                "system_scope": "SAGE_ON_REPOSITORY",
                "acquisition_mode": "EXTERNAL_TARGET",
                "profile_id": "target_repository_default",
                "subject_root": str(ROOT),
                "analysis_root": str(ROOT),
                "artifact_strategy": "EXTERNAL_TARGET_NAMESPACE",
                "external_target": True,
            },
        }
        with (
            patch.object(quant_engine, "RAW_DIR", external_raw),
            patch.object(quant_engine, "watchdog_artifact_identity", return_value=bound_identity),
            patch.object(
                quant_engine,
                "watchdog_artifact_path",
                side_effect=lambda role: Path(
                    "watchdog_audit_report.json"
                    if role == "audit"
                    else "watchdog_graph_advisories.json"
                ),
            ),
            patch.object(quant_engine, "load_json_file", return_value=audit_payload),
            patch.object(
                quant_engine,
                "evaluate_watchdog_quant_input_evidence",
                return_value=("PASS", [], ["circular_deps", "blast_radius"], evidence, {"scope_status": "complete"}),
            ),
            patch.object(
                quant_engine,
                "_filter_scoped_signal_files",
                return_value=(
                    [
                        {
                            "path": "src/App.tsx",
                            "kind": "source",
                            "node_key": "MAIN::src/App.tsx",
                            "atlas_rel_path": "src/App.tsx",
                            "project_key": "MAIN",
                        }
                    ],
                    {"non_file": 0, "unsupported_kind": 0, "duplicate": 0},
                ),
            ),
            patch.object(quant_engine, "get_changed_files_from_watchdog", side_effect=AssertionError("stale session read")),
            patch.object(quant_engine, "get_git_changed_files", side_effect=AssertionError("git scope rediscovery")),
            patch.object(
                quant_engine,
                "save_json_atomic",
                side_effect=lambda path, payload: saved_by_name.setdefault(Path(path).name, payload),
            ),
            patch.object(quant_engine, "write_current_atlas_lineage") as lineage,
            patch.object(quant_engine.logger, "info") as info,
        ):
            self.assertTrue(quant_engine.run_quant_engine(focus_files=["MAIN::src/App.tsx"], atlas=atlas))

        saved = saved_by_name["signals.json"]
        self.assertEqual(saved["meta"]["source_mode"], "direct_pipeline_scope")
        self.assertEqual(saved["meta"]["atlas_input_source"], "provided_runtime_payload")
        self.assertTrue(saved["meta"]["broad_proof_deferred"])
        self.assertEqual(saved["active_signals"][0]["target_ref"], "MAIN::src/App.tsx")
        self.assertEqual(saved["active_signals"][0]["impact_score"], 0.0)
        self.assertEqual(saved["active_signals"][0]["impact_score_status"], "scoped_dependency_lower_bound")
        self.assertEqual(saved["active_signals"][0]["circular_cycles_status"], "scoped_scc_membership")
        self.assertIn("watchdog_graph_advisories.json", saved_by_name)
        self.assertIs(lineage.call_args.kwargs["atlas"], atlas)
        self.assertIs(lineage.call_args.kwargs["artifact_payload"], saved)
        info.assert_any_call(
            "Surgically committed %s active Focus signals to %s successfully.",
            1,
            external_raw / "signals.json",
        )

    def test_broad_quant_derives_target_project_without_direct_scope_metadata(self):
        payloads = {
            "circular_deps.json": {
                "nodes": {"MAIN::src/App.tsx": {}},
                "edges": [],
                "cycles": [],
            },
            "blast_radius.json": {"blast_radius": []},
            "audit_report.json": {"violations": []},
        }
        input_evidence = {
            name: {
                "status": "available",
                "source": "sqlite_state_payloads",
                "shape_status": "valid",
                "payload_bytes": 1,
                "updated_at": "now",
            }
            for name in ("circular_deps", "blast_radius", "audit_report")
        }
        saved = {}
        lineage_atlas = {"MAIN": {"files": {"src/App.tsx": {}}}}

        def _load(path, default):
            return payloads.get(Path(path).name, default)

        with (
            patch.object(quant_engine, "load_json_file", side_effect=_load),
            patch.object(quant_engine, "_quant_input_evidence", return_value=("PASS", [], input_evidence)),
            patch.object(
                quant_engine,
                "get_changed_files_from_watchdog",
                return_value=(
                    ["src/App.tsx"],
                    quant_engine._change_scope_evidence("available", "watchdog_session.json", ["src/App.tsx"]),
                ),
            ),
            patch.object(
                quant_engine,
                "_filter_signal_files",
                return_value=([{"path": "src/App.tsx", "kind": "source"}], {"non_file": 0, "unsupported_kind": 0, "duplicate": 0}),
            ),
            patch.object(
                quant_engine,
                "resolve_atlas_data",
                return_value=(lineage_atlas, "sqlite_first_artifact_store"),
            ) as atlas_resolver,
            patch.object(quant_engine, "save_json_atomic", side_effect=lambda _path, payload: saved.update(payload)),
            patch.object(quant_engine, "write_current_atlas_lineage") as lineage,
        ):
            self.assertTrue(quant_engine.run_quant_engine())

        self.assertEqual(saved["active_signals"][0]["target_ref"], "MAIN::src/App.tsx")
        self.assertEqual(saved["meta"]["atlas_input_source"], "not_required_for_broad_artifact_projection")
        self.assertEqual(saved["meta"]["lineage_atlas_source"], "sqlite_first_artifact_store")
        atlas_resolver.assert_called_once_with(None)
        self.assertEqual(lineage.call_args.kwargs["atlas"], lineage_atlas)

    def test_scoped_quant_excludes_managed_clean_mirror_from_actionable_signals(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            mirror_root = Path(temp_dir) / "sage-clean-install"
            source_path = mirror_root / "tools" / "core" / "config.py"
            source_path.parent.mkdir(parents=True)
            (mirror_root / "CLEAN_INSTALL_NOTES.md").write_text("clean mirror\n", encoding="utf-8")
            source_path.write_text("VALUE = 1\n", encoding="utf-8")
            atlas = {
                "MIRROR": {
                    "project": {"root": str(mirror_root)},
                    "files": {
                        "tools/core/config.py": {
                            "repo_relative_path": "tools/core/config.py",
                        }
                    },
                }
            }

            accepted, rejected = quant_engine._filter_scoped_signal_files(
                ["MIRROR::tools/core/config.py"],
                atlas,
            )

        self.assertEqual(accepted, [])
        self.assertEqual(rejected["managed_projection"], 1)

    def test_git_probe_failure_is_unavailable_not_empty_change_scope(self):
        failed = type("Result", (), {"returncode": 128, "stdout": "", "stderr": "unsafe repository"})()
        with patch.object(changed_file_scope, "run_observed_subprocess", return_value=(failed, 0.1)) as run_probe:
            files, evidence = quant_engine.get_git_changed_files()

        self.assertEqual(files, [])
        self.assertEqual(evidence["status"], "unavailable")
        self.assertEqual(evidence["reason"], "one_or_more_git_probes_failed")
        self.assertEqual(evidence["probes"][0]["stderr"], "unsafe repository")
        self.assertEqual(
            [call.kwargs["label"] for call in run_probe.call_args_list],
            ["quant_git_diff_unstaged", "quant_git_diff_cached", "quant_git_status_porcelain"],
        )

    def test_watchdog_change_scope_uses_sqlite_truth_without_json_shadow(self):
        with (
            patch.object(
                changed_file_scope,
                "artifact_state_meta",
                return_value={
                    "exists": True,
                    "source": "sqlite_state_payloads",
                    "mtime": 0.0,
                    "updated_at": "2026-07-23T07:00:00+00:00",
                },
            ),
            patch.object(
                changed_file_scope,
                "load_json_file",
                return_value={"changed_files": ["src/App.tsx"]},
            ) as loader,
        ):
            files, evidence = changed_file_scope.get_changed_files_from_watchdog()

        self.assertEqual(files, ["src/App.tsx"])
        self.assertEqual(evidence["status"], "available")
        self.assertEqual(evidence["truth_source"], "sqlite_state_payloads")
        self.assertEqual(evidence["updated_at"], "2026-07-23T07:00:00+00:00")
        loader.assert_called_once()

    def test_broad_quant_marks_unavailable_change_scope_partial(self):
        payloads = {
            "circular_deps.json": {"nodes": {}, "edges": [], "cycles": []},
            "blast_radius.json": {"blast_radius": []},
            "audit_report.json": {"violations": []},
        }
        input_evidence = {
            name: {
                "status": "available",
                "source": "sqlite_state_payloads",
                "shape_status": "valid",
                "payload_bytes": 1,
                "updated_at": "now",
            }
            for name in ("circular_deps", "blast_radius", "audit_report")
        }
        unavailable = quant_engine._change_scope_evidence(
            "unavailable",
            "git_changed_file_probes",
            [],
            reason="one_or_more_git_probes_failed",
        )
        saved = {}

        with (
            patch.object(quant_engine, "load_json_file", side_effect=lambda path, default: payloads.get(Path(path).name, default)),
            patch.object(quant_engine, "_quant_input_evidence", return_value=("PASS", [], input_evidence)),
            patch.object(
                quant_engine,
                "get_changed_files_from_watchdog",
                return_value=([], quant_engine._change_scope_evidence("unavailable", "watchdog_session.json", [])),
            ),
            patch.object(quant_engine, "get_git_changed_files", return_value=([], unavailable)),
            patch.object(quant_engine, "_filter_signal_files", return_value=([], {"non_file": 0, "unsupported_kind": 0, "duplicate": 0})),
            patch.object(quant_engine, "save_json_atomic", side_effect=lambda _path, payload: saved.update(payload)),
            patch.object(quant_engine, "write_current_atlas_lineage"),
        ):
            self.assertTrue(quant_engine.run_quant_engine())

        self.assertEqual(saved["meta"]["input_evidence_status"], "PARTIAL")
        self.assertEqual(saved["input_evidence"]["change_scope"]["status"], "unavailable")
        self.assertIn("change_scope", saved["summary"]["unavailable_inputs"])

    def test_broad_quant_propagates_stale_dependency_evidence_downstream(self):
        input_chain = {
            "id": "contextos_quant_input_chain",
            "required_artifacts": ["circular_deps", "blast_radius", "audit_report"],
            "artifacts": [
                {"artifact": name, "exists": True, "source": "sqlite_state_payloads", "payload_bytes": 10, "updated_at": "now"}
                for name in ("circular_deps", "blast_radius", "audit_report")
            ],
        }
        dependency_chain = {
            "id": "contextos_quant_dependency_graph_chain",
            "ordered_artifacts": ["atlas", "circular_deps", "blast_radius"],
            "missing_artifacts": [],
            "stale_edges": [{"producer": "atlas", "consumer": "circular_deps"}],
        }
        audit_chain = {
            "id": "contextos_quant_audit_chain",
            "ordered_artifacts": ["atlas", "audit_report"],
            "missing_artifacts": [],
            "stale_edges": [],
        }
        payloads = {
            "circular_deps": {"edges": [], "cycles": []},
            "blast_radius": {"blast_radius": []},
            "audit_report": {"violations": []},
        }
        with patch.object(
            quant_engine,
            "evaluate_named_artifact_chain",
            side_effect=[input_chain, dependency_chain, audit_chain],
        ):
            status, unavailable, evidence = quant_engine._quant_input_evidence(payloads)

        self.assertEqual(status, "PARTIAL")
        self.assertEqual(set(unavailable), {"circular_deps", "blast_radius"})
        self.assertEqual(evidence["circular_deps"]["status"], "unavailable")
        self.assertEqual(evidence["blast_radius"]["status"], "unavailable")
        self.assertEqual(evidence["audit_report"]["status"], "available")


if __name__ == "__main__":
    unittest.main()
