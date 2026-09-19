from __future__ import annotations

import tomllib
from collections.abc import Iterable
from pathlib import Path


SELF_TARGET_MODE = "self_target"
EMBEDDED_TARGET_MODE = "embedded"
CENTRAL_EXTERNAL_TARGET_MODE = "central_external"


def installation_target_mode(
    target_root: str | Path,
    installation_root: str | Path,
) -> str:
    """Classify the filesystem relationship without inferring operator authority."""

    target = Path(target_root).resolve()
    installation = Path(installation_root).resolve()
    if installation == target:
        return SELF_TARGET_MODE
    if installation.is_relative_to(target):
        return EMBEDDED_TARGET_MODE
    return CENTRAL_EXTERNAL_TARGET_MODE


def runtime_installation_excluded_roots(
    target_root: str | Path,
    installation_root: str | Path,
) -> set[Path]:
    """Return the running installation root only when it is embedded in the target."""

    installation = Path(installation_root).resolve()
    if installation_target_mode(target_root, installation) == EMBEDDED_TARGET_MODE:
        return {installation}
    return set()


def is_sage_installation_root(root: str | Path) -> bool:
    """Identify a SAGE installation from package metadata and entrypoints, not its folder name."""

    candidate = Path(root).resolve()
    pyproject = candidate / "pyproject.toml"
    if not pyproject.is_file():
        return False
    try:
        payload = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return False
    project = payload.get("project")
    tool = payload.get("tool")
    if not isinstance(project, dict) or not isinstance(tool, dict):
        return False
    sage_tool = tool.get("nexora_sage")
    if not isinstance(sage_tool, dict) or not isinstance(sage_tool.get("distribution"), dict):
        return False
    return (
        str(project.get("name") or "").strip().lower() == "nexora-sage"
        and (candidate / "sage.py").is_file()
        and (candidate / "codemaps.py").is_file()
    )


def path_is_excluded(path: str | Path, excluded_roots: Iterable[Path]) -> bool:
    candidate = Path(path).resolve()
    return any(
        candidate == excluded or candidate.is_relative_to(excluded)
        for excluded in {Path(root).resolve() for root in excluded_roots}
    )


def prune_walk_directories(
    current_root: str | Path,
    directories: list[str],
    *,
    skipped_names: Iterable[str],
    excluded_roots: Iterable[Path],
) -> None:
    """Prune generic names and resolved runtime roots from an os.walk traversal."""

    root = Path(current_root)
    skipped = {str(name).strip().lower() for name in skipped_names if str(name).strip()}
    excluded = {Path(path).resolve() for path in excluded_roots}
    retained: list[str] = []
    for name in directories:
        if name.strip().lower() in skipped:
            continue
        candidate = (root / name).resolve()
        if any(
            candidate == excluded_root or candidate.is_relative_to(excluded_root)
            for excluded_root in excluded
        ):
            continue
        retained.append(name)
    directories[:] = retained
