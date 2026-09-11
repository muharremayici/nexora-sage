from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.core.hitl_ledger_state import effective_active_approvals, ledger_entries

from tools.core.config import PROFILES_FILE
from tools.core.json_io import load_json_object_strict_cached


INSUFFICIENT_SOURCE_EVIDENCE = "INSUFFICIENT_SOURCE_EVIDENCE"
ARCHITECTURE_PROPOSAL_EVIDENCE_PREFIX = "architecture_proposal_identity:"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _approval_proposal_identities(entry: dict[str, Any]) -> set[str]:
    identities: set[str] = set()
    for raw in entry.get("evidence", []) if isinstance(entry.get("evidence"), list) else []:
        value = str(raw or "").strip()
        if value.startswith(ARCHITECTURE_PROPOSAL_EVIDENCE_PREFIX):
            identity = value[len(ARCHITECTURE_PROPOSAL_EVIDENCE_PREFIX):].strip()
            if identity:
                identities.add(identity)
    return identities


def build_effective_architecture_policy(
    oracle: dict[str, Any] | None,
    approval_ledger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Materialize post-Atlas architecture policy without promoting advisory evidence."""

    oracle_payload = oracle if isinstance(oracle, dict) else {}
    active_approvals = [
        entry
        for entry in effective_active_approvals(ledger_entries(approval_ledger or {}))
        if entry.get("gate") == "architecture_doctrine_seal"
    ]
    approvals_by_scope: dict[str, list[dict[str, Any]]] = {}
    for entry in active_approvals:
        scope = str(entry.get("scope") or "")
        if scope:
            approvals_by_scope.setdefault(scope, []).append(entry)

    projects: dict[str, dict[str, Any]] = {}
    active_projects: list[str] = []
    awaiting_projects: list[str] = []
    blocked_projects: list[str] = []
    stale_approval_projects: list[str] = []
    for project in oracle_payload.get("projects", []) if isinstance(oracle_payload.get("projects"), list) else []:
        if not isinstance(project, dict):
            continue
        project_id = str(project.get("project") or "")
        if not project_id:
            continue
        proposal_identity = str(project.get("proposal_identity") or "")
        identity_bound = project.get("proposal_identity_status") == "BOUND" and bool(proposal_identity)
        scoped_approvals = approvals_by_scope.get(project_id, [])
        matching_approval = next(
            (
                entry
                for entry in scoped_approvals
                if proposal_identity in _approval_proposal_identities(entry)
            ),
            None,
        ) if identity_bound else None
        language_capability = (
            project.get("project_language_capability")
            if isinstance(project.get("project_language_capability"), dict)
            else {}
        )
        language_status = str(language_capability.get("status") or "UNAVAILABLE")
        evidence_sufficient = (
            project.get("classification_status") != INSUFFICIENT_SOURCE_EVIDENCE
            and identity_bound
            and bool(project.get("recommended_profile"))
        )
        engine_eligible = language_status == "ENGINE_AVAILABLE"
        language_activation_state = {
            "ENGINE_AVAILABLE": "eligible",
            "PARTIAL_ENGINE_COVERAGE": "partial_only",
            "RECOGNIZED_NO_ENGINE": "unavailable",
            "NO_SOURCE_LANGUAGE_EVIDENCE": "no_source_evidence",
        }.get(language_status, "unavailable")
        if not evidence_sufficient or not engine_eligible:
            status = "BLOCKED_INSUFFICIENT_EVIDENCE"
            blocked_projects.append(project_id)
        elif matching_approval:
            status = "ACTIVE"
            active_projects.append(project_id)
        elif scoped_approvals:
            status = "STALE_OR_UNBOUND_APPROVAL"
            stale_approval_projects.append(project_id)
        else:
            status = "ADVISORY_AWAITING_HITL"
            awaiting_projects.append(project_id)
        projects[project_id] = {
            "status": status,
            "policy_activation_allowed": status == "ACTIVE",
            "recommended_profile": project.get("recommended_profile"),
            "blueprint": project.get("blueprint") if isinstance(project.get("blueprint"), dict) else {},
            "proposal_identity_status": project.get("proposal_identity_status") or "UNAVAILABLE",
            "proposal_identity": proposal_identity or None,
            "approval_entry_id": matching_approval.get("id") if matching_approval else None,
            "project_system_kind": project.get("project_system_kind") or {},
            "project_language_capability": language_capability,
            "feature_flags": {
                "architecture_sensitive_rules": "enabled" if status == "ACTIVE" else "advisory_only",
                "language_engine_activation": language_activation_state,
            },
        }

    if active_projects and len(active_projects) == len(projects):
        status = "ACTIVE"
    elif active_projects:
        status = "PARTIALLY_ACTIVE"
    elif projects and (awaiting_projects or stale_approval_projects):
        status = "AWAITING_HITL"
    else:
        status = "INSUFFICIENT_EVIDENCE"
    repository_blueprint = (
        oracle_payload.get("repository_blueprint")
        if isinstance(oracle_payload.get("repository_blueprint"), dict)
        else {}
    )
    return {
        "meta": {
            "kind": "effective_architecture_policy",
            "version": "v1",
            "generated_at": _utc_now(),
            "generator": "tools.core.architecture_blueprints.build_effective_architecture_policy",
            "source_artifacts": [
                "output/.raw/architecture_oracle.json",
                "output/.raw/hitl_approval_ledger.json",
            ],
        },
        "summary": {
            "status": status,
            "projects": len(projects),
            "active_projects": sorted(active_projects),
            "awaiting_hitl_projects": sorted(awaiting_projects),
            "blocked_projects": sorted(blocked_projects),
            "stale_or_unbound_approval_projects": sorted(stale_approval_projects),
            "policy_activation_allowed": bool(projects) and len(active_projects) == len(projects),
        },
        "projects": projects,
        "repository_policy": {
            "status": "ADVISORY_ONLY",
            "policy_activation_allowed": False,
            "proposal_identity": repository_blueprint.get("proposal_identity"),
            "proposal_identity_status": repository_blueprint.get("proposal_identity_status") or "UNAVAILABLE",
            "composition_model": repository_blueprint.get("composition_model"),
            "relationship_model": repository_blueprint.get("relationship_model"),
        },
        "invalidation": {
            "atlas_snapshot_id": ((repository_blueprint.get("evidence") or {}).get("snapshot_binding") or {}).get("atlas_snapshot_id"),
            "scope_authority_id": (oracle_payload.get("scope_authority") or {}).get("scope_authority_id"),
            "project_system_kind_identity": (oracle_payload.get("scope_authority") or {}).get("project_system_kind_identity"),
            "project_language_capability_identity": (oracle_payload.get("scope_authority") or {}).get("project_language_capability_identity"),
            "rule": "Any changed identity invalidates the proposal and requires a new exact HITL decision.",
        },
        "claim_boundary": (
            "Recognition and Oracle proposals are advisory. Architecture-sensitive activation requires "
            "an engine-eligible project, a bound post-Atlas proposal identity and an active project-scoped "
            "HITL approval carrying that exact identity. Repository-composition enforcement is not active."
        ),
    }


def resolve_effective_architecture_project(
    effective_policy: dict[str, Any] | None,
    project_id: str,
    *,
    expected_snapshot_id: str = "",
) -> dict[str, Any]:
    """Resolve one exact project's snapshot-bound architecture activation state."""

    policy = effective_policy if isinstance(effective_policy, dict) else {}
    meta = policy.get("meta") if isinstance(policy.get("meta"), dict) else {}
    invalidation = policy.get("invalidation") if isinstance(policy.get("invalidation"), dict) else {}
    projects = policy.get("projects") if isinstance(policy.get("projects"), dict) else {}
    project_policy = (
        projects.get(str(project_id))
        if isinstance(projects.get(str(project_id)), dict)
        else {}
    )
    observed_snapshot_id = str(invalidation.get("atlas_snapshot_id") or "")
    if not expected_snapshot_id:
        status = "ATLAS_SNAPSHOT_UNAVAILABLE"
    elif meta.get("kind") != "effective_architecture_policy" or meta.get("version") != "v1":
        status = "UNAVAILABLE"
    elif observed_snapshot_id != str(expected_snapshot_id):
        status = "STALE_SNAPSHOT"
    elif not project_policy:
        status = "PROJECT_UNAVAILABLE"
    else:
        status = str(project_policy.get("status") or "UNAVAILABLE")
    flags = (
        project_policy.get("feature_flags")
        if isinstance(project_policy.get("feature_flags"), dict)
        else {}
    )
    activation_allowed = (
        status == "ACTIVE"
        and project_policy.get("policy_activation_allowed") is True
        and flags.get("architecture_sensitive_rules") == "enabled"
    )
    return {
        "project": str(project_id),
        "effective_policy_status": status,
        "expected_snapshot_id": str(expected_snapshot_id or "") or None,
        "observed_snapshot_id": observed_snapshot_id or None,
        "recommended_profile": project_policy.get("recommended_profile"),
        "architecture_sensitive_rules_enabled": activation_allowed,
        "feature_flags": flags,
    }


def load_effective_architecture_policy_context(
    raw_dir: Path | None = None,
) -> tuple[dict[str, Any], str]:
    """Load the SQLite-first effective policy and its complete Atlas subject."""

    from tools.core.analysis_snapshot_lineage import load_atlas_commit
    from tools.core.config import RAW_DIR
    from tools.core.json_io import load_raw_artifact_path

    selected_raw_dir = Path(raw_dir or RAW_DIR)
    if selected_raw_dir.resolve() == RAW_DIR.resolve():
        from tools.core.artifact_store import STORE

        payload = STORE.load_raw("effective_architecture_policy", {})
    else:
        payload = load_raw_artifact_path(
            selected_raw_dir / "effective_architecture_policy.json",
            {},
        )
    policy = payload if isinstance(payload, dict) else {}
    atlas_commit = load_atlas_commit(selected_raw_dir)
    snapshot_id = (
        str(atlas_commit.get("snapshot_id") or "")
        if atlas_commit.get("state") == "complete"
        else ""
    )
    return policy, snapshot_id


def render_effective_architecture_policy(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    lines = [
        "# Effective Architecture Policy",
        "",
        f"- status: `{summary.get('status')}`",
        f"- projects: `{summary.get('projects')}`",
        f"- policy activation allowed: `{summary.get('policy_activation_allowed')}`",
        "",
        "| Project | Status | Profile | Architecture rules | Language engine |",
        "|---|---|---|---|---|",
    ]
    for project_id, project in sorted((payload.get("projects") or {}).items()):
        flags = project.get("feature_flags") if isinstance(project.get("feature_flags"), dict) else {}
        lines.append(
            f"| `{project_id}` | `{project.get('status')}` | `{project.get('recommended_profile')}` | "
            f"`{flags.get('architecture_sensitive_rules')}` | `{flags.get('language_engine_activation')}` |"
        )
    lines.extend(["", "## Boundary", "", f"- {payload.get('claim_boundary')}"])
    return "\n".join(lines) + "\n"


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
    effective_policy: dict[str, Any] | None = None,
    *,
    max_projects: int = 5,
    project_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Project Post-Atlas architecture evidence and human seal truth for agents."""
    oracle_payload = oracle if isinstance(oracle, dict) else {}
    ledger_payload = approval_ledger if isinstance(approval_ledger, dict) else {}
    effective_policy_payload = effective_policy if isinstance(effective_policy, dict) else {}
    effective_policy_projects = (
        effective_policy_payload.get("projects")
        if isinstance(effective_policy_payload.get("projects"), dict)
        else {}
    )
    active_seals = [
        entry
        for entry in effective_active_approvals(ledger_entries(ledger_payload))
        if entry.get("gate") == "architecture_doctrine_seal"
    ]
    active_seals_by_scope = {
        str(entry.get("scope") or ""): entry
        for entry in active_seals
        if str(entry.get("scope") or "")
    }
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
        classification_status = str(project.get("classification_status") or "CLASSIFIED")
        evidence_is_sufficient = classification_status != INSUFFICIENT_SOURCE_EVIDENCE
        project_seal = active_seals_by_scope.get(project_id) if evidence_is_sufficient else None
        project_system_kind = (
            project.get("project_system_kind")
            if isinstance(project.get("project_system_kind"), dict)
            else {
                "kind": "unknown",
                "authority": "system_kind_not_available_to_agent_context",
                "confidence": "none",
                "evidence": [],
                "candidate_kinds": [],
            }
        )
        project_language_capability = (
            project.get("project_language_capability")
            if isinstance(project.get("project_language_capability"), dict)
            else {
                "contract": "project_language_engine_capability_v1",
                "status": "UNAVAILABLE",
                "recognized_language_families": [],
                "engine_available_language_families": [],
                "engine_unavailable_language_families": [],
                "recognition_does_not_authorize_engine_activation": True,
            }
        )
        project_effective_policy = (
            effective_policy_projects.get(project_id)
            if isinstance(effective_policy_projects.get(project_id), dict)
            else {}
        )
        projects.append({
            "project": project_id,
            "classification_status": classification_status,
            "project_system_kind": project_system_kind,
            "project_language_capability": project_language_capability,
            "recommended_profile": project.get("recommended_profile"),
            "topology": blueprint.get("topology"),
            "runtime": blueprint.get("runtime"),
            "repository_shape": blueprint.get("repository_shape"),
            "composition_model": blueprint.get("composition_model"),
            "confidence": project.get("confidence"),
            "seal_ready": bool(project.get("seal_ready")),
            "proposal_status": (project.get("seal_proposal") or {}).get("status"),
            "seal_state": (
                "EVIDENCE_INSUFFICIENT"
                if not evidence_is_sufficient
                else "HUMAN_SEALED" if project_seal else "PROPOSAL_ONLY"
            ),
            "seal_scope": str(project_seal.get("scope") or "") if project_seal else None,
            "effective_policy_status": project_effective_policy.get("status") or "NOT_MATERIALIZED",
            "policy_activation_allowed": project_effective_policy.get("policy_activation_allowed") is True,
            "feature_flags": (
                project_effective_policy.get("feature_flags")
                if isinstance(project_effective_policy.get("feature_flags"), dict)
                else {
                    "architecture_sensitive_rules": "advisory_only",
                    "language_engine_activation": "unavailable",
                }
            ),
        })

    projects = projects[: max(1, int(max_projects or 5))]
    summary = oracle_payload.get("summary") if isinstance(oracle_payload.get("summary"), dict) else {}
    sealed_project_count = sum(project["seal_state"] == "HUMAN_SEALED" for project in projects)
    all_selected_projects_sealed = bool(projects) and sealed_project_count == len(projects)
    activated_project_count = sum(project["policy_activation_allowed"] for project in projects)
    all_selected_projects_activated = bool(projects) and activated_project_count == len(projects)
    matched_scopes = {project["project"] for project in projects if project["seal_state"] == "HUMAN_SEALED"}
    repository_blueprint = (
        dict(oracle_payload.get("repository_blueprint"))
        if isinstance(oracle_payload.get("repository_blueprint"), dict)
        else None
    )
    repository_blueprint_projects = {
        str(project)
        for project in ((repository_blueprint or {}).get("project_ids") or [])
        if str(project)
    }
    repository_blueprint_applicability = (
        "NOT_GENERATED"
        if repository_blueprint is None
        else "PROJECT_FILTER_SCOPE_MISMATCH"
        if project_ids and set(project_ids) != repository_blueprint_projects
        else "MATCHING_CONTEXT"
    )
    return {
        "oracle_status": summary.get("status") or "NOT_GENERATED",
        "seal_state": "HUMAN_SEALED" if all_selected_projects_sealed else "PROPOSAL_ONLY",
        "active_seal_approvals": sealed_project_count,
        "unmatched_active_seal_approvals": len(active_seals) - len(matched_scopes),
        "sealed_project_count": sealed_project_count,
        "selected_project_count": len(projects),
        "effective_policy_status": (
            (effective_policy_payload.get("summary") or {}).get("status") or "UNAVAILABLE"
            if effective_policy_payload
            else "NOT_MATERIALIZED"
        ),
        "policy_activation_allowed": all_selected_projects_activated,
        "activated_project_count": activated_project_count,
        "top_recommended_profile": summary.get("top_recommended_profile"),
        "repository_blueprint": repository_blueprint,
        "repository_blueprint_applicability": repository_blueprint_applicability,
        "projects": projects,
        "agent_rule": (
            "Treat blueprint coordinates as Post-Atlas evidence. An exact project-scoped human seal "
            "approves that proposal only; it does not prove a compiled or activated effective policy. "
            "A human seal cannot replace insufficient Atlas source evidence. "
            "A repository blueprint applies only to its exact project set and remains advisory. "
            "Do not enforce Oracle proposals unless policy_activation_allowed is true for the exact project."
        ),
        "source_artifacts": [
            "output/.raw/architecture_oracle.json",
            "output/.raw/effective_architecture_policy.json",
            "config/architecture_doctrine.json",
            "output/.raw/hitl_approval_ledger.json",
        ],
        "mcp_tool": "get_architecture_oracle",
    }
