from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.core.config import CONFIG_DIR
from tools.core.json_io import clear_strict_json_content_cache
from tools.core.json_io import clear_json_content_cache
from tools.core.json_io import DuplicateJSONKeyError
from tools.core.json_io import load_json_object_strict
from tools.core.json_io import load_json_object_strict_cached


class CentralContractCacheFreshnessTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_strict_json_content_cache()

    def tearDown(self) -> None:
        clear_strict_json_content_cache()
        clear_json_content_cache()

    def _write(self, path: Path, payload: dict) -> None:
        path.write_text(json.dumps(payload), encoding="utf-8")

    def test_strict_loader_family_uses_shared_content_cache(self) -> None:
        policy = load_json_object_strict(CONFIG_DIR / "source_contract_policy.json")
        cache_policy = policy.get("central_contract_cache", {})
        self.assertIsInstance(cache_policy, dict)
        modules = cache_policy.get("strict_loader_files", [])
        self.assertIsInstance(modules, list)
        required_helper = str(cache_policy.get("required_helper") or "")
        forbidden_cache_token = str(cache_policy.get("forbidden_process_lifetime_cache_token") or "")
        self.assertTrue(required_helper)
        self.assertTrue(forbidden_cache_token)
        core_dir = Path(__file__).resolve().parents[1] / "core"
        for relative_path in modules:
            path = Path(str(relative_path))
            source = (core_dir.parent.parent / path).read_text(encoding="utf-8")
            self.assertIn(required_helper, source, relative_path)
            self.assertNotIn(forbidden_cache_token, source, relative_path)

    def test_strict_object_loader_rejects_duplicate_keys_at_any_depth(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "duplicate.json"
            path.write_text('{"outer": {"state": "pending", "state": "complete"}}', encoding="utf-8")
            with self.assertRaises(DuplicateJSONKeyError):
                load_json_object_strict(path)

    def test_cached_strict_object_loader_rejects_duplicate_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "duplicate.json"
            path.write_text('{"state": "pending", "state": "complete"}', encoding="utf-8")
            with self.assertRaises(DuplicateJSONKeyError):
                load_json_object_strict_cached(path)

    def test_source_contract_inventory_rejects_duplicate_config_keys(self) -> None:
        distribution_root = Path(__file__).resolve().parents[2]
        validator_path = distribution_root / "tools" / "validate_source_contracts.py"
        if (distribution_root / "PUBLIC_DISTRIBUTION_MANIFEST.json").is_file():
            self.assertFalse(validator_path.exists())
            return
        from tools import validate_source_contracts

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_dir = root / "config"
            config_dir.mkdir()
            (config_dir / "ambiguous.json").write_text(
                '{"policy": {"enabled": true, "enabled": false}}',
                encoding="utf-8",
            )
            with (
                patch.object(validate_source_contracts, "CODE_MAPS_DIR", root),
                patch.object(validate_source_contracts, "CONFIG_DIR", config_dir),
            ):
                inventory = validate_source_contracts._canonical_config_json_parse_inventory()
            self.assertEqual(inventory["total_config_files"], 1)
            self.assertEqual(len(inventory["invalid_or_ambiguous_json"]), 1)
            self.assertIn("Duplicate JSON object key: enabled", inventory["invalid_or_ambiguous_json"][0]["error"])

    def test_adapter_registry_refreshes_when_contract_content_changes(self) -> None:
        from tools.core import adapter_registry

        with tempfile.TemporaryDirectory() as temp_dir:
            registry_path = Path(temp_dir) / "adapters.json"
            self._write(registry_path, {"adapters": []})
            with patch.object(adapter_registry, "ADAPTERS_FILE", registry_path):
                self.assertEqual(adapter_registry.load_adapter_registry()["adapters"], [])
                self._write(registry_path, {"adapters": [{"id": "python"}]})
                self.assertEqual(adapter_registry.load_adapter_registry()["adapters"], [{"id": "python"}])

    def test_blueprint_decision_symbol_policy_and_test_profiles_refresh_in_process(self) -> None:
        from tools.core import architecture_blueprints, decision_ownership, language_agnostic_symbols, pipeline_policy, test_impact_profiles

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            blueprint_path = root / "profiles.json"
            decision_path = root / "decision.json"
            symbol_path = root / "symbols.json"
            policy_path = root / "audit.json"
            profile_path = root / "test-impact.json"
            self._write(blueprint_path, {"revision": 1})
            self._write(decision_path, {"revision": 1})
            self._write(symbol_path, {"revision": 1})
            self._write(policy_path, {"revision": 1})
            self._write(profile_path, {"languages": {"python": {}}, "confidence": {"direct": 0.5}, "fallback_command": "pytest {test_path}", "revision": 1})
            with (
                patch.object(architecture_blueprints, "PROFILES_FILE", blueprint_path),
                patch.object(decision_ownership, "REGISTRY_PATH", decision_path),
                patch.object(language_agnostic_symbols, "CONFIG_PATH", symbol_path),
                patch.object(pipeline_policy, "AUDIT_POLICY_FILE", policy_path),
                patch.object(test_impact_profiles, "PROFILE_PATH", profile_path),
            ):
                self.assertEqual(architecture_blueprints.load_blueprint_registry()["revision"], 1)
                self.assertEqual(decision_ownership.load_decision_ownership_registry()["revision"], 1)
                self.assertEqual(language_agnostic_symbols.load_symbol_schema()["revision"], 1)
                self.assertEqual(pipeline_policy.get_audit_policy()["revision"], 1)
                self.assertEqual(test_impact_profiles.load_test_impact_profiles()["revision"], 1)
                self._write(blueprint_path, {"revision": 2})
                self._write(decision_path, {"revision": 2})
                self._write(symbol_path, {"revision": 2})
                self._write(policy_path, {"revision": 2})
                self._write(profile_path, {"languages": {"python": {}}, "confidence": {"direct": 0.5}, "fallback_command": "pytest {test_path}", "revision": 2})
                self.assertEqual(architecture_blueprints.load_blueprint_registry()["revision"], 2)
                self.assertEqual(decision_ownership.load_decision_ownership_registry()["revision"], 2)
                self.assertEqual(language_agnostic_symbols.load_symbol_schema()["revision"], 2)
                self.assertEqual(pipeline_policy.get_audit_policy()["revision"], 2)
                self.assertEqual(test_impact_profiles.load_test_impact_profiles()["revision"], 2)

    def test_language_registry_and_discovery_context_follow_content_identity(self) -> None:
        from tools.core import language_registry
        from tools.orchestrators import discovery

        with tempfile.TemporaryDirectory() as temp_dir:
            registry_path = Path(temp_dir) / "language_registry.json"
            first = {
                "languages": {"alpha": {"extensions": [".alpha"], "index_files": []}},
                "plugin_library_map": {"alpha": ["alpha_plugin"]},
                "plugin_substring_match_packages": ["alpha"],
                "bundler_plugin_map": {"alpha-bundler": ["alpha_bundler_plugin"]},
                "skip_dirs": [],
                "root_level_app_markers": [],
                "monorepo_root_markers": [],
                "monorepo_root_files": [],
                "config_file_markers": {},
            }
            second = {
                "languages": {"beta": {"extensions": [".beta"], "index_files": []}},
                "plugin_library_map": {"beta": ["beta_plugin"]},
                "plugin_substring_match_packages": ["beta"],
                "bundler_plugin_map": {"beta-bundler": ["beta_bundler_plugin"]},
                "skip_dirs": [],
                "root_level_app_markers": [],
                "monorepo_root_markers": [],
                "monorepo_root_files": [],
                "config_file_markers": {},
            }
            self._write(registry_path, first)
            with patch.object(language_registry, "REGISTRY_FILE", registry_path):
                self.assertEqual(language_registry.language_for_extension(".alpha"), "alpha")
                self.assertEqual(language_registry.plugins_for_dependencies({"alpha-custom": "1"}), {"alpha_plugin"})
                self.assertEqual(language_registry.plugins_for_bundler("alpha-bundler"), {"alpha_bundler_plugin"})
                first_state = language_registry.language_registry_provenance()
                discovery.refresh_language_registry_context()
                self.assertEqual(discovery.LANGUAGE_MAP[".alpha"], "alpha")
                self._write(registry_path, second)
                self.assertEqual(language_registry.language_for_extension(".beta"), "beta")
                self.assertEqual(language_registry.plugins_for_dependencies({"beta-custom": "1"}), {"beta_plugin"})
                self.assertEqual(language_registry.plugins_for_bundler("beta-bundler"), {"beta_bundler_plugin"})
                second_state = language_registry.language_registry_provenance()
                discovery.refresh_language_registry_context()
                self.assertEqual(discovery.LANGUAGE_MAP[".beta"], "beta")
                self.assertNotEqual(first_state["identity"], second_state["identity"])
        discovery.refresh_language_registry_context()

    def test_language_registry_fallback_is_explicit_and_telemetry_is_deduplicated(self) -> None:
        from tools.core import language_registry

        with tempfile.TemporaryDirectory() as temp_dir:
            missing_registry = Path(temp_dir) / "missing_language_registry.json"
            with (
                patch.object(language_registry, "REGISTRY_FILE", missing_registry),
                patch.object(language_registry, "_FALLBACK_TELEMETRY_KEYS", set()),
                patch("tools.core.honesty_telemetry.record_honesty_event") as record_event,
            ):
                first = language_registry.language_registry_provenance()
                second = language_registry.language_registry_provenance()
                self.assertEqual(first["source"], "fallback")
                self.assertEqual(second["source"], "fallback")
                self.assertTrue(first["identity"].startswith("fallback:registry_missing"))
                self.assertEqual(record_event.call_count, 1)


if __name__ == "__main__":
    unittest.main()
