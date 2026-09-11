from __future__ import annotations

import json
import subprocess
import sys

from tools.core.repository_topology import (
    attach_declared_exclusions_to_nearest_owner,
    classify_project_system_kind,
    is_project_owned_path,
    project_ownership_exclusions,
    prune_owned_walk_dirs,
    resolve_repository_topology,
)


def _system_kind_policy() -> dict:
    return {
        "contract": "technical_system_kind_static_inventory_v1",
        "allowed_kinds": [
            "application", "library", "service", "tool", "configuration",
            "documentation", "asset_bundle", "unknown",
        ],
        "confidence_precedence": ["high", "medium", "low"],
        "kind_precedence": [
            "documentation", "configuration", "asset_bundle", "tool",
            "service", "application", "library",
        ],
        "same_confidence_conflict_policy": "unknown",
        "manifest_dependency_sections": ["dependencies", "devDependencies"],
        "documentation_extensions": [".md", ".mdx"],
        "configuration_extensions": [".json", ".yaml", ".yml"],
        "asset_extensions": [".png", ".svg"],
        "documentation_dependency_markers": ["mintlify"],
        "documentation_script_tokens": ["mintlify"],
        "application_dependency_markers": ["next"],
        "service_dependency_markers": ["fastify"],
        "application_dependency_sections": ["dependencies", "devDependencies"],
        "service_dependency_sections": ["dependencies", "optionalDependencies"],
        "application_script_names": ["dev", "start"],
        "tool_manifest_fields": ["bin"],
        "library_manifest_fields": ["exports", "main", "types"],
        "dominance_threshold": 0.6,
        "minimum_dominant_file_count": 1,
    }


def _kind_inventory(**overrides) -> dict:
    payload = {
        "file_count": 2,
        "inventory_truncated": False,
        "analysis_source_file_count": 0,
        "analysis_config_file_count": 0,
        "extension_counts": {".json": 2},
        "manifest_files": {"javascript_node": ["package.json"]},
    }
    payload.update(overrides)
    return payload


def test_static_system_kind_classifier_preserves_distinct_non_program_projects():
    policy = _system_kind_policy()
    configuration = classify_project_system_kind(
        _kind_inventory(extension_counts={".json": 1, ".yml": 1}, file_count=2),
        {"name": "config-package"},
        policy,
    )
    documentation = classify_project_system_kind(
        _kind_inventory(extension_counts={".mdx": 8, ".json": 1}, file_count=9),
        {"scripts": {"dev": "npx mintlify dev"}},
        policy,
    )
    assets = classify_project_system_kind(
        _kind_inventory(extension_counts={".svg": 4}, file_count=4, manifest_files={}),
        {},
        policy,
    )

    assert configuration["kind"] == "configuration"
    assert documentation["kind"] == "documentation"
    assert assets["kind"] == "asset_bundle"


def test_static_system_kind_classifier_uses_manifest_identity_and_fails_closed():
    policy = _system_kind_policy()
    source = _kind_inventory(
        file_count=4,
        analysis_source_file_count=3,
        extension_counts={".ts": 3, ".json": 1},
    )

    assert classify_project_system_kind(source, {"bin": {"cli": "dist/cli.js"}}, policy)["kind"] == "tool"
    assert classify_project_system_kind(
        source,
        {"dependencies": {"fastify": "1"}},
        policy,
    )["kind"] == "service"
    assert classify_project_system_kind(
        source,
        {"dependencies": {"next": "1"}, "scripts": {"dev": "next dev"}},
        policy,
    )["kind"] == "application"
    assert classify_project_system_kind(
        source,
        {
            "dependencies": {"next": "1"},
            "devDependencies": {"fastify": "1"},
            "scripts": {"dev": "next dev"},
        },
        policy,
    )["kind"] == "application"
    assert classify_project_system_kind(source, {"exports": {".": "./src/index.ts"}}, policy)["kind"] == "library"
    assert classify_project_system_kind(source, {}, policy)["kind"] == "unknown"
    assert classify_project_system_kind(
        {**source, "inventory_truncated": True},
        {"bin": "cli.js"},
        policy,
    )["authority"] == "inventory_truncated"


