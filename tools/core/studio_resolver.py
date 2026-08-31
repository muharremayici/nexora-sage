from __future__ import annotations

import re
from collections import Counter
from typing import Dict, Tuple

from tools.core.config import DOCTRINE, DYNAMIC_CONFIG
from tools.core.projects_registry import MODULE_TO_STUDIO, PROJECT_STUDIO_REGISTRY, STUDIO_TO_MODULE
from tools.core.fractal_policy import get_module_container
from tools.core.doctrine_contract import require_doctrine_path

PLATFORM_CORE_STUDIO = "platform_core"
CAPABILITY_TO_STUDIO = DOCTRINE.get("capability_to_studio_map", {})
STUDIO_KEYWORDS = DOCTRINE.get("studio_definitions", {})
MAIN_FIRST_DOMAIN_OVERRIDES = DOCTRINE.get("main_first_domain_overrides", {})
GENERIC_PATH_HINTS = DOCTRINE.get("generic_path_hints", [])


def detect_capability(name: str, file_path: str, capability_keywords: Dict[str, list[str]]) -> Tuple[str, int]:
    low = (str(name or "") + " " + str(file_path or "")).lower()
    best = (PLATFORM_CORE_STUDIO, 0)
    for capability, keywords in capability_keywords.items():
        matches = sum(1 for keyword in keywords if keyword in low)
        if matches > best[1]:
            best = (capability, matches)
    return best


def studio_from_keywords(text: str) -> Tuple[str, int]:
    low = str(text or "").lower()
    best = (PLATFORM_CORE_STUDIO, 0)
    for studio, keywords in STUDIO_KEYWORDS.items():
        matches = sum(1 for keyword in keywords if keyword in low)
        if matches > best[1]:
            best = (studio, matches)
    return best


def studio_from_project_path(project: str, file_path: str) -> Tuple[str, int]:
    low = str(file_path or "").lower().replace("\\", "/")
    rules = PROJECT_STUDIO_REGISTRY.get(project, [])
    for pattern, studio in rules:
        if re.search(pattern, low):
            return studio, 3
    return PLATFORM_CORE_STUDIO, 0


def studio_from_generic_path(file_path: str) -> Tuple[str, int]:
    normalized = str(file_path or "").lower().replace("\\", "/").strip("/")
    if not normalized:
        return PLATFORM_CORE_STUDIO, 0

    structural_platform_families = require_doctrine_path("platform_structural_families", expected_type=list)
    if normalized.startswith("src/"):
        stripped = normalized[4:]
        if any(stripped.startswith(prefix) for prefix in structural_platform_families):
            return PLATFORM_CORE_STUDIO, 2
    if any(normalized.startswith(prefix) for prefix in structural_platform_families):
        return PLATFORM_CORE_STUDIO, 2
    if "service-worker" in normalized:
        return PLATFORM_CORE_STUDIO, 2

    for pattern, studio in GENERIC_PATH_HINTS:
        if re.search(pattern, normalized):
            return studio, 3

    module_root = get_module_container().lower()
    module_prefix = f"{module_root}/" if module_root and module_root != "." else ""

    if module_prefix and normalized.startswith(module_prefix):
        remainder = normalized[len(module_prefix):]
        parts = remainder.split("/", 1)
        if parts:
            module_name = parts[0]
            mapped = MODULE_TO_STUDIO.get(module_name)
            if mapped:
                return mapped, 4

    best = studio_from_keywords(normalized)
    if best[1] > 0:
        return best

    return PLATFORM_CORE_STUDIO, 0



def normalize_target_studio(studio: str | None) -> str:
    if studio in STUDIO_TO_MODULE or studio == PLATFORM_CORE_STUDIO:
        return str(studio)
    return PLATFORM_CORE_STUDIO


def resolve_target_studio_context(
    project: str,
    name: str,
    occurrence: Dict,
    capability_keywords: Dict[str, list[str]],
) -> Dict[str, object]:
    path = str(occurrence.get("file", ""))
    meta = occurrence.get("meta", {}) or {}

    registry_studio, registry_hits = studio_from_project_path(project, path)
    generic_studio, generic_hits = studio_from_generic_path(path)
    path_studio, path_hits = studio_from_keywords(path)
    meta_studio, meta_hits = studio_from_keywords(str(meta.get("studio", "")))
    capability, capability_hits = detect_capability(name, path, capability_keywords)
    capability_studio = CAPABILITY_TO_STUDIO.get(capability, PLATFORM_CORE_STUDIO)

    votes = Counter()
    votes[generic_studio] += generic_hits * 4
    votes[registry_studio] += registry_hits * 2
    votes[path_studio] += path_hits * 2
    votes[meta_studio] += meta_hits * 2
    votes[capability_studio] += capability_hits

    target_studio, score = votes.most_common(1)[0]
    total_possible = max(
        1,
        generic_hits * 4 + registry_hits * 2 + max(path_hits, 1) * 2 + max(meta_hits, 1) * 2 + max(capability_hits, 1),
    )
    confidence = min(1.0, score / total_possible)
    target_studio = normalize_target_studio(target_studio)

    return {
        "source_studio": generic_studio if generic_hits >= path_hits else path_studio,
        "target_studio": target_studio,
        "capability": capability,
        "confidence": round(confidence, 2),
    }


def domain_bucket(name: str, path: str) -> str:
    low = (str(name or "") + " " + str(path or "")).lower()
    for bucket, keywords in MAIN_FIRST_DOMAIN_OVERRIDES.items():
        if any(keyword in low for keyword in keywords):
            return bucket
    return "general"


def studio_for_main_relative_path(path: str) -> str | None:
    normalized = str(path or "").replace("\\", "/").strip("/")
    if not normalized:
        return None

    module_root = get_module_container()
    module_prefix = f"{module_root}/" if module_root and module_root != "." else ""

    if module_prefix and normalized.startswith(module_prefix):
        remainder = normalized[len(module_prefix):]
        module_name = remainder.split("/", 1)[0]
        return MODULE_TO_STUDIO.get(module_name)

    if normalized.startswith("platform/"):
        return PLATFORM_CORE_STUDIO

    return None


def get_module_name(file_path: str) -> str:
    """Extract module name or top-level category from a file path (Universal)."""
    normalized = str(file_path or "").replace("\\", "/").strip("/")
    if not normalized:
        return "@root"

    module_root = get_module_container()
    module_prefix = f"{module_root}/" if module_root and module_root != "." else ""

    if module_prefix and normalized.startswith(module_prefix):
        remainder = normalized[len(module_prefix):]
        parts = remainder.split("/")
        if parts:
            return parts[0]

    # Global/Platform categorization
    parts = normalized.split("/")
    if len(parts) >= 1:
        # Check if the first part is a known generic folder
        return f"@{parts[0]}"

    return "@root"
