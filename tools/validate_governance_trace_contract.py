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
        required_trace_fields = {
            "trace_id", "principal", "tool_name", "context_fingerprint",
            "policy_version", "retry_count", "latency_ms", "outcome", "failure_layer",
        }
        checks.extend([
            {"id": "contract_kind", "ok": meta.get("kind") == "governance_trace_contract"},
            {"id": "local_first_no_remote_egress", "ok": bool(scope.get("local_first")) and scope.get("remote_egress") == "prohibited_by_contract"},
            {"id": "sqlite_event_table", "ok": event.get("table") == "governance_trace_events"},
            {"id": "trace_fields_complete", "ok": required_trace_fields.issubset(set(map(str, required)))},
            {"id": "unknown_is_explicit", "ok": event.get("unknown_value") == "not_available"},
            {"id": "detail_allowlist_is_declared", "ok": set(detail_allowed.get("mcp_tool_call", [])) == {"argument_keys"} and set(detail_allowed.get("watchdog_session", [])) == {"pulse_id", "changed_file_count", "violation_count", "deep_proof_debt_status"} and set(detail_allowed.get("release_proof_step", [])) == {"step_id", "required", "timed_out", "timeout_basis", "raw_artifact_present"} and set(detail_allowed.get("agent_handoff", [])) == {"packet_kind", "packet_status", "source_grounding_status"} and set(detail_allowed.get("work_package_operation", [])) == {"package_id", "package_status", "operation_id", "result_status", "evidence_artifact", "evidence_fingerprint", "authority"}},
            {"id": "observed_surfaces_declared", "ok": {"mcp_server", "watchdog", "release_proof", "agent_handoff", "work_package_operations"}.issubset(observed_surfaces)},
            {"id": "mcp_trace_writer_is_connected", "ok": "record_mcp_tool_trace" in mcp_source and "_record_governance_trace" in mcp_source and "activate_trace" in mcp_source and "reset_trace" in mcp_source},
            {"id": "watchdog_trace_writer_is_connected", "ok": "record_watchdog_session_trace" in watchdog_source and 'session["governance_trace"]' in watchdog_source},
            {"id": "release_proof_trace_writer_is_connected", "ok": "record_release_proof_step_trace" in release_proof_source and 'result["governance_trace"]' in release_proof_source},
            {"id": "agent_handoff_trace_writer_is_connected", "ok": "record_agent_handoff_trace" in mcp_source and "current_trace_id" in mcp_source and 'result["operation_trace"]' in mcp_source and "agent_handoff_trace_persistence_failed" in mcp_source},
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
