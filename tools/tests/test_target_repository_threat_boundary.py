from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.core import projects_registry, source_snapshot_reader
from tools.core.repository_topology import discover_project_candidates
from tools.core.target_inventory import source_inventory
from tools.core.target_policy_profile import inventory_project_target_policy
from tools.core.target_repository_trust import (
    classify_target_path,
    new_target_path_boundary_state,
)
from tools.engines.validation_oracle import ValidationOracle
from tools.external_target_preflight import build_preflight


CODE_MAPS_DIR = Path(__file__).resolve().parents[2]


class TargetRepositoryThreatBoundaryTests(unittest.TestCase):
    def _symlink(self, link: Path, target: Path, *, directory: bool = False) -> None:
        try:
            os.symlink(target, link, target_is_directory=directory)
        except OSError as exc:
            self.skipTest(f"symlink creation unavailable: {exc}")

    def test_path_classifier_accepts_contained_and_rejects_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "repo"
            outside = Path(temp) / "outside.txt"
            root.mkdir()
            (root / "inside.txt").write_text("inside", encoding="utf-8")
            outside.write_text("outside", encoding="utf-8")
            self.assertTrue(classify_target_path(root, root / "inside.txt")["contained"])
            self.assertFalse(classify_target_path(root, root / ".." / "outside.txt")["contained"])

    def test_inventory_excludes_escaping_file_symlink_and_records_attention(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "repo"
            outside = Path(temp) / "outside-package.json"
            root.mkdir()
            (root / "inside.ts").write_text("export const inside = 1;", encoding="utf-8")
            outside.write_text('{"scripts":{"attack":"echo no"}}', encoding="utf-8")
            self._symlink(root / "package.json", outside)
            state = new_target_path_boundary_state()
            result = source_inventory(
                root,
                {
                    "file_count_limit": 100,
                    "root_only_skip_dirs": [],
                    "framework_source_evidence": {"react": {"source_extensions": [], "excluded_path_segments": []}},
                },
                ["package.json"],
                {"javascript_node": ["package.json"]},
                {},
                path_boundary_state=state,
            )
            self.assertEqual(result[0], 1)
            self.assertEqual(result[7], {})
            self.assertGreaterEqual(state["escaping_path_count"], 1)

    def test_topology_does_not_follow_escaping_directory_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "repo"
            outside = Path(temp) / "outside-project"
            root.mkdir()
            outside.mkdir()
            (outside / "package.json").write_text("{}", encoding="utf-8")
            self._symlink(root / "linked-project", outside, directory=True)
            state = new_target_path_boundary_state()
            projects = discover_project_candidates(
                root,
                config_or_manifest_predicate=lambda name: name == "package.json",
                path_boundary_state=state,
            )
            self.assertEqual(projects, {"MAIN": "."})
            self.assertGreaterEqual(state["escaping_path_count"], 1)

    def test_preflight_marks_escaping_symlink_attention_and_hostile_class_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "repo"
            outside = Path(temp) / "outside.json"
            root.mkdir()
            (root / "src").mkdir()
            (root / "src" / "index.ts").write_text("export const ok = true;", encoding="utf-8")
            outside.write_text('{"compilerOptions":{"paths":{"@/*":["../*"]}}}', encoding="utf-8")
            self._symlink(root / "tsconfig.json", outside)
            ordinary = build_preflight(root)
            threat = ordinary["summary"]["threat_boundary"]
            self.assertEqual(ordinary["summary"]["status"], "ATTENTION")
            self.assertIn("escaping_target_paths_excluded", ordinary["summary"]["attention_reasons"])
            self.assertGreaterEqual(threat["path_boundary"]["escaping_path_count"], 1)
            hostile = build_preflight(root, trust_class="adversarial_or_hostile")
            self.assertEqual(hostile["summary"]["status"], "FAIL")
            self.assertEqual(hostile["summary"]["threat_boundary"]["hostile_repository_safety"], "not_available")

    def test_runtime_project_registry_rejects_outside_variation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "repo"
            outside = Path(temp) / "outside"
            root.mkdir()
            outside.mkdir()
            with patch.object(projects_registry, "PROJECTS", {"ESCAPE": ["../outside"], "MAIN": ["."]}):
                resolved = projects_registry.resolve_projects(root)
            self.assertNotIn("ESCAPE", resolved)
            self.assertEqual(resolved["MAIN"], root.resolve())

    def test_live_source_fallback_refuses_outside_path_and_stays_under_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "repo"
            outside = Path(temp) / "outside.ts"
            root.mkdir()
            outside.write_text("secret", encoding="utf-8")
            with patch.object(source_snapshot_reader, "ROOT", root), patch.object(
                source_snapshot_reader,
                "resolve_runtime_projects",
                return_value={"MAIN": root},
            ):
                result = source_snapshot_reader._normalize_fallback_path("MAIN", "outside.ts", outside)
            self.assertNotEqual(result, outside.resolve())
            self.assertTrue(result == root.resolve() or root.resolve() in result.parents)
            self.assertFalse(result.exists())
            with patch.object(source_snapshot_reader, "ROOT", root):
                traversal = source_snapshot_reader._normalize_fallback_path(
                    "MAIN",
                    "../outside.ts",
                    outside,
                )
            self.assertIsNone(traversal)

    def test_target_policy_does_not_hash_outside_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "repo"
            outside = Path(temp) / "package.json"
            root.mkdir()
            outside.write_text('{"dependencies":{"eslint":"9"}}', encoding="utf-8")
            profile = inventory_project_target_policy(
                root,
                project="MAIN",
                package_json={"dependencies": {"eslint": "9"}},
                package_path=outside,
                workspace_root=root,
            )
            self.assertIsNone(profile["manifest"])

    def test_oracle_uses_only_sage_compiler_and_filters_unsafe_options(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "repo"
            fake_bin = root / "node_modules" / ".bin"
            fake_bin.mkdir(parents=True)
            (fake_bin / "tsc.cmd").write_text("echo target-controlled", encoding="utf-8")
            (root / "tsconfig.json").write_text(
                json.dumps(
                    {
                        "compilerOptions": {
                            "strict": True,
                            "paths": {"evil/*": ["../outside/*"]},
                            "plugins": [{"name": "evil"}],
                            "typeRoots": ["../outside"],
                        }
                    }
                ),
                encoding="utf-8",
            )
            oracle = ValidationOracle(str(root))
            node_modules_root = oracle._resolve_node_modules_root(root)
            command = oracle._resolve_tsc_command(node_modules_root)
            config = oracle._build_sanctuary_tsconfig(root)
            self.assertEqual(command[0], "node")
            self.assertEqual(Path(command[1]).resolve(), (CODE_MAPS_DIR / "tools/engines/node_modules/typescript/bin/tsc").resolve())
            self.assertNotIn(str(fake_bin / "tsc.cmd"), command)
            self.assertTrue(config["compilerOptions"]["strict"])
            self.assertEqual(config["compilerOptions"]["paths"], {"@/*": ["./src/*"]})
            self.assertNotIn("plugins", config["compilerOptions"])
            self.assertNotIn("typeRoots", config["compilerOptions"])
            with (
                patch.object(oracle, "_source_root_for_project", return_value=root),
                patch.object(oracle, "_resolve_tsc_command", return_value=command),
            ):
                rejected = oracle._run_tsc_validation(
                    "MAIN",
                    str(root),
                    node_modules_root=node_modules_root,
                    command=["node", str(fake_bin / "tsc.cmd")],
                )
            self.assertEqual(rejected, [])
            self.assertFalse((root / "tsconfig.sanctuary.json").exists())

    def test_oracle_refuses_escaping_config_alias_and_relative_hydration_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "repo"
            outside = Path(temp) / "outside"
            source = root / "src" / "inside.ts"
            root.mkdir()
            outside.mkdir()
            source.parent.mkdir()
            source.write_text('import "../../outside/escape";', encoding="utf-8")
            (outside / "tsconfig.json").write_text(
                json.dumps({"compilerOptions": {"strict": True}}),
                encoding="utf-8",
            )
            (outside / "escape.ts").write_text("export const escape = true;", encoding="utf-8")
            self._symlink(root / "tsconfig.json", outside / "tsconfig.json")
            self._symlink(root / "linked.ts", outside / "escape.ts")
            oracle = ValidationOracle(str(root))
            config = oracle._build_sanctuary_tsconfig(root)
            self.assertNotIn("strict", config["compilerOptions"])
            self.assertIsNone(
                oracle._resolve_relative_under_source(
                    source,
                    "../../outside/escape",
                    source_root=root,
                )
            )
            self.assertIsNone(oracle._resolve_alias_under_root(root, ["linked"]))

    def test_ts_diagnostics_collector_drops_outside_include(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "repo"
            outside = Path(temp) / "outside"
            src = root / "src"
            src.mkdir(parents=True)
            outside.mkdir()
            (src / "inside.ts").write_text("export const inside = 1;", encoding="utf-8")
            (outside / "outside.ts").write_text("export const outside = 2;", encoding="utf-8")
            (root / "tsconfig.json").write_text(
                json.dumps({"include": ["src/**/*.ts", "../outside/**/*.ts"]}),
                encoding="utf-8",
            )
            runtime_config = Path(temp) / "runtime.json"
            output = Path(temp) / "diagnostics.json"
            runtime_config.write_text(
                json.dumps({"workspace_root": str(root), "variations": {"MAIN": "."}}),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    "node",
                    str(CODE_MAPS_DIR / "tools/engines/ts_diagnostics_collector.cjs"),
                    "--config",
                    str(runtime_config),
                    "--out",
                    str(output),
                    "--semantic",
                    "false",
                ],
                cwd=CODE_MAPS_DIR,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["projects"]["MAIN"]["summary"]["root_files_total"], 1)

    def test_ts_diagnostics_semantic_host_refuses_outside_import(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "repo"
            outside = Path(temp) / "outside"
            src = root / "src"
            src.mkdir(parents=True)
            outside.mkdir()
            (src / "inside.ts").write_text(
                'import { outside } from "../../outside/outside"; export const inside = outside;',
                encoding="utf-8",
            )
            (outside / "outside.ts").write_text(
                'export const outside: string = 42;',
                encoding="utf-8",
            )
            (root / "tsconfig.json").write_text(
                json.dumps({"include": ["src/**/*.ts"]}),
                encoding="utf-8",
            )
            runtime_config = Path(temp) / "runtime.json"
            output = Path(temp) / "diagnostics.json"
            runtime_config.write_text(
                json.dumps({"workspace_root": str(root), "variations": {"MAIN": "."}}),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    "node",
                    str(CODE_MAPS_DIR / "tools/engines/ts_diagnostics_collector.cjs"),
                    "--config",
                    str(runtime_config),
                    "--out",
                    str(output),
                    "--semantic",
                    "true",
                ],
                cwd=CODE_MAPS_DIR,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(output.read_text(encoding="utf-8"))
            project = payload["projects"]["MAIN"]
            self.assertEqual(project["status"], "OK")
            self.assertEqual(project["summary"]["source_files"], 1)
            self.assertTrue(any(row["code"] == "TS2307" for row in project["diagnostics"]))
            self.assertFalse(any(row["file"].startswith("../") for row in project["diagnostics"]))


if __name__ == "__main__":
    unittest.main()
