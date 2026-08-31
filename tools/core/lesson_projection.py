from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from tools.core.config import CODE_MAPS_DIR
from tools.core.source_layer_classifier import classify_source_layer
from tools.core.path_identity import strip_current_directory_prefix


_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")


def resolve_lesson_projection_scope(
    *,
    changed_files: list[str] | None,
    failure_families: list[str] | None,
    active_package: dict[str, Any],
    work_items: list[dict[str, Any]],
    current_wave: object,
    contract: dict[str, Any],
) -> dict[str, Any]:
    resolution = contract.get("scope_resolution") if isinstance(contract.get("scope_resolution"), dict) else {}
    required_resolution_fields = {
        "explicit_arguments_precede",
        "active_package_status",
        "inactive_work_item_statuses",
        "ready_status",
        "unavailable_status",
        "explicit_source",
        "default_source",
        "unavailable_projection_status",
        "invalid_explicit_projection_status",
        "reject_unsafe_paths",
        "work_item_optional_release_scope_modes",
    }
    if not required_resolution_fields.issubset(resolution) or resolution.get("explicit_arguments_precede") is not True:
        return {
            "ready": False,
            "status": str(resolution.get("unavailable_status") or "contract_error"),
            "source": str(resolution.get("default_source") or "not_available"),
            "reason": "scope_resolution_contract_missing_or_invalid",
            "projection_status": str(resolution.get("unavailable_projection_status") or "FAIL"),
            "changed_files": [],
            "failure_families": [],
        }
    explicit_files = [str(item).replace("\\", "/").strip() for item in changed_files or [] if str(item).strip()]
    explicit_families = [str(item).strip() for item in failure_families or [] if str(item).strip()]
    if explicit_files or explicit_families:
        unsafe_explicit_paths = [
            path for path in explicit_files if Path(path).is_absolute() or ".." in Path(path).parts
        ]
        if resolution["reject_unsafe_paths"] is True and unsafe_explicit_paths:
            return {
                "ready": False,
                "status": str(resolution["unavailable_status"]),
                "source": str(resolution["explicit_source"]),
                "reason": "explicit_changed_files_unsafe",
                "projection_status": str(resolution["invalid_explicit_projection_status"]),
                "changed_files": [],
                "failure_families": [],
            }
        return {
            "ready": True,
            "status": str(resolution["ready_status"]),
            "source": str(resolution["explicit_source"]),
            "reason": "explicit_scope_has_precedence",
            "projection_status": "not_applicable",
            "changed_files": sorted(set(explicit_files)),
            "failure_families": sorted(set(explicit_families)),
        }

    package_ids = [str(item) for item in active_package.get("work_item_ids", []) if str(item)]
    release_scope = active_package.get("release_scope") if isinstance(active_package.get("release_scope"), dict) else {}
    work_item_optional_modes = {
        str(item)
        for item in resolution.get("work_item_optional_release_scope_modes", [])
        if str(item)
    }
    work_items_optional = str(release_scope.get("mode") or "") in work_item_optional_modes
    item_by_id = {str(row.get("id") or ""): row for row in work_items if isinstance(row, dict)}
    affected = [str(item).replace("\\", "/").strip() for item in active_package.get("affected_contracts", []) if str(item).strip()]
    unsafe_paths = [path for path in affected if Path(path).is_absolute() or ".." in Path(path).parts]
    reasons = []
    if active_package.get("status") != resolution["active_package_status"]:
        reasons.append("active_package_not_in_progress")
    if not current_wave or active_package.get("execution_wave") != current_wave:
        reasons.append("active_package_wave_not_current")
    if (not package_ids and not work_items_optional) or any(item not in item_by_id for item in package_ids):
        reasons.append("active_package_work_items_missing")
    elif any(str(item_by_id[item].get("status") or "") in set(map(str, resolution["inactive_work_item_statuses"])) for item in package_ids):
        reasons.append("active_package_work_item_closed")
    if not affected:
        reasons.append("active_package_affected_contracts_missing")
    if unsafe_paths:
        reasons.append("active_package_affected_contracts_unsafe")
    if reasons:
        return {
            "ready": False,
            "status": str(resolution["unavailable_status"]),
            "source": str(resolution["default_source"]),
            "reason": ",".join(reasons),
            "projection_status": str(resolution["unavailable_projection_status"]),
            "changed_files": [],
            "failure_families": [],
        }
    return {
        "ready": True,
        "status": str(resolution["ready_status"]),
        "source": str(resolution["default_source"]),
        "reason": "validated_active_package_scope",
        "projection_status": "not_applicable",
        "changed_files": sorted(set(affected)),
        "failure_families": [],
    }