def test_static_system_kind_classifier_uses_policy_order_and_refuses_equal_confidence_conflicts():
    policy = _system_kind_policy()
    source = _kind_inventory(
        file_count=4,
        analysis_source_file_count=3,
        extension_counts={".ts": 3, ".json": 1},
    )

    application_with_exports = classify_project_system_kind(
        source,
        {
            "dependencies": {"next": "1"},
            "scripts": {"dev": "next dev"},
            "exports": {".": "./src/index.ts"},
        },
        policy,
    )
    assert application_with_exports["kind"] == "application"
    assert application_with_exports["candidate_kinds"] == ["application", "library"]

    ambiguous = classify_project_system_kind(
        source,
        {
            "dependencies": {"next": "1", "fastify": "1"},
            "scripts": {"start": "node server.js"},
        },
        policy,
    )
    assert ambiguous == {
        "kind": "unknown",
        "authority": "ambiguous_static_system_kind_evidence",
        "confidence": "none",
        "evidence": [
            "service_framework_dependency",
            "application_framework_and_runtime_script",
        ],
        "candidate_kinds": ["service", "application"],
    }

    incomplete_policy = {**policy, "kind_precedence": ["application"]}
    assert classify_project_system_kind(source, {}, incomplete_policy)["authority"] == (
        "system_kind_policy_unavailable"
    )


def test_scope_identity_preserves_frozen_canonical_hashes():
    from tools.core.analysis_scope_authority import scope_authority_id as consumer_id
    from tools.core.repository_topology import scope_authority_id

    topology = {"ontology_contract": "fixture", "selected_projects": {"MAIN": "src", "WEB": "web"}}
    expected = {
        None: "sha256:cef557277b831458b01694f0a3f95fba15204ce90bef5e9f96c52cd4a938a0a5",
        "main": "sha256:59fa57fc1476d7b18e58913459a330541b70b3360640c5d8c1b253d22b4ba255",
        "missing": "sha256:b6d6f4f7c8684ffbf6e387cd2b3204de46d5f50612cb2c5d66f15debc6f97275",
    }
    assert consumer_id is scope_authority_id
    for selection, identity in expected.items():
        assert scope_authority_id(topology, selection) == identity
    assert scope_authority_id(topology, "MAIN, main") == expected["main"]
    changed = {**topology, "selected_projects": {"MAIN": "other", "WEB": "web"}}
    assert scope_authority_id(changed, "MAIN") != expected["main"]


