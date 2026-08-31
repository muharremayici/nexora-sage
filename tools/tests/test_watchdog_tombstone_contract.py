import tempfile
import unittest
from json import dumps
from pathlib import Path
from unittest.mock import Mock, patch

from tools.engines import generate_atlas as generate_atlas_module
from tools.orchestrators import watchdog
from tools.orchestrators.watchdog import collect_smoke_file_selection


class WatchdogTombstoneContractTests(unittest.TestCase):
    @staticmethod
    def _indexed_atlas(project_root: Path) -> dict:
        return {
            "MAIN": {
                "project": {
                    "name": "MAIN",
                    "language": "typescript",
                    "framework": "React/Next.js/Vite",
                    "root": str(project_root),
                    "ast_contract_version": generate_atlas_module.AST_CONTRACT_VERSION,
                },
                "structure": {
                    "deleted.ts": {},
                    "surviving": {"sibling.ts": {}},
                },
                "files": {
                    "deleted.ts": {
                        "hash": "old",
                        "symbols": [],
                        "features": [],
                        "internal_deps": [],
                    }
                },
                "dependencies": {"deleted.ts": []},
                "symbols": [],
                "features": {},
                "clusters": {},
                "public_contracts": {},
            }
        }

    def test_real_atlas_producer_processes_tombstone_only_scope_for_default_and_external_roots(self):
        for acquisition_mode in ("default", "external"):
            with self.subTest(acquisition_mode=acquisition_mode), tempfile.TemporaryDirectory() as temp_dir:
                repository_root = Path(temp_dir) / acquisition_mode
                project_root = repository_root / "src"
                project_root.mkdir(parents=True)
                previous = self._indexed_atlas(project_root)
                runtime_projection = (
                    {"_target_root_override": {"target_root": str(repository_root)}}
                    if acquisition_mode == "external"
                    else {}
                )
                with (
                    patch.object(generate_atlas_module, "ROOT", repository_root),
                    patch.object(generate_atlas_module, "resolve_runtime_projects", return_value={"MAIN": project_root}),
                    patch.object(generate_atlas_module, "project_ownership_exclusions", return_value={"MAIN": []}),
                    patch.object(generate_atlas_module, "load_previous_atlas", return_value=previous),
                    patch.object(generate_atlas_module, "ensure_output_dir"),
                    patch.dict(generate_atlas_module.DYNAMIC_CONFIG, runtime_projection, clear=True),
                ):
                    atlas, changed_files, dna_changed_files = generate_atlas_module.generate_atlas(
                        stale_projects=["MAIN"],
                        dry_run=True,
                        surgical_files=["MAIN::deleted.ts"],
                    )

                self.assertNotIn("deleted.ts", atlas["MAIN"]["files"])
                self.assertNotIn("deleted.ts", atlas["MAIN"]["dependencies"])
                self.assertNotIn("deleted.ts", atlas["MAIN"]["structure"])
                self.assertEqual(
                    atlas["MAIN"]["structure"]["surviving"],
                    {"sibling.ts": {}},
                )
                self.assertEqual(changed_files, ["MAIN::deleted.ts"])
                self.assertEqual(dna_changed_files, ["MAIN::deleted.ts"])

    def test_real_atlas_producer_prunes_empty_structure_parents_semantically(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repository_root = Path(temp_dir) / "repository"
            project_root = repository_root / "src"
            project_root.mkdir(parents=True)
            previous = self._indexed_atlas(project_root)
            previous["MAIN"]["files"] = {
                "nested/empty/deleted.ts": previous["MAIN"]["files"].pop("deleted.ts")
            }
            previous["MAIN"]["dependencies"] = {"nested/empty/deleted.ts": []}
            previous["MAIN"]["structure"] = {
                "nested": {"empty": {"deleted.ts": {}}},
                "surviving": {"sibling.ts": {}},
            }

            with (
                patch.object(generate_atlas_module, "ROOT", repository_root),
                patch.object(generate_atlas_module, "resolve_runtime_projects", return_value={"MAIN": project_root}),
                patch.object(generate_atlas_module, "project_ownership_exclusions", return_value={"MAIN": []}),
                patch.object(generate_atlas_module, "load_previous_atlas", return_value=previous),
                patch.object(generate_atlas_module, "ensure_output_dir"),
                patch.dict(generate_atlas_module.DYNAMIC_CONFIG, {}, clear=True),
            ):
                atlas, changed_files, _dna_changed_files = generate_atlas_module.generate_atlas(
                    stale_projects=["MAIN"],
                    dry_run=True,
                    surgical_files=["MAIN::nested/empty/deleted.ts"],
                )

        self.assertNotIn("nested", atlas["MAIN"]["structure"])
        self.assertEqual(atlas["MAIN"]["structure"]["surviving"], {"sibling.ts": {}})
        self.assertEqual(changed_files, ["MAIN::nested/empty/deleted.ts"])

    def test_public_watchdog_once_routes_exact_tombstone_through_real_atlas_producer(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repository_root = Path(temp_dir) / "repository"
            project_root = repository_root / "src"
            project_root.mkdir(parents=True)
            target = (project_root / "deleted.ts").resolve()
            previous = self._indexed_atlas(project_root)
            indexed = {
                target: {
                    "project": "MAIN",
                    "rel_path": "deleted.ts",
                    "target_ref": "MAIN::deleted.ts",
                }
            }
            observed = {}

            def run_real_producer(files, watchdog_profile):
                self.assertEqual(files, [str(target)])
                self.assertEqual(watchdog_profile, "smoke")
                with (
                    patch.object(generate_atlas_module, "ROOT", repository_root),
                    patch.object(generate_atlas_module, "resolve_runtime_projects", return_value={"MAIN": project_root}),
                    patch.object(generate_atlas_module, "project_ownership_exclusions", return_value={"MAIN": []}),
                    patch.object(generate_atlas_module, "load_previous_atlas", return_value=previous),
                    patch.object(generate_atlas_module, "ensure_output_dir"),
                    patch.dict(generate_atlas_module.DYNAMIC_CONFIG, {}, clear=True),
                ):
                    observed["result"] = generate_atlas_module.generate_atlas(
                        stale_projects=["MAIN"],
                        dry_run=True,
                        surgical_files=["MAIN::deleted.ts"],
                    )

            with (
                patch.object(watchdog, "ROOT", repository_root),
                patch.object(watchdog, "CODE_MAPS_DIR", repository_root / "SAGE"),
                patch.object(watchdog, "_canonical_indexed_watch_paths", return_value=indexed),
                patch.object(watchdog, "_run_surgical_pipeline_with_heartbeats", side_effect=run_real_producer),
                patch.object(watchdog.orchestrator, "normalize_changed_file_scope", return_value=["MAIN::deleted.ts"]),
                patch.object(watchdog, "load_json_file", return_value={"violations": []}),
                patch.object(
                    watchdog,
                    "validate_watchdog_audit_scope",
                    return_value={
                        "status": "valid",
                        "scope_match": True,
                        "scope_status": "complete",
                        "reason": "scope_complete",
                    },
                ),
                patch.object(watchdog, "watchdog_artifact_path", return_value=repository_root / "watchdog_audit.json"),
                patch.object(watchdog.CodeMapsHandler, "display_summary"),
                patch("tools.core.artifact_store.flush_shadow_writes", return_value=True),
            ):
                result = watchdog.run_watchdog_once(target, 0.0)

        self.assertEqual(result, 0)
        atlas, changed_files, dna_changed_files = observed["result"]
        self.assertNotIn("deleted.ts", atlas["MAIN"]["files"])
        self.assertNotIn("deleted.ts", atlas["MAIN"]["structure"])
        self.assertEqual(changed_files, ["MAIN::deleted.ts"])
        self.assertEqual(dna_changed_files, ["MAIN::deleted.ts"])

    def test_real_atlas_producer_projects_rename_as_linked_delete_and_create(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repository_root = Path(temp_dir) / "repository"
            project_root = repository_root / "src"
            project_root.mkdir(parents=True)
            renamed = project_root / "renamed.ts"
            renamed.write_text("export const renamed = true;\n", encoding="utf-8")
            previous = self._indexed_atlas(project_root)

            def observed_node_result(command, **_kwargs):
                if "--batch-json" in command:
                    return Mock(returncode=0, stdout="{}", stderr=""), 0.01
                return (
                    Mock(
                        returncode=0,
                        stdout=dumps(
                            [
                                {
                                    "name": "__file_meta__",
                                    "parserStatus": "observed",
                                    "parserDiagnosticCount": 0,
                                }
                            ]
                        ),
                        stderr="",
                    ),
                    0.01,
                )

            with (
                patch.object(generate_atlas_module, "ROOT", repository_root),
                patch.object(generate_atlas_module, "resolve_runtime_projects", return_value={"MAIN": project_root}),
                patch.object(generate_atlas_module, "project_ownership_exclusions", return_value={"MAIN": []}),
                patch.object(generate_atlas_module, "load_previous_atlas", return_value=previous),
                patch.object(generate_atlas_module, "ensure_output_dir"),
                patch.object(generate_atlas_module, "run_observed_subprocess", side_effect=observed_node_result),
                patch.dict(generate_atlas_module.DYNAMIC_CONFIG, {}, clear=True),
            ):
                atlas, changed_files, _dna_changed_files = generate_atlas_module.generate_atlas(
                    stale_projects=["MAIN"],
                    dry_run=True,
                    surgical_files=["MAIN::deleted.ts", "MAIN::renamed.ts"],
                )

        self.assertNotIn("deleted.ts", atlas["MAIN"]["files"])
        self.assertIn("renamed.ts", atlas["MAIN"]["files"])
        self.assertNotIn("deleted.ts", atlas["MAIN"]["structure"])
        self.assertIn("renamed.ts", atlas["MAIN"]["structure"])
        self.assertEqual(changed_files, ["MAIN::deleted.ts", "MAIN::renamed.ts"])

    def test_watchdog_once_accepts_previously_indexed_missing_file_as_tombstone(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = (root / "deleted.ts").resolve()
            indexed = {
                target: {
                    "project": "MAIN",
                    "rel_path": "deleted.ts",
                    "target_ref": "MAIN::deleted.ts",
                }
            }
            handler = Mock()
            with (
                patch.object(watchdog, "ROOT", root),
                patch.object(watchdog, "CODE_MAPS_DIR", root),
                patch.object(watchdog, "_canonical_indexed_watch_paths", return_value=indexed),
                patch.object(watchdog, "CodeMapsHandler", return_value=handler),
                patch("tools.core.artifact_store.flush_shadow_writes", return_value=True),
            ):
                result = watchdog.run_watchdog_once(target, 0.0)

        self.assertEqual(result, 0)
        acquisition = handler.run_analysis.call_args.kwargs["acquisition"]
        self.assertEqual(acquisition["mode"], "exact_tombstone")
        self.assertEqual(acquisition["selected_tombstones"], [str(target)])
        self.assertEqual(acquisition["change_events"][0]["event_kind"], "delete")

    def test_watchdog_once_fails_closed_when_tombstone_generation_does_not_commit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = (root / "deleted.ts").resolve()
            indexed = {
                target: {
                    "project": "MAIN",
                    "rel_path": "deleted.ts",
                    "target_ref": "MAIN::deleted.ts",
                }
            }
            handler = Mock()
            handler.run_analysis.return_value = False
            with (
                patch.object(watchdog, "ROOT", root),
                patch.object(watchdog, "CODE_MAPS_DIR", root),
                patch.object(watchdog, "_canonical_indexed_watch_paths", return_value=indexed),
                patch.object(watchdog, "CodeMapsHandler", return_value=handler),
                patch("tools.core.artifact_store.flush_shadow_writes") as flush,
            ):
                result = watchdog.run_watchdog_once(target, 0.0)

        self.assertEqual(result, 2)
        flush.assert_not_called()

    def test_indexed_path_projection_uses_current_default_or_external_runtime_root(self):
        for mode in ("default", "external"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir) / mode
                project_root = root / "src"
                project_root.mkdir(parents=True)
                target = (project_root / "deleted.ts").resolve()
                atlas = {"MAIN": {"files": {"deleted.ts": {"hash": "old"}}}}
                with (
                    patch.object(watchdog, "ROOT", root),
                    patch.object(watchdog.orchestrator, "load_atlas_data", return_value=atlas),
                    patch(
                        "tools.core.projects_registry.resolve_runtime_projects",
                        return_value={"MAIN": project_root},
                    ),
                ):
                    indexed = watchdog._canonical_indexed_watch_paths()

                self.assertEqual(indexed[target]["target_ref"], "MAIN::deleted.ts")

    def test_directory_selection_prioritizes_and_discloses_omitted_tombstones(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            existing = root / "existing.ts"
            existing.write_text("export const live = true;", encoding="utf-8")
            deleted = [(root / f"deleted_{index}.ts").resolve() for index in range(3)]
            indexed = {
                path: {
                    "project": "MAIN",
                    "rel_path": path.name,
                    "target_ref": f"MAIN::{path.name}",
                }
                for path in [existing.resolve(), *deleted]
            }

            selection = collect_smoke_file_selection(root, limit=2, indexed_paths=indexed)

        self.assertEqual(selection["mode"], "directory_tombstone_sample")
        self.assertEqual(selection["selected_files"], [str(deleted[0]), str(deleted[1])])
        self.assertEqual(selection["selected_tombstones"], [str(deleted[0]), str(deleted[1])])
        self.assertEqual(selection["omitted_tombstone_count"], 1)
        self.assertEqual(selection["omitted_existing_count"], 1)
        self.assertFalse(selection["tombstone_coverage_complete"])

    def test_watchdog_move_event_preserves_linked_rename_identities(self):
        handler = watchdog.CodeMapsHandler(debounce_seconds=10.0)
        event = Mock(is_directory=False, src_path="old.ts", dest_path="new.ts")
        with patch.object(handler, "_schedule_trigger_locked"):
            handler.on_moved(event)

        self.assertEqual(handler.change_events["old.ts"]["event_kind"], "rename_from")
        self.assertEqual(handler.change_events["old.ts"]["related_path"], "new.ts")
        self.assertEqual(handler.change_events["new.ts"]["event_kind"], "rename_to")
        self.assertEqual(handler.change_events["new.ts"]["related_path"], "old.ts")


if __name__ == "__main__":
    unittest.main()
