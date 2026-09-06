from __future__ import annotations

from functools import lru_cache
from tools.core.config import DOCTRINE, DYNAMIC_CONFIG, MAIN_PROJECT_ROOT

FALLBACK_PLATFORM_PATH = "src/platform/core/"
FALLBACK_MODULE_ROOT = "lifecycle-modules"


def get_layer_rules():
    return DOCTRINE.get("layer_rules", [])


def get_platform_mapping():
    return DOCTRINE.get("platform_mapping", [])


def get_platform_default_path() -> str:
    # Prioritize dynamic config or doctrine
    architecture = DYNAMIC_CONFIG.get("architecture", {}) or {}
    platform_gate = architecture.get("platform_gateway") or DOCTRINE.get("platform_default_path")
    if platform_gate:
        return str(platform_gate)
    return FALLBACK_PLATFORM_PATH



def get_module_root() -> str:
    try:
        from tools.core.config import DYNAMIC_CONFIG
        architecture = DYNAMIC_CONFIG.get("architecture", {}) or {}
        return str(architecture.get("module_root", FALLBACK_MODULE_ROOT)).strip("/")
    except (ImportError, NameError):
        return FALLBACK_MODULE_ROOT


def _normalized_relative_path(value: object) -> str:
    text = str(value or ".").replace("\\", "/").strip("/")
    return "." if text in {"", "."} else text


@lru_cache(maxsize=1)
def get_module_container() -> str:
    """Return the directory that directly contains the canonical domain modules."""
    architecture = DYNAMIC_CONFIG.get("architecture", {}) or {}
    declared = _normalized_relative_path(architecture.get("module_container"))
    if declared != ".":
        return declared

    known_modules = {
        str(value).strip("/")
        for value in (DOCTRINE.get("studio_to_module_map", {}) or {}).values()
        if str(value).strip("/")
    }
    if not known_modules:
        return _normalized_relative_path(architecture.get("module_root"))

    direct_names = {
        child.name
        for child in MAIN_PROJECT_ROOT.iterdir()
        if child.is_dir()
    }
    if direct_names.intersection(known_modules):
        return "."

    candidates: list[tuple[int, str]] = []
    for child in sorted(MAIN_PROJECT_ROOT.iterdir(), key=lambda item: item.name):
        if not child.is_dir():
            continue
        child_names = {
            nested.name
            for nested in child.iterdir()
            if nested.is_dir()
        }
        match_count = len(child_names.intersection(known_modules))
        if match_count:
            candidates.append((match_count, child.name))
    if candidates:
        candidates.sort(key=lambda item: (-item[0], item[1]))
        return candidates[0][1]

    return _normalized_relative_path(architecture.get("module_root"))


def get_main_module_base_prefix() -> str:
    """Return the workspace-relative prefix used for generated MAIN module targets."""
    main_project = _normalized_relative_path(
        (DYNAMIC_CONFIG.get("variations", {}) or {}).get("MAIN")
    )
    container = get_module_container()
    parts = [part for part in (main_project, container) if part != "."]
    return "/".join(parts) or "."


def get_scoring_config():
    return DOCTRINE.get("scoring_config", {})
