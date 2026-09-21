from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.core.config import CODE_MAPS_DIR, CONFIG_DIR
from tools.core.json_io import load_json_object_strict
from tools.core.release_train import (
    project_release_train,
    project_release_train_action_boundary,
    successor_selection_policy_issues,
    unavailable_release_train_projection,
)
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


def project_successor_selection(
    work_items: list[dict[str, Any]],
    *,
    active_package: dict[str, Any] | None,
    wave_rows: list[dict[str, Any]],
    roadmap_registry: dict[str, Any],
    wave_registry: dict[str, Any],
    excluded_work_item_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Rank one dependency-correct successor or expose a bounded human-choice edge."""
    policy = (
        wave_registry.get("policy", {}).get("successor_selection_policy", {})
        if isinstance(wave_registry.get("policy"), dict)
        else {}
    )
    roadmap_validation = (
        roadmap_registry.get("validation_contract", {})
        if isinstance(roadmap_registry.get("validation_contract"), dict)
        else {}
    )
    production_release_statuses = {
        str(item)
        for item in roadmap_validation.get("production_release_statuses", [])
        if str(item)
    }
    policy_issues = successor_selection_policy_issues(
        policy,
        production_release_statuses=production_release_statuses,
    )
    raw_human_choice = (
        active_package.get("successor_selection_decision")
        if isinstance(active_package, dict)
        else None
    )
    human_choice_present = raw_human_choice is not None
    human_choice = raw_human_choice if isinstance(raw_human_choice, dict) else {}
    human_choice_result = {
        "present": human_choice_present,
        "applied": False,
        "work_item_id": str(human_choice.get("work_item_id") or "") or None,
        "decided_by": str(human_choice.get("decided_by") or "") or None,
        "reason": str(human_choice.get("reason") or "") or None,
        "authority": "tie_resolution_only",
    }
    authority = {
        "delivery_authorized": False,
        "concrete_release_selected": False,
        "release_preparation_authorized": False,
        "publication_authorized": False,
    }
    boundary = (
        "Successor projection may seed one bounded roadmap work package, require predecessor "
        "release action or expose a human-choice boundary. It never closes work, assigns "
        "delivered_release, selects a concrete release, activates claims, authorizes release "
        "preparation or publishes artifacts."
    )
    if policy_issues:
        return {
            "meta": {"kind": "sage_successor_selection_projection", "version": "v1"},
            "status": "INVALID_POLICY",
            "reason_codes": policy_issues,
            "selected_work_item_id": None,
            "selected_package_seed": None,
            "top_candidate_work_item_ids": [],
            "ranked_candidates": [],
            "excluded_active_work_item_ids": sorted(excluded_work_item_ids or set()),
            "human_choice_decision": human_choice_result,
            "release_train": unavailable_release_train_projection(),
            "transition_validation_eligible": False,
            "authority": authority,
            "claim_boundary": boundary,
        }

    item_by_id = {
        str(row.get("id") or ""): row
        for row in work_items
        if isinstance(row, dict) and str(row.get("id") or "").strip()
    }
    item_order = {item_id: index for index, item_id in enumerate(item_by_id)}
    row_by_id = {
        str(row.get("id") or ""): row
        for row in wave_rows
        if isinstance(row, dict) and str(row.get("id") or "").strip()
    }
    configured_waves = [
        row for row in wave_registry.get("waves", []) if isinstance(row, dict)
    ]
    ordered_wave_ids = [
        str(item)
        for item in wave_registry.get("policy", {}).get("ordered_wave_ids", [])
        if str(item)
    ]
    if not ordered_wave_ids:
        ordered_wave_ids = [str(row.get("id") or "") for row in configured_waves]
    wave_order = {wave_id: index for index, wave_id in enumerate(ordered_wave_ids)}
    assignment: dict[str, tuple[str, int]] = {}
    duplicate_assignments: set[str] = set()
    for wave in configured_waves:
        wave_id = str(wave.get("id") or "")
        for index, item_id in enumerate(
            str(item) for item in wave.get("work_item_ids", []) if str(item)
        ):
            if item_id in assignment:
                duplicate_assignments.add(item_id)
            assignment[item_id] = (wave_id, index)

    phases = {
        str(row.get("release") or ""): row
        for row in roadmap_phase_rows(roadmap_registry)
        if str(row.get("release") or "").strip()
    }
    phase_order = [
        str(item)
        for item in roadmap_registry.get("activation_planning_window", [])
        if str(item)
    ]
    active_scope = (
        active_package.get("release_scope", {})
        if isinstance(active_package, dict)
        and isinstance(active_package.get("release_scope"), dict)
        else {}
    )
    active_phase = str(
        active_scope.get("roadmap_phase") or active_scope.get("target_release") or ""
    )
    preferred_releases: list[str] = []
    for release in [active_phase, *phase_order, *phases]:
        if release and release not in preferred_releases:
            preferred_releases.append(release)

    candidate_statuses = {
        str(item) for item in policy.get("candidate_statuses", []) if str(item)
    }
    priority_order = [str(item) for item in policy.get("priority_order", []) if str(item)]
    priority_rank = {priority: index for index, priority in enumerate(priority_order)}
    interrupt_priorities = {
        str(item) for item in policy.get("interrupt_priorities", []) if str(item)
    }
    automatic_release_statuses = {
        str(item) for item in policy.get("automatic_release_statuses", []) if str(item)
    }
    human_choice_release_statuses = {
        str(item) for item in policy.get("human_choice_release_statuses", []) if str(item)
    }
    excluded = set(excluded_work_item_ids or set())
    candidates: list[dict[str, Any]] = []
    for item_id, item in item_by_id.items():
        status = str(item.get("status") or "")
        if status not in candidate_statuses or item_id in excluded or item_id not in assignment:
            continue
        wave_id, wave_item_order = assignment[item_id]
        wave = row_by_id.get(wave_id, {})
        unsatisfied = [
            str(value)
            for value in wave.get("unsatisfied_technical_dependencies", [])
            if str(value)
        ]
        target_release = str(item.get("target_release") or "")
        release_status = str(phases.get(target_release, {}).get("status") or "unknown")
        priority = str(item.get("priority") or "")
        candidates.append(
            {
                "work_item_id": item_id,
                "status": status,
                "priority": priority,
                "target_release": target_release,
                "release_status": release_status,
                "wave_id": wave_id,
                "dependency_ready": not unsatisfied,
                "unsatisfied_technical_dependencies": unsatisfied,
                "interrupt_priority": priority in interrupt_priorities,
                "automatic_release_scope": release_status in automatic_release_statuses,
                "human_choice_release_scope": release_status in human_choice_release_statuses,
                "stable_order": {
                    "wave": wave_order.get(wave_id, len(wave_order)),
                    "wave_work_item": wave_item_order,
                    "work_item_registry": item_order.get(item_id, len(item_order)),
                },
            }
        )

    def stable_key(row: dict[str, Any]) -> tuple[int, int, int]:
        order = row["stable_order"]
        return (
            int(order["wave"]),
            int(order["wave_work_item"]),
            int(order["work_item_registry"]),
        )

    candidates.sort(key=stable_key)
    release_train = project_release_train(
        policy=policy,
        work_items_by_id=item_by_id,
        item_order=item_order,
        candidates=candidates,
        phases=phases,
        phase_order=phase_order,
        active_phase=active_phase,
    )
    input_issues = [
        f"duplicate_wave_assignment:{item_id}"
        for item_id in sorted(duplicate_assignments)
        if item_id in item_by_id
    ]
    for row in candidates:
        if row["priority"] not in priority_rank:
            input_issues.append(f"unknown_priority:{row['work_item_id']}")
        if row["target_release"] not in phases:
            input_issues.append(f"unknown_target_release:{row['work_item_id']}")
        if row["wave_id"] not in row_by_id or row["wave_id"] not in wave_order:
            input_issues.append(f"unknown_wave:{row['work_item_id']}")
    if human_choice_present:
        if not isinstance(raw_human_choice, dict):
            input_issues.append("invalid_human_choice_decision_type")
        else:
            for field in ("work_item_id", "decided_by", "reason"):
                if not str(human_choice.get(field) or "").strip():
                    input_issues.append(f"missing_human_choice_{field}")
    if input_issues:
        return {
            "meta": {"kind": "sage_successor_selection_projection", "version": "v1"},
            "status": "INVALID_INPUT",
            "reason_codes": sorted(set(input_issues)),
            "selected_work_item_id": None,
            "selected_package_seed": None,
            "top_candidate_work_item_ids": [],
            "ranked_candidates": candidates,
            "excluded_active_work_item_ids": sorted(excluded),
            "human_choice_decision": human_choice_result,
            "release_train": release_train,
            "transition_validation_eligible": False,
            "authority": authority,
            "claim_boundary": boundary,
        }
    if release_train["action_required"]:
        return project_release_train_action_boundary(
            policy=policy,
            release_train=release_train,
            candidates=candidates,
            excluded=excluded,
            human_choice_present=human_choice_present,
            human_choice_result=human_choice_result,
            authority=authority,
            boundary=boundary,
        )

    gated_release = str(release_train.get("gated_release") or "")
    same_release_candidate_ids = set(
        release_train.get("same_release_candidate_work_item_ids", [])
    )
    selection_candidates = (
        [
            row
            for row in candidates
            if row["work_item_id"] in same_release_candidate_ids
        ]
        if gated_release
        else candidates
    )
    dependency_ready = [
        row for row in selection_candidates if row["dependency_ready"]
    ]
    interrupt = [row for row in dependency_ready if row["interrupt_priority"]]
    reason_codes: list[str] = []
    pool: list[dict[str, Any]] = []
    if interrupt:
        if any(not row["automatic_release_scope"] for row in interrupt):
            reason_codes = ["interrupt_candidate_requires_release_scope_choice"]
            pool = interrupt
        else:
            reason_codes = ["interrupt_priority"]
            pool = interrupt
    else:
        automatic = [row for row in dependency_ready if row["automatic_release_scope"]]
        if automatic:
            reason_codes = ["unpublished_roadmap_scope"]
            pool = automatic
        elif dependency_ready:
            reason_codes = ["published_carryover_requires_human_scope"]
            pool = [
                row
                for row in dependency_ready
                if row["human_choice_release_scope"]
            ] or dependency_ready

    if not selection_candidates:
        status = "NO_CANDIDATE"
        reason_codes = ["no_open_or_in_progress_candidate"]
        top: list[dict[str, Any]] = []
    elif not dependency_ready:
        status = "DEPENDENCY_BLOCKED"
        reason_codes = ["all_candidates_dependency_blocked"]
        top = []
    elif not pool:
        status = "HUMAN_CHOICE_REQUIRED"
        reason_codes = ["no_automatic_release_scope"]
        top = dependency_ready
    elif reason_codes[0] in {
        "interrupt_candidate_requires_release_scope_choice",
        "published_carryover_requires_human_scope",
    }:
        status = "HUMAN_CHOICE_REQUIRED"
        top = pool
    else:
        available_releases = {row["target_release"] for row in pool}
        selected_release = next(
            (release for release in preferred_releases if release in available_releases),
            "",
        )
        if selected_release:
            pool = [row for row in pool if row["target_release"] == selected_release]
            reason_codes.append("preferred_release_scope")
        best_priority = min(
            (priority_rank.get(row["priority"], len(priority_rank)) for row in pool),
            default=len(priority_rank),
        )
        pool = [
            row
            for row in pool
            if priority_rank.get(row["priority"], len(priority_rank)) == best_priority
        ]
        reason_codes.append("highest_priority")
        best_wave = min(
            (wave_order.get(row["wave_id"], len(wave_order)) for row in pool),
            default=len(wave_order),
        )
        top = [
            row
            for row in pool
            if wave_order.get(row["wave_id"], len(wave_order)) == best_wave
        ]
        top.sort(key=stable_key)
        reason_codes.append("earliest_dependency_correct_wave")
        if len(top) == 1:
            status = "SELECTED"
            reason_codes.append("unique_top_rank")
        else:
            status = "HUMAN_CHOICE_REQUIRED"
            reason_codes.append("equal_rank_requires_human_choice")

    if gated_release:
        reason_codes.insert(0, "release_train_predecessor_scope")

    if human_choice_present:
        chosen_id = str(human_choice.get("work_item_id") or "")
        top_by_id = {str(row.get("work_item_id") or ""): row for row in top}
        if (
            status != "HUMAN_CHOICE_REQUIRED"
            or "equal_rank_requires_human_choice" not in reason_codes
        ):
            status = "INVALID_INPUT"
            reason_codes = ["human_choice_decision_not_applicable"]
            top = []
        elif chosen_id not in top_by_id:
            status = "INVALID_INPUT"
            reason_codes = ["human_choice_not_current_top_candidate"]
            top = []
        else:
            top = [top_by_id[chosen_id]]
            status = "SELECTED"
            reason_codes.append("attributable_human_tie_resolution")
            human_choice_result["applied"] = True

    selected = top[0] if status == "SELECTED" else None
    selected_seed = (
        {
            "execution_wave": selected["wave_id"],
            "work_item_ids": [selected["work_item_id"]],
            "release_scope": {
                "mode": "roadmap_delivery",
                "roadmap_phase": selected["target_release"],
                "concrete_release": None,
                "does_not_expand_current_release_claims": True,
            },
        }
        if selected is not None
        else None
    )
    return {
        "meta": {"kind": "sage_successor_selection_projection", "version": "v1"},
        "status": status,
        "reason_codes": reason_codes,
        "selected_work_item_id": selected["work_item_id"] if selected else None,
        "selected_package_seed": selected_seed,
        "top_candidate_work_item_ids": [row["work_item_id"] for row in top],
        "ranked_candidates": candidates,
        "excluded_active_work_item_ids": sorted(excluded),
        "human_choice_decision": human_choice_result,
        "release_train": release_train,
        "transition_validation_eligible": status == "SELECTED",
        "authority": authority,
        "claim_boundary": boundary,
    }


def project_execution_waves(
    work_items: list[dict[str, Any]],
    *,
    active_package: dict[str, Any] | None = None,
    technical_ready_work_item_ids: set[str] | None = None,
    readiness_root: Path = CODE_MAPS_DIR,
    wave_registry: dict[str, Any] | None = None,
    roadmap_registry: dict[str, Any] | None = None,
    successor_excluded_work_item_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Project public delivery and technical-development readiness as separate axes."""
    registry = wave_registry if wave_registry is not None else load_execution_wave_registry()
    roadmap = roadmap_registry if roadmap_registry is not None else load_roadmap_phase_registry()
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
            or bool(row_by_id[dependency].get("unsatisfied_technical_dependencies"))
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
            roadmap_registry=roadmap,
            wave_registry=registry,
            technical_ready_work_item_ids=technically_ready,
        )
        if isinstance(active_package, dict) and active_package
        else None
    )
    excluded_successor_ids = (
        set(successor_excluded_work_item_ids)
        if successor_excluded_work_item_ids is not None
        else {
            str(item)
            for item in (active_package or {}).get("work_item_ids", [])
            if str(item)
        }
    )
    successor_selection = project_successor_selection(
        work_items,
        active_package=active_package,
        wave_rows=rows,
        roadmap_registry=roadmap,
        wave_registry=registry,
        excluded_work_item_ids=excluded_successor_ids,
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
        "successor_selection": successor_selection,
        "release_delivery": release_delivery,
        "waves": rows,
        "source": "config/sage_execution_wave_registry.json",
        "detail_source": "config/sage_work_item_registry.json",
    }
