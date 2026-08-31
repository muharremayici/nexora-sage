from __future__ import annotations

from pathlib import Path


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
