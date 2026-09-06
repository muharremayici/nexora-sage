from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.config import CONFIG_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file
from tools.core.manual_audit_contract_identity import contract_fingerprint


CHECKLIST_PATH = CONFIG_DIR / "manual_adversarial_audit_checklist.json"
PROGRESS_PATH = CONFIG_DIR / "manual_adversarial_audit_progress.json"
LESSONS_PATH = CONFIG_DIR / "audit_lesson_registry.json"
SOURCE_LAYER_TAXONOMY_PATH = CONFIG_DIR / "source_layer_taxonomy.json"
SYSTEM_SPINE_REGISTRY_PATH = CONFIG_DIR / "system_spine_registry.json"
RELEASE_PROOF_STEPS_CONTRACT_PATH = CONFIG_DIR / "release_proof_steps_contract.json"
PIPELINE_STEP_REGISTRY_PATH = RAW_DIR / "pipeline_step_registry.json"
RAW_OUTPUT_PATH = RAW_DIR / "manual_adversarial_audit_progress_validation.json"
REPORT_OUTPUT_PATH = REPORTS_DIR / "manual_adversarial_audit_progress_validation.md"

def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check(name: str, passed: bool, details: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, dict)]


def build_validation() -> dict[str, Any]:
    checklist = load_json_file(CHECKLIST_PATH, {})
    progress = load_json_file(PROGRESS_PATH, {})
    lessons = load_json_file(LESSONS_PATH, {})
    source_taxonomy = load_json_file(SOURCE_LAYER_TAXONOMY_PATH, {})
    system_spine_registry = load_json_file(SYSTEM_SPINE_REGISTRY_PATH, {})
    release_proof_steps_contract = load_json_file(RELEASE_PROOF_STEPS_CONTRACT_PATH, {})
    pipeline_step_registry = load_json_file(PIPELINE_STEP_REGISTRY_PATH, {})

    checklist_layers = _rows(checklist.get("audit_layers")) if isinstance(checklist, dict) else []
    source_policy = checklist.get("source_layer_coverage_policy", {}) if isinstance(checklist, dict) else {}
    source_policy_rows = _rows(source_policy.get("layers")) if isinstance(source_policy, dict) else []
    source_taxonomy_rows = _rows(source_taxonomy.get("layers")) if isinstance(source_taxonomy, dict) else []
    source_taxonomy_order = [str(row.get("id") or "") for row in source_taxonomy_rows if row.get("id")]
    validation_config = checklist.get("validation", {}) if isinstance(checklist, dict) else {}
    allowed_layer_statuses = {
        str(status)
        for status in validation_config.get("allowed_progress_statuses", [])
        if str(status).strip()
    }
    allowed_coverage_statuses = {
        str(status)
        for status in validation_config.get("allowed_source_layer_coverage_statuses", [])
        if str(status).strip()
    }
    source_layer_completion_statuses = {
        str(status)
        for status in validation_config.get("source_layer_completion_statuses", [])
        if str(status).strip()
    }
    checklist_layer_ids = {str(row.get("id") or "") for row in checklist_layers if row.get("id")}
    source_policy_by_id = {
        str(row.get("source_layer_id") or ""): row
        for row in source_policy_rows
        if row.get("source_layer_id")
    }
    taxonomy_layer_ids = {
        str(row.get("id") or "")
        for row in source_taxonomy_rows
        if row.get("id")
    }
    progress_layers = _rows(progress.get("layer_statuses")) if isinstance(progress, dict) else []
    coverage_progress = (
        progress.get("source_layer_coverage_progress", {})
        if isinstance(progress, dict) and isinstance(progress.get("source_layer_coverage_progress"), dict)
        else {}
    )
    coverage_rows = _rows(coverage_progress.get("layers"))
    fresh_audit_start_policy = (
        coverage_progress.get("fresh_audit_start_policy", {})
        if isinstance(coverage_progress.get("fresh_audit_start_policy"), dict)
        else {}
    )
    spot_check_cursor = (
        coverage_progress.get("spot_check_cursor", {})
        if isinstance(coverage_progress.get("spot_check_cursor"), dict)
        else {}
    )
    reset_policy = (
        coverage_progress.get("reset_policy", {})
        if isinstance(coverage_progress.get("reset_policy"), dict)
        else {}
    )
    micro_walkthrough_progress = (
        coverage_progress.get("micro_walkthrough_progress", {})
        if isinstance(coverage_progress.get("micro_walkthrough_progress"), dict)
        else {}
    )
    progress_layer_ids = [str(row.get("layer_id") or "") for row in progress_layers]
    coverage_layer_ids = [str(row.get("source_layer_id") or "") for row in coverage_rows]
    lesson_ids = {
        str(row.get("id") or "")
        for row in _rows(lessons.get("lessons")) if row.get("id")
    } if isinstance(lessons, dict) else set()

    current_layer_id = str(progress.get("current_layer_id") or "") if isinstance(progress, dict) else ""
    return_point_layer_id = str(progress.get("return_point_layer_id") or "") if isinstance(progress, dict) else ""
    statuses = {str(row.get("status") or "") for row in progress_layers}
    in_progress_layers = [
        str(row.get("layer_id") or "")
        for row in progress_layers
        if row.get("status") == "in_progress"
    ]

    unknown_progress_layers = sorted(
        layer_id for layer_id in progress_layer_ids if layer_id not in checklist_layer_ids
    )
    derived_pending_layers = sorted(
        layer_id for layer_id in checklist_layer_ids if layer_id not in set(progress_layer_ids)
    )
    duplicate_progress_layers = sorted(
        layer_id for layer_id in set(progress_layer_ids)
        if layer_id and progress_layer_ids.count(layer_id) > 1
    )
    duplicate_coverage_layers = sorted(
        layer_id for layer_id in set(coverage_layer_ids)
        if layer_id and coverage_layer_ids.count(layer_id) > 1
    )
    coverage_id_set = {item for item in coverage_layer_ids if item}
    missing_coverage_layers = sorted(taxonomy_layer_ids - coverage_id_set)
    extra_coverage_layers = sorted(coverage_id_set - taxonomy_layer_ids)
    invalid_statuses = sorted(status for status in statuses if status not in allowed_layer_statuses)
    invalid_coverage_statuses = sorted(
        str(row.get("status") or "")
        for row in coverage_rows
        if str(row.get("status") or "") not in allowed_coverage_statuses
    )
    current_source_layer_id = str(spot_check_cursor.get("current_source_layer_id") or "")
    cursor_status = str(spot_check_cursor.get("status") or "")
    cursor_index = spot_check_cursor.get("current_source_layer_index")
    completed_source_layer_ids = [
        str(item)
        for item in spot_check_cursor.get("completed_source_layer_ids", [])
        if str(item).strip()
    ] if isinstance(spot_check_cursor.get("completed_source_layer_ids"), list) else []
    next_source_layer_ids = [
        str(item)
        for item in spot_check_cursor.get("next_source_layer_ids", [])
        if str(item).strip()
    ] if isinstance(spot_check_cursor.get("next_source_layer_ids"), list) else []
    expected_index = (
        source_taxonomy_order.index(current_source_layer_id)
        if current_source_layer_id in source_taxonomy_order
        else None
    )
    expected_next_source_layers = (
        source_taxonomy_order[(expected_index or 0) + 1 : (expected_index or 0) + 3]
        if expected_index is not None
        else []
    )
    def _completed_layer_is_invalid(layer_id: str) -> bool:
        if layer_id not in source_taxonomy_order:
            return True
        if expected_index is None:
            return False
        layer_index = source_taxonomy_order.index(layer_id)
        if cursor_status == "complete":
            return layer_index > expected_index
        return layer_index >= expected_index

    invalid_completed_layers = [
        layer_id
        for layer_id in completed_source_layer_ids
        if _completed_layer_is_invalid(layer_id)
    ]
    spot_check_cursor_issues: list[dict[str, Any]] = []
    if not spot_check_cursor:
        spot_check_cursor_issues.append({"missing": "source_layer_coverage_progress.spot_check_cursor"})
    if spot_check_cursor.get("order_source") != "config/source_layer_taxonomy.json":
        spot_check_cursor_issues.append({"invalid_order_source": spot_check_cursor.get("order_source")})
    if current_source_layer_id not in source_taxonomy_order:
        spot_check_cursor_issues.append({"unknown_current_source_layer_id": current_source_layer_id})
    if expected_index is None or cursor_index != expected_index:
        spot_check_cursor_issues.append(
            {
                "current_source_layer_id": current_source_layer_id,
                "current_source_layer_index": cursor_index,
                "expected_index": expected_index,
            }
        )
    if next_source_layer_ids != expected_next_source_layers:
        spot_check_cursor_issues.append(
            {
                "next_source_layer_ids": next_source_layer_ids,
                "expected_next_source_layer_ids": expected_next_source_layers,
            }
        )
    if invalid_completed_layers:
        spot_check_cursor_issues.append({"invalid_completed_source_layer_ids": invalid_completed_layers})
    if not str(spot_check_cursor.get("selection_rule") or "").strip():
        spot_check_cursor_issues.append({"missing": "selection_rule"})
    reset_policy_issues: list[dict[str, Any]] = []
    resettable = [str(item) for item in reset_policy.get("resettable", [])] if isinstance(reset_policy.get("resettable"), list) else []
    preserve = [str(item) for item in reset_policy.get("preserve", [])] if isinstance(reset_policy.get("preserve"), list) else []
    required_preserve_signals = [
        "config/audit_lesson_registry.json",
        "config/sage_work_item_registry.json",
        "config/roadmap_phase_registry.json",
        "config/release_identity.json",
        "human approval",
    ]
    for signal in required_preserve_signals:
        if not any(signal in item for item in preserve):
            reset_policy_issues.append({"missing_preserve_signal": signal})
    if not resettable:
        reset_policy_issues.append({"missing": "resettable"})
    if not str(reset_policy.get("rule") or "").strip():
        reset_policy_issues.append({"missing": "rule"})
    fresh_audit_start_issues: list[dict[str, Any]] = []
    fresh_order = _rows(fresh_audit_start_policy.get("order"))
    fresh_order_by_scope = {str(row.get("scope") or ""): row for row in fresh_order if row.get("scope")}
    pipeline_registry_steps = _rows(pipeline_step_registry.get("steps"))
    pipeline_registry_available = PIPELINE_STEP_REGISTRY_PATH.exists() and bool(pipeline_registry_steps)
    declared_pipeline_count = fresh_order_by_scope.get("pipeline_steps", {}).get("expected_count")
    actual_counts = {
        "macro_audit_layers": len(checklist_layers),
        "source_layers": len(source_taxonomy_order),
        "system_spine_nodes": len(_rows(system_spine_registry.get("spine_nodes"))),
        "pipeline_steps": len(pipeline_registry_steps) if pipeline_registry_available else declared_pipeline_count,
        "release_proof_steps": len(_rows(release_proof_steps_contract.get("steps"))),
    }
    required_fresh_scopes = [
        "macro_audit_layers",
        "source_layers",
        "system_spine_nodes",
        "pipeline_steps",
        "release_proof_steps",
        "artifact_connectivity",
    ]
    for scope in required_fresh_scopes:
        row = fresh_order_by_scope.get(scope)
        if not row:
            fresh_audit_start_issues.append({"missing_scope": scope})
            continue
        if not str(row.get("source") or "").strip():
            fresh_audit_start_issues.append({"scope": scope, "missing": "source"})
        if not str(row.get("rule") or "").strip():
            fresh_audit_start_issues.append({"scope": scope, "missing": "rule"})
        expected_count = row.get("expected_count")
        derives_count = expected_count == "derived_from_order_source"
        if scope in actual_counts and not derives_count and expected_count != actual_counts[scope]:
            fresh_audit_start_issues.append(
                {
                    "scope": scope,
                    "expected_count": expected_count,
                    "actual_count": actual_counts[scope],
                }
            )
        if scope in {"system_spine_nodes", "pipeline_steps", "release_proof_steps"}:
            if not derives_count and (not isinstance(expected_count, int) or expected_count <= 0):
                fresh_audit_start_issues.append(
                    {
                        "scope": scope,
                        "invalid_expected_count": expected_count,
                        "rule": "source-clean progress validation checks the declared audit coverage order; generated connectivity counts are owned by system_connectivity_map.",
                    }
                )
        if scope == "artifact_connectivity":
            expected_groups = [str(item) for item in row.get("expected_groups", [])] if isinstance(row.get("expected_groups"), list) else []
            required_groups = {"writers", "consumers", "writer_conflicts", "external_inputs", "unowned_consumed_artifacts"}
            missing_groups = sorted(required_groups - set(expected_groups))
            if missing_groups:
                fresh_audit_start_issues.append(
                    {
                        "scope": scope,
                        "missing_connectivity_groups": missing_groups,
                        "declared_groups": expected_groups,
                    }
                )
    if not str(fresh_audit_start_policy.get("reset_rule") or "").strip():
        fresh_audit_start_issues.append({"missing": "reset_rule"})
    pipeline_step_order = [
        str(row.get("slug") or "")
        for row in pipeline_registry_steps
        if row.get("slug")
    ]
    release_proof_step_order = [
        str(row.get("id") or "")
        for row in _rows(release_proof_steps_contract.get("steps"))
        if row.get("id")
    ]
    artifact_connectivity_order = [
        str(item)
        for item in fresh_order_by_scope.get("artifact_connectivity", {}).get("expected_groups", [])
        if str(item).strip()
    ] if isinstance(fresh_order_by_scope.get("artifact_connectivity", {}).get("expected_groups"), list) else []

    def _validate_micro_cursor(
        scope: str,
        row: dict[str, Any],
        expected_order: list[str],
        expected_source: str,
        expected_fingerprints: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        status = str(row.get("status") or "")
        current_item_id = str(row.get("current_item_id") or "")
        current_item_index = row.get("current_item_index")
        completed_item_ids = [
            str(item)
            for item in row.get("completed_item_ids", [])
            if str(item).strip()
        ] if isinstance(row.get("completed_item_ids"), list) else []
        completed_item_fingerprints = {
            str(item_id): str(fingerprint)
            for item_id, fingerprint in (row.get("completed_item_fingerprints") or {}).items()
            if str(item_id) and str(fingerprint)
        }
        next_item_ids = [
            str(item)
            for item in row.get("next_item_ids", [])
            if str(item).strip()
        ] if isinstance(row.get("next_item_ids"), list) else []
        if row.get("order_source") != expected_source:
            issues.append({"scope": scope, "invalid_order_source": row.get("order_source"), "expected": expected_source})
        if status not in {"not_started", "in_progress", "complete"}:
            issues.append({"scope": scope, "invalid_status": status})
        if not expected_order:
            if scope == "pipeline_steps" and not pipeline_registry_available:
                projected_order = [current_item_id, *next_item_ids]
                if status not in {"not_started", "in_progress", "complete"}:
                    issues.append(
                        {
                            "scope": scope,
                            "status": status,
                            "expected": ["not_started", "in_progress", "complete"],
                            "mode": "source_clean_projection_without_generated_runtime_registry",
                        }
                    )
                if not projected_order or not all(str(item).strip() for item in projected_order):
                    issues.append(
                        {
                            "scope": scope,
                            "missing_projected_cursor_items": True,
                            "mode": "source_clean_projection_without_generated_runtime_registry",
                        }
                    )
                if not isinstance(current_item_index, int) or current_item_index < 0:
                    issues.append(
                        {
                            "scope": scope,
                            "current_item_index": current_item_index,
                            "expected": "non_negative_integer",
                            "mode": "source_clean_projection_without_generated_runtime_registry",
                        }
                    )
                if status == "complete":
                    if not isinstance(declared_pipeline_count, int) or declared_pipeline_count <= 0:
                        issues.append(
                            {
                                "scope": scope,
                                "declared_pipeline_count": declared_pipeline_count,
                                "expected": "positive_integer",
                                "mode": "source_clean_projection_without_generated_runtime_registry",
                            }
                        )
                    elif len(completed_item_ids) != declared_pipeline_count:
                        issues.append(
                            {
                                "scope": scope,
                                "completed_item_count": len(completed_item_ids),
                                "expected_count": declared_pipeline_count,
                                "mode": "source_clean_projection_without_generated_runtime_registry",
                            }
                        )
                    elif current_item_index != declared_pipeline_count - 1:
                        issues.append(
                            {
                                "scope": scope,
                                "current_item_index": current_item_index,
                                "expected_index": declared_pipeline_count - 1,
                                "mode": "source_clean_projection_without_generated_runtime_registry",
                            }
                        )
                if not str(row.get("selection_rule") or "").strip():
                    issues.append({"scope": scope, "missing": "selection_rule"})
                return issues
            issues.append({"scope": scope, "empty_expected_order": True})
            return issues
        if current_item_id not in expected_order:
            issues.append({"scope": scope, "unknown_current_item_id": current_item_id})
            return issues
        expected_index = expected_order.index(current_item_id)
        if current_item_index != expected_index:
            issues.append(
                {
                    "scope": scope,
                    "current_item_id": current_item_id,
                    "current_item_index": current_item_index,
                    "expected_index": expected_index,
                }
            )
        missing_item_ids = [item for item in expected_order if item not in completed_item_ids]
        expected_current_item_id = expected_order[-1] if status == "complete" else (missing_item_ids[0] if missing_item_ids else expected_order[-1])
        if current_item_id != expected_current_item_id:
            issues.append(
                {
                    "scope": scope,
                    "current_item_id": current_item_id,
                    "expected_first_incomplete_item_id": expected_current_item_id,
                }
            )
        expected_next_item_ids = [] if status == "complete" else [item for item in missing_item_ids if item != current_item_id][:2]
        if next_item_ids != expected_next_item_ids:
            issues.append(
                {
                    "scope": scope,
                    "next_item_ids": next_item_ids,
                    "expected_next_item_ids": expected_next_item_ids,
                }
            )
        invalid_completed = [item for item in completed_item_ids if item not in expected_order]
        if invalid_completed:
            issues.append({"scope": scope, "invalid_completed_item_ids": invalid_completed})
        if status == "not_started" and completed_item_ids:
            issues.append({"scope": scope, "not_started_has_completed_items": completed_item_ids})
        if expected_fingerprints is not None:
            missing_fingerprints = [item for item in completed_item_ids if item not in completed_item_fingerprints]
            stale_fingerprints = [
                item
                for item in completed_item_ids
                if completed_item_fingerprints.get(item) != expected_fingerprints.get(item)
            ]
            extra_fingerprints = sorted(set(completed_item_fingerprints) - set(completed_item_ids))
            if missing_fingerprints:
                issues.append({"scope": scope, "completed_items_missing_fingerprints": missing_fingerprints})
            if stale_fingerprints:
                issues.append({"scope": scope, "completed_item_fingerprint_mismatches": stale_fingerprints})
            if extra_fingerprints:
                issues.append({"scope": scope, "fingerprints_without_completed_items": extra_fingerprints})
        if status == "complete" and completed_item_ids != expected_order:
            issues.append(
                {
                    "scope": scope,
                    "complete_scope_missing_items": sorted(set(expected_order) - set(completed_item_ids)),
                    "extra_completed_items": sorted(set(completed_item_ids) - set(expected_order)),
                }
            )
        if not str(row.get("selection_rule") or "").strip():
            issues.append({"scope": scope, "missing": "selection_rule"})
        return issues

    micro_walkthrough_issues: list[dict[str, Any]] = []
    micro_scope_rows = _rows(micro_walkthrough_progress.get("scopes"))
    micro_scope_by_id = {str(row.get("scope") or ""): row for row in micro_scope_rows if row.get("scope")}
    pipeline_fingerprints = {
        str(row.get("slug")): contract_fingerprint("pipeline_steps", row)
        for row in pipeline_registry_steps
        if row.get("slug")
    }
    release_proof_rows = _rows(release_proof_steps_contract.get("steps"))
    release_fingerprints = {
        str(row.get("id")): contract_fingerprint("release_proof_steps", row)
        for row in release_proof_rows
        if row.get("id")
    }
    expected_micro_scopes = {
        "pipeline_steps": ("output/.raw/pipeline_step_registry.json", pipeline_step_order, pipeline_fingerprints if pipeline_registry_available else None),
        "release_proof_steps": ("config/release_proof_steps_contract.json", release_proof_step_order, release_fingerprints),
        "artifact_connectivity": ("output/.raw/system_connectivity_map.json", artifact_connectivity_order, None),
    }
    if not micro_walkthrough_progress:
        micro_walkthrough_issues.append({"missing": "source_layer_coverage_progress.micro_walkthrough_progress"})
    elif not str(micro_walkthrough_progress.get("rule") or "").strip():
        micro_walkthrough_issues.append({"missing": "micro_walkthrough_progress.rule"})
    for scope, (expected_source, expected_order, expected_fingerprints) in expected_micro_scopes.items():
        row = micro_scope_by_id.get(scope)
        if not row:
            micro_walkthrough_issues.append({"missing_micro_scope": scope})
            continue
        micro_walkthrough_issues.extend(_validate_micro_cursor(scope, row, expected_order, expected_source, expected_fingerprints))
    extra_micro_scopes = sorted(set(micro_scope_by_id) - set(expected_micro_scopes))
    if extra_micro_scopes:
        micro_walkthrough_issues.append({"extra_micro_scopes": extra_micro_scopes})
    coverage_issues: list[dict[str, Any]] = []
    for row in coverage_rows:
        source_layer_id = str(row.get("source_layer_id") or "")
        policy_row = source_policy_by_id.get(source_layer_id, {})
        status = str(row.get("status") or "")
        if not row.get("evidence"):
            coverage_issues.append({"source_layer_id": source_layer_id, "missing_evidence": True})
        if not str(row.get("notes") or "").strip():
            coverage_issues.append({"source_layer_id": source_layer_id, "missing_notes": True})
        risk_tier = str(policy_row.get("risk_tier") or "")
        if risk_tier in {"critical", "high"} and status not in source_layer_completion_statuses:
            coverage_issues.append(
                {
                    "source_layer_id": source_layer_id,
                    "risk_tier": risk_tier,
                    "status": status,
                    "required_status": sorted(source_layer_completion_statuses),
                }
            )

    excursion_issues: list[dict[str, Any]] = []
    for row in _rows(progress.get("temporary_excursions")):
        issue: dict[str, Any] = {"id": row.get("id")}
        for field in ("from_layer_id", "to_layer_id", "return_point_layer_id"):
            if str(row.get(field) or "") not in checklist_layer_ids:
                issue.setdefault("unknown_layers", []).append({field: row.get(field)})
        missing_lessons = [
            lesson_id for lesson_id in [str(item) for item in row.get("lesson_ids", [])]
            if lesson_id not in lesson_ids
        ]
        if missing_lessons:
            issue["missing_lessons"] = missing_lessons
        if row.get("status") == "closed" and not row.get("evidence_commits"):
            issue["missing_evidence_commits"] = True
        if len(issue) > 1:
            excursion_issues.append(issue)

    checks = [
        _check(
            "progress_exists_and_has_known_kind",
            PROGRESS_PATH.exists()
            and isinstance(progress, dict)
            and progress.get("_meta", {}).get("kind") == "nexora.manual_adversarial_audit_progress",
            {"path": "config/manual_adversarial_audit_progress.json"},
        ),
        _check(
            "explicit_progress_layers_reference_known_checklist_layers",
            not unknown_progress_layers and not duplicate_progress_layers,
            {
                "unknown_progress_layers": unknown_progress_layers,
                "derived_pending_layers": derived_pending_layers,
                "duplicate_progress_layers": duplicate_progress_layers,
            },
        ),
        _check(
            "current_layer_is_declared_and_unique",
            current_layer_id in checklist_layer_ids
            and in_progress_layers == [current_layer_id],
            {"current_layer_id": current_layer_id, "in_progress_layers": in_progress_layers},
        ),
        _check(
            "return_point_is_declared",
            return_point_layer_id in checklist_layer_ids,
            {"return_point_layer_id": return_point_layer_id},
        ),
        _check(
            "layer_statuses_use_known_vocabulary",
            bool(allowed_layer_statuses) and not invalid_statuses,
            {"invalid_statuses": invalid_statuses, "allowed": sorted(allowed_layer_statuses)},
        ),
        _check(
            "temporary_excursions_reference_known_layers_and_lessons",
            not excursion_issues,
            excursion_issues,
        ),
        _check(
            "source_layer_coverage_progress_covers_taxonomy",
            bool(taxonomy_layer_ids)
            and not missing_coverage_layers
            and not extra_coverage_layers
            and not duplicate_coverage_layers,
            {
                "taxonomy_layers": len(taxonomy_layer_ids),
                "coverage_rows": len(coverage_rows),
                "missing_coverage_layers": missing_coverage_layers,
                "extra_coverage_layers": extra_coverage_layers,
                "duplicate_coverage_layers": duplicate_coverage_layers,
            },
        ),
        _check(
            "source_layer_coverage_progress_is_risk_aware",
            bool(allowed_coverage_statuses)
            and bool(source_layer_completion_statuses)
            and not invalid_coverage_statuses
            and not coverage_issues,
            {
                "invalid_coverage_statuses": invalid_coverage_statuses,
                "coverage_issues": coverage_issues,
                "allowed_coverage_statuses": sorted(allowed_coverage_statuses),
                "source_layer_completion_statuses": sorted(source_layer_completion_statuses),
            },
        ),
        _check(
            "source_layer_spot_check_cursor_is_sequential",
            not spot_check_cursor_issues,
            spot_check_cursor_issues,
        ),
        _check(
            "source_layer_reset_policy_preserves_learning_memory",
            not reset_policy_issues,
            reset_policy_issues,
        ),
        _check(
            "fresh_audit_start_policy_covers_macro_micro_spine_pipeline_and_artifacts",
            not fresh_audit_start_issues,
            fresh_audit_start_issues,
        ),
        _check(
            "micro_walkthrough_progress_tracks_pipeline_release_and_artifact_scopes",
            not micro_walkthrough_issues,
            micro_walkthrough_issues,
        ),
        _check(
            "closure_rule_is_explicit",
            bool(str(progress.get("closure_rule") or "").strip()) if isinstance(progress, dict) else False,
            {"closure_rule": progress.get("closure_rule") if isinstance(progress, dict) else ""},
        ),
    ]
    failures = [row for row in checks if not row["passed"]]
    return {
        "meta": {
            "kind": "manual_adversarial_audit_progress_validation",
            "version": "1.0.0",
            "generated_at": _utc_now(),
            "generator": "tools.validate_manual_adversarial_audit_progress",
            "source": "config/manual_adversarial_audit_progress.json",
        },
        "summary": {
            "status": "PASS" if not failures else "FAIL",
            "current_layer_id": current_layer_id,
            "return_point_layer_id": return_point_layer_id,
            "checklist_layers": len(checklist_layers),
            "explicit_progress_layers": len(progress_layers),
            "source_layer_coverage_rows": len(coverage_rows),
            "source_layer_spot_check_current": current_source_layer_id,
            "source_layer_spot_check_next": next_source_layer_ids,
            "micro_walkthrough_status": micro_walkthrough_progress.get("status") if isinstance(micro_walkthrough_progress, dict) else "",
            "micro_walkthrough_scopes": len(micro_scope_rows),
            "pipeline_step_registry_source": "generated_runtime_registry" if pipeline_registry_available else "source_clean_projection_without_generated_runtime_registry",
            "derived_pending_layers": derived_pending_layers,
            "temporary_excursions": len(_rows(progress.get("temporary_excursions"))) if isinstance(progress, dict) else 0,
            "total_checks": len(checks),
            "passed_checks": len(checks) - len(failures),
            "failed_checks": len(failures),
        },
        "checks": checks,
    }


def render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# Manual Adversarial Audit Progress Validation",
        "",
        f"- status: `{summary.get('status')}`",
        f"- current_layer_id: `{summary.get('current_layer_id')}`",
        f"- return_point_layer_id: `{summary.get('return_point_layer_id')}`",
        f"- checklist_layers: `{summary.get('checklist_layers')}`",
        f"- explicit_progress_layers: `{summary.get('explicit_progress_layers')}`",
        f"- source_layer_coverage_rows: `{summary.get('source_layer_coverage_rows')}`",
        f"- source_layer_spot_check_current: `{summary.get('source_layer_spot_check_current')}`",
        f"- source_layer_spot_check_next: `{summary.get('source_layer_spot_check_next')}`",
        f"- micro_walkthrough_status: `{summary.get('micro_walkthrough_status')}`",
        f"- micro_walkthrough_scopes: `{summary.get('micro_walkthrough_scopes')}`",
        f"- pipeline_step_registry_source: `{summary.get('pipeline_step_registry_source')}`",
        f"- derived_pending_layers: `{summary.get('derived_pending_layers')}`",
        f"- temporary_excursions: `{summary.get('temporary_excursions')}`",
        f"- checks: `{summary.get('passed_checks')}/{summary.get('total_checks')}`",
        "",
        "| Check | Result | Details |",
        "|---|---|---|",
    ]
    for check in payload.get("checks", []) or []:
        details = json.dumps(check.get("details"), ensure_ascii=False, sort_keys=True)[:900]
        escaped_details = details.replace("|", "\\|")
        lines.append(f"| `{check.get('name')}` | `{'PASS' if check.get('passed') else 'FAIL'}` | `{escaped_details}` |")
    return "\n".join(lines) + "\n"


def run() -> dict[str, Any]:
    payload = build_validation()
    save_json_atomic(RAW_OUTPUT_PATH, payload)
    save_text_atomic(REPORT_OUTPUT_PATH, render_report(payload))
    return payload


def main() -> int:
    payload = run()
    print(json.dumps(payload.get("summary", {}), ensure_ascii=False))
    return 0 if payload.get("summary", {}).get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
