import hashlib
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch


CODE_MAPS_DIR = Path(__file__).resolve().parents[2]
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))


from tools.core.watchdog_event_acquisition import classify_filesystem_event_batch
from tools.core.watchdog_runtime_contract import filesystem_event_acquisition_policy
from tools.orchestrators import watchdog


def _atlas_hash(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _atlas_file_hash(path: Path) -> str:
    with path.open("r", encoding="utf-8", errors="ignore", newline="") as source_file:
        return _atlas_hash(source_file.read())


def _event(path: Path, kind: str = "modify") -> dict:
    return {"path": str(path), "event_kind": kind, "related_path": "", "raw_event_count": 1}


def _provenance(*, raw: int = 1) -> dict:
    return {
        "observer_session_id": "fixture-observer",
        "observer_started_at": "2026-09-13T00:00:00+00:00",
        "pulse_sequence": 1,
        "seconds_since_observer_start": 1.0,
        "cold_start": True,
        "raw_event_count": raw,
        "event_ring": [],
    }


class WatchdogEventAcquisitionTests(unittest.TestCase):
    def test_matching_canonical_content_is_omitted_with_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "same.ts"
            content = "export const same = true;\n"
            target.write_text(content, encoding="utf-8")
            result = classify_filesystem_event_batch(
                [_event(target)],
                indexed_paths={target.resolve(): {"hash": _atlas_file_hash(target), "target_ref": "MAIN::same.ts"}},
                baseline={"status": "current", "snapshot_id": "snapshot"},
                policy=filesystem_event_acquisition_policy(),
                provenance=_provenance(),
            )

        self.assertEqual(result["selected_files"], [])
        self.assertEqual(result["scope_decision"]["status"], "no_content_change")
        self.assertEqual(result["scope_decision"]["unchanged_path_count"], 1)
        self.assertEqual(result["change_events"][0]["disposition"], "omit_unchanged")

    def test_changed_content_is_retained_for_analysis(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "changed.ts"
            target.write_text("export const value = 2;\n", encoding="utf-8")
            result = classify_filesystem_event_batch(
                [_event(target)],
                indexed_paths={target.resolve(): {"hash": _atlas_hash("export const value = 1;\n")}},
                baseline={"status": "current", "snapshot_id": "snapshot"},
                policy=filesystem_event_acquisition_policy(),
                provenance=_provenance(),
            )

        self.assertEqual(result["selected_files"], [str(target.resolve())])
        self.assertEqual(result["scope_decision"]["status"], "accepted")
        self.assertEqual(result["change_events"][0]["content_identity_status"], "differs_from_canonical_atlas")

    def test_small_unknown_scope_is_retained_instead_of_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "unknown.ts"
            target.write_text("export const value = 1;\n", encoding="utf-8")
            result = classify_filesystem_event_batch(
                [_event(target)],
                indexed_paths={target.resolve(): {"hash": _atlas_hash("export const value = 1;\n")}},
                baseline={"status": "unavailable", "reason": "atlas_commit_invalid"},
                policy=filesystem_event_acquisition_policy(),
                provenance=_provenance(),
            )

        self.assertEqual(result["selected_files"], [str(target.resolve())])
        self.assertEqual(result["scope_decision"]["unknown_path_count"], 1)
        self.assertFalse(result["scope_decision"]["silent_scope_truncation"])

    def test_ambiguous_large_scope_is_held_without_partial_analysis(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = []
            indexed = {}
            for index in range(3):
                path = root / f"unknown-{index}.ts"
                path.write_text(f"export const value{index} = {index};\n", encoding="utf-8")
                paths.append(path)
                indexed[path.resolve()] = {"hash": _atlas_hash(path.read_text(encoding="utf-8"))}
            policy = filesystem_event_acquisition_policy()
            policy["amplification_guard"] = dict(policy["amplification_guard"], candidate_path_threshold=3)
            result = classify_filesystem_event_batch(
                [_event(path) for path in paths],
                indexed_paths=indexed,
                baseline={"status": "unavailable", "reason": "atlas_commit_invalid"},
                policy=policy,
                provenance=_provenance(raw=3),
            )

        self.assertEqual(result["selected_files"], [])
        self.assertEqual(result["scope_decision"]["status"], "operator_confirmation_required")
        self.assertEqual(len(result["held_files"]), 3)
        self.assertFalse(result["scope_decision"]["silent_scope_truncation"])

    def test_missing_baseline_does_not_treat_unindexed_burst_as_verified_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = []
            for index in range(3):
                path = root / f"unindexed-{index}.ts"
                path.write_text(f"export const value{index} = {index};\n", encoding="utf-8")
                paths.append(path)
            policy = filesystem_event_acquisition_policy()
            policy["amplification_guard"] = dict(policy["amplification_guard"], candidate_path_threshold=3)
            result = classify_filesystem_event_batch(
                [_event(path, "create") for path in paths],
                indexed_paths={},
                baseline={"status": "unavailable", "reason": "atlas_commit_missing"},
                policy=policy,
                provenance=_provenance(raw=3),
            )

        self.assertEqual(result["selected_files"], [])
        self.assertEqual(result["scope_decision"]["status"], "operator_confirmation_required")
        self.assertEqual(result["scope_decision"]["unknown_path_count"], 3)
        self.assertEqual(len(result["held_files"]), 3)

    def test_verified_bulk_edit_preserves_every_changed_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = []
            indexed = {}
            for index in range(3):
                path = root / f"changed-{index}.ts"
                path.write_text(f"export const value{index} = 'new';\n", encoding="utf-8")
                paths.append(path)
                indexed[path.resolve()] = {"hash": _atlas_hash(f"export const value{index} = 'old';\n")}
            policy = filesystem_event_acquisition_policy()
            policy["amplification_guard"] = dict(policy["amplification_guard"], candidate_path_threshold=3)
            result = classify_filesystem_event_batch(
                [_event(path) for path in paths],
                indexed_paths=indexed,
                baseline={"status": "current", "snapshot_id": "snapshot"},
                policy=policy,
                provenance=_provenance(raw=3),
            )

        self.assertEqual(set(result["selected_files"]), {str(path.resolve()) for path in paths})
        self.assertEqual(result["scope_decision"]["status"], "accepted_verified_bulk")
        self.assertEqual(result["scope_decision"]["selected_path_count"], 3)

    def test_large_metadata_burst_admits_only_content_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = []
            indexed = {}
            for index in range(52):
                path = root / f"source-{index}.ts"
                path.write_text(f"export const value{index} = 'old';\n", encoding="utf-8")
                paths.append(path)
                indexed[path.resolve()] = {"hash": _atlas_file_hash(path)}
            for path in paths[-2:]:
                path.write_text(path.read_text(encoding="utf-8").replace("old", "new"), encoding="utf-8")
            result = classify_filesystem_event_batch(
                [_event(path) for path in paths],
                indexed_paths=indexed,
                baseline={"status": "current", "snapshot_id": "snapshot"},
                policy=filesystem_event_acquisition_policy(),
                provenance=_provenance(raw=52),
            )

        self.assertEqual(set(result["selected_files"]), {str(path.resolve()) for path in paths[-2:]})
        self.assertEqual(result["scope_decision"]["status"], "accepted_after_content_identity_filter")
        self.assertEqual(result["scope_decision"]["unchanged_path_count"], 50)
        self.assertEqual(result["scope_decision"]["selected_path_count"], 2)

    def test_handler_bounds_raw_event_ring_while_preserving_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "event.ts"
            target.write_text("export const event = true;\n", encoding="utf-8")
            handler = watchdog.CodeMapsHandler(debounce_seconds=60.0, display_root=root)
            for _ in range(205):
                handler._track_event_path(str(target))
            with handler.lock:
                if handler.timer:
                    handler.timer.cancel()
                    handler.timer = None
                raw_count = handler.raw_event_count
                ring_count = len(handler.raw_event_ring)
                deduplicated_count = len(handler.changed_files)

        self.assertEqual(raw_count, 205)
        self.assertEqual(ring_count, filesystem_event_acquisition_policy()["provenance_ring_max_events"])
        self.assertEqual(deduplicated_count, 1)

    def test_handler_metadata_only_batch_does_not_launch_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "same.ts"
            content = "export const same = true;\n"
            target.write_text(content, encoding="utf-8")
            handler = watchdog.CodeMapsHandler(debounce_seconds=60.0, display_root=root)
            handler.run_analysis = Mock()
            handler._record_acquisition_only_session = Mock()
            handler._track_event_path(str(target))
            with handler.lock:
                if handler.timer:
                    handler.timer.cancel()
                    handler.timer = None
            baseline = ({target.resolve(): {"hash": _atlas_file_hash(target)}}, {"status": "current"})
            with patch.object(watchdog, "_canonical_indexed_watch_baseline", return_value=baseline):
                handler.trigger_pipeline()

        handler.run_analysis.assert_not_called()
        handler._record_acquisition_only_session.assert_called_once()

    def test_handler_releases_running_state_when_baseline_resolution_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "failure.ts"
            target.write_text("export const failure = true;\n", encoding="utf-8")
            handler = watchdog.CodeMapsHandler(debounce_seconds=60.0, display_root=root)
            handler._track_event_path(str(target))
            with handler.lock:
                if handler.timer:
                    handler.timer.cancel()
                    handler.timer = None
            with (
                patch.object(watchdog, "_canonical_indexed_watch_baseline", side_effect=RuntimeError("fixture")),
                self.assertRaisesRegex(RuntimeError, "fixture"),
            ):
                handler.trigger_pipeline()

        self.assertFalse(handler.is_running)

    def test_sqlite_baseline_queries_only_requested_paths_without_loading_atlas(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "src" / "indexed.ts"
            target.parent.mkdir()
            target.write_text("export const indexed = true;\n", encoding="utf-8")
            expected_hash = _atlas_file_hash(target)
            db_path = root / "codemaps.db"
            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute(
                    "CREATE TABLE files (project_key TEXT, rel_path TEXT, hash TEXT, size_bytes INTEGER)"
                )
                conn.execute(
                    "INSERT INTO files VALUES (?, ?, ?, ?)",
                    ("MAIN", "src/indexed.ts", expected_hash, target.stat().st_size),
                )
                conn.commit()
            commit = {
                "meta": {"kind": "nexora.atlas_commit"},
                "state": "complete",
                "snapshot_id": "snapshot",
                "atlas_sha256": "atlas-sha",
                "counts": {"files": 1},
            }
            with (
                patch("tools.core.config.RAW_DIR", root),
                patch("tools.core.analysis_snapshot_lineage.load_atlas_commit", return_value=commit),
                patch(
                    "tools.core.json_io.raw_artifact_content_fingerprint",
                    return_value="sqlite:atlas-sha",
                ),
                patch("tools.core.projects_registry.resolve_runtime_projects", return_value={"MAIN": root}),
                patch("tools.core.projects_registry.resolve_project_for_path", return_value="MAIN"),
                patch.object(watchdog.orchestrator, "load_atlas_data", side_effect=AssertionError("full Atlas load")),
            ):
                indexed, baseline = watchdog._canonical_indexed_watch_baseline([str(target)])

        self.assertEqual(indexed[target.resolve()]["target_ref"], "MAIN::src/indexed.ts")
        self.assertEqual(indexed[target.resolve()]["hash"], expected_hash)
        self.assertEqual(baseline["status"], "current")
        self.assertEqual(baseline["truth_source"], "sqlite_state_payload_identity_plus_files_index")

    def test_sqlite_baseline_count_mismatch_falls_back_without_trusting_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "src" / "indexed.ts"
            target.parent.mkdir()
            target.write_text("export const indexed = true;\n", encoding="utf-8")
            db_path = root / "codemaps.db"
            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute(
                    "CREATE TABLE files (project_key TEXT, rel_path TEXT, hash TEXT, size_bytes INTEGER)"
                )
                conn.execute(
                    "INSERT INTO files VALUES (?, ?, ?, ?)",
                    ("MAIN", "src/indexed.ts", _atlas_file_hash(target), target.stat().st_size),
                )
                conn.commit()
            commit = {
                "meta": {"kind": "nexora.atlas_commit"},
                "state": "complete",
                "snapshot_id": "snapshot",
                "atlas_sha256": "atlas-sha",
                "counts": {"files": 2},
            }
            with (
                patch("tools.core.config.RAW_DIR", root),
                patch("tools.core.analysis_snapshot_lineage.load_atlas_commit", return_value=commit),
                patch(
                    "tools.core.json_io.raw_artifact_content_fingerprint",
                    return_value="sqlite:atlas-sha",
                ),
                patch("tools.core.projects_registry.resolve_runtime_projects", return_value={"MAIN": root}),
                patch("tools.core.projects_registry.resolve_project_for_path", return_value="MAIN"),
                patch.object(watchdog.orchestrator, "load_atlas_data", return_value={}) as load_atlas,
            ):
                indexed, baseline = watchdog._canonical_indexed_watch_baseline([str(target)])

        load_atlas.assert_called_once()
        self.assertEqual(indexed, {})
        self.assertEqual(baseline["status"], "unavailable")
        self.assertEqual(baseline["truth_source"], "unavailable")

    def test_empty_commit_does_not_trust_nonempty_sqlite_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "stale.ts"
            target.write_text("export const stale = true;\n", encoding="utf-8")
            with closing(sqlite3.connect(root / "codemaps.db")) as conn:
                conn.execute(
                    "CREATE TABLE files (project_key TEXT, rel_path TEXT, hash TEXT, size_bytes INTEGER)"
                )
                conn.execute(
                    "INSERT INTO files VALUES (?, ?, ?, ?)",
                    ("MAIN", "stale.ts", _atlas_file_hash(target), target.stat().st_size),
                )
                conn.commit()
            commit = {
                "meta": {"kind": "nexora.atlas_commit"},
                "state": "complete",
                "snapshot_id": "empty-snapshot",
                "atlas_sha256": "empty-atlas-sha",
                "counts": {"files": 0},
            }
            with (
                patch("tools.core.config.RAW_DIR", root),
                patch("tools.core.analysis_snapshot_lineage.load_atlas_commit", return_value=commit),
                patch(
                    "tools.core.json_io.raw_artifact_content_fingerprint",
                    return_value="sqlite:empty-atlas-sha",
                ),
                patch("tools.core.projects_registry.resolve_runtime_projects", return_value={"MAIN": root}),
                patch("tools.core.projects_registry.resolve_project_for_path", return_value="MAIN"),
                patch.object(watchdog.orchestrator, "load_atlas_data", return_value={}),
            ):
                indexed, baseline = watchdog._canonical_indexed_watch_baseline([str(target)])

        self.assertEqual(indexed, {})
        self.assertEqual(baseline["status"], "unavailable")

    def test_non_analysis_event_session_does_not_advance_or_resolve_pulse_ledger(self):
        handler = watchdog.CodeMapsHandler(debounce_seconds=60.0)
        acquisition = {
            "mode": "filesystem_events",
            "selected_existing_files": [],
            "selected_tombstones": [],
            "change_events": [],
            "omitted_existing_count": 1,
            "omitted_tombstone_count": 0,
            "tombstone_coverage_complete": True,
            "scope_decision": {"status": "no_content_change", "unchanged_path_count": 1},
            "filesystem_event_provenance": {
                "batch_id": "batch",
                "raw_event_count": 2,
                "deduplicated_path_count": 1,
                "event_ring": [],
            },
        }
        ledger_snapshot = {
            "status": "HAS_UNRESOLVED",
            "pulse_id": "prior-pulse",
            "unresolved_unread_count": 1,
            "proof_debt_state": {"status": "current", "thresholds": {}, "due_reasons": []},
        }
        descriptor = {
            "system_scope": "SAGE_ON_REPOSITORY",
            "acquisition_mode": "DEFAULT_WORKSPACE",
            "profile_id": "target_repository_default",
            "proof_debt_field": "target_repository_deep_proof_debt",
        }
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("tools.core.config.RAW_DIR", Path(tmp)),
            patch("tools.core.config.REPORTS_DIR", Path(tmp)),
            patch("tools.core.config.save_json_atomic"),
            patch("tools.core.config.save_text_atomic"),
            patch.object(watchdog, "_update_watchdog_pulse_ledger") as update,
            patch.object(watchdog, "_watchdog_pulse_ledger_snapshot", return_value=ledger_snapshot) as snapshot,
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
                [],
                0.0,
                [],
                {"status": "not_run", "scope_status": "empty", "scope_match": False},
                "UNKNOWN",
                input_origin="filesystem_event",
                acquisition=acquisition,
                analysis_status="no_content_change",
            )

        update.assert_not_called()
        snapshot.assert_called_once()
        self.assertEqual(session["summary"]["analysis_status"], "no_content_change")
        self.assertEqual(session["summary"]["raw_event_count"], 2)
        self.assertEqual(session["watchdog_pulse_ledger"]["pulse_id"], "prior-pulse")


if __name__ == "__main__":
    unittest.main()
