from __future__ import annotations

from typing import Any

from tools.core.config import CONFIG_DIR
from tools.core.json_io import load_json_object_strict


FRAMEWORK_CAPABILITIES_FILE = CONFIG_DIR / "framework_capabilities.json"


def load_framework_capabilities() -> dict[str, Any]:
    return load_json_object_strict(FRAMEWORK_CAPABILITIES_FILE, label="Framework capabilities")


def framework_ecosystems() -> dict[str, dict[str, Any]]:
    ecosystems = load_framework_capabilities().get("ecosystems", {})
    return ecosystems if isinstance(ecosystems, dict) else {}


def framework_package_index() -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    for ecosystem, entry in framework_ecosystems().items():
        if not isinstance(entry, dict):
            continue
        for package in entry.get("packages", []) if isinstance(entry.get("packages"), list) else []:
            key = str(package)
            index.setdefault(key, [])
            if ecosystem not in index[key]:
                index[key].append(ecosystem)
    return index


def framework_config_files() -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for ecosystem, entry in framework_ecosystems().items():
        if not isinstance(entry, dict):
            continue
        files = [str(item) for item in entry.get("config_files", []) if str(item).strip()]
        if files:
            result[ecosystem] = files
    return result
