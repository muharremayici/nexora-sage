"""Strict, reloadable access to validator-owned policy decisions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.core.config import CODE_MAPS_DIR, CONFIG_DIR
from tools.core.json_io import load_json_object_strict_cached


REGISTRY_PATH = CONFIG_DIR / "validator_policy_registry.json"
_REQUIRED_INVENTORY_LISTS = {
    "decision_words",
    "path_or_artifact_suffixes",
    "policy_assignment_markers",
    "decision_table_row_field_markers",
    "allowed_decision_table_files",
    "allowed_test_or_fixture_parts",
}
_REQUIRED_SQLITE_LISTS = {
    "hot_integration_targets",
    "active_runtime_roots",
    "active_runtime_exists_gate_roots",
    "raw_disk_read_allowed_files",
    "raw_exists_gate_allowed_files",
}


def _relative_posix_values(values: Any, *, field: str) -> list[str]:
    if not isinstance(values, list) or not values:
        raise ValueError(f"validator policy registry {field} must be a non-empty list")
    normalized = [str(value or "").strip() for value in values]
    if any(not value or "\\" in value or value.startswith("/") or ".." in Path(value).parts for value in normalized):
        raise ValueError(f"validator policy registry {field} must contain relative POSIX paths")
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"validator policy registry {field} contains duplicates")
    return normalized


def _string_values(values: Any, *, field: str) -> list[str]:
    if not isinstance(values, list) or not values:
        raise ValueError(f"validator policy registry {field} must be a non-empty list")
    normalized = [str(value or "").strip() for value in values]
    if any(not value for value in normalized) or len(normalized) != len(set(normalized)):
        raise ValueError(f"validator policy registry {field} must contain unique non-empty strings")
    return normalized


def _normalize_validator_policy_registry(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("meta", {}).get("kind") != "validator_policy_registry":
        raise ValueError("validator policy registry has unexpected meta.kind")
    inventory = payload.get("hardcoded_decision_inventory")
    sqlite_proxy = payload.get("sqlite_proxy_coverage")
    if not isinstance(inventory, dict) or not isinstance(sqlite_proxy, dict):
        raise ValueError("validator policy registry requires hardcoded inventory and SQLite proxy sections")
    if not isinstance(inventory.get("scan_root"), str) or not inventory["scan_root"].strip():
        raise ValueError("validator policy registry hardcoded inventory scan_root is required")
    _relative_posix_values([inventory["scan_root"]], field="hardcoded_decision_inventory.scan_root")
    for field in _REQUIRED_INVENTORY_LISTS:
        _string_values(inventory.get(field), field=f"hardcoded_decision_inventory.{field}")
    for field in ("decision_table_min_rows", "decision_table_min_matching_fields"):
        value = inventory.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"validator policy registry hardcoded_decision_inventory.{field} must be a positive integer")
    if inventory["decision_table_min_rows"] < 2:
        raise ValueError("validator policy registry hardcoded_decision_inventory.decision_table_min_rows must be at least 2")
    if inventory["decision_table_min_matching_fields"] > len(inventory["decision_table_row_field_markers"]):
        raise ValueError("validator policy registry hardcoded decision table field threshold exceeds marker count")
    triage = inventory.get("triage_actions")
    if not isinstance(triage, dict):
        raise ValueError("validator policy registry hardcoded inventory triage_actions is required")
    actionable = _string_values(triage.get("actionable"), field="hardcoded_decision_inventory.triage_actions.actionable")
    deferred = _string_values(triage.get("bulk_deferred"), field="hardcoded_decision_inventory.triage_actions.bulk_deferred")
    if set(actionable) & set(deferred):
        raise ValueError("validator policy registry triage action groups overlap")
    for field in _REQUIRED_SQLITE_LISTS:
        paths = _relative_posix_values(sqlite_proxy.get(field), field=f"sqlite_proxy_coverage.{field}")
        for path in paths:
            if not (CODE_MAPS_DIR / path).exists():
                raise FileNotFoundError(f"validator policy registry path does not exist: {path}")
    return payload


def load_validator_policy_registry() -> dict[str, Any]:
    return load_json_object_strict_cached(
        REGISTRY_PATH,
        label="validator policy registry",
        normalizer=_normalize_validator_policy_registry,
    )


def hardcoded_decision_inventory_policy() -> dict[str, Any]:
    return dict(load_validator_policy_registry()["hardcoded_decision_inventory"])


def sqlite_proxy_coverage_policy() -> dict[str, Any]:
    return dict(load_validator_policy_registry()["sqlite_proxy_coverage"])
