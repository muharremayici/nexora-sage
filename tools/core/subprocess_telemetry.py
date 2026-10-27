from __future__ import annotations

import os
import hashlib
import json
import queue
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tools.core.config import CONFIG_DIR
from tools.core.json_io import load_json_object_strict
from tools.core.operational_limits import pipeline_step_heartbeat_seconds
from tools.core.heartbeat_cadence import record_execution_duration
from tools.core.python_runtime_env import utf8_subprocess_env


RAW_OBSERVATION_PROTOCOL = "sha256_stream_bytes_exact_env_closed_stdin_v1"


@dataclass(frozen=True)
class RawStreamObservation:
    bytes_seen: int
    sha256: str | None
    complete: bool
    error: str | None


@dataclass(frozen=True)
class RawCleanupObservation:
    attempted: bool
    parent_exited: bool
    readers_joined: bool
    errors: tuple[str, ...]
    descendant_quiescence: str = "not_verified"


@dataclass(frozen=True)
class RawSubprocessObservation:
    status: str
    protocol: str
    returncode: int | None
    timed_out: bool
    termination_reason: str | None
    capture_complete: bool
    stdout: RawStreamObservation
    stderr: RawStreamObservation
    cleanup: RawCleanupObservation
    duration_seconds: float
    command_sha256: str
    environment_sha256: str
    cwd_sha256: str
    policy_sha256: str
    observer_module_sha256: str
    authority: str = "diagnostic_only_no_test_or_release_admission"
    reuse: bool = False
    may_skip_tests: bool = False
    fresh_native_baseline: bool = False
    canonical_profile_reuse_enabled: bool = False


def load_raw_observation_policy(policy: object = None) -> dict[str, Any]:
    """Strict opt-in limits only; never apply defaults from the text path."""
    if policy is None:
        policy = load_json_object_strict(CONFIG_DIR / "pipeline_execution_policy.json",
            label="Pipeline execution policy")
    runtime = policy.get("subprocess_runtime_policy") if isinstance(policy, dict) else None
    raw = runtime.get("raw_observation") if isinstance(runtime, dict) else None
    if (not isinstance(raw, dict) or set(raw) != {"protocol", "authority", "limits", "rationale"}
            or raw["protocol"] != RAW_OBSERVATION_PROTOCOL
            or raw["authority"] != "diagnostic_only_no_test_or_release_admission"
            or not isinstance(raw["rationale"], str) or not raw["rationale"].strip()):
        raise ValueError("Missing or unsupported raw-observation policy")
    limits = raw["limits"]
    integer_keys = {"stdout_bytes", "stderr_bytes", "total_bytes", "read_chunk_bytes", "queue_chunks"}
    time_keys = {"poll_seconds", "cleanup_grace_seconds"}
    if (not isinstance(limits, dict) or set(limits) != integer_keys | time_keys
            or any(type(limits[k]) is not int or limits[k] <= 0 for k in integer_keys)
            or any(type(limits[k]) not in (int, float)
                or not 0 < limits[k] <= threading.TIMEOUT_MAX for k in time_keys)
            or limits["total_bytes"] > limits["stdout_bytes"] + limits["stderr_bytes"]
            or limits["read_chunk_bytes"] > min(limits["stdout_bytes"], limits["stderr_bytes"], limits["total_bytes"])
            or limits["queue_chunks"] * limits["read_chunk_bytes"] > limits["total_bytes"]):
        raise ValueError("Incomplete or inconsistent raw-observation limits")
    return {**raw, "limits": dict(limits)}


