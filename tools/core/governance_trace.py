"""Local, privacy-bounded governance trace persistence.

This module records observed lifecycle handoffs. It deliberately preserves
unknown fields as ``not_available`` instead of inferring agent intent, model
reasoning, or a root cause from a final outcome.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from contextvars import ContextVar, Token
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.core.config import CONFIG_DIR, RAW_DIR
from tools.core.db import SQLiteManager
from tools.core.json_io import load_json_file


CONTRACT_PATH = CONFIG_DIR / "governance_trace_contract.json"
UNKNOWN_VALUE = "not_available"
_ACTIVE_TRACE_ID: ContextVar[str | None] = ContextVar("sage_governance_trace_id", default=None)
_ACTIVE_TRACE_DB_PATH: ContextVar[Path | None] = ContextVar("sage_governance_trace_db_path", default=None)


def _contract() -> dict[str, Any]:
    payload = load_json_file(CONTRACT_PATH, {})
    if not isinstance(payload, dict):
        raise ValueError("governance_trace_contract must be a JSON object")
    return payload


def _event_config(contract: dict[str, Any]) -> dict[str, Any]:
    event = contract.get("event")
    if not isinstance(event, dict):
        raise ValueError("governance_trace_contract.event must be an object")
    return event


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fingerprint(value: Any) -> str:
    """Return a deterministic fingerprint without retaining the raw value."""
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8", errors="replace")).hexdigest()


def new_trace_id() -> str:
    return f"sage-trace-{uuid.uuid4()}"


def activate_trace(trace_id: str | None = None) -> Token[str | None]:
    """Bind one opaque trace id to the current MCP call context."""
    return _ACTIVE_TRACE_ID.set(_bounded_text(trace_id or new_trace_id(), limit=120))


def current_trace_id() -> str | None:
    """Return the active call trace id, if this code runs inside an MCP handoff."""
    return _ACTIVE_TRACE_ID.get()


def reset_trace(token: Token[str | None]) -> None:
    """Clear the call-local trace context after the MCP operation completes."""
    _ACTIVE_TRACE_ID.reset(token)


def activate_trace_storage(db_path: Path) -> Token[Path | None]:
    """Bind nested trace events to one call-local SQLite authority."""
    return _ACTIVE_TRACE_DB_PATH.set(Path(db_path).resolve())


def current_trace_storage() -> Path | None:
    """Return the call-local trace database, if one is active."""
    return _ACTIVE_TRACE_DB_PATH.get()


def reset_trace_storage(token: Token[Path | None]) -> None:
    """Restore the previous call-local trace storage authority."""
    _ACTIVE_TRACE_DB_PATH.reset(token)


def _required_enum(event: dict[str, Any], key: str, value: str) -> str:
    allowed = {str(item) for item in event.get(key, []) if str(item)}
    if value not in allowed:
        raise ValueError(f"Unsupported governance trace {key}: {value}")
    return value


def _bounded_text(value: Any, *, limit: int = 500) -> str:
    text = UNKNOWN_VALUE if value is None else str(value).strip()
    text = text or UNKNOWN_VALUE
    return text[:limit]


def _sanitized_details(event: dict[str, Any], event_type: str, details: dict[str, Any] | None) -> dict[str, Any]:
    """Keep only centrally declared, non-content metadata keys for this event type."""
    source = details if isinstance(details, dict) else {}
    allowed_by_type = event.get("detail_allowed_keys") if isinstance(event.get("detail_allowed_keys"), dict) else {}
    allowed = {str(key) for key in allowed_by_type.get(event_type, []) if str(key)}
    sanitized: dict[str, Any] = {}
    for key in sorted(allowed):
        if key not in source:
            continue
        value = source.get(key)
        if isinstance(value, list):
            sanitized[key] = [_bounded_text(item, limit=120) for item in value[:50]]
        elif isinstance(value, str) or value is None:
            sanitized[key] = _bounded_text(value, limit=500)
        elif isinstance(value, (int, float, bool)):
            sanitized[key] = value
    omitted = sorted(str(key) for key in source if str(key) not in allowed)
    if omitted:
        sanitized["omitted_detail_key_count"] = len(omitted)
    return sanitized


def record_trace_event(
    *,
    event_type: str,
    principal: str = UNKNOWN_VALUE,
    tool_name: str = UNKNOWN_VALUE,
    task_fingerprint: str = UNKNOWN_VALUE,
    context_fingerprint: str = UNKNOWN_VALUE,
    policy_version: str = UNKNOWN_VALUE,
    state_change: str = UNKNOWN_VALUE,
    retry_count: int = 0,
    latency_ms: float | None = None,
    outcome: str = "unknown",
    failure_layer: str = "unknown",
    details: dict[str, Any] | None = None,
    trace_id: str | None = None,
    db_path: Path | None = None,
    max_events: int | None = None,
) -> dict[str, Any]:
    """Persist one observed event in the active local/tenant SQLite database."""
    contract = _contract()
    event = _event_config(contract)
    event_type = _required_enum(event, "event_types", _bounded_text(event_type, limit=80))
    outcome = _required_enum(event, "outcomes", _bounded_text(outcome, limit=80))
    failure_layer = _required_enum(event, "failure_layers", _bounded_text(failure_layer, limit=80))

    trace = _bounded_text(trace_id or new_trace_id(), limit=120)
    payload = {
        "trace_id": trace,
        "recorded_at": _utc_now(),
        "event_type": event_type,
        "principal": _bounded_text(principal, limit=160),
        "tool_name": _bounded_text(tool_name, limit=160),
        "task_fingerprint": _bounded_text(task_fingerprint, limit=128),
        "context_fingerprint": _bounded_text(context_fingerprint, limit=128),
        "policy_version": _bounded_text(policy_version, limit=80),
        "state_change": _bounded_text(state_change, limit=160),
        "retry_count": max(0, int(retry_count or 0)),
        "latency_ms": round(max(0.0, float(latency_ms or 0.0)), 2),
        "outcome": outcome,
        "failure_layer": failure_layer,
        "claim_boundary": str(contract.get("scope", {}).get("claim_boundary") or UNKNOWN_VALUE),
        "details": _sanitized_details(event, event_type, details),
    }
    manager = SQLiteManager(db_path or current_trace_storage() or (RAW_DIR / "codemaps.db"))
    manager.initialize_schema()
    retention = contract.get("retention") if isinstance(contract.get("retention"), dict) else {}
    retained_events = max(
        1,
        int(max_events if max_events is not None else retention.get("max_events_per_tenant") or 1000),
    )
    with manager.get_connection() as conn:
        conn.execute(
            """
            INSERT INTO governance_trace_events (
                trace_id, recorded_at, event_type, principal, tool_name,
                task_fingerprint, context_fingerprint, policy_version, state_change,
                retry_count, latency_ms, outcome, failure_layer, claim_boundary, details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                payload["trace_id"], payload["recorded_at"], payload["event_type"], payload["principal"],
                payload["tool_name"], payload["task_fingerprint"], payload["context_fingerprint"],
                payload["policy_version"], payload["state_change"], payload["retry_count"],
                payload["latency_ms"], payload["outcome"], payload["failure_layer"],
                payload["claim_boundary"], json.dumps(payload["details"], ensure_ascii=False, sort_keys=True),
            ),
        )
        if max_events is None:
            conn.execute(
                """
                DELETE FROM governance_trace_events
                WHERE event_id IN (
                    SELECT event_id FROM governance_trace_events
                    ORDER BY event_id DESC
                    LIMIT -1 OFFSET ?
                );
                """,
                (retained_events,),
            )
        else:
            conn.execute(
                """
                DELETE FROM governance_trace_events
                WHERE event_id IN (
                    SELECT event_id FROM governance_trace_events
                    WHERE event_type = ?
                    ORDER BY event_id DESC
                    LIMIT -1 OFFSET ?
                );
                """,
                (event_type, retained_events),
            )
    return payload


