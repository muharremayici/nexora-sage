from __future__ import annotations

from typing import Any


def release_train_policy_issues(policy: dict[str, Any]) -> list[str]:
    guard = (
        policy.get("release_train_guard")
        if isinstance(policy.get("release_train_guard"), dict)
        else {}
    )
    issues: list[str] = []
    if not guard:
        return ["missing_release_train_guard"]
    if guard.get("enabled") is not True:
        issues.append("release_train_guard_not_enabled")
    if (
        guard.get("release_order_source")
        != "config/roadmap_phase_registry.json:activation_planning_window"
    ):
        issues.append("invalid_release_train_order_source")
    blocking_statuses = guard.get("blocking_statuses")
    if (
        not isinstance(blocking_statuses, list)
        or not blocking_statuses
        or any(not str(item).strip() for item in blocking_statuses)
        or len(blocking_statuses) != len(set(blocking_statuses))
    ):
        issues.append("invalid_release_train_blocking_statuses")
    elif "ready_for_delivery" not in {str(item) for item in blocking_statuses}:
        issues.append("ready_for_delivery_missing_from_release_train_guard")
    if guard.get("release_blocking_field") != "release_blocking":
        issues.append("invalid_release_train_blocking_field")
    if guard.get("later_release_behavior") != "block_automatic_activation":
        issues.append("unsafe_release_train_later_release_behavior")
    if guard.get("action_required_status") != "RELEASE_TRAIN_ACTION_REQUIRED":
        issues.append("invalid_release_train_action_status")
    if (
        guard.get("parallel_development_rule")
        != "requires_explicit_future_contract_not_implicit_fallthrough"
    ):
        issues.append("unsafe_release_train_parallel_development_rule")
    return issues


def successor_selection_policy_issues(
    policy: dict[str, Any],
    *,
    production_release_statuses: set[str],
) -> list[str]:
    required_lists = (
        "candidate_statuses",
        "priority_order",
        "interrupt_priorities",
        "automatic_release_statuses",
        "human_choice_release_statuses",
    )
    issues = [
        f"missing_{field}"
        for field in required_lists
        if not isinstance(policy.get(field), list) or not policy.get(field)
    ]
    for field in required_lists:
        raw_values = policy.get(field, [])
        values = [str(item).strip() for item in raw_values if str(item).strip()]
        if any(not str(item).strip() for item in raw_values):
            issues.append(f"empty_{field}")
        if len(values) != len(set(values)):
            issues.append(f"duplicate_{field}")
    priority_order = {
        str(item) for item in policy.get("priority_order", []) if str(item)
    }
    interrupt_priorities = {
        str(item) for item in policy.get("interrupt_priorities", []) if str(item)
    }
    if not interrupt_priorities.issubset(priority_order):
        issues.append("interrupt_priority_outside_priority_order")
    automatic_statuses = {
        str(item) for item in policy.get("automatic_release_statuses", []) if str(item)
    }
    human_statuses = {
        str(item)
        for item in policy.get("human_choice_release_statuses", [])
        if str(item)
    }
    if automatic_statuses & human_statuses:
        issues.append("release_status_authority_overlap")
    if automatic_statuses & production_release_statuses:
        issues.append("production_release_status_is_automatic")
    if not production_release_statuses.issubset(human_statuses):
        issues.append("production_release_status_missing_human_boundary")
    if policy.get("tie_policy") != "human_choice_required":
        issues.append("unsafe_tie_policy")
    if (
        policy.get("release_preference")
        != "active_package_roadmap_phase_then_activation_planning_window"
    ):
        issues.append("unsafe_release_preference")
    if policy.get("stable_order") != [
        "ordered_wave_ids",
        "wave_work_item_ids",
        "work_item_registry",
    ]:
        issues.append("unstable_candidate_order")
    if policy.get("stable_order_authority") != "presentation_only":
        issues.append("unsafe_stable_order_authority")
    human_choice_input = (
        policy.get("human_choice_input")
        if isinstance(policy.get("human_choice_input"), dict)
        else {}
    )
    if (
        human_choice_input.get("source")
        != "config/sage_active_work_package.json:active_package.successor_selection_decision"
    ):
        issues.append("invalid_human_choice_source")
    if human_choice_input.get("applicable_reason_code") != "equal_rank_requires_human_choice":
        issues.append("invalid_human_choice_reason_boundary")
    if human_choice_input.get("required_fields") != [
        "work_item_id",
        "decided_by",
        "reason",
    ]:
        issues.append("invalid_human_choice_required_fields")
    if human_choice_input.get("authority") != "tie_resolution_only":
        issues.append("unsafe_human_choice_authority")
    issues.extend(release_train_policy_issues(policy))
    return sorted(set(issues))


