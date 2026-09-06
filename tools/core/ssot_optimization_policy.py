from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.core.config import CONFIG_DIR
from tools.core.json_io import load_json_object_strict


SSOT_OPTIMIZATION_POLICY_PATH = CONFIG_DIR / "ssot_optimization_policy.json"


def load_ssot_optimization_policy(path: Path | None = None) -> dict[str, Any]:
    return load_json_object_strict(path or SSOT_OPTIMIZATION_POLICY_PATH, label="SSOT optimization policy")


def ssot_profile_fields(path: Path | None = None) -> list[str]:
    rows = load_ssot_optimization_policy(path).get("profile_fields", [])
    return [str(row) for row in rows if str(row).strip()] if isinstance(rows, list) else []


def ssot_priority_hints(path: Path | None = None) -> dict[str, dict[str, str]]:
    rows = load_ssot_optimization_policy(path).get("priority_hints", {})
    if not isinstance(rows, dict):
        return {}
    result: dict[str, dict[str, str]] = {}
    for key, value in rows.items():
        if isinstance(value, dict):
            result[str(key)] = {str(inner_key): str(inner_value) for inner_key, inner_value in value.items()}
    return result


def ssot_default_priorities(path: Path | None = None) -> dict[str, str]:
    rows = load_ssot_optimization_policy(path).get("default_priorities", {})
    return {str(key): str(value) for key, value in rows.items()} if isinstance(rows, dict) else {}


def ssot_default_candidate_hint(path: Path | None = None) -> dict[str, str]:
    rows = load_ssot_optimization_policy(path).get("default_candidate_hint", {})
    return {str(key): str(value) for key, value in rows.items()} if isinstance(rows, dict) else {}


def ssot_large_artifact_min_bytes(path: Path | None = None) -> int:
    value = load_ssot_optimization_policy(path).get("large_artifact_min_bytes", 1_000_000)
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return 1_000_000


def ssot_runtime_reference_roots(path: Path | None = None) -> tuple[str, ...]:
    rows = load_ssot_optimization_policy(path).get("runtime_reference_roots", [])
    return tuple(str(row) for row in rows if str(row).strip()) if isinstance(rows, list) else ()


def ssot_v1_critical_artifacts(path: Path | None = None) -> set[str]:
    rows = load_ssot_optimization_policy(path).get("v1_critical_artifacts", [])
    return {str(row) for row in rows if str(row).strip()} if isinstance(rows, list) else set()


def ssot_profile_gated_heavy_artifacts(path: Path | None = None) -> set[str]:
    rows = load_ssot_optimization_policy(path).get("profile_gated_heavy_artifacts", [])
    return {str(row) for row in rows if str(row).strip()} if isinstance(rows, list) else set()


def ssot_dedicated_sqlite_first_validators(path: Path | None = None) -> dict[str, str]:
    rows = load_ssot_optimization_policy(path).get("dedicated_sqlite_first_validators", {})
    return {str(key): str(value) for key, value in rows.items()} if isinstance(rows, dict) else {}


def ssot_dedicated_validator_summaries(path: Path | None = None) -> dict[str, dict[str, str]]:
    rows = load_ssot_optimization_policy(path).get("dedicated_validator_summaries", {})
    if not isinstance(rows, dict):
        return {}
    result: dict[str, dict[str, str]] = {}
    for key, value in rows.items():
        if isinstance(value, dict):
            result[str(key)] = {str(inner_key): str(inner_value) for inner_key, inner_value in value.items()}
    return result
