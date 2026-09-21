from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.config import CONFIG_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.agent_surface_seal_contract import load_agent_surface_seal_contract
from tools.core.json_io import load_json_object_strict
from tools.core.distribution_policy import is_clean_install_root
from tools.core.execution_waves import project_execution_waves
from tools.core.work_item_readiness import (
    assess_work_item_technical_readiness,
    technical_evidence_artifacts,
)
from tools.core.sage_active_work_package import active_work_package


REGISTRY_PATH = CONFIG_DIR / "sage_work_item_registry.json"
RAW_PATH = RAW_DIR / "sage_work_item_report.json"
REPORT_PATH = REPORTS_DIR / "sage_work_item_report.md"


def _registry() -> dict[str, Any]:
    return load_json_object_strict(REGISTRY_PATH, label="SAGE work item registry")


def _agent_surface_followups() -> list[dict[str, Any]]:
    payload = load_agent_surface_seal_contract()
    rows = payload.get("open_followups") if isinstance(payload, dict) else []
    return [
        row
        for row in rows
        if isinstance(row, dict) and str(row.get("status") or "").strip().lower() != "closed"
    ]


def _group_counts(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    return dict(sorted(Counter(str(row.get(key) or "unknown") for row in rows).items()))


def _policy_list(policy: dict[str, Any], key: str) -> list[str]:
    raw = policy.get(key)
    return [str(item) for item in raw if str(item).strip()] if isinstance(raw, list) else []


def _technical_evidence_artifacts() -> set[str]:
    """Compatibility wrapper for existing validator and test consumers."""
    return technical_evidence_artifacts()


def assess_delivery_readiness(row: dict[str, Any], *, root: Path = ROOT) -> dict[str, Any]:
    """Compatibility wrapper; technical readiness never grants delivery authority."""
    return assess_work_item_technical_readiness(
        row,
        root=root,
        clean_install=is_clean_install_root(root),
    )


def build_report() -> dict[str, Any]:
    registry = _registry()
    priority_policy = registry.get("priority_policy") if isinstance(registry.get("priority_policy"), dict) else {}
    blocking_priorities = set(_policy_list(priority_policy, "blocking_priorities"))
    rows = registry.get("work_items") if isinstance(registry.get("work_items"), list) else []
    work_items = [row for row in rows if isinstance(row, dict)]
    open_items = [row for row in work_items if str(row.get("status") or "") != "closed"]
    closed_items = [row for row in work_items if str(row.get("status") or "") == "closed"]
    delivery_variances = [
        row
        for row in closed_items
        if str(row.get("delivered_release") or "") != str(row.get("target_release") or "")
    ]
    blocking_items = [
        row
        for row in open_items
        if str(row.get("priority") or "") in blocking_priorities or row.get("release_blocking") is True
    ]
    agent_surface_followups = _agent_surface_followups()
    delivery_assessments = [assess_delivery_readiness(row) for row in open_items
                           if row.get("status") == "ready_for_delivery"]
    technically_ready = {row["id"] for row in delivery_assessments if row["ready"]}
    technical_blocking_items = [row for row in blocking_items if row.get("id") not in technically_ready]
    agent_surface_ids = {str(row.get("id") or "") for row in agent_surface_followups}
    registry_ids = {str(row.get("id") or "") for row in work_items}
    untracked_agent_surface_followups = sorted(agent_surface_ids - registry_ids)
    execution_plan = project_execution_waves(
        work_items,
        active_package=active_work_package(),
        technical_ready_work_item_ids=technically_ready,
    )
    successor_selection = (
        execution_plan.get("successor_selection")
        if isinstance(execution_plan.get("successor_selection"), dict)
        else {}
    )
    successor_projection_ok = successor_selection.get("status") in {
        "SELECTED",
        "HUMAN_CHOICE_REQUIRED",
        "DEPENDENCY_BLOCKED",
        "NO_CANDIDATE",
        "RELEASE_TRAIN_ACTION_REQUIRED",
    }
    release_train = (
        successor_selection.get("release_train")
        if isinstance(successor_selection.get("release_train"), dict)
        else {}
    )
    successor_human_choice = (
        successor_selection.get("human_choice_decision")
        if isinstance(successor_selection.get("human_choice_decision"), dict)
        else {}
    )
    release_delivery = execution_plan.get("release_delivery") if isinstance(execution_plan.get("release_delivery"), dict) else {}
    release_planning = release_delivery.get("planning") if isinstance(release_delivery.get("planning"), dict) else {}
    semver_candidate = release_delivery.get("semver_candidate") if isinstance(release_delivery.get("semver_candidate"), dict) else {}
    bounded_package = release_delivery.get("bounded_package") if isinstance(release_delivery.get("bounded_package"), dict) else {}
    payload = {
        "meta": {
            "kind": "sage_work_item_report",
            "version": "v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "generator": "tools.generate_sage_work_item_report",
        },
        "summary": {
            "status": "PASS" if (
                not untracked_agent_surface_followups
                and not any(assessment["errors"] for assessment in delivery_assessments)
                and successor_projection_ok
            ) else "FAIL",
            "total_work_items": len(work_items),
            "open_work_items": len(open_items),
            "blocking_work_items": len(blocking_items),
            "technical_blocking_work_items": len(technical_blocking_items),
            "delivery_pending_work_items": len(delivery_assessments),
            "untracked_agent_surface_followups": len(untracked_agent_surface_followups),
            "by_priority": _group_counts(work_items, "priority"),
            "open_by_priority": _group_counts(open_items, "priority"),
            "by_owner_layer": _group_counts(work_items, "owner_layer"),
            "by_planned_release": _group_counts(work_items, "target_release"),
            "open_by_planned_release": _group_counts(open_items, "target_release"),
            "closed_by_delivered_release": _group_counts(closed_items, "delivered_release"),
            "closed_delivery_variances": len(delivery_variances),
            "current_execution_wave": execution_plan.get("current_wave"),
            "next_open_delivery_wave": execution_plan.get("next_open_delivery_wave"),
            "next_technical_development_wave": execution_plan.get("next_technical_development_wave"),
            "successor_selection_status": successor_selection.get("status"),
            "selected_successor_work_item_id": successor_selection.get("selected_work_item_id"),
            "successor_top_candidate_work_items": len(
                successor_selection.get("top_candidate_work_item_ids", [])
            ),
            "successor_human_choice_present": successor_human_choice.get("present") is True,
            "successor_human_choice_applied": successor_human_choice.get("applied") is True,
            "release_train_status": release_train.get("status"),
            "release_train_gated_release": release_train.get("gated_release"),
            "release_train_blocking_ready_work_items": len(
                release_train.get("blocking_ready_work_item_ids", [])
            ),
            "release_train_action_required": release_train.get("action_required") is True,
            "later_release_activation_allowed": release_train.get(
                "later_release_activation_allowed"
            ) is True,
            "release_delivery_status": release_delivery.get("status"),
            "roadmap_phase": release_planning.get("roadmap_phase"),
            "concrete_release": release_planning.get("concrete_release"),
            "publication_target_status": release_planning.get("publication_target_status"),
            "semver_candidate_status": semver_candidate.get("status"),
            "recommended_concrete_release": semver_candidate.get("recommended_release"),
            "semver_candidate_work_items": semver_candidate.get("candidate_scope", {}).get("work_items", 0),
            "bounded_package_work_items": len(bounded_package.get("work_item_ids", [])),
        },
        "registry": str(REGISTRY_PATH.relative_to(ROOT)),
        "open_items": open_items,
        "blocking_items": blocking_items,
        "technical_blocking_items": technical_blocking_items,
        "delivery_assessments": delivery_assessments,
        "delivery_variances": delivery_variances,
        "agent_surface_followup_ids": sorted(agent_surface_ids),
        "untracked_agent_surface_followups": untracked_agent_surface_followups,
        "execution_plan": execution_plan,
        "rule": "Open work is grouped by planned roadmap phase; a bounded package may omit concrete_release until publication planning. Every ready_for_delivery item carries an explicit SemVer impact disposition, so the highest impact across all eligible undelivered work recommends the next release without selecting or publishing it. Closed work is grouped by actual delivered_release.",
    }
    return payload


def render_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# SAGE Work Item Report",
        "",
        f"- status: `{summary.get('status')}`",
        f"- total_work_items: `{summary.get('total_work_items')}`",
        f"- open_work_items: `{summary.get('open_work_items')}`",
        f"- blocking_work_items: `{summary.get('blocking_work_items')}`",
        f"- technical_blocking_work_items: `{summary.get('technical_blocking_work_items')}`",
        f"- delivery_pending_work_items: `{summary.get('delivery_pending_work_items')}`",
        f"- untracked_agent_surface_followups: `{summary.get('untracked_agent_surface_followups')}`",
        f"- open_by_priority: `{json.dumps(summary.get('open_by_priority') or {}, ensure_ascii=False, sort_keys=True)}`",
        f"- open_by_planned_release: `{json.dumps(summary.get('open_by_planned_release') or {}, ensure_ascii=False, sort_keys=True)}`",
        f"- closed_by_delivered_release: `{json.dumps(summary.get('closed_by_delivered_release') or {}, ensure_ascii=False, sort_keys=True)}`",
        f"- closed_delivery_variances: `{summary.get('closed_delivery_variances')}`",
        f"- current_execution_wave: `{summary.get('current_execution_wave')}`",
        f"- next_open_delivery_wave: `{summary.get('next_open_delivery_wave')}`",
        f"- next_technical_development_wave: `{summary.get('next_technical_development_wave')}`",
        f"- successor_selection_status: `{summary.get('successor_selection_status')}`",
        f"- selected_successor_work_item_id: `{summary.get('selected_successor_work_item_id') or 'not_selected'}`",
        f"- successor_top_candidate_work_items: `{summary.get('successor_top_candidate_work_items')}`",
        f"- successor_human_choice_present: `{summary.get('successor_human_choice_present')}`",
        f"- successor_human_choice_applied: `{summary.get('successor_human_choice_applied')}`",
        f"- release_train_status: `{summary.get('release_train_status')}`",
        f"- release_train_gated_release: `{summary.get('release_train_gated_release') or 'none'}`",
        f"- release_train_blocking_ready_work_items: `{summary.get('release_train_blocking_ready_work_items')}`",
        f"- release_train_action_required: `{summary.get('release_train_action_required')}`",
        f"- later_release_activation_allowed: `{summary.get('later_release_activation_allowed')}`",
        f"- release_delivery_status: `{summary.get('release_delivery_status')}`",
        f"- roadmap_phase: `{summary.get('roadmap_phase')}`",
        f"- concrete_release: `{summary.get('concrete_release') or 'not_selected'}`",
        f"- publication_target_status: `{summary.get('publication_target_status')}`",
        f"- semver_candidate_status: `{summary.get('semver_candidate_status')}`",
        f"- recommended_concrete_release: `{summary.get('recommended_concrete_release') or 'not_available'}`",
        f"- semver_candidate_work_items: `{summary.get('semver_candidate_work_items')}`",
        f"- bounded_package_work_items: `{summary.get('bounded_package_work_items')}`",
        "",
        "## Rule",
        "",
        f"- {payload.get('rule')}",
        "",
    ]
    execution_plan = payload.get("execution_plan") if isinstance(payload.get("execution_plan"), dict) else {}
    lines.extend(["## Execution Waves", ""])
    for wave in execution_plan.get("waves", []):
        if not isinstance(wave, dict):
            continue
        lines.append(
            f"- `{wave.get('id')}` `{wave.get('computed_status')}`: "
            f"delivery_open={wave.get('open_work_items')}/{wave.get('work_items')}, "
            f"technical_blockers={wave.get('technical_blocking_work_items')} - {wave.get('title')}"
        )
    successor = (
        execution_plan.get("successor_selection")
        if isinstance(execution_plan.get("successor_selection"), dict)
        else {}
    )
    lines.extend(
        [
            "",
            "## Successor Selection",
            "",
            f"- status: `{successor.get('status') or 'not_available'}`",
            f"- selected_work_item_id: `{successor.get('selected_work_item_id') or 'not_selected'}`",
            f"- reason_codes: `{json.dumps(successor.get('reason_codes') or [], ensure_ascii=False)}`",
            f"- top_candidate_work_item_ids: `{json.dumps(successor.get('top_candidate_work_item_ids') or [], ensure_ascii=False)}`",
            f"- transition_validation_eligible: `{successor.get('transition_validation_eligible') is True}`",
            f"- human_choice_decision: `{json.dumps(successor.get('human_choice_decision') or {}, ensure_ascii=False, sort_keys=True)}`",
            f"- release_train: `{json.dumps(successor.get('release_train') or {}, ensure_ascii=False, sort_keys=True)}`",
        ]
    )
    lines.extend(["", "## Open Items", ""])
    open_items = payload.get("open_items") if isinstance(payload.get("open_items"), list) else []
    if not open_items:
        lines.append("- none")
    for row in open_items:
        lines.extend(
            [
                f"- `{row.get('priority')}` `{row.get('id')}` ({row.get('owner_layer')}): {row.get('problem')}",
                f"  - status: `{row.get('status')}`",
                f"  - target_release: `{row.get('target_release')}`",
                f"  - next_action: {row.get('next_action')}",
                f"  - evidence: `{row.get('evidence')}`",
            ]
        )
    if payload.get("untracked_agent_surface_followups"):
        lines.extend(["", "## Untracked Agent Surface Followups", ""])
        for item in payload["untracked_agent_surface_followups"]:
            lines.append(f"- `{item}`")
    delivery_variances = payload.get("delivery_variances") if isinstance(payload.get("delivery_variances"), list) else []
    if delivery_variances:
        lines.extend(["", "## Planned And Delivered Release Variance", ""])
        for row in delivery_variances:
            lines.append(
                f"- `{row.get('id')}`: planned_release=`{row.get('target_release')}`, "
                f"delivered_release=`{row.get('delivered_release')}`, delivered_at=`{row.get('delivered_at') or 'not_recorded'}`"
            )
    return "\n".join(lines) + "\n"


def run() -> dict[str, Any]:
    payload = build_report()
    save_json_atomic(RAW_PATH, payload)
    save_text_atomic(REPORT_PATH, render_markdown(payload))
    return payload


def main() -> int:
    payload = run()
    print(json.dumps(payload["summary"], ensure_ascii=False, sort_keys=True))
    return 0 if payload["summary"]["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
