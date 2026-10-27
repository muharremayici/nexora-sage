from __future__ import annotations

import json
import hashlib
from copy import deepcopy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

from tools.core.agent_command_contracts import target_package_script_command
from tools.core.package_contracts import build_package_public_contracts
from tools.core.package_contracts import _existing_source_entry_patterns
from tools.core import package_contracts
from tools.core.atlas_typescript_inputs import capture_auxiliary_inputs, auxiliary_input_policy
from tools.engines.dead_code_detector import DeadCodeDetector


class PackageContractAtlasTests(unittest.TestCase):
    def _bound_contracts(self, root, payload):
        manifest = root / "package.json"
        manifest.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        auxiliary = capture_auxiliary_inputs(root, [(str(manifest), manifest.name, manifest.name)])
        return build_package_public_contracts(root, [manifest], auxiliary_inputs=auxiliary), auxiliary

    def test_target_package_commands_preserve_declared_package_manager(self):
        self.assertEqual(
            target_package_script_command({"packageManager": "pnpm@10.26.0"}, ".", "test"),
            'pnpm --dir "." run test',
        )
        self.assertEqual(
            target_package_script_command({"packageManager": "yarn@4.0.0"}, "packages/ui", "lint"),
            'yarn --cwd "packages/ui" run lint',
        )
        self.assertEqual(
            target_package_script_command({}, ".", "build"),
            'npm --prefix "." run build',
        )

    def test_package_entrypoints_are_normalized_for_atlas_storage(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            package_dir = root / "packages" / "ui"
            package_dir.mkdir(parents=True)
            manifest = package_dir / "package.json"
            manifest.write_text(
                json.dumps(
                    {
                        "name": "@example/ui",
                        "exports": {".": "./src/index.ts", "./theme": "./src/theme.ts"},
                        "typesVersions": {"*": {"theme": ["src/theme-augmentation.d.ts"]}},
                        "sideEffects": ["src/register.ts"],
                    }
                ),
                encoding="utf-8",
            )

            contracts = build_package_public_contracts(root, [manifest])

            self.assertEqual(contracts["source"], "package_manifests")
            self.assertEqual(contracts["packages"][0]["manifest"], "packages/ui/package.json")
            self.assertIn("packages/ui/src/index.ts", contracts["entry_patterns"])
            self.assertIn("packages/ui/src/theme-augmentation.d.ts", contracts["entry_patterns"])
            self.assertIn("packages/ui/src/register.ts", contracts["entry_patterns"])

    def test_dead_code_consumes_atlas_contract_without_filesystem_scan(self):
        detector = DeadCodeDetector()
        project_data = {
            "public_contracts": {
                "source": "package_manifests",
                "entry_patterns": ["src/index.ts"],
                "packages": [{"manifest": "package.json"}],
            }
        }

        self.assertTrue(detector._is_package_public_export_surface("MISSING_PROJECT", "src/index.ts", project_data))
        self.assertEqual(detector._package_scan_stats["MISSING_PROJECT"]["source"], "atlas_public_contracts")


    def test_wildcard_source_projection_requires_matching_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "src" / "icons").mkdir(parents=True)
            # A directory with a source suffix is not a source file.
            (root / "src" / "icons" / "directory.ts").mkdir()
            exports = {"./icons/*": "./dist/icons/*.js", "./missing/*": "./dist/missing/*.js"}
            self.assertEqual(_existing_source_entry_patterns(root, "packages/ui", exports), set())

            (root / "src" / "icons" / "check.tsx").write_text("export const check = true;", encoding="utf-8")
            self.assertEqual(_existing_source_entry_patterns(root, "packages/ui", exports),
                             {"packages/ui/src/icons/*.*"})
            (root / "icons").mkdir()
            (root / "icons" / "check.js").write_text("export const check = true;", encoding="utf-8")
            self.assertEqual(_existing_source_entry_patterns(root, "packages/ui", exports),
                             {"packages/ui/src/icons/*.*", "packages/ui/icons/*.*"})

    def test_missing_wildcard_source_does_not_remove_declared_package_contract(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest = root / "package.json"
            manifest.write_text(json.dumps({"exports": {"./missing/*": {
                "types": "./types/*.d.mts", "default": "./dist/*.mjs"}},
                "typesVersions": {"*": {"missing/*": ["types/*.d.cts"]}}}), encoding="utf-8")
            contracts = build_package_public_contracts(root, [manifest])
            self.assertEqual(contracts["packages"][0]["source_entry_patterns"], [])
            self.assertEqual(contracts["entry_patterns"],
                             ["dist/*.mjs", "types/*.d.cts", "types/*.d.mts"])

    def test_wildcard_source_projection_preserves_filesystem_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(Path, "glob", side_effect=OSError("fixture enumeration failed")):
                with self.assertRaisesRegex(OSError, "fixture enumeration failed"):
                    _existing_source_entry_patterns(Path(temp_dir), "", {"./icons/*": "./dist/*.js"})


    def test_type_entry_provenance_preserves_paths_conditions_and_order(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            payload = {"name": "@example/ui", "types": " ./src/index.ts ", "typings": "./legacy.d.ts",
                "exports": {".": {"import": {"types": "./types/import.d.mts", "default": "./dist/index.mjs"},
                                   "types@>=5.2": "./types/new.d.ts"},
                            "./feature": {"require": {"types": ["./a.d.cts", "./b.d.cts"]}},
                            "./plain": "./opaque.d.ts"},
                "typesVersions": {">=5.0": {"feature/*": ["./v5/*.d.ts", "./fallback/*.d.ts"]}}}
            contracts, auxiliary = self._bound_contracts(root, payload)
            evidence = contracts["packages"][0]["type_entry_evidence"]
            self.assertEqual(evidence["status"], "observed")
            self.assertEqual(evidence["source_hash"], auxiliary["files"]["package.json"]["raw_sha256"])
            self.assertEqual(evidence["shown_count"], 8)
            self.assertEqual(evidence["omitted_count"], 0)
            rows = evidence["records"]
            self.assertEqual(rows[0]["target_literal"], " ./src/index.ts ")
            self.assertEqual(rows[2]["field_path"], ["exports", ".", "import", "types"])
            self.assertEqual(rows[2]["sibling_order"], [3, 0, 0, 0])
            self.assertEqual(rows[2]["condition_path"], ["import", "types"])
            self.assertEqual(rows[3]["condition_path"], ["types@>=5.2"])
            self.assertEqual(rows[4]["field_path"], ["exports", "./feature", "require", "types", 0])
            self.assertEqual(rows[4]["subpath"], "./feature")
            self.assertEqual(rows[-1]["field_path"], ["typesVersions", ">=5.0", "feature/*", 1])
            self.assertEqual(rows[-1]["version_selector"], ">=5.0")
            self.assertTrue(all(row["resolution_status"] == "not_evaluated" for row in rows))
            self.assertEqual(evidence["runtime_execution"], "not_established")
            self.assertIn("opaque.d.ts", contracts["entry_patterns"])
            self.assertNotIn("./opaque.d.ts", [row["target_literal"] for row in rows])
            # The old protection view is identical, even without new binding authority.
            legacy = build_package_public_contracts(root, [root / "package.json"])
            self.assertEqual(legacy["entry_patterns"], contracts["entry_patterns"])
            self.assertEqual(legacy["packages"][0]["type_entry_evidence"]["status"], "unavailable")

    def test_type_entry_provenance_rejects_unbound_or_foreign_input(self):
        cases = ["missing", "foreign_root", "wrong_scope", "surgical", "noncanonical", "bad_size", "stale", "missing_hash"]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for case in cases:
                with self.subTest(case=case):
                    _, auxiliary = self._bound_contracts(root, {"types": "./api.d.ts"})
                    if case == "missing":
                        auxiliary["files"].clear()
                    elif case == "foreign_root":
                        auxiliary["project_root"] = root.parent.as_posix()
                    elif case == "wrong_scope":
                        auxiliary["scope"] = "entire_repository"
                    elif case == "surgical":
                        auxiliary["status"] = "unavailable_surgical_scan"
                    elif case == "noncanonical":
                        auxiliary["files"]["package.json"]["canonical_file_path"] = False
                    elif case == "bad_size":
                        auxiliary["files"]["package.json"]["size_bytes"] += 1
                    elif case == "stale":
                        (root / "package.json").write_text('{"types": "./new.d.ts"}', encoding="utf-8")
                    elif case == "missing_hash":
                        auxiliary["files"]["package.json"].pop("raw_sha256")
                    view = build_package_public_contracts(root, [root / "package.json"], auxiliary_inputs=auxiliary)["packages"][0]["type_entry_evidence"]
                    self.assertEqual(view["status"], "unavailable")
                    self.assertEqual(view["records"], [])
                    self.assertIsNone(view["source_hash"])

    def test_type_entry_provenance_partial_inventory_can_supply_positive_manifest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _, auxiliary = self._bound_contracts(root, {"types": "./api.d.ts"})
            auxiliary.update(status="partial", omitted_files=1)
            view = build_package_public_contracts(root, [root / "package.json"], auxiliary_inputs=auxiliary)["packages"][0]["type_entry_evidence"]
            self.assertEqual(view["status"], "observed")
            self.assertEqual(view["records"][0]["target_literal"], "./api.d.ts")

    def test_type_entry_provenance_preserves_exact_newline_bytes_and_rejects_duplicate_keys(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest = root / "package.json"
            for data, status in [(b'{\r\n"types":"./api.d.ts"\r\n}', "observed"),
                                 (b'{"types":"./a.d.ts","types":"./b.d.ts"}', "unavailable")]:
                with self.subTest(status=status):
                    manifest.write_bytes(data)
                    auxiliary = capture_auxiliary_inputs(root, [(str(manifest), manifest.name, manifest.name)])
                    contracts = build_package_public_contracts(root, [manifest], auxiliary_inputs=auxiliary)
                    view = contracts["packages"][0]["type_entry_evidence"]
                    self.assertEqual(view["status"], status)
                    if status == "observed":
                        self.assertEqual(view["source_hash"], hashlib.sha256(data).hexdigest())
                    else:
                        self.assertEqual(view["reasons"], ["duplicate_manifest_keys"])
                        self.assertIn("b.d.ts", contracts["entry_patterns"])

    def test_type_entry_provenance_budgets_and_nonliteral_omissions_are_explicit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _, auxiliary = self._bound_contracts(root, {"typesVersions": {"*": {"*": ["a", "b", "c"]}}})
            for key, value in [("max_records", 1), ("max_nodes", 1), ("max_depth", 1)]:
                with self.subTest(key=key):
                    policy = deepcopy(auxiliary_input_policy())
                    policy["package_type_entry_evidence"][key] = value
                    with patch.object(package_contracts, "auxiliary_input_policy", return_value=policy):
                        view = build_package_public_contracts(root, [root / "package.json"], auxiliary_inputs=auxiliary)["packages"][0]["type_entry_evidence"]
                    self.assertEqual(view["status"], "partial")
                    self.assertIsNone(view["omitted_count"])
                    self.assertTrue(view["reasons"])
            contracts, _ = self._bound_contracts(root, {"types": None})
            view = contracts["packages"][0]["type_entry_evidence"]
            self.assertEqual(view["status"], "partial")
            self.assertEqual(view["reasons"], ["nonliteral_type_value"])

    def test_type_entry_provenance_policy_is_strict_and_identity_changes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            contracts, auxiliary = self._bound_contracts(root, {"types": "./api.d.ts"})
            first = contracts["packages"][0]["type_entry_evidence"]
            policy = deepcopy(auxiliary_input_policy())
            policy["package_type_entry_evidence"]["max_nodes"] += 1
            with patch.object(package_contracts, "auxiliary_input_policy", return_value=policy):
                second = build_package_public_contracts(root, [root / "package.json"], auxiliary_inputs=auxiliary)["packages"][0]["type_entry_evidence"]
            self.assertNotEqual(first["policy_sha256"], second["policy_sha256"])
            policy["package_type_entry_evidence"]["max_nodes"] = True
            with patch.object(package_contracts, "auxiliary_input_policy", return_value=policy):
                with self.assertRaisesRegex(ValueError, "budget"):
                    build_package_public_contracts(root, [root / "package.json"], auxiliary_inputs=auxiliary)


    def test_type_entry_provenance_changes_snapshot_not_implementation_counts(self):
        from tools.core.atlas_integrity import build_atlas_commit

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first, _ = self._bound_contracts(root, {"types": "./first.ts"})
            second, _ = self._bound_contracts(root, {"types": "./second.ts"})
            def commit(contracts):
                return build_atlas_commit({"MAIN": {"project": {"root": str(root)},
                    "files": {}, "public_contracts": contracts}})
            before, after = commit(first), commit(second)
            self.assertNotEqual(before["snapshot_id"], after["snapshot_id"])
            self.assertEqual(before["counts"], after["counts"])
            self.assertEqual(before["counts"]["files"], 0)

    def test_utf8_bom_keeps_literal_contract_and_exact_raw_identity(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            package = root / "packages" / "ui"
            (package / "src").mkdir(parents=True)
            (package / "src" / "index.ts").write_text("export const value = 1;", encoding="utf-8")
            manifest = package / "package.json"
            text = json.dumps({"name": "@fixture/Ö", "types": "./types/Ö.d.ts", "typings": "./src/index.ts",
                "exports": {".": {"import": {"types": "./types/import.d.mts", "default": "./dist/index.mjs"}},
                            "./feature": {"types@>=5.2": "./new.d.cts"}},
                "typesVersions": {">=5": {"*": ["./v5/*.d.ts"]}}, "sideEffects": ["./src/register.ts"]},
                ensure_ascii=False, indent=2).replace("\n", "\r\n").encode("utf-8")
            observed = []
            for prefix in (b"", b"\xef\xbb\xbf"):
                data = prefix + text
                manifest.write_bytes(data)
                auxiliary = capture_auxiliary_inputs(root, [(str(manifest), manifest.name, "packages/ui/package.json")])
                contracts = build_package_public_contracts(root, [manifest], auxiliary_inputs=auxiliary)
                view = contracts["packages"][0]["type_entry_evidence"]
                self.assertEqual(view["status"], "observed")
                self.assertEqual(view["source_hash"], hashlib.sha256(data).hexdigest())
                self.assertEqual(view["source_hash"], auxiliary["files"]["packages/ui/package.json"]["raw_sha256"])
                self.assertEqual(manifest.read_bytes(), data)
                self.assertEqual(view["runtime_execution"], "not_established")
                self.assertTrue(all(row["resolution_status"] == "not_evaluated" for row in view["records"]))
                legacy = build_package_public_contracts(root, [manifest])
                self.assertEqual(legacy["entry_patterns"], contracts["entry_patterns"])
                self.assertEqual(legacy["packages"][0]["type_entry_evidence"]["status"], "unavailable")
                detector = DeadCodeDetector()
                self.assertTrue(detector._is_package_public_export_surface("MISSING_PROJECT", "packages/ui/src/index.ts",
                    {"public_contracts": contracts}))
                observed.append(deepcopy(contracts))
            first = observed[0]["packages"][0]["type_entry_evidence"]
            second = observed[1]["packages"][0]["type_entry_evidence"]
            self.assertNotEqual(first["source_hash"], second["source_hash"])
            self.assertEqual(second["source_hash"], hashlib.sha256(b"\xef\xbb\xbf" + text).hexdigest())
            second["source_hash"] = first["source_hash"]
            self.assertEqual(observed[0], observed[1])  # Conditions/order/patterns and source projection unchanged.

    def test_bom_changes_raw_identity_even_with_identical_json_value(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest = root / "package.json"
            data = b'{"types":"./index.d.ts"}'
            manifest.write_bytes(data)
            capture = capture_auxiliary_inputs(root, [(str(manifest), manifest.name, manifest.name)])
            manifest.write_bytes(b"\xef\xbb\xbf" + data)
            stale = build_package_public_contracts(root, [manifest], auxiliary_inputs=capture)["packages"][0]["type_entry_evidence"]
            self.assertEqual(stale["status"], "unavailable")
            self.assertEqual(stale["reasons"], ["manifest_input_unavailable"])
            # Equal length cannot bypass the raw-byte digest check either.
            capture["files"]["package.json"]["size_bytes"] += 3
            stale = build_package_public_contracts(root, [manifest], auxiliary_inputs=capture)["packages"][0]["type_entry_evidence"]
            self.assertEqual(stale["reasons"], ["manifest_input_mismatch"])
            self.assertIsNone(stale["source_hash"])

    def test_bom_does_not_hide_duplicate_keys_or_remove_in_string_feff(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest = root / "package.json"
            for body in ('{"types":"./a.d.ts","types":"./b.d.ts"}',
                         '{"exports":{".":{"types":"./a.d.ts","types":"./b.d.ts"}}}'):
                manifest.write_bytes(b"\xef\xbb\xbf" + body.encode("utf-8"))
                capture = capture_auxiliary_inputs(root, [(str(manifest), manifest.name, manifest.name)])
                view = build_package_public_contracts(root, [manifest], auxiliary_inputs=capture)["packages"][0]["type_entry_evidence"]
                self.assertEqual(view["reasons"], ["duplicate_manifest_keys"])
                self.assertEqual(view["records"], [])
            data = b"\xef\xbb\xbf" + '{"types":"./api\ufeff.d.ts"}'.encode("utf-8")
            manifest.write_bytes(data)
            capture = capture_auxiliary_inputs(root, [(str(manifest), manifest.name, manifest.name)])
            view = build_package_public_contracts(root, [manifest], auxiliary_inputs=capture)["packages"][0]["type_entry_evidence"]
            self.assertEqual(view["records"][0]["target_literal"], "./api\ufeff.d.ts")
            self.assertEqual(view["source_hash"], hashlib.sha256(data).hexdigest())

    def test_bom_admission_does_not_repair_malformed_or_other_encodings(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest = root / "package.json"
            body = b'{"types":"./api.d.ts"}'
            for data in (b"\xef\xbb\xbf" * 2 + body, b" \xef\xbb\xbf" + body, b"\xef\xbb\xbf",
                         b"\xef\xbb\xbf" + body + b"garbage", b"\xef\xbb\xbf{\xff}",
                         body.decode().encode("utf-16"), body.decode().encode("utf-32"), b"\xef\xbb\xbf[]"):
                with self.subTest(data=data):
                    manifest.write_bytes(data)
                    self.assertEqual(build_package_public_contracts(root, [manifest])["packages"], [])


@pytest.mark.parametrize("suffix", [".mts", ".cts"])
@pytest.mark.parametrize("key,stem,pattern", [(".", "src/index", "src/index.*"),
    ("./feature", "src/feature", "src/feature.*"), ("./icons/*", "src/icons/check", "src/icons/*.*")])
def test_module_export_key_projection_requires_ordinary_physical_file(tmp_path, suffix, key, stem, pattern):
    exports = {key: "./dist/missing.js"}
    assert _existing_source_entry_patterns(tmp_path, "packages/Ö-ui", exports) == set()
    source = tmp_path / (stem + suffix)
    source.parent.mkdir(parents=True, exist_ok=True)
    source.mkdir()  # A same-suffix directory must not serve as evidence.
    assert _existing_source_entry_patterns(tmp_path, "packages/Ö-ui", exports) == set()
    source.rmdir()
    source.write_text("export const value = 1;", encoding="utf-8")
    assert _existing_source_entry_patterns(tmp_path, "packages/Ö-ui", exports) == {"packages/Ö-ui/"+pattern}


@pytest.mark.parametrize("field", ["main", "module", "browser", "bin"])
@pytest.mark.parametrize("entry,source", [("./dist/index.mjs", "src/index.mts"),
    ("./lib/prod/index.cjs", "src/index.cts"), ("./build/types/index.d.mts", "src/index.mts"),
    ("./dist/index.d.cts", "src/index.cts"), ("./src/index.mts", "src/index.mts"),
    ("./src/index.cts", "src/index.cts")])
def test_module_entry_value_projects_only_to_existing_source(tmp_path, field, entry, source):
    payload = {field: {"command": entry} if field in {"bin", "browser"} else entry}
    manifest = tmp_path / "package.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    assert build_package_public_contracts(tmp_path, [manifest])["packages"][0]["source_entry_patterns"] == []
    target = tmp_path / source
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("export const value = 1;", encoding="utf-8")
    contracts = build_package_public_contracts(tmp_path, [manifest])
    assert contracts["packages"][0]["source_entry_patterns"] == [source]
    assert entry[2:] in contracts["entry_patterns"]  # Do not replace declared artifact paths.
    assert contracts["packages"][0]["type_entry_evidence"]["status"] == "unavailable"


@pytest.mark.parametrize("suffix", [".d.ts", ".d.mts", ".d.cts"])
def test_declaration_only_wildcard_does_not_invent_source_projection(tmp_path, suffix):
    (tmp_path / "src").mkdir()
    (tmp_path / ("src/api"+suffix)).write_text("export interface Api {}", encoding="utf-8")
    manifest = tmp_path / "package.json"
    manifest.write_text(json.dumps({"exports": {"./*": "./dist/*"+suffix}}), encoding="utf-8")
    contracts = build_package_public_contracts(tmp_path, [manifest])
    assert contracts["packages"][0]["source_entry_patterns"] == []
    assert contracts["entry_patterns"] == ["dist/*"+suffix]


def test_projection_uses_registry_and_preserves_bootstrap_fallback(tmp_path):
    from tools.core import language_registry
    source = tmp_path / "src/index.fixture"
    source.parent.mkdir()
    source.write_text("export const value = 1;", encoding="utf-8")
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"languages": {"typescript": {"extensions": [".fixture"]},
        "javascript": {"extensions": []}}, "non_source_compound_suffixes": [".d.fixture"]}), encoding="utf-8")
    with patch.object(language_registry, "REGISTRY_FILE", registry):
        assert _existing_source_entry_patterns(tmp_path, "", {".": "./dist/index.js"}) == {"src/index.*"}
        assert package_contracts._existing_compiled_entry_source_patterns(tmp_path, "", {"main": "./dist/index.fixture"}) == {"src/index.fixture"}
    source.unlink()
    (tmp_path / "src/index.cts").write_text("export const value = 1;", encoding="utf-8")
    with patch.object(language_registry, "REGISTRY_FILE", tmp_path / "missing-registry.json"):
        assert _existing_source_entry_patterns(tmp_path, "", {".": "./dist/index.cjs"}) == {"src/index.*"}


def test_module_candidates_preserve_ambiguity_and_non_script_boundary(tmp_path):
    for suffix in (".mts", ".cts", ".py", ".pyi", ".java", ".go", ".cs"):
        path = tmp_path / ("src/index"+suffix)
        path.parent.mkdir(exist_ok=True)
        path.write_text("fixture", encoding="utf-8")
    candidates = package_contracts._existing_compiled_entry_source_patterns(tmp_path, "", {"main": "./dist/index.mjs"})
    assert candidates == {"src/index.mts", "src/index.cts"}  # All physical candidates, not native winner selection.
    for suffix in (".py", ".java", ".go", ".cs"):
        path = tmp_path / ("src/legacy"+suffix)
        path.write_text("fixture", encoding="utf-8")
        assert _existing_source_entry_patterns(tmp_path, "", {"./legacy": "./dist/legacy.js"}) == {"src/legacy.*"}
        path.unlink()
    assert _existing_source_entry_patterns(tmp_path, "", {"./legacy": "./dist/legacy.js"}) == set()


def test_projection_registry_implementation_changes_both_producer_fingerprints(monkeypatch):
    from tools.core import atlas_integrity
    from tools.engines import generate_atlas as generator
    before = (generator.atlas_staging_producer_contract(), atlas_integrity.generator_fingerprint())
    original = Path.read_bytes
    def changed(path):
        content = original(path)
        return content + b"\n# fixture registry dependency" if path.name == "language_registry.py" else content
    monkeypatch.setattr(Path, "read_bytes", changed)
    after = (generator.atlas_staging_producer_contract(), atlas_integrity.generator_fingerprint())
    assert all(a != b for a,b in zip(before, after))


if __name__ == "__main__":
    unittest.main()
