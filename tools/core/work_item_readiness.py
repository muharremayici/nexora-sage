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
    completion = row.get("technical_completion")
    if (
        not isinstance(completion, dict)
        or not isinstance(completion.get("review"), str)
        or not completion["review"].strip()
    ):
        result["errors"].append("missing_technical_review")
        return result
    references = completion.get("validation_artifacts")
    if (
        not isinstance(references, list)
        or not references
        or not all(isinstance(reference, str) for reference in references)
    ):
        result["errors"].append("missing_validation_artifact_references")
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