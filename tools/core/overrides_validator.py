"""Canonical overrides-match-workspace validator.

Checks whether the user overrides file is compatible with the
discovered workspace projects (variations).  Used by config_compiler,
governance_sync, and validate_lifecycle.
"""
from __future__ import annotations


def _normalize_path(value: str) -> str:
    return str(value).replace("\\", "/").strip()


def overrides_match_workspace(discovery_variations: dict, overrides: dict, discovery: dict | None = None) -> bool:
    """Return True when *overrides* are compatible with the discovered workspace."""
    if not discovery_variations or not isinstance(overrides, dict):
        return False

    allowed_keys = {
        str(key).strip()
        for key in discovery_variations.keys()
        if isinstance(key, str) and str(key).strip()
    }
    allowed_paths = {
        str(value).replace("\\", "/").strip()
        for value in discovery_variations.values()
        if isinstance(value, str)
    }
    if not allowed_keys or not allowed_paths:
        return False

    if discovery:
        if discovery.get("workspace_root") and overrides.get("workspace_root") != discovery.get("workspace_root"):
            return False
        if sorted(overrides.get("source_extensions", []) or []) != sorted(discovery.get("source_extensions", []) or []):
            return False
        if sorted(overrides.get("skip_dirs", []) or []) != sorted(discovery.get("skip_dirs", []) or []):
            return False

    override_variations = overrides.get("variations", {}) or {}
    override_keys = {
        str(key).strip()
        for key in override_variations.keys()
        if isinstance(key, str) and str(key).strip()
    }
    if override_keys and not override_keys.issubset(allowed_keys):
        return False
    for value in override_variations.values():
        if not isinstance(value, str) or value.replace("\\", "/").strip() not in allowed_paths:
            return False

    project_roles = overrides.get("project_roles", {}) or {}
    project_role_keys = {
        str(key).strip()
        for key in project_roles.keys()
        if isinstance(key, str) and str(key).strip()
    }
    if project_role_keys and not project_role_keys.issubset(allowed_keys):
        return False
    if discovery and project_roles != (discovery.get("project_roles", {}) or {}):
        return False

    for payload in (overrides.get("variation_aliases", {}) or {}).values():
        if not isinstance(payload, dict):
            continue
        discovery_key = payload.get("discovery_key")
        if isinstance(discovery_key, str) and discovery_key.strip() not in allowed_keys:
            return False
        path = payload.get("path")
        if isinstance(path, str) and path.replace("\\", "/").strip() not in allowed_paths:
            return False
    return True


def overrides_match_discovery(discovery: dict, overrides: dict) -> bool:
    if not isinstance(discovery, dict):
        return False
    return overrides_match_workspace(discovery.get("variations", {}) or {}, overrides, discovery=discovery)
