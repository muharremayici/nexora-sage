from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.core.operational_limits import install_proof_timeout_seconds


PUBLIC_DISTRIBUTION_MANIFEST = "PUBLIC_DISTRIBUTION_MANIFEST.json"
PUBLIC_TARGET_REPOSITORY_PROFILE = "public_target_repository"
PRIVATE_MAINTAINER_PROFILE = "private_maintainer"


def resolve_installation_authority_profile(
    root: Path,
    *,
    public_distribution: bool | None = None,
) -> str:
    if public_distribution is None:
        public_distribution = (root / PUBLIC_DISTRIBUTION_MANIFEST).is_file()
    return (
        PUBLIC_TARGET_REPOSITORY_PROFILE
        if public_distribution
        else PRIVATE_MAINTAINER_PROFILE
    )


class InstallationDoctorBudgetError(ValueError):
    """The independent installation cannot safely start with this deadline configuration."""


def public_release_doctor_budget(
    contract: dict[str, Any], *, outer_override: int | None = None,
) -> dict[str, int]:
    profiles = contract.get("validation_profiles")
    profile = profiles.get(PUBLIC_TARGET_REPOSITORY_PROFILE) if isinstance(profiles, dict) else None
    budget = profile.get("installation_proof_release_doctor_budget") if isinstance(profile, dict) else None
    keys = (
        "quick_validation_seconds",
        "outer_doctor_watchdog_seconds",
        "minimum_non_validator_headroom_seconds",
    )
    if not isinstance(budget, dict) or any(type(budget.get(key)) is not int or budget[key] <= 0 for key in keys):
        raise InstallationDoctorBudgetError("Public release installation doctor requires three positive integer budgets.")

    inner, default_outer, headroom = (budget[key] for key in keys)
    minimum_outer = inner + headroom
    enclosing_limit = install_proof_timeout_seconds("release")
    if not minimum_outer <= default_outer < enclosing_limit:
        raise InstallationDoctorBudgetError(
            f"Public release installation doctor requires a default outer limit of at least "
            f"{minimum_outer}s and less than the {enclosing_limit}s installation proof limit."
        )
    if outer_override is not None and (
        type(outer_override) is not int or not minimum_outer <= outer_override < enclosing_limit
    ):
        raise InstallationDoctorBudgetError(
            f"Public release installation --max-doctor-seconds must be at least {minimum_outer}s "
            f"and less than {enclosing_limit}s."
        )
    return {
        "quick_validation_seconds": inner,
        "outer_doctor_watchdog_seconds": default_outer if outer_override is None else outer_override,
        "minimum_non_validator_headroom_seconds": headroom,
    }