def _raw_json_sha256(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()


def _raw_stream_reader(
    name: str, stream: Any, events: queue.Queue, stopped: threading.Event,
    chunk_bytes: int, poll_seconds: float,
) -> None:
    """Drain one bounded binary pipe without retaining payload after hashing."""
    def emit(data: bytes | None, error: str | None = None) -> bool:
        while not stopped.is_set():
            try:
                events.put((name, data, error), timeout=poll_seconds)
                return True
            except queue.Full:
                continue
        return False

    error = None
    reached_eof = False
    try:
        while not stopped.is_set():
            block = stream.read(chunk_bytes)
            if not isinstance(block, bytes) or len(block) > chunk_bytes:
                raise ValueError("Raw stream did not supply bounded bytes")
            if not block:
                reached_eof = True
                break
            if not emit(block):
                break
    except (OSError, ValueError, TypeError) as exc:
        error = f"read:{type(exc).__name__}"
    finally:
        if not reached_eof:
            error = error or "read:incomplete"
        closed = False
        try:
            stream.close()
            closed = True
        except (OSError, ValueError) as exc:
            error = error or f"close:{type(exc).__name__}"
        finally:
            if not closed:
                error = error or "close:incomplete"
            emit(None, error)


def _raw_finish_process(
    process: Any, readers: list[threading.Thread], stopped: threading.Event,
    *, terminate: bool, grace_seconds: float,
) -> RawCleanupObservation:
    stopped.set()
    errors = []
    if terminate:
        try:
            terminate_process_tree(process)
        except (OSError, subprocess.SubprocessError) as exc:
            errors.append(f"terminate:{type(exc).__name__}")
        try:
            process.wait(timeout=grace_seconds)
        except (OSError, subprocess.SubprocessError) as exc:
            errors.append(f"wait:{type(exc).__name__}")
    deadline = time.monotonic() + grace_seconds
    for reader in readers:
        reader.join(timeout=max(0.0, deadline - time.monotonic()))
    return RawCleanupObservation(attempted=terminate, parent_exited=process.poll() is not None,
        readers_joined=all(not reader.is_alive() for reader in readers), errors=tuple(errors))


def _run_raw_observed_subprocess(
    command: list[str], *, cwd: Path, label: str, timeout: int | float,
    env: dict[str, str] | None, heartbeat_seconds: int | float | None,
    log: Callable[[str], None] | None,
) -> RawSubprocessObservation:
    policy = load_raw_observation_policy()
    limits = policy["limits"]
    if (not isinstance(command, list) or not command or not command[0]
            or not all(isinstance(v, str) and "\0" not in v for v in command)
            or not isinstance(env, dict)
            or not all(isinstance(k, str) and k and "=" not in k and "\0" not in k
                and isinstance(v, str) and "\0" not in v for k, v in env.items())
            or (sys.platform == "win32" and len({k.casefold() for k in env}) != len(env))
            or type(timeout) not in (int, float) or not 0 < timeout <= threading.TIMEOUT_MAX):
        raise ValueError("Raw observation requires explicit argv, exact environment and finite timeout")
    heartbeat = (pipeline_step_heartbeat_seconds(scope="subprocess")
                 if heartbeat_seconds is None else heartbeat_seconds)
    if type(heartbeat) not in (int, float) or not 0 < heartbeat <= threading.TIMEOUT_MAX:
        raise ValueError("Raw observation heartbeat must be finite and positive")
    argv, environment, directory = list(command), dict(env), str(cwd.resolve())
    module_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    started = time.monotonic()
    events: queue.Queue = queue.Queue(maxsize=limits["queue_chunks"])
    stopped = threading.Event()
    readers: list[threading.Thread] = []
    states = {name: {"bytes": 0, "digest": hashlib.sha256(), "eof": False, "error": None}
              for name in ("stdout", "stderr")}
    reason = None
    timed_out = False
    completed = False
    last_heartbeat = started
    process = subprocess.Popen(argv, cwd=directory, env=environment, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=False, bufsize=0, **process_group_popen_kwargs())
    try:
        for name in states:
            reader = threading.Thread(target=_raw_stream_reader, name=f"SAGE.RawCapture.{name}",
                args=(name, getattr(process, name), events, stopped,
                    limits["read_chunk_bytes"], limits["poll_seconds"]), daemon=True)
            reader.start()
            readers.append(reader)
        while True:
            now = time.monotonic()
            if now - started >= timeout:
                timed_out, reason = True, "timeout"
                break
            if all(s["eof"] for s in states.values()) and process.poll() is not None:
                completed = True
                break
            if now - last_heartbeat >= heartbeat:
                if log is not None:
                    log(f"WAIT {label} raw_observation runtime_seconds={round(now - started, 1)} timeout_seconds={timeout}")
                last_heartbeat = now
            try:
                name, data, error = events.get(timeout=min(limits["poll_seconds"], timeout - (now - started)))
            except queue.Empty:
                continue
            state = states[name]
            if data is None:
                state["eof"], state["error"] = error is None, error
                if error:
                    reason = "capture_error"
                    break
            else:
                state["bytes"] += len(data)
                if state["bytes"] > limits[f"{name}_bytes"]:
                    reason = f"{name}_limit"
                    break
                if sum(s["bytes"] for s in states.values()) > limits["total_bytes"]:
                    reason = "total_limit"
                    break
                state["digest"].update(data)
    finally:
        cleanup = _raw_finish_process(process, readers, stopped, terminate=not completed,
            grace_seconds=limits["cleanup_grace_seconds"])
    actual_returncode = process.returncode if type(process.returncode) is int else None
    capture_complete = (completed and actual_returncode is not None and cleanup.parent_exited
        and cleanup.readers_joined and not cleanup.errors)
    streams = {}
    for name, state in states.items():
        stream_complete = (state["eof"] and not state["error"] and cleanup.readers_joined
            and reason not in ("stdout_limit", "stderr_limit", "total_limit"))
        streams[name] = RawStreamObservation(bytes_seen=state["bytes"],
            sha256=state["digest"].hexdigest() if stream_complete else None,
            complete=stream_complete, error=state["error"])
    duration = round(time.monotonic() - started, 3)
    record_execution_duration("subprocess_execution", label, duration)
    return RawSubprocessObservation(status="CAPTURED" if capture_complete else "INCOMPLETE",
        protocol=policy["protocol"], returncode=actual_returncode, timed_out=timed_out,
        termination_reason=reason, capture_complete=capture_complete,
        stdout=streams["stdout"], stderr=streams["stderr"], cleanup=cleanup, duration_seconds=duration,
        command_sha256=_raw_json_sha256(argv), environment_sha256=_raw_json_sha256(environment),
        cwd_sha256=_raw_json_sha256(directory), policy_sha256=_raw_json_sha256(policy),
        observer_module_sha256=module_sha256)


def process_group_popen_kwargs() -> dict[str, Any]:
    """Create an isolated child-process group so timeout cleanup includes descendants."""

    if sys.platform == "win32":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}


