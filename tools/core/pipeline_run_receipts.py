"""Durable, non-authoritative lifecycle receipts for material pipeline runs."""

from __future__ import annotations

import ctypes
import json
import os
import sqlite3
import sys
import time
import uuid
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.core.config import CONFIG_DIR, RAW_DIR, save_json_atomic
from tools.core.governance_trace import fingerprint, record_trace_event
from tools.core.heartbeat_cadence import heartbeat_cadence_selection, record_execution_duration
from tools.core.honesty_telemetry import record_honesty_event
from tools.core.json_io import load_json_file, load_json_object_strict
from tools.core.unmanaged_atomic_io import native_filesystem_path


POLICY_PATH = CONFIG_DIR / "pipeline_execution_policy.json"
TRACE_CONTRACT_PATH = CONFIG_DIR / "governance_trace_contract.json"
DEFAULT_SHADOW_PATH = RAW_DIR / "pipeline_run_receipt.json"
TERMINAL_STATES = {"PASS", "FAILED", "BLOCKED", "INTERRUPTED"}


def pipeline_run_receipt_contract() -> dict[str, Any]:
    policy = load_json_object_strict(POLICY_PATH, label="Pipeline execution policy")
    contract = policy.get("pipeline_run_receipts")
    if not isinstance(contract, dict):
        raise ValueError("pipeline_execution_policy.pipeline_run_receipts must be an object")
    return contract


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_utc(value: Any) -> float | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return round(ordered[0], 3)
    rank = (len(ordered) - 1) * min(1.0, max(0.0, float(percentile)))
    lower = int(rank)
    upper = min(len(ordered) - 1, lower + 1)
    value = ordered[lower] + (ordered[upper] - ordered[lower]) * (rank - lower)
    return round(value, 3)


def _duration_guidance(command_profile: str) -> dict[str, Any]:
    contract = pipeline_run_receipt_contract()
    telemetry = load_json_file(RAW_DIR / "telemetry_traces.json", {})
    traces = telemetry.get("traces", []) if isinstance(telemetry, dict) else []
    sample_limit = max(1, int(contract.get("duration_sample_limit") or 10))
    minimum_samples = max(1, int(contract.get("duration_minimum_samples") or 2))
    durations = [
        float(row.get("execution_ms") or 0) / 1000.0
        for row in traces
        if isinstance(row, dict)
        and str(row.get("type") or "") == "pipeline_execution"
        and str(row.get("identifier") or "") == command_profile
        and float(row.get("execution_ms") or 0) > 0
    ][-sample_limit:]
    available = len(durations) >= minimum_samples
    return {
        "basis": "local_exact_profile_percentiles" if available else "insufficient_exact_profile_samples",
        "sample_count": len(durations),
        "minimum_samples": minimum_samples,
        "p50_seconds": _percentile(durations, 0.50) if available else None,
        "p95_seconds": _percentile(durations, 0.95) if available else None,
    }


def _process_alive(pid: int) -> bool | None:
    if pid <= 0:
        return None
    if pid == os.getpid():
        return True
    if sys.platform == "win32":
        try:
            from ctypes import wintypes

            process_query_limited_information = 0x1000
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
            if not handle:
                return None if ctypes.get_last_error() == 5 else False
            kernel32.CloseHandle(handle)
            return True
        except Exception:
            return None
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None


def _database_path(db_path: Path | None = None) -> Path:
    return Path(db_path) if db_path is not None else RAW_DIR / "codemaps.db"


def _query_events(*, run_id: str = "", db_path: Path | None = None) -> list[dict[str, Any]]:
    database = _database_path(db_path)
    if not Path(native_filesystem_path(database)).is_file():
        return []
    with closing(sqlite3.connect(native_filesystem_path(database), timeout=5)) as conn:
        conn.row_factory = sqlite3.Row
        selected_run_id = str(run_id or "").strip()
        if not selected_run_id:
            row = conn.execute(
                """
                SELECT trace_id FROM governance_trace_events
                WHERE event_type = 'pipeline_invocation'
                ORDER BY event_id DESC LIMIT 1;
                """
            ).fetchone()
            if row is None:
                return []
            selected_run_id = str(row["trace_id"])
        rows = conn.execute(
            """
            SELECT * FROM (
                SELECT * FROM governance_trace_events
                WHERE event_type = 'pipeline_invocation' AND trace_id = ?
                ORDER BY event_id DESC LIMIT 500
            ) ORDER BY event_id ASC;
            """,
            (selected_run_id,),
        ).fetchall()
    events: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["details"] = json.loads(str(item.pop("details_json") or "{}"))
        events.append(item)
    return events


