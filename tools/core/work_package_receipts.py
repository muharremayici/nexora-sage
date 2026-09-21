"""Privacy-bounded execution receipts for the active SAGE work package."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path, PurePosixPath
from typing import Any

from tools.core.config import CODE_MAPS_DIR, CONFIG_DIR, RAW_DIR
from tools.core.distribution_policy import is_clean_install_root
from tools.core.governance_trace import UNKNOWN_VALUE, fingerprint, record_trace_event
from tools.core.json_io import load_json_object_strict
from tools.core.sage_active_work_package import active_work_package
from tools.core.source_layer_classifier import classify_source_layer


CONTRACT_PATH = CONFIG_DIR / "governance_trace_contract.json"


def _receipt_contract() -> dict[str, Any]:
    contract = load_json_object_strict(CONTRACT_PATH, label="Governance trace contract")
    value = contract.get("work_package_receipts")
    if not isinstance(value, dict):
        raise ValueError("governance_trace_contract.work_package_receipts must be an object")
    return value


def _operation_profile(operation_id: str) -> dict[str, Any]:
    profiles = _receipt_contract().get("operation_profiles")
    profile = profiles.get(operation_id) if isinstance(profiles, dict) else None
    if not isinstance(profile, dict):
        raise ValueError(f"Unknown governed work-package operation: {operation_id}")
    return profile


def work_package_identity(package: dict[str, Any] | None = None) -> str:
    current = package or active_work_package()
    return fingerprint(
        {
            "id": current.get("id"),
            "execution_wave": current.get("execution_wave"),
            "work_item_ids": sorted(str(item) for item in current.get("work_item_ids", []) if str(item)),
            "objective": current.get("objective"),
        }
    )


def _safe_source_path(relative: str) -> Path:
    path = (CODE_MAPS_DIR / relative).resolve()
    root = CODE_MAPS_DIR.resolve()
    if path != root and root not in path.parents:
        raise ValueError(f"Affected contract escapes SAGE root: {relative}")
    return path


def work_package_source_identity(package: dict[str, Any] | None = None) -> str:
    current = package or active_work_package()
    receipt_contract = _receipt_contract()
    excluded = {str(item) for item in receipt_contract.get("source_fingerprint_excluded_paths", []) if str(item)}
    rows: list[dict[str, str]] = []
    for relative in sorted({str(item).replace("\\", "/") for item in current.get("affected_contracts", []) if str(item)}):
        if relative in excluded:
            continue
        path = _safe_source_path(relative)
        rows.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "missing",
            }
        )
    return fingerprint(rows)


def _artifact_fingerprint(relative: str) -> str:
    if not relative or relative == UNKNOWN_VALUE or relative == "not_available":
        return UNKNOWN_VALUE
    path = _safe_source_path(relative)
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else UNKNOWN_VALUE


def record_work_package_operation(
    *,
    operation_id: str,
    result_status: str,
    started: float,
    evidence_artifact: str | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    package = active_work_package()
    profile = _operation_profile(operation_id)
    accepted = {str(item) for item in profile.get("accepted_statuses", []) if str(item)}
    status = str(result_status or UNKNOWN_VALUE)
    artifact = str(evidence_artifact or profile.get("evidence_artifact") or UNKNOWN_VALUE)
    outcome = "success" if status in accepted else "failure"
    return record_trace_event(
        event_type="work_package_operation",
        principal="sage_development_loop",
        tool_name=operation_id,
        task_fingerprint=work_package_identity(package),
        context_fingerprint=work_package_source_identity(package),
        policy_version=str(load_json_object_strict(CONTRACT_PATH).get("meta", {}).get("version") or UNKNOWN_VALUE),
        state_change="work_package_operation_observed",
        latency_ms=(time.perf_counter() - started) * 1000,
        outcome=outcome,
        failure_layer="none" if outcome == "success" else "validation",
        details={
            "package_id": package.get("id"),
            "package_status": package.get("status"),
            "operation_id": operation_id,
            "result_status": status,
            "evidence_artifact": artifact,
            "evidence_fingerprint": _artifact_fingerprint(artifact),
            "authority": _receipt_contract().get("authority"),
        },
        db_path=db_path,
    )


def record_work_package_operation_safely(**kwargs: Any) -> dict[str, Any]:
    if is_clean_install_root(CODE_MAPS_DIR):
        print(
            "[work-package-receipt] status=not_recorded reason=clean_mirror_runtime_write_prohibited",
            file=sys.stderr,
            flush=True,
        )
        return {"status": "not_recorded", "reason": "clean_mirror_runtime_write_prohibited"}
    try:
        event = record_work_package_operation(**kwargs)
        receipt = {"status": "recorded", "trace_id": event["trace_id"], "operation_id": event["tool_name"]}
        print(
            f"[work-package-receipt] status=recorded operation_id={event['tool_name']} trace_id={event['trace_id']}",
            file=sys.stderr,
            flush=True,
        )
        return receipt
    except Exception as exc:
        print(
            f"[work-package-receipt] status=not_recorded error_type={type(exc).__name__} reason={exc}",
            file=sys.stderr,
            flush=True,
        )
        return {"status": "not_recorded", "reason": type(exc).__name__, "message": str(exc)}


def propose_work_package_evidence(*, db_path: Path | None = None) -> dict[str, Any]:
    package = active_work_package()
    receipt_contract = _receipt_contract()
    profiles = receipt_contract.get("operation_profiles") if isinstance(receipt_contract.get("operation_profiles"), dict) else {}
    package_fp = work_package_identity(package)
    source_fp = work_package_source_identity(package)
    limit = max(1, int(receipt_contract.get("proposal_max_receipts") or 500))
    database = db_path or (RAW_DIR / "codemaps.db")
    rows: list[dict[str, Any]] = []
    if database.exists():
        with closing(sqlite3.connect(database)) as conn:
            conn.row_factory = sqlite3.Row
            selected = conn.execute(
                """
                SELECT * FROM governance_trace_events
                WHERE event_type = ? AND task_fingerprint = ? AND context_fingerprint = ?
                ORDER BY event_id DESC LIMIT ?;
                """,
                ("work_package_operation", package_fp, source_fp, limit),
            ).fetchall()
        for row in selected:
            item = dict(row)
            item["details"] = json.loads(str(item.pop("details_json") or "{}"))
            rows.append(item)

    observed_operations: dict[str, dict[str, Any]] = {}
    stale_or_mismatched_receipts = 0
    for event in rows:
        details = event.get("details") if isinstance(event.get("details"), dict) else {}
        operation_id = str(details.get("operation_id") or event.get("tool_name") or "")
        profile = profiles.get(operation_id) if isinstance(profiles.get(operation_id), dict) else {}
        if not profile or operation_id in observed_operations or event.get("outcome") != "success":
            continue
        if str(details.get("package_id") or "") != str(package.get("id") or ""):
            continue
        artifact = str(details.get("evidence_artifact") or UNKNOWN_VALUE)
        expected_artifact = str(profile.get("evidence_artifact") or UNKNOWN_VALUE)
        stored_artifact_fingerprint = str(details.get("evidence_fingerprint") or UNKNOWN_VALUE)
        if artifact != expected_artifact:
            stale_or_mismatched_receipts += 1
            continue
        if artifact != "not_available" and stored_artifact_fingerprint != _artifact_fingerprint(artifact):
            stale_or_mismatched_receipts += 1
            continue
        observed_operations[operation_id] = {
            "operation_id": operation_id,
            "trace_id": event.get("trace_id"),
            "recorded_at": event.get("recorded_at"),
            "result_status": details.get("result_status"),
            "evidence_artifact": details.get("evidence_artifact"),
            "evidence_fingerprint": details.get("evidence_fingerprint"),
        }
    evidence_requirements = receipt_contract.get("evidence_requirements") if isinstance(receipt_contract.get("evidence_requirements"), dict) else {}
    proposals: dict[str, dict[str, Any]] = {}
    for evidence_id, raw_required in evidence_requirements.items():
        required = [str(item) for item in raw_required if str(item)] if isinstance(raw_required, list) else []
        observed = [item for item in required if item in observed_operations]
        if not observed:
            continue
        missing = [item for item in required if item not in observed_operations]
        proposals[str(evidence_id)] = {
            "readiness": "receipt_group_complete_review_required" if not missing else "partial_receipt_group",
            "authority": receipt_contract.get("authority"),
            "may_mutate_closure": bool(receipt_contract.get("closure_mutation_allowed")),
            "required_operations": required,
            "observed_operations": observed,
            "missing_operations": missing,
            "references": [observed_operations[item] for item in observed],
        }
    preflight_contract = (
        receipt_contract.get("mutation_preflight")
        if isinstance(receipt_contract.get("mutation_preflight"), dict)
        else {}
    )
    required_preflight_operations = [
        str(item) for item in preflight_contract.get("required_operations", []) if str(item)
    ]
    observed_preflight_operations = [
        item for item in required_preflight_operations if item in observed_operations
    ]
    missing_preflight_operations = [
        item for item in required_preflight_operations if item not in observed_operations
    ]
    accepted_package_statuses = {
        str(item) for item in preflight_contract.get("accepted_package_statuses", []) if str(item)
    }
    preflight_ready = (
        bool(required_preflight_operations)
        and not missing_preflight_operations
        and str(package.get("status") or "") in accepted_package_statuses
    )
    mutation_preflight = {
        "status": str(
            preflight_contract.get("ready_status")
            if preflight_ready
            else preflight_contract.get("blocked_status") or "BLOCKED"
        ),
        "ready": preflight_ready,
        "required_operations": required_preflight_operations,
        "observed_operations": observed_preflight_operations,
        "missing_operations": missing_preflight_operations,
        "read_only_recovery_allowed": bool(preflight_contract.get("read_only_recovery_allowed_when_blocked")),
        "external_editor_enforcement": str(preflight_contract.get("external_editor_enforcement") or UNKNOWN_VALUE),
        "claim_boundary": str(preflight_contract.get("claim_boundary") or ""),
    }
    return {
        "meta": {"kind": "work_package_evidence_proposal", "version": "v1"},
        "package": {"id": package.get("id"), "status": package.get("status"), "identity": package_fp, "source_identity": source_fp},
        "authority": {
            "mode": receipt_contract.get("authority"),
            "closure_mutation_allowed": bool(receipt_contract.get("closure_mutation_allowed")),
            "claim_boundary": "Receipt-backed proposals are diagnostics only. A complete receipt group does not prove every active-package validator, manual review, approval or seal; an authorized actor must review and write closure evidence.",
        },
        "summary": {"matching_receipts": len(rows), "evidence_groups": len(proposals), "complete_receipt_groups": sum(1 for row in proposals.values() if row.get("readiness") == "receipt_group_complete_review_required"), "stale_or_mismatched_receipts": stale_or_mismatched_receipts},
        "mutation_preflight": mutation_preflight,
        "proposals": proposals,
    }


def build_work_package_closeout_proposal(
    *,
    package: dict[str, Any],
    receipt_projection: dict[str, Any],
    closure_validation: dict[str, Any],
    changed_files: list[str],
) -> dict[str, Any]:
    receipt_contract = _receipt_contract()
    closeout_policy = (
        receipt_contract.get("closeout_policy")
        if isinstance(receipt_contract.get("closeout_policy"), dict)
        else {}
    )
    scope_policy = (
        closeout_policy.get("changed_file_scope")
        if isinstance(closeout_policy.get("changed_file_scope"), dict)
        else {}
    )
    declared = {
        str(item).replace("\\", "/")
        for item in package.get("affected_contracts", [])
        if str(item).strip()
    }
    changed: list[str] = []
    excluded: list[dict[str, str]] = []
    governed: list[dict[str, str]] = []
    unknown: list[dict[str, str]] = []
    invalid: list[dict[str, str]] = []
    excluded_layers = {
        str(item) for item in scope_policy.get("excluded_source_layers", []) if str(item)
    }
    unknown_layers = {
        str(item) for item in scope_policy.get("unknown_source_layers", []) if str(item)
    }
    root = CODE_MAPS_DIR.resolve()
    for raw_path in sorted({str(item) for item in changed_files if str(item)}):
        normalized = raw_path.replace("\\", "/")
        pure = PurePosixPath(normalized)
        unsafe = (
            pure.is_absolute()
            or not pure.parts
            or any(part in {"", ".", ".."} for part in pure.parts)
            or ":" in pure.parts[0]
        )
        if unsafe:
            invalid.append({"path": normalized, "reason": "invalid_relative_changed_path"})
            continue
        normalized = pure.as_posix()
        candidate = (root / Path(*pure.parts)).resolve()
        if candidate == root or root not in candidate.parents:
            invalid.append({"path": normalized, "reason": "changed_path_escapes_sage_root"})
            continue
        changed.append(normalized)
        layer, reason = classify_source_layer(candidate, root=root)
        row = {"path": normalized, "source_layer": layer, "reason": reason}
        if layer in excluded_layers:
            excluded.append(row)
        elif layer in unknown_layers:
            unknown.append(row)
        else:
            governed.append(row)
    changed = sorted(set(changed))
    undeclared = sorted(row["path"] for row in governed if row["path"] not in declared)
    proposals = (
        receipt_projection.get("proposals", {})
        if isinstance(receipt_projection.get("proposals"), dict)
        else {}
    )
    closure_records = package.get("closure", {}).get("evidence", {})
    closure_records = closure_records if isinstance(closure_records, dict) else {}
    release_scope = package.get("release_scope") if isinstance(package.get("release_scope"), dict) else {}
    release_mode = str(release_scope.get("mode") or "")
    applicability_profiles = (
        closeout_policy.get("evidence_applicability_by_release_mode")
        if isinstance(closeout_policy.get("evidence_applicability_by_release_mode"), dict)
        else {}
    )
    applicability_profile = applicability_profiles.get(release_mode)
    applicability_errors: list[str] = []
    applicability_profile_known = isinstance(applicability_profile, dict)
    if not applicability_profile_known:
        applicability_profile = {}
        applicability_errors.append(f"unknown_release_mode:{release_mode or '<missing>'}")
    all_machine_groups = {
        str(item) for item in receipt_contract.get("evidence_requirements", {}) if str(item)
    }
    required_machine_groups = sorted(
        str(item)
        for item in applicability_profile.get("required_evidence_groups", [])
        if str(item)
    )
    not_applicable_reasons = (
        applicability_profile.get("not_applicable_evidence_groups")
        if isinstance(applicability_profile.get("not_applicable_evidence_groups"), dict)
        else {}
    )
    not_applicable_groups = {
        str(evidence_id): str(reason)
        for evidence_id, reason in not_applicable_reasons.items()
        if str(evidence_id) and str(reason)
    }
    if applicability_profile_known and set(required_machine_groups) & set(not_applicable_groups):
        applicability_errors.append("evidence_group_is_both_required_and_not_applicable")
    if applicability_profile_known and set(required_machine_groups) | set(not_applicable_groups) != all_machine_groups:
        applicability_errors.append("release_mode_does_not_classify_every_machine_group")
    expected_not_applicable = str(
        closeout_policy.get("explicit_not_applicable_closure_status") or ""
    )
    for evidence_id in sorted(not_applicable_groups):
        record = closure_records.get(evidence_id)
        status = str(record.get("status") or "") if isinstance(record, dict) else ""
        if not expected_not_applicable or status != expected_not_applicable:
            applicability_errors.append(
                f"explicit_not_applicable_closure_status_missing:{evidence_id}"
            )
    incomplete_groups = sorted(
        evidence_id
        for evidence_id in required_machine_groups
        for row in [proposals.get(evidence_id)]
        if not isinstance(row, dict) or row.get("readiness") != "receipt_group_complete_review_required"
    )
    missing_groups = [item for item in required_machine_groups if item not in proposals]
    preflight = (
        receipt_projection.get("mutation_preflight", {})
        if isinstance(receipt_projection.get("mutation_preflight"), dict)
        else {}
    )
    stale_receipts = int(receipt_projection.get("summary", {}).get("stale_or_mismatched_receipts") or 0)
    machine_ready = (
        closure_validation.get("status") == "PASS"
        and not undeclared
        and not unknown
        and not invalid
        and not applicability_errors
        and not missing_groups
        and not incomplete_groups
        and stale_receipts == 0
        and preflight.get("ready") is True
    )
    manual_evidence_ids = sorted(
        evidence_id
        for evidence_id in closure_records
        if evidence_id not in all_machine_groups
    )
    return {
        "meta": {"kind": "sage_work_package_closeout_proposal", "version": "v1"},
        "status": "EVIDENCE_READY_HUMAN_ACTION_REQUIRED" if machine_ready else "BLOCKED",
        "package": {
            "id": package.get("id"),
            "status": package.get("status"),
            "identity": work_package_identity(package),
            "source_identity": work_package_source_identity(package),
        },
        "live_diff": {
            "changed_files": changed,
            "governed_changed_files": governed,
            "excluded_changed_files": excluded,
            "unknown_source_files": unknown,
            "invalid_changed_files": invalid,
            "declared_affected_contracts": sorted(declared),
            "undeclared_changed_files": undeclared,
        },
        "machine_evidence": {
            "required_groups": required_machine_groups,
            "not_applicable_groups": not_applicable_groups,
            "missing_groups": missing_groups,
            "incomplete_groups": incomplete_groups,
            "stale_or_mismatched_receipts": stale_receipts,
            "proposed_updates": {
                evidence_id: row
                for evidence_id, row in proposals.items()
                if isinstance(row, dict) and row.get("readiness") == "receipt_group_complete_review_required"
            },
        },
        "applicability": {
            "release_mode": release_mode or None,
            "status": "VALID" if not applicability_errors else "BLOCKED",
            "errors": sorted(set(applicability_errors)),
            "required_machine_groups": required_machine_groups,
            "not_applicable_machine_groups": not_applicable_groups,
        },
        "mutation_preflight": preflight,
        "closure_validation": closure_validation,
        "human_authority": {
            "required": True,
            "manual_evidence_ids": manual_evidence_ids,
            "may_mutate_package": False,
            "may_grant_approval_or_human_seal": False,
            "next_action": "Review proposed machine evidence, complete manual evidence records, then update closure and transition ledgers as an authorized actor.",
        },
        "claim_boundary": "This command composes current evidence into a closeout proposal. It does not edit ledgers, close a package, activate another package, push Git state, approve a mutation or issue a human seal.",
    }


def render_work_package_closeout_proposal(payload: dict[str, Any]) -> str:
    package = payload.get("package", {}) if isinstance(payload.get("package"), dict) else {}
    live_diff = payload.get("live_diff", {}) if isinstance(payload.get("live_diff"), dict) else {}
    machine = payload.get("machine_evidence", {}) if isinstance(payload.get("machine_evidence"), dict) else {}
    preflight = payload.get("mutation_preflight", {}) if isinstance(payload.get("mutation_preflight"), dict) else {}
    authority = payload.get("human_authority", {}) if isinstance(payload.get("human_authority"), dict) else {}
    lines = [
        "# SAGE Work Package Closeout Proposal",
        "",
        f"- status: `{payload.get('status')}`",
        f"- package: `{package.get('id')}`",
        f"- package_status: `{package.get('status')}`",
        f"- changed_files: `{len(live_diff.get('changed_files', []))}`",
        f"- excluded_changed_files: `{len(live_diff.get('excluded_changed_files', []))}`",
        f"- unknown_source_files: `{len(live_diff.get('unknown_source_files', []))}`",
        f"- invalid_changed_files: `{len(live_diff.get('invalid_changed_files', []))}`",
        f"- undeclared_changed_files: `{len(live_diff.get('undeclared_changed_files', []))}`",
        f"- missing_machine_groups: `{', '.join(machine.get('missing_groups', [])) or 'none'}`",
        f"- incomplete_machine_groups: `{', '.join(machine.get('incomplete_groups', [])) or 'none'}`",
        f"- stale_or_mismatched_receipts: `{machine.get('stale_or_mismatched_receipts')}`",
        f"- mutation_preflight: `{preflight.get('status')}`",
        f"- human_action_required: `{str(bool(authority.get('required'))).lower()}`",
        "",
        "## Claim Boundary",
        "",
        str(payload.get("claim_boundary") or ""),
        "",
        "## Undeclared Changed Files",
        "",
    ]
    lines.extend(f"- `{path}`" for path in live_diff.get("undeclared_changed_files", []))
    if not live_diff.get("undeclared_changed_files"):
        lines.append("- none")
    lines.extend(["", "## Human Authority", "", str(authority.get("next_action") or ""), ""])
    return "\n".join(lines)
