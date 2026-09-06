from __future__ import annotations

import fnmatch
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable, Iterable

TOPOLOGY_MODES = {"auto", "single_project", "multi_project"}


def normalize_project_filter(projects: Iterable[str] | str | None) -> list[str]:
    if isinstance(projects, str):
        values = projects.split(",")
    else:
        values = projects or []
    return sorted({str(value).strip().upper() for value in values if str(value).strip()})


def runtime_project_projection(
    topology: dict[str, Any],
    projects: Iterable[str] | str | None,
) -> dict[str, Any]:
    selected = topology.get("selected_projects")
    selected = selected if isinstance(selected, dict) else {}
    requested = normalize_project_filter(projects)
    selected_by_upper = {str(key).upper(): (str(key), value) for key, value in selected.items()}
    unavailable = sorted(set(requested) - set(selected_by_upper))
    if requested:
        effective = {
            selected_by_upper[key][0]: selected_by_upper[key][1]
            for key in requested
            if key in selected_by_upper
        }
    else:
        effective = dict(selected)
    return {
        "requested_project_filter": requested,
        "effective_runtime_projects": effective,
        "unavailable_requested_projects": unavailable,
    }


def _canonical_identity_payload(
    topology: dict[str, Any],
    runtime_projection: dict[str, Any],
) -> dict[str, Any]:
    return {
        "ontology_contract": topology.get("ontology_contract"),
        "selection_mode": topology.get("selection_mode"),
        "project_candidates": topology.get("project_candidates", {}),
        "project_candidate_relationship_roles": topology.get(
            "project_candidate_relationship_roles",
            topology.get("project_candidate_roles", {}),
        ),
        "selected_projects": topology.get("selected_projects", {}),
        "excluded_projects": topology.get("excluded_projects", {}),
        "excluded_project_reasons": topology.get("excluded_project_reasons", {}),
        "requested_project_filter": runtime_projection.get("requested_project_filter", []),
        "effective_runtime_projects": runtime_projection.get("effective_runtime_projects", {}),
        "unavailable_requested_projects": runtime_projection.get("unavailable_requested_projects", []),
    }


