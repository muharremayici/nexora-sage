from __future__ import annotations

import math
import os
import queue
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from statistics import median
from typing import Any, Callable, TextIO

from tools.core.config import CONFIG_DIR, RAW_DIR
from tools.core.heartbeat_cadence import record_execution_duration
from tools.core.json_io import load_json_file, load_json_object_strict
from tools.core.subprocess_telemetry import process_group_popen_kwargs, terminate_process_tree


_TELEMETRY_PATH = RAW_DIR / "telemetry_traces.json"
_OFFLINE_TOKENS = (
    "eai_again",
    "enotfound",
    "network is unreachable",
    "temporary failure in name resolution",
    "could not resolve host",
    "getaddrinfo failed",
    "connection timed out",
)


class DependencyAcquisitionError(RuntimeError):
    """One declared dependency action failed with a structured outcome."""

    def __init__(self, result: dict[str, Any]):
        self.result = result
        action_id = str(result.get("action_id") or "unknown")
        status = str(result.get("status") or "INSTALL_FAILED")
        super().__init__(f"Dependency action {action_id} ended with {status}.")


def _execution_policy() -> dict[str, Any]:
    return load_json_object_strict(
        CONFIG_DIR / "pipeline_execution_policy.json",
        label="Pipeline execution policy",
    )


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * min(1.0, max(0.0, float(percentile)))
    lower = int(rank)
    upper = min(len(ordered) - 1, lower + 1)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (rank - lower)


def _successful_duration_samples(identifier: str, *, sample_limit: int) -> list[float]:
    payload = load_json_file(_TELEMETRY_PATH, {})
    traces = payload.get("traces", []) if isinstance(payload, dict) else []
    return [
        float(row.get("execution_ms") or 0) / 1000.0
        for row in traces
        if isinstance(row, dict)
        and str(row.get("type") or "") == "subprocess_execution"
        and str(row.get("identifier") or "") == identifier
        and float(row.get("execution_ms") or 0) > 0
    ][-sample_limit:]


def select_dependency_budget(
    action: dict[str, Any],
    *,
    policy: dict[str, Any] | None = None,
    successful_durations_seconds: list[float] | None = None,
) -> dict[str, Any]:
    """Select one bounded budget from operation class and successful local evidence."""

    execution_policy = policy or _execution_policy()
    operation_class = str(action.get("operation_class") or "").strip()
    profiles = execution_policy.get("dependency_acquisition_budgets", {})
    profile = profiles.get(operation_class) if isinstance(profiles, dict) else None
    if not isinstance(profile, dict):
        raise ValueError(
            f"Dependency action '{action.get('id')}' has no budget profile for "
            f"operation class '{operation_class}'."
        )

    bootstrap = max(1.0, float(profile.get("bootstrap_hard_timeout_seconds") or 1))
    maximum = max(
        bootstrap,
        float(profile.get("maximum_hard_timeout_seconds") or bootstrap),
    )
    stall = max(1.0, float(profile.get("stall_timeout_seconds") or 1))
    minimum_samples = max(1, int(profile.get("minimum_success_samples") or 1))
    sample_limit = max(
        minimum_samples,
        int(profile.get("sample_limit") or minimum_samples),
    )
    multiplier = max(1.0, float(profile.get("observed_p95_multiplier") or 1.0))
    identifier = str(action.get("telemetry_identifier") or "").strip()
    if not identifier:
        raise ValueError(
            f"Dependency action '{action.get('id')}' has no telemetry identifier."
        )

    durations = (
        [float(value) for value in successful_durations_seconds if float(value) > 0]
        if successful_durations_seconds is not None
        else _successful_duration_samples(identifier, sample_limit=sample_limit)
    )[-sample_limit:]
    if len(durations) >= minimum_samples:
        p95 = _percentile(durations, 0.95)
        selected = min(maximum, max(bootstrap, math.ceil(p95 * multiplier)))
        basis = "successful_local_p95_clamped"
    else:
        p95 = None
        selected = bootstrap
        basis = "bootstrap_insufficient_success_samples"
    return {
        "operation_class": operation_class,
        "hard_timeout_seconds": selected,
        "maximum_hard_timeout_seconds": maximum,
        "stall_timeout_seconds": min(stall, selected),
        "basis": basis,
        "sample_count": len(durations),
        "minimum_success_samples": minimum_samples,
        "p50_seconds": round(float(median(durations)), 3) if durations else None,
        "p95_seconds": round(float(p95), 3) if p95 is not None else None,
        "observed_p95_multiplier": multiplier,
        "telemetry_identifier": identifier,
    }