def record_mcp_tool_trace(
    *,
    tool_name: str,
    profile: str,
    arguments: dict[str, Any],
    started: float,
    outcome: str,
    failure_layer: str = "none",
    trace_id: str | None = None,
    payload_chars: int = 0,
    result_status: str = "unknown",
    fail_closed_reason: str = "",
    target_mode: str = "default_repository",
    db_path: Path | None = None,
    max_events: int | None = None,
) -> dict[str, Any]:
    """Record a bounded MCP call handoff without persisting raw arguments."""
    return record_trace_event(
        event_type="mcp_tool_call",
        principal=f"mcp_profile:{_bounded_text(profile, limit=100)}",
        tool_name=tool_name,
        task_fingerprint=UNKNOWN_VALUE,
        context_fingerprint=fingerprint({"tool_name": tool_name, "argument_keys": sorted(arguments)}),
        policy_version=str(_contract().get("meta", {}).get("version") or UNKNOWN_VALUE),
        state_change="tool_call_completed" if outcome == "success" else "tool_call_failed",
        latency_ms=(time.perf_counter() - started) * 1000,
        outcome=outcome,
        failure_layer=failure_layer,
        details={
            "argument_keys": sorted(str(key) for key in arguments),
            "payload_chars": max(0, int(payload_chars or 0)),
            "result_status": _bounded_text(result_status, limit=80),
            "fail_closed_reason": _bounded_text(fail_closed_reason or "none", limit=120),
            "target_mode": _bounded_text(target_mode, limit=80),
        },
        trace_id=trace_id,
        db_path=db_path,
        max_events=max_events,
    )


