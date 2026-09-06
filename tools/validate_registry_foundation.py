from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

CODE_MAPS_DIR = Path(__file__).resolve().parent.parent
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))

from tools.core.adapter_registry import load_adapter_registry, summarize_adapters
from tools.core.artifact_store import STORE
from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.framework_capabilities import framework_ecosystems, load_framework_capabilities
from tools.core.json_io import load_json_object_strict
from tools.core.language_registry import load_language_registry


def _check(name: str, passed: bool, details: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def _contract_list(contract: dict[str, Any], key: str) -> list[str]:
    raw = contract.get(key)
    return [str(item) for item in raw if str(item).strip()] if isinstance(raw, list) else []


def run_validation() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    language_registry = load_language_registry()
    language_validation_contract = (
        language_registry.get("validation_contract", {})
        if isinstance(language_registry.get("validation_contract"), dict)
        else {}
    )
    required_source_languages = _contract_list(language_validation_contract, "required_polyglot_source_languages")
    required_observation_only_languages = _contract_list(
        language_validation_contract,
        "required_observation_only_languages",
    )
    required_react_data_stacks = _contract_list(language_validation_contract, "required_react_data_stack_libraries")
    framework_caps = load_framework_capabilities()
    adapter_payload = load_adapter_registry()
    adapter_summary = summarize_adapters(adapter_payload)

    checks.append(
        _check(
            "language_registry_has_polyglot_sources",
            bool(required_source_languages)
            and all(lang in (language_registry.get("languages") or {}) for lang in required_source_languages),
            {"required": required_source_languages, "present": sorted((language_registry.get("languages") or {}).keys())},
        )
    )
    analyzed_languages = language_registry.get("languages") or {}
    observation_only_languages = language_registry.get("observation_only_languages") or {}
    analyzed_extensions = {
        str(extension).lower()
        for payload in analyzed_languages.values()
        if isinstance(payload, dict)
        for extension in payload.get("extensions", [])
    }
    observation_only_extensions = {
        str(extension).lower()
        for payload in observation_only_languages.values()
        if isinstance(payload, dict)
        for extension in payload.get("extensions", [])
    }
    checks.append(
        _check(
            "observation_only_languages_are_declared_and_authority_disjoint",
            bool(required_observation_only_languages)
            and all(language in observation_only_languages for language in required_observation_only_languages)
            and not (analyzed_extensions & observation_only_extensions),
            {
                "required": required_observation_only_languages,
                "present": sorted(observation_only_languages),
                "overlapping_extensions": sorted(analyzed_extensions & observation_only_extensions),
            },
        )
    )
    plugin_map = language_registry.get("plugin_library_map", {})
    checks.append(
        _check(
            "plugin_library_map_covers_react_data_stacks",
            bool(required_react_data_stacks)
            and all(key in plugin_map for key in required_react_data_stacks),
            {"required": required_react_data_stacks, "present": sorted(plugin_map.keys())},
        )
    )
    checks.append(
        _check(
            "framework_capabilities_declares_react_specialist_boundary",
            (framework_caps.get("ecosystems") or {}).get("react", {}).get("claim_level") == "deep_specialist",
            (framework_caps.get("ecosystems") or {}).get("react", {}),
        )
    )
    discovery_source = _read(CODE_MAPS_DIR / "tools" / "orchestrators" / "discovery.py")
    config_source = _read(CODE_MAPS_DIR / "tools" / "core" / "config.py")
    checks.append(
        _check(
            "discovery_uses_registry_not_local_doctrine_registry",
            "DOCTRINE_REGISTRY" not in discovery_source and "plugins_for_dependencies" in discovery_source,
            "discovery profile/rule/plugin ownership is delegated to JSON-backed registries",
        )
    )
    checks.append(
        _check(
            "external_target_plugin_inference_uses_registry",
            "dependency_plugin_map =" not in config_source and "plugins_for_dependencies" in config_source,
            "CODEMAPS_TARGET_ROOT inference uses language_registry plugin map",
        )
    )
    checks.append(
        _check(
            "artifact_store_boundary_exists",
            STORE.backend in ("json", "sqlite", "hybrid_sqlite") and callable(getattr(STORE, "load_raw", None)) and callable(getattr(STORE, "save_raw", None)),
            {"backend": STORE.backend, "atlas_path": str(STORE.raw_path("atlas"))},
        )
    )
    checks.append(
        _check(
            "adapter_registry_manifest_is_valid_and_framework_covered",
            adapter_summary.get("total", 0) >= 1
            and adapter_summary.get("valid") == adapter_summary.get("total")
            and all(
                all(row.get("framework_capability_coverage", {}).values())
                for row in adapter_summary.get("adapters", [])
                if row.get("enabled")
            ),
            adapter_summary,
        )
    )
    polyglot_capabilities = load_json_object_strict(CODE_MAPS_DIR / "config" / "polyglot_capabilities.json", label="Polyglot capabilities")
    checks.append(
        _check(
            "polyglot_claim_guardrail_exists",
            bool(polyglot_capabilities.get("release_guardrails", {}).get("forbid_compiler_grade_claims_without_adapters")),
            polyglot_capabilities.get("release_guardrails", {}),
        )
    )

    failed = [item for item in checks if not item["passed"]]
    payload = {
        "meta": {"kind": "registry_foundation_validation", "version": "v1"},
        "summary": {"total_checks": len(checks), "passed_checks": len(checks) - len(failed), "failed_checks": len(failed), "status": "PASS" if not failed else "FAIL"},
        "checks": checks,
    }
    save_json_atomic(RAW_DIR / "registry_foundation_validation.json", payload)
    lines = [
        "# Registry Foundation Validation",
        "",
        f"- Status: {payload['summary']['status']}",
        f"- Checks: {payload['summary']['passed_checks']}/{payload['summary']['total_checks']}",
        "",
    ]
    for item in checks:
        lines.append(f"- {'PASS' if item['passed'] else 'FAIL'} `{item['name']}`")
    save_text_atomic(REPORTS_DIR / "registry_foundation_validation.md", "\n".join(lines) + "\n")
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return payload


if __name__ == "__main__":
    result = run_validation()
    raise SystemExit(0 if result["summary"]["failed_checks"] == 0 else 1)

