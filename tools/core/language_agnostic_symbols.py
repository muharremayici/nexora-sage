from __future__ import annotations

from typing import Any

from tools.core.config import CONFIG_DIR
from tools.core.json_io import load_json_object_strict_cached


CONFIG_PATH = CONFIG_DIR / "language_agnostic_symbols.json"
DEFAULT_CANONICAL_TYPE = "metadata"


def load_symbol_schema() -> dict[str, Any]:
    return load_json_object_strict_cached(CONFIG_PATH, label="Language-agnostic symbol schema")


def canonical_symbol_types() -> set[str]:
    data = load_symbol_schema()
    return {str(item) for item in data.get("canonical_types", []) if str(item).strip()}


def canonical_symbol_type(raw_type: str, language: str = "typescript") -> str:
    data = load_symbol_schema()
    canonical = canonical_symbol_types()
    language_key = str(language or "typescript").lower()
    raw_key = str(raw_type or "").strip()
    type_map = data.get("raw_type_map", {}) if isinstance(data.get("raw_type_map"), dict) else {}
    mapped = ""
    if isinstance(type_map.get(language_key), dict):
        mapped = str(type_map[language_key].get(raw_key) or "").strip()
    if not mapped:
        for mapping in type_map.values():
            if isinstance(mapping, dict) and raw_key in mapping:
                mapped = str(mapping.get(raw_key) or "").strip()
                break
    if mapped in canonical:
        return mapped
    lowered = raw_key.lower()
    return lowered if lowered in canonical else DEFAULT_CANONICAL_TYPE


def normalization_profile(name: str = "typescript_react_v1") -> dict[str, Any]:
    data = load_symbol_schema()
    profiles = data.get("normalization_profiles", {}) if isinstance(data.get("normalization_profiles"), dict) else {}
    profile = profiles.get(name, {})
    return profile if isinstance(profile, dict) else {}


def normalization_profile_name(language: str) -> str:
    data = load_symbol_schema()
    defaults = data.get("default_profiles", {}) if isinstance(data.get("default_profiles"), dict) else {}
    return str(defaults.get(str(language or "").lower()) or "").strip()


def normalization_profile_for_language(language: str) -> tuple[str, dict[str, Any]]:
    name = normalization_profile_name(language)
    return name, normalization_profile(name) if name else {}