def _pump_stream(
    stream: TextIO | None,
    sink: TextIO,
    source: str,
    events: queue.Queue[tuple[str, str]],
) -> None:
    if stream is None:
        return
    try:
        for line in iter(stream.readline, ""):
            sink.write(line)
            sink.flush()
            events.put((source, line))
    finally:
        stream.close()


def _failure_status(output: str) -> str:
    lowered = output.lower()
    if any(token in lowered for token in _OFFLINE_TOKENS):
        return "OFFLINE"
    return "INSTALL_FAILED"


def _progress_signature(
    paths: list[Path],
    *,
    entry_limit: int = 4096,
) -> tuple[int, int, int]:
    count = total_bytes = latest_mtime_ns = 0
    for root in paths:
        try:
            candidates = [root] if root.is_file() else (
                (
                    Path(base) / name
                    for base, _dirs, files in os.walk(root)
                    for name in files
                )
                if root.is_dir() else []
            )
            for candidate in candidates:
                try:
                    stat = candidate.stat()
                except OSError:
                    continue
                count += 1
                total_bytes += int(stat.st_size)
                latest_mtime_ns = max(latest_mtime_ns, int(stat.st_mtime_ns))
                if count >= entry_limit:
                    return count, total_bytes, latest_mtime_ns
        except OSError:
            continue
    return count, total_bytes, latest_mtime_ns


