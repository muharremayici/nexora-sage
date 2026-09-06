from __future__ import annotations

from typing import Any

from tools.core.config import CONFIG_DIR
from tools.core.json_io import load_json_object_strict
from tools.core.execution_waves import project_execution_waves
from tools.core.sage_active_work_package import active_work_package
from tools.core.watchdog_proof_debt import validate_watchdog_proof_debt_policy


TAXONOMY_PATH = CONFIG_DIR / "agent_surface_taxonomy.json"
LESSON_REGISTRY_PATH = CONFIG_DIR / "audit_lesson_registry.json"
WORK_ITEM_REGISTRY_PATH = CONFIG_DIR / "sage_work_item_registry.json"
ROADMAP_REGISTRY_PATH = CONFIG_DIR / "roadmap_phase_registry.json"
CAPABILITY_REGISTRY_PATH = CONFIG_DIR / "capability_registry.json"
REALITY_TARGET_PROFILES_PATH = CONFIG_DIR / "reality_target_profiles.json"
PUBLIC_DISTRIBUTION_MANIFEST_PATH = CONFIG_DIR.parent / "PUBLIC_DISTRIBUTION_MANIFEST.json"
SAGE_DEVELOPER_PROJECTION_ID = "sage_developer"
TARGET_REPOSITORY_PROJECTION_ID = "target_repository"
SAGE_SELF_TARGET_PROFILE_ID = "sage_self"


def _require_private_maintainer_surface(surface_id: str) -> None:
    if PUBLIC_DISTRIBUTION_MANIFEST_PATH.is_file():
        raise ValueError(
            f"The {surface_id} surface requires private maintainer authority and is unavailable "
            "in a public distribution. Use a target_repository profile instead."
        )


def load_reality_scope_taxonomy() -> dict[str, Any]:
    return load_json_object_strict(TAXONOMY_PATH, label="Agent surface and reality scope taxonomy")


def system_scope_ids(taxonomy: dict[str, Any] | None = None) -> set[str]:
    payload = taxonomy or load_reality_scope_taxonomy()
    rows = payload.get("system_scopes", []) if isinstance(payload, dict) else []
    return {str(row.get("id")) for row in rows if isinstance(row, dict) and row.get("id")}


def capability_scope_assignments(taxonomy: dict[str, Any] | None = None) -> dict[str, set[str]]:
    payload = taxonomy or load_reality_scope_taxonomy()
    assignments = payload.get("capability_scope_assignments", {}) if isinstance(payload, dict) else {}
    if not isinstance(assignments, dict):
        return {}
    return {
        str(scope): {str(capability_id) for capability_id in values if str(capability_id).strip()}
        for scope, values in assignments.items()
        if isinstance(values, list)
    }


def capability_scope(capability_id: str, taxonomy: dict[str, Any] | None = None) -> str | None:
    target = str(capability_id or "").strip()
    matches = [
        scope
        for scope, capability_ids in capability_scope_assignments(taxonomy).items()
        if target in capability_ids
    ]
    return matches[0] if len(matches) == 1 else None


def projection_capability_scopes(
    projection_id: str,
    taxonomy: dict[str, Any] | None = None,
) -> set[str]:
    payload = taxonomy or load_reality_scope_taxonomy()
    policies = payload.get("canonical_registry_projection_policy", {})
    policy = policies.get(projection_id, {}) if isinstance(policies, dict) else {}
    if not isinstance(policy, dict) or not policy:
        raise ValueError(f"Unknown reality scope projection: {projection_id}")
    return {str(item) for item in policy.get("capability_scopes", []) if str(item).strip()}


def load_reality_target_profiles() -> dict[str, Any]:
    return load_json_object_strict(REALITY_TARGET_PROFILES_PATH, label="Reality target profiles")