def scope_authority_id(
    topology: dict[str, Any],
    projects: Iterable[str] | str | None,
) -> str:
    runtime_projection = runtime_project_projection(topology, projects)
    encoded = json.dumps(
        _canonical_identity_payload(topology, runtime_projection),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def workspace_patterns(root: Path) -> list[str]:
    patterns: set[str] = set()
    pnpm_workspace = root / "pnpm-workspace.yaml"
    if pnpm_workspace.is_file():
        in_packages = False
        package_indent = 0
        for line in pnpm_workspace.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if re.match(r"^packages\s*:\s*$", stripped):
                in_packages = True
                package_indent = len(line) - len(line.lstrip())
                continue
            if in_packages and not line.startswith(" " * (package_indent + 1)):
                in_packages = False
            if in_packages and stripped.startswith("-"):
                pattern = stripped[1:].strip().strip("'\"").rstrip("/")
                if pattern and not pattern.startswith("@"):
                    patterns.add(pattern)

    package_json = root / "package.json"
    if package_json.is_file():
        try:
            payload = json.loads(package_json.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            payload = {}
        workspaces = payload.get("workspaces", []) if isinstance(payload, dict) else []
        if isinstance(workspaces, list):
            patterns.update(
                str(item).strip().rstrip("/")
                for item in workspaces
                if str(item).strip()
            )
        elif isinstance(workspaces, dict):
            patterns.update(
                str(item).strip().rstrip("/")
                for item in workspaces.get("packages", [])
                if str(item).strip()
            )
    return sorted(patterns)


def _project_key(path: Path, existing: dict[str, str]) -> str:
    key = re.sub(r"[^A-Z0-9_]+", "_", path.name.upper()).strip("_") or "PROJECT"
    if key not in existing:
        return key
    parent = re.sub(r"[^A-Z0-9_]+", "_", path.parent.name.upper()).strip("_")
    candidate = f"{parent}_{key}" if parent else key
    suffix = 2
    while candidate in existing:
        candidate = f"{parent}_{key}_{suffix}" if parent else f"{key}_{suffix}"
        suffix += 1
    return candidate


def discover_project_candidates(
    root: Path,
    *,
    main_project_path: str = ".",
    structural_markers: Iterable[str] = (),
    excluded_paths: Iterable[Path] = (),
    excluded_path_predicate: Callable[[Path], bool] | None = None,
    config_or_manifest_predicate: Callable[[str], bool] | None = None,
    skipped_names: Iterable[str] = (),
    max_depth: int = 4,
) -> dict[str, str]:
    """Discover repository project boundaries without depending on acquisition mode."""

    root = Path(root).resolve()
    projects = {"MAIN": str(main_project_path or ".").replace("\\", "/").strip("/") or "."}
    markers = {str(item).lower() for item in structural_markers if str(item).strip()}
    skipped = {str(item).lower() for item in skipped_names}
    is_config_or_manifest = config_or_manifest_predicate or (lambda _name: False)
    excluded = {Path(path).resolve() for path in excluded_paths}
    declared_workspace_patterns = workspace_patterns(root)
    processed = {root, *excluded}
    queue: list[tuple[Path, int, bool]] = [(root, 0, False)]

    while queue:
        current, depth, inherited_manifest_ownership = queue.pop(0)
        if depth >= max_depth:
            continue
        try:
            entries = list(current.iterdir())
        except (OSError, PermissionError):
            continue
        current_item_names = {item.name.lower() for item in entries}
        current_has_config = any(
            is_config_or_manifest(name)
            for name in current_item_names
        )
        manifest_owned_scope = inherited_manifest_ownership or current_has_config
        for entry in entries:
            if not entry.is_dir():
                continue
            normalized_name = entry.name.strip().lower()
            if (
                entry.name.startswith(".")
                or normalized_name in skipped
            ):
                continue
            resolved = entry.resolve()
            if resolved in processed or (
                excluded_path_predicate is not None
                and excluded_path_predicate(resolved)
            ):
                continue
            processed.add(resolved)
            try:
                item_names = {item.name.lower() for item in entry.iterdir()}
            except (OSError, PermissionError):
                continue
            has_config = any(is_config_or_manifest(name) for name in item_names)
            has_structure = len(markers.intersection(item_names)) >= 2
            relative_path = entry.relative_to(root).as_posix()
            workspace_match = any(
                fnmatch.fnmatch(relative_path, pattern)
                for pattern in declared_workspace_patterns
            )
            parent_owned_structure = (
                has_structure
                and not has_config
                and not workspace_match
                and manifest_owned_scope
            )
            if parent_owned_structure:
                queue.append((entry, depth + 1, manifest_owned_scope))
                continue
            if not (has_config or has_structure or workspace_match):
                queue.append((entry, depth + 1, manifest_owned_scope))
                continue
            if relative_path in projects.values():
                continue
            projects[_project_key(entry, projects)] = relative_path
    return projects


def project_ownership_exclusions(
    projects: dict[str, str | Path],
) -> dict[str, list[Path]]:
    """Return nested project roots that each parent project must not traverse."""

    resolved = {
        str(key): Path(path).resolve()
        for key, path in projects.items()
    }
    exclusions: dict[str, list[Path]] = {}
    for parent_key, parent_root in resolved.items():
        nested_roots = {
            candidate_root
            for candidate_key, candidate_root in resolved.items()
            if candidate_key != parent_key
            and candidate_root != parent_root
            and candidate_root.is_relative_to(parent_root)
        }
        exclusions[parent_key] = sorted(
            nested_roots,
            key=lambda path: (len(path.parts), path.as_posix().lower()),
        )
    return exclusions


def attach_declared_exclusions_to_nearest_owner(
    projects: dict[str, str | Path],
    exclusions: dict[str, list[Path]],
    declared_excluded_paths: Iterable[Path],
) -> dict[str, list[Path]]:
    """Bind explicit traversal exclusions to their nearest selected project."""

    resolved_projects = {
        str(key): Path(path).resolve()
        for key, path in projects.items()
    }
    merged = {
        key: {Path(path).resolve() for path in exclusions.get(key, [])}
        for key in resolved_projects
    }
    for excluded_path in {Path(path).resolve() for path in declared_excluded_paths}:
        owners = [
            (key, project_root)
            for key, project_root in resolved_projects.items()
            if excluded_path != project_root
            and excluded_path.is_relative_to(project_root)
        ]
        if not owners:
            continue
        owner_key, _owner_root = max(owners, key=lambda item: len(item[1].parts))
        merged[owner_key].add(excluded_path)
    topology = {
        key: sorted(
            paths,
            key=lambda path: (len(path.parts), path.as_posix().lower()),
        )
        for key, paths in merged.items()
    }
    return topology


def prune_owned_walk_dirs(
    current_root: str | Path,
    directories: list[str],
    *,
    skipped_names: Iterable[str],
    excluded_roots: Iterable[Path],
) -> None:
    """Mutate an os.walk directory list to preserve nearest-project ownership."""

    root = Path(current_root)
    skipped = {str(name) for name in skipped_names}
    excluded = {Path(path).resolve() for path in excluded_roots}
    directories[:] = [
        directory
        for directory in directories
        if directory not in skipped
        and (root / directory).resolve() not in excluded
    ]


def is_project_owned_path(
    project_root: str | Path,
    relative_path: str | Path,
    *,
    excluded_roots: Iterable[Path],
) -> bool:
    """Return whether a relative file identity belongs to the declared project."""

    root = Path(project_root).resolve()
    candidate = (root / Path(relative_path)).resolve()
    if candidate != root and not candidate.is_relative_to(root):
        return False
    return not any(
        candidate == excluded_root
        or candidate.is_relative_to(excluded_root)
        for excluded_root in {
            Path(path).resolve()
            for path in excluded_roots
        }
    )


def classify_project_roles(
    projects: dict[str, str],
    role_markers: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Classify governance relationship roles without inventing a companion edge."""
    markers = role_markers if isinstance(role_markers, dict) else {}
    host_aliases = {str(item).upper() for item in (markers.get("host_aliases") or ["MAIN"])}
    variant_containers = {str(item).lower() for item in (markers.get("variant_containers") or [])}
    companion_containers = {str(item).lower() for item in (markers.get("companion_containers") or [])}
    variant_leaf_tokens = {str(item).lower() for item in (markers.get("variant_leaf_tokens") or [])}
    companion_path_tokens = {str(item).lower() for item in (markers.get("companion_path_tokens") or [])}
    roles: dict[str, str] = {}

    for key, relative_path in projects.items():
        if key in host_aliases:
            roles[key] = "host"
            continue
        normalized = str(relative_path or "").replace("\\", "/").strip("/")
        parts = [part.lower() for part in normalized.split("/") if part]
        top = parts[0] if parts else ""
        leaf = parts[-1] if parts else ""
        if top in variant_containers or any(token in leaf for token in variant_leaf_tokens):
            roles[key] = "variant"
        elif (
            top in companion_containers
            or leaf in companion_containers
            or any(token in normalized for token in companion_path_tokens)
        ):
            roles[key] = "companion"
        else:
            roles[key] = "unresolved"
    return roles


def project_candidate_selection_evidence(
    root: Path,
    candidates: dict[str, str],
    *,
    structural_markers: Iterable[str] = (),
    role_markers: dict[str, Any] | None = None,
    config_or_manifest_predicate: Callable[[str], bool] | None = None,
) -> dict[str, dict[str, Any]]:
    """Explain why each discovered candidate may or may not run automatically."""

    root = Path(root).resolve()
    markers = {str(item).lower() for item in structural_markers if str(item).strip()}
    declared_workspace_patterns = workspace_patterns(root)
    role_policy = role_markers if isinstance(role_markers, dict) else {}
    is_config_or_manifest = config_or_manifest_predicate or (lambda _name: False)
    variant_containers = {
        str(item).lower()
        for item in (role_policy.get("variant_containers") or [])
    }
    companion_containers = {
        str(item).lower()
        for item in (role_policy.get("companion_containers") or [])
    }
    evidence: dict[str, dict[str, Any]] = {}

    for key, relative_path in candidates.items():
        normalized = str(relative_path or ".").replace("\\", "/").strip("/") or "."
        project_root = root if normalized == "." else root / normalized
        try:
            item_names = {item.name.lower() for item in project_root.iterdir()}
        except (OSError, PermissionError):
            item_names = set()
        has_config = any(is_config_or_manifest(name) for name in item_names)
        structural_matches = sorted(markers.intersection(item_names))
        workspace_matches = sorted(
            pattern
            for pattern in declared_workspace_patterns
            if fnmatch.fnmatch(normalized, pattern)
        )
        parts = [part.lower() for part in normalized.split("/") if part and part != "."]
        top = parts[0] if parts else ""
        explicit_container_role = (
            "variant"
            if top in variant_containers
            else "companion"
            if top in companion_containers
            else None
        )
        relationship_reasons = []
        if key == "MAIN":
            relationship_reasons.append("host_scope")
        if workspace_matches:
            relationship_reasons.append("declared_workspace_match")
        if explicit_container_role:
            relationship_reasons.append(f"declared_{explicit_container_role}_container")
        coverage_reasons = list(relationship_reasons)
        if key != "MAIN" and has_config:
            coverage_reasons.append("candidate_local_config_or_manifest")
        if key != "MAIN" and len(structural_matches) >= 2:
            coverage_reasons.append("strong_independent_structure")
        evidence[key] = {
            "relative_path": normalized,
            "has_config_or_manifest": has_config,
            "structural_matches": structural_matches,
            "workspace_pattern_matches": workspace_matches,
            "explicit_container_role": explicit_container_role,
            "candidate_boundary_strength": (
                "strong_structural"
                if len(structural_matches) >= 2
                else "config_or_manifest"
                if has_config
                else "workspace_or_container_only"
            ),
            "analysis_coverage_eligible": bool(coverage_reasons),
            "automatic_selection_eligible": bool(coverage_reasons),
            "selection_reasons": coverage_reasons,
            "relationship_evidence_reasons": relationship_reasons,
        }
    return evidence


def resolve_repository_topology(
    root: Path,
    *,
    main_project_path: str = ".",
    structural_markers: Iterable[str] = (),
    role_markers: dict[str, Any] | None = None,
    requested_mode: str = "auto",
    excluded_paths: Iterable[Path] = (),
    excluded_path_predicate: Callable[[Path], bool] | None = None,
    config_or_manifest_predicate: Callable[[str], bool] | None = None,
    skipped_names: Iterable[str] = (),
) -> dict[str, Any]:
    if requested_mode not in TOPOLOGY_MODES:
        raise ValueError(f"Unsupported repository topology mode: {requested_mode}")

    excluded_paths = tuple(Path(path).resolve() for path in excluded_paths)

    candidates = discover_project_candidates(
        root,
        main_project_path=main_project_path,
        structural_markers=structural_markers,
        excluded_paths=excluded_paths,
        excluded_path_predicate=excluded_path_predicate,
        config_or_manifest_predicate=config_or_manifest_predicate,
        skipped_names=skipped_names,
    )
    candidate_roles = classify_project_roles(candidates, role_markers)
    candidate_evidence = project_candidate_selection_evidence(
        root,
        candidates,
        structural_markers=structural_markers,
        role_markers=role_markers,
        config_or_manifest_predicate=config_or_manifest_predicate,
    )
    candidate_role_authority = {}
    for key, role in candidate_roles.items():
        evidence = candidate_evidence[key]
        if key == "MAIN":
            authority = "authoritative_host_scope"
            confidence = "high"
            relationship_resolved = True
        elif evidence["explicit_container_role"] == role:
            authority = (
                "declared_workspace_edge_plus_policy_relation_container"
                if evidence["workspace_pattern_matches"]
                else "policy_inferred_relation_container"
            )
            confidence = "medium"
            relationship_resolved = False
        elif evidence["workspace_pattern_matches"]:
            authority = "evidence_backed_workspace_membership_relationship_unresolved"
            confidence = "medium"
            relationship_resolved = False
        elif requested_mode == "auto" and evidence["analysis_coverage_eligible"]:
            authority = "relationship_unresolved_analysis_coverage_selected"
            confidence = "low"
            relationship_resolved = False
        else:
            authority = "relationship_unresolved_requires_explicit_selection"
            confidence = "low"
            relationship_resolved = False
        candidate_role_authority[key] = {
            "relationship_role": role,
            "authority": authority,
            "confidence": confidence,
            "relationship_resolved": relationship_resolved,
        }
    candidate_system_kinds = {
        key: {
            "kind": "unknown",
            "authority": "unresolved_without_evidence_bearing_system_kind_classifier",
        }
        for key in candidates
    }
    discovered_topology = "multi_project" if len(candidates) > 1 else "single_project"
    if requested_mode == "single_project":
        selected_projects = {"MAIN": candidates["MAIN"]}
        selection_mode = "main_only"
    elif requested_mode == "multi_project":
        selected_projects = dict(candidates)
        selection_mode = "all_candidates_explicit"
    else:
        selected_projects = {
            key: value
            for key, value in candidates.items()
            if candidate_evidence[key]["automatic_selection_eligible"]
        }
        selection_mode = "evidence_backed_auto"
    projection_mode = "multi_project" if len(selected_projects) > 1 else "single_project"
    selected_roles = {
        key: candidate_roles[key]
        for key in selected_projects
    }
    excluded_projects = {
        key: value
        for key, value in candidates.items()
        if key not in selected_projects
    }
    excluded_reason = (
        "explicit_single_project_projection"
        if requested_mode == "single_project"
        else "candidate_requires_explicit_selection"
    )
    excluded_project_reasons = {key: excluded_reason for key in excluded_projects}
    relationship_operation_projects = {
        key: value
        for key, value in selected_projects.items()
        if key == "MAIN" or candidate_roles[key] != "unresolved"
    }
    coverage_only_projects = {
        key: value
        for key, value in selected_projects.items()
        if key != "MAIN" and candidate_roles[key] == "unresolved"
    }
    repository_root = Path(root).resolve()
    candidate_ownership_exclusions = project_ownership_exclusions({
        key: repository_root / relative_path
        for key, relative_path in candidates.items()
    })
    selected_project_roots = {
        key: repository_root / relative_path
        for key, relative_path in selected_projects.items()
    }
    ownership_exclusions = {
        key: candidate_ownership_exclusions[key]
        for key in selected_projects
    }
    ownership_exclusions = attach_declared_exclusions_to_nearest_owner(
        selected_project_roots,
        ownership_exclusions,
        excluded_paths,
    )
    topology = {
        "status": "resolved",
        "ontology_contract": "canonical_repository_topology_v1",
        "requested_mode": requested_mode,
        "discovered_topology": discovered_topology,
        "analysis_projection": projection_mode,
        "selection_mode": selection_mode,
        "project_candidates": candidates,
        "project_candidate_roles": candidate_roles,
        "project_candidate_relationship_roles": candidate_roles,
        "project_candidate_role_authority": candidate_role_authority,
        "project_candidate_system_kinds": candidate_system_kinds,
        "project_candidate_selection_evidence": candidate_evidence,
        "selected_projects": selected_projects,
        "selected_project_roles": selected_roles,
        "relationship_operation_projects": relationship_operation_projects,
        "coverage_only_projects": coverage_only_projects,
        "excluded_projects": excluded_projects,
        "excluded_project_reasons": excluded_project_reasons,
        "project_ownership_exclusions": {
            key: [
                path.relative_to(repository_root).as_posix()
                for path in excluded_roots
            ]
            for key, excluded_roots in ownership_exclusions.items()
        },
        "file_ownership_contract": "nearest_discovered_project_root_v1",
        "relationship_role_contract": "governance_relationship_role_v2_unresolved_explicit",
        "system_kind_contract": "technical_system_kind_unresolved_v1",
        "analysis_coverage_contract": "evidence_bearing_candidate_coverage_v1",
        "comparative_analysis_enabled": any(
            role == "variant"
            for key, role in selected_roles.items()
            if key != "MAIN"
        ),
        "candidate_count": len(candidates),
        "selected_project_count": len(selected_projects),
        "topology_is_independent_of_source_mode": True,
    }
    topology["topology_authority_id"] = scope_authority_id(topology, None)
    return topology
