from __future__ import annotations

from typing import Any

from tools.core.config import CONFIG_DIR
from tools.core.json_io import load_json_object_strict


POLICY_PATH = CONFIG_DIR / "merge_review_hardening_policy.json"


def load_merge_review_hardening_policy() -> dict[str, Any]:
    return load_json_object_strict(POLICY_PATH, label="Merge review hardening policy")


def merge_dependency_packager_policy() -> dict[str, Any]:
    section = load_merge_review_hardening_policy().get("merge_dependency_packager", {})
    return section if isinstance(section, dict) else {}


def ui_smoke_spec_policy() -> dict[str, Any]:
    section = load_merge_review_hardening_policy().get("ui_smoke_spec_generator", {})
    return section if isinstance(section, dict) else {}


def policy_int(section: dict[str, Any], key: str, default: int = 0) -> int:
    try:
        return int(section.get(key, default))
    except (TypeError, ValueError):
        return default


def policy_string(section: dict[str, Any], key: str, default: str = "") -> str:
    value = section.get(key, default)
    return str(value if value is not None else default)


def policy_string_list(section: dict[str, Any], key: str) -> list[str]:
    value = section.get(key, [])
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]
