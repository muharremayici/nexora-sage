from __future__ import annotations

import sys
import time
from pathlib import Path
import subprocess
from copy import deepcopy
import hashlib
import io
import json
import os

import pytest

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


@pytest.fixture
def raw_capture(tmp_path, monkeypatch):
    """Finite process/pipe doubles; never run proof, import host graphs or store output."""
    policy = json.loads((subprocess_telemetry.CONFIG_DIR / "pipeline_execution_policy.json").read_text(encoding="utf-8"))
    policy["subprocess_runtime_policy"]["raw_observation"]["limits"] = {
        "stdout_bytes": 128, "stderr_bytes": 128, "total_bytes": 192,
        "read_chunk_bytes": 16, "queue_chunks": 2, "poll_seconds": 0.001, "cleanup_grace_seconds": 0.05}
    config = tmp_path / "config"
    config.mkdir()
    policy_path = config / "pipeline_execution_policy.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    monkeypatch.setattr(subprocess_telemetry, "CONFIG_DIR", config)
    monkeypatch.setattr(subprocess_telemetry, "record_execution_duration", lambda *_: None)
    launched = []
    terminated = []

    class Process:
        def __init__(self, stdout=b"", stderr=b"", returncode=0):
            self.stdout, self.stderr = io.BytesIO(stdout), io.BytesIO(stderr)
            self.returncode = returncode
            self.pid = 12345

        def poll(self):
            return self.returncode

        def wait(self, timeout):
            if self.returncode is None:
                raise subprocess.TimeoutExpired("fixture", timeout)
            return self.returncode

    process = Process()
    def popen(command, **kwargs):
        launched.append((list(command), deepcopy(kwargs)))
        process.args = list(command)
        return process
    def terminate(owned):
        assert owned is process
        terminated.append(owned)
        owned.returncode = -9
    monkeypatch.setattr(subprocess_telemetry.subprocess, "Popen", popen)
    monkeypatch.setattr(subprocess_telemetry, "terminate_process_tree", terminate)
    def run(**changes):
        arguments = {"command": ["fixture-python", "fixture.py"], "cwd": tmp_path,
            "label": "raw-fixture", "timeout": 0.1, "env": {"KEEP": "value", "PYTHONUTF8": "0"},
            "raw_observation": True}
        arguments.update(changes)
        return run_observed_subprocess(**arguments)
    return process, run, policy, policy_path, launched, terminated


def _write_raw_policy(fixture):
    fixture[3].write_text(json.dumps(fixture[2]), encoding="utf-8")


def test_raw_mode_hashes_exact_bytes_separately_without_retaining_payload(raw_capture):
    process, run, policy, path, launched, terminated = raw_capture
    process.stdout, process.stderr = io.BytesIO(b"\xff\r\n"), io.BytesIO(b"\xfe\n")
    result = run()
    assert isinstance(result, subprocess_telemetry.RawSubprocessObservation)
    assert result.status == "CAPTURED" and result.capture_complete and result.returncode == 0
    assert result.stdout.sha256 == hashlib.sha256(b"\xff\r\n").hexdigest()
    assert result.stderr.sha256 == hashlib.sha256(b"\xfe\n").hexdigest()
    assert (result.stdout.bytes_seen, result.stderr.bytes_seen) == (3, 2)
    assert result.stdout.complete and result.stderr.complete and not terminated
    assert len(launched) == 1
    command, options = launched[0]
    assert options["env"] == {"KEEP": "value", "PYTHONUTF8": "0"}
    assert options["text"] is False and options["stdin"] == subprocess.DEVNULL
    assert "encoding" not in options and "errors" not in options
    assert result.policy_sha256 == subprocess_telemetry._raw_json_sha256(
        policy["subprocess_runtime_policy"]["raw_observation"])
    assert all(getattr(result, key) is False for key in
        ("reuse", "may_skip_tests", "fresh_native_baseline", "canonical_profile_reuse_enabled"))
    assert not hasattr(result.stdout, "data") and not hasattr(result, "passed")
    assert result.cleanup.descendant_quiescence == "not_verified"


@pytest.mark.parametrize("returncode", [1, 124, -9])
def test_raw_mode_observation_does_not_relabel_child_exit_as_timeout_or_test_PASS(raw_capture, returncode):
    raw_capture[0].returncode = returncode
    result = raw_capture[1]()
    assert result.status == "CAPTURED" and result.capture_complete
    assert result.returncode == returncode and result.timed_out is False
    assert result.termination_reason is None and not raw_capture[5]


