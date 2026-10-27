"""Static repository-root evidence shared by acquisition and canonical topology.

This leaf reads declarations and root markers; it does not select projects,
classify architecture, load runtime config, or authorize execution.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

from tools.core.target_repository_trust import is_target_path_contained as _is_target_path_contained


def workspace_patterns(root: Path, *, path_boundary_state: dict[str, Any] | None = None) -> list[str]:
    patterns: set[str] = set()
    pnpm_workspace = root / "pnpm-workspace.yaml"
    if _is_target_path_contained(root, pnpm_workspace, state=path_boundary_state) and pnpm_workspace.is_file():
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
    if _is_target_path_contained(root, package_json, state=path_boundary_state) and package_json.is_file():
        try:
            payload = json.loads(package_json.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            payload = {}
        workspaces = payload.get("workspaces", []) if isinstance(payload, dict) else []
        if isinstance(workspaces, list):
            patterns.update(
                str(item).strip().rstrip("/")
                for item in workspaces
                if isinstance(item, str) and item.strip()
            )
        elif isinstance(workspaces, dict):
            patterns.update(
                str(item).strip().rstrip("/")
                for item in (workspaces.get("packages") if isinstance(workspaces.get("packages"), list) else [])
                if isinstance(item, str) and item.strip()
            )
    return sorted(patterns)


def main_root_policy_fingerprint(registry: dict[str, Any]) -> str | None:
    """Validate and identify root policy without re-inspecting a target."""
    policy = registry.get("repository_main_root_policy")
    fields = ("source_directories", "workspace_root_files", "router_dependency_sections",
              "router_dependency_names", "router_directories")
    if (not isinstance(policy, dict) or policy.get("contract") != "acquisition_neutral_main_root_v1"
            or any(not isinstance(policy.get(key), list) or not policy[key]
                   or any(not isinstance(value, str) or not value.strip() for value in policy[key])
                   for key in fields)):
        return None
    group = policy.get("router_config_marker_group")
    marker_groups = registry.get("config_file_markers")
    if not isinstance(group, str) or not isinstance(marker_groups, dict):
        return None
    markers = marker_groups.get(group)
    paths = policy["source_directories"] + policy["workspace_root_files"] + policy["router_directories"]
    if (not isinstance(markers, list) or not markers or any(not isinstance(v, str) for v in markers)
            or any(PurePosixPath(p).is_absolute() or ".." in PurePosixPath(p).parts
                   or any(c in p for c in ("\\", ":", "\0")) for p in paths + markers)):
        return None
    return hashlib.sha256(json.dumps(
        {"policy": policy, "router_config_markers": markers}, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()


def resolve_main_project_root(
    root: Path, registry: dict[str, Any], *, path_boundary_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Infer MAIN ownership from confined static facts, never architecture labels."""
    result = {"contract": "acquisition_neutral_main_root_v1", "status": "unresolved",
              "project_relative_path": None, "source_orientation_hint": None,
              "resolution_basis": "main_root_policy_unavailable", "evidence": []}
    identity = main_root_policy_fingerprint(registry)
    if identity is None:
        return result
    result["policy_fingerprint"] = identity
    policy = registry["repository_main_root_policy"]
    markers = registry["config_file_markers"][policy["router_config_marker_group"]]
    paths = policy["source_directories"] + policy["workspace_root_files"] + policy["router_directories"]
    root = Path(root).resolve()
    if not root.is_dir():
        return {**result, "status": "invalid", "resolution_basis": "repository_not_directory"}
    # Check every decision input before reading it. An escaping src link is not
    # evidence that the project should silently be widened to the repo root.
    for relative in paths + markers + ["package.json", "pnpm-workspace.yaml"]:
        if not _is_target_path_contained(root, root / relative, state=path_boundary_state):
            return {**result, "status": "invalid", "resolution_basis": "escaping_root_input"}
    manifest = root / "package.json"
    package = {}
    try:
        if manifest.is_file():
            package = json.loads(manifest.read_text(encoding="utf-8-sig"))
            if not isinstance(package, dict):
                raise ValueError("Package manifest is not an object")
        declarations = package.get("workspaces", [])
        if isinstance(declarations, dict):
            declarations = declarations.get("packages", [])
        if not isinstance(declarations, list) or any(not isinstance(v, str) for v in declarations):
            raise ValueError("Workspace declarations are not literal string patterns")
        patterns = workspace_patterns(root, path_boundary_state=path_boundary_state)
    except (OSError, UnicodeError, ValueError) as exc:
        return {**result, "resolution_basis": "root_manifest_unreadable_or_invalid",
                "evidence": [type(exc).__name__]}
    source = next((p for p in policy["source_directories"] if (root / p).is_dir()), None)
    result["source_orientation_hint"] = source or "."
    evidence = [f"workspace:{pattern}" for pattern in patterns]
    deps = {name for section in policy["router_dependency_sections"]
            for name in (package.get(section) if isinstance(package.get(section), dict) else {})}
    router_markers = [p for p in markers if (root / p).is_file()]
    router_dirs = [p for p in policy["router_directories"] if (root / p).is_dir()]
    tool_files = [p for p in policy["workspace_root_files"] if (root / p).is_file()]
    if patterns:
        selected, basis = ".", "declared_workspace_root"
    elif router_dirs and (deps.intersection(policy["router_dependency_names"]) or router_markers):
        selected, basis = ".", "evidence_backed_router_root"
        evidence += [f"router_directory:{p}" for p in router_dirs]
        evidence += [f"router_config:{p}" for p in router_markers]
        evidence += [f"router_dependency:{p}" for p in deps.intersection(policy["router_dependency_names"])]
    elif tool_files:
        selected, basis = ".", "workspace_tool_root"
        evidence += [f"workspace_tool:{p}" for p in tool_files]
    else:
        selected, basis = source or ".", "source_directory" if source else "repository_root"
        evidence.append(f"directory:{selected}")
    return {**result, "status": "resolved", "project_relative_path": selected,
            "resolution_basis": basis, "evidence": sorted(evidence)}
