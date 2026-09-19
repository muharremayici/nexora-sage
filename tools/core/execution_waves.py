from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.core.config import CODE_MAPS_DIR, CONFIG_DIR
from tools.core.json_io import load_json_object_strict
from tools.core.roadmap_phase_registry import (
    current_product_release,
    load_roadmap_phase_registry,
    project_semver_candidate,
    production_release_history,
    roadmap_phase_rows,
)
from tools.core.work_item_readiness import assess_work_item_technical_readiness


WAVE_REGISTRY_PATH = CONFIG_DIR / "sage_execution_wave_registry.json"


def load_execution_wave_registry() -> dict[str, Any]:
    return load_json_object_strict(WAVE_REGISTRY_PATH, label="SAGE execution wave registry")


def release_scope_matches_wave(
    wave: dict[str, Any],
    work_items: dict[str, dict[str, Any]],
    policy: dict[str, Any],
    known_releases: set[str],
    current_release: str,
) -> tuple[bool, dict[str, Any]]:
    """Verify finite wave delivery scope without conflating it with dependency order."""
    scope = wave.get("release_scope") if isinstance(wave.get("release_scope"), dict) else {}
    kind = str(scope.get("kind") or "")
    declared_releases = {str(item) for item in scope.get("target_releases", []) if str(item)}
    item_ids = [str(item) for item in wave.get("work_item_ids", []) if str(item)]
    item_releases = {
        str(work_items[item_id].get("target_release") or "")
        for item_id in item_ids
        if item_id in work_items
    }
    allowed_kinds = {str(item) for item in policy.get("allowed_kinds", []) if str(item)}
    closure_kind = str(policy.get("release_closure_kind") or "")
    requires_active_release = policy.get("release_closure_requires_active_product_release") is True
    closure_valid = (
        kind != closure_kind
        or (
            not item_ids
            and (not requires_active_release or declared_releases == {current_release})
        )
    )
    valid = (
        kind in allowed_kinds
        and bool(declared_releases)
        and declared_releases.issubset(known_releases)
        and item_releases.issubset(declared_releases)
        and closure_valid
    )
    return valid, {
        "wave": str(wave.get("id") or ""),
        "scope_kind": kind,
        "declared_target_releases": sorted(declared_releases),
        "work_item_target_releases": sorted(item_releases),
        "current_product_release": current_release,
        "unknown_declared_releases": sorted(declared_releases - known_releases),
        "outside_scope_work_item_releases": sorted(item_releases - declared_releases),
        "closure_scope_valid": closure_valid,
    }


