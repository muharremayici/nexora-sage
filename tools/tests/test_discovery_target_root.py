from __future__ import annotations

import tempfile
import unittest
import json
import hashlib
from copy import deepcopy
from pathlib import Path

import pytest
from tools import config_compiler
from tools.core import config as runtime_config
from tools.core.repository_topology import scope_authority_id, runtime_project_projection
from tools.external_target_preflight import build_preflight
from tools.orchestrators import discovery
from tools.orchestrators.discovery import resolve_discovery_root, workspace_root_reference


@pytest.mark.parametrize("shape,expected", [
    ("vite", "src"), ("empty_workspaces", "src"), ("workspaces_list", "."),
    ("workspaces_object", "."), ("nested", "."), ("next_router", "."),
    ("root_only", "."), ("next_without_router", "src"), ("bare_packages", "src"),
])
def test_discovery_and_real_preflight_share_root_and_compiler_scope(tmp_path, monkeypatch, shape, expected):
    root = tmp_path / "target"
    root.mkdir()
    package = {"dependencies": {"react": "19"}, "devDependencies": {"vite": "6"}}
    if shape != "root_only":
        (root / "src").mkdir()
        (root / "src" / "index.ts").write_text("export const source = 1;")
    else:
        (root / "index.ts").write_text("export const source = 1;")
    if shape == "empty_workspaces":
        package["workspaces"] = []
    elif shape in {"workspaces_list", "nested"}:
        package["workspaces"] = ["packages/*"]
    elif shape == "workspaces_object":
        package["workspaces"] = {"packages": ["packages/*"]}
    if shape in {"next_router", "next_without_router"}:
        package["dependencies"]["next"] = "15"
    if shape == "next_router":
        (root / "src" / "app").mkdir()
        (root / "src" / "app" / "page.tsx").write_text("export default function Page() {return null;}")
    if shape == "bare_packages":
        (root / "packages").mkdir()
    if shape == "nested":
        for name in ("packages/widget", "Variations/Legacy"):
            child = root / name
            child.mkdir(parents=True)
            (child / "package.json").write_text('{"name":"fixture-child"}')
            (child / "index.ts").write_text("export const child = 1;")
    (root / "package.json").write_text(json.dumps(package))
    monkeypatch.setattr(discovery, "ROOT_DIR", root)
    monkeypatch.setattr(discovery, "DYNAMIC_CONFIG", {})
    monkeypatch.delenv("CODEMAPS_TOPOLOGY_MODE", raising=False)
    left = discovery.resolve_discovery_topology()
    right = build_preflight(root)["summary"]["analysis_scope"]
    for top in (left, right):
        assert top["selected_projects"]["MAIN"] == expected
        assert scope_authority_id(top, None) == top["topology_authority_id"]
        assert runtime_project_projection(top, "MISSING")["effective_runtime_projects"] == {}
    for field in ("selected_projects", "project_candidates", "project_ownership_exclusions",
                  "project_candidate_selection_evidence", "project_candidate_relationship_roles",
                  "topology_authority_id", "main_root_decision"):
        assert left[field] == right[field], field
    declared = {"workspace_root": str(root), "source_extensions": [".ts", ".tsx"],
        "skip_dirs": [], "project_roles": left["selected_project_roles"],
        "variations": left["selected_projects"], "_repository_topology": left,
        "architecture": {"module_root": "src"}, "plugins": []}
    overrides = {key: deepcopy(declared[key]) for key in (
        "workspace_root", "source_extensions", "skip_dirs", "project_roles")}
    overrides["architecture"] = {"module_root": ".", "detected_profile": "USER_SELECTED"}
    for name, value in (("DISCOVERY_PATH", declared), ("OVERRIDES_PATH", overrides), ("CONFIG_PATH", {})):
        path = tmp_path / (name + ".json")
        path.write_text(json.dumps(value))
        monkeypatch.setattr(config_compiler, name, path)
    compiled = config_compiler.compile_runtime_config()
    assert compiled["variations"] == left["selected_projects"]
    assert compiled["architecture"]["module_root"] == "."
    assert compiled["architecture"]["detected_profile"] == "USER_SELECTED"
    if shape == "nested":
        assert set(compiled["variations"]) == {"MAIN", "WIDGET", "LEGACY"}
        assert runtime_project_projection(left, "WIDGET")["effective_runtime_projects"] == {"WIDGET": "packages/widget"}
        declared.pop("_repository_topology")
        config_compiler.DISCOVERY_PATH.write_text(json.dumps(declared))
        assert "MAIN" not in config_compiler.compile_runtime_config()["variations"]


