from __future__ import annotations

import json

from tools.core.repository_topology import (
    attach_declared_exclusions_to_nearest_owner,
    is_project_owned_path,
    project_ownership_exclusions,
    prune_owned_walk_dirs,
    resolve_repository_topology,
)


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
        "execution_role": "companion",
        "authority": "evidence_backed_declared_workspace_edge",
        "confidence": "high",
        "relationship_resolved": True,
    }
    assert first["project_candidate_role_authority"]["LEGACY"] == {
        "execution_role": "variant",
        "authority": "policy_inferred_relation_container",
        "confidence": "medium",
        "relationship_resolved": False,
    }
    assert first["selection_mode"] == "evidence_backed_auto"


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
    assert topology["selected_projects"] == {"MAIN": "."}
    assert topology["excluded_projects"] == {
        "EMBEDDED_SAGE_CLIENT": "Embedded SAGE Client",
    }
    assert topology["project_candidate_role_authority"]["EMBEDDED_SAGE_CLIENT"] == {
        "execution_role": "companion",
        "authority": "conservative_companion_execution_fallback",
        "confidence": "low",
        "relationship_resolved": False,
    }
    assert topology["project_candidate_system_kinds"]["EMBEDDED_SAGE_CLIENT"] == {
        "kind": "unknown",
        "authority": "unresolved_without_evidence_bearing_system_kind_classifier",
    }
    assert topology["project_ownership_exclusions"] == {
        "MAIN": ["Embedded SAGE Client"],
    }


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
    assert topology["selected_projects"] == {"MAIN": "."}
    evidence = topology["project_candidate_selection_evidence"]["INDEPENDENT_PRODUCT"]
    assert evidence["candidate_boundary_strength"] == "strong_structural"
    assert evidence["automatic_selection_eligible"] is False
    assert topology["project_ownership_exclusions"] == {
        "MAIN": ["Independent Product"],
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
    assert topology["excluded_projects"] == {"SRC": "src"}
    assert topology["project_ownership_exclusions"] == {"MAIN": ["src"]}


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