def normalize_signal(value: object) -> str:
    return "_".join(part for part in _TOKEN_SPLIT.split(str(value or "").lower()) if part)


def _stem_signals(stem: str) -> set[str]:
    values = {normalize_signal(stem)}
    for prefix in ("validate_", "generate_", "run_", "update_"):
        if stem.startswith(prefix):
            values.add(normalize_signal(stem[len(prefix) :]))
    return {value for value in values if value}


def _file_signals(relative_path: str, contract: dict[str, Any]) -> tuple[str, set[str], set[str]]:
    normalized_path = strip_current_directory_prefix(Path(relative_path.replace("\\", "/")).as_posix())
    layer, _reason = classify_source_layer(CODE_MAPS_DIR / normalized_path)
    stem = Path(normalized_path).stem
    strong_signals = {
        normalize_signal(normalized_path),
        normalize_signal(layer),
    }
    strong_signals.update(_stem_signals(stem))
    weak_signals = {normalize_signal(part) for part in Path(normalized_path).parts if part}
    aliases = contract.get("source_layer_signal_aliases", {})
    if isinstance(aliases, dict):
        weak_signals.update(normalize_signal(item) for item in aliases.get(layer, []) if str(item).strip())
    for rule in contract.get("path_signal_rules", []):
        if not isinstance(rule, dict):
            continue
        prefix = str(rule.get("path_prefix") or "").replace("\\", "/")
        if prefix and normalized_path.startswith(prefix):
            weak_signals.update(normalize_signal(item) for item in rule.get("signals", []) if str(item).strip())
    return layer, {item for item in strong_signals if item}, {item for item in weak_signals if item}


def _lesson_signals(lesson: dict[str, Any], fields: list[str]) -> set[str]:
    signals: set[str] = set()
    for field in fields:
        value = lesson.get(field)
        values = value if isinstance(value, list) else [value]
        signals.update(normalize_signal(item) for item in values if str(item or "").strip())
    return signals


