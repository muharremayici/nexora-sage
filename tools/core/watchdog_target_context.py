from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from tools.core.config import CODE_MAPS_DIR, DYNAMIC_CONFIG, RAW_DIR, REPORTS_DIR, ROOT
from tools.core.execution_identity import (
    SAGE_ACTOR_PROFILE_ENV,
    SAGE_OPERATOR_ACTOR_PROFILE,
    SAGE_REALITY_TARGET_PROFILE_ENV,
    resolve_execution_identity,
)
from tools.core.json_io import load_json_object_strict


REALITY_TARGET_PROFILES_PATH = CODE_MAPS_DIR / "config" / "reality_target_profiles.json"
AGENT_SURFACE_TAXONOMY_PATH = CODE_MAPS_DIR / "config" / "agent_surface_taxonomy.json"
REALITY_TARGET_PROFILE_ENV = SAGE_REALITY_TARGET_PROFILE_ENV


def _target_repository_scope() -> str:
    taxonomy = load_json_object_strict(
        AGENT_SURFACE_TAXONOMY_PATH,
        label="Agent surface and reality scope taxonomy",
    )
    policies = taxonomy.get("canonical_registry_projection_policy", {})
    policy = policies.get("target_repository", {}) if isinstance(policies, dict) else {}
    scope = str(policy.get("system_scope") or "").strip()
    if not scope:
        raise ValueError("Target repository scope is missing from the canonical agent surface taxonomy")
    return scope


def _sage_self_profile() -> dict[str, Any]:
    payload = load_json_object_strict(
        REALITY_TARGET_PROFILES_PATH,
        label="Reality target profiles",
    )
    profiles = payload.get("profiles", []) if isinstance(payload, dict) else []
    for profile in profiles:
        if isinstance(profile, dict) and profile.get("id") == "sage_self":
            return profile
    return {}


def current_watchdog_target_descriptor() -> dict[str, Any]:
    """Describe the current watchdog process subject without changing its engine."""
    override = DYNAMIC_CONFIG.get("_target_root_override", {})
    override = override if isinstance(override, dict) else {}
    external_target = bool(override.get("enabled"))
    analysis_root = Path(override.get("target_root") or ROOT).resolve()
    requested_profile = str(os.environ.get(REALITY_TARGET_PROFILE_ENV) or "").strip()
    if requested_profile and requested_profile != "sage_self":
        raise ValueError(f"Unsupported watchdog reality target profile: {requested_profile}")
    sage_self = requested_profile == "sage_self"
    if sage_self:
        from tools.core.reality_scope import build_reality_target_workflow

        workflow = build_reality_target_workflow(requested_profile)
        expected_root = Path(str(workflow.get("analysis_root") or "")).resolve()
        if not external_target or analysis_root != expected_root:
            raise ValueError(
                "The explicit sage_self watchdog profile requires its canonical explicit target root"
            )
    execution_identity = resolve_execution_identity(
        target_root=str(analysis_root) if external_target else "",
        actor_profile=str(os.environ.get(SAGE_ACTOR_PROFILE_ENV) or ""),
        reality_profile=requested_profile,
        public_distribution=(CODE_MAPS_DIR / "PUBLIC_DISTRIBUTION_MANIFEST.json").is_file(),
        installation_root=CODE_MAPS_DIR,
        default_repository_root=ROOT,
    )
    if sage_self and execution_identity.get("actor_profile") != SAGE_OPERATOR_ACTOR_PROFILE:
        raise ValueError("The sage_self watchdog requires the private sage_operator_debug actor profile")
    profile = _sage_self_profile() if sage_self else {}
    watchdog_support = profile.get("watchdog_support", {}) if isinstance(profile, dict) else {}
    watchdog_support = watchdog_support if isinstance(watchdog_support, dict) else {}
    proof_debt_policy = (
        dict(watchdog_support.get("proof_debt_policy"))
        if isinstance(watchdog_support.get("proof_debt_policy"), dict)
        else {}
    )
    reset_artifact_scope = str(
        proof_debt_policy.get("reset_artifact_scope") or "current_target_output"
    )
    proof_authority_root = (
        CODE_MAPS_DIR / "output" / ".raw"
        if reset_artifact_scope == "primary_sage_output"
        else RAW_DIR
    )

    return {
        "kind": "watchdog_target_descriptor",
        "version": "v1",
        "system_scope": str(
            execution_identity.get("system_scope")
            or profile.get("system_scope")
            or _target_repository_scope()
        ),
        "acquisition_mode": str(execution_identity.get("acquisition_mode") or ""),
        "profile_id": str(profile.get("id") or "target_repository_default"),
        "actor_profile": str(execution_identity.get("actor_profile") or ""),
        "reality_profile": str(execution_identity.get("reality_profile") or ""),
        "authority_mode": str(execution_identity.get("authority_mode") or ""),
        "subject_root": str(execution_identity.get("subject_root") or analysis_root),
        "analysis_root": str(analysis_root),
        "artifact_strategy": str(
            profile.get("artifact_strategy")
            or execution_identity.get("artifact_strategy")
        ),
        "artifact_root": str(RAW_DIR),
        "reports_root": str(REPORTS_DIR),
        "external_target": external_target,
        "proof_authority_root": str(proof_authority_root),
        "proof_debt_field": str(
            watchdog_support.get("session_debt_field") or "target_repository_deep_proof_debt"
        ),
        "proof_debt_policy": proof_debt_policy,
        "release_proof_behavior": str(
            watchdog_support.get("release_proof_behavior") or "policy_controlled"
        ),
    }