def terminate_process_tree(proc_handle: subprocess.Popen[str]) -> None:
    """Terminate a bounded subprocess and every child it spawned."""

    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(proc_handle.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=5,
        )
        if proc_handle.poll() is None:
            proc_handle.kill()
        return
    try:
        os.killpg(proc_handle.pid, signal.SIGKILL)
    except Exception:
        proc_handle.kill()


def _collect_timed_out_process(proc_handle: subprocess.Popen[str]) -> tuple[str, str]:
    """Terminate a timed-out tree and collect the bounded parent result."""

    terminate_process_tree(proc_handle)
    try:
        return proc_handle.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        terminate_process_tree(proc_handle)
        try:
            return proc_handle.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            return "", "Timed out process tree did not exit within bounded cleanup grace period."


def run_observed_subprocess(
    command: list[str],
    *,
    cwd: Path,
    label: str,
    timeout: int | float,
    env: dict[str, str] | None = None,
    heartbeat_seconds: int | float | None = None,
    log: Callable[[str], None] | None = None,
    raw_observation: bool = False,
) -> tuple[subprocess.CompletedProcess[str], float] | RawSubprocessObservation:
    """Run a child process without turning long waits into a black box.

    The implementation uses communicate(timeout=...) so captured stdout/stderr
    cannot fill OS pipes and freeze the child while the parent emits progress.
    """

    if raw_observation is True:
        return _run_raw_observed_subprocess(command, cwd=cwd, label=label, timeout=timeout,
            env=env, heartbeat_seconds=heartbeat_seconds, log=log)
    if raw_observation is not False:
        raise ValueError("raw_observation must be an explicit boolean")
    started = time.perf_counter()
    proc_handle = subprocess.Popen(
        command,
        cwd=str(cwd),
        env=utf8_subprocess_env(env),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        **process_group_popen_kwargs(),
    )
    stdout = ""
    stderr = ""
    timed_out = False
    heartbeat = max(1.0, float(heartbeat_seconds or pipeline_step_heartbeat_seconds(scope="subprocess")))
    limit = max(1.0, float(timeout or 1))
    while True:
        elapsed = time.perf_counter() - started
        if elapsed >= limit:
            timed_out = True
            stdout, stderr = _collect_timed_out_process(proc_handle)
            break
        remaining = max(0.1, min(heartbeat, limit - elapsed))
        try:
            stdout, stderr = proc_handle.communicate(timeout=remaining)
            break
        except subprocess.TimeoutExpired:
            elapsed = time.perf_counter() - started
            if elapsed >= limit:
                timed_out = True
                stdout, stderr = _collect_timed_out_process(proc_handle)
                break
            if log is not None:
                command_preview = " ".join(str(part) for part in command[:3])
                log(
                    f"WAIT {label} cmd={command_preview} "
                    f"runtime_seconds={round(elapsed, 1)} timeout_seconds={round(limit, 1)}"
                )
                try:
                    sys.stdout.flush()
                    sys.stderr.flush()
                except Exception:
                    pass
    returncode = 124 if timed_out else int(proc_handle.returncode or 0)
    if timed_out:
        stderr = (stderr or "") + f"\nTimed out after {round(limit, 1)}s"
    completed = subprocess.CompletedProcess(command, returncode=returncode, stdout=stdout or "", stderr=stderr or "")
    duration_seconds = round(time.perf_counter() - started, 3)
    record_execution_duration("subprocess_execution", label, duration_seconds)
    return completed, duration_seconds


def subprocess_result_row(
    name: str,
    command: list[str],
    proc: subprocess.CompletedProcess[str],
    duration_seconds: float,
    *,
    cwd: Path | None = None,
    output_limit: int = 4000,
) -> dict[str, Any]:
    output = "\n".join(part for part in [(proc.stdout or "").strip(), (proc.stderr or "").strip()] if part)
    row: dict[str, Any] = {
        "name": name,
        "command": command,
        "returncode": proc.returncode,
        "passed": proc.returncode == 0,
        "duration_seconds": duration_seconds,
        "output_excerpt": output[-int(output_limit or 4000) :],
    }
    if cwd is not None:
        row["cwd"] = str(cwd)
    return row