def pipeline_run_status(
    run_id: str = "",
    *,
    db_path: Path | None = None,
    now_epoch: float | None = None,
) -> dict[str, Any]:
    try:
        events = _query_events(run_id=run_id, db_path=db_path)
    except sqlite3.OperationalError as exc:
        return {
            "meta": {"kind": "pipeline_run_receipt", "version": "1.0.0"},
            "status": "QUERY_UNAVAILABLE",
            "run_id": str(run_id or "not_available"),
            "retry_guidance": "Receipt storage is temporarily unavailable. Do not retry the pipeline; retry this status query.",
            "claim_boundary": f"No terminal or engineering conclusion is available from this query ({type(exc).__name__}).",
        }
    if not events:
        return {
            "meta": {"kind": "pipeline_run_receipt", "version": "1.0.0"},
            "status": "NOT_FOUND",
            "run_id": str(run_id or "not_available"),
            "retry_guidance": "No run receipt was found. Verify the target root and run id before starting work.",
            "claim_boundary": "No execution or engineering conclusion is available.",
        }

    contract = pipeline_run_receipt_contract()
    latest = events[-1]
    details = latest.get("details") if isinstance(latest.get("details"), dict) else {}
    first_details = events[0].get("details") if isinstance(events[0].get("details"), dict) else {}
    run = str(latest.get("trace_id") or run_id)
    terminal_status = str(details.get("terminal_status") or "")
    pid = int(first_details.get("pid") or details.get("pid") or 0)
    heartbeat_seconds = max(1, int(first_details.get("heartbeat_seconds") or 15))
    updated_epoch = _parse_utc(latest.get("recorded_at"))
    age_seconds = max(0.0, float(now_epoch or time.time()) - updated_epoch) if updated_epoch else None
    alive = _process_alive(pid)

    if terminal_status in TERMINAL_STATES:
        status = terminal_status
    elif alive is True:
        stale_limit = heartbeat_seconds * max(2, int(contract.get("stale_after_heartbeat_intervals") or 3))
        status = "ACTIVE_STALE_HEARTBEAT" if age_seconds is not None and age_seconds > stale_limit else "ACTIVE"
    elif alive is False:
        status = "INTERRUPTED_UNCONFIRMED"
    else:
        status = "UNKNOWN"

    if status == "ACTIVE":
        retry_guidance = "Do not retry. Continue polling this run_id; a transport timeout is not process failure."
    elif status == "ACTIVE_STALE_HEARTBEAT":
        retry_guidance = "Do not retry. Inspect the recorded PID and poll again; stale heartbeat alone is not terminal failure."
    elif status == "INTERRUPTED_UNCONFIRMED":
        retry_guidance = "The recorded process is absent without a terminal event. Human review is required before retry."
    elif status in {"FAILED", "BLOCKED", "INTERRUPTED"}:
        retry_guidance = "Review the terminal reason and current repository state before authorizing another run."
    elif status == "PASS":
        retry_guidance = "No retry is needed for execution. Validate produced engineering evidence separately."
    else:
        retry_guidance = "Run state is unknown. Do not launch a duplicate until process and receipt state are reconciled."

    step_states: dict[str, str] = {}
    evidence_identities: list[str] = []
    blocking_run: dict[str, Any] = {}
    phase_timestamps: dict[str, str] = {}
    execution_claim_plan: dict[str, Any] = {}
    shadow_closeout: dict[str, Any] = {
        "started_count": 0,
        "completed_count": 0,
        "failed_count": 0,
        "pending_count": 0,
        "global_pending_count": 0,
        "worker_ids": [],
        "artifacts": [],
        "identities_truncated": False,
        "flush_timed_out": False,
        "flush_wait_seconds": 0.0,
    }
    for event in events:
        row = event.get("details") if isinstance(event.get("details"), dict) else {}
        phase = str(row.get("phase") or "")
        if phase in {"pipeline_completed", "evidence_closeout", "process_exit_ready", "terminal"}:
            phase_timestamps[f"{phase}_at"] = str(event.get("recorded_at") or "")
        step_id = str(row.get("step_id") or "")
        step_status = str(row.get("step_status") or "")
        if step_id not in {"", "not_available"} and step_status not in {"", "not_available"}:
            step_states[step_id] = step_status
        if str(row.get("active_run_id") or "") not in {"", "not_available"}:
            blocking_run = {
                "run_id": row.get("active_run_id"),
                "pid": row.get("active_pid"),
            }
        if str(row.get("execution_claim_profile") or "") not in {"", "not_available"}:
            execution_claim_plan = {
                "profile": str(row.get("execution_claim_profile")),
                "target_step": str(row.get("execution_claim_target_step") or ""),
                "step_count": int(row.get("execution_claim_step_count") or 0),
                "claim_boundary": str(row.get("execution_claim_boundary") or ""),
                "release_authority": row.get("execution_claim_release_authority") is True,
                "project_scope": {
                    "requested_projects": [
                        str(item) for item in row.get("execution_claim_projects", [])
                    ] if isinstance(row.get("execution_claim_projects"), list) else [],
                },
                "excluded_direct_dependency_slugs": [
                    str(item) for item in row.get("execution_claim_excluded_steps", [])
                ] if isinstance(row.get("execution_claim_excluded_steps"), list) else [],
                "cost_band": {
                    "status": str(row.get("execution_claim_cost_status") or "UNKNOWN"),
                    "basis": str(row.get("execution_claim_cost_basis") or ""),
                },
            }
        for identity in row.get("evidence_identities", []) if isinstance(row.get("evidence_identities"), list) else []:
            if str(identity) not in evidence_identities:
                evidence_identities.append(str(identity))
        for source_key, target_key in (
            ("shadow_worker_started_count", "started_count"),
            ("shadow_worker_completed_count", "completed_count"),
            ("shadow_worker_failed_count", "failed_count"),
            ("shadow_worker_pending_count", "pending_count"),
            ("shadow_worker_global_pending_count", "global_pending_count"),
            ("shadow_flush_wait_seconds", "flush_wait_seconds"),
        ):
            if source_key in row:
                shadow_closeout[target_key] = row[source_key]
        if "shadow_flush_timed_out" in row:
            shadow_closeout["flush_timed_out"] = bool(row["shadow_flush_timed_out"])
        if "shadow_worker_identities_truncated" in row:
            shadow_closeout["identities_truncated"] = bool(row["shadow_worker_identities_truncated"])
        for source_key, target_key in (
            ("shadow_worker_ids", "worker_ids"),
            ("shadow_artifacts", "artifacts"),
        ):
            values = row.get(source_key)
            if isinstance(values, list):
                shadow_closeout[target_key] = [str(item) for item in values[:50]]

    return {
        "meta": {
            "kind": "pipeline_run_receipt",
            "version": "1.0.0",
            "source_of_truth": "SQLite governance_trace_events",
            "shadow_authority": "projection_only",
        },
        "status": status,
        "run_id": run,
        "command_profile": first_details.get("command_profile"),
        "scope": first_details.get("scope"),
        "projects": first_details.get("projects", []),
        "pid": pid or None,
        "process_alive": alive,
        "started_at": events[0].get("recorded_at"),
        "updated_at": latest.get("recorded_at"),
        "last_phase": details.get("phase"),
        "age_seconds": round(age_seconds, 3) if age_seconds is not None else None,
        "heartbeat_seconds": heartbeat_seconds,
        "client_timeout_floor_seconds": first_details.get("client_timeout_floor_seconds"),
        "duration_guidance": {
            "basis": first_details.get("duration_basis"),
            "sample_count": first_details.get("duration_sample_count"),
            "p50_seconds": first_details.get("duration_p50_seconds"),
            "p95_seconds": first_details.get("duration_p95_seconds"),
        },
        "terminal": {
            "status": terminal_status or None,
            "exit_code": details.get("exit_code"),
            "interruption_reason": details.get("interruption_reason"),
            "governance_verdict": details.get("governance_verdict"),
        },
        "analysis_scope_evidence": {
            "topology_authority_id": details.get("scope_topology_authority_id"),
            "scope_authority_id": details.get("scope_authority_id"),
            "evidence_status": details.get("scope_evidence_status"),
            "claim_scope": details.get("scope_claim"),
            "full_repository_claim_eligible": details.get("scope_full_repository_claim_eligible"),
            "repository_inventory_file_count": details.get("scope_repository_inventory_file_count"),
            "effective_supported_source_file_count": details.get("scope_effective_supported_source_file_count"),
            "indexed_source_file_count": details.get("scope_indexed_source_file_count"),
            "claim_eligible_source_file_count": details.get("scope_claim_eligible_source_file_count"),
            "discovered_candidate_count": details.get("scope_discovered_candidate_count"),
            "auto_selected_project_count": details.get("scope_auto_selected_project_count"),
            "effective_project_count": details.get("scope_effective_project_count"),
            "excluded_project_count": details.get("scope_excluded_project_count"),
            "unresolved_project_candidate_count": details.get("scope_unresolved_project_candidate_count"),
            "mismatch_reasons": details.get("scope_mismatch_reasons", []),
            "consumer_checks": details.get("scope_consumer_checks", []),
            "artifact": details.get("scope_evidence_artifact"),
        },
        "lifecycle": {
            "pipeline_completed_at": phase_timestamps.get("pipeline_completed_at") or None,
            "evidence_closeout_at": phase_timestamps.get("evidence_closeout_at") or None,
            "process_exit_ready_at": phase_timestamps.get("process_exit_ready_at") or None,
            "terminal_at": phase_timestamps.get("terminal_at") or None,
        },
        "shadow_write_closeout": shadow_closeout,
        "blocking_run": blocking_run or None,
        "steps": step_states,
        "execution_claim_plan": execution_claim_plan or None,
        "evidence_identities": evidence_identities[:50],
        "event_count": len(events),
        "retry_guidance": retry_guidance,
        "status_query": str(
            first_details.get("status_query")
            or contract.get("status_query")
            or "python sage.py run-status --run-id <run_id>"
        ),
        "engineering_claim_validity": "not_evaluated_by_execution_receipt",
        "claim_boundary": str(contract.get("claim_boundary") or ""),
    }


