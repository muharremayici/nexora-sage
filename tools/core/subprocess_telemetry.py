from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tools.core.operational_limits import pipeline_step_heartbeat_seconds
from tools.core.heartbeat_cadence import record_execution_duration
from tools.core.python_runtime_env import utf8_subprocess_env


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
) -> tuple[subprocess.CompletedProcess[str], float]:
    """Run a child process without turning long waits into a black box.

    The implementation uses communicate(timeout=...) so captured stdout/stderr
    cannot fill OS pipes and freeze the child while the parent emits progress.
    """

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