def record_watchdog_session_trace(session: dict[str, Any]) -> dict[str, Any]:
    """Record bounded watchdog session evidence without retaining file or finding content."""
    summary = session.get("summary") if isinstance(session.get("summary"), dict) else {}
    descriptor = session.get("target_descriptor") if isinstance(session.get("target_descriptor"), dict) else {}
    debt_field = str(descriptor.get("proof_debt_field") or "target_repository_deep_proof_debt")
    debt = session.get(debt_field) if isinstance(session.get(debt_field), dict) else {}
    ledger = session.get("watchdog_pulse_ledger") if isinstance(session.get("watchdog_pulse_ledger"), dict) else {}
    pulse_id = _bounded_text(ledger.get("pulse_id"), limit=80)
    acquisition = session.get("acquisition") if isinstance(session.get("acquisition"), dict) else {}
    provenance = (
        acquisition.get("filesystem_event_provenance")
        if isinstance(acquisition.get("filesystem_event_provenance"), dict)
        else {}
    )
    decision = acquisition.get("scope_decision") if isinstance(acquisition.get("scope_decision"), dict) else {}
    event_batch_id = _bounded_text(provenance.get("batch_id"), limit=80)
    analysis_status = _bounded_text(summary.get("analysis_status") or "completed", limit=80)
    analysis_completed = analysis_status == "completed"
    return record_trace_event(
        event_type="watchdog_session",
        principal="watchdog",
        task_fingerprint=UNKNOWN_VALUE,
        context_fingerprint=fingerprint(
            {
                "pulse_id": pulse_id,
                "event_batch_id": event_batch_id,
                "analysis_status": analysis_status,
                "changed_file_count": int(summary.get("changed_files") or 0),
                "violation_count": int(summary.get("violation_count") or 0),
            }
        ),
        policy_version=str(_contract().get("meta", {}).get("version") or UNKNOWN_VALUE),
        state_change="watchdog_pulse_completed" if analysis_completed else "watchdog_event_scope_observed",
        latency_ms=float(summary.get("elapsed_seconds") or 0) * 1000,
        outcome="success" if analysis_completed or analysis_status == "no_content_change" else "unknown",
        failure_layer="none" if analysis_completed or analysis_status == "no_content_change" else "unknown",
        details={
            "pulse_id": pulse_id,
            "event_batch_id": event_batch_id,
            "analysis_status": analysis_status,
            "raw_event_count": int(summary.get("raw_event_count") or 0),
            "deduplicated_path_count": int(summary.get("deduplicated_path_count") or 0),
            "metadata_only_count": int(summary.get("metadata_only_count") or 0),
            "unknown_path_count": int(summary.get("unknown_path_count") or 0),
            "scope_decision_status": _bounded_text(decision.get("status"), limit=80),
            "changed_file_count": int(summary.get("changed_files") or 0),
            "violation_count": int(summary.get("violation_count") or 0),
            "system_scope": _bounded_text(descriptor.get("system_scope"), limit=80),
            "profile_id": _bounded_text(descriptor.get("profile_id"), limit=80),
            "proof_debt_field": _bounded_text(debt_field, limit=80),
            "deep_proof_debt_status": _bounded_text(debt.get("status"), limit=80),
        },
        trace_id=(
            f"watchdog:{pulse_id}"
            if analysis_completed and pulse_id != UNKNOWN_VALUE
            else f"watchdog-event:{event_batch_id}"
            if event_batch_id != UNKNOWN_VALUE
            else None
        ),
    )


