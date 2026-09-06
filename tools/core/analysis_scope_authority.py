from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from tools.core.repository_topology import (
    normalize_project_filter,
    runtime_project_projection,
    scope_authority_id,
)


COMPLETE_REPOSITORY = "COMPLETE_REPOSITORY"
BOUNDED_PROJECT_SELECTION = "BOUNDED_PROJECT_SELECTION"
INCOMPLETE_EVIDENCE = "INCOMPLETE_EVIDENCE"


def extract_scope_authority(payload: Any) -> dict[str, Any] | None:
    """Read either a scope artifact wrapper or an embedded authority object."""

    if not isinstance(payload, dict):
        return None
    nested = payload.get("scope_authority")
    if isinstance(nested, dict) and nested:
        return dict(nested)
    if payload.get("contract") == "repository_analysis_scope_authority_v1":
        return dict(payload)
    return None


def fail_closed_scope_authority(reason: str) -> dict[str, Any]:
    """Return a non-authoritative placeholder; consumers must never regenerate missing authority."""

    return {
        "contract": "repository_analysis_scope_authority_v1",
        "topology_authority_id": "",
        "scope_authority_id": "",
        "evidence_status": INCOMPLETE_EVIDENCE,
        "claim_scope": "incomplete_evidence_only",
        "full_repository_claim_eligible": False,
        "incomplete_reasons": [str(reason)],
        "effective_runtime_projects": {},
        "claim_eligible_source_file_count": 0,
        "layer_consistency": INCOMPLETE_EVIDENCE,
    }


