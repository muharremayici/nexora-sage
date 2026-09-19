from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping


AUDIT_FINDINGS_GENERATION_KIND = "nexora.audit_findings_generation"
AUDIT_FINDINGS_GENERATION_VERSION = "v1"
AUDIT_FINDINGS_FACT_ARTIFACT = "audit_findings_generation"
AUDIT_FINDINGS_FACT_KEY = "manifest"
GENERATION_COMPOSITION_CONTRACT_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "artifact_freshness_contract.json"
)


def load_generation_composition_contract() -> dict[str, Any]:
    payload = json.loads(
        GENERATION_COMPOSITION_CONTRACT_PATH.read_text(encoding="utf-8")
    )
    contract = payload.get("generation_composition") if isinstance(payload, dict) else None
    if not isinstance(contract, dict):
        raise ValueError("Artifact freshness contract has no generation_composition section.")
    sqlite_fact = contract.get("sqlite_fact")
    if (
        contract.get("kind") != AUDIT_FINDINGS_GENERATION_KIND
        or contract.get("version") != AUDIT_FINDINGS_GENERATION_VERSION
        or not isinstance(sqlite_fact, dict)
        or sqlite_fact.get("artifact_name") != AUDIT_FINDINGS_FACT_ARTIFACT
        or sqlite_fact.get("fact_key") != AUDIT_FINDINGS_FACT_KEY
    ):
        raise ValueError("Audit finding generation constants drifted from the freshness SSoT.")
    return contract


def generation_consumer_profile(profile: str) -> dict[str, Any]:
    contract = load_generation_composition_contract()
    profiles = contract.get("consumer_profiles")
    profiles = profiles if isinstance(profiles, dict) else {}
    row = profiles.get(str(profile or ""))
    if not isinstance(row, dict):
        raise ValueError(f"Unknown finding-generation consumer profile: {profile}")
    return row


def normalize_finding_scope_refs(values: Iterable[Any] | None) -> list[str]:
    normalized: set[str] = set()
    for value in values or []:
        text = str(value or "").replace("\\", "/").strip()
        if "::" not in text:
            continue
        project, rel_path = text.split("::", 1)
        project = project.strip()
        rel_path = rel_path.strip("/")
        if project and rel_path:
            normalized.add(f"{project}::{rel_path}")
    return sorted(normalized)


