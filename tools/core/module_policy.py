from __future__ import annotations

import os

from tools.core.config import DOCTRINE, MAIN_PROJECT_ROOT, normalize_path
from tools.core.fractal_policy import get_module_container

PLATFORM_MODULES = DOCTRINE.get("governance_policy", {}).get("platform_modules", [])


def get_module_root_name() -> str:
    return get_module_container()


def get_dynamic_modules():
    module_root_name = get_module_root_name()
    module_root_path = os.path.join(MAIN_PROJECT_ROOT, module_root_name)
    if not os.path.exists(module_root_path):
        return []
    return [name for name in os.listdir(module_root_path) if os.path.isdir(os.path.join(module_root_path, name))]


def classify_module_path(filepath: str, lifecycle_modules=None) -> str:
    normalized = normalize_path(filepath)
    modules = lifecycle_modules if lifecycle_modules is not None else get_dynamic_modules()
    for module_name in modules:
        if module_name in normalized:
            return module_name
    for module_name in PLATFORM_MODULES:
        if normalized.startswith(module_name + "/") or f"/{module_name}/" in normalized:
            return module_name
    return "other"


def get_semantic_rules():
    return DOCTRINE.get("semantic_rules", {})
