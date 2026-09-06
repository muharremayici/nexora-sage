from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from tools.core.config import CONFIG_DIR
from tools.core.json_io import load_json_object_strict_cached
from tools.core.language_registry import language_for_extension


PROFILE_PATH = CONFIG_DIR / "test_impact_profiles.json"

def load_test_impact_profiles() -> dict[str, Any]:
    payload = load_json_object_strict_cached(PROFILE_PATH, label="Test impact profiles")
    for key, expected_type in (("languages", dict), ("confidence", dict), ("fallback_command", str)):
        value = payload.get(key)
        if not isinstance(value, expected_type) or value in ("", {}, []):
            raise ValueError(f"Test impact profiles missing mandatory non-empty field: {key}")
    return payload


def confidence_value(name: str) -> float:
    raw = (load_test_impact_profiles().get("confidence") or {}).get(name)
    if raw is None:
        raise ValueError(f"Test impact profiles missing confidence value: {name}")
    try:
        return float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid test impact confidence value for {name}: {raw!r}") from exc


def _language_entry_for_path(path_str: str) -> tuple[str, dict[str, Any]]:
    ext = Path(path_str).suffix.lower()
    language = language_for_extension(ext)
    languages = load_test_impact_profiles().get("languages") or {}
    entry = languages.get(language) if isinstance(languages, dict) else None
    if not isinstance(entry, dict):
        for candidate, payload in languages.items():
            if isinstance(payload, dict) and ext in payload.get("extensions", []):
                return str(candidate), payload
        return language, {}
    return language, entry


def is_test_path(path_str: str) -> bool:
    path_lower = str(path_str or "").lower().replace("\\", "/")
    profile = load_test_impact_profiles()
    for fragment in profile.get("ignored_path_fragments", []):
        if str(fragment).lower() in path_lower:
            return False

    filename = Path(path_lower).name
    _, entry = _language_entry_for_path(path_lower)
    if not entry:
        return False
    path_with_edges = f"/{path_lower}"
    if any(str(fragment).lower() in path_with_edges for fragment in entry.get("path_fragments", [])):
        return True
    if any(token.lower() in filename for token in entry.get("filename_contains", [])):
        return True
    if any(filename.startswith(str(prefix).lower()) for prefix in entry.get("filename_prefixes", [])):
        return True
    if any(filename.endswith(str(suffix).lower()) for suffix in entry.get("filename_suffixes", [])):
        return True
    return False


def extract_logical_base_name(path_str: str) -> str:
    filename = Path(path_str).name
    base = Path(filename).stem
    _, entry = _language_entry_for_path(path_str)
    for cleanup in entry.get("base_cleanup", []) if isinstance(entry, dict) else []:
        if not isinstance(cleanup, dict):
            continue
        pattern = str(cleanup.get("pattern", ""))
        replacement = str(cleanup.get("replacement", ""))
        if pattern:
            base = re.sub(pattern, replacement, base, flags=re.IGNORECASE)
    return base.strip("_.-")


def command_for_test(test_path: str) -> str:
    path = str(test_path or "").replace("\\", "/")
    _, entry = _language_entry_for_path(path)
    template = str(entry.get("command") or load_test_impact_profiles()["fallback_command"])
    test_dir = str(Path(path).parent).replace("\\", "/")
    if test_dir == ".":
        test_dir = "."
    class_name = Path(path).stem
    return template.format(test_path=path, test_dir=test_dir, class_name=class_name)