@pytest.mark.parametrize("mode", ["stdout", "stderr", "total"])
def test_raw_mode_refuses_overflow_without_certifying_prefix_or_empty_output(raw_capture, mode):
    process, run, *_ = raw_capture
    if mode == "total":
        process.stdout, process.stderr = io.BytesIO(b"x" * 112), io.BytesIO(b"y" * 112)
    else:
        setattr(process, mode, io.BytesIO(b"x" * 144))
    result = run()
    assert result.status == "INCOMPLETE" and not result.capture_complete
    assert result.termination_reason == f"{mode}_limit" and result.timed_out is False
    assert result.stdout.sha256 is None and result.stderr.sha256 is None
    assert len(raw_capture[4]) == 1 and len(raw_capture[5]) == 1


def test_raw_mode_timeout_is_structured_and_never_injected_into_stderr(raw_capture):
    raw_capture[0].stdout, raw_capture[0].stderr = io.BytesIO(b"before-timeout"), io.BytesIO(b"child-stderr")
    raw_capture[0].returncode = None
    result = raw_capture[1](timeout=0.01)
    assert result.timed_out is True and result.termination_reason == "timeout"
    assert not result.capture_complete and result.returncode == -9
    assert result.stderr.sha256 in (None, hashlib.sha256(b"child-stderr").hexdigest())
    assert result.cleanup.attempted and result.cleanup.parent_exited
    assert not hasattr(result, "stderr_text")


def test_raw_mode_cleanup_failure_stays_incomplete_without_false_exit(raw_capture, monkeypatch):
    process, run, *_ = raw_capture
    process.returncode = None
    def fail(_):
        raise OSError("fixture cleanup failed")
    monkeypatch.setattr(subprocess_telemetry, "terminate_process_tree", fail)
    result = run(timeout=0.01)
    assert result.status == "INCOMPLETE" and result.returncode is None
    assert not result.cleanup.parent_exited
    assert "terminate:OSError" in result.cleanup.errors
    assert "wait:TimeoutExpired" in result.cleanup.errors


def test_raw_mode_read_error_is_not_empty_capture_success(raw_capture):
    class Broken(io.BytesIO):
        def read(self, count):
            raise OSError("fixture read failed")
    raw_capture[0].stdout = Broken()
    result = raw_capture[1]()
    assert not result.capture_complete and result.termination_reason == "capture_error"
    assert result.stdout.error == "read:OSError" and result.stdout.sha256 is None


def test_raw_mode_nonbinary_pipe_is_refused(raw_capture):
    class TextPipe(io.StringIO):
        pass
    raw_capture[0].stdout = TextPipe("text is not raw")
    result = raw_capture[1]()
    assert not result.capture_complete and result.stdout.error == "read:ValueError"


def test_raw_mode_close_failure_is_not_complete(raw_capture):
    class CloseFailure:
        def read(self, size):
            return b""

        def close(self):
            raise OSError("fixture close failed")
    raw_capture[0].stdout = CloseFailure()
    result = raw_capture[1]()
    assert not result.capture_complete and result.stdout.error == "close:OSError"


def test_raw_mode_reader_join_failure_is_explicit_without_unbounded_wait(raw_capture):
    import threading
    joins = []
    class StillAlive:
        def join(self, timeout):
            joins.append(timeout)

        def is_alive(self):
            return True
    outcome = subprocess_telemetry._raw_finish_process(raw_capture[0], [StillAlive()], threading.Event(),
        terminate=False, grace_seconds=0.01)
    assert not outcome.readers_joined and outcome.parent_exited
    assert len(joins) == 1 and 0 <= joins[0] <= 0.01


@pytest.mark.parametrize("exception", [RuntimeError, SystemExit])
@pytest.mark.parametrize("phase", ["read", "close"])
def test_raw_mode_unexpected_reader_exit_never_emits_false_EOF(exception, phase):
    import queue
    import threading
    class UnexpectedFailure:
        def read(self, size):
            if phase == "read":
                raise exception("fixture unexpected read failure")
            return b""

        def close(self):
            if phase == "close":
                raise exception("fixture unexpected close failure")
    events = queue.Queue(maxsize=1)
    with pytest.raises(exception):
        subprocess_telemetry._raw_stream_reader("stdout", UnexpectedFailure(), events,
            threading.Event(), 16, 0.001)
    name, data, error = events.get_nowait()
    assert name == "stdout" and data is None and error == f"{phase}:incomplete"


def test_raw_mode_callback_failure_cleans_owned_process_and_propagates(raw_capture):
    process = raw_capture[0]
    process.returncode = None
    def fail(_):
        raise RuntimeError("fixture callback failed")
    with pytest.raises(RuntimeError, match="callback failed"):
        raw_capture[1](heartbeat_seconds=0.0001, log=fail)
    assert len(raw_capture[4]) == 1 and len(raw_capture[5]) == 1


