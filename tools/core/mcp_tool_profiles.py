from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from tools.core.json_io import load_json_file


MCP_TOOL_PROFILE_ENV = "SAGE_MCP_TOOL_PROFILE"
PUBLIC_DISTRIBUTION_MANIFEST = "PUBLIC_DISTRIBUTION_MANIFEST.json"
PUBLIC_MCP_TOOL_PROFILES = {
    "target_repository_default",
    "target_repository_followup",
}


def _profile_contract(root: Path) -> tuple[dict[str, Any], dict[str, Any], str]:
    surface = load_json_file(root / "config" / "agent_surface_contract.json", {})
    roles = load_json_file(root / "config" / "mcp_tool_roles.json", {})
    readiness = surface.get("ai_agent_readiness", {}) if isinstance(surface, dict) else {}
    profiles = readiness.get("tool_context_profiles", {}) if isinstance(readiness, dict) else {}
    tools = roles.get("tools", {}) if isinstance(roles, dict) else {}
    return (
        profiles if isinstance(profiles, dict) else {},
        tools if isinstance(tools, dict) else {},
        str(readiness.get("default_tool_context_profile") or "") if isinstance(readiness, dict) else "",
    )


def available_mcp_tool_profiles(root: Path) -> list[str]:
    profiles, _, _ = _profile_contract(root)
    available = {str(name) for name in profiles if str(name).strip()}
    if (root / PUBLIC_DISTRIBUTION_MANIFEST).is_file():
        available &= PUBLIC_MCP_TOOL_PROFILES
    return sorted(available)


def resolve_mcp_tool_profile(root: Path, requested: str | None = None) -> str:
    profiles = available_mcp_tool_profiles(root)
    _, _, default_profile = _profile_contract(root)
    profile = str(requested or os.environ.get(MCP_TOOL_PROFILE_ENV) or default_profile).strip()
    if profile not in profiles:
        raise ValueError(
            f"Unknown MCP tool profile {profile!r}. Available profiles: {', '.join(profiles) or 'none'}"
        )
    return profile


def project_mcp_tool_names(root: Path, profile: str) -> dict[str, Any]:
    profile = resolve_mcp_tool_profile(root, profile)
    profiles, tools, _ = _profile_contract(root)
    row = profiles.get(profile)
    if not isinstance(row, dict):
        raise ValueError(f"MCP tool profile {profile!r} is not declared")

    allowed_roles = {str(value) for value in row.get("roles", []) if str(value).strip()}
    allowed_audiences = {str(value) for value in row.get("allowed_audiences", []) if str(value).strip()}
    include_tools = {str(value) for value in row.get("include_tools", []) if str(value).strip()}
    exclude_tools = {str(value) for value in row.get("exclude_tools", []) if str(value).strip()}
    exclude_heavy = bool(row.get("exclude_heavy", False))

    visible: list[str] = []
    for name, tool in tools.items():
        if not isinstance(tool, dict):
            continue
        role_allowed = str(tool.get("role") or "") in allowed_roles
        audience_allowed = not allowed_audiences or str(tool.get("audience") or "") in allowed_audiences
        heavy_allowed = not exclude_heavy or not bool(tool.get("heavy"))
        if ((role_allowed and audience_allowed and heavy_allowed) or name in include_tools) and name not in exclude_tools:
            visible.append(str(name))

    max_tools = int(row.get("max_tools") or 0)
    if max_tools <= 0 or len(visible) > max_tools:
        raise ValueError(
            f"MCP profile {profile!r} exposes {len(visible)} tools outside max_tools={max_tools}"
        )
    return {
        "profile": profile,
        "system_scope": str(row.get("system_scope") or ""),
        "visible_tools": sorted(visible),
        "declared_tools": sorted(str(name) for name in tools),
        "max_tools": max_tools,
        "exclude_heavy": exclude_heavy,
    }


def requires_sage_developer_mutation_preflight(root: Path, profile: str, tool_name: str) -> bool:
    profiles, tools, _ = _profile_contract(root)
    profile_row = profiles.get(profile) if isinstance(profiles.get(profile), dict) else {}
    tool_row = tools.get(tool_name) if isinstance(tools.get(tool_name), dict) else {}
    return profile_row.get("system_scope") == "SAGE_ON_SAGE" and tool_row.get("mutates") is True


def validate_mcp_tool_profiles(root: Path, registered_tools: set[str]) -> dict[str, Any]:
    profiles, _, default_profile = _profile_contract(root)
    projections: dict[str, Any] = {}
    errors: list[str] = []
    for profile in available_mcp_tool_profiles(root):
        try:
            projections[profile] = project_mcp_tool_names(root, profile)
        except (TypeError, ValueError) as exc:
            errors.append(f"{profile}: {exc}")
    default_tools = set((projections.get(default_profile) or {}).get("visible_tools", []))
    default_scope = str((profiles.get(default_profile) or {}).get("system_scope") or "")
    different_scope_tools = {
        name
        for profile, projection in projections.items()
        if str((profiles.get(profile) or {}).get("system_scope") or "") != default_scope
        for name in projection.get("visible_tools", [])
    }
    missing_system_scope_profiles = sorted(
        profile for profile, row in profiles.items() if not str((row or {}).get("system_scope") or "")
    )
    declared_tools = set((projections.get(default_profile) or {}).get("declared_tools", []))
    server_text = (root / "tools" / "mcp" / "server.py").read_text(encoding="utf-8")
    cli_text = (root / "codemaps.py").read_text(encoding="utf-8")
    runtime_config_text = (root / "tools" / "core" / "mcp_runtime_config.py").read_text(encoding="utf-8")
    runtime_guard_present = "class ProfiledFastMCP(FastMCP)" in server_text and "if allowed is not None and name not in allowed" in server_text
    cli_profile_present = (
        "build_mcp_runtime_contract(" in cli_text
        and "--profile" in cli_text
        and "process_env[MCP_TOOL_PROFILE_ENV] = profile" in runtime_config_text
    )
    return {
        "passed": not errors
        and bool(projections)
        and default_profile in projections
        and bool(default_scope)
        and not missing_system_scope_profiles
        and declared_tools == registered_tools
        and not (default_tools & different_scope_tools)
        and runtime_guard_present
        and cli_profile_present,
        "profiles": projections,
        "errors": errors,
        "missing_registered_tools": sorted(registered_tools - declared_tools),
        "stale_declared_tools": sorted(declared_tools - registered_tools),
        "default_internal_overlap": sorted(default_tools & different_scope_tools),
        "default_system_scope": default_scope,
        "missing_system_scope_profiles": missing_system_scope_profiles,
        "runtime_guard_present": runtime_guard_present,
        "cli_profile_present": cli_profile_present,
    }