def record_release_proof_step_trace(step_result: dict[str, Any]) -> dict[str, Any]:
    """Record release-step outcome metadata without retaining commands or output text."""
    step_id = _bounded_text(step_result.get("id"), limit=120)
    execution_status = _bounded_text(
        step_result.get("execution_status") or "COMPLETED",
        limit=80,
    )
    dependency_blocked = execution_status == "BLOCKED_BY_FAILED_DEPENDENCY"
    blocked_dependencies = step_result.get("blocked_by_failed_dependencies")
    blocked_dependency_count = len(blocked_dependencies) if isinstance(blocked_dependencies, list) else 0
    advisory_dependencies = step_result.get("advisory_failed_dependencies")
    advisory_dependency_failure_count = (
        len(advisory_dependencies) if isinstance(advisory_dependencies, list) else 0
    )
    return record_trace_event(
        event_type="release_proof_step",
        principal="release_proof_runner",
        task_fingerprint=fingerprint({"scope": "release_proof", "step_id": step_id}),
        context_fingerprint=_bounded_text(step_result.get("raw_artifact_sha256"), limit=128),
        policy_version=str(_contract().get("meta", {}).get("version") or UNKNOWN_VALUE),
        state_change=(
            "release_proof_step_blocked_by_failed_dependency"
            if dependency_blocked
            else "release_proof_step_completed"
        ),
        latency_ms=float(step_result.get("duration_seconds") or 0) * 1000,
        outcome="success" if bool(step_result.get("passed")) else "failure",
        failure_layer="none" if bool(step_result.get("passed")) else "validation",
        details={
            "step_id": step_id,
            "required": bool(step_result.get("required")),
            "timed_out": bool(step_result.get("timed_out")),
            "timeout_basis": _bounded_text(step_result.get("timeout_basis"), limit=80),
            "raw_artifact_present": bool(step_result.get("raw_artifact")),
            "execution_status": execution_status,
            "blocked_dependency_count": blocked_dependency_count,
            "advisory_dependency_failure_count": advisory_dependency_failure_count,
        },
        trace_id=f"release-proof:{step_id}:{_bounded_text(step_result.get('started_at'), limit=80)}",
    )


def record_agent_handoff_trace(packet: dict[str, Any], *, trace_id: str | None = None) -> dict[str, Any]:
    """Record a bounded packet handoff without retaining the packet's code or evidence text."""
    meta = packet.get("meta") if isinstance(packet.get("meta"), dict) else {}
    summary = packet.get("summary") if isinstance(packet.get("summary"), dict) else {}
    grounding = packet.get("source_grounding") if isinstance(packet.get("source_grounding"), dict) else {}
    packet_kind = _bounded_text(meta.get("kind"), limit=120)
    packet_status = _bounded_text(summary.get("status") or summary.get("integrity"), limit=80)
    grounding_status = _bounded_text(
        grounding.get("drift_check_status") or grounding.get("status"),
        limit=80,
    )
    return record_trace_event(
        event_type="agent_handoff",
        principal="agent_packet_generator",
        tool_name="get_surgical_operation_packet",
        task_fingerprint=fingerprint({"packet_kind": packet_kind, "focus_count": summary.get("returned_focus_files")}),
        context_fingerprint=fingerprint({"packet_kind": packet_kind, "source_grounding_status": grounding_status}),
        policy_version=str(_contract().get("meta", {}).get("version") or UNKNOWN_VALUE),
        state_change="agent_packet_handed_off",
        outcome="success",
        failure_layer="none",
        details={
            "packet_kind": packet_kind,
            "packet_status": packet_status,
            "source_grounding_status": grounding_status,
        },
        trace_id=trace_id,
    )


def load_trace_events(
    *,
    trace_id: str = "",
    event_type: str = "",
    limit: int = 50,
    db_path: Path | None = None,
    initialize_schema: bool = True,
) -> list[dict[str, Any]]:
    """Read bounded local trace events for diagnostics; never infer root cause."""
    resolved_db_path = db_path or current_trace_storage() or (RAW_DIR / "codemaps.db")
    if not initialize_schema and not Path(resolved_db_path).exists():
        return []
    manager = SQLiteManager(resolved_db_path)
    if initialize_schema:
        manager.initialize_schema()
    bounded_limit = max(1, min(200, int(limit or 50)))
    query = "SELECT * FROM governance_trace_events"
    values: list[Any] = []
    clauses: list[str] = []
    if trace_id:
        clauses.append("trace_id = ?")
        values.append(trace_id)
    if event_type:
        clauses.append("event_type = ?")
        values.append(event_type)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY event_id DESC LIMIT ?"
    values.append(bounded_limit)
    with manager.get_connection() as conn:
        rows = conn.execute(query, values).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["details"] = json.loads(str(item.pop("details_json") or "{}"))
        result.append(item)
    return result
