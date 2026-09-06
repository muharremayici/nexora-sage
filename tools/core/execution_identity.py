from __future__ import annotations

from pathlib import Path
from typing import Any


SAGE_ON_SAGE = "SAGE_ON_SAGE"
SAGE_ON_REPOSITORY = "SAGE_ON_REPOSITORY"
DEFAULT_WORKSPACE = "DEFAULT_WORKSPACE"
EXPLICIT_TARGET = "EXPLICIT_TARGET"
SAGE_OPERATOR_ACTOR_PROFILE = "sage_operator_debug"
SAGE_SELF_REALITY_PROFILE = "sage_self"
TARGET_REPOSITORY_ACTOR_PROFILES = {
    "target_repository_default",
    "target_repository_followup",
}
SAGE_ACTOR_PROFILE_ENV = "SAGE_ACTOR_PROFILE"
SAGE_REALITY_TARGET_PROFILE_ENV = "SAGE_REALITY_TARGET_PROFILE"


def resolve_execution_identity(
    *,
    target_root: str = "",
    actor_profile: str = "",
    reality_profile: str = "",
    public_distribution: bool = False,
    installation_root: str | Path,
    default_repository_root: str | Path | None = None,
) -> dict[str, Any]:
    """Resolve governance subject independently from repository acquisition."""
    installation = Path(installation_root).resolve()
    requested_target = str(target_root or "").strip()
    target = Path(requested_target).expanduser().resolve() if requested_target else None
    acquisition_mode = EXPLICIT_TARGET if target is not None else DEFAULT_WORKSPACE
    actor = str(actor_profile or "").strip()
    reality = str(reality_profile or "").strip()

    known_actor_profiles = TARGET_REPOSITORY_ACTOR_PROFILES | {SAGE_OPERATOR_ACTOR_PROFILE}
    if actor and actor not in known_actor_profiles:
        raise ValueError(f"Unknown SAGE actor profile: {actor}")
    if reality and reality != SAGE_SELF_REALITY_PROFILE:
        raise ValueError(f"Unknown SAGE reality profile: {reality}")

    has_self_actor = actor == SAGE_OPERATOR_ACTOR_PROFILE
    has_self_reality = reality == SAGE_SELF_REALITY_PROFILE
    if has_self_actor != has_self_reality:
        raise ValueError(
            "SAGE_ON_SAGE requires the explicit sage_operator_debug + sage_self profile pair"
        )
    self_profile_pair = has_self_actor and has_self_reality
    if public_distribution and self_profile_pair:
        raise ValueError("Public distributions cannot authorize SAGE_ON_SAGE")
    if self_profile_pair and target is not None and target != installation:
        raise ValueError(
            "The sage_self reality profile may target only the canonical SAGE installation root"
        )

    explicit_self_target = bool(self_profile_pair and target is not None)
    system_scope = SAGE_ON_SAGE if self_profile_pair else SAGE_ON_REPOSITORY
    default_repository = (
        Path(default_repository_root).resolve()
        if default_repository_root is not None
        else installation
    )
    subject_root = (
        target
        if target is not None
        else installation
        if system_scope == SAGE_ON_SAGE
        else default_repository
    )
    return {
        "system_scope": system_scope,
        "acquisition_mode": acquisition_mode,
        "subject_root": str(subject_root),
        "installation_root": str(installation),
        "repository_semantics": "CANONICAL_REPOSITORY_ONTOLOGY",
        "artifact_strategy": (
            "ISOLATED_TARGET_NAMESPACE"
            if acquisition_mode == EXPLICIT_TARGET
            else "DEFAULT_WORKSPACE_NAMESPACE"
        ),
        "actor_profile": actor,
        "reality_profile": reality,
        "authority_mode": (
            "EXPLICIT_SAGE_SELF_PROFILE_PAIR"
            if self_profile_pair
            else "TARGET_REPOSITORY_DEFAULT"
        ),
        "explicit_self_target": explicit_self_target,
    }
