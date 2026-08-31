from __future__ import annotations

from collections import Counter
from typing import Any

from tools.core.config import CONFIG_DIR
from tools.core.json_io import load_json_object_strict
from tools.core.reality_scope import capability_scope


CAPABILITY_REGISTRY_FILE = CONFIG_DIR / "capability_registry.json"
REQUIRED_CAPABILITY_FIELDS = {
    "id",
    "title",
    "domain",
    "language_scope",
    "maturity",
    "engines",
    "artifacts",
    "validators",
    "claim_boundary",
}


def load_capability_registry() -> dict[str, Any]:
    return load_json_object_strict(CAPABILITY_REGISTRY_FILE, label="Capability registry")


def capability_ids(payload: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for capability in payload.get("capabilities", []) if isinstance(payload, dict) else []:
        if isinstance(capability, dict) and capability.get("id"):
            ids.add(str(capability["id"]))
            for alias in capability.get("aliases", []) or []:
                ids.add(str(alias))
    return ids


def iter_capabilities(payload: dict[str, Any]) -> list[dict[str, Any]]:
    capabilities = payload.get("capabilities", []) if isinstance(payload, dict) else []
    return [item for item in capabilities if isinstance(item, dict)]


def get_capability(payload: dict[str, Any], capability_id: str) -> dict[str, Any] | None:
    target = str(capability_id or "").strip()
    if not target:
        return None
    for capability in iter_capabilities(payload):
        identifiers = {str(capability.get("id") or "")}
        identifiers.update(str(alias) for alias in capability.get("aliases", []) or [])
        if target in identifiers:
            return capability
    return None


def capabilities_for_artifact(payload: dict[str, Any], artifact: str) -> list[dict[str, Any]]:
    target = str(artifact or "").strip()
    if not target:
        return []
    matches = []
    for capability in iter_capabilities(payload):
        artifacts = {str(item) for item in capability.get("artifacts", []) or []}
        if target in artifacts:
            matches.append(capability)
    return matches


def compact_capability_contract(capability: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": capability.get("id"),
        "title": capability.get("title"),
        "domain": capability.get("domain"),
        "language_scope": capability.get("language_scope", []),
        "framework_scope": capability.get("framework_scope", []),
        "maturity": capability.get("maturity"),
        "introduced_in": capability.get("introduced_in"),
        "target_release": capability.get("target_release"),
        "priority": capability.get("priority"),
        "effort": capability.get("effort"),
        "value": capability.get("value"),
        "current_state": capability.get("current_state"),
        "roadmap_scope": capability.get("roadmap_scope"),
        "artifacts_to_trust": capability.get("artifacts", []),
        "validators_to_run": capability.get("validators", []),
        "claim_boundary": capability.get("claim_boundary"),
    }


def relevant_capability_contracts(
    payload: dict[str, Any],
    *,
    capability_ids: list[str] | None = None,
    artifacts: list[str] | None = None,
    limit: int = 8,
    allowed_system_scopes: set[str] | None = None,
) -> list[dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    for capability_id in capability_ids or []:
        capability = get_capability(payload, capability_id)
        if capability and capability.get("id"):
            selected[str(capability["id"])] = capability
    for artifact in artifacts or []:
        for capability in capabilities_for_artifact(payload, artifact):
            if capability.get("id"):
                selected[str(capability["id"])] = capability
    ordered = [
        compact_capability_contract(capability)
        for _, capability in sorted(selected.items())[: max(1, int(limit or 8))]
    ]
    if allowed_system_scopes is None:
        return ordered
    return [
        capability
        for capability in ordered
        if capability_scope(str(capability.get("id") or "")) in allowed_system_scopes
    ]


def validate_capability(capability: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    missing = sorted(field for field in REQUIRED_CAPABILITY_FIELDS if field not in capability)
    if missing:
        errors.append(f"missing_fields:{','.join(missing)}")
    if not str(capability.get("id") or "").strip():
        errors.append("empty_id")
    for field in ("language_scope", "engines", "artifacts", "validators"):
        value = capability.get(field)
        if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
            errors.append(f"invalid_list:{field}")
    if "framework_scope" in capability:
        value = capability.get("framework_scope")
        if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
            errors.append("invalid_list:framework_scope")
    if "aliases" in capability:
        value = capability.get("aliases")
        if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
            errors.append("invalid_list:aliases")
    if not str(capability.get("claim_boundary") or "").strip():
        errors.append("empty_claim_boundary")
    return errors


def summarize_capabilities(payload: dict[str, Any]) -> dict[str, Any]:
    capabilities = iter_capabilities(payload)
    rows: list[dict[str, Any]] = []
    domains: Counter[str] = Counter()
    languages: Counter[str] = Counter()
    maturities: Counter[str] = Counter()
    engine_refs: Counter[str] = Counter()
    artifact_refs: Counter[str] = Counter()
    validator_refs: Counter[str] = Counter()
    ids: list[str] = []

    for capability in capabilities if isinstance(capabilities, list) else []:
        if not isinstance(capability, dict):
            continue
        errors = validate_capability(capability)
        cap_id = str(capability.get("id") or "")
        ids.append(cap_id)
        domains[str(capability.get("domain") or "unknown")] += 1
        maturities[str(capability.get("maturity") or "unknown")] += 1
        for language in capability.get("language_scope", []) if isinstance(capability.get("language_scope"), list) else []:
            languages[str(language)] += 1
        for engine in capability.get("engines", []) if isinstance(capability.get("engines"), list) else []:
            engine_refs[str(engine)] += 1
        for artifact in capability.get("artifacts", []) if isinstance(capability.get("artifacts"), list) else []:
            artifact_refs[str(artifact)] += 1
        for validator in capability.get("validators", []) if isinstance(capability.get("validators"), list) else []:
            validator_refs[str(validator)] += 1
        rows.append(
            {
                "id": cap_id,
                "title": capability.get("title"),
                "domain": capability.get("domain"),
                "language_scope": capability.get("language_scope", []),
                "framework_scope": capability.get("framework_scope", []),
                "aliases": capability.get("aliases", []),
                "maturity": capability.get("maturity"),
                "introduced_in": capability.get("introduced_in"),
                "target_release": capability.get("target_release"),
                "priority": capability.get("priority"),
                "effort": capability.get("effort"),
                "value": capability.get("value"),
                "current_state": capability.get("current_state"),
                "roadmap_scope": capability.get("roadmap_scope"),
                "rationale": capability.get("rationale"),
                "engines": capability.get("engines", []),
                "artifacts": capability.get("artifacts", []),
                "validators": capability.get("validators", []),
                "claim_boundary": capability.get("claim_boundary"),
                "valid": not errors,
                "errors": errors,
            }
        )

    duplicates = sorted(item for item in set(ids) if item and ids.count(item) > 1)
    return {
        "total": len(rows),
        "valid": sum(1 for row in rows if row["valid"]),
        "duplicates": duplicates,
        "domains": dict(sorted(domains.items())),
        "language_scopes": dict(sorted(languages.items())),
        "maturities": dict(sorted(maturities.items())),
        "engine_refs": dict(sorted(engine_refs.items())),
        "artifact_refs": dict(sorted(artifact_refs.items())),
        "validator_refs": dict(sorted(validator_refs.items())),
        "capabilities": rows,
    }


def build_agent_capability_map(
    payload: dict[str, Any],
    *,
    allowed_system_scopes: set[str] | None = None,
) -> dict[str, Any]:
    summary = summarize_capabilities(payload)
    capability_rows = []
    artifact_to_capabilities: dict[str, list[str]] = {}
    validator_to_capabilities: dict[str, list[str]] = {}

    for capability in summary.get("capabilities", []):
        cap_id = str(capability.get("id") or "")
        system_scope = capability_scope(cap_id)
        if allowed_system_scopes is not None and system_scope not in allowed_system_scopes:
            continue
        for artifact in capability.get("artifacts", []) or []:
            artifact_to_capabilities.setdefault(str(artifact), []).append(cap_id)
        for validator in capability.get("validators", []) or []:
            validator_to_capabilities.setdefault(str(validator), []).append(cap_id)
        capability_rows.append(
            {
                "id": cap_id,
                "system_scope": system_scope,
                "title": capability.get("title"),
                "domain": capability.get("domain"),
                "language_scope": capability.get("language_scope", []),
                "framework_scope": capability.get("framework_scope", []),
                "maturity": capability.get("maturity"),
                "introduced_in": capability.get("introduced_in"),
                "target_release": capability.get("target_release"),
                "priority": capability.get("priority"),
                "effort": capability.get("effort"),
                "value": capability.get("value"),
                "current_state": capability.get("current_state"),
                "roadmap_scope": capability.get("roadmap_scope"),
                "artifacts_to_trust": capability.get("artifacts", []),
                "validators_to_run": capability.get("validators", []),
                "claim_boundary": capability.get("claim_boundary"),
                "aliases": capability.get("aliases", []),
                "valid": capability.get("valid"),
            }
        )

    return {
        "meta": {
            "kind": "agent_capability_map",
            "source": "config/capability_registry.json",
            "version": "v1",
        },
        "summary": {
            "capabilities": len(capability_rows),
            "valid_capabilities": sum(1 for row in capability_rows if row.get("valid")),
            "system_scope_filter": sorted(allowed_system_scopes) if allowed_system_scopes is not None else [],
            "domains": summary.get("domains", {}),
            "language_scopes": summary.get("language_scopes", {}),
            "maturities": summary.get("maturities", {}),
        },
        "capabilities": capability_rows,
        "artifact_to_capabilities": {
            key: sorted(value)
            for key, value in sorted(artifact_to_capabilities.items())
        },
        "validator_to_capabilities": {
            key: sorted(value)
            for key, value in sorted(validator_to_capabilities.items())
        },
    }