def render_pipeline_run_status(payload: dict[str, Any]) -> str:
    lines = [
        "# Nexora SAGE Pipeline Run Receipt",
        "",
        f"- run_id: `{payload.get('run_id')}`",
        f"- status: `{payload.get('status')}`",
        f"- command_profile: `{payload.get('command_profile')}`",
        f"- scope: `{payload.get('scope')}`",
        f"- projects: `{', '.join(str(item) for item in payload.get('projects', [])) or 'all'}`",
        f"- pid: `{payload.get('pid')}`",
        f"- process_alive: `{payload.get('process_alive')}`",
        f"- execution_claim_profile: `{(payload.get('execution_claim_plan') or {}).get('profile') or 'none'}`",
        f"- execution_claim_steps: `{(payload.get('execution_claim_plan') or {}).get('step_count') or 0}`",
        f"- last_phase: `{payload.get('last_phase')}`",
        f"- updated_at: `{payload.get('updated_at')}`",
        f"- status_query: `{payload.get('status_query')}`",
        f"- pipeline_completed_at: `{payload.get('lifecycle', {}).get('pipeline_completed_at')}`",
        f"- evidence_closeout_at: `{payload.get('lifecycle', {}).get('evidence_closeout_at')}`",
        f"- process_exit_ready_at: `{payload.get('lifecycle', {}).get('process_exit_ready_at')}`",
        "",
        "## Retry Guidance",
        "",
        str(payload.get("retry_guidance") or ""),
        "",
        "## Claim Boundary",
        "",
        str(payload.get("claim_boundary") or ""),
        "",
    ]
    return "\n".join(lines)


