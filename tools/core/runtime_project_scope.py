from __future__ import annotations

from typing import Any


_RUNTIME_PROJECT_FILTER: list[str] | None = None


def set_runtime_project_filter(projects: list[str] | None) -> None:
    global _RUNTIME_PROJECT_FILTER
    _RUNTIME_PROJECT_FILTER = list(projects) if isinstance(projects, list) else None


def get_runtime_project_filter() -> list[str] | None:
    return list(_RUNTIME_PROJECT_FILTER) if isinstance(_RUNTIME_PROJECT_FILTER, list) else None


def canonical_project_keys(atlas: dict[str, Any]) -> set[str]:
    return {
        str(project)
        for project, payload in atlas.items()
        if project != "symbols" and isinstance(payload, dict)
    }


def project_runtime_atlas(atlas: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Project persisted multi-project Atlas state onto the active execution scope."""

    available = sorted(canonical_project_keys(atlas))
    requested = sorted(
        {
            str(project).strip().upper()
            for project in (get_runtime_project_filter() or [])
            if str(project).strip()
        }
    )
    allowed = set(requested) if requested else set(available)
    analyzed = [project for project in available if project.upper() in allowed]
    preserved = [project for project in available if project not in analyzed]
    unavailable = [project for project in requested if project not in {item.upper() for item in available}]

    projected = {project: atlas[project] for project in analyzed}
    return projected, {
        "requested_projects": requested or available,
        "analyzed_projects": analyzed,
        "preserved_only_projects": preserved,
        "unavailable_requested_projects": unavailable,
        "aggregation_scope": "runtime_project_projection",
        "target_decision_eligible": not unavailable and bool(analyzed),
    }
