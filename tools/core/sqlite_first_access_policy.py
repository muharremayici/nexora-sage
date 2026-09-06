from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.core.config import CONFIG_DIR
from tools.core.json_io import load_json_file


CONTRACT_PATH = CONFIG_DIR / "sqlite_first_access_policy.json"


def load_sqlite_first_access_policy(path: Path = CONTRACT_PATH) -> dict[str, Any]:
    payload = load_json_file(path, {})
    return payload if isinstance(payload, dict) else {}


def sqlite_first_artifact_policy(artifact_id: str, path: Path = CONTRACT_PATH) -> dict[str, Any]:
    managed = load_sqlite_first_access_policy(path).get("managed_artifacts", {})
    if not isinstance(managed, dict):
        return {}
    payload = managed.get(artifact_id, {})
    return payload if isinstance(payload, dict) else {}


def sqlite_first_artifact_file(artifact_id: str) -> str:
    return str(sqlite_first_artifact_policy(artifact_id).get("artifact_file") or "")


def sqlite_first_hot_consumers(artifact_id: str) -> set[str]:
    values = sqlite_first_artifact_policy(artifact_id).get("hot_consumers", [])
    return {str(item) for item in values or []}


def sqlite_first_allowed_references(artifact_id: str) -> dict[str, str]:
    values = sqlite_first_artifact_policy(artifact_id).get("allowed_direct_path_references", {})
    if not isinstance(values, dict):
        return {}
    return {str(key): str(value) for key, value in values.items()}


def sqlite_first_central_adapter(artifact_id: str) -> str:
    return str(sqlite_first_artifact_policy(artifact_id).get("central_adapter") or "")


def sqlite_first_central_apis(artifact_id: str) -> set[str]:
    values = sqlite_first_artifact_policy(artifact_id).get("central_apis", [])
    return {str(item) for item in values or [] if str(item).strip()}


def sqlite_first_source_marker(artifact_id: str) -> str:
    return str(sqlite_first_artifact_policy(artifact_id).get("sqlite_first_source_marker") or "")


def module_name_to_repo_path(module: str) -> str:
    if not module:
        return ""
    return module.replace(".", "/") + ".py"


def pipeline_declared_artifact_modules(artifact_id: str) -> dict[str, str]:
    from tools.core.pipeline_registry import ARTIFACT_OWNERSHIP, STEP_MODULE_INVOCATIONS

    expected: dict[str, str] = {}
    for step_name, ownership in ARTIFACT_OWNERSHIP.items():
        reads = {str(item) for item in ownership.get("reads", []) or []}
        writes = {str(item) for item in ownership.get("writes", []) or []}
        if artifact_id not in reads and artifact_id not in writes:
            continue
        module_file = module_name_to_repo_path(STEP_MODULE_INVOCATIONS.get(step_name, ""))
        if module_file:
            expected[module_file] = str(step_name)
    return expected


def sqlite_first_large_artifact_policies(path: Path = CONTRACT_PATH) -> dict[str, dict[str, Any]]:
    values = load_sqlite_first_access_policy(path).get("large_managed_artifacts", {})
    if not isinstance(values, dict):
        return {}
    return {str(key): value for key, value in values.items() if isinstance(value, dict)}


def sqlite_first_large_allowed_references(path: Path = CONTRACT_PATH) -> dict[str, str]:
    values = load_sqlite_first_access_policy(path).get("large_allowed_reference_files", {})
    if not isinstance(values, dict):
        return {}
    return {str(key): str(value) for key, value in values.items()}