@dataclass
class PipelineRunRecorder:
    run_id: str
    command_profile: str
    scope: str
    projects: list[str]
    context: dict[str, Any]
    db_path: Path | None = None
    shadow_path: Path | None = None
    pid: int = field(default_factory=os.getpid)
    started_monotonic: float = field(default_factory=time.perf_counter)
    started_at: str = field(default_factory=_utc_now)
    terminal_recorded: bool = False
    evidence_closeout_recorded: bool = False
    process_exit_ready_recorded: bool = False
    last_payload: dict[str, Any] = field(default_factory=dict)

    def _write_event(self, *, lifecycle_state: str, phase: str, **details: Any) -> dict[str, Any]:
        contract = pipeline_run_receipt_contract()
        payload = {
            "run_id": self.run_id,
            "lifecycle_state": lifecycle_state,
            "phase": phase,
            "pid": self.pid,
            "command_profile": self.command_profile,
            "scope": self.scope,
            "projects": self.projects,
            "status_query": self._status_query(),
            "retry_rule": str(contract.get("retry_rule") or ""),
            **details,
        }
        terminal_status = str(payload.get("terminal_status") or "")
        outcome = "success" if terminal_status == "PASS" else "failure" if terminal_status in {"FAILED", "BLOCKED"} else "unknown"
        failure_layer = "tool_execution" if outcome == "failure" else "unknown" if terminal_status == "INTERRUPTED" else "none"
        event = record_trace_event(
            event_type="pipeline_invocation",
            principal="sage_pipeline_operator",
            tool_name="pipeline_run",
            task_fingerprint=fingerprint({"profile": self.command_profile, "projects": self.projects}),
            context_fingerprint=fingerprint(self.context),
            policy_version=str(load_json_object_strict(TRACE_CONTRACT_PATH).get("meta", {}).get("version") or "not_available"),
            state_change=f"pipeline_run_{lifecycle_state.lower()}",
            latency_ms=(time.perf_counter() - self.started_monotonic) * 1000,
            outcome=outcome,
            failure_layer=failure_layer,
            details=payload,
            trace_id=self.run_id,
            db_path=self.db_path,
        )
        self.last_payload = payload
        if lifecycle_state in {"STARTED", "TERMINAL"} or phase in {"heartbeat", "evidence_closeout"}:
            try:
                self._write_shadow()
            except Exception as exc:
                print(
                    f"[RUN_RECEIPT] shadow_status=DEGRADED run_id={self.run_id} "
                    f"error_type={type(exc).__name__}",
                    file=sys.stderr,
                    flush=True,
                )
        return event

    def _write_shadow(self) -> None:
        path = Path(self.shadow_path) if self.shadow_path is not None else DEFAULT_SHADOW_PATH
        projection = pipeline_run_status(self.run_id, db_path=self.db_path)
        # This is a projection of the SQLite receipt, not another managed raw
        # artifact generation. Terminal closeout must not enqueue a new shadow.
        save_json_atomic(path, projection, bypass_proxy=True)

    def start(self) -> None:
        contract = pipeline_run_receipt_contract()
        cadence = heartbeat_cadence_selection("pipeline")
        guidance = _duration_guidance(self.command_profile)
        self._write_event(
            lifecycle_state="STARTED",
            phase="acquisition",
            heartbeat_seconds=int(cadence.get("interval_seconds") or 15),
            duration_basis=guidance.get("basis"),
            duration_sample_count=guidance.get("sample_count"),
            duration_p50_seconds=guidance.get("p50_seconds"),
            duration_p95_seconds=guidance.get("p95_seconds"),
            client_timeout_floor_seconds=int(contract.get("client_timeout_floor_seconds") or 0),
        )
        print(
            f"[RUN_RECEIPT] run_id={self.run_id} status=ACTIVE profile={self.command_profile} "
            f"heartbeat_seconds={cadence.get('interval_seconds')} "
            f"client_timeout_floor_seconds={contract.get('client_timeout_floor_seconds')}",
            flush=True,
        )
        print(
            f"[RUN_RECEIPT] query=\"{self._status_query()}\" "
            "transport_timeout_does_not_authorize_retry=true",
            flush=True,
        )

    def _status_query(self) -> str:
        contract = pipeline_run_receipt_contract()
        if str(self.context.get("acquisition_mode") or "") == "EXPLICIT_TARGET":
            template = str(
                contract.get("external_status_query")
                or "python sage.py run-status --target-root <repository> --run-id <run_id>"
            )
        else:
            template = str(contract.get("status_query") or "python sage.py run-status --run-id <run_id>")
        return template.replace("<run_id>", self.run_id)

    def progress(self, phase: str, **details: Any) -> None:
        if self.terminal_recorded:
            return
        normalized_phase = str(phase or "not_available")
        if normalized_phase == "process_exit_ready" and not self.evidence_closeout_recorded:
            raise RuntimeError("process_exit_ready requires a completed evidence_closeout phase")
        self._write_event(lifecycle_state="PROGRESS", phase=normalized_phase, **details)
        if normalized_phase == "evidence_closeout":
            self.evidence_closeout_recorded = True
        elif normalized_phase == "process_exit_ready":
            self.process_exit_ready_recorded = True

    def terminal(
        self,
        *,
        terminal_status: str,
        exit_code: int,
        interruption_reason: str = "",
        governance_verdict: str = "NOT_EVALUATED",
        completed_steps: list[str] | None = None,
        failed_steps: list[str] | None = None,
        skipped_steps: list[str] | None = None,
        evidence_identities: list[str] | None = None,
        scope_details: dict[str, Any] | None = None,
    ) -> None:
        if self.terminal_recorded:
            return
        if not self.process_exit_ready_recorded:
            raise RuntimeError("Pipeline terminal receipt requires process_exit_ready closeout evidence")
        normalized = str(terminal_status or "FAILED").upper()
        if normalized not in TERMINAL_STATES:
            normalized = "FAILED"
        elapsed = round(time.perf_counter() - self.started_monotonic, 3)
        self._write_event(
            lifecycle_state="TERMINAL",
            phase="terminal",
            terminal_status=normalized,
            exit_code=int(exit_code),
            interruption_reason=interruption_reason or "none",
            governance_verdict=governance_verdict or "NOT_EVALUATED",
            elapsed_seconds=elapsed,
            completed_steps=sorted(completed_steps or []),
            failed_steps=sorted(failed_steps or []),
            skipped_steps=sorted(skipped_steps or []),
            evidence_identities=sorted(evidence_identities or [])[:50],
            **dict(scope_details or {}),
        )
        self.terminal_recorded = True
        try:
            record_execution_duration("pipeline_execution", self.command_profile, elapsed)
        except Exception as exc:
            print(
                f"[RUN_RECEIPT] duration_telemetry_status=DEGRADED run_id={self.run_id} "
                f"error_type={type(exc).__name__}",
                file=sys.stderr,
                flush=True,
            )
        print(
            f"[RUN_RECEIPT] run_id={self.run_id} terminal_status={normalized} "
            f"exit_code={int(exit_code)} elapsed_seconds={elapsed}",
            flush=True,
        )


