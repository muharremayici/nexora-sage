from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.adapter_registry import load_adapter_registry
from tools.core.artifact_validator import ARTIFACT_PATHS
from tools.core.capability_registry import capability_ids, load_capability_registry, summarize_capabilities
from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.roadmap_phase_registry import (
    allowed_roadmap_releases,
    current_product_release,
    load_roadmap_phase_registry,
    phases_by_capability_domain,
    production_release_history,
)


RAW_OUTPUT_PATH = RAW_DIR / "capability_registry_validation.json"
REPORT_OUTPUT_PATH = REPORTS_DIR / "capability_registry_validation.md"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check(name: str, passed: bool, details: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _validator_exists(path_text: str) -> bool:
    return (ROOT / path_text).exists()


def _validation_contract(registry: dict[str, Any]) -> dict[str, Any]:
    meta = registry.get("_meta", {}) if isinstance(registry, dict) else {}
    contract = meta.get("validation_contract", {}) if isinstance(meta, dict) else {}
    return contract if isinstance(contract, dict) else {}


def _roadmap_decision_contract(registry: dict[str, Any]) -> dict[str, set[str]]:
    contract = _validation_contract(registry).get("roadmap_decision_matrix", {})
    contract = contract if isinstance(contract, dict) else {}
    return {
        "required_fields": {str(item) for item in contract.get("required_fields", []) if str(item).strip()},
        "allowed_priorities": {str(item) for item in contract.get("allowed_priorities", []) if str(item).strip()},
        "allowed_efforts": {str(item) for item in contract.get("allowed_efforts", []) if str(item).strip()},
        "allowed_values": {str(item) for item in contract.get("allowed_values", []) if str(item).strip()},
    }


def _roadmap_activation_contract(registry: dict[str, Any]) -> dict[str, Any]:
    contract = _validation_contract(registry).get("roadmap_activation_contract", {})
    return contract if isinstance(contract, dict) else {}


def _capability_specific_contract(registry: dict[str, Any], capability_id: str) -> dict[str, Any]:
    contracts = _validation_contract(registry).get("capability_specific_contracts", {})
    contracts = contracts if isinstance(contracts, dict) else {}
    contract = contracts.get(capability_id, {})
    return contract if isinstance(contract, dict) else {}


def _capability_validation_scope(registry: dict[str, Any]) -> dict[str, Any]:
    scope = _validation_contract(registry).get("capability_validation_scope", {})
    return scope if isinstance(scope, dict) else {}


def _string_set(raw: Any) -> set[str]:
    return {str(item) for item in raw if str(item).strip()} if isinstance(raw, list) else set()


def build_validation() -> dict[str, Any]:
    registry = load_capability_registry()
    capability_validation_scope = _capability_validation_scope(registry)
    explicit_runtime_report_artifacts = _string_set(
        capability_validation_scope.get("explicit_runtime_report_artifacts")
    )
    required_language_scopes = _string_set(capability_validation_scope.get("required_language_scopes"))
    required_domain_prefixes = tuple(sorted(_string_set(capability_validation_scope.get("required_domain_prefixes"))))
    polyglot_anchor_capability_id = str(capability_validation_scope.get("polyglot_anchor_capability_id") or "")
    roadmap_decision_contract = _roadmap_decision_contract(registry)
    roadmap_activation_contract = _roadmap_activation_contract(registry)
    required_roadmap_fields = roadmap_decision_contract["required_fields"]
    allowed_roadmap_priorities = roadmap_decision_contract["allowed_priorities"]
    allowed_roadmap_efforts = roadmap_decision_contract["allowed_efforts"]
    allowed_roadmap_values = roadmap_decision_contract["allowed_values"]
    required_activation_conditions = _string_set(roadmap_activation_contract.get("required_conditions"))
    release_claim_rule = str(roadmap_activation_contract.get("release_claim_rule") or "").strip()
    phase_registry = load_roadmap_phase_registry()
    active_release = current_product_release(phase_registry)
    allowed_releases = allowed_roadmap_releases(phase_registry)
    released_or_active_releases = production_release_history(phase_registry)
    polyglot_releases = phases_by_capability_domain(phase_registry, "polyglot")
    summary = summarize_capabilities(registry)
    capabilities = summary.get("capabilities", [])
    ids_with_aliases = capability_ids(registry)
    adapter_registry = load_adapter_registry()
    adapters = adapter_registry.get("adapters", []) if isinstance(adapter_registry, dict) else []
    adapter_capabilities = sorted(
        {
            str(capability)
            for adapter in adapters
            if isinstance(adapter, dict)
            for capability in (adapter.get("capabilities", []) or [])
        }
    )
    missing_adapter_capabilities = [item for item in adapter_capabilities if item not in ids_with_aliases]
    missing_artifacts = sorted(
        {
            artifact
            for capability in capabilities
            for artifact in capability.get("artifacts", [])
            if artifact not in ARTIFACT_PATHS and artifact not in explicit_runtime_report_artifacts
        }
    )
    missing_validators = sorted(
        {
            validator
            for capability in capabilities
            for validator in capability.get("validators", [])
            if not _validator_exists(validator)
        }
    )
    roadmap_claim_violations = sorted(
        str(capability.get("id") or "<missing-id>")
        for capability in capabilities
        if capability.get("maturity") == "roadmap"
        and "not active" not in str(capability.get("claim_boundary") or "").lower()
    )
    roadmap_phase_violations = sorted(
        {
            str(capability.get("id") or "<missing-id>")
            for capability in capabilities
            if capability.get("maturity") == "roadmap"
            and str(capability.get("target_release") or "") not in allowed_releases
        }
    )
    roadmap_decision_field_violations = sorted(
        {
            f"{str(capability.get('id') or '<missing-id>')}:{field}"
            for capability in capabilities
            if capability.get("maturity") == "roadmap"
            for field in required_roadmap_fields
            if not str(capability.get(field) or "").strip()
        }
    )
    roadmap_priority_violations = sorted(
        {
            str(capability.get("id") or "<missing-id>")
            for capability in capabilities
            if capability.get("maturity") == "roadmap"
            and str(capability.get("priority") or "") not in allowed_roadmap_priorities
        }
    )
    roadmap_effort_violations = sorted(
        {
            str(capability.get("id") or "<missing-id>")
            for capability in capabilities
            if capability.get("maturity") == "roadmap"
            and str(capability.get("effort") or "") not in allowed_roadmap_efforts
        }
    )
    roadmap_value_violations = sorted(
        {
            str(capability.get("id") or "<missing-id>")
            for capability in capabilities
            if capability.get("maturity") == "roadmap"
            and str(capability.get("value") or "") not in allowed_roadmap_values
        }
    )
    production_without_contract_surfaces = sorted(
        str(capability.get("id") or "<missing-id>")
        for capability in capabilities
        if capability.get("maturity") == "production_candidate"
        and any(not capability.get(field) for field in ("engines", "artifacts", "validators"))
    )
    language_scopes = set((summary.get("language_scopes") or {}).keys())
    v2_roadmap = [
        capability
        for capability in capabilities
        if capability.get("maturity") == "roadmap"
        and str(capability.get("target_release") or "") in polyglot_releases
    ]
    production_release_violations = sorted(
        str(capability.get("id") or "<missing-id>")
        for capability in capabilities
        if capability.get("maturity") == "production_candidate"
        and capability.get("introduced_in") not in released_or_active_releases
    )
    v2_non_polyglot = sorted(
        str(capability.get("id") or "<missing-id>")
        for capability in v2_roadmap
        if "polyglot" not in str(capability.get("domain") or "").lower()
        and "polyglot" not in str(capability.get("id") or "").lower()
        and "polyglot" not in str(capability.get("roadmap_scope") or "").lower()
    )
    polyglot_release_text = ", ".join(sorted(polyglot_releases)) or "<missing-polyglot-release>"
    capability_by_id = {
        str(capability.get("id") or ""): capability
        for capability in capabilities
    }
    sqlite_storage = capability_by_id.get("sqlite_artifact_store", {})
    sqlite_storage_contract = _capability_specific_contract(registry, "sqlite_artifact_store")
    sqlite_storage_artifacts = {
        str(item)
        for item in sqlite_storage.get("artifacts", [])
        if str(item).strip()
    } if isinstance(sqlite_storage, dict) else set()
    sqlite_storage_validators = {
        str(item)
        for item in sqlite_storage.get("validators", [])
        if str(item).strip()
    } if isinstance(sqlite_storage, dict) else set()
    required_sqlite_storage_artifacts = {
        str(item)
        for item in sqlite_storage_contract.get("required_artifacts", [])
        if str(item).strip()
    }
    required_sqlite_storage_validators = {
        str(item)
        for item in sqlite_storage_contract.get("required_validators", [])
        if str(item).strip()
    }
    missing_sqlite_storage_artifacts = sorted(required_sqlite_storage_artifacts - sqlite_storage_artifacts)
    missing_sqlite_storage_validators = sorted(required_sqlite_storage_validators - sqlite_storage_validators)

    checks = [
        _check(
            "capability_registry_exists_and_has_capabilities",
            registry.get("_meta", {}).get("kind") == "nexora.capability_registry"
            and summary.get("total", 0) >= 10,
            {"total": summary.get("total"), "kind": registry.get("_meta", {}).get("kind")},
        ),
        _check(
            "all_capabilities_have_required_contract_fields",
            summary.get("valid") == summary.get("total") and not summary.get("duplicates"),
            {"valid": summary.get("valid"), "total": summary.get("total"), "duplicates": summary.get("duplicates")},
        ),
        _check(
            "adapter_capabilities_resolve_to_registry_ids_or_aliases",
            not missing_adapter_capabilities,
            {"missing_adapter_capabilities": missing_adapter_capabilities},
        ),
        _check(
            "capability_artifacts_are_registered_or_explicit_runtime_reports",
            not missing_artifacts,
            {"missing_artifacts": missing_artifacts},
        ),
        _check(
            "capability_validators_exist",
            not missing_validators,
            {"missing_validators": missing_validators},
        ),
        _check(
            "production_capabilities_have_engines_artifacts_and_validators",
            not production_without_contract_surfaces,
            {"production_without_contract_surfaces": production_without_contract_surfaces},
        ),
        _check(
            "roadmap_capabilities_are_not_active_claims",
            not roadmap_claim_violations,
            {"roadmap_claim_violations": roadmap_claim_violations},
        ),
        _check(
            "roadmap_capabilities_have_valid_target_release",
            not roadmap_phase_violations,
            {
                "allowed_releases": sorted(allowed_releases),
                "roadmap_phase_violations": roadmap_phase_violations,
            },
        ),
        _check(
            "production_capabilities_have_released_introduction",
            not production_release_violations,
            {
                "current_product_release": active_release,
                "released_or_active_releases": sorted(released_or_active_releases),
                "violations": production_release_violations,
            },
        ),
        _check(
            "roadmap_capabilities_have_decision_matrix_fields",
            bool(required_roadmap_fields)
            and bool(allowed_roadmap_priorities)
            and bool(allowed_roadmap_efforts)
            and bool(allowed_roadmap_values)
            and not roadmap_decision_field_violations
            and not roadmap_priority_violations
            and not roadmap_effort_violations
            and not roadmap_value_violations,
            {
                "source": "config/capability_registry.json:_meta.validation_contract.roadmap_decision_matrix",
                "required_fields": sorted(required_roadmap_fields),
                "allowed_priorities": sorted(allowed_roadmap_priorities),
                "allowed_efforts": sorted(allowed_roadmap_efforts),
                "allowed_values": sorted(allowed_roadmap_values),
                "missing_or_empty_fields": roadmap_decision_field_violations,
                "priority_violations": roadmap_priority_violations,
                "effort_violations": roadmap_effort_violations,
                "value_violations": roadmap_value_violations,
            },
        ),
        _check(
            "roadmap_activation_contract_is_registry_backed",
            {
                "engines",
                "artifacts",
                "validators",
                "release_proof",
                "claim_guard_update",
            }
            <= required_activation_conditions
            and bool(release_claim_rule),
            {
                "source": "config/capability_registry.json:_meta.validation_contract.roadmap_activation_contract",
                "required_conditions": sorted(required_activation_conditions),
                "release_claim_rule_present": bool(release_claim_rule),
            },
        ),
        _check(
            "capability_registry_preserves_polyglot_future_boundary",
            bool(required_language_scopes) and required_language_scopes.issubset(language_scopes),
            {
                "required_language_scopes": sorted(required_language_scopes),
                "language_scopes": sorted(language_scopes),
            },
        ),
        _check(
            "capability_registry_separates_language_and_capability_domains",
            bool(required_domain_prefixes)
            and all(
                any(str(cap.get("domain", "")).startswith(prefix) for cap in capabilities)
                for prefix in required_domain_prefixes
            ),
            {
                "required_domain_prefixes": list(required_domain_prefixes),
                "domains": summary.get("domains"),
            },
        ),
        _check(
            "v2_roadmap_preserves_polyglot_first_identity",
            bool(polyglot_anchor_capability_id)
            and any(str(capability.get("id")) == polyglot_anchor_capability_id for capability in v2_roadmap)
            and not v2_non_polyglot,
            {
                "polyglot_anchor_capability_id": polyglot_anchor_capability_id,
                "v2_roadmap_ids": [str(capability.get("id")) for capability in v2_roadmap],
                "non_polyglot_v2_items": v2_non_polyglot,
                "policy": (
                    "1.x deepens React dominance; "
                    f"{polyglot_release_text} is reserved for polyglot semantic expansion; "
                    "later runtime, distributed and memory phases must be declared by roadmap_phase_registry."
                ),
            },
        ),
        _check(
            "sqlite_artifact_store_capability_matches_release_deep_ssot_validators",
            bool(required_sqlite_storage_artifacts)
            and bool(required_sqlite_storage_validators)
            and not missing_sqlite_storage_artifacts
            and not missing_sqlite_storage_validators,
            {
                "source": "config/capability_registry.json:_meta.validation_contract.capability_specific_contracts.sqlite_artifact_store",
                "missing_artifacts": missing_sqlite_storage_artifacts,
                "missing_validators": missing_sqlite_storage_validators,
                "policy": "Capability registry entry is the canonical SQLite-first SSOT validator advertisement; this check verifies it is self-consistent and executable.",
            },
        ),
    ]

    payload = {
        "meta": {"kind": "capability_registry_validation", "version": "v1", "generator": "tools.validate_capability_registry"},
        "summary": {
            "status": "PASS" if all(check["passed"] for check in checks) else "FAIL",
            "total_checks": len(checks),
            "passed_checks": sum(1 for check in checks if check["passed"]),
            "failed_checks": sum(1 for check in checks if not check["passed"]),
            "capabilities": summary.get("total"),
            "generated_at": _utc_now(),
        },
        "checks": checks,
    }
    return payload


def render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# Capability Registry Validation",
        "",
        f"- status: `{summary.get('status')}`",
        f"- capabilities: `{summary.get('capabilities')}`",
        f"- passed: `{summary.get('passed_checks')}/{summary.get('total_checks')}`",
        "",
        "| Check | Result | Details |",
        "|---|---|---|",
    ]
    for check in payload.get("checks", []):
        details = check.get("details")
        if not isinstance(details, str):
            details = json.dumps(details, ensure_ascii=False)
        escaped_details = details.replace("|", "\\|")
        lines.append(f"| `{check.get('name')}` | {'PASS' if check.get('passed') else 'FAIL'} | {escaped_details} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    payload = build_validation()
    save_json_atomic(RAW_OUTPUT_PATH, payload)
    save_text_atomic(REPORT_OUTPUT_PATH, render_report(payload))
    print(json.dumps(payload.get("summary", {}), ensure_ascii=False))
    return 0 if payload.get("summary", {}).get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