def finding_scope_sha256(values: Iterable[Any] | None) -> str:
    encoded = json.dumps(
        normalize_finding_scope_refs(values),
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validated_scoped_generation_transition(
    atlas_commit: Mapping[str, Any],
) -> dict[str, Any]:
    transition = atlas_commit.get("generation_transition")
    transition = transition if isinstance(transition, Mapping) else {}
    raw_changed = transition.get("changed_files")
    raw_deleted = transition.get("deleted_files")
    changed_files = normalize_finding_scope_refs(raw_changed)
    deleted_files = normalize_finding_scope_refs(raw_deleted)
    deleted_fields_present = (
        "deleted_files" in transition or "deleted_files_sha256" in transition
    )
    deleted_fields_valid = (
        not deleted_fields_present
        or (
            raw_deleted == deleted_files
            and transition.get("deleted_files_sha256")
            == finding_scope_sha256(deleted_files)
        )
    )
    if (
        atlas_commit.get("state") != "complete"
        or atlas_commit.get("generation_mode") != "surgical"
        or transition.get("kind") != "scoped_delta"
        or not str(atlas_commit.get("snapshot_id") or "").strip()
        or not str(transition.get("parent_snapshot_id") or "").strip()
        or not changed_files
        or raw_changed != changed_files
        or transition.get("changed_files_sha256")
        != finding_scope_sha256(changed_files)
        or not set(deleted_files).issubset(changed_files)
        or not deleted_fields_valid
    ):
        raise ValueError(
            "Current Atlas commit does not declare a valid signed scoped transition."
        )
    return {
        "kind": "scoped_delta",
        "parent_snapshot_id": str(transition["parent_snapshot_id"]).strip(),
        "changed_files": changed_files,
        "changed_files_sha256": finding_scope_sha256(changed_files),
        "deleted_files": deleted_files,
        "deleted_files_sha256": finding_scope_sha256(deleted_files),
    }


def audit_payload_snapshot_id(payload: Mapping[str, Any]) -> str:
    identity = payload.get("artifact_identity")
    if not isinstance(identity, Mapping) or identity.get("status") != "BOUND":
        return ""
    return str(identity.get("atlas_snapshot_id") or "").strip()


def build_canonical_finding_manifest(payload: Mapping[str, Any]) -> dict[str, Any]:
    snapshot_id = audit_payload_snapshot_id(payload)
    if not snapshot_id:
        raise ValueError("Canonical Audit findings require a bound Atlas snapshot identity.")
    scope = payload.get("audit_scope")
    scope = scope if isinstance(scope, Mapping) else {}
    projects = sorted(
        {
            str(item).strip()
            for item in scope.get("audited_projects", [])
            if str(item).strip()
        }
    )
    if not projects:
        raise ValueError("Canonical Audit findings require at least one audited project.")
    return {
        "kind": AUDIT_FINDINGS_GENERATION_KIND,
        "version": AUDIT_FINDINGS_GENERATION_VERSION,
        "snapshot_id": snapshot_id,
        "generation_mode": "canonical_replacement",
        "coverage_kind": str(scope.get("scope_kind") or "incomplete_evidence"),
        "full_repository_claim": scope.get("full_repository_claim") is True,
        "covered_projects": projects,
        "composition_depth": 0,
        "last_parent_snapshot_id": None,
        "last_changed_files": [],
        "last_changed_files_sha256": finding_scope_sha256([]),
        "last_deleted_files": [],
        "last_deleted_files_sha256": finding_scope_sha256([]),
        "source_artifact": "audit_report",
        "audit_contract": (
            f"{((payload.get('meta') or {}).get('kind') or 'audit_report')}:"
            f"{((payload.get('meta') or {}).get('version') or 'unknown')}"
        ),
    }


def build_scoped_finding_manifest(
    previous: Mapping[str, Any],
    atlas_commit: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    if (
        previous.get("kind") != AUDIT_FINDINGS_GENERATION_KIND
        or previous.get("version") != AUDIT_FINDINGS_GENERATION_VERSION
    ):
        raise ValueError("Scoped Audit findings require a recognized canonical finding baseline.")
    current_snapshot = str(atlas_commit.get("snapshot_id") or "").strip()
    transition = validated_scoped_generation_transition(atlas_commit)
    parent_snapshot = transition["parent_snapshot_id"]
    transition_files = transition["changed_files"]
    deleted_files = transition["deleted_files"]
    if str(previous.get("snapshot_id") or "") != parent_snapshot:
        raise ValueError("Scoped Audit parent snapshot does not match the finding baseline.")
    if audit_payload_snapshot_id(payload) != current_snapshot:
        raise ValueError("Scoped Audit is not bound to the current Atlas snapshot.")

    scope = payload.get("audit_scope")
    scope = scope if isinstance(scope, Mapping) else {}
    requested = normalize_finding_scope_refs(scope.get("requested_files"))
    audited = normalize_finding_scope_refs(scope.get("audited_files"))
    unresolved = normalize_finding_scope_refs(scope.get("unresolved_requested_files"))
    expected_audited = sorted(set(requested) - set(deleted_files))
    expected_scope_status = (
        "complete"
        if not deleted_files
        else "partial"
        if expected_audited
        else "empty"
    )
    if (
        scope.get("scope_kind") != "scoped_change"
        or scope.get("full_repository_claim") is not False
        or scope.get("scope_status") != expected_scope_status
        or unresolved != deleted_files
        or audited != expected_audited
        or requested != transition_files
    ):
        raise ValueError("Scoped Audit coverage does not exactly match the Atlas transition.")

    covered_projects = {
        str(item).strip()
        for item in previous.get("covered_projects", [])
        if str(item).strip()
    }
    changed_projects = {item.split("::", 1)[0] for item in requested}
    if not changed_projects.issubset(covered_projects):
        raise ValueError("Scoped Audit cannot expand the project coverage of its canonical baseline.")

    prior_contract = str(previous.get("audit_contract") or "")
    current_contract = (
        f"{((payload.get('meta') or {}).get('kind') or 'watchdog_audit_report')}:"
        f"{((payload.get('meta') or {}).get('version') or 'unknown')}"
    )
    canonical_current_contract = current_contract.replace("watchdog_audit_report:", "audit_report:", 1)
    if prior_contract != canonical_current_contract:
        raise ValueError("Scoped Audit contract does not match the canonical finding baseline.")

    try:
        depth = max(0, int(previous.get("composition_depth") or 0)) + 1
    except (TypeError, ValueError):
        depth = 1
    return {
        **dict(previous),
        "snapshot_id": current_snapshot,
        "generation_mode": "scoped_composition",
        "coverage_kind": "composed_scoped_delta",
        "full_repository_claim": False,
        "composition_depth": depth,
        "last_parent_snapshot_id": parent_snapshot,
        "last_changed_files": requested,
        "last_changed_files_sha256": finding_scope_sha256(requested),
        "last_deleted_files": deleted_files,
        "last_deleted_files_sha256": finding_scope_sha256(deleted_files),
        "source_artifact": "watchdog_audit_report",
    }


def evaluate_finding_manifest(
    manifest: Mapping[str, Any] | None,
    *,
    expected_snapshot_id: str,
    requested_project: str = "",
) -> dict[str, Any]:
    row = manifest if isinstance(manifest, Mapping) else {}
    project = str(requested_project or "").strip()
    all_projects = project in {"", "*", "all", "ALL"}
    covered_projects = {
        str(item).strip()
        for item in row.get("covered_projects", [])
        if str(item).strip()
    }
    structural = (
        row.get("kind") == AUDIT_FINDINGS_GENERATION_KIND
        and row.get("version") == AUDIT_FINDINGS_GENERATION_VERSION
        and bool(row.get("snapshot_id"))
        and isinstance(row.get("composition_depth"), int)
        and bool(covered_projects)
    )
    snapshot_match = structural and str(row.get("snapshot_id") or "") == str(expected_snapshot_id or "")
    coverage_match = bool(
        structural
        and (
            row.get("full_repository_claim") is True
            if all_projects
            else project in covered_projects
        )
    )
    passed = bool(snapshot_match and coverage_match)
    return {
        "status": "PASS" if passed else "FAIL",
        "snapshot_match": bool(snapshot_match),
        "coverage_match": bool(coverage_match),
        "expected_snapshot_id": str(expected_snapshot_id or "") or None,
        "observed_snapshot_id": str(row.get("snapshot_id") or "") or None,
        "requested_project": project or "*",
        "covered_projects": sorted(covered_projects),
        "full_repository_claim": row.get("full_repository_claim") is True,
        "generation_mode": str(row.get("generation_mode") or "unavailable"),
        "composition_depth": int(row.get("composition_depth") or 0)
        if isinstance(row.get("composition_depth"), int)
        else 0,
        "reason": (
            "current_generation_and_coverage"
            if passed
            else "manifest_unavailable_or_invalid"
            if not structural
            else "snapshot_mismatch"
            if not snapshot_match
            else "requested_scope_not_covered"
        ),
    }