def build_reality_target_workflow(profile_id: str) -> dict[str, Any]:
    if (
        str(profile_id or "") == SAGE_SELF_TARGET_PROFILE_ID
        and PUBLIC_DISTRIBUTION_MANIFEST_PATH.is_file()
    ):
        raise ValueError(
            "The sage_self reality target requires private maintainer authority and is unavailable "
            "in a public distribution. Analyze SAGE source through a target_repository profile instead."
        )
    payload = load_reality_target_profiles()
    profiles = payload.get("profiles", []) if isinstance(payload, dict) else []
    profile = next(
        (
            row
            for row in profiles
            if isinstance(row, dict) and str(row.get("id") or "") == str(profile_id or "")
        ),
        None,
    )
    if profile is None:
        raise ValueError(f"Unknown reality target profile: {profile_id}")
    root_token = str(profile.get("analysis_root_token") or "")
    if root_token != "${code_maps}":
        raise ValueError(f"Unsupported analysis root token for {profile_id}: {root_token}")
    analysis_root = str(CONFIG_DIR.parent.resolve())

    def _resolve_tokens(value: Any) -> Any:
        if isinstance(value, str):
            return value.replace("${analysis_root}", analysis_root).replace("${code_maps}", analysis_root)
        if isinstance(value, list):
            return [_resolve_tokens(item) for item in value]
        if isinstance(value, dict):
            return {str(key): _resolve_tokens(item) for key, item in value.items()}
        return value

    resolved = _resolve_tokens(profile)
    return {
        "meta": {
            "kind": "reality_target_workflow",
            "version": "v1",
            "profile_id": profile_id,
            "source": "config/reality_target_profiles.json",
        },
        "analysis_root": analysis_root,
        "profile": resolved,
    }


def validate_reality_target_profiles(available_tools: set[str] | None = None) -> dict[str, Any]:
    _require_private_maintainer_surface("reality target profile validation")
    payload = load_reality_target_profiles()
    contract = payload.get("validation_contract", {}) if isinstance(payload, dict) else {}
    profiles = payload.get("profiles", []) if isinstance(payload, dict) else []
    profile_rows = {
        str(row.get("id")): row
        for row in profiles
        if isinstance(row, dict) and str(row.get("id") or "").strip()
    }
    required_ids = {str(item) for item in contract.get("required_profile_ids", [])}
    required_fields = {str(item) for item in contract.get("required_profile_fields", [])}
    allowed_root_tokens = {str(item) for item in contract.get("allowed_analysis_root_tokens", [])}
    required_self_tools = {str(item) for item in contract.get("required_sage_self_tools", [])}
    issues: list[dict[str, Any]] = []
    for profile_id in sorted(required_ids):
        row = profile_rows.get(profile_id)
        if row is None:
            issues.append({"missing_profile": profile_id})
            continue
        missing_fields = sorted(field for field in required_fields if row.get(field) in (None, "", []))
        if missing_fields:
            issues.append({"profile": profile_id, "missing_fields": missing_fields})
        if str(row.get("analysis_root_token") or "") not in allowed_root_tokens:
            issues.append({"profile": profile_id, "invalid_analysis_root_token": row.get("analysis_root_token")})
    sage_self = profile_rows.get(SAGE_SELF_TARGET_PROFILE_ID, {})
    if sage_self.get("actor_profile") != "sage_operator_debug":
        issues.append({"sage_self_actor_profile_mismatch": sage_self.get("actor_profile")})
    bootstrap_tools = {
        str(row.get("tool"))
        for row in sage_self.get("bootstrap_tools", [])
        if isinstance(row, dict) and row.get("tool")
    }
    surgical_tools = {str(item) for item in sage_self.get("surgical_tools", [])}
    declared_tools = bootstrap_tools | surgical_tools
    missing_required_tools = sorted(required_self_tools - declared_tools)
    if missing_required_tools:
        issues.append({"sage_self_missing_required_tools": missing_required_tools})
    unavailable_tools = sorted(declared_tools - available_tools) if available_tools is not None else []
    if unavailable_tools:
        issues.append({"sage_self_unavailable_mcp_tools": unavailable_tools})
    watchdog = sage_self.get("watchdog_support", {}) if isinstance(sage_self.get("watchdog_support"), dict) else {}
    proof_debt_policy = watchdog.get("proof_debt_policy") if isinstance(watchdog.get("proof_debt_policy"), dict) else {}
    proof_debt_validation = validate_watchdog_proof_debt_policy(proof_debt_policy)
    if (
        watchdog.get("status") != "supported_for_isolated_self_target"
        or not watchdog.get("tracked_work_item")
        or not watchdog.get("session_debt_field")
        or watchdog.get("release_proof_behavior") != "explicit_only_non_recursive"
        or not proof_debt_validation["valid"]
    ):
        issues.append(
            {
                "sage_self_watchdog_boundary_incomplete": watchdog,
                "proof_debt_policy_validation": proof_debt_validation,
            }
        )
    framework_boundary = (
        sage_self.get("framework_evidence_boundary", {})
        if isinstance(sage_self.get("framework_evidence_boundary"), dict)
        else {}
    )
    if (
        framework_boundary.get("status") != "source_vocabulary_is_not_ecosystem_proof"
        or not framework_boundary.get("required_activation_evidence")
        or not framework_boundary.get("excluded_from_default_self_target_meaning")
        or not framework_boundary.get("agent_rule")
    ):
        issues.append({"sage_self_framework_evidence_boundary_incomplete": framework_boundary})
    validation_commands = [str(item) for item in sage_self.get("validation_commands", []) if str(item).strip()]
    nonportable_commands = [command for command in validation_commands if "\\" in command]
    if nonportable_commands:
        issues.append({"sage_self_nonportable_validation_commands": nonportable_commands})
    return {
        "issues": issues,
        "profiles": sorted(profile_rows),
        "sage_self_tools": sorted(declared_tools),
        "sage_self_watchdog_status": watchdog.get("status"),
        "sage_self_framework_evidence_status": framework_boundary.get("status"),
    }


