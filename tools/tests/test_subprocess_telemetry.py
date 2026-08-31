from __future__ import annotations

import sys
import time
from pathlib import Path
import subprocess

from tools.core import subprocess_telemetry
from tools.core.subprocess_telemetry import run_observed_subprocess


def test_observed_subprocess_enforces_timeout_and_returns_promptly(monkeypatch, tmp_path):
    monkeypatch.setattr("tools.core.subprocess_telemetry.record_execution_duration", lambda *_args: None)
    started = time.perf_counter()

    result, duration_seconds = run_observed_subprocess(
        [sys.executable, "-c", "import time; time.sleep(3)"],
        cwd=Path(tmp_path),
        label="timeout-regression-child",
        timeout=0.2,
        heartbeat_seconds=0.1,
    )

    assert result.returncode == 124
    assert "Timed out after" in result.stderr
    assert duration_seconds < 2.5
    assert time.perf_counter() - started < 2.5


def test_timed_out_collection_never_falls_back_to_unbounded_wait(monkeypatch):
    class NeverExits:
        def __init__(self) -> None:
            self.calls = 0

        def communicate(self, *, timeout: float):
            self.calls += 1
            raise subprocess.TimeoutExpired("fixture", timeout)

    terminated: list[object] = []
    monkeypatch.setattr(subprocess_telemetry, "terminate_process_tree", lambda process: terminated.append(process))
    process = NeverExits()

    stdout, stderr = subprocess_telemetry._collect_timed_out_process(process)

    assert stdout == ""
    assert "bounded cleanup grace period" in stderr
    assert process.calls == 2
    assert terminated == [process, process]


def test_observed_subprocess_enforces_utf8_without_mutating_caller_env(monkeypatch, tmp_path):
    observed = {}

    class CompletedProcess:
        returncode = 0

        def communicate(self, *, timeout: float):
            return "", ""

    def popen(command, **kwargs):
        observed["command"] = command
        observed["env"] = kwargs["env"]
        return CompletedProcess()

    source_env = {"PYTHONIOENCODING": "cp1254", "PYTHONUTF8": "0", "KEEP": "yes"}
    monkeypatch.setattr(subprocess_telemetry.subprocess, "Popen", popen)
    monkeypatch.setattr(subprocess_telemetry, "record_execution_duration", lambda *_args: None)

    result, _duration = run_observed_subprocess(
        [sys.executable, "-c", "print('ok')"],
        cwd=Path(tmp_path),
        label="utf8-child",
        timeout=1,
        env=source_env,
    )

    assert result.returncode == 0
    assert source_env["PYTHONIOENCODING"] == "cp1254"
    assert observed["env"]["PYTHONIOENCODING"] == "utf-8"
    assert observed["env"]["PYTHONUTF8"] == "1"
    assert observed["env"]["KEEP"] == "yes"
