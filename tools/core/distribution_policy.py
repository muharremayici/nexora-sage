from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any


CODE_MAPS_DIR = Path(__file__).resolve().parents[2]
PYPROJECT_PATH = CODE_MAPS_DIR / "pyproject.toml"


def distribution_contract() -> dict[str, Any]:
    payload = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
    contract = payload.get("tool", {}).get("nexora_sage", {}).get("distribution", {})
    return contract if isinstance(contract, dict) else {}


def _relative_path_set(key: str) -> set[str]:
    values = distribution_contract().get(key, [])
    if not isinstance(values, list):
        return set()
    return {Path(str(value)).as_posix() for value in values if str(value).strip()}


def clean_mirror_preserved_relative_paths() -> set[str]:
    return _relative_path_set("clean_mirror_preserved_relative_paths")


def clean_mirror_forbidden_relative_paths() -> set[str]:
    return _relative_path_set("clean_mirror_forbidden_relative_paths")


def clean_mirror_source_authority() -> str:
    return str(distribution_contract().get("clean_mirror_source_authority") or "").strip()


def clean_mirror_supplemental_source_paths() -> set[str]:
    return _relative_path_set("clean_mirror_supplemental_source_paths")


def is_clean_install_root(root: Path) -> bool:
    contract = distribution_contract()
    name_token = str(contract.get("clean_mirror_name_token") or "").lower()
    marker = str(contract.get("clean_mirror_marker") or "")
    resolved = root.resolve()
    return bool(name_token and marker and name_token in resolved.name.lower() and (resolved / marker).is_file())


def clean_install_root_for_path(path: Path) -> Path | None:
    """Return the owning clean-install root for a managed projection path."""
    resolved = path.resolve()
    candidates = (resolved, *resolved.parents) if resolved.is_dir() else (resolved.parent, *resolved.parents)
    for candidate in candidates:
        if is_clean_install_root(candidate):
            return candidate
    return None


def is_managed_clean_mirror_path(path: Path) -> bool:
    return clean_install_root_for_path(path) is not None
