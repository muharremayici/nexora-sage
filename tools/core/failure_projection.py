from __future__ import annotations

from typing import Any


def project_dependency_failures(steps: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Project failed proof steps into roots and dependency-derived cascades."""
    by_id = {str(step.get("id") or ""): step for step in steps if str(step.get("id") or "")}
    failed_ids = {step_id for step_id, step in by_id.items() if not bool(step.get("passed"))}

    def failed_ancestors(step_id: str, seen: set[str] | None = None) -> set[str]:
        seen = set() if seen is None else seen
        if step_id in seen:
            return set()
        seen.add(step_id)
        ancestors: set[str] = set()
        for dependency in by_id[step_id].get("depends_on", []) or []:
            dependency_id = str(dependency)
            if dependency_id not in by_id:
                continue
            if dependency_id in failed_ids:
                ancestors.add(dependency_id)
            ancestors.update(failed_ancestors(dependency_id, seen | {step_id}))
        return ancestors

    roots: list[dict[str, Any]] = []
    cascades: list[dict[str, Any]] = []
    for step_id in sorted(failed_ids):
        ancestors = failed_ancestors(step_id)
        row = {
            "step_id": step_id,
            "required": bool(by_id[step_id].get("required")),
            "dependency_ids": [str(value) for value in by_id[step_id].get("depends_on", []) or []],
        }
        if ancestors:
            row["root_cause_step_ids"] = sorted(
                ancestor for ancestor in ancestors if not failed_ancestors(ancestor)
            )
            cascades.append(row)
        else:
            roots.append(row)
    return {"root_causes": roots, "cascaded_failures": cascades}


def project_action_causes(actions: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Group explicit actions by declared shared causes without collapsing them."""
    groups: dict[str, list[str]] = {}
    for action in actions:
        cause_ids = sorted({str(value) for value in action.get("cause_ids", []) if str(value)})
        for cause_id in cause_ids:
            groups.setdefault(cause_id, []).append(str(action.get("id") or ""))
    return {
        "umbrella_categories": [
            {"cause_ids": [cause_id], "action_ids": sorted(action_ids)}
            for cause_id, action_ids in sorted(groups.items())
            if len(action_ids) > 1
        ]
    }