def test_topology_import_does_not_initialize_storage_or_config():
    result = subprocess.run(
        [
            sys.executable, "-B", "-c",
            "import sys; import tools.core.repository_topology; "
            "assert not set(sys.modules) & "
            "{'tools.core.config', 'tools.core.json_io', 'tools.core.artifact_store', "
            "'tools.core.analysis_scope_authority'}",
        ],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr


def _role_markers() -> dict:
    return {
        "host_aliases": ["MAIN"],
        "variant_containers": ["variations"],
        "companion_containers": ["packages"],
        "variant_leaf_tokens": ["legacy"],
        "companion_path_tokens": [],
    }


def _is_manifest(name: str) -> bool:
    return name in {"package.json", "pyproject.toml"}


def test_repository_source_does_not_change_canonical_topology(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "packages" / "ui").mkdir(parents=True)
    (tmp_path / "Variations" / "Legacy").mkdir(parents=True)
    (tmp_path / "package.json").write_text(
        json.dumps({"workspaces": ["packages/*"]}),
        encoding="utf-8",
    )
    (tmp_path / "Variations" / "Legacy" / "package.json").write_text(
        "{}",
        encoding="utf-8",
    )

    first = resolve_repository_topology(
        tmp_path,
        main_project_path=".",
        structural_markers={"src", "app"},
        role_markers=_role_markers(),
        config_or_manifest_predicate=_is_manifest,
    )
    second = resolve_repository_topology(
        tmp_path,
        main_project_path=".",
        structural_markers={"src", "app"},
        role_markers=_role_markers(),
        config_or_manifest_predicate=_is_manifest,
    )

    assert first == second
    assert first["discovered_topology"] == "multi_project"
    assert first["selected_projects"] == {
        "MAIN": ".",
        "UI": "packages/ui",
        "LEGACY": "Variations/Legacy",
    }
    assert first["selected_project_roles"] == {
        "MAIN": "host",
        "UI": "companion",
        "LEGACY": "variant",
    }
    assert first["project_candidate_role_authority"]["UI"] == {
        "relationship_role": "companion",
        "authority": "declared_workspace_edge_plus_policy_relation_container",
        "confidence": "medium",
        "relationship_resolved": False,
    }
    assert first["project_candidate_role_authority"]["LEGACY"] == {
        "relationship_role": "variant",
        "authority": "policy_inferred_relation_container",
        "confidence": "medium",
        "relationship_resolved": False,
    }
    assert first["selection_mode"] == "evidence_backed_auto"
    assert first["topology_authority_id"].startswith("sha256:")
    assert len(first["topology_authority_id"]) == len("sha256:") + 64


def test_single_projection_does_not_relabel_discovered_topology(tmp_path):
    (tmp_path / "packages" / "api").mkdir(parents=True)
    (tmp_path / "package.json").write_text(
        json.dumps({"workspaces": ["packages/*"]}),
        encoding="utf-8",
    )

    topology = resolve_repository_topology(
        tmp_path,
        requested_mode="single_project",
        role_markers=_role_markers(),
    )

    assert topology["discovered_topology"] == "multi_project"
    assert topology["analysis_projection"] == "single_project"
    assert topology["selected_projects"] == {"MAIN": "."}
    assert topology["excluded_projects"] == {"API": "packages/api"}
    assert topology["project_ownership_exclusions"] == {
        "MAIN": ["packages/api"],
    }
    assert topology["topology_authority_id"].startswith("sha256:")


def test_project_ownership_excludes_nested_selected_roots_from_parent(tmp_path):
    root = tmp_path.resolve()
    projects = {
        "MAIN": root,
        "WEB": root / "packages" / "web",
        "API": root / "packages" / "api",
        "EXTERNAL": tmp_path.parent / "external",
    }

    exclusions = project_ownership_exclusions(projects)

    assert exclusions["MAIN"] == [
        (root / "packages" / "api").resolve(),
        (root / "packages" / "web").resolve(),
    ]
    assert exclusions["WEB"] == []
    assert exclusions["API"] == []
    assert exclusions["EXTERNAL"] == []


def test_walk_pruning_preserves_container_and_excludes_nested_project(tmp_path):
    packages = tmp_path / "packages"
    web = packages / "web"
    api = packages / "api"
    web.mkdir(parents=True)
    api.mkdir()
    directories = ["web", "api", "node_modules"]

    prune_owned_walk_dirs(
        packages,
        directories,
        skipped_names={"node_modules"},
        excluded_roots={web},
    )

    assert directories == ["api"]


def test_surgical_path_must_remain_inside_its_discovered_project_owner(tmp_path):
    root = tmp_path.resolve()
    child = root / "packages" / "web"
    child.mkdir(parents=True)

    assert is_project_owned_path(
        root,
        "src/app.ts",
        excluded_roots={child},
    )
    assert not is_project_owned_path(
        root,
        "packages/web/App.tsx",
        excluded_roots={child},
    )
    assert not is_project_owned_path(
        root,
        "../outside.py",
        excluded_roots={child},
    )


def test_project_name_does_not_create_an_implicit_exclusion(tmp_path):
    project = tmp_path / "Embedded SAGE Client"
    project.mkdir()
    (project / "package.json").write_text("{}", encoding="utf-8")

    topology = resolve_repository_topology(
        tmp_path,
        config_or_manifest_predicate=_is_manifest,
    )

    assert topology["project_candidates"] == {
        "MAIN": ".",
        "EMBEDDED_SAGE_CLIENT": "Embedded SAGE Client",
    }
    assert topology["selected_projects"] == {
        "MAIN": ".",
        "EMBEDDED_SAGE_CLIENT": "Embedded SAGE Client",
    }
    assert topology["excluded_projects"] == {}
    assert topology["project_candidate_role_authority"]["EMBEDDED_SAGE_CLIENT"] == {
        "relationship_role": "unresolved",
        "authority": "relationship_unresolved_analysis_coverage_selected",
        "confidence": "low",
        "relationship_resolved": False,
    }
    assert topology["project_candidate_system_kinds"]["EMBEDDED_SAGE_CLIENT"] == {
        "kind": "unknown",
        "authority": "unresolved_without_evidence_bearing_system_kind_classifier",
    }
    assert topology["project_ownership_exclusions"] == {
        "MAIN": ["Embedded SAGE Client"],
        "EMBEDDED_SAGE_CLIENT": [],
    }
    assert topology["coverage_only_projects"] == {
        "EMBEDDED_SAGE_CLIENT": "Embedded SAGE Client",
    }
    assert topology["comparative_analysis_enabled"] is False


def test_explicit_multi_project_projection_selects_all_visible_candidates(tmp_path):
    project = tmp_path / "Embedded Tool"
    project.mkdir()
    (project / "package.json").write_text("{}", encoding="utf-8")

    topology = resolve_repository_topology(
        tmp_path,
        requested_mode="multi_project",
        config_or_manifest_predicate=_is_manifest,
    )

    assert topology["selected_projects"] == {
        "MAIN": ".",
        "EMBEDDED_TOOL": "Embedded Tool",
    }
    assert topology["selection_mode"] == "all_candidates_explicit"
    assert topology["selected_project_roles"]["EMBEDDED_TOOL"] == "unresolved"


def test_strong_structure_proves_candidate_not_companion_relationship(tmp_path):
    project = tmp_path / "Independent Product"
    (project / "src").mkdir(parents=True)
    (project / "app").mkdir()

    topology = resolve_repository_topology(
        tmp_path,
        structural_markers={"src", "app"},
    )

    assert topology["project_candidates"] == {
        "MAIN": ".",
        "INDEPENDENT_PRODUCT": "Independent Product",
    }
    assert topology["selected_projects"] == {
        "MAIN": ".",
        "INDEPENDENT_PRODUCT": "Independent Product",
    }
    evidence = topology["project_candidate_selection_evidence"]["INDEPENDENT_PRODUCT"]
    assert evidence["candidate_boundary_strength"] == "strong_structural"
    assert evidence["automatic_selection_eligible"] is True
    assert evidence["selection_reasons"] == ["strong_independent_structure"]
    assert topology["project_ownership_exclusions"] == {
        "MAIN": ["Independent Product"],
        "INDEPENDENT_PRODUCT": [],
    }


def test_manifest_owned_source_structure_is_not_a_nested_project_boundary(tmp_path):
    source_root = tmp_path / "src"
    for name in ("app", "components", "pages"):
        (source_root / name).mkdir(parents=True)
    (source_root / "app" / "page.tsx").write_text(
        "export default function Page() { return null }\n",
        encoding="utf-8",
    )
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")

    topology = resolve_repository_topology(
        tmp_path,
        structural_markers={"src", "app", "components", "pages"},
        config_or_manifest_predicate=_is_manifest,
    )

    assert topology["discovered_topology"] == "single_project"
    assert topology["project_candidates"] == {"MAIN": "."}
    assert topology["selected_projects"] == {"MAIN": "."}
    assert topology["project_ownership_exclusions"] == {"MAIN": []}


def test_manifest_ownership_flows_through_unmarked_containers(tmp_path):
    nested_tests = tmp_path / "tests" / "unit_tests"
    (nested_tests / "extensions").mkdir(parents=True)
    (nested_tests / "utils").mkdir()
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")

    topology = resolve_repository_topology(
        tmp_path,
        structural_markers={"extensions", "utils"},
        config_or_manifest_predicate=_is_manifest,
    )

    assert topology["project_candidates"] == {"MAIN": "."}
    assert topology["selected_projects"] == {"MAIN": "."}
    assert topology["project_ownership_exclusions"] == {"MAIN": []}


def test_manifest_inside_source_structure_preserves_real_nested_boundary(tmp_path):
    source_root = tmp_path / "src"
    (source_root / "app").mkdir(parents=True)
    (source_root / "components").mkdir()
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    (source_root / "package.json").write_text("{}", encoding="utf-8")

    topology = resolve_repository_topology(
        tmp_path,
        structural_markers={"src", "app", "components"},
        config_or_manifest_predicate=_is_manifest,
    )

    assert topology["project_candidates"] == {"MAIN": ".", "SRC": "src"}
    assert topology["selected_projects"] == {"MAIN": ".", "SRC": "src"}
    assert topology["excluded_projects"] == {}
    assert topology["project_ownership_exclusions"] == {"MAIN": ["src"], "SRC": []}


def test_polyglot_candidates_without_root_workspace_are_selected_for_coverage(tmp_path):
    backend = tmp_path / "superset"
    frontend = tmp_path / "superset-frontend"
    (backend / "src").mkdir(parents=True)
    (backend / "tests").mkdir()
    frontend.mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname='host'\n", encoding="utf-8")
    (frontend / "package.json").write_text('{"name":"frontend"}', encoding="utf-8")

    topology = resolve_repository_topology(
        tmp_path,
        structural_markers={"src", "tests"},
        role_markers=_role_markers(),
        config_or_manifest_predicate=_is_manifest,
    )

    assert topology["selected_projects"] == {
        "MAIN": ".",
        "SUPERSET_FRONTEND": "superset-frontend",
    }
    assert topology["selected_project_roles"] == {
        "MAIN": "host",
        "SUPERSET_FRONTEND": "unresolved",
    }
    assert topology["excluded_projects"] == {}
    assert topology["coverage_only_projects"] == {
        "SUPERSET_FRONTEND": "superset-frontend",
    }
    assert topology["relationship_operation_projects"] == {"MAIN": "."}
    assert topology["comparative_analysis_enabled"] is False


def test_mattermost_style_polyglot_projects_are_covered_without_companion_claim(tmp_path):
    (tmp_path / "server").mkdir()
    (tmp_path / "webapp").mkdir()
    (tmp_path / "e2e-tests" / "playwright").mkdir(parents=True)
    (tmp_path / "server" / "pyproject.toml").write_text("[project]\nname='server'\n", encoding="utf-8")
    (tmp_path / "webapp" / "package.json").write_text('{"name":"webapp"}', encoding="utf-8")
    (tmp_path / "e2e-tests" / "playwright" / "package.json").write_text(
        '{"name":"playwright"}',
        encoding="utf-8",
    )

    topology = resolve_repository_topology(
        tmp_path,
        role_markers=_role_markers(),
        config_or_manifest_predicate=_is_manifest,
    )

    assert topology["selected_projects"] == {
        "MAIN": ".",
        "SERVER": "server",
        "WEBAPP": "webapp",
        "PLAYWRIGHT": "e2e-tests/playwright",
    }
    assert topology["coverage_only_projects"] == {
        "SERVER": "server",
        "WEBAPP": "webapp",
        "PLAYWRIGHT": "e2e-tests/playwright",
    }
    assert topology["relationship_operation_projects"] == {"MAIN": "."}


def test_sentry_style_manifest_owner_keeps_source_package_in_main(tmp_path):
    source_package = tmp_path / "src" / "sentry"
    (source_package / "components").mkdir(parents=True)
    (source_package / "services").mkdir()
    api_docs = tmp_path / "api-docs"
    api_docs.mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname='sentry-host'\n", encoding="utf-8")
    (api_docs / "package.json").write_text('{"name":"api-docs"}', encoding="utf-8")

    topology = resolve_repository_topology(
        tmp_path,
        structural_markers={"components", "services"},
        role_markers=_role_markers(),
        config_or_manifest_predicate=_is_manifest,
    )

    assert topology["selected_projects"] == {
        "MAIN": ".",
        "API_DOCS": "api-docs",
    }
    assert "src/sentry" not in topology["project_candidates"].values()
    assert topology["project_ownership_exclusions"] == {
        "MAIN": ["api-docs"],
        "API_DOCS": [],
    }


def test_explicit_managed_projection_predicate_excludes_candidate(tmp_path):
    project = tmp_path / "projection"
    project.mkdir()
    (project / "package.json").write_text("{}", encoding="utf-8")

    topology = resolve_repository_topology(
        tmp_path,
        excluded_path_predicate=lambda path: path.name == "projection",
        config_or_manifest_predicate=_is_manifest,
    )

    assert topology["project_candidates"] == {"MAIN": "."}


def test_explicit_excluded_root_is_bound_to_nearest_project_owner(tmp_path):
    embedded_sage = tmp_path / "SAGE"
    embedded_sage.mkdir()
    (embedded_sage / "sage.py").write_text("print('tool')\n", encoding="utf-8")

    topology = resolve_repository_topology(
        tmp_path,
        excluded_paths=[embedded_sage],
        config_or_manifest_predicate=_is_manifest,
    )

    assert topology["project_candidates"] == {"MAIN": "."}
    assert topology["project_ownership_exclusions"] == {"MAIN": ["SAGE"]}

    root_directories = ["src", "SAGE"]
    prune_owned_walk_dirs(
        tmp_path,
        root_directories,
        skipped_names=set(),
        excluded_roots={
            tmp_path / path
            for path in topology["project_ownership_exclusions"]["MAIN"]
        },
    )
    assert root_directories == ["src"]


def test_declared_exclusion_uses_nearest_selected_owner_and_preserves_self_target(tmp_path):
    package = tmp_path / "packages" / "web"
    embedded_tool = package / "SAGE"
    embedded_tool.mkdir(parents=True)
    projects = {"MAIN": tmp_path, "WEB": package}
    base = project_ownership_exclusions(projects)

    merged = attach_declared_exclusions_to_nearest_owner(
        projects,
        base,
        [embedded_tool, package],
    )

    assert merged["MAIN"] == [package.resolve()]
    assert merged["WEB"] == [embedded_tool.resolve()]


def test_resolved_topology_preserves_generator_exclusions_for_downstream_ownership(tmp_path):
    embedded_tool = tmp_path / "SAGE"
    embedded_tool.mkdir()
    (embedded_tool / "sage.py").write_text("print('tool')\n", encoding="utf-8")

    topology = resolve_repository_topology(
        tmp_path,
        excluded_paths=(path for path in [embedded_tool]),
        config_or_manifest_predicate=_is_manifest,
    )

    assert topology["project_candidates"] == {"MAIN": "."}
    assert topology["project_ownership_exclusions"] == {"MAIN": ["SAGE"]}
