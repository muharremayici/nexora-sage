"""Independent technical-readiness evidence for SAGE roadmap work items."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.core.config import CODE_MAPS_DIR, RAW_DIR
from tools.core.distribution_policy import is_clean_install_root
from tools.core.evidence_status import evidence_passed
from tools.core.json_io import load_json_file
from tools.core.release_proof_steps import load_release_proof_steps


def technical_evidence_artifacts() -> set[str]:
    """Return required upstream proof artifacts allowed to support readiness."""
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
        for step_id in ancestors
        for step in [steps[step_id]]
        if step.get("required")
        and step.get("raw_artifact")
        and step["raw_artifact"].parent == RAW_DIR
    }


def _completion_references(row: dict[str, Any]) -> tuple[list[str], list[str]]:
    completion = row.get("technical_completion")
    if (not isinstance(completion, dict)
            or not isinstance(completion.get("review"), str)
            or not completion["review"].strip()):
        return [], ["missing_technical_review"]
    references = completion.get("validation_artifacts")
    if (not isinstance(references, list) or not references
            or not all(isinstance(reference, str) for reference in references)):
        return [], ["missing_validation_artifact_references"]
    return references, []


def assess_work_item_preparation(registry: dict[str, Any], *, root: Path = CODE_MAPS_DIR) -> dict[str, Any]:
    """Reject unfinished blocker metadata before freeze; never certify runtime evidence."""
    from tools.core.roadmap_phase_registry import release_impact_issues

    policy = registry.get("priority_policy") if isinstance(registry, dict) else None
    if not isinstance(policy, dict):
        raise ValueError("work_item_preparation_priority_policy_missing")
    for key in ("blocking_priorities", "deferred_priorities", "allowed_statuses"):
        values = policy.get(key)
        if (not isinstance(values, list) or not values
                or any(not isinstance(value, str) or not value for value in values)
                or len(set(values)) != len(values)):
            raise ValueError("work_item_preparation_priority_policy_invalid")
    if (set(policy["blocking_priorities"]) & set(policy["deferred_priorities"])
            or not {"closed", "ready_for_delivery"} <= set(policy["allowed_statuses"])):
        raise ValueError("work_item_preparation_priority_policy_invalid")
    rows = registry.get("work_items")
    if not isinstance(rows, list):
        raise ValueError("work_item_preparation_rows_invalid")
    priorities = set(policy["blocking_priorities"] + policy["deferred_priorities"])
    pending, seen, allowed = [], set(), None
    resolved_root = root.resolve()
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"].strip()
                or row["id"] in seen or not isinstance(row.get("priority"), str)
                or row["priority"] not in priorities or not isinstance(row.get("status"), str)
                or row["status"] not in policy["allowed_statuses"]
                or ("release_blocking" in row and type(row["release_blocking"]) is not bool)):
            raise ValueError("work_item_preparation_row_invalid")
        seen.add(row["id"])
        if row["status"] == "closed" or not (
                row["priority"] in policy["blocking_priorities"] or row.get("release_blocking") is True):
            continue
        references, errors = _completion_references(row) if row["status"] == "ready_for_delivery" else (
            [], ["technical_status_not_ready"])
        if not errors:
            if allowed is None:
                allowed = technical_evidence_artifacts()
            for reference in references:
                if reference not in allowed:
                    errors.append(f"not_required_upstream_proof_artifact:{reference}")
                elif not (root / reference).resolve().is_relative_to(resolved_root):
                    errors.append(f"evidence_path_escapes_root:{reference}")
            errors.extend(release_impact_issues(row))
        if errors:
            pending.append({"id": row["id"], "status": row["status"], "errors": errors})
    return {"status": "LIFECYCLE_METADATA_BLOCKED" if pending else "LIFECYCLE_METADATA_READY",
            "unresolved_work_item_count": len(pending), "unresolved_work_items": pending,
            "runtime_evidence_checked": False, "technical_delivery_readiness_established": False,
            "publication_authority": False}


def assess_work_item_technical_readiness(
    row: dict[str, Any],
    *,
    root: Path = CODE_MAPS_DIR,
    clean_install: bool | None = None,
) -> dict[str, Any]:
    """Assess technical evidence without granting shipment or release authority."""
    result = {
        "id": row.get("id"),
        "ready": False,
        "errors": [],
        "runtime_evidence_checked": False,
    }
    if row.get("status") != "ready_for_delivery":
        return result
    references, errors = _completion_references(row)
    if errors:
        result["errors"].extend(errors)
        return result

    allowed = technical_evidence_artifacts()
    unavailable = False
    clean_projection = is_clean_install_root(root) if clean_install is None else bool(clean_install)
    resolved_root = root.resolve()
    for reference in references:
        if reference not in allowed:
            result["errors"].append(f"not_required_upstream_proof_artifact:{reference}")
            continue
        path = (root / reference).resolve()
        if not path.is_relative_to(resolved_root):
            result["errors"].append(f"evidence_path_escapes_root:{reference}")
            continue
        if not path.is_file() and clean_projection:
            unavailable = True
            continue
        if not evidence_passed(load_json_file(path, {}), default=False):
            result["errors"].append(f"validation_not_passed:{reference}")
    result["runtime_evidence_checked"] = not unavailable
    result["ready"] = not result["errors"] and not unavailable
    return result
