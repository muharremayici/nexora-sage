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
from tools.core.json_io import load_json_object_strict, load_json_file
from tools.core.evidence_status import evidence_passed
from tools.core.distribution_policy import is_clean_install_root
from tools.core.release_proof_steps import load_release_proof_steps
from tools.core.execution_waves import project_execution_waves
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
    """Only required upstream proof may support readiness; no self/downstream cycle."""
    steps = {step["id"]: step for step in load_release_proof_steps()}
    consumer = "sage_work_item_registry_validation"
    pending = list(steps[consumer].get("depends_on", []))
    ancestors: set[str] = set()
    while pending:
        step_id = pending.pop()
        if step_id == consumer:
            raise ValueError("Work-item technical evidence has a proof dependency cycle")
        if step_id in ancestors:
            continue
        ancestors.add(step_id)
        pending.extend(steps[step_id].get("depends_on", []))
    return {
        "output/.raw/" + step["raw_artifact"].name
        for step_id in ancestors for step in [steps[step_id]]
        if step.get("required") and step.get("raw_artifact")
        and step["raw_artifact"].parent == RAW_DIR
    }


def assess_delivery_readiness(row: dict[str, Any], *, root: Path = ROOT) -> dict[str, Any]:
    """Assess technical evidence, never shipment or release authorization.

    Pre-seal independently requires current full source-bound proof. This
    assessment alone cannot substitute for that proof or human authority.
    """
    result = {"id": row.get("id"), "ready": False, "errors": [], "runtime_evidence_checked": False}
    if row.get("status") != "ready_for_delivery":
        return result
    completion = row.get("technical_completion")
    if not isinstance(completion, dict) or not isinstance(completion.get("review"), str) or not completion["review"].strip():
        result["errors"].append("missing_technical_review")
        return result
    references = completion.get("validation_artifacts")
    if not isinstance(references, list) or not references or not all(isinstance(ref, str) for ref in references):
        result["errors"].append("missing_validation_artifact_references")
        return result
    allowed = _technical_evidence_artifacts()
    unavailable = False
    for reference in references:
        if reference not in allowed:
            result["errors"].append(f"not_required_upstream_proof_artifact:{reference}")
            continue
        path = (root / reference).resolve()
        if not path.is_relative_to(root.resolve()):
            result["errors"].append(f"evidence_path_escapes_root:{reference}")
            continue
        if not path.is_file() and is_clean_install_root(root):
            unavailable = True
            continue
        if not evidence_passed(load_json_file(path, {}), default=False):
            result["errors"].append(f"validation_not_passed:{reference}")
    result["runtime_evidence_checked"] = not unavailable
    result["ready"] = not result["errors"] and not unavailable
    return result


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
    execution_plan = project_execution_waves(work_items, active_package=active_work_package())
    payload = {
        "meta": {
            "kind": "sage_work_item_report",
            "version": "v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "generator": "tools.generate_sage_work_item_report",
        },
        "summary": {
            "status": "PASS" if not untracked_agent_surface_followups and not any(
                assessment["errors"] for assessment in delivery_assessments
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
        "rule": "Open work is grouped by planned target_release; closed work is grouped by actual delivered_release. Planning history and shipped scope are never inferred from one another.",
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
            f"{wave.get('open_work_items')}/{wave.get('work_items')} open - {wave.get('title')}"
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