def project_impacted_lessons(
    lessons: list[dict[str, Any]],
    *,
    changed_files: list[str],
    failure_families: list[str],
    contract: dict[str, Any],
) -> dict[str, Any]:
    fields = [str(item) for item in contract.get("lesson_fields", []) if str(item).strip()]
    maximum = int(contract.get("maximum_lessons", 0) or 0)
    priorities = [str(item) for item in contract.get("priority_order", []) if str(item).strip()]
    priority_rank = {value: index for index, value in enumerate(priorities)}
    contract_errors = []
    if contract.get("matching_mode") != "normalized_exact_signal_intersection":
        contract_errors.append("unsupported_matching_mode")
    if not fields:
        contract_errors.append("missing_lesson_fields")
    if maximum < 1:
        contract_errors.append("invalid_maximum_lessons")
    if contract.get("selection_requires_strong_signal") is not True:
        contract_errors.append("strong_signal_selection_not_required")
    if contract.get("failure_family_mode") != "restrict_when_provided":
        contract_errors.append("unsupported_failure_family_mode")
    if contract.get("unknown_failure_family_behavior") != "fail_closed":
        contract_errors.append("unsupported_unknown_failure_family_behavior")

    file_rows = []
    failure_signals = {normalize_signal(item) for item in failure_families if str(item).strip()}
    aggregate_strong_signals = set(failure_signals)
    aggregate_weak_signals: set[str] = set()
    for relative_path in sorted(set(str(item) for item in changed_files if str(item).strip())):
        layer, strong_signals, weak_signals = _file_signals(relative_path, contract)
        aggregate_strong_signals.update(strong_signals)
        aggregate_weak_signals.update(weak_signals)
        file_rows.append(
            {
                "path": relative_path.replace("\\", "/"),
                "source_layer": layer,
                "strong_signals": sorted(strong_signals),
                "context_signals": sorted(weak_signals),
            }
        )

    matches = []
    matched_file_paths: set[str] = set()
    for lesson in lessons:
        if not isinstance(lesson, dict):
            continue
        lesson_signals = _lesson_signals(lesson, fields)
        family_intersection = sorted(failure_signals & lesson_signals)
        if failure_signals and not family_intersection:
            continue
        strong_intersection = sorted(aggregate_strong_signals & lesson_signals)
        context_intersection = sorted(aggregate_weak_signals & lesson_signals)
        if not strong_intersection:
            continue
        file_matches = (
            [row["path"] for row in file_rows]
            if family_intersection
            else [row["path"] for row in file_rows if set(row["strong_signals"]) & lesson_signals]
        )
        matched_file_paths.update(file_matches)
        matches.append(
            {
                "id": lesson.get("id"),
                "priority": lesson.get("priority"),
                "status": lesson.get("status"),
                "owner_surface": lesson.get("owner_surface"),
                "risk_class": lesson.get("risk_class"),
                "matched_strong_signals": strong_intersection,
                "matched_context_signals": context_intersection,
                "matched_files": file_matches,
                "lesson": lesson.get("lesson"),
            }
        )
    matches.sort(key=lambda row: (priority_rank.get(str(row.get("priority")), len(priority_rank)), str(row.get("id"))))
    shown = matches[:maximum] if maximum > 0 else []
    unknown_source_layer_files = [row["path"] for row in file_rows if row["source_layer"] == "unknown_or_review"]
    unmatched_failure_families = sorted(
        signal
        for signal in failure_signals
        if not any(signal in _lesson_signals(lesson, fields) for lesson in lessons if isinstance(lesson, dict))
    )
    status = (
        "FAIL"
        if contract_errors or unmatched_failure_families
        else "NO_INPUT"
        if not file_rows and not failure_signals
        else "NEEDS_CLASSIFICATION"
        if unknown_source_layer_files
        else "PASS"
    )
    return {
        "meta": {"kind": "semantic_diff_impacted_lesson_projection", "version": "v1"},
        "summary": {
            "status": status,
            "changed_files": len(file_rows),
            "failure_families": len([item for item in failure_families if str(item).strip()]),
            "matched_lessons": len(matches),
            "shown_lessons": len(shown),
            "omitted_lessons": max(0, len(matches) - len(shown)),
            "unmatched_changed_files": len([row for row in file_rows if row["path"] not in matched_file_paths]),
            "unknown_source_layer_files": len(unknown_source_layer_files),
            "unmatched_failure_families": len(unmatched_failure_families),
            "contract_errors": contract_errors,
        },
        "changed_files": file_rows,
        "failure_families": sorted(set(str(item) for item in failure_families if str(item).strip())),
        "unmatched_failure_families": unmatched_failure_families,
        "lessons": shown,
        "unmatched_changed_files": [row["path"] for row in file_rows if row["path"] not in matched_file_paths],
        "unknown_source_layer_files": unknown_source_layer_files,
        "limits": {
            "matching_mode": contract.get("matching_mode"),
            "maximum_lessons": maximum,
            "selection_requires_strong_signal": contract.get("selection_requires_strong_signal"),
            "failure_family_mode": contract.get("failure_family_mode"),
            "unknown_failure_family_behavior": contract.get("unknown_failure_family_behavior"),
            "semantic_inference": False,
            "full_registry_replay": False,
        },
    }