def build_reality_scope_projection(projection_id: str) -> dict[str, Any]:
    if str(projection_id or "") == SAGE_DEVELOPER_PROJECTION_ID:
        _require_private_maintainer_surface(SAGE_DEVELOPER_PROJECTION_ID)
    taxonomy = load_reality_scope_taxonomy()
    policies = taxonomy.get("canonical_registry_projection_policy", {})
    policy = policies.get(projection_id, {}) if isinstance(policies, dict) else {}
    if not isinstance(policy, dict) or not policy:
        raise ValueError(f"Unknown reality scope projection: {projection_id}")

    included_registries = {str(item) for item in policy.get("include_registries", [])}
    capability_scopes = projection_capability_scopes(projection_id, taxonomy)
    assignments = capability_scope_assignments(taxonomy)
    visible_capability_ids = set().union(
        *(assignments.get(scope, set()) for scope in capability_scopes)
    ) if capability_scopes else set()

    capability_registry = load_json_object_strict(CAPABILITY_REGISTRY_PATH, label="Capability registry")
    capabilities = [
        row
        for row in capability_registry.get("capabilities", [])
        if isinstance(row, dict) and str(row.get("id") or "") in visible_capability_ids
    ]
    result: dict[str, Any] = {
        "meta": {
            "kind": "reality_scope_projection",
            "version": "v1",
            "projection_id": projection_id,
            "system_scope": policy.get("system_scope"),
            "source": "config/agent_surface_taxonomy.json",
        },
        "policy": policy,
        "capabilities": capabilities,
        "runtime_finding_source": policy.get("runtime_finding_source"),
        "scope_boundary": {
            "visible_capability_scopes": sorted(capability_scopes),
            "excluded_registries": [str(item) for item in policy.get("exclude_registries", [])],
        },
    }
    if policy.get("include_transfer_matrix") is True:
        result["bidirectional_capability_transfer_matrix"] = taxonomy.get(
            "bidirectional_capability_transfer_matrix", []
        )
    if "audit_lesson_registry" in included_registries:
        result["lessons"] = load_json_object_strict(LESSON_REGISTRY_PATH, label="Audit lesson registry").get("lessons", [])
    if "sage_work_item_registry" in included_registries:
        result["work_items"] = load_json_object_strict(WORK_ITEM_REGISTRY_PATH, label="SAGE work item registry").get("work_items", [])
    if "sage_execution_wave_registry" in included_registries:
        result["execution_plan"] = project_execution_waves(
            result.get("work_items", []),
            active_package=active_work_package(),
        )
    if "roadmap_phase_registry" in included_registries:
        result["roadmap_phases"] = load_json_object_strict(ROADMAP_REGISTRY_PATH, label="Roadmap phase registry").get("phases", [])
    return result


