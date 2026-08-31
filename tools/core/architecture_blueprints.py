from __future__ import annotations

from typing import Any

from tools.core.hitl_ledger_state import effective_active_approvals, ledger_entries

from tools.core.config import PROFILES_FILE
from tools.core.json_io import load_json_object_strict_cached


def load_blueprint_registry() -> dict[str, Any]:
    return load_json_object_strict_cached(PROFILES_FILE, label="Architecture blueprint registry")


def blueprint_contract() -> dict[str, Any]:
    contract = load_blueprint_registry().get("blueprint_contract", {})
    return contract if isinstance(contract, dict) else {}


def profile_aliases() -> dict[str, str]:
    aliases = blueprint_contract().get("aliases", {})
    return {str(key): str(value) for key, value in aliases.items()} if isinstance(aliases, dict) else {}


def canonical_profile_id(profile_id: str) -> str:
    current = str(profile_id or "").strip()
    aliases = profile_aliases()
    seen: set[str] = set()
    while current in aliases and current not in seen:
        seen.add(current)
        current = aliases[current]
    return current


def canonical_profiles() -> dict[str, dict[str, Any]]:
    profiles = blueprint_contract().get("canonical_profiles", {})
    if not isinstance(profiles, dict):
        return {}
    return {str(key): value for key, value in profiles.items() if isinstance(value, dict)}


def effective_profile_ids(profile_id: str) -> set[str]:
    canonical = canonical_profile_id(profile_id)
    profiles = canonical_profiles()
    effective = {canonical}
    pending = list(profiles.get(canonical, {}).get("implies", []) or [])
    legacy_families: set[str] = set()
    while pending:
        implied = canonical_profile_id(str(pending.pop()))
        if implied in effective:
            continue
        effective.add(implied)
        pending.extend(profiles.get(implied, {}).get("implies", []) or [])
    for profile in list(effective):
        legacy_families.update(str(item) for item in profiles.get(profile, {}).get("legacy_rule_families", []) or [])
    reverse_aliases = {
        alias for alias, target in profile_aliases().items()
        if canonical_profile_id(target) in effective
    }
    return effective | legacy_families | reverse_aliases


def blueprint_axes_valid(profile_id: str) -> bool:
    profile = canonical_profiles().get(canonical_profile_id(profile_id), {})
    axes = blueprint_contract().get("axes", {})
    if not profile or not isinstance(axes, dict):
        return False
    return all(
        profile.get(axis) in set(axes.get(axis, []) or [])
        for axis in ("topology", "runtime", "repository_shape", "composition_model")
    )


def blueprint_coordinates(profile_id: str) -> dict[str, Any]:
    canonical = canonical_profile_id(profile_id)
    profile = canonical_profiles().get(canonical, {})
    return {
        "canonical_profile": canonical,
        "topology": profile.get("topology"),
        "runtime": profile.get("runtime"),
        "repository_shape": profile.get("repository_shape"),
        "composition_model": profile.get("composition_model"),
        "evidence_status": profile.get("evidence_status"),
        "seal_policy": profile.get("seal_policy"),
    }


def architecture_governance_context(
    oracle: dict[str, Any] | None,
    approval_ledger: dict[str, Any] | None = None,
    *,
    max_projects: int = 5,
    project_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Project Post-Atlas architecture evidence and human seal truth for agents."""
    oracle_payload = oracle if isinstance(oracle, dict) else {}
    ledger_payload = approval_ledger if isinstance(approval_ledger, dict) else {}
    active_seals = [
        entry
        for entry in effective_active_approvals(ledger_entries(ledger_payload))
        if entry.get("gate") == "architecture_doctrine_seal"
    ]
    projects = []
    for project in oracle_payload.get("projects", []):
        if not isinstance(project, dict):
            continue
        project_id = str(project.get("project") or "")
        if project_ids and project_id not in project_ids:
            continue
        blueprint = project.get("blueprint") if isinstance(project.get("blueprint"), dict) else {}
        if not all(blueprint.get(axis) for axis in ("topology", "runtime", "repository_shape", "composition_model")):
            blueprint = blueprint_coordinates(str(project.get("recommended_profile") or ""))
        projects.append({
            "project": project_id,
            "recommended_profile": project.get("recommended_profile"),
            "topology": blueprint.get("topology"),
            "runtime": blueprint.get("runtime"),
            "repository_shape": blueprint.get("repository_shape"),
            "composition_model": blueprint.get("composition_model"),
            "confidence": project.get("confidence"),
            "seal_ready": bool(project.get("seal_ready")),
            "proposal_status": (project.get("seal_proposal") or {}).get("status"),
        })

    summary = oracle_payload.get("summary") if isinstance(oracle_payload.get("summary"), dict) else {}
    return {
        "oracle_status": summary.get("status") or "NOT_GENERATED",
        "seal_state": "HUMAN_SEALED" if active_seals else "PROPOSAL_ONLY",
        "active_seal_approvals": len(active_seals),
        "top_recommended_profile": summary.get("top_recommended_profile"),
        "projects": projects[: max(1, int(max_projects or 5))],
        "agent_rule": (
            "Treat blueprint coordinates as Post-Atlas evidence. Apply their compiled doctrine rules, "
            "but never describe an Oracle proposal as human-sealed unless seal_state is HUMAN_SEALED."
        ),
        "source_artifacts": [
            "output/.raw/architecture_oracle.json",
            "config/architecture_doctrine.json",
            "output/.raw/hitl_approval_ledger.json",
        ],
        "mcp_tool": "get_architecture_oracle",
    }
