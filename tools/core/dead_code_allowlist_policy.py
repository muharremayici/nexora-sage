from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from tools.core.json_io import load_json_file


ALLOWLIST_POLICY_FILENAME = "dead_code_policy.json"
_DEFAULT_SCOPE = "global_only"
_VALID_SCOPES = {"global_only", "project_only", "all"}


def _normalize_scope(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"global", "global_only", "workspace"}:
        return "global_only"
    if raw in {"project", "project_only"}:
        return "project_only"
    if raw in {"all", "global_and_project", "project_and_global"}:
        return "all"
    return _DEFAULT_SCOPE


def get_allowlist_scope(config_dir: Path) -> str:
    policy_path = config_dir / "golden" / ALLOWLIST_POLICY_FILENAME
    policy = load_json_file(policy_path, {})
    if isinstance(policy, dict):
        if "allowlist_scope" in policy:
            return _normalize_scope(policy.get("allowlist_scope"))
        if "include_project_allowlists" in policy:
            include_projects = bool(policy.get("include_project_allowlists"))
            return "all" if include_projects else "global_only"

    env_scope = os.environ.get("CODEMAPS_DEAD_CODE_ALLOWLIST_SCOPE")
    if env_scope:
        return _normalize_scope(env_scope)
    return _DEFAULT_SCOPE


def scope_allows_global(scope: str) -> bool:
    return scope in {"global_only", "all"}


def scope_allows_project(scope: str) -> bool:
    return scope in {"project_only", "all"}


def is_valid_scope(scope: str) -> bool:
    return scope in _VALID_SCOPES
