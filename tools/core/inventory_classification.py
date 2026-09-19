from __future__ import annotations

from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any


INVENTORY_DISPOSITIONS = (
    "configured_manifest",
    "configured_configuration",
    "analysis_source",
    "observed_non_analysis_language",
    "known_non_source_template",
    "runtime_state",
    "unclassified",
)


def matches_marker(filename: str, patterns: list[str]) -> bool:
    lowered = filename.lower()
    return any(fnmatchcase(lowered, str(pattern).lower()) for pattern in patterns)


def is_analysis_source_file(
    path: Path,
    *,
    skip: set[str],
    source_extensions: set[str],
    compound_suffixes: set[str],
    template_extensions: set[str],
) -> bool:
    if any(part.lower() in skip for part in path.parts):
        return False
    suffixes = [suffix.lower() for suffix in path.suffixes]
    if not suffixes:
        return False
    compound = "".join(suffixes[-2:]) if len(suffixes) >= 2 else suffixes[-1]
    return (
        compound not in compound_suffixes
        and suffixes[-1] not in template_extensions
        and suffixes[-1] in source_extensions
    )


def inventory_classification_policy(policy: dict[str, Any]) -> dict[str, Any]:
    payload = policy.get("inventory_classification")
    return payload if isinstance(payload, dict) else {}


def new_inventory_classification_state() -> dict[str, Any]:
    return {
        "disposition_counts": {},
        "unclassified_extension_counts": {},
        "unclassified_examples": [],
    }


def _runtime_state_file(path: Path, classification_policy: dict[str, Any]) -> bool:
    runtime_policy = classification_policy.get("runtime_state")
    if not isinstance(runtime_policy, dict):
        return False
    lowered_name = path.name.lower()
    extensions = {
        str(value).lower()
        for value in runtime_policy.get("file_extensions", [])
        if str(value).startswith(".")
    }
    compound_suffixes = {
        str(value).lower()
        for value in runtime_policy.get("compound_suffixes", [])
        if str(value).startswith(".")
    }
    name_patterns = [
        str(value).lower()
        for value in runtime_policy.get("file_name_patterns", [])
        if str(value).strip()
    ]
    return (
        path.suffix.lower() in extensions
        or any(lowered_name.endswith(value) for value in compound_suffixes)
        or any(fnmatchcase(lowered_name, pattern) for pattern in name_patterns)
    )


def _inventory_disposition(
    path: Path,
    *,
    classification_policy: dict[str, Any],
    config_patterns: list[str],
    manifest_patterns: dict[str, list[str]],
    language_by_extension: dict[str, str],
    skip: set[str],
    source_extensions: set[str],
    compound_suffixes: set[str],
    template_extensions: set[str],
) -> str:
    if any(
        matches_marker(path.name, patterns)
        for patterns in manifest_patterns.values()
    ):
        return "configured_manifest"
    if matches_marker(path.name, config_patterns):
        return "configured_configuration"
    language = language_by_extension.get(path.suffix.lower())
    if language and is_analysis_source_file(
        path,
        skip=skip,
        source_extensions=source_extensions,
        compound_suffixes=compound_suffixes,
        template_extensions=template_extensions,
    ):
        return "analysis_source"
    if language:
        return "observed_non_analysis_language"
    if path.suffix.lower() in template_extensions:
        return "known_non_source_template"
    if _runtime_state_file(path, classification_policy):
        return "runtime_state"
    return "unclassified"


def record_inventory_classification(
    state: dict[str, Any],
    path: Path,
    relative_path: str,
    *,
    policy: dict[str, Any],
    config_patterns: list[str],
    manifest_patterns: dict[str, list[str]],
    language_by_extension: dict[str, str],
    skip: set[str],
    source_extensions: set[str],
    compound_suffixes: set[str],
    template_extensions: set[str],
) -> None:
    classification_policy = inventory_classification_policy(policy)
    disposition = _inventory_disposition(
        path,
        classification_policy=classification_policy,
        config_patterns=config_patterns,
        manifest_patterns=manifest_patterns,
        language_by_extension=language_by_extension,
        skip=skip,
        source_extensions=source_extensions,
        compound_suffixes=compound_suffixes,
        template_extensions=template_extensions,
    )
    counts = state.setdefault("disposition_counts", {})
    counts[disposition] = int(counts.get(disposition, 0)) + 1
    if disposition != "unclassified":
        return
    extension = path.suffix.lower() or "<none>"
    extension_counts = state.setdefault("unclassified_extension_counts", {})
    extension_counts[extension] = int(extension_counts.get(extension, 0)) + 1
    try:
        example_limit = max(
            0,
            int(classification_policy.get("unclassified_example_limit", 0) or 0),
        )
    except (TypeError, ValueError):
        example_limit = 0
    examples = state.setdefault("unclassified_examples", [])
    examples.append(relative_path)
    examples.sort()
    del examples[example_limit:]


def finalize_inventory_classification(
    state: dict[str, Any],
    *,
    policy: dict[str, Any],
    truncated: bool,
) -> dict[str, Any]:
    classification_policy = inventory_classification_policy(policy)
    disposition_counts = {
        disposition: int((state.get("disposition_counts") or {}).get(disposition, 0))
        for disposition in INVENTORY_DISPOSITIONS
        if int((state.get("disposition_counts") or {}).get(disposition, 0)) > 0
    }
    observed_file_count = sum(disposition_counts.values())
    unclassified_file_count = int(disposition_counts.get("unclassified", 0))
    examples = sorted(str(value) for value in state.get("unclassified_examples", []))
    try:
        example_limit = max(
            0,
            int(classification_policy.get("unclassified_example_limit", 0) or 0),
        )
    except (TypeError, ValueError):
        example_limit = 0
    return {
        "status": "partial" if truncated else "complete",
        "observed_file_count": observed_file_count,
        "classified_file_count": observed_file_count - unclassified_file_count,
        "unclassified_file_count": unclassified_file_count,
        "disposition_counts": disposition_counts,
        "unclassified_extension_counts": dict(
            sorted(
                (str(extension), int(count))
                for extension, count in (state.get("unclassified_extension_counts") or {}).items()
            )
        ),
        "unclassified_examples": examples,
        "unclassified_example_limit": example_limit,
        "unclassified_examples_omitted": max(0, unclassified_file_count - len(examples)),
        "unclassified_semantics": classification_policy.get("unclassified_semantics"),
        "excluded_file_count": None,
        "excluded_file_count_status": classification_policy.get(
            "excluded_file_count_status",
            "unavailable_pruned_not_walked",
        ),
        "decision_effect": classification_policy.get("decision_effect", "observability_only"),
    }
