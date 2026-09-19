from __future__ import annotations

from typing import Any, Iterable

from tools.core.artifact_freshness_contract import evaluate_named_artifact_chain
from tools.core.artifact_registry import artifact_path_for_storage_root, artifact_paths
from tools.core import config as runtime_config
from tools.core.json_io import load_raw_artifact_path
from tools.core.pipeline_registry import load_pipeline_execution_policy


def normalize_watchdog_scope_refs(values: Iterable[Any] | None) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        text = str(value or "").strip().replace("\\", "/")
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return sorted(normalized)


def load_watchdog_runtime_contract() -> dict[str, Any]:
    policy = load_pipeline_execution_policy()
    modes = policy.get("execution_modes")
    mode = modes.get("watchdog_save_pulse") if isinstance(modes, dict) else None
    contract = mode.get("scoped_evidence_contract") if isinstance(mode, dict) else None
    if not isinstance(contract, dict):
        raise ValueError("watchdog_save_pulse is missing scoped_evidence_contract")
    if contract.get("scope_transport") != "direct_pipeline_argument":
        raise ValueError("watchdog scope must be transported as a direct pipeline argument")
    if not str(contract.get("evidence_scope") or "").strip():
        raise ValueError("watchdog scoped evidence contract is missing evidence_scope")
    change_events = contract.get("change_event_contract")
    required_event_kinds = {"create", "modify", "delete", "rename_from", "rename_to"}
    if not isinstance(change_events, dict):
        raise ValueError("watchdog scoped evidence contract is missing change_event_contract")
    if set(change_events.get("event_kinds") or []) != required_event_kinds:
        raise ValueError("watchdog change-event contract must declare canonical create/modify/delete/rename identities")
    if change_events.get("missing_exact_path_policy") != "accept_only_when_present_in_previous_canonical_atlas":
        raise ValueError("watchdog missing-path authority must come from the previous canonical Atlas")
    if change_events.get("omitted_tombstone_integrity") != "UNKNOWN":
        raise ValueError("watchdog must not emit a clean result while indexed tombstones are omitted")
    if change_events.get("rename_projection") != "linked_delete_plus_create":
        raise ValueError("watchdog rename events must preserve linked delete and create identities")
    explicit_change_set = change_events.get("explicit_change_set_acquisition")
    if not isinstance(explicit_change_set, dict):
        raise ValueError("watchdog change-event contract is missing explicit change-set acquisition policy")
    if explicit_change_set.get("transport") != "repeated_path_arguments":
        raise ValueError("watchdog explicit change sets must use the declared repeated-path transport")
    if int(explicit_change_set.get("max_paths") or 0) < 2:
        raise ValueError("watchdog explicit change-set bound must support related multi-file edits")
    if "reject_not_truncate" not in str(explicit_change_set.get("bound_basis") or ""):
        raise ValueError("watchdog explicit change-set bound must reject rather than truncate")
    if explicit_change_set.get("path_authority") != "canonical_resolved_repository_path":
        raise ValueError("watchdog explicit change-set paths must resolve under repository authority")
    if explicit_change_set.get("deduplication") != "canonical_path_identity_with_duplicate_receipt":
        raise ValueError("watchdog explicit change sets must preserve duplicate-path receipts")
    if set(explicit_change_set.get("accepted_entry_kinds") or []) != {
        "existing_supported_source",
        "current_atlas_tombstone",
    }:
        raise ValueError("watchdog explicit change sets must admit only exact source files and current tombstones")
    if explicit_change_set.get("directory_behavior") != "single_path_smoke_only":
        raise ValueError("watchdog directories must remain single-path smoke acquisition")
    if explicit_change_set.get("invalid_entry_behavior") != "reject_entire_change_set_without_partial_analysis":
        raise ValueError("watchdog invalid change-set entries must block partial analysis")
    if explicit_change_set.get("git_change_inference") != "forbidden":
        raise ValueError("watchdog must not infer an explicit change set from Git state")
    if explicit_change_set.get("silent_scope_truncation") != "forbidden":
        raise ValueError("watchdog explicit change sets must never be silently truncated")
    acquisition = change_events.get("filesystem_event_acquisition")
    guard = acquisition.get("amplification_guard") if isinstance(acquisition, dict) else None
    if not isinstance(acquisition, dict) or not isinstance(guard, dict):
        raise ValueError("watchdog change-event contract is missing filesystem event acquisition policy")
    if acquisition.get("baseline_authority") != "exact_committed_canonical_atlas_file_hash":
        raise ValueError("watchdog filesystem events must use the exact committed canonical Atlas baseline")
    if acquisition.get("content_hash_algorithm") != "atlas_md5_utf8_ignore":
        raise ValueError("watchdog filesystem event identity must match the Atlas text hash contract")
    if acquisition.get("unchanged_event_disposition") != "omit_with_evidence":
        raise ValueError("watchdog unchanged filesystem events must be omitted only with evidence")
    if acquisition.get("unknown_event_disposition") != "retain_or_require_operator_confirmation":
        raise ValueError("watchdog unknown filesystem events must fail open to analysis or operator confirmation")
    if int(acquisition.get("provenance_ring_max_events") or 0) < 1:
        raise ValueError("watchdog filesystem event provenance ring must be bounded and non-empty")
    if float(acquisition.get("cold_start_window_seconds") or -1) < 0:
        raise ValueError("watchdog filesystem event cold-start window must be non-negative")
    if int(guard.get("candidate_path_threshold") or 0) < 2:
        raise ValueError("watchdog amplification guard threshold must distinguish single-file edits")
    if "decision_boundary_not_scope_cap" not in str(guard.get("threshold_basis") or ""):
        raise ValueError("watchdog amplification threshold must be declared as a decision boundary, not a scope cap")
    if guard.get("ambiguous_large_scope_behavior") != "require_operator_confirmation_without_partial_analysis":
        raise ValueError("watchdog ambiguous large scopes must not be partially analyzed")
    if guard.get("verified_bulk_behavior") != "process_all":
        raise ValueError("watchdog must preserve every path in a verified bulk edit")
    if guard.get("silent_scope_truncation") != "forbidden":
        raise ValueError("watchdog filesystem scope must never be silently truncated")

    artifacts = contract.get("artifacts")
    audit = artifacts.get("audit") if isinstance(artifacts, dict) else None
    signals = artifacts.get("signals") if isinstance(artifacts, dict) else None
    graph_advisories = artifacts.get("graph_advisories") if isinstance(artifacts, dict) else None
    if not all(isinstance(row, dict) for row in (audit, signals, graph_advisories)):
        raise ValueError("watchdog scoped evidence contract is missing audit, signals or graph artifact ownership")
    if audit.get("canonical_overwrite_policy") != "forbidden":
        raise ValueError("watchdog scoped Audit must forbid canonical full-artifact overwrite")
    if graph_advisories.get("canonical_overwrite_policy") != "forbidden":
        raise ValueError("watchdog scoped graph projection must forbid canonical full-artifact overwrite")

    transport = contract.get("current_pulse_atlas_transport")
    required_transport_fields = {
        "mode",
        "authority",
        "lifetime",
        "consumer_access",
        "fallback",
        "consumers",
        "forbidden_behaviors",
    }
    if not isinstance(transport, dict) or required_transport_fields - set(transport):
        raise ValueError("watchdog scoped evidence contract has incomplete current-pulse Atlas transport")
    if transport.get("authority") != "sqlite_persisted_atlas":
        raise ValueError("watchdog current-pulse Atlas transport must preserve SQLite authority")
    if transport.get("consumer_access") != "read_only":
        raise ValueError("watchdog current-pulse Atlas consumers must be read-only")
    if transport.get("fallback") != "sqlite_first_artifact_store":
        raise ValueError("watchdog current-pulse Atlas fallback must remain SQLite-first")
    if not isinstance(transport.get("consumers"), list) or not transport["consumers"]:
        raise ValueError("watchdog current-pulse Atlas transport must declare consumers")

    paths = artifact_paths()
    for role, row in (("audit", audit), ("signals", signals), ("graph_advisories", graph_advisories)):
        artifact_id = str(row.get("artifact_id") or "").strip()
        if not artifact_id or artifact_id not in paths:
            raise ValueError(f"watchdog {role} artifact is missing from artifact_registry: {artifact_id!r}")

    integrity_states = contract.get("integrity_states")
    if not isinstance(integrity_states, dict) or not all(
        str(integrity_states.get(key) or "").strip() for key in ("clean", "violations", "unknown")
    ):
        raise ValueError("watchdog scoped evidence contract has incomplete integrity_states")
    return contract