@pytest.mark.parametrize("key,value", [("protocol", "unknown"),
    ("authority", "canonical_reuse"), ("limits", {}), ("rationale", "")])
def test_raw_mode_invalid_policy_refuses_before_launch(raw_capture, key, value):
    raw_capture[2]["subprocess_runtime_policy"]["raw_observation"][key] = value
    _write_raw_policy(raw_capture)
    with pytest.raises(ValueError):
        raw_capture[1]()
    assert not raw_capture[4]


@pytest.mark.parametrize("key,value", [("stdout_bytes", 0), ("stderr_bytes", -1), ("total_bytes", 0),
    ("stdout_bytes", True), ("read_chunk_bytes", 1.5), ("queue_chunks", 0),
    ("poll_seconds", float("inf")), ("cleanup_grace_seconds", False),
    ("read_chunk_bytes", 1024), ("queue_chunks", 100), ("total_bytes", 300)])
def test_raw_mode_invalid_or_inconsistent_limits_refuse_before_launch(raw_capture, key, value):
    raw_capture[2]["subprocess_runtime_policy"]["raw_observation"]["limits"][key] = value
    _write_raw_policy(raw_capture)
    with pytest.raises(ValueError):
        raw_capture[1]()
    assert not raw_capture[4]


def test_raw_mode_missing_unknown_or_duplicate_policy_is_not_defaulted(raw_capture):
    raw = raw_capture[2]["subprocess_runtime_policy"]["raw_observation"]
    raw["unknown"] = True
    _write_raw_policy(raw_capture)
    with pytest.raises(ValueError):
        raw_capture[1]()
    del raw["unknown"]
    del raw_capture[2]["subprocess_runtime_policy"]["raw_observation"]
    _write_raw_policy(raw_capture)
    with pytest.raises(ValueError):
        raw_capture[1]()
    assert not raw_capture[4]
    raw_capture[3].write_text('{"subprocess_runtime_policy": {}, "subprocess_runtime_policy": {}}', encoding="utf-8")
    with pytest.raises(ValueError):
        raw_capture[1]()
    assert not raw_capture[4]


@pytest.mark.parametrize("changes", [{"env": None}, {"env": {"BAD": None}},
    {"env": {"BAD=KEY": "value"}}, {"env": {"KEY": "bad\0value"}},
    {"command": []}, {"command": ["python", None]}, {"timeout": 0}, {"timeout": True},
    {"timeout": float("nan")}, {"timeout": 10 ** 1000}, {"heartbeat_seconds": float("inf")},
    {"raw_observation": "raw"}])
def test_raw_mode_invalid_invocation_refuses_before_launch(raw_capture, changes):
    with pytest.raises(ValueError):
        raw_capture[1](**changes)
    assert not raw_capture[4]


def test_raw_mode_does_not_ignore_launch_error_or_retry(raw_capture, monkeypatch):
    attempts = []
    def fail(*args, **kwargs):
        attempts.append(1)
        raise OSError("fixture launch failed")
    monkeypatch.setattr(subprocess_telemetry.subprocess, "Popen", fail)
    with pytest.raises(OSError, match="launch failed"):
        raw_capture[1]()
    assert attempts == [1]


def test_raw_mode_capture_setup_failure_precedes_process_launch(raw_capture, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("fixture capture setup failed")
    monkeypatch.setattr(subprocess_telemetry.queue, "Queue", fail)
    with pytest.raises(RuntimeError, match="capture setup failed"):
        raw_capture[1]()
    assert not raw_capture[4]


def test_raw_mode_real_binary_pipes_and_closed_stdin(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess_telemetry, "record_execution_duration", lambda *_: None)
    env = {k: os.environ[k] for k in ("SYSTEMROOT", "WINDIR") if k in os.environ}
    env["RAW_OBSERVER_SENTINEL"] = "exact"
    script = ("import os,sys; assert sys.stdin.buffer.read()==b''; "
        "assert os.environ['RAW_OBSERVER_SENTINEL']=='exact'; "
        "sys.stdout.buffer.write(b'\\xff\\r\\n'); sys.stderr.buffer.write(b'\\xfe\\n')")
    result = run_observed_subprocess([sys.executable, "-I", "-S", "-c", script],
        cwd=tmp_path, label="raw-binary-fixture", timeout=5, env=env, raw_observation=True)
    assert result.status == "CAPTURED" and result.returncode == 0
    assert result.stdout.sha256 == hashlib.sha256(b"\xff\r\n").hexdigest()
    assert result.stderr.sha256 == hashlib.sha256(b"\xfe\n").hexdigest()