def start_pipeline_run_receipt(
    *,
    command_profile: str,
    scope: str,
    projects: list[str] | None,
    context: dict[str, Any],
    run_id: str = "",
    db_path: Path | None = None,
    shadow_path: Path | None = None,
    pid: int | None = None,
) -> PipelineRunRecorder | None:
    recorder = PipelineRunRecorder(
        run_id=str(run_id or f"sage-run-{uuid.uuid4()}"),
        command_profile=str(command_profile or "run:full"),
        scope=str(scope or "SAGE_ON_REPOSITORY"),
        projects=sorted({str(item) for item in (projects or []) if str(item)}),
        context=context if isinstance(context, dict) else {},
        db_path=db_path,
        shadow_path=shadow_path,
        pid=int(pid or os.getpid()),
    )
    try:
        recorder.start()
        return recorder
    except Exception as exc:
        record_honesty_event(
            component="pipeline_run_receipts",
            category="caught_error",
            operation="start_pipeline_run_receipt",
            subject=recorder.run_id,
            reason="Pipeline run receipt could not be persisted before execution.",
            fallback="continue_with_explicit_receipt_degradation",
            claim_impact="invocation_status_query_unavailable",
            exception=exc,
        )
        print(
            f"[RUN_RECEIPT] status=DEGRADED run_id={recorder.run_id} error_type={type(exc).__name__}",
            file=sys.stderr,
            flush=True,
        )
        return None