def filesystem_event_acquisition_policy() -> dict[str, Any]:
    contract = load_watchdog_runtime_contract()
    return dict(contract["change_event_contract"]["filesystem_event_acquisition"])


def explicit_change_set_policy() -> dict[str, Any]:
    contract = load_watchdog_runtime_contract()
    return dict(contract["change_event_contract"]["explicit_change_set_acquisition"])


def watchdog_artifact_path(role: str, storage_root=None):
    contract = load_watchdog_runtime_contract()
    artifacts = contract["artifacts"]
    row = artifacts.get(str(role or "").strip())
    if not isinstance(row, dict):
        raise ValueError(f"unknown watchdog artifact role: {role}")
    active_storage_root = runtime_config.RAW_DIR if storage_root is None else storage_root
    return artifact_path_for_storage_root(active_storage_root, str(row["artifact_id"]))


def watchdog_artifact_identity() -> dict[str, Any]:
    """Bind scoped evidence to its target namespace and committed Atlas generation."""
    from tools.core.watchdog_target_context import current_watchdog_target_descriptor

    descriptor = current_watchdog_target_descriptor()
    atlas_commit = load_raw_artifact_path(runtime_config.RAW_DIR / "atlas_commit.json", {})
    snapshot_id = str(atlas_commit.get("snapshot_id") or "") if isinstance(atlas_commit, dict) else ""
    return {
        "status": "BOUND" if snapshot_id else "INCOMPLETE",
        "artifact_root": str(runtime_config.RAW_DIR.resolve()),
        "atlas_snapshot_id": snapshot_id or None,
        "target_descriptor": {
            key: descriptor.get(key)
            for key in (
                "system_scope",
                "acquisition_mode",
                "profile_id",
                "subject_root",
                "analysis_root",
                "artifact_strategy",
                "external_target",
            )
        },
    }