def validate_reality_scope_projections(required_system_scopes: set[str]) -> dict[str, Any]:
    taxonomy = load_reality_scope_taxonomy()
    capability_registry = load_json_object_strict(CAPABILITY_REGISTRY_PATH, label="Capability registry")
    capability_ids = {
        str(row.get("id"))
        for row in capability_registry.get("capabilities", [])
        if isinstance(row, dict) and row.get("id")
    }
    assignments = capability_scope_assignments(taxonomy)
    assigned_ids = set().union(*assignments.values()) if assignments else set()
    duplicate_assignments = sorted(
        capability_id
        for capability_id in assigned_ids
        if sum(1 for values in assignments.values() if capability_id in values) != 1
    )
    issues: list[dict[str, Any]] = []
    identity_contract = taxonomy.get("execution_identity_contract", {})
    identity_axes = identity_contract.get("axes", {}) if isinstance(identity_contract, dict) else {}
    scope_axis = identity_axes.get("system_scope", {}) if isinstance(identity_axes, dict) else {}
    acquisition_axis = identity_axes.get("acquisition_mode", {}) if isinstance(identity_axes, dict) else {}
    declared_scopes = set(scope_axis.get("allowed", []) if isinstance(scope_axis, dict) else [])
    acquisition_modes = set(
        acquisition_axis.get("allowed", []) if isinstance(acquisition_axis, dict) else []
    )
    parity_invariants = (
        identity_contract.get("parity_invariants", []) if isinstance(identity_contract, dict) else []
    )
    forbidden_inferences = (
        identity_contract.get("forbidden_inferences", []) if isinstance(identity_contract, dict) else []
    )
    authority_rules = (
        identity_contract.get("authority_rules", {}) if isinstance(identity_contract, dict) else {}
    )
    required_forbidden_inferences = {
        "installation-root equality grants SAGE_ON_SAGE authority",
        "folder names or source markers grant SAGE_ON_SAGE authority",
        "analyzing SAGE source as a repository inherits private lessons debt roadmap release proof or seal authority",
    }
    if (
        declared_scopes != {"SAGE_ON_SAGE", "SAGE_ON_REPOSITORY"}
        or acquisition_modes != {"DEFAULT_WORKSPACE", "EXPLICIT_TARGET"}
        or identity_contract.get("repository_semantics") != "CANONICAL_REPOSITORY_ONTOLOGY"
        or len(parity_invariants) < 4
        or not required_forbidden_inferences.issubset(set(forbidden_inferences))
        or authority_rules.get("sage_on_sage_actor_profile") != "sage_operator_debug"
        or authority_rules.get("sage_on_sage_reality_profile") != "sage_self"
        or authority_rules.get("sage_on_sage_requires_explicit_profile_pair") is not True
        or set(authority_rules.get("public_distribution_actor_profiles", []))
        != {"target_repository_default", "target_repository_followup"}
        or set(authority_rules.get("private_maintainer_actor_profiles", []))
        != {"sage_operator_debug"}
        or set(authority_rules.get("private_maintainer_reality_profiles", []))
        != {"sage_self"}
        or set(authority_rules.get("public_distribution_allowed_system_scopes", []))
        != {"SAGE_ON_REPOSITORY"}
        or authority_rules.get("source_tree_as_ordinary_target") != "SAGE_ON_REPOSITORY"
        or not str(authority_rules.get("public_source_development_rule") or "").strip()
    ):
        issues.append(
            {
                "execution_identity_contract_incomplete": {
                    "system_scopes": sorted(declared_scopes),
                    "acquisition_modes": sorted(acquisition_modes),
                    "repository_semantics": identity_contract.get("repository_semantics"),
                    "parity_invariants": len(parity_invariants),
                    "forbidden_inferences": len(forbidden_inferences),
                    "authority_rules": authority_rules,
                }
            }
        )
    assignment_issues = {
        "unknown_assignment_scopes": sorted(set(assignments) - required_system_scopes),
        "unassigned_capabilities": sorted(capability_ids - assigned_ids),
        "unknown_capabilities": sorted(assigned_ids - capability_ids),
        "duplicate_assignments": duplicate_assignments,
    }
    if any(assignment_issues.values()):
        issues.append({"capability_scope_assignment_issues": assignment_issues})

    projection_summaries: dict[str, Any] = {}
    try:
        developer_projection = build_reality_scope_projection(SAGE_DEVELOPER_PROJECTION_ID)
        target_projection = build_reality_scope_projection(TARGET_REPOSITORY_PROJECTION_ID)
        target_capability_ids = {
            str(row.get("id"))
            for row in target_projection.get("capabilities", [])
            if isinstance(row, dict) and row.get("id")
        }
        forbidden_target_keys = sorted(
            key for key in ("lessons", "work_items", "roadmap_phases") if key in target_projection
        )
        leaked_self_capabilities = sorted(target_capability_ids & assignments.get("SAGE_ON_SAGE", set()))
        target_transfer_leak = "bidirectional_capability_transfer_matrix" in target_projection
        developer_transfer_rows = developer_projection.get("bidirectional_capability_transfer_matrix", [])
        if forbidden_target_keys or leaked_self_capabilities or target_transfer_leak:
            issues.append(
                {
                    "target_repository": {
                        "forbidden_registry_keys": forbidden_target_keys,
                        "leaked_self_capabilities": leaked_self_capabilities,
                        "transfer_matrix_leaked": target_transfer_leak,
                    }
                }
            )
        for required_key in ("lessons", "work_items", "roadmap_phases"):
            if required_key not in developer_projection:
                issues.append({"sage_developer_missing_registry": required_key})
        if not isinstance(developer_transfer_rows, list) or not developer_transfer_rows:
            issues.append({"sage_developer_missing_transfer_matrix": True})
        projection_summaries = {
            "sage_developer": {
                "system_scope": developer_projection.get("meta", {}).get("system_scope"),
                "capabilities": len(developer_projection.get("capabilities", [])),
                "lessons": len(developer_projection.get("lessons", [])),
                "work_items": len(developer_projection.get("work_items", [])),
                "roadmap_phases": len(developer_projection.get("roadmap_phases", [])),
                "transfer_decisions": len(developer_transfer_rows),
            },
            "target_repository": {
                "system_scope": target_projection.get("meta", {}).get("system_scope"),
                "capabilities": len(target_projection.get("capabilities", [])),
                "forbidden_registry_keys": forbidden_target_keys,
                "leaked_self_capabilities": leaked_self_capabilities,
                "transfer_matrix_leaked": target_transfer_leak,
            },
        }
    except (OSError, ValueError, TypeError, KeyError) as exc:
        issues.append({"projection_build_error": f"{type(exc).__name__}: {exc}"})
    return {
        "issues": issues,
        "capability_scope_counts": {scope: len(values) for scope, values in sorted(assignments.items())},
        "projection_summaries": projection_summaries,
    }
