"""Validate the declarative local governance trace contract."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "governance_trace_contract.json"
MCP_SERVER_PATH = ROOT / "tools" / "mcp" / "server.py"
WATCHDOG_PATH = ROOT / "tools" / "orchestrators" / "watchdog.py"
RELEASE_PROOF_RUNNER_PATH = ROOT / "tools" / "run_release_proof_bundle.py"
PIPELINE_RECEIPT_PATH = ROOT / "tools" / "core" / "pipeline_run_receipts.py"
ORCHESTRATOR_PATH = ROOT / "tools" / "orchestrators" / "orchestrator.py"
CLI_PATH = ROOT / "codemaps.py"
PIPELINE_POLICY_PATH = ROOT / "config" / "pipeline_execution_policy.json"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.work_package_receipts import propose_work_package_evidence, record_work_package_operation_safely

RAW_OUTPUT_PATH = RAW_DIR / "governance_trace_contract_validation.json"
REPORT_OUTPUT_PATH = REPORTS_DIR / "governance_trace_contract_validation.md"


def _load() -> dict[str, Any]:
    with CONTRACT_PATH.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("contract must be a JSON object")
    return payload


def run() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    try:
        contract = _load()
        meta = contract.get("meta") if isinstance(contract.get("meta"), dict) else {}
        event = contract.get("event") if isinstance(contract.get("event"), dict) else {}
        scope = contract.get("scope") if isinstance(contract.get("scope"), dict) else {}
        retention = contract.get("retention") if isinstance(contract.get("retention"), dict) else {}
        integration = contract.get("integration") if isinstance(contract.get("integration"), dict) else {}
        required = event.get("required_fields") if isinstance(event.get("required_fields"), list) else []
        detail_allowed = event.get("detail_allowed_keys") if isinstance(event.get("detail_allowed_keys"), dict) else {}
        receipts = contract.get("work_package_receipts") if isinstance(contract.get("work_package_receipts"), dict) else {}
        operation_profiles = receipts.get("operation_profiles") if isinstance(receipts.get("operation_profiles"), dict) else {}
        evidence_requirements = receipts.get("evidence_requirements") if isinstance(receipts.get("evidence_requirements"), dict) else {}
        mutation_preflight = receipts.get("mutation_preflight") if isinstance(receipts.get("mutation_preflight"), dict) else {}
        pipeline_policy_source = PIPELINE_POLICY_PATH.read_text(encoding="utf-8")
        pipeline_policy = json.loads(pipeline_policy_source)
        pipeline_receipts = (
            pipeline_policy.get("pipeline_run_receipts", {})
            if isinstance(pipeline_policy, dict)
            else {}
        )
        closure_operations = {
            str(operation_id)
            for operation_ids in evidence_requirements.values()
            if isinstance(operation_ids, list)
            for operation_id in operation_ids
            if str(operation_id)
        }
        preflight_operations = {str(item) for item in mutation_preflight.get("required_operations", []) if str(item)}
        required_operations = closure_operations | preflight_operations
        profiled_operations = set(map(str, operation_profiles))
        operation_profiles_are_coherent = required_operations == profiled_operations and all(
            isinstance(profile, dict)
            and isinstance(profile.get("accepted_statuses"), list)
            and bool(profile.get("accepted_statuses"))
            and (
                (
                    str(profile.get("closure_evidence_id") or "") in evidence_requirements
                    and operation_id in evidence_requirements.get(str(profile.get("closure_evidence_id") or ""), [])
                )
                or (
                    profile.get("purpose") == "mutation_preflight"
                    and operation_id in preflight_operations
                )
            )
            and bool(profile.get("evidence_artifact"))
            for operation_id, profile in operation_profiles.items()
        )
        observed_surfaces = set(map(str, integration.get("observed_surfaces") or []))
        mcp_source = MCP_SERVER_PATH.read_text(encoding="utf-8", errors="replace") if MCP_SERVER_PATH.exists() else ""
        watchdog_source = WATCHDOG_PATH.read_text(encoding="utf-8", errors="replace") if WATCHDOG_PATH.exists() else ""
        release_proof_source = RELEASE_PROOF_RUNNER_PATH.read_text(encoding="utf-8", errors="replace") if RELEASE_PROOF_RUNNER_PATH.exists() else ""
        pipeline_receipt_source = PIPELINE_RECEIPT_PATH.read_text(encoding="utf-8", errors="replace") if PIPELINE_RECEIPT_PATH.exists() else ""
        orchestrator_source = ORCHESTRATOR_PATH.read_text(encoding="utf-8", errors="replace") if ORCHESTRATOR_PATH.exists() else ""
        cli_source = CLI_PATH.read_text(encoding="utf-8", errors="replace") if CLI_PATH.exists() else ""
        pipeline_detail_fields = {
            "run_id", "lifecycle_state", "phase", "pid", "command_profile", "scope", "projects",
            "status_query", "retry_rule", "heartbeat_seconds", "duration_basis",
            "duration_sample_count", "duration_p50_seconds", "duration_p95_seconds",
            "client_timeout_floor_seconds", "step_id", "step_status", "active_steps",
            "active_run_id", "active_pid", "terminal_status", "exit_code", "interruption_reason",
            "governance_verdict", "elapsed_seconds", "completed_steps", "failed_steps",
            "skipped_steps", "evidence_identities", "shadow_worker_started_count",
            "shadow_worker_completed_count", "shadow_worker_failed_count",
            "shadow_worker_pending_count", "shadow_worker_global_pending_count",
            "shadow_worker_ids", "shadow_artifacts", "shadow_worker_identities_truncated",
            "shadow_flush_timed_out", "shadow_flush_timeout_seconds", "shadow_flush_wait_seconds",
            "scope_topology_authority_id", "scope_authority_id", "scope_evidence_status",
            "scope_claim", "scope_full_repository_claim_eligible",
            "scope_repository_inventory_file_count", "scope_effective_supported_source_file_count",
            "scope_indexed_source_file_count", "scope_claim_eligible_source_file_count",
            "scope_discovered_candidate_count", "scope_auto_selected_project_count",
            "scope_effective_project_count", "scope_excluded_project_count",
            "scope_unresolved_project_candidate_count", "scope_mismatch_reasons",
            "scope_consumer_checks", "scope_evidence_artifact",
        }
        required_trace_fields = {
            "trace_id", "principal", "tool_name", "context_fingerprint",
            "policy_version", "retry_count", "latency_ms", "outcome", "failure_layer",
        }
        expected_pipeline_phases = {
            "acquisition", "lock_acquired", "blocked_by_active_run", "atlas_refresh",
            "execution_planned", "step_completed", "heartbeat", "pipeline_completed",
            "shadow_flush_started", "shadow_flush_timeout", "evidence_closeout",
            "process_exit_ready", "terminal",
        }
        checks.extend([
            {"id": "contract_kind", "ok": meta.get("kind") == "governance_trace_contract"},
            {"id": "local_first_no_remote_egress", "ok": bool(scope.get("local_first")) and scope.get("remote_egress") == "prohibited_by_contract"},
            {"id": "sqlite_event_table", "ok": event.get("table") == "governance_trace_events"},
            {"id": "trace_fields_complete", "ok": required_trace_fields.issubset(set(map(str, required)))},
            {"id": "unknown_is_explicit", "ok": event.get("unknown_value") == "not_available"},
            {"id": "detail_allowlist_is_declared", "ok": set(detail_allowed.get("mcp_tool_call", [])) == {"argument_keys", "payload_chars", "result_status", "fail_closed_reason", "target_mode"} and set(detail_allowed.get("watchdog_session", [])) == {"pulse_id", "changed_file_count", "violation_count", "deep_proof_debt_status"} and set(detail_allowed.get("release_proof_step", [])) == {"step_id", "required", "timed_out", "timeout_basis", "raw_artifact_present"} and set(detail_allowed.get("agent_handoff", [])) == {"packet_kind", "packet_status", "source_grounding_status"} and set(detail_allowed.get("work_package_operation", [])) == {"package_id", "package_status", "operation_id", "result_status", "evidence_artifact", "evidence_fingerprint", "authority"} and set(detail_allowed.get("pipeline_invocation", [])) == pipeline_detail_fields},
            {"id": "mcp_trace_storage_authority_is_product_global", "ok": isinstance(scope.get("storage_authority_by_event_type"), dict) and scope.get("storage_authority_by_event_type", {}).get("mcp_tool_call") == "product-global output/.operational/mcp/mcp_call_telemetry.db"},
            {"id": "observed_surfaces_declared", "ok": {"mcp_server", "watchdog", "release_proof", "agent_handoff", "work_package_operations", "pipeline_orchestrator"}.issubset(observed_surfaces)},
            {"id": "mcp_trace_writer_is_connected", "ok": "persist_completed_mcp_call" in mcp_source and "_record_governance_trace" in mcp_source and "activate_trace_storage" in mcp_source and "reset_trace_storage" in mcp_source},
            {"id": "watchdog_trace_writer_is_connected", "ok": "record_watchdog_session_trace" in watchdog_source and 'session["governance_trace"]' in watchdog_source},
            {"id": "release_proof_trace_writer_is_connected", "ok": "record_release_proof_step_trace" in release_proof_source and 'result["governance_trace"]' in release_proof_source},
            {"id": "agent_handoff_trace_writer_is_connected", "ok": "record_agent_handoff_trace" in mcp_source and "current_trace_id" in mcp_source and 'result["operation_trace"]' in mcp_source and "agent_handoff_trace_persistence_failed" in mcp_source},
            {"id": "pipeline_invocation_receipt_is_sqlite_first_and_queryable", "ok": '"pipeline_invocation"' in pipeline_receipt_source and "record_trace_event(" in pipeline_receipt_source and "governance_trace_events" in pipeline_receipt_source and "pipeline_run_status" in pipeline_receipt_source and "start_pipeline_run_receipt" in orchestrator_source and "_pipeline_receipt_terminal" in orchestrator_source and "process_exit_ready" in pipeline_receipt_source and "flush_shadow_writes_report" in orchestrator_source and "release_shadow_write_run" in orchestrator_source and "bypass_proxy=True" in pipeline_receipt_source and "cmd_run_status" in cli_source},
            {"id": "pipeline_terminal_requires_run_owned_shadow_closeout", "ok": set(pipeline_receipts.get("lifecycle_phases", [])) == expected_pipeline_phases and pipeline_receipts.get("terminal_requires_phase") == "process_exit_ready" and pipeline_receipts.get("shadow_worker_attribution") == "process_local_run_id_worker_id_and_artifact_name_only" and pipeline_receipts.get("shadow_worker_content_policy") == "exclude_paths_payloads_and_source_content" and int(pipeline_receipts.get("shadow_worker_identity_limit") or 0) == 50 and pipeline_receipts.get("process_exit_readiness_requires_global_pending_count") == 0 and pipeline_receipts.get("compatibility_shadow_failure_effect") == "preserve_sqlite_authority_expose_failed_count_and_do_not_promote_engineering_correctness" and pipeline_receipts.get("shadow_flush_timeout_behavior") == "retain_pipeline_lock_emit_timeout_receipt_and_continue_bounded_waits_until_terminal" and pipeline_receipts.get("receipt_shadow_write_mode") == "synchronous_projection_bypass_managed_artifact_store"},
            {"id": "pipeline_receipt_does_not_promote_execution_to_engineering_proof", "ok": "not_evaluated_by_execution_receipt" in pipeline_receipt_source and "does not prove repository correctness" in pipeline_policy_source.lower()},
            {"id": "work_package_receipt_authority_is_non_mutating", "ok": receipts.get("authority") == "non_authoritative_evidence_proposal" and receipts.get("closure_mutation_allowed") is False},
            {"id": "work_package_receipts_do_not_dirty_clean_mirrors", "ok": receipts.get("write_scope") == "development_workspace_only" and receipts.get("clean_mirror_behavior") == "skip_with_explicit_notice"},
            {"id": "work_package_operation_profiles_are_contract_derived_and_coherent", "ok": operation_profiles_are_coherent},
            {"id": "mutation_preflight_is_fail_closed_and_non_authoritative", "ok": bool(preflight_operations) and bool(mutation_preflight.get("accepted_package_statuses")) and mutation_preflight.get("blocked_status") == "BLOCKED" and mutation_preflight.get("external_editor_enforcement") == "not_available" and mutation_preflight.get("enforced_surfaces") == ["mcp_sage_internal_mutating_tools"] and mutation_preflight.get("read_only_recovery_allowed_when_blocked") is True and "requires_sage_developer_mutation_preflight" in mcp_source and "propose_work_package_evidence" in mcp_source},
            {"id": "bounded_retention", "ok": int(retention.get("max_events_per_tenant") or 0) > 0},
        ])
    except Exception as exc:
        checks.append({"id": "contract_readable", "ok": False, "details": str(exc)})
    failed = [row["id"] for row in checks if not row.get("ok")]
    return {
        "validator": "governance_trace_contract",
        "status": "PASS" if not failed else "FAIL",
        "checks": checks,
        "failed_checks": failed,
    }


if __name__ == "__main__":
    started = time.perf_counter()
    result = run()
    save_json_atomic(RAW_OUTPUT_PATH, result)
    lines = ["# Governance Trace Contract Validation", "", f"status: `{result['status']}`", ""]
    for check in result["checks"]:
        lines.append(f"- {'PASS' if check.get('ok') else 'FAIL'}: `{check['id']}`")
    save_text_atomic(REPORT_OUTPUT_PATH, "\n".join(lines) + "\n")
    record_work_package_operation_safely(
        operation_id="governance_trace_contract_validation",
        result_status=result["status"],
        started=started,
        evidence_artifact="output/.raw/governance_trace_contract_validation.json",
    )
    save_json_atomic(RAW_DIR / "work_package_evidence_proposal.json", propose_work_package_evidence())
    print(json.dumps(result, indent=2, ensure_ascii=False))
    raise SystemExit(0 if result["status"] == "PASS" else 1)
