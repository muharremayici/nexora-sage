from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.artifact_validator import ARTIFACT_PATHS
from tools.core.capability_registry import capability_ids, load_capability_registry
from tools.core.config import CONFIG_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_object_strict
from tools.core.roadmap_phase_registry import (
    allowed_roadmap_releases,
    current_product_release,
    load_roadmap_phase_registry,
    production_release_history,
    required_roadmap_profile_ids,
)


CLAIM_PROFILE_PATH = CONFIG_DIR / "release_claim_profiles.json"
RELEASE_IDENTITY_PATH = CONFIG_DIR / "release_identity.json"
RAW_OUTPUT_PATH = RAW_DIR / "release_claim_profiles_validation.json"
REPORT_OUTPUT_PATH = REPORTS_DIR / "release_claim_profiles_validation.md"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check(name: str, passed: bool, details: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _as_set(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {str(item) for item in value if str(item).strip()}


def _contract_set(contract: dict[str, Any], field: str) -> set[str]:
    return _as_set(contract.get(field) if isinstance(contract, dict) else [])


def _contract_str_map(contract: dict[str, Any], field: str) -> dict[str, str]:
    value = contract.get(field) if isinstance(contract, dict) else {}
    if not isinstance(value, dict):
        return {}
    return {
        str(key): str(item)
        for key, item in value.items()
        if str(key).strip() and str(item).strip()
    }


def _proof_step_ids() -> set[str]:
    from tools.run_release_proof_bundle import PROOF_STEPS

    return {str(step.get("id") or "") for step in PROOF_STEPS if isinstance(step, dict)}


def build_validation() -> dict[str, Any]:
    profiles_doc = load_json_object_strict(CLAIM_PROFILE_PATH, label="Release claim profiles")
    validation_contract = (
        profiles_doc.get("validation_contract", {})
        if isinstance(profiles_doc, dict) and isinstance(profiles_doc.get("validation_contract"), dict)
        else {}
    )
    allowed_profile_statuses = _contract_set(validation_contract, "allowed_profile_statuses")
    required_active_validators = _contract_set(validation_contract, "required_active_validators")
    required_active_artifacts = _contract_set(validation_contract, "required_active_artifacts")
    required_proof_surface_classes = _contract_set(validation_contract, "required_proof_surface_classes")
    required_active_proof_surfaces = _contract_set(validation_contract, "required_active_proof_surfaces")
    forbidden_active_proof_surfaces = _contract_set(validation_contract, "forbidden_active_proof_surfaces")
    validator_step_aliases = _contract_str_map(validation_contract, "validator_step_aliases")
    allowed_runtime_artifacts = _contract_set(validation_contract, "allowed_runtime_artifacts")
    allowed_release_policy_validator_exceptions = _contract_set(validation_contract, "allowed_release_policy_validator_exceptions")
    allowed_release_policy_artifact_exceptions = _contract_set(validation_contract, "allowed_release_policy_artifact_exceptions")
    phase_registry = load_roadmap_phase_registry()
    active_release = current_product_release(phase_registry)
    release_history = production_release_history(phase_registry)
    allowed_releases = allowed_roadmap_releases(phase_registry)
    required_profile_ids = required_roadmap_profile_ids(phase_registry)
    release_identity = load_json_object_strict(RELEASE_IDENTITY_PATH, label="Release identity")
    pipeline_policy = load_json_object_strict(CONFIG_DIR / "pipeline_execution_policy.json", label="Pipeline execution policy")
    capabilities = load_capability_registry()
    known_capabilities = capability_ids(capabilities)
    proof_step_ids = _proof_step_ids()

    profiles = profiles_doc.get("profiles", []) if isinstance(profiles_doc, dict) else []
    profile_rows = [profile for profile in profiles if isinstance(profile, dict)]
    proof_surface_rows = [
        row for row in profiles_doc.get("proof_surface_classes", [])
        if isinstance(row, dict)
    ] if isinstance(profiles_doc, dict) else []
    proof_surface_ids = {str(row.get("id") or "") for row in proof_surface_rows}
    active_profile_id = str(profiles_doc.get("active_profile_id") or "") if isinstance(profiles_doc, dict) else ""
    active_profiles = [profile for profile in profile_rows if profile.get("status") == "active"]
    active_profile = next((profile for profile in active_profiles if profile.get("id") == active_profile_id), {})
    roadmap_profiles = [profile for profile in profile_rows if profile.get("status") == "roadmap"]
    release_mode = (
        pipeline_policy.get("execution_modes", {}).get("release_deep", {})
        if isinstance(pipeline_policy, dict)
        else {}
    )
    release_mode_validators = _as_set(release_mode.get("required_validators") if isinstance(release_mode, dict) else [])
    release_mode_artifacts = _as_set(release_mode.get("required_artifacts") if isinstance(release_mode, dict) else [])
    active_validators = _as_set(active_profile.get("required_validators") if isinstance(active_profile, dict) else [])
    active_artifacts = _as_set(active_profile.get("required_artifacts") if isinstance(active_profile, dict) else [])
    active_capabilities = _as_set(active_profile.get("capability_ids") if isinstance(active_profile, dict) else [])
    active_proof_surfaces = _as_set(active_profile.get("proof_surface_ids") if isinstance(active_profile, dict) else [])
    identity_release_claim = (
        (release_identity.get("release_claim") or {}) if isinstance(release_identity, dict) else {}
    )
    identity_claim = str(identity_release_claim.get("allowed") or "")
    active_allowed_claim = str(active_profile.get("allowed_claim") or "") if isinstance(active_profile, dict) else ""
    active_claim_origin_release = (
        str(active_profile.get("claim_origin_release") or "") if isinstance(active_profile, dict) else ""
    )

    profile_ids = [str(profile.get("id") or "") for profile in profile_rows]
    duplicate_profile_ids = sorted({profile_id for profile_id in profile_ids if profile_id and profile_ids.count(profile_id) > 1})
    invalid_statuses = sorted(
        {
            f"{profile.get('id') or '<missing-id>'}:{profile.get('status')}"
            for profile in profile_rows
            if str(profile.get("status") or "") not in allowed_profile_statuses
        }
    )
    roadmap_active_claim_leaks = sorted(
        str(profile.get("id") or "<missing-id>")
        for profile in roadmap_profiles
        if "not active" not in str(profile.get("claim_boundary") or "").lower()
    )
    invalid_roadmap_releases = sorted(
        str(profile.get("id") or "<missing-id>")
        for profile in roadmap_profiles
        if str(profile.get("target_release") or "") not in allowed_releases
    )
    roadmap_with_required_gates = sorted(
        str(profile.get("id") or "<missing-id>")
        for profile in roadmap_profiles
        if _as_set(profile.get("required_artifacts")) or _as_set(profile.get("required_validators"))
    )
    missing_roadmap_profiles = sorted(required_profile_ids - {str(profile.get("id") or "") for profile in roadmap_profiles})
    missing_active_validators = sorted(required_active_validators - active_validators)
    missing_active_artifacts = sorted(required_active_artifacts - active_artifacts)
    active_validators_not_in_release_policy = sorted(
        active_validators - release_mode_validators - allowed_release_policy_validator_exceptions
    )
    active_artifacts_not_in_release_policy = sorted(
        active_artifacts - release_mode_artifacts - allowed_release_policy_artifact_exceptions
    )
    validators_without_proof_steps = sorted(
        validator
        for validator in active_validators
        if validator not in proof_step_ids and validator_step_aliases.get(validator) not in proof_step_ids
    )
    artifacts_not_registered = sorted(
        artifact
        for artifact in active_artifacts
        if artifact not in ARTIFACT_PATHS and artifact not in allowed_runtime_artifacts
    )
    missing_capabilities = sorted(active_capabilities - known_capabilities)
    missing_proof_surface_classes = sorted(required_proof_surface_classes - proof_surface_ids)
    malformed_proof_surfaces = sorted(
        str(row.get("id") or "<missing-id>")
        for row in proof_surface_rows
        if not str(row.get("owner") or "").strip()
        or not str(row.get("verdict_subject") or "").strip()
        or not str(row.get("claim_boundary") or "").strip()
        or not isinstance(row.get("primary_artifacts"), list)
        or not row.get("primary_artifacts")
        or not isinstance(row.get("not_for"), list)
        or not row.get("not_for")
    )
    proof_surface_artifacts_not_registered = sorted(
        f"{row.get('id') or '<missing-id>'}:{artifact}"
        for row in proof_surface_rows
        for artifact in _as_set(row.get("primary_artifacts"))
        if artifact not in ARTIFACT_PATHS and artifact not in allowed_runtime_artifacts
    )
    active_missing_proof_surfaces = sorted(required_active_proof_surfaces - active_proof_surfaces)
    active_forbidden_proof_surfaces = sorted(active_proof_surfaces & forbidden_active_proof_surfaces)
    unknown_active_proof_surfaces = sorted(active_proof_surfaces - proof_surface_ids)
    target_repo_roadmap_profiles = [
        profile
        for profile in roadmap_profiles
        if "target_repository_proof_bundle" in _as_set(profile.get("proof_surface_ids"))
    ]
    target_repo_roadmap_surfaces = set().union(
        *[
            _as_set(profile.get("proof_surface_ids"))
            for profile in target_repo_roadmap_profiles
        ]
    ) if target_repo_roadmap_profiles else set()

    checks = [
        _check(
            "release_claim_profiles_document_is_valid",
            profiles_doc.get("meta", {}).get("kind") == "nexora.release_claim_profiles"
            and profiles_doc.get("current_product_release") == active_release
            and bool(validation_contract)
            and bool(allowed_profile_statuses)
            and bool(required_active_validators)
            and bool(required_active_artifacts)
            and bool(required_proof_surface_classes)
            and len(profile_rows) >= 2
            and not duplicate_profile_ids,
            {
                "kind": profiles_doc.get("meta", {}).get("kind") if isinstance(profiles_doc, dict) else None,
                "current_product_release": profiles_doc.get("current_product_release") if isinstance(profiles_doc, dict) else None,
                "validation_contract_fields": sorted(validation_contract.keys()) if isinstance(validation_contract, dict) else [],
                "profile_count": len(profile_rows),
                "duplicate_profile_ids": duplicate_profile_ids,
            },
        ),
        _check(
            "exactly_one_active_profile_matches_current_release",
            len(active_profiles) == 1
            and bool(active_profile)
            and active_profile.get("target_release") == active_release,
            {"active_profile_id": active_profile_id, "active_profiles": [profile.get("id") for profile in active_profiles]},
        ),
        _check(
            "active_allowed_claim_matches_release_identity",
            bool(identity_claim) and active_allowed_claim == identity_claim,
            {"active_allowed_claim": active_allowed_claim, "release_identity_claim": identity_claim},
        ),
        _check(
            "active_claim_origin_release_is_released",
            bool(active_claim_origin_release) and active_claim_origin_release in release_history,
            {
                "active_release": active_release,
                "claim_origin_release": active_claim_origin_release,
                "production_release_history": sorted(release_history),
            },
        ),
        _check("profile_statuses_are_known", not invalid_statuses, {"invalid_statuses": invalid_statuses}),
        _check(
            "active_profile_declares_required_v1_gates",
            not missing_active_validators and not missing_active_artifacts,
            {
                "missing_validators": missing_active_validators,
                "missing_artifacts": missing_active_artifacts,
            },
        ),
        _check(
            "active_profile_validators_are_release_proof_steps",
            not validators_without_proof_steps,
            {"validators_without_proof_steps": validators_without_proof_steps},
        ),
        _check(
            "active_profile_artifacts_are_registered",
            not artifacts_not_registered,
            {"artifacts_not_registered": artifacts_not_registered},
        ),
        _check(
            "active_profile_capabilities_exist",
            not missing_capabilities,
            {"missing_capabilities": missing_capabilities},
        ),
        _check(
            "active_profile_release_deep_policy_alignment",
            not active_validators_not_in_release_policy and not active_artifacts_not_in_release_policy,
            {
                "validators_not_in_release_policy": active_validators_not_in_release_policy,
                "artifacts_not_in_release_policy": active_artifacts_not_in_release_policy,
            },
        ),
        _check(
            "roadmap_profiles_are_preserved_but_not_active_claims",
            not roadmap_active_claim_leaks
            and not invalid_roadmap_releases
            and not roadmap_with_required_gates
            and not missing_roadmap_profiles,
            {
                "roadmap_active_claim_leaks": roadmap_active_claim_leaks,
                "invalid_roadmap_releases": invalid_roadmap_releases,
                "roadmap_with_required_gates": roadmap_with_required_gates,
                "missing_roadmap_profiles": missing_roadmap_profiles,
            },
        ),
        _check(
            "proof_surface_classes_separate_sage_health_agent_surface_and_target_repo_proof",
            not missing_proof_surface_classes
            and not malformed_proof_surfaces
            and not proof_surface_artifacts_not_registered
            and not active_missing_proof_surfaces
            and not active_forbidden_proof_surfaces
            and not unknown_active_proof_surfaces
            and len(target_repo_roadmap_profiles) == 1
            and "target_repository_proof_bundle" in target_repo_roadmap_surfaces,
            {
                "missing_proof_surface_classes": missing_proof_surface_classes,
                "malformed_proof_surfaces": malformed_proof_surfaces,
                "proof_surface_artifacts_not_registered": proof_surface_artifacts_not_registered,
                "active_missing_proof_surfaces": active_missing_proof_surfaces,
                "active_forbidden_proof_surfaces": active_forbidden_proof_surfaces,
                "unknown_active_proof_surfaces": unknown_active_proof_surfaces,
                "target_repo_roadmap_profiles": [profile.get("id") for profile in target_repo_roadmap_profiles],
                "target_repo_roadmap_surfaces": sorted(target_repo_roadmap_surfaces),
            },
        ),
    ]
    failed = [check for check in checks if not check["passed"]]
    payload = {
        "meta": {
            "kind": "release_claim_profiles_validation",
            "version": "v1",
            "generated_at": _utc_now(),
            "generator": "tools.validate_release_claim_profiles",
        },
        "summary": {
            "status": "PASS" if not failed else "FAIL",
            "current_product_release": active_release,
            "active_profile_id": active_profile_id,
            "profile_count": len(profile_rows),
            "checks": len(checks),
            "passed": len(checks) - len(failed),
            "failed": len(failed),
        },
        "active_profile": {
            "id": active_profile.get("id") if isinstance(active_profile, dict) else None,
            "allowed_claim": active_profile.get("allowed_claim") if isinstance(active_profile, dict) else None,
            "claim_boundary": active_profile.get("claim_boundary") if isinstance(active_profile, dict) else None,
            "required_validators": sorted(active_validators),
            "required_artifacts": sorted(active_artifacts),
            "proof_surface_ids": sorted(active_proof_surfaces),
        },
        "proof_surface_classes": proof_surface_rows,
        "roadmap_profiles": [
            {
                "id": profile.get("id"),
                "target_release": profile.get("target_release"),
                "planned_work": profile.get("planned_work", []),
                "claim_boundary": profile.get("claim_boundary"),
                "proof_surface_ids": profile.get("proof_surface_ids", []),
            }
            for profile in roadmap_profiles
        ],
        "checks": checks,
    }
    save_json_atomic(RAW_OUTPUT_PATH, payload)
    save_text_atomic(REPORT_OUTPUT_PATH, render_report(payload))
    return payload


def render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    active = payload.get("active_profile", {})
    lines = [
        "# Release Claim Profiles Validation",
        "",
        f"- status: `{summary.get('status')}`",
        f"- current_product_release: `{summary.get('current_product_release')}`",
        f"- active_profile_id: `{summary.get('active_profile_id')}`",
        f"- allowed_claim: `{active.get('allowed_claim')}`",
        f"- checks: `{summary.get('passed')}/{summary.get('checks')}`",
        "",
        "## Active Claim Boundary",
        "",
        str(active.get("claim_boundary") or ""),
        "",
        "## Active Proof Surfaces",
        "",
        ", ".join(f"`{item}`" for item in active.get("proof_surface_ids", [])) or "_none_",
        "",
        "## Proof Surface Classes",
        "",
        "| Surface | Owner | Verdict Subject | Not For |",
        "|---|---|---|---|",
    ]
    for surface in payload.get("proof_surface_classes", []):
        not_for = ", ".join(str(item) for item in surface.get("not_for", [])[:4])
        lines.append(
            f"| `{surface.get('id')}` | `{surface.get('owner')}` | {surface.get('verdict_subject')} | {not_for} |"
        )
    lines.extend([
        "",
        "## Checks",
        "",
        "| Check | Passed | Details |",
        "|---|---|---|",
    ])
    for check in payload.get("checks", []):
        details = json.dumps(check.get("details"), ensure_ascii=False)[:500]
        lines.append(f"| `{check.get('name')}` | `{check.get('passed')}` | `{details}` |")
    lines.extend(["", "## Roadmap Profiles", "", "| Profile | Target Release | Planned Work |", "|---|---|---|"])
    for profile in payload.get("roadmap_profiles", []):
        planned = ", ".join(str(item) for item in profile.get("planned_work", [])[:6])
        lines.append(f"| `{profile.get('id')}` | `{profile.get('target_release')}` | {planned} |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    payload = build_validation()
    print(json.dumps(payload.get("summary", {}), ensure_ascii=False))
    return 0 if payload.get("summary", {}).get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
