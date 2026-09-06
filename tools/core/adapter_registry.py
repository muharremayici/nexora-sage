from __future__ import annotations

from typing import Any

from tools.core.config import CONFIG_DIR
from tools.core.framework_capabilities import framework_ecosystems
from tools.core.json_io import load_json_object_strict_cached


ADAPTERS_FILE = CONFIG_DIR / "codemaps.adapters.json"
ADAPTERS_SCHEMA_FILE = CONFIG_DIR / "schemas" / "codemaps.adapters.schema.json"


def _load_adapter_registry_payload() -> dict[str, Any]:
    return load_json_object_strict_cached(ADAPTERS_FILE, label="Adapter registry")


def required_adapter_fields() -> tuple[str, ...]:
    schema = load_json_object_strict_cached(ADAPTERS_SCHEMA_FILE, label="Adapter registry schema")
    adapters_schema = schema.get("properties", {}).get("adapters", {})
    adapter_schema = adapters_schema.get("items", {}) if isinstance(adapters_schema, dict) else {}
    required = adapter_schema.get("required", []) if isinstance(adapter_schema, dict) else []
    if not isinstance(required, list) or not all(isinstance(item, str) and item.strip() for item in required):
        raise ValueError(f"Adapter registry schema must declare adapter item required fields: {ADAPTERS_SCHEMA_FILE}")
    return tuple(required)


def load_adapter_registry() -> dict[str, Any]:
    return dict(_load_adapter_registry_payload())


def validate_adapter_manifest(adapter: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    missing = sorted(field for field in required_adapter_fields() if field not in adapter)
    if missing:
        errors.append(f"missing_fields:{','.join(missing)}")
    for field in ("frameworks", "capabilities", "engines"):
        value = adapter.get(field)
        if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
            errors.append(f"invalid_list:{field}")
    if not str(adapter.get("id") or "").strip():
        errors.append("empty_id")
    if adapter.get("enabled") is not True and adapter.get("enabled") is not False:
        errors.append("invalid_enabled")
    return errors


def summarize_adapters(payload: dict[str, Any]) -> dict[str, Any]:
    adapters = payload.get("adapters", []) if isinstance(payload, dict) else []
    framework_capabilities = framework_ecosystems()
    valid = 0
    enabled = 0
    ecosystems: dict[str, int] = {}
    capability_counts: dict[str, int] = {}
    rows = []
    for adapter in adapters if isinstance(adapters, list) else []:
        if not isinstance(adapter, dict):
            continue
        errors = validate_adapter_manifest(adapter)
        if not errors:
            valid += 1
        if adapter.get("enabled") is True:
            enabled += 1
        ecosystem = str(adapter.get("ecosystem") or "unknown")
        ecosystems[ecosystem] = ecosystems.get(ecosystem, 0) + 1
        for capability in adapter.get("capabilities", []) if isinstance(adapter.get("capabilities"), list) else []:
            capability_counts[str(capability)] = capability_counts.get(str(capability), 0) + 1
        rows.append(
            {
                "id": adapter.get("id"),
                "enabled": adapter.get("enabled"),
                "ecosystem": ecosystem,
                "frameworks": adapter.get("frameworks", []),
                "capabilities": adapter.get("capabilities", []),
                "engines": adapter.get("engines", []),
                "maturity": adapter.get("maturity"),
                "valid": not errors,
                "errors": errors,
                "framework_capability_coverage": {
                    framework: framework in framework_capabilities for framework in adapter.get("frameworks", [])
                }
                if isinstance(adapter.get("frameworks"), list)
                else {},
            }
        )
    return {
        "total": len(rows),
        "enabled": enabled,
        "valid": valid,
        "ecosystems": ecosystems,
        "capabilities": capability_counts,
        "framework_capability_ecosystems": sorted(framework_capabilities),
        "adapters": rows,
    }