def load_scope_authority_for_consumer(
    raw_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load the immutable post-Atlas authority instead of re-deriving it."""

    from tools.core.json_io import load_raw_artifact_path

    artifact = load_raw_artifact_path(
        Path(raw_dir) / "analysis_scope_authority.json",
        {},
    )
    artifact = artifact if isinstance(artifact, dict) else {}
    authority = extract_scope_authority(artifact)
    if authority is None:
        authority = fail_closed_scope_authority(
            "analysis_scope_authority_missing_at_consumer"
        )
    return artifact, authority


def topology_projection(dynamic_config: dict[str, Any]) -> dict[str, Any]:
    """Return the one topology projection used by default and explicit targets."""

    for key in ("_target_root_override", "_repository_topology"):
        value = dynamic_config.get(key)
        if isinstance(value, dict) and value:
            return value
    variations = dynamic_config.get("variations")
    selected = variations if isinstance(variations, dict) else {}
    roles = dynamic_config.get("project_roles")
    return {
        "status": "resolved" if selected else "unavailable",
        "ontology_contract": "compiled_variations_fallback_v1",
        "selection_mode": "compiled_runtime_fallback",
        "project_candidates": dict(selected),
        "project_candidate_relationship_roles": dict(roles) if isinstance(roles, dict) else {},
        "project_candidate_role_authority": {},
        "selected_projects": dict(selected),
        "excluded_projects": {},
        "excluded_project_reasons": {},
    }


def supported_source_count(
    language_counts: dict[str, Any],
    polyglot_capabilities: dict[str, Any],
) -> int:
    supported = polyglot_capabilities.get("languages")
    supported = set(supported) if isinstance(supported, dict) else set()
    return sum(
        max(0, int(count or 0))
        for language, count in (language_counts or {}).items()
        if str(language) in supported
    )


def build_preflight_scope_authority(
    *,
    topology: dict[str, Any],
    projects: Iterable[str] | str | None,
    repository_file_count: int,
    repository_inventory_truncated: bool,
    repository_language_counts: dict[str, Any],
    effective_file_count: int,
    effective_inventory_truncated: bool,
    effective_language_counts: dict[str, Any],
    effective_project_file_counts: dict[str, Any],
    polyglot_capabilities: dict[str, Any],
    repository_analysis_language_counts: dict[str, Any] | None = None,
    effective_analysis_language_counts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    runtime_projection = runtime_project_projection(topology, projects)
    requested = runtime_projection["requested_project_filter"]
    effective = runtime_projection["effective_runtime_projects"]
    unavailable = runtime_projection["unavailable_requested_projects"]
    candidates = topology.get("project_candidates")
    candidates = candidates if isinstance(candidates, dict) else {}
    selected = topology.get("selected_projects")
    selected = selected if isinstance(selected, dict) else {}
    excluded = topology.get("excluded_projects")
    excluded = excluded if isinstance(excluded, dict) else {}
    role_authority = topology.get("project_candidate_role_authority")
    role_authority = role_authority if isinstance(role_authority, dict) else {}
    unresolved_candidates = sorted(
        str(key)
        for key, value in role_authority.items()
        if isinstance(value, dict) and value.get("relationship_resolved") is not True
    )
    repository_observed_sources = sum(max(0, int(value or 0)) for value in repository_language_counts.values())
    effective_observed_sources = sum(max(0, int(value or 0)) for value in effective_language_counts.values())
    repository_analysis_languages = (
        repository_language_counts
        if repository_analysis_language_counts is None
        else repository_analysis_language_counts
    )
    effective_analysis_languages = (
        effective_language_counts
        if effective_analysis_language_counts is None
        else effective_analysis_language_counts
    )
    repository_supported_sources = supported_source_count(
        repository_analysis_languages,
        polyglot_capabilities,
    )
    effective_supported_sources = supported_source_count(
        effective_analysis_languages,
        polyglot_capabilities,
    )
    supported_source_gap = max(0, repository_supported_sources - effective_supported_sources)

    reasons: list[str] = []
    if unavailable:
        reasons.append("requested_project_filter_not_in_topology")
    if not effective:
        reasons.append("effective_runtime_project_set_empty")
    if effective_inventory_truncated:
        reasons.append("effective_scope_inventory_truncated")
    if not requested and repository_inventory_truncated:
        reasons.append("repository_inventory_truncated")
    if not requested and supported_source_gap:
        reasons.append("supported_source_outside_effective_scope")
    if not requested and excluded and supported_source_gap:
        reasons.append("material_excluded_project_candidates")

    if reasons:
        evidence_status = INCOMPLETE_EVIDENCE
    elif requested:
        evidence_status = BOUNDED_PROJECT_SELECTION
    else:
        evidence_status = COMPLETE_REPOSITORY

    return {
        "contract": "repository_analysis_scope_authority_v1",
        "topology_authority_id": str(
            topology.get("topology_authority_id") or scope_authority_id(topology, None)
        ),
        "scope_authority_id": scope_authority_id(topology, projects),
        "evidence_status": evidence_status,
        "claim_scope": (
            "supported_source_repository"
            if evidence_status == COMPLETE_REPOSITORY
            else "explicit_project_selection"
            if evidence_status == BOUNDED_PROJECT_SELECTION
            else "incomplete_evidence_only"
        ),
        "full_repository_claim_eligible": evidence_status == COMPLETE_REPOSITORY,
        "incomplete_reasons": sorted(set(reasons)),
        "discovered_candidate_count": len(candidates),
        "auto_selected_project_count": len(selected),
        "excluded_project_count": len(excluded),
        "unresolved_project_candidates": unresolved_candidates,
        **runtime_projection,
        "repository_inventory_file_count": max(0, int(repository_file_count or 0)),
        "repository_inventory_truncated": bool(repository_inventory_truncated),
        "repository_observed_source_file_count": repository_observed_sources,
        "repository_analysis_language_counts": dict(sorted(repository_analysis_languages.items())),
        "repository_supported_source_file_count": repository_supported_sources,
        "effective_inventory_file_count": max(0, int(effective_file_count or 0)),
        "effective_inventory_truncated": bool(effective_inventory_truncated),
        "effective_observed_source_file_count": effective_observed_sources,
        "effective_analysis_language_counts": dict(sorted(effective_analysis_languages.items())),
        "effective_supported_source_file_count": effective_supported_sources,
        "effective_project_file_counts": {
            str(key): max(0, int(value or 0))
            for key, value in (effective_project_file_counts or {}).items()
        },
        "supported_source_file_gap": supported_source_gap,
        "indexed_project_count": None,
        "indexed_source_file_count": None,
        "claim_eligible_source_file_count": None,
        "layer_consistency": "PRE_ATLAS",
    }


def bind_atlas_materialization(
    scope_authority: dict[str, Any],
    atlas: dict[str, Any],
) -> dict[str, Any]:
    """Bind Atlas materialization to the pre-Atlas authority without widening it."""

    payload = dict(scope_authority) if isinstance(scope_authority, dict) else {}
    effective = payload.get("effective_runtime_projects")
    effective = effective if isinstance(effective, dict) else {}
    expected = sorted(str(key) for key in effective)
    atlas_projects = {
        str(key): value
        for key, value in (atlas or {}).items()
        if key != "symbols" and isinstance(value, dict)
    }
    indexed = sorted(key for key in expected if key in atlas_projects)
    missing = sorted(set(expected) - set(indexed))
    file_counts = {
        key: len((atlas_projects.get(key) or {}).get("files", {}))
        for key in indexed
        if isinstance((atlas_projects.get(key) or {}).get("files", {}), dict)
    }
    indexed_source_count = sum(file_counts.values())
    reasons = list(payload.get("incomplete_reasons") or [])
    atlas_reasons: list[str] = []
    if missing:
        atlas_reasons.append("effective_projects_missing_from_atlas")
    if expected and not indexed_source_count and int(payload.get("effective_supported_source_file_count") or 0) > 0:
        atlas_reasons.append("supported_effective_scope_materialized_zero_source_files")
    if int(payload.get("effective_supported_source_file_count") or 0) > indexed_source_count:
        atlas_reasons.append("atlas_indexed_fewer_files_than_effective_supported_source_inventory")
    reasons.extend(atlas_reasons)
    if reasons:
        payload["evidence_status"] = INCOMPLETE_EVIDENCE
        payload["claim_scope"] = "incomplete_evidence_only"
        payload["full_repository_claim_eligible"] = False
    payload.update({
        "incomplete_reasons": sorted(set(reasons)),
        "indexed_projects": indexed,
        "missing_effective_projects_in_atlas": missing,
        "indexed_project_file_counts": file_counts,
        "indexed_project_count": len(indexed),
        "indexed_source_file_count": indexed_source_count,
        "claim_eligible_source_file_count": (
            indexed_source_count if payload.get("evidence_status") != INCOMPLETE_EVIDENCE else 0
        ),
        "atlas_consistency": "CONSISTENT" if not atlas_reasons else INCOMPLETE_EVIDENCE,
        "atlas_incomplete_reasons": sorted(set(atlas_reasons)),
        "layer_consistency": "CONSISTENT" if not atlas_reasons else INCOMPLETE_EVIDENCE,
    })
    return payload


def bind_consumer_projects(
    scope_authority: dict[str, Any],
    *,
    layer: str,
    observed_projects: Iterable[str],
    expected_projects: Iterable[str] | None = None,
) -> dict[str, Any]:
    payload = dict(scope_authority) if isinstance(scope_authority, dict) else {}
    observed = sorted({str(project) for project in observed_projects if str(project)})
    effective = payload.get("effective_runtime_projects")
    effective = effective if isinstance(effective, dict) else {}
    expected = sorted(
        {str(project) for project in (expected_projects if expected_projects is not None else effective) if str(project)}
    )
    missing = sorted(set(expected) - set(observed))
    unexpected = sorted(set(observed) - set(expected))
    reasons = list(payload.get("incomplete_reasons") or [])
    layer_mismatch = bool(missing or unexpected)
    if missing:
        reasons.append(f"{layer}_missing_authorized_projects")
    if unexpected:
        reasons.append(f"{layer}_observed_unauthorized_projects")
    if reasons:
        payload["evidence_status"] = INCOMPLETE_EVIDENCE
        payload["claim_scope"] = "incomplete_evidence_only"
        payload["full_repository_claim_eligible"] = False
        payload["claim_eligible_source_file_count"] = 0
    payload.update({
        "incomplete_reasons": sorted(set(reasons)),
        f"{layer}_expected_projects": expected,
        f"{layer}_observed_projects": observed,
        f"{layer}_missing_projects": missing,
        f"{layer}_unexpected_projects": unexpected,
        "layer_consistency": INCOMPLETE_EVIDENCE if layer_mismatch else "CONSISTENT",
    })
    return payload


def _fallback_scope_authority(
    topology: dict[str, Any],
    projects: Iterable[str] | str | None,
    *,
    external_preflight_missing: bool,
) -> dict[str, Any]:
    runtime_projection = runtime_project_projection(topology, projects)
    requested = runtime_projection["requested_project_filter"]
    effective = runtime_projection["effective_runtime_projects"]
    excluded = topology.get("excluded_projects")
    excluded = excluded if isinstance(excluded, dict) else {}
    reasons: list[str] = []
    if runtime_projection["unavailable_requested_projects"]:
        reasons.append("requested_project_filter_not_in_topology")
    if not effective:
        reasons.append("effective_runtime_project_set_empty")
    if external_preflight_missing:
        reasons.append("external_target_preflight_scope_authority_missing")
    if not requested and excluded:
        reasons.append("excluded_project_materiality_not_evaluated")
    if reasons:
        status = INCOMPLETE_EVIDENCE
    elif requested:
        status = BOUNDED_PROJECT_SELECTION
    else:
        status = COMPLETE_REPOSITORY
    return {
        "contract": "repository_analysis_scope_authority_v1",
        "topology_authority_id": str(
            topology.get("topology_authority_id") or scope_authority_id(topology, None)
        ),
        "scope_authority_id": scope_authority_id(topology, projects),
        "evidence_status": status,
        "claim_scope": (
            "supported_source_repository"
            if status == COMPLETE_REPOSITORY
            else "explicit_project_selection"
            if status == BOUNDED_PROJECT_SELECTION
            else "incomplete_evidence_only"
        ),
        "full_repository_claim_eligible": status == COMPLETE_REPOSITORY,
        "incomplete_reasons": sorted(set(reasons)),
        "discovered_candidate_count": len(topology.get("project_candidates") or {}),
        "auto_selected_project_count": len(topology.get("selected_projects") or {}),
        "excluded_project_count": len(excluded),
        "unresolved_project_candidates": sorted(
            str(key)
            for key, value in (topology.get("project_candidate_role_authority") or {}).items()
            if isinstance(value, dict) and value.get("relationship_resolved") is not True
        ),
        **runtime_projection,
        "repository_inventory_file_count": None,
        "repository_inventory_truncated": None,
        "repository_observed_source_file_count": None,
        "repository_analysis_language_counts": {},
        "repository_supported_source_file_count": None,
        "effective_inventory_file_count": None,
        "effective_inventory_truncated": None,
        "effective_observed_source_file_count": None,
        "effective_analysis_language_counts": {},
        "effective_supported_source_file_count": None,
        "effective_project_file_counts": {},
        "supported_source_file_gap": None,
        "indexed_project_count": None,
        "indexed_source_file_count": None,
        "claim_eligible_source_file_count": None,
        "layer_consistency": "PRE_ATLAS",
    }


def runtime_scope_authority(
    *,
    dynamic_config: dict[str, Any],
    projects: Iterable[str] | str | None,
    atlas: dict[str, Any] | None = None,
    raw_dir: Path | None = None,
) -> dict[str, Any]:
    """Resolve one runtime scope authority for Preflight, Atlas and consumers."""

    topology = topology_projection(dynamic_config)
    expected_topology_id = str(
        topology.get("topology_authority_id") or scope_authority_id(topology, None)
    )
    expected_id = scope_authority_id(topology, projects)
    external = isinstance(dynamic_config.get("_target_root_override"), dict)
    authority: dict[str, Any] = {}
    if external and raw_dir is not None:
        preflight_path = Path(raw_dir) / "external_target_preflight.json"
        if preflight_path.is_file() or (Path(raw_dir) / "codemaps.db").is_file():
            from tools.core.json_io import load_raw_artifact_path

            preflight = load_raw_artifact_path(preflight_path, {})
            candidate = (((preflight.get("summary") or {}).get("analysis_scope") or {}).get("scope_authority"))
            if isinstance(candidate, dict):
                authority = dict(candidate)
    if not authority:
        authority = _fallback_scope_authority(
            topology,
            projects,
            external_preflight_missing=external,
        )
    topology_identity_matches = (
        str(authority.get("topology_authority_id") or "") == expected_topology_id
    )
    scope_identity_matches = str(authority.get("scope_authority_id") or "") == expected_id
    if not topology_identity_matches or not scope_identity_matches:
        reasons = list(authority.get("incomplete_reasons") or [])
        if not topology_identity_matches:
            reasons.append("preflight_discovery_topology_authority_identity_mismatch")
        if not scope_identity_matches:
            reasons.append("preflight_discovery_scope_authority_identity_mismatch")
        authority.update({
            "topology_authority_id": expected_topology_id,
            "scope_authority_id": expected_id,
            "evidence_status": INCOMPLETE_EVIDENCE,
            "claim_scope": "incomplete_evidence_only",
            "full_repository_claim_eligible": False,
            "incomplete_reasons": sorted(set(reasons)),
            "layer_consistency": INCOMPLETE_EVIDENCE,
        })
    if atlas is not None:
        authority = bind_atlas_materialization(authority, atlas)
    return authority


def reconcile_consumer_scope_authorities(
    scope_authority: dict[str, Any],
    consumer_authorities: dict[str, dict[str, Any] | None],
) -> dict[str, Any]:
    """Fail closed when a completed consumer diverges from the shared scope identity."""

    payload = dict(scope_authority) if isinstance(scope_authority, dict) else {}
    expected_id = str(payload.get("scope_authority_id") or "")
    reasons = list(payload.get("incomplete_reasons") or [])
    existing_checks = payload.get("consumer_scope_checks")
    checks: dict[str, dict[str, Any]] = (
        dict(existing_checks) if isinstance(existing_checks, dict) else {}
    )
    for layer, candidate in sorted(consumer_authorities.items()):
        if not isinstance(candidate, dict) or not candidate:
            status = "MISSING"
            reasons.append(f"{layer}_scope_authority_missing")
            observed_id = ""
        else:
            observed_id = str(candidate.get("scope_authority_id") or "")
            if not observed_id or observed_id != expected_id:
                status = "IDENTITY_MISMATCH"
                reasons.append(f"{layer}_scope_authority_identity_mismatch")
            elif candidate.get("layer_consistency") == INCOMPLETE_EVIDENCE:
                status = "PROJECT_SET_MISMATCH"
                reasons.append(f"{layer}_scope_project_set_mismatch")
            elif (
                candidate.get("evidence_status") == INCOMPLETE_EVIDENCE
                and payload.get("evidence_status") != INCOMPLETE_EVIDENCE
            ):
                status = "EVIDENCE_DEGRADED"
                reasons.append(f"{layer}_scope_evidence_degraded")
            else:
                status = "CONSISTENT"
        checks[layer] = {
            "status": status,
            "expected_scope_authority_id": expected_id,
            "observed_scope_authority_id": observed_id,
        }

    mismatched = [layer for layer, check in checks.items() if check["status"] != "CONSISTENT"]
    if mismatched:
        payload.update({
            "evidence_status": INCOMPLETE_EVIDENCE,
            "claim_scope": "incomplete_evidence_only",
            "full_repository_claim_eligible": False,
            "claim_eligible_source_file_count": 0,
        })
    payload.update({
        "incomplete_reasons": sorted(set(reasons)),
        "consumer_scope_checks": checks,
        "consumer_scope_consistency": "CONSISTENT" if not mismatched else INCOMPLETE_EVIDENCE,
    })
    return payload


def reconcile_quality_scope_authority(
    scope_payload: Any,
    quality_gate: Any,
) -> tuple[dict[str, Any], bool]:
    """Bind a user-facing action surface to the exact Quality Gate scope identity."""

    shared = extract_scope_authority(scope_payload) or fail_closed_scope_authority(
        "analysis_scope_authority_missing_at_action_surface"
    )
    quality = quality_gate if isinstance(quality_gate, dict) else {}
    quality_authority = extract_scope_authority(quality.get("analysis_scope_authority"))
    reconciled = reconcile_consumer_scope_authorities(
        shared,
        {"quality_gate": quality_authority},
    )
    reasons = list(reconciled.get("incomplete_reasons") or [])
    if quality.get("scope_gate_status") != "PASS":
        reasons.append("quality_gate_scope_not_accepted")
    actionable = (
        bool(reconciled.get("scope_authority_id"))
        and reconciled.get("evidence_status")
        in {COMPLETE_REPOSITORY, BOUNDED_PROJECT_SELECTION}
        and reconciled.get("consumer_scope_consistency") == "CONSISTENT"
        and quality.get("scope_gate_status") == "PASS"
    )
    if not actionable:
        reconciled.update({
            "evidence_status": INCOMPLETE_EVIDENCE,
            "claim_scope": "incomplete_evidence_only",
            "full_repository_claim_eligible": False,
            "claim_eligible_source_file_count": 0,
        })
    reconciled["incomplete_reasons"] = sorted(set(reasons))
    return reconciled, actionable


def scope_receipt_details(scope_authority: dict[str, Any]) -> dict[str, Any]:
    """Flatten scope evidence into trace-safe terminal receipt fields."""

    payload = scope_authority if isinstance(scope_authority, dict) else {}
    effective = payload.get("effective_runtime_projects")
    effective = effective if isinstance(effective, dict) else {}
    unresolved = payload.get("unresolved_project_candidates")
    unresolved = unresolved if isinstance(unresolved, list) else []
    checks = payload.get("consumer_scope_checks")
    checks = checks if isinstance(checks, dict) else {}
    return {
        "scope_topology_authority_id": str(
            payload.get("topology_authority_id") or "not_available"
        ),
        "scope_authority_id": str(payload.get("scope_authority_id") or "not_available"),
        "scope_evidence_status": str(payload.get("evidence_status") or "INCOMPLETE_EVIDENCE"),
        "scope_claim": str(payload.get("claim_scope") or "incomplete_evidence_only"),
        "scope_full_repository_claim_eligible": payload.get("full_repository_claim_eligible") is True,
        "scope_repository_inventory_file_count": payload.get("repository_inventory_file_count"),
        "scope_effective_supported_source_file_count": payload.get("effective_supported_source_file_count"),
        "scope_indexed_source_file_count": payload.get("indexed_source_file_count"),
        "scope_claim_eligible_source_file_count": payload.get("claim_eligible_source_file_count"),
        "scope_discovered_candidate_count": int(payload.get("discovered_candidate_count") or 0),
        "scope_auto_selected_project_count": int(payload.get("auto_selected_project_count") or 0),
        "scope_effective_project_count": len(effective),
        "scope_excluded_project_count": int(payload.get("excluded_project_count") or 0),
        "scope_unresolved_project_candidate_count": len(unresolved),
        "scope_mismatch_reasons": list(payload.get("incomplete_reasons") or []),
        "scope_consumer_checks": [
            f"{layer}:{check.get('status', 'UNKNOWN')}"
            for layer, check in sorted(checks.items())
            if isinstance(check, dict)
        ],
        "scope_evidence_artifact": "analysis_scope_authority",
    }
