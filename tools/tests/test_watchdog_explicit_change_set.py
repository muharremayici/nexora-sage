from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


CODE_MAPS_DIR = Path(__file__).resolve().parents[2]
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))


from tools.core.watchdog_runtime_contract import explicit_change_set_policy
from tools.mcp import server
from tools.orchestrators import watchdog
from tools.orchestrators.watchdog import collect_watch_input_selection


class WatchdogExplicitChangeSetTests(unittest.TestCase):
    def test_keeps_production_and_test_in_one_pulse(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "src" / "feature.ts"
            test_file = root / "src" / "feature.test.ts"
            source.parent.mkdir()
            source.write_text("export const feature = true;", encoding="utf-8")
            test_file.write_text("export const testFeature = true;", encoding="utf-8")
            handler = Mock()
            baseline = (
                {
                    source.resolve(): {"target_ref": "MAIN::src/feature.ts"},
                    test_file.resolve(): {"target_ref": "MAIN::src/feature.test.ts"},
                },
                {"status": "current", "snapshot_id": "snapshot"},
            )
            with (
                patch.object(watchdog, "ROOT", root),
                patch.object(watchdog, "CODE_MAPS_DIR", root),
                patch.object(watchdog, "_canonical_indexed_watch_baseline", return_value=baseline),
                patch.object(watchdog, "CodeMapsHandler", return_value=handler),
                patch("tools.core.artifact_store.flush_shadow_writes", return_value=True),
            ):
                result = watchdog.run_watchdog_once([source, test_file], 0.0)

        self.assertEqual(result, 0)
        selected = handler.run_analysis.call_args.args[0]
        acquisition = handler.run_analysis.call_args.kwargs["acquisition"]
        self.assertEqual(selected, [str(source.resolve()), str(test_file.resolve())])
        self.assertEqual(handler.run_analysis.call_args.kwargs["input_origin"], "explicit_change_set")
        self.assertEqual(acquisition["mode"], "explicit_change_set")
        self.assertEqual(acquisition["requested_path_count"], 2)
        self.assertEqual(acquisition["rejected_path_count"], 0)

    def test_deduplicates_canonical_paths_with_receipt(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "src" / "feature.ts"
            source.parent.mkdir()
            source.write_text("export const feature = true;", encoding="utf-8")
            baseline = (
                {source.resolve(): {"target_ref": "MAIN::src/feature.ts"}},
                {"status": "current", "snapshot_id": "snapshot"},
            )
            with (
                patch.object(watchdog, "ROOT", root),
                patch.object(watchdog, "CODE_MAPS_DIR", root),
                patch.object(watchdog, "_canonical_indexed_watch_baseline", return_value=baseline),
            ):
                selection = collect_watch_input_selection(["src/feature.ts", str(source.resolve())])

        self.assertEqual(selection["status"], "accepted")
        self.assertEqual(selection["selected_files"], [str(source.resolve())])
        self.assertEqual(selection["duplicate_path_count"], 1)
        self.assertEqual(selection["duplicate_entries"][0]["reason"], "duplicate_canonical_path")

    def test_rejects_everything_when_one_entry_is_invalid(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "feature.ts"
            unsupported = root / "notes.txt"
            source.write_text("export const feature = true;", encoding="utf-8")
            unsupported.write_text("not source", encoding="utf-8")
            with (
                patch.object(watchdog, "ROOT", root),
                patch.object(watchdog, "CODE_MAPS_DIR", root),
                patch.object(
                    watchdog,
                    "_canonical_indexed_watch_baseline",
                    return_value=({}, {"status": "current", "snapshot_id": "snapshot"}),
                ),
            ):
                selection = collect_watch_input_selection([source, unsupported])

        self.assertEqual(selection["status"], "rejected")
        self.assertEqual(selection["selected_files"], [])
        self.assertEqual(selection["candidate_files"], [str(source.resolve())])
        self.assertEqual(selection["rejected_entries"][0]["reason"], "unsupported_analysis_source")
        self.assertFalse(selection["silent_scope_truncation"])

    def test_rejects_directory_and_root_escape(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "repo"
            outside = Path(temp_dir) / "outside.ts"
            source = root / "feature.ts"
            root.mkdir()
            source.write_text("export const feature = true;", encoding="utf-8")
            outside.write_text("export const outside = true;", encoding="utf-8")
            with (
                patch.object(watchdog, "ROOT", root),
                patch.object(watchdog, "CODE_MAPS_DIR", root),
                patch.object(
                    watchdog,
                    "_canonical_indexed_watch_baseline",
                    return_value=({}, {"status": "current", "snapshot_id": "snapshot"}),
                ),
            ):
                selection = collect_watch_input_selection([source, root, outside])

        reasons = {row["reason"] for row in selection["rejected_entries"]}
        self.assertEqual(selection["status"], "rejected")
        self.assertEqual(selection["selected_files"], [])
        self.assertEqual(reasons, {"directory_not_allowed_in_explicit_change_set", "repository_root_escape"})

    def test_requires_current_atlas_for_tombstone(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "feature.ts"
            missing = root / "removed.ts"
            source.write_text("export const feature = true;", encoding="utf-8")
            with (
                patch.object(watchdog, "ROOT", root),
                patch.object(watchdog, "CODE_MAPS_DIR", root),
                patch.object(
                    watchdog,
                    "_canonical_indexed_watch_baseline",
                    return_value=(
                        {missing.resolve(): {"target_ref": "MAIN::removed.ts"}},
                        {"status": "unavailable", "reason": "atlas_commit_invalid"},
                    ),
                ),
            ):
                selection = collect_watch_input_selection([source, missing])

        self.assertEqual(selection["status"], "rejected")
        self.assertEqual(
            selection["rejected_entries"][0]["reason"],
            "canonical_atlas_baseline_unavailable_for_tombstone",
        )

    def test_accepts_current_atlas_tombstone_with_existing_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "feature.ts"
            missing = root / "removed.ts"
            source.write_text("export const feature = true;", encoding="utf-8")
            with (
                patch.object(watchdog, "ROOT", root),
                patch.object(watchdog, "CODE_MAPS_DIR", root),
                patch.object(
                    watchdog,
                    "_canonical_indexed_watch_baseline",
                    return_value=(
                        {missing.resolve(): {"target_ref": "MAIN::removed.ts"}},
                        {"status": "current", "snapshot_id": "snapshot"},
                    ),
                ),
            ):
                selection = collect_watch_input_selection([source, missing])

        self.assertEqual(selection["status"], "accepted")
        self.assertEqual(selection["mode"], "explicit_change_set")
        self.assertEqual(selection["selected_files"], [str(source.resolve()), str(missing.resolve())])
        self.assertEqual(selection["selected_tombstones"], [str(missing.resolve())])
        self.assertEqual([row["event_kind"] for row in selection["change_events"]], ["create", "delete"])

    def test_bound_rejects_without_resolving_or_truncating(self):
        maximum = explicit_change_set_policy()["max_paths"]
        requested = [f"src/file-{index}.ts" for index in range(maximum + 1)]
        with patch.object(watchdog, "resolve_watch_input_path", side_effect=AssertionError("must reject first")):
            selection = collect_watch_input_selection(requested)

        self.assertEqual(selection["status"], "rejected")
        self.assertEqual(selection["reason"], "max_explicit_paths_exceeded")
        self.assertEqual(selection["requested_path_count"], maximum + 1)
        self.assertEqual(selection["selected_files"], [])

    def test_session_persists_aggregate_explicit_change_set_receipt(self):
        handler = watchdog.CodeMapsHandler(debounce_seconds=0.0)
        pulse = {
            "status": "CLEAR",
            "unresolved_unread_count": 0,
            "proof_debt_state": {"status": "current", "thresholds": {}, "due_reasons": []},
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
        acquisition = {
            "mode": "explicit_change_set",
            "requested_path_count": 3,
            "duplicate_path_count": 1,
            "rejected_path_count": 0,
            "selected_existing_files": ["src/feature.ts", "src/feature.test.ts"],
            "selected_tombstones": [],
            "duplicate_entries": [
                {
                    "path": "src/../src/feature.ts",
                    "canonical_path": "src/feature.ts",
                    "reason": "duplicate_canonical_path",
                }
            ],
        }
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("tools.core.config.RAW_DIR", Path(tmp)),
            patch("tools.core.config.REPORTS_DIR", Path(tmp)),
            patch.object(watchdog, "_update_watchdog_pulse_ledger", return_value=pulse),
            patch.object(
                watchdog,
                "_watchdog_deep_proof_debt_policy",
                return_value={"recommended_refresh_modes": []},
            ),
            patch("tools.core.config.save_json_atomic"),
            patch("tools.core.config.save_text_atomic"),
            patch(
                "tools.core.watchdog_target_context.current_watchdog_target_descriptor",
                return_value=descriptor,
            ),
            patch(
                "tools.core.governance_trace.record_watchdog_session_trace",
                return_value={"trace_id": "trace", "claim_boundary": "surgical_change_context"},
            ),
        ):
            session = handler.write_session_report(
                ["src/feature.ts", "src/feature.test.ts"],
                0.1,
                [],
                {},
                "CLEAN_IN_SCOPE",
                input_origin="explicit_change_set",
                acquisition=acquisition,
            )

        self.assertEqual(session["producer"]["kind"], "explicit_change_set_invocation")
        self.assertEqual(session["summary"]["requested_path_count"], 3)
        self.assertEqual(session["summary"]["duplicate_path_count"], 1)
        self.assertEqual(session["summary"]["rejected_path_count"], 0)
        self.assertEqual(session["acquisition"]["duplicate_entries"][0]["reason"], "duplicate_canonical_path")

    def test_mcp_reader_projects_explicit_change_set_receipt(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            raw_dir = Path(temp_dir)
            (raw_dir / "watchdog_session.json").write_text("{}", encoding="utf-8")
            session = {
                "meta": {"generated_at": "2026-09-13T00:00:00+00:00"},
                "summary": {
                    "input_origin": "explicit_change_set",
                    "analysis_status": "completed",
                    "input_files": 2,
                    "changed_files": 2,
                    "requested_path_count": 3,
                    "duplicate_path_count": 1,
                    "rejected_path_count": 0,
                    "integrity": "CLEAN_IN_SCOPE",
                },
                "target_descriptor": {
                    "system_scope": "SAGE_ON_REPOSITORY",
                    "acquisition_mode": "DEFAULT_WORKSPACE",
                    "profile_id": "target_repository_default",
                    "subject_root": str(raw_dir),
                    "artifact_strategy": "primary_workspace",
                    "proof_debt_field": "target_repository_deep_proof_debt",
                },
                "producer": {
                    "kind": "explicit_change_set_invocation",
                    "command": ["python", "sage.py", "watch", "--once"],
                },
                "proof_boundary": {"recommended_deep_proof": "python sage.py run --profile daily"},
                "path_contract": {"analysis_root": str(raw_dir)},
                "input_origin": "explicit_change_set",
                "input_files": ["src/feature.ts", "src/feature.test.ts"],
                "changed_files": ["src/feature.ts", "src/feature.test.ts"],
                "sample_files": [],
                "acquisition": {
                    "mode": "explicit_change_set",
                    "requested_path_count": 3,
                    "duplicate_path_count": 1,
                    "rejected_path_count": 0,
                },
                "target_repository_deep_proof_debt": {"status": "current"},
                "watchdog_pulse_ledger": {"status": "CLEAR", "unresolved_unread_count": 0},
                "violations": [],
            }

            def load_fixture(path):
                return session if Path(path).name == "watchdog_session.json" else {}

            with (
                patch.object(server, "_watchdog_session_roots", return_value=(raw_dir, raw_dir, "")),
                patch.object(server, "_load_json", side_effect=load_fixture),
                patch.object(server, "_raw_artifact_source_mtime", return_value=1.0),
                patch.object(
                    server,
                    "_record_mcp_call_result",
                    side_effect=lambda _name, _started, result, **_kwargs: result,
                ),
            ):
                rendered = server.get_watchdog_session()

        self.assertIn("status: watchdog_session_available", rendered)
        self.assertIn("requested_path_count: 3", rendered)
        self.assertIn("duplicate_path_count: 1", rendered)
        self.assertIn("rejected_path_count: 0", rendered)


if __name__ == "__main__":
    unittest.main()
