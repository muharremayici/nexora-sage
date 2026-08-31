from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.core.config import CONFIG_DIR, RAW_DIR
from tools.core.json_io import load_json_file


SAGE_ON_SAGE = "SAGE_ON_SAGE"
SAGE_ON_REPOSITORY = "SAGE_ON_REPOSITORY"


def seal_impact_summary(raw_dir: Path | None = None) -> dict[str, Any]:
    payload = load_json_file((raw_dir or RAW_DIR) / "seal_impact_validation.json", {})
    summary = payload.get("summary", {}) if isinstance(payload, dict) else {}
    return summary if isinstance(summary, dict) else {}


def seal_impact_policy() -> dict[str, Any]:
    payload = load_json_file(CONFIG_DIR / "seal_impact_policy.json", {})
    return payload if isinstance(payload, dict) else {}


def mutating_agent_surface_block(
    *,
    system_scope: str,
    raw_dir: Path | None = None,
    subject_root: str = "",
) -> dict[str, Any]:
    scope = str(system_scope or "").strip()
    artifact_root = str((raw_dir or RAW_DIR).resolve())
    common = {
        "system_scope": scope or "UNKNOWN",
        "subject_root": str(subject_root or ""),
        "artifact_root": artifact_root,
    }
    if scope == SAGE_ON_REPOSITORY:
        return {
            **common,
            "blocked": False,
            "seal_impact_status": "not_applicable",
            "highest_impact_level": "not_applicable",
            "active_seal_scopes": [],
            "matched_changes": 0,
            "message": "SAGE release seals do not authorize or block target-repository mutation.",
            "report": "",
            "authority": "target_repository_policy_and_explicit_human_authority",
        }
    if scope != SAGE_ON_SAGE:
        return {
            **common,
            "blocked": True,
            "seal_impact_status": "invalid_governance_scope",
            "highest_impact_level": "unknown",
            "active_seal_scopes": [],
            "matched_changes": None,
            "message": "Mutation governance scope is unknown; no seal authority was inferred.",
            "report": "",
            "authority": "unresolved_fail_closed",
        }

    summary = seal_impact_summary(raw_dir)
    policy = seal_impact_policy()
    status = str(summary.get("seal_impact_status") or "")
    enforcement = policy.get("enforcement", {}) if isinstance(policy.get("enforcement"), dict) else {}
    rule = enforcement.get(status, {}) if isinstance(enforcement.get(status), dict) else {}
    blocked = bool(rule.get("block_mutating_agent_surfaces"))
    return {
        **common,
        "blocked": blocked,
        "seal_impact_status": status or "unknown",
        "highest_impact_level": summary.get("highest_impact_level"),
        "active_seal_scopes": summary.get("active_seal_scopes"),
        "matched_changes": summary.get("matched_changes"),
        "message": rule.get("agent_message") or "",
        "report": "output/reports/seal_impact_validation.md",
        "authority": "sage_release_seal",
    }
