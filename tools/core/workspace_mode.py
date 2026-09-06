from __future__ import annotations

from typing import Any, Dict

from tools.core.config import DOCTRINE, DYNAMIC_CONFIG
from tools.core.doctrine_contract import require_dead_code_policy


def get_workspace_mode() -> Dict[str, Any]:
    variations = DYNAMIC_CONFIG.get("variations", {}) or {}
    project_roles = DYNAMIC_CONFIG.get("project_roles", {}) or {}
    project_count = len(variations)
    host_projects = sorted(
        str(key) for key in variations.keys()
        if project_roles.get(key, "host" if str(key) == "MAIN" else "unresolved") == "host"
    )
    variant_projects = sorted(
        str(key) for key in variations.keys()
        if project_roles.get(key) == "variant"
    )
    companion_projects = sorted(
        str(key) for key in variations.keys()
        if project_roles.get(key, "host" if str(key) == "MAIN" else "unresolved") == "companion"
    )
    unresolved_projects = sorted(
        str(key) for key in variations.keys()
        if project_roles.get(key, "host" if str(key) == "MAIN" else "unresolved") == "unresolved"
    )

    if project_count <= 1:
        mode = "single_project"
        comparative = False
    elif variant_projects:
        mode = "multi_project_comparative"
        comparative = True
    else:
        mode = "multi_project_noncomparative"
        comparative = False

    return {
        "mode": mode,
        "project_count": project_count,
        "comparative_enabled": comparative,
        "projects": sorted(str(key) for key in variations.keys()),
        "host_projects": host_projects,
        "variant_projects": variant_projects,
        "companion_projects": companion_projects,
        "unresolved_projects": unresolved_projects,
    }


def get_project_role(project_name: str | None) -> str:
    name = str(project_name or "").strip()
    if not name:
        return "unknown"
    roles = DYNAMIC_CONFIG.get("project_roles", {}) or {}
    if name in roles:
        return str(roles.get(name) or "unknown")
    return "host" if name == "MAIN" else "unresolved"


def is_source_allowed_for_host_merge(source_project: str | None, target_project: str | None = "MAIN") -> tuple[bool, str]:
    source = str(source_project or "").strip()
    target = str(target_project or "MAIN").strip() or "MAIN"
    if not source:
        return False, "unknown_source_project"

    source_role = get_project_role(source)
    target_role = get_project_role(target)
    if source_role in {"unknown", "unresolved"}:
        return False, "source_relationship_unresolved"
    if target_role in {"unknown", "unresolved"}:
        return False, "target_relationship_unresolved"
    policy = require_dead_code_policy("assembly_governance").get("source_role_policy", {}) or {}
    deny_companion = bool(policy.get("deny_companion_to_host_by_default", True))
    allow_companion = bool(policy.get("allow_companion_to_host_merge", False))
    allow_sources = {str(item).strip() for item in (policy.get("allow_companion_donor_projects", []) or []) if str(item).strip()}
    allow_targets = {str(item).strip() for item in (policy.get("allow_companion_target_hosts", []) or []) if str(item).strip()}

    if source_role == "companion" and target_role == "host":
        if source in allow_sources or target in allow_targets:
            return True, "companion_source_allowlisted"
        if deny_companion and not allow_companion:
            return False, "companion_source_disallowed"

    return True, "allowed"