def execute_dependency_action(
    action: dict[str, Any],
    *,
    env: dict[str, str] | None = None,
    budget: dict[str, Any] | None = None,
    log: Callable[[str], None] | None = None,
    stdout_sink: TextIO | None = None,
    stderr_sink: TextIO | None = None,
) -> dict[str, Any]:
    """Run one SAGE-owned acquisition with streaming output and honest outcomes."""

    command = [str(value) for value in action.get("command", []) if str(value)]
    if not command:
        raise ValueError(f"Dependency action '{action.get('id')}' has no command.")
    if str(action.get("dependency_authority") or "") != "sage_runtime":
        raise ValueError("Only sage_runtime dependency actions may execute automatically.")
    if bool(action.get("target_repository_mutation")):
        raise ValueError("Automatic dependency acquisition may not mutate the target repository.")

    selected_budget = budget or select_dependency_budget(action)
    hard_timeout = max(0.01, float(selected_budget["hard_timeout_seconds"]))
    stall_timeout = max(0.01, float(selected_budget["stall_timeout_seconds"]))
    poll_seconds = max(
        0.01,
        min(
            0.25,
            float(selected_budget.get("poll_interval_seconds") or 0.1),
        ),
    )
    heartbeat_seconds = max(
        0.05,
        float(selected_budget.get("heartbeat_seconds") or 15.0),
    )
    cwd_value = action.get("cwd")
    cwd = Path(str(cwd_value)).resolve() if cwd_value else Path.cwd().resolve()
    if not cwd.is_dir():
        raise ValueError(f"Dependency action working directory is unavailable: {cwd}")
    progress_paths = [
        (cwd / str(value)).resolve()
        for value in action.get("progress_relative_paths", [])
        if str(value).strip()
    ]
    stall_authority_sources = {
        str(value)
        for value in action.get("stall_authority_sources", [])
        if str(value).strip()
    }
    progress_signature = _progress_signature(progress_paths)
    events: queue.Queue[tuple[str, str]] = queue.Queue()
    output_tail: deque[str] = deque(maxlen=200)
    out_sink = stdout_sink or sys.stdout
    err_sink = stderr_sink or sys.stderr
    started = time.perf_counter()
    last_progress = last_stall_progress = last_heartbeat = started
    progress_events = 0
    stall_progress_events = 0
    progress_sources: set[str] = set()
    try:
        proc_handle = subprocess.Popen(
            command,
            cwd=str(cwd),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            **process_group_popen_kwargs(),
        )
    except FileNotFoundError as exc:
        raise DependencyAcquisitionError(
            {
                "action_id": str(action.get("id") or "unknown"),
                "status": "MISSING_MANAGER",
                "dependency_authority": "sage_runtime",
                "operation_class": str(action.get("operation_class") or ""),
                "command": command,
                "cwd": str(cwd),
                "returncode": None,
                "duration_seconds": round(time.perf_counter() - started, 3),
                "budget": selected_budget,
                "progress": {
                    "event_count": 0,
                    "sources": [],
                    "stall_authority_sources": sorted(stall_authority_sources),
                    "stall_event_count": 0,
                    "last_progress_elapsed_seconds": None,
                },
                "cleanup_status": "not_started",
                "failure_evidence": {"error_type": type(exc).__name__},
                "target_repository_mutation": False,
            }
        ) from exc
    threads = [
        threading.Thread(
            target=_pump_stream,
            args=(proc_handle.stdout, out_sink, "stdout", events),
            daemon=True,
        ),
        threading.Thread(
            target=_pump_stream,
            args=(proc_handle.stderr, err_sink, "stderr", events),
            daemon=True,
        ),
    ]
    for thread in threads:
        thread.start()

    forced_status: str | None = None
    while proc_handle.poll() is None:
        now = time.perf_counter()
        while True:
            try:
                source, line = events.get_nowait()
            except queue.Empty:
                break
            output_tail.append(line[-2000:])
            progress_events += 1
            progress_sources.add(source)
            last_progress = now
            if source in stall_authority_sources:
                stall_progress_events += 1
                last_stall_progress = now
        if progress_paths:
            current_signature = _progress_signature(progress_paths)
            if current_signature != progress_signature:
                progress_signature = current_signature
                progress_events += 1
                progress_sources.add("filesystem")
                last_progress = now
                if "filesystem" in stall_authority_sources:
                    stall_progress_events += 1
                    last_stall_progress = now
        elapsed = now - started
        if (
            stall_progress_events
            and now - last_stall_progress >= stall_timeout
        ):
            forced_status = "STALLED"
            break
        if elapsed >= hard_timeout:
            forced_status = "TIMED_OUT"
            break
        if log is not None and now - last_heartbeat >= heartbeat_seconds:
            log(
                f"Dependency action {action.get('id')} running: "
                f"elapsed={round(elapsed, 1)}s budget={round(hard_timeout, 1)}s "
                f"progress_events={progress_events}."
            )
            last_heartbeat = now
        time.sleep(poll_seconds)

    cleanup_status = "not_required"
    if forced_status:
        terminate_process_tree(proc_handle)
        cleanup_status = "process_tree_terminated"
        try:
            proc_handle.wait(timeout=5)
        except subprocess.TimeoutExpired:
            terminate_process_tree(proc_handle)
            cleanup_status = "process_tree_cleanup_incomplete"
    for thread in threads:
        thread.join(timeout=1)
    while True:
        try:
            source, line = events.get_nowait()
        except queue.Empty:
            break
        output_tail.append(line[-2000:])
        progress_events += 1
        progress_sources.add(source)

    duration = round(time.perf_counter() - started, 3)
    returncode = int(proc_handle.returncode if proc_handle.returncode is not None else 124)
    combined_output = "".join(output_tail)[-4000:]
    if forced_status:
        status = forced_status
        returncode = 124
    elif returncode == 0:
        status = "SUCCESS"
    else:
        status = _failure_status(combined_output)
    result = {
        "action_id": str(action.get("id") or "unknown"),
        "status": status,
        "dependency_authority": str(action.get("dependency_authority") or ""),
        "operation_class": str(action.get("operation_class") or ""),
        "command": command,
        "cwd": str(cwd),
        "returncode": returncode,
        "duration_seconds": duration,
        "budget": selected_budget,
        "progress": {
            "event_count": progress_events,
            "sources": sorted(progress_sources),
            "stall_authority_sources": sorted(stall_authority_sources),
            "stall_event_count": stall_progress_events,
            "last_progress_elapsed_seconds": (
                round(last_progress - started, 3) if progress_events else None
            ),
        },
        "cleanup_status": cleanup_status,
        "failure_evidence": {
            "offline_tokens": [
                token for token in _OFFLINE_TOKENS if token in combined_output.lower()
            ]
        },
        "target_repository_mutation": False,
    }
    if status == "SUCCESS":
        record_execution_duration(
            "subprocess_execution",
            str(selected_budget["telemetry_identifier"]),
            duration,
        )
        return result
    raise DependencyAcquisitionError(result)
