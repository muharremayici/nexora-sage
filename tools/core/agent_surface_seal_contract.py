from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.core.config import CONFIG_DIR
from tools.core.json_io import load_json_object_strict


AGENT_SURFACE_SEAL_CONTRACT_PATH = CONFIG_DIR / "agent_surface_seal_contract.json"
AGENT_SURFACE_MANUAL_REVIEW_EVIDENCE_PATH = CONFIG_DIR / "agent_surface_manual_review_evidence.json"

EFFECTIVE_HUMAN_SEAL_STATUSES = frozenset(
    {"human_sealed", "human_sealed_current", "human_sealed_proof_refresh_required"}
)


def is_effective_human_seal(status: Any) -> bool:
    """Recognize only ledger-backed statuses that represent a current human seal."""
    return str(status or "") in EFFECTIVE_HUMAN_SEAL_STATUSES


def derive_seal_currentness(summary: dict[str, Any]) -> dict[str, Any]:
    status = str(summary.get("seal_impact_status") or "unknown")
    highest = str(summary.get("highest_impact_level") or "")
    blocking_machine = bool(summary.get("blocking_for_machine_readiness"))
    blocking_release = bool(summary.get("blocking_for_public_release"))
    seal_current = (
        status in {"seal_still_current", "no_seal_impact"}
        and not blocking_machine
        and not blocking_release
    )
    proof_refresh_required = (
        status == "proof_refresh_required"
        and summary.get("human_action") == "machine_proof_refresh_required"
        and not blocking_machine
        and not blocking_release
    )
    human_authority_current = seal_current or proof_refresh_required
    if status == "unknown":
        effective = "unknown"
    elif seal_current:
        effective = "current"
    else:
        effective = status
    return {
        "seal_currentness_status": effective,
        "seal_impact_status": status,
        "seal_impact_highest_level": highest,
        "seal_current": seal_current,
        "human_seal_authority_current": human_authority_current,
        "machine_proof_refresh_required": proof_refresh_required,
        "seal_currentness_blocks_machine_readiness": blocking_machine,
        "seal_currentness_blocks_public_release": blocking_release,
        "seal_currentness_human_action": summary.get("human_action"),
        "seal_currentness_matched_changes": summary.get("matched_changes"),
    }


def project_effective_human_seal_status(
    human_seal_status: str,
    currentness: dict[str, Any],
    *,
    prerequisite_satisfied: bool = True,
    prerequisite_failure_status: str = "prerequisite_required",
) -> str:
    """Project historical approval through the latest seal-impact evidence."""
    if human_seal_status != "human_sealed":
        return "not_human_sealed"
    status = str(currentness.get("seal_currentness_status") or "unknown")
    if status in {"human_reseal_required", "human_review_required"}:
        return status
    if not prerequisite_satisfied:
        return prerequisite_failure_status
    if currentness.get("seal_current"):
        return "human_sealed_current"
    if currentness.get("machine_proof_refresh_required"):
        return "human_sealed_proof_refresh_required"
    if status == "proof_refresh_required":
        return status
    return "human_seal_currentness_unknown"


def load_agent_surface_seal_contract(path: Path | None = None) -> dict[str, Any]:
    return load_json_object_strict(
        path or AGENT_SURFACE_SEAL_CONTRACT_PATH,
        label="Agent surface seal contract",
    )


def load_agent_surface_seal_families(path: Path | None = None) -> list[dict[str, Any]]:
    contract = load_agent_surface_seal_contract(path)
    rows = contract.get("families", [])
    return [row for row in rows if isinstance(row, dict)]


def load_agent_surface_semantic_family_checks(path: Path | None = None) -> dict[str, list[str]]:
    contract = load_agent_surface_seal_contract(path)
    rows = contract.get("semantic_family_checks", {})
    if not isinstance(rows, dict):
        return {}
    result: dict[str, list[str]] = {}
    for family_id, checks in rows.items():
        if isinstance(checks, list):
            result[str(family_id)] = [str(check) for check in checks if str(check).strip()]
    return result


def load_agent_surface_required_upstream_artifacts(path: Path | None = None) -> list[dict[str, Any]]:
    contract = load_agent_surface_seal_contract(path)
    rows = contract.get("required_upstream_artifacts", [])
    return [row for row in rows if isinstance(row, dict)]


def load_agent_surface_manual_review_evidence(path: Path | None = None) -> dict[str, Any]:
    return load_json_object_strict(
        path or AGENT_SURFACE_MANUAL_REVIEW_EVIDENCE_PATH,
        label="Agent surface manual review evidence",
    )