def test_preflight_root_decision_transport_is_reused_without_reinference(tmp_path, monkeypatch):
    # This tests transport after receipt admission, not a bypass of receipt validation.
    (tmp_path / "src").mkdir()
    (tmp_path / "package.json").write_text('{"workspaces":["packages/*"]}')
    preflight = build_preflight(tmp_path)
    monkeypatch.setenv("CODEMAPS_TARGET_ROOT", str(tmp_path))
    monkeypatch.setattr(runtime_config, "_load_external_target_preflight_receipt", lambda *a: preflight)
    monkeypatch.setattr(runtime_config, "resolve_main_project_root", lambda *a, **k: pytest.fail("receipt was re-inferred"))
    projected = runtime_config._apply_target_root_override({"variations": {"PRIVATE": "private"}})
    monkeypatch.setattr(discovery, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(discovery, "DYNAMIC_CONFIG", projected)
    monkeypatch.setattr(discovery, "resolve_main_project_root", lambda *a, **k: pytest.fail("receipt was re-inferred"))
    top = discovery.resolve_discovery_topology()
    original = preflight["summary"]["analysis_scope"]
    assert top["main_root_decision"] == original["main_root_decision"]
    assert top["selected_projects"] == original["selected_projects"] == {"MAIN": "."}
    assert scope_authority_id(top, None) == original["topology_authority_id"]


def test_runtime_fallback_uses_shared_decision_not_private_architecture(tmp_path, monkeypatch):
    (tmp_path / "src").mkdir()
    (tmp_path / "package.json").write_text('{"workspaces":["packages/*"]}')
    monkeypatch.setenv("CODEMAPS_TARGET_ROOT", str(tmp_path))
    monkeypatch.setattr(runtime_config, "_load_external_target_preflight_receipt", lambda *a: None)
    result = runtime_config._apply_target_root_override({
        "variations": {"PRIVATE": "private"}, "architecture": {"module_root": "private"}})
    assert result["variations"] == {"MAIN": "."}
    assert result["_target_root_override"]["main_root_decision"]["resolution_basis"] == "declared_workspace_root"


@pytest.mark.parametrize("mutation", ["changed", "missing"])
def test_admitted_receipt_rejects_changed_root_policy_without_target_recrawl(tmp_path, monkeypatch, mutation):
    root = tmp_path / "target"
    root.mkdir()
    (root / "package.json").write_text('{"workspaces":["packages/*"]}')
    payload = build_preflight(root)
    payload["meta"]["run_id"] = "fixture-preflight"
    receipt = tmp_path / "runs" / "fixture-preflight.json"
    receipt.parent.mkdir()
    receipt.write_text(json.dumps(payload), encoding="utf-8")
    env = {"CODEMAPS_TARGET_PREFLIGHT_RECEIPT": str(receipt),
           "CODEMAPS_TARGET_PREFLIGHT_RECEIPT_SHA256": hashlib.sha256(receipt.read_bytes()).hexdigest()}
    assert runtime_config.load_external_target_preflight_receipt(root, source_env=env) == payload
    registry = deepcopy(runtime_config.load_language_registry())
    if mutation == "missing":
        registry.pop("repository_main_root_policy")
    else:
        registry["repository_main_root_policy"]["source_directories"].append("source")
    monkeypatch.setattr(runtime_config, "load_language_registry", lambda: registry)
    with pytest.raises(RuntimeError, match="MAIN root decision is stale"):
        runtime_config.load_external_target_preflight_receipt(root, source_env=env)


def test_entrypoint_smoke_root_selection_does_not_become_full_topology(tmp_path, monkeypatch):
    (tmp_path / "src").mkdir()
    monkeypatch.setattr(discovery, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(discovery, "DYNAMIC_CONFIG", {})
    monkeypatch.setattr(discovery, "resolve_repository_topology", lambda *a, **k: pytest.fail("smoke expanded into a full crawl"))
    result = discovery.resolve_discovery_topology(profile="entrypoint-smoke")
    assert result["selected_projects"] == {"MAIN": "src"}


def test_invalid_main_root_cannot_authorize_default_or_runtime_fallback(tmp_path, monkeypatch):
    from tools.core import repository_root
    (tmp_path / "src").mkdir()
    monkeypatch.setattr(repository_root, "_is_target_path_contained",
                        lambda root, path, **kw: path.name != "src")
    monkeypatch.setattr(discovery, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(discovery, "DYNAMIC_CONFIG", {})
    with pytest.raises(RuntimeError, match="escaping_root_input"):
        discovery.resolve_discovery_topology()
    projection = runtime_config.external_target_scope_projection(tmp_path)
    assert projection["status"] == "invalid"
    assert projection["project_relative_path"] is None
    monkeypatch.setenv("CODEMAPS_TARGET_ROOT", str(tmp_path))
    monkeypatch.setattr(runtime_config, "_load_external_target_preflight_receipt", lambda *a: None)
    with pytest.raises(RuntimeError, match="escaping_root_input"):
        runtime_config._apply_target_root_override({})

class DiscoveryTargetRootTests(unittest.TestCase):
    def test_explicit_target_root_overrides_installation_parent(self):
        with tempfile.TemporaryDirectory() as install_tmp, tempfile.TemporaryDirectory() as target_tmp:
            install_root = Path(install_tmp)
            target_root = Path(target_tmp)

            resolved = resolve_discovery_root(
                code_maps_dir=install_root,
                environment={"CODEMAPS_TARGET_ROOT": str(target_root)},
            )

        self.assertEqual(resolved, target_root.resolve())

    def test_public_projection_requires_explicit_target_root(self):
        with tempfile.TemporaryDirectory() as install_tmp:
            install_root = Path(install_tmp)
            (install_root / "PUBLIC_DISTRIBUTION_MANIFEST.json").write_text("{}", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "requires an explicit target repository"):
                resolve_discovery_root(code_maps_dir=install_root, environment={})

    def test_internal_legacy_layout_retains_parent_default_without_public_marker(self):
        with tempfile.TemporaryDirectory() as parent_tmp:
            install_root = Path(parent_tmp) / "sage-install"
            install_root.mkdir()

            resolved = resolve_discovery_root(code_maps_dir=install_root, environment={})

        self.assertEqual(resolved, install_root.parent.resolve())

    def test_workspace_reference_is_relative_to_installation(self):
        with tempfile.TemporaryDirectory() as parent_tmp:
            parent = Path(parent_tmp)
            install_root = parent / "sage-install"
            target_root = parent / "target-repository"
            install_root.mkdir()
            target_root.mkdir()

            reference = workspace_root_reference(target_root, code_maps_dir=install_root)

        self.assertEqual(reference, "../target-repository")


if __name__ == "__main__":
    unittest.main()