def project_release_delivery(
    work_items: list[dict[str, Any]],
    *,
    active_package: dict[str, Any],
    roadmap_registry: dict[str, Any] | None = None,
    wave_registry: dict[str, Any] | None = None,
    technical_ready_work_item_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Project one bounded package without inventing a concrete publication."""
    roadmap = roadmap_registry if roadmap_registry is not None else load_roadmap_phase_registry()
    waves_doc = wave_registry if wave_registry is not None else load_execution_wave_registry()
    scope = active_package.get("release_scope") if isinstance(active_package.get("release_scope"), dict) else {}
    mode = str(scope.get("mode") or "")
    explicit_phase = str(scope.get("roadmap_phase") or "").strip()
    legacy_target = str(scope.get("target_release") or "").strip()
    roadmap_phase = explicit_phase or legacy_target
    concrete_value = scope.get("concrete_release")
    concrete_release = str(concrete_value).strip() if concrete_value not in {None, ""} else ""
    phase_rows = {
        str(row.get("release") or ""): row
        for row in roadmap_phase_rows(roadmap)
        if str(row.get("release") or "").strip()
    }
    current_release = current_product_release(roadmap)
    semver_candidate = project_semver_candidate(
        work_items,
        roadmap,
        technical_ready_work_item_ids=technical_ready_work_item_ids,
    )
    issues: list[str] = []
    if explicit_phase and legacy_target and explicit_phase != legacy_target:
        issues.append("ambiguous_roadmap_phase")
    if not roadmap_phase:
        issues.append("missing_roadmap_phase")
    elif roadmap_phase not in phase_rows:
        issues.append("unknown_roadmap_phase")
    if concrete_release and concrete_release not in phase_rows:
        issues.append("unknown_concrete_release")
    recommended_release = str(semver_candidate.get("recommended_release") or "")
    if (
        mode == "roadmap_delivery"
        and concrete_release
        and recommended_release
        and concrete_release != recommended_release
    ):
        issues.append("concrete_release_semver_recommendation_mismatch")

    phase_status = str(phase_rows.get(roadmap_phase, {}).get("status") or "")
    concrete_status = str(phase_rows.get(concrete_release, {}).get("status") or "") if concrete_release else ""
    if mode == "roadmap_delivery":
        if phase_status != "roadmap":
            issues.append("roadmap_delivery_requires_roadmap_phase")
        if scope.get("does_not_expand_current_release_claims") is not True:
            issues.append("roadmap_claim_non_expansion_not_declared")
        if concrete_release and concrete_status != "roadmap":
            issues.append("concrete_release_not_unpublished_roadmap")
    elif mode == "active_product_release":
        if roadmap_phase != current_release:
            issues.append("active_release_phase_mismatch")
        if concrete_release and concrete_release != current_release:
            issues.append("active_release_concrete_target_mismatch")
    elif mode == "active_release_closure":
        if roadmap_phase != current_release:
            issues.append("release_closure_phase_mismatch")
        if concrete_release and concrete_release != current_release:
            issues.append("release_closure_concrete_target_mismatch")
    else:
        issues.append("unknown_release_scope_mode")

    item_by_id = {
        str(row.get("id") or ""): row
        for row in work_items
        if isinstance(row, dict) and str(row.get("id") or "").strip()
    }
    open_items = [
        row
        for row in item_by_id.values()
        if str(row.get("status") or "") != "closed"
    ]
    eligible = [
        row
        for row in open_items
        if str(row.get("target_release") or "") == roadmap_phase
    ]
    eligible_ids = {str(row.get("id") or "") for row in eligible}
    package_ids = [str(item) for item in active_package.get("work_item_ids", []) if str(item)]
    missing_package_ids = sorted(item for item in package_ids if item not in item_by_id)
    outside_phase_ids = sorted(item for item in package_ids if item in item_by_id and item not in eligible_ids)
    if missing_package_ids:
        issues.append("unknown_bounded_package_work_item")
    if outside_phase_ids:
        issues.append("bounded_package_outside_roadmap_phase")
    if mode != "active_release_closure" and not package_ids:
        issues.append("empty_bounded_package")

    ordered_wave_ids = [
        str(item)
        for item in (waves_doc.get("policy", {}) or {}).get("ordered_wave_ids", [])
        if str(item)
    ]
    wave_rows = [row for row in waves_doc.get("waves", []) if isinstance(row, dict)]
    wave_by_id = {str(row.get("id") or ""): row for row in wave_rows}
    if not ordered_wave_ids:
        ordered_wave_ids = [str(row.get("id") or "") for row in wave_rows if str(row.get("id") or "")]
    delivery_waves: list[dict[str, Any]] = []
    for wave_id in ordered_wave_ids:
        wave = wave_by_id.get(wave_id, {})
        wave_item_ids = [str(item) for item in wave.get("work_item_ids", []) if str(item)]
        matching = [item for item in wave_item_ids if item in eligible_ids]
        selected = [item for item in matching if item in package_ids]
        if matching or selected:
            delivery_waves.append(
                {
                    "id": wave_id,
                    "eligible_work_item_ids": matching,
                    "bounded_package_work_item_ids": selected,
                    "blocked_by": [str(item) for item in wave.get("blocked_by", []) if str(item)],
                }
            )

    historical_releases = production_release_history(roadmap)
    overdue = sorted(
        str(row.get("id") or "")
        for row in open_items
        if str(row.get("target_release") or "") in historical_releases
    )
    status = "FAIL" if issues else (
        "ATTENTION_PLANNING_ONLY"
        if mode == "roadmap_delivery" and not concrete_release
        else "PASS"
    )
    return {
        "meta": {"kind": "sage_release_delivery_projection", "version": "v1"},
        "status": status,
        "issues": sorted(set(issues)),
        "planning": {
            "mode": mode,
            "roadmap_phase": roadmap_phase or None,
            "roadmap_phase_status": phase_status or None,
            "current_product_release": current_release,
            "concrete_release": concrete_release or None,
            "concrete_release_status": concrete_status or None,
            "publication_target_status": "selected" if concrete_release else "not_selected",
            "recommended_concrete_release": semver_candidate.get("recommended_release"),
            "semver_candidate_status": semver_candidate.get("status"),
            "claim_non_expansion_declared": scope.get("does_not_expand_current_release_claims") is True,
        },
        "bounded_package": {
            "id": active_package.get("id"),
            "execution_wave": active_package.get("execution_wave"),
            "work_item_ids": package_ids,
            "missing_work_item_ids": missing_package_ids,
            "outside_phase_work_item_ids": outside_phase_ids,
        },
        "eligible_backlog": {
            "work_items": len(eligible_ids),
            "work_item_ids": sorted(eligible_ids),
            "waves": delivery_waves,
        },
        "overdue_carryover": {
            "work_items": len(overdue),
            "work_item_ids": overdue,
        },
        "semver_candidate": semver_candidate,
        "authority": {
            "publication_authorized": False,
            "release_preparation_authorized": False,
            "claim_profile_activation_authorized": False,
        },
        "claim_boundary": "This projection selects planning work and may recommend one SemVer candidate from explicit ready-work impact evidence. It does not activate a claim profile, select a concrete public version, authorize release preparation or publish artifacts.",
    }


def project_execution_waves(
    work_items: list[dict[str, Any]],
    *,
    active_package: dict[str, Any] | None = None,
    technical_ready_work_item_ids: set[str] | None = None,
    readiness_root: Path = CODE_MAPS_DIR,
    wave_registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Project public delivery and technical-development readiness as separate axes."""
    registry = wave_registry if wave_registry is not None else load_execution_wave_registry()
    status_by_id = {
        str(row.get("id") or ""): str(row.get("status") or "")
        for row in work_items
        if isinstance(row, dict) and row.get("id")
    }
    item_by_id = {
        str(row.get("id") or ""): row
        for row in work_items
        if isinstance(row, dict) and row.get("id")
    }
    ready_rows = [
        row
        for row in item_by_id.values()
        if str(row.get("status") or "") == "ready_for_delivery"
    ]
    if technical_ready_work_item_ids is None:
        readiness_assessments = [
            assess_work_item_technical_readiness(row, root=readiness_root)
            for row in ready_rows
        ]
        technically_ready = {
            str(row.get("id") or "")
            for row in readiness_assessments
            if row.get("ready") is True
        }
        readiness_source = "independent_upstream_artifact_assessment"
    else:
        declared_ready = {str(item) for item in technical_ready_work_item_ids if str(item)}
        technically_ready = {
            str(row.get("id") or "")
            for row in ready_rows
            if str(row.get("id") or "") in declared_ready
        }
        readiness_assessments = [
            {
                "id": row.get("id"),
                "ready": str(row.get("id") or "") in technically_ready,
                "errors": [],
                "runtime_evidence_checked": True,
            }
            for row in ready_rows
        ]
        readiness_source = "caller_prevalidated_upstream_artifacts"

    rows: list[dict[str, Any]] = []
    next_open_delivery_wave = ""
    wave_ids = {
        str(wave.get("id") or "")
        for wave in registry.get("waves", [])
        if isinstance(wave, dict)
    }
    active_wave = (
        str(active_package.get("execution_wave") or "")
        if isinstance(active_package, dict) and str(active_package.get("status") or "") == "in_progress"
        else ""
    )
    if active_wave not in wave_ids:
        active_wave = ""

    for wave in registry.get("waves", []):
        if not isinstance(wave, dict):
            continue
        item_ids = [str(item) for item in wave.get("work_item_ids", [])]
        delivery_open_ids = [
            item for item in item_ids if status_by_id.get(item) != "closed"
        ]
        technical_blocking_ids = [
            item
            for item in delivery_open_ids
            if item not in technically_ready
        ]
        if delivery_open_ids and not next_open_delivery_wave:
            next_open_delivery_wave = str(wave.get("id") or "")
        rows.append(
            {
                "id": wave.get("id"),
                "title": wave.get("title"),
                "computed_status": "complete" if item_ids and not delivery_open_ids else "planned",
                "blocked_by": [str(item) for item in wave.get("blocked_by", []) if str(item)],
                "unsatisfied_technical_dependencies": [],
                "technical_dependency_ready": not technical_blocking_ids,
                "objective": wave.get("objective"),
                "release_scope": wave.get("release_scope", {}),
                "work_items": len(item_ids),
                "open_work_items": len(delivery_open_ids),
                "open_work_item_ids": delivery_open_ids,
                "technical_blocking_work_items": len(technical_blocking_ids),
                "technical_blocking_work_item_ids": technical_blocking_ids,
                "next_actions": [
                    {
                        "id": item_id,
                        "priority": item_by_id.get(item_id, {}).get("priority"),
                        "target_release": item_by_id.get(item_id, {}).get("target_release"),
                        "next_action": item_by_id.get(item_id, {}).get("next_action"),
                    }
                    for item_id in technical_blocking_ids[:5]
                ],
                "capability_ids": wave.get("capability_ids", []),
            }
        )

    row_by_id = {str(row.get("id") or ""): row for row in rows}
    for row in rows:
        row["unsatisfied_technical_dependencies"] = [
            dependency
            for dependency in row["blocked_by"]
            if dependency not in row_by_id
            or row_by_id[dependency].get("technical_dependency_ready") is not True
        ]

    next_technical_development_wave = ""
    for row in rows:
        if (
            row["technical_blocking_work_items"]
            and not row["unsatisfied_technical_dependencies"]
        ):
            next_technical_development_wave = str(row.get("id") or "")
            break

    current_wave = active_wave or next_technical_development_wave or next_open_delivery_wave
    active_wave_dependency_issues = (
        list(row_by_id.get(active_wave, {}).get("unsatisfied_technical_dependencies", []))
        if active_wave
        else []
    )
    for row in rows:
        if not row["open_work_items"]:
            row["computed_status"] = "complete"
        elif row["id"] == current_wave:
            row["computed_status"] = "in_progress"
        elif row["unsatisfied_technical_dependencies"]:
            row["computed_status"] = "blocked"
        elif row["technical_dependency_ready"]:
            row["computed_status"] = "ready_for_delivery"
        elif row["id"] == next_technical_development_wave:
            row["computed_status"] = "planned"
        else:
            row["computed_status"] = "blocked"

    release_delivery = (
        project_release_delivery(
            work_items,
            active_package=active_package,
            wave_registry=registry,
            technical_ready_work_item_ids=technically_ready,
        )
        if isinstance(active_package, dict) and active_package
        else None
    )
    return {
        "meta": {"kind": "sage_execution_wave_projection", "version": "v4"},
        "current_wave": current_wave,
        "next_open_delivery_wave": next_open_delivery_wave,
        "next_technical_development_wave": next_technical_development_wave,
        "selection_basis": (
            "active_work_package"
            if active_wave
            else "first_technical_development_wave"
            if next_technical_development_wave
            else "first_open_delivery_wave"
        ),
        "active_wave_dependency_issues": active_wave_dependency_issues,
        "technical_readiness": {
            "source": readiness_source,
            "ready_work_item_ids": sorted(technically_ready),
            "assessments": readiness_assessments,
            "claim_boundary": (
                "Technical readiness may unlock downstream roadmap development only. It does not "
                "close work, assign delivered_release, satisfy public delivery, activate claims or "
                "authorize publication."
            ),
        },
        "release_delivery": release_delivery,
        "waves": rows,
        "source": "config/sage_execution_wave_registry.json",
        "detail_source": "config/sage_work_item_registry.json",
    }