def validate_watchdog_artifact_identity(payload: Any, expected_identity: dict[str, Any]) -> dict[str, Any]:
    observed = payload.get("artifact_identity") if isinstance(payload, dict) else None
    complete = (
        isinstance(observed, dict)
        and observed.get("status") == "BOUND"
        and bool(observed.get("atlas_snapshot_id"))
        and observed.get("artifact_root") == expected_identity.get("artifact_root")
        and observed.get("atlas_snapshot_id") == expected_identity.get("atlas_snapshot_id")
        and observed.get("target_descriptor") == expected_identity.get("target_descriptor")
    )
    return {
        "status": "valid" if complete else "invalid",
        "identity_match": complete,
        "observed": observed if isinstance(observed, dict) else {},
        "expected": expected_identity,
        "reason": "identity_bound" if complete else "target_or_generation_identity_mismatch",
    }


def _declared_count_matches(value: Any, actual: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value == actual


def validate_watchdog_audit_scope(
    payload: Any,
    expected_refs: Iterable[Any] | None,
    expected_identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    expected = normalize_watchdog_scope_refs(expected_refs)
    expected_set = set(expected)
    if not isinstance(payload, dict):
        return {
            "status": "invalid",
            "scope_match": False,
            "scope_status": "empty",
            "expected_files": expected,
            "reason": "watchdog_audit_payload_not_object",
        }

    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    scope = payload.get("audit_scope") if isinstance(payload.get("audit_scope"), dict) else {}
    requested = normalize_watchdog_scope_refs(scope.get("requested_files"))
    audited = normalize_watchdog_scope_refs(scope.get("audited_files"))
    unresolved = normalize_watchdog_scope_refs(scope.get("unresolved_requested_files"))
    requested_set = set(requested)
    audited_set = set(audited)
    unresolved_set = set(unresolved)
    declared_status = str(scope.get("scope_status") or "")
    derived_status = "complete" if requested_set and audited_set == requested_set else ("partial" if audited_set else "empty")
    structural_valid = (
        meta.get("kind") == "watchdog_audit_report"
        and scope.get("scope_kind") == "scoped_change"
        and scope.get("full_repository_claim") is False
        and _declared_count_matches(scope.get("requested_file_count"), len(requested))
        and _declared_count_matches(scope.get("audited_file_count"), len(audited))
        and requested_set == audited_set | unresolved_set
        and declared_status == derived_status
    )
    identity_validation = (
        validate_watchdog_artifact_identity(payload, expected_identity)
        if isinstance(expected_identity, dict)
        else {"status": "not_evaluated", "identity_match": True, "reason": "identity_not_requested"}
    )
    structural_valid = structural_valid and bool(identity_validation.get("identity_match"))
    request_match = structural_valid and requested_set == expected_set
    scope_match = request_match and declared_status == "complete"
    return {
        "status": "valid" if structural_valid else "invalid",
        "request_match": request_match,
        "scope_match": scope_match,
        "scope_status": declared_status or derived_status,
        "expected_files": expected,
        "requested_files": requested,
        "audited_files": audited,
        "unresolved_requested_files": unresolved,
        "artifact_identity": identity_validation,
        "reason": (
            "scope_complete"
            if scope_match
            else ("scope_incomplete" if request_match else "scope_mismatched")
        ),
    }


def evaluate_watchdog_quant_input_evidence(
    atlas: dict[str, Any],
    audit_data: dict[str, Any],
    expected_scope: list[str],
) -> tuple[str, list[str], list[str], dict[str, Any], dict[str, Any]]:
    chain = evaluate_named_artifact_chain("contextos_quant_watchdog_input_chain", runtime_config.RAW_DIR)
    rows = {
        str(row.get("artifact")): row
        for row in chain.get("artifacts", [])
        if isinstance(row, dict) and row.get("artifact")
    }
    scope_validation = validate_watchdog_audit_scope(
        audit_data,
        expected_scope,
        watchdog_artifact_identity(),
    )
    stale_artifacts = {
        str(edge.get("consumer") or "")
        for edge in chain.get("stale_edges", [])
        if isinstance(edge, dict)
    }
    evidence: dict[str, Any] = {}
    unavailable: list[str] = []

    atlas_row = rows.get("atlas", {})
    atlas_valid = bool(atlas_row.get("exists")) and isinstance(atlas, dict) and bool(atlas)
    evidence["atlas"] = {
        "status": "available" if atlas_valid else "unavailable",
        "source": str(atlas_row.get("source") or "missing"),
        "shape_status": "valid" if isinstance(atlas, dict) and bool(atlas) else "invalid",
        "payload_bytes": int(atlas_row.get("payload_bytes") or 0),
        "updated_at": str(atlas_row.get("updated_at") or ""),
        "scope_status": "global_universe_with_current_changed_nodes" if atlas_valid else "unavailable",
    }
    if not atlas_valid:
        unavailable.append("atlas")

    audit_row = rows.get("watchdog_audit_report", {})
    audit_shape_valid = isinstance(audit_data, dict) and isinstance(audit_data.get("violations"), list)
    audit_valid = (
        bool(audit_row.get("exists"))
        and audit_shape_valid
        and scope_validation.get("scope_match") is True
        and scope_validation.get("scope_status") == "complete"
        and "watchdog_audit_report" not in stale_artifacts
    )
    evidence["watchdog_audit_report"] = {
        "status": "available" if audit_valid else "unavailable",
        "source": str(audit_row.get("source") or "missing"),
        "shape_status": "valid" if audit_shape_valid else "invalid",
        "payload_bytes": int(audit_row.get("payload_bytes") or 0),
        "updated_at": str(audit_row.get("updated_at") or ""),
        "scope_status": str(scope_validation.get("scope_status") or "empty"),
    }
    if not audit_valid:
        unavailable.append("watchdog_audit_report")

    deferred_inputs = ["circular_deps", "blast_radius"]
    for artifact in deferred_inputs:
        evidence[artifact] = {
            "status": "deferred",
            "source": "watchdog_deep_proof_debt",
            "shape_status": "not_evaluated",
            "payload_bytes": 0,
            "updated_at": "",
            "scope_status": "not_available_in_live_pulse",
        }
    if chain.get("missing_contract"):
        unavailable.append("contextos_quant_watchdog_input_chain")
    return (
        "PASS" if not unavailable else "PARTIAL",
        sorted(set(unavailable)),
        deferred_inputs,
        evidence,
        scope_validation,
    )


def watchdog_integrity_state(violations: list[dict[str, Any]], scope_validation: dict[str, Any]) -> str:
    contract = load_watchdog_runtime_contract()
    states = contract["integrity_states"]
    if not scope_validation.get("scope_match") or scope_validation.get("scope_status") != "complete":
        return str(states["unknown"])
    acquisition = scope_validation.get("acquisition") if isinstance(scope_validation.get("acquisition"), dict) else {}
    if int(acquisition.get("omitted_tombstone_count") or 0) > 0:
        return str(states["unknown"])
    if violations:
        return str(states["violations"])
    return str(states["clean"])