def unavailable_release_train_projection() -> dict[str, Any]:
    return {
        "status": "UNAVAILABLE",
        "gated_release": None,
        "blocking_ready_work_item_ids": [],
        "all_blocking_ready_work_item_ids": [],
        "same_release_candidate_work_item_ids": [],
        "dependency_ready_same_release_candidate_work_item_ids": [],
        "later_release_activation_allowed": False,
        "active_scope_conflict": False,
        "action_required": False,
        "action_reason": "policy_not_validated",
        "parallel_development_authorized": False,
        "authority": {
            "release_preparation_authorized": False,
            "publication_authorized": False,
        },
    }


def project_release_train(
    *,
    policy: dict[str, Any],
    work_items_by_id: dict[str, dict[str, Any]],
    item_order: dict[str, int],
    candidates: list[dict[str, Any]],
    phases: dict[str, dict[str, Any]],
    phase_order: list[str],
    active_phase: str,
) -> dict[str, Any]:
    guard = policy["release_train_guard"]
    blocking_statuses = {
        str(item) for item in guard.get("blocking_statuses", []) if str(item)
    }
    blocking_field = str(guard.get("release_blocking_field") or "")
    roadmap_release_order = [
        release
        for release in phase_order
        if str(phases.get(release, {}).get("status") or "") == "roadmap"
    ]
    release_rank = {
        release: index for index, release in enumerate(roadmap_release_order)
    }
    blockers = [
        item
        for item in work_items_by_id.values()
        if str(item.get("status") or "") in blocking_statuses
        and item.get(blocking_field) is True
        and not str(item.get("delivered_release") or "").strip()
        and str(item.get("target_release") or "") in release_rank
    ]
    blockers.sort(
        key=lambda item: (
            release_rank[str(item.get("target_release") or "")],
            item_order[str(item.get("id") or "")],
        )
    )
    gated_release = (
        str(blockers[0].get("target_release") or "") if blockers else ""
    )
    gated_blocker_ids = [
        str(item.get("id") or "")
        for item in blockers
        if str(item.get("target_release") or "") == gated_release
    ]
    same_release = [
        row for row in candidates if row["target_release"] == gated_release
    ]
    dependency_ready_same_release = [
        row for row in same_release if row["dependency_ready"]
    ]
    active_scope_conflict = bool(
        gated_release
        and active_phase in release_rank
        and release_rank[active_phase] > release_rank[gated_release]
    )
    action_reason = (
        "active_package_is_ahead_of_predecessor_release"
        if active_scope_conflict
        else (
            "no_dependency_ready_same_release_successor"
            if gated_release and not dependency_ready_same_release
            else None
        )
    )
    return {
        "status": "PREDECESSOR_DELIVERY_REQUIRED" if gated_release else "CLEAR",
        "gated_release": gated_release or None,
        "blocking_ready_work_item_ids": gated_blocker_ids,
        "all_blocking_ready_work_item_ids": [
            str(item.get("id") or "") for item in blockers
        ],
        "same_release_candidate_work_item_ids": [
            row["work_item_id"] for row in same_release
        ],
        "dependency_ready_same_release_candidate_work_item_ids": [
            row["work_item_id"] for row in dependency_ready_same_release
        ],
        "later_release_activation_allowed": not bool(gated_release),
        "active_scope_conflict": active_scope_conflict,
        "action_required": bool(action_reason),
        "action_reason": action_reason,
        "parallel_development_authorized": False,
        "authority": {
            "release_preparation_authorized": False,
            "publication_authorized": False,
        },
    }


def project_release_train_action_boundary(
    *,
    policy: dict[str, Any],
    release_train: dict[str, Any],
    candidates: list[dict[str, Any]],
    excluded: set[str],
    human_choice_present: bool,
    human_choice_result: dict[str, Any],
    authority: dict[str, Any],
    boundary: str,
) -> dict[str, Any]:
    return {
        "meta": {"kind": "sage_successor_selection_projection", "version": "v1"},
        "status": (
            "INVALID_INPUT"
            if human_choice_present
            else str(policy["release_train_guard"]["action_required_status"])
        ),
        "reason_codes": [
            (
                "human_choice_decision_not_applicable"
                if human_choice_present
                else str(release_train["action_reason"])
            )
        ],
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
