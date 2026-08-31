from __future__ import annotations

from typing import Any

from tools.core.config import RAW_DIR
from tools.core.json_io import load_json_file
from tools.core.reality_scope import capability_scope
from tools.engines.capability_activation_planner import build_capability_activation_plan


def load_capability_activation_plan(*, regenerate: bool = False) -> dict[str, Any]:
    if not regenerate:
        payload = load_json_file(RAW_DIR / "capability_activation_plan.json", {})
        if isinstance(payload, dict) and payload.get("meta", {}).get("kind") == "capability_activation_plan":
            return payload
    return build_capability_activation_plan()


def compact_activation_summary(
    plan: dict[str, Any],
    *,
    allowed_system_scopes: set[str] | None = None,
) -> dict[str, Any]:
    summary = plan.get("summary", {}) if isinstance(plan.get("summary"), dict) else {}
    def _visible_ids(key: str) -> list[str]:
        values = [str(item) for item in summary.get(key, []) if str(item).strip()]
        if allowed_system_scopes is None:
            return values
        return [item for item in values if capability_scope(item) in allowed_system_scopes]
    return {
        "status": summary.get("status"),
        "projects": summary.get("projects"),
        "capabilities": summary.get("capabilities"),
        "planning_only": summary.get("planning_only"),
        "pipeline_scheduler_enforced": summary.get("pipeline_scheduler_enforced"),
        "disabled_semantics": summary.get("disabled_semantics"),
        "dependency_change_policy": summary.get("dependency_change_policy"),
        "enabled_capability_ids": _visible_ids("enabled_capability_ids"),
        "disabled_capability_ids": _visible_ids("disabled_capability_ids"),
        "system_scope_filter": sorted(allowed_system_scopes) if allowed_system_scopes is not None else [],
    }


def relevant_activation_context(
    plan: dict[str, Any],
    *,
    capability_ids: list[str] | None = None,
    limit: int = 8,
    max_projects: int = 3,
    include_disabled: bool = False,
    allowed_system_scopes: set[str] | None = None,
    project_ids: list[str] | None = None,
) -> dict[str, Any]:
    target_ids = {str(item) for item in capability_ids or [] if str(item).strip()}
    requested_projects = [str(item) for item in project_ids or [] if str(item).strip()]
    requested_project_set = set(requested_projects)
    project_rows = [
        project
        for project in (plan.get("projects", []) if isinstance(plan, dict) else [])
        if isinstance(project, dict)
        and (not requested_project_set or str(project.get("project") or "") in requested_project_set)
    ]
    if requested_projects:
        project_order = {project_id: index for index, project_id in enumerate(requested_projects)}
        project_rows.sort(key=lambda row: project_order.get(str(row.get("project") or ""), len(project_order)))
    rows: list[dict[str, Any]] = []
    for project in project_rows[: max(1, int(max_projects or 3))]:
        selected: list[dict[str, Any]] = []
        group_names = ["enabled_capabilities"]
        if include_disabled:
            group_names.append("disabled_capabilities")
        for group_name in group_names:
            for item in project.get(group_name, []) or []:
                if not isinstance(item, dict):
                    continue
                if target_ids and str(item.get("id")) not in target_ids:
                    continue
                if allowed_system_scopes is not None and capability_scope(str(item.get("id") or "")) not in allowed_system_scopes:
                    continue
                selected.append(
                    {
                        "id": item.get("id"),
                        "status": item.get("status"),
                        "reason": item.get("reason"),
                        "matched_signals": item.get("matched_signals", []),
                        "trusted_artifacts": item.get("trusted_artifacts", []),
                        "validators": item.get("validators", []),
                        "claim_boundary": item.get("claim_boundary"),
                    }
                )
        rows.append(
            {
                "project": project.get("project"),
                "display_name": project.get("display_name"),
                "detected_signals": project.get("detected_signals", []),
                "capabilities": selected[: max(1, int(limit or 8))],
            }
        )
    summary = compact_activation_summary(plan, allowed_system_scopes=allowed_system_scopes)
    summary["source_projects"] = summary.get("projects")
    summary["projects"] = len(rows)
    summary["project_ids"] = [str(row.get("project") or "") for row in rows]
    return {
        "summary": summary,
        "surface_policy": {
            "detail": "bounded_context_projection",
            "full_plan_artifact": "output/.raw/capability_activation_plan.json",
            "full_plan_mcp_tool": "get_capability_activation_plan",
            "disabled_rows_included": bool(include_disabled),
            "max_projects": max(1, int(max_projects or 3)),
            "system_scope_filter": sorted(allowed_system_scopes) if allowed_system_scopes is not None else [],
            "project_filter": requested_projects,
        },
        "projects": rows,
    }
