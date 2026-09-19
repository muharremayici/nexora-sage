from __future__ import annotations

import re
from typing import Any

from tools.core.config import CONFIG_DIR
from tools.core.json_io import load_json_object_strict


PHASE_REGISTRY_PATH = CONFIG_DIR / "roadmap_phase_registry.json"
SEMVER_CORE_PATTERN = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def load_roadmap_phase_registry() -> dict[str, Any]:
    return load_json_object_strict(PHASE_REGISTRY_PATH, label="Roadmap phase registry")


def roadmap_phase_rows(registry: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    doc = registry if registry is not None else load_roadmap_phase_registry()
    rows = doc.get("phases", []) if isinstance(doc, dict) else []
    return [row for row in rows if isinstance(row, dict)]


def allowed_roadmap_releases(registry: dict[str, Any] | None = None) -> set[str]:
    return {
        str(row.get("release") or "")
        for row in roadmap_phase_rows(registry)
        if row.get("status") == "roadmap" and str(row.get("release") or "").strip()
    }


def current_product_release(registry: dict[str, Any] | None = None) -> str:
    doc = registry if registry is not None else load_roadmap_phase_registry()
    value = doc.get("current_product_release", "") if isinstance(doc, dict) else ""
    return str(value).strip()


def semantic_versioning_policy(registry: dict[str, Any] | None = None) -> dict[str, Any]:
    doc = registry if registry is not None else load_roadmap_phase_registry()
    policy = doc.get("semantic_versioning_policy") if isinstance(doc, dict) else None
    return policy if isinstance(policy, dict) else {}


def semantic_versioning_policy_issues(registry: dict[str, Any] | None = None) -> list[str]:
    doc = registry if registry is not None else load_roadmap_phase_registry()
    policy = semantic_versioning_policy(doc)
    planning_model = doc.get("planning_model") if isinstance(doc.get("planning_model"), dict) else {}
    standard = policy.get("standard") if isinstance(policy.get("standard"), dict) else {}
    impact = policy.get("work_item_impact") if isinstance(policy.get("work_item_impact"), dict) else {}
    candidate = policy.get("candidate_scope") if isinstance(policy.get("candidate_scope"), dict) else {}
    authorities = policy.get("public_api_authorities") if isinstance(policy.get("public_api_authorities"), list) else []
    allowed_levels = [str(item) for item in impact.get("allowed_levels", []) if str(item)]
    rank = impact.get("rank") if isinstance(impact.get("rank"), dict) else {}
    effects = impact.get("allowed_effects_by_level") if isinstance(impact.get("allowed_effects_by_level"), dict) else {}
    issues: list[str] = []
    if planning_model.get("semantic_versioning_policy_source") != "semantic_versioning_policy":
        issues.append("semantic_versioning_policy_source_invalid")
    if standard.get("id") != "semver_2_0_0" or standard.get("url") != "https://semver.org/":
        issues.append("official_semver_2_0_0_reference_missing")
    if standard.get("public_api_must_be_declared") is not True:
        issues.append("public_api_declaration_requirement_missing")
    if standard.get("released_contents_are_immutable") is not True:
        issues.append("released_contents_immutability_requirement_missing")
    if standard.get("normal_version_format") != "MAJOR.MINOR.PATCH":
        issues.append("normal_semver_format_invalid")
    if not authorities or any(
        not isinstance(row, dict)
        or not str(row.get("id") or "").strip()
        or not str(row.get("authority") or "").strip()
        for row in authorities
    ):
        issues.append("public_api_authorities_missing_or_invalid")
    authority_ids = [str(row.get("id") or "") for row in authorities if isinstance(row, dict)]
    if len(authority_ids) != len(set(authority_ids)):
        issues.append("duplicate_public_api_authority_ids")
    if impact.get("field") != "release_impact":
        issues.append("work_item_release_impact_field_invalid")
    if allowed_levels != ["patch", "minor", "major", "unknown"]:
        issues.append("release_impact_levels_invalid")
    if rank != {"patch": 1, "minor": 2, "major": 3}:
        issues.append("release_impact_rank_invalid")
    if set(effects) != set(allowed_levels) or any(
        not isinstance(effects.get(level), list) or not effects.get(level)
        for level in allowed_levels
    ):
        issues.append("release_impact_effect_mapping_invalid")
    if candidate.get("eligible_statuses") != ["ready_for_delivery"]:
        issues.append("candidate_eligible_statuses_invalid")
    for required_true in (
        "include_all_eligible",
        "exclude_delivered",
        "highest_impact_wins",
        "unknown_impact_blocks_recommendation",
    ):
        if candidate.get(required_true) is not True:
            issues.append(f"candidate_{required_true}_missing")
    if candidate.get("pre_release_labels_automatically_assigned") is not False:
        issues.append("candidate_prerelease_assignment_boundary_invalid")
    authority = policy.get("authority_boundary") if isinstance(policy.get("authority_boundary"), dict) else {}
    if any(
        authority.get(key) is not False
        for key in (
            "selects_concrete_release",
            "authorizes_release_preparation",
            "authorizes_publication",
            "activates_claim_profile",
        )
    ):
        issues.append("semver_projection_authority_boundary_invalid")
    return sorted(set(issues))


def release_impact_issues(
    work_item: dict[str, Any],
    registry: dict[str, Any] | None = None,
) -> list[str]:
    policy = semantic_versioning_policy(registry)
    contract = policy.get("work_item_impact") if isinstance(policy.get("work_item_impact"), dict) else {}
    field = str(contract.get("field") or "release_impact")
    impact = work_item.get(field)
    if not isinstance(impact, dict):
        return ["missing_release_impact"]
    level = str(impact.get("level") or "")
    effect = str(impact.get("effect") or "")
    evidence = [str(item) for item in impact.get("evidence", []) if str(item).strip()] if isinstance(impact.get("evidence"), list) else []
    allowed_levels = {str(item) for item in contract.get("allowed_levels", []) if str(item)}
    effects = contract.get("allowed_effects_by_level") if isinstance(contract.get("allowed_effects_by_level"), dict) else {}
    allowed_effects = {str(item) for item in effects.get(level, []) if str(item)} if isinstance(effects.get(level), list) else set()
    issues: list[str] = []
    if level not in allowed_levels:
        issues.append("invalid_release_impact_level")
    if effect not in allowed_effects:
        issues.append("invalid_release_impact_effect")
    if not evidence:
        issues.append("missing_release_impact_evidence")
    if not str(impact.get("rationale") or "").strip():
        issues.append("missing_release_impact_rationale")
    return sorted(set(issues))


def next_semver(current_release: str, impact_level: str) -> str:
    match = SEMVER_CORE_PATTERN.fullmatch(str(current_release).strip())
    if not match:
        raise ValueError("current product release must be a normal SemVer X.Y.Z")
    major, minor, patch = (int(value) for value in match.groups())
    if impact_level == "patch":
        patch += 1
    elif impact_level == "minor":
        minor += 1
        patch = 0
    elif impact_level == "major":
        major += 1
        minor = 0
        patch = 0
    else:
        raise ValueError("release impact must be patch, minor or major")
    return f"{major}.{minor}.{patch}"


def project_semver_candidate(
    work_items: list[dict[str, Any]],
    registry: dict[str, Any] | None = None,
    *,
    technical_ready_work_item_ids: set[str] | None = None,
) -> dict[str, Any]:
    doc = registry if registry is not None else load_roadmap_phase_registry()
    policy = semantic_versioning_policy(doc)
    policy_issues = semantic_versioning_policy_issues(doc)
    scope_policy = policy.get("candidate_scope") if isinstance(policy.get("candidate_scope"), dict) else {}
    eligible_statuses = {str(item) for item in scope_policy.get("eligible_statuses", []) if str(item)}
    eligible = sorted(
        (
            row
            for row in work_items
            if isinstance(row, dict)
            and str(row.get("status") or "") in eligible_statuses
            and not str(row.get("delivered_release") or "").strip()
        ),
        key=lambda row: str(row.get("id") or ""),
    )
    eligible_ids = {str(row.get("id") or "<missing-id>") for row in eligible}
    unverified_technical_ids = (
        sorted(eligible_ids - {str(item) for item in technical_ready_work_item_ids if str(item)})
        if technical_ready_work_item_ids is not None
        else []
    )
    disposition_issues = {
        str(row.get("id") or "<missing-id>"): release_impact_issues(row, doc)
        for row in eligible
    }
    disposition_issues = {key: value for key, value in disposition_issues.items() if value}
    unknown_ids = sorted(
        str(row.get("id") or "<missing-id>")
        for row in eligible
        if isinstance(row.get("release_impact"), dict)
        and str(row["release_impact"].get("level") or "") == "unknown"
    )
    by_impact: dict[str, list[str]] = {"patch": [], "minor": [], "major": [], "unknown": []}
    for row in eligible:
        impact = row.get("release_impact") if isinstance(row.get("release_impact"), dict) else {}
        level = str(impact.get("level") or "unknown")
        by_impact.setdefault(level, []).append(str(row.get("id") or "<missing-id>"))

    current_release = current_product_release(doc)
    recommended_release: str | None = None
    highest_impact: str | None = None
    registered_status: str | None = None
    status = "NO_ELIGIBLE_WORK"
    issues = list(policy_issues)
    if policy_issues:
        status = "FAIL_POLICY"
    elif not eligible:
        status = "NO_ELIGIBLE_WORK"
    elif unverified_technical_ids:
        status = "ATTENTION_TECHNICAL_EVIDENCE"
    elif disposition_issues or unknown_ids:
        status = "ATTENTION_IMPACT_REVIEW"
    else:
        rank = policy.get("work_item_impact", {}).get("rank", {})
        highest_impact = max(
            (str(row["release_impact"]["level"]) for row in eligible),
            key=lambda value: int(rank[value]),
        )
        try:
            recommended_release = next_semver(current_release, highest_impact)
        except ValueError as exc:
            issues.append(str(exc))
            status = "FAIL_CURRENT_VERSION"
        else:
            row_by_release = {
                str(row.get("release") or ""): row
                for row in roadmap_phase_rows(doc)
                if str(row.get("release") or "").strip()
            }
            registered = row_by_release.get(recommended_release)
            registered_status = str(registered.get("status") or "") if isinstance(registered, dict) else None
            status = "READY_REGISTERED" if registered_status == "roadmap" else "ATTENTION_REGISTRATION_REQUIRED"

    return {
        "meta": {"kind": "sage_semver_candidate_projection", "version": "v1"},
        "status": status,
        "standard": policy.get("standard", {}),
        "current_product_release": current_release or None,
        "highest_impact": highest_impact,
        "recommended_release": recommended_release,
        "recommended_release_registry_status": registered_status,
        "candidate_scope": {
            "work_items": len(eligible),
            "work_item_ids": [str(row.get("id") or "<missing-id>") for row in eligible],
            "by_impact": by_impact,
            "include_all_eligible": scope_policy.get("include_all_eligible") is True,
        },
        "impact_review": {
            "disposition_issues": disposition_issues,
            "unknown_work_item_ids": unknown_ids,
        },
        "technical_readiness": {
            "source": (
                "independent_upstream_artifact_assessment"
                if technical_ready_work_item_ids is not None
                else "work_item_lifecycle_status"
            ),
            "unverified_work_item_ids": unverified_technical_ids,
        },
        "issues": sorted(set(issues)),
        "authority": {
            "concrete_release_selected": False,
            "release_preparation_authorized": False,
            "publication_authorized": False,
            "claim_profile_activation_authorized": False,
        },
        "claim_boundary": "This deterministic projection recommends the next normal SemVer target and includes all technically ready undelivered work. It does not select a concrete release, prepare or publish artifacts, activate claims, or replace human authority for ambiguous impact.",
    }


def production_release_history(registry: dict[str, Any] | None = None) -> set[str]:
    doc = registry if registry is not None else load_roadmap_phase_registry()
    contract = doc.get("validation_contract", {}) if isinstance(doc, dict) else {}
    statuses = contract.get("production_release_statuses", []) if isinstance(contract, dict) else []
    allowed_statuses = {str(value) for value in statuses if str(value).strip()}
    return {
        str(row.get("release") or "")
        for row in roadmap_phase_rows(doc)
        if row.get("status") in allowed_statuses and str(row.get("release") or "").strip()
    }


def active_phase(registry: dict[str, Any] | None = None) -> dict[str, Any]:
    rows = [
        row
        for row in roadmap_phase_rows(registry)
        if row.get("status") == "active"
    ]
    return rows[0] if len(rows) == 1 else {}


def phases_by_capability_domain(registry: dict[str, Any] | None = None, domain: str = "") -> set[str]:
    needle = str(domain).strip().lower()
    if not needle:
        return set()
    return {
        str(row.get("release") or "")
        for row in roadmap_phase_rows(registry)
        if any(
            needle in str(row.get(field) or "").lower()
            for field in ("capability_domain", "title", "claim_profile_id")
        )
        and str(row.get("release") or "").strip()
    }


def required_roadmap_profile_ids(registry: dict[str, Any] | None = None) -> set[str]:
    return {
        str(row.get("claim_profile_id") or "")
        for row in roadmap_phase_rows(registry)
        if row.get("status") == "roadmap"
        and row.get("release_claim_profile_required") is True
        and str(row.get("claim_profile_id") or "").strip()
    }


def activation_planning_window(registry: dict[str, Any] | None = None) -> set[str]:
    doc = registry if registry is not None else load_roadmap_phase_registry()
    values = doc.get("activation_planning_window", []) if isinstance(doc, dict) else []
    return {str(value) for value in values if str(value).strip()}
