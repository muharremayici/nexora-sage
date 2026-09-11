from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from pathlib import Path


SAFE_PYTHON_SUBPROCESS_KEYS = frozenset(
    {
        "PATH",
        "SYSTEMROOT",
        "COMSPEC",
        "TEMP",
        "TMP",
        "PATHEXT",
        "PYTHONPATH",
        "PYTHONUSERBASE",
        "PYTHONNOUSERSITE",
        "APPDATA",
        "LOCALAPPDATA",
        "USERPROFILE",
        "HOME",
        "HOMEPATH",
        "HOMEDRIVE",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
    }
)

SAFE_CODEMAPS_SUBPROCESS_KEYS = frozenset(
    {
        "CODEMAPS_ALLOW_UNSAFE_AST_PARALLEL",
        "CODEMAPS_ALLOW_UNSAFE_FRACTAL_PARALLEL",
        "CODEMAPS_AST_BATCH_WORKERS",
        "CODEMAPS_AST_CHUNK_SIZE",
        "CODEMAPS_AST_LEGACY_PARALLEL",
        "CODEMAPS_ATLAS_FINGERPRINT_LIFT",
        "CODEMAPS_ATLAS_MD5_LIFT",
        "CODEMAPS_ATLAS_PROJECT_WORKERS",
        "CODEMAPS_DEAD_CODE_ALLOWLIST_SCOPE",
        "CODEMAPS_FRACTAL_MD5_LIFT",
        "CODEMAPS_FRACTAL_SEMANTIC_MODE",
        "CODEMAPS_FRACTAL_WORKERS",
        "CODEMAPS_INSIDE_PIPELINE",
        "CODEMAPS_PATH",
        "CODEMAPS_PIPELINE_WORKERS",
        "CODEMAPS_PROFILE",
        "CODEMAPS_TARGET_PREFLIGHT_RECEIPT",
        "CODEMAPS_TARGET_PREFLIGHT_RECEIPT_SHA256",
        "CODEMAPS_TARGET_PREFLIGHT_REUSE_MODE",
        "CODEMAPS_TARGET_PROJECTS",
        "CODEMAPS_TARGET_ROOT",
    }
)


def _safe_git_config_environment(source_env: Mapping[str, str]) -> dict[str, str]:
    """Forward only invocation-scoped safe.directory entries."""
    try:
        count = max(0, int(source_env.get("GIT_CONFIG_COUNT", "0")))
    except (TypeError, ValueError):
        count = 0
    entries: list[str] = []
    for index in range(count):
        if source_env.get(f"GIT_CONFIG_KEY_{index}") != "safe.directory":
            continue
        value = str(source_env.get(f"GIT_CONFIG_VALUE_{index}") or "").strip()
        if value and value not in entries:
            entries.append(value)
    safe_git_env: dict[str, str] = {"GIT_CONFIG_COUNT": str(len(entries))}
    for index, value in enumerate(entries):
        safe_git_env[f"GIT_CONFIG_KEY_{index}"] = "safe.directory"
        safe_git_env[f"GIT_CONFIG_VALUE_{index}"] = value
    return safe_git_env


def compose_pythonpath(
    preferred_entries: Iterable[str | Path],
    existing_value: str | None,
    *,
    separator: str | None = None,
) -> str:
    """Preserve first-seen import order while dropping empty and duplicate path entries."""
    path_separator = separator or os.pathsep
    candidates = [str(entry) for entry in preferred_entries]
    if existing_value:
        candidates.extend(existing_value.split(path_separator))

    seen: set[str] = set()
    entries: list[str] = []
    for entry in candidates:
        if not entry:
            continue
        identity = os.path.normcase(os.path.normpath(entry))
        if identity in seen:
            continue
        seen.add(identity)
        entries.append(entry)
    return path_separator.join(entries)


def python_subprocess_env(
    source_env: Mapping[str, str],
    *,
    code_maps_dir: Path,
    vendor_paths: Iterable[Path],
) -> dict[str, str]:
    """Build the shared Python subprocess environment without mutating caller state."""
    env = dict(source_env)
    env["PYTHONPATH"] = compose_pythonpath(
        [code_maps_dir, *vendor_paths],
        env.get("PYTHONPATH"),
    )
    return utf8_subprocess_env(env)


def utf8_subprocess_env(source_env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return an isolated copy with deterministic UTF-8 child-process I/O."""

    env = dict(os.environ if source_env is None else source_env)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def isolated_python_subprocess_env(
    source_env: Mapping[str, str],
    *,
    code_maps_dir: Path,
    vendor_paths: Iterable[Path],
) -> dict[str, str]:
    """Preserve Python runtime identity without forwarding unrelated caller secrets."""
    safe_source = {
        key: value
        for key, value in source_env.items()
        if key in SAFE_PYTHON_SUBPROCESS_KEYS
        or key in SAFE_CODEMAPS_SUBPROCESS_KEYS
    }
    safe_source.update(_safe_git_config_environment(source_env))
    return python_subprocess_env(
        safe_source,
        code_maps_dir=code_maps_dir,
        vendor_paths=vendor_paths,
    )
