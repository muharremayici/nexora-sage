"""Constructed runtime probes; no installed npm, VM or installation certification."""
import copy
import ctypes
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from tools.core import installation_preflight as owner
from tools.core.distribution_test_selection import OWNED_PROBE_FAILURE_PROTOCOL
from tools.tests.test_installation_preflight import _machine, _target


@pytest.fixture
def policy():
    return copy.deepcopy(owner.command_observation_policy())


def _script(tmp_path, content):
    script = tmp_path / "authored_probe.py"
    script.write_text(content, encoding="utf-8")
    return script


def _diagnostic(observed, record_property):
    detail = {key: observed.get(key) for key in (
        "status", "observed_status", "returncode", "elapsed_seconds", "cleanup_confirmed", "cleanup_observation",
    )}
    record_property(OWNED_PROBE_FAILURE_PROTOCOL, detail)
    return str(detail)


def test_npm_observation_accepts_startup_longer_than_old_limit(tmp_path, monkeypatch, policy, record_property):
    script = _script(tmp_path, "import time\ntime.sleep(5.2)\nprint('11.16.0')\n")
    monkeypatch.setattr(owner.shutil, "which", lambda _command: sys.executable)
    observed = owner._command_observation("npm", [str(script)], policy=policy)
    assert policy["timeout_seconds"] == {"node": 30, "npm": 30, "git": 5}
    assert observed["status"] == "available", _diagnostic(observed, record_property)
    assert observed["budget_seconds"] == 30
    assert observed["elapsed_seconds"] > 5
    assert observed["version"] == "11.16.0"


def test_node_observation_accepts_startup_longer_than_old_limit(tmp_path, monkeypatch, policy, record_property):
    script = _script(tmp_path, "import time\ntime.sleep(5.2)\nprint('v24.18.0')\n")
    monkeypatch.setattr(owner.shutil, "which", lambda _command: sys.executable)
    observed = owner._command_observation("node", [str(script)], policy=policy)
    assert observed["status"] == "available", _diagnostic(observed, record_property)
    assert observed["budget_seconds"] == 30
    assert observed["elapsed_seconds"] > 5
    assert observed["version"] == "v24.18.0"


@pytest.mark.parametrize("command", ["node", "npm", "git"])
def test_unresolved_command_does_not_launch(command, monkeypatch, policy):
    monkeypatch.setattr(owner.shutil, "which", lambda _command: None)
    monkeypatch.setattr(owner.subprocess, "Popen", lambda *_a, **_kw: pytest.fail("must not launch"))
    observed = owner._command_observation(command, ["--version"], policy=policy)
    assert observed["status"] == "missing"
    assert observed["available"] is False and observed["path"] is None


@pytest.mark.parametrize("source,status", [
    ("import sys; print('failed'); sys.exit(3)", "nonzero_exit"),
    ("print('')", "invalid_output"),
    ("print('not a version')", "invalid_output"),
    ("import time; print('11.16.0' + 'x' * 50000, flush=True); time.sleep(30)", "output_limit"),
])
def test_nonzero_empty_invalid_and_oversized_output_fail_closed(tmp_path, monkeypatch, policy, source, status, record_property):
    script = _script(tmp_path, source)
    monkeypatch.setattr(owner.shutil, "which", lambda _command: sys.executable)
    observed = owner._command_observation("npm", [str(script)], policy=policy)
    assert observed["status"] == status, _diagnostic(observed, record_property)
    assert observed["observed_status"] == status
    assert observed["available"] is False and observed["version"] is None
    assert len(observed["stdout"]) <= policy["retained_characters"]


@pytest.mark.skipif(os.name != "nt", reason="Windows cleanup refusal protocol")
@pytest.mark.parametrize("failure", ["exited_before_taskkill", "terminate_error", "terminate_timeout", "wait_timeout"])
def test_cleanup_failure_retains_primary_refusal_and_bounded_diagnostics(tmp_path, monkeypatch, policy, failure):
    release = tmp_path / "release.signal"
    script = _script(tmp_path, "import sys,time\nfrom pathlib import Path\n"
                     "print('11.16.0' + 'x' * 50000, flush=True)\n"
                     "deadline=time.monotonic()+30\n"
                     "while not Path(sys.argv[1]).exists() and time.monotonic()<deadline:time.sleep(.005)\n")
    real_popen = subprocess.Popen
    children = []
    original_wait = []

    def tracked_popen(*args, **kwargs):
        child = real_popen(*args, **kwargs)
        children.append(child)
        original_wait.append(child.wait)
        return child

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("authored cleanup fixture", policy["cleanup_seconds"])

    def controlled_cleanup(args, **kwargs):
        assert Path(args[0]).name.lower() == "taskkill.exe"
        assert args[1:] == ["/PID", str(children[0].pid), "/T", "/F"]
        assert kwargs["timeout"] == policy["cleanup_seconds"]
        if failure == "terminate_error":
            raise PermissionError(5, "private fixture detail must not escape")
        if failure == "terminate_timeout":
            return timeout()
        release.touch()
        assert original_wait[0](timeout=3) == 0
        if failure == "wait_timeout":
            monkeypatch.setattr(children[0], "wait", timeout)
            return subprocess.CompletedProcess(args, 0, b"", b"")
        return subprocess.CompletedProcess(args, 128, b"", b"fixture process already exited")

    monkeypatch.setattr(owner.shutil, "which", lambda _command: sys.executable)
    monkeypatch.setattr(owner.subprocess, "Popen", tracked_popen)
    monkeypatch.setattr(owner.subprocess, "run", controlled_cleanup)
    try:
        observed = owner._command_observation("npm", [str(script), str(release)], policy=policy)
    finally:
        for child, wait in zip(children, original_wait):
            if child.poll() is None:
                child.kill()
            wait(timeout=3)
    assert observed["observed_status"] == "output_limit"
    assert observed["status"] == "cleanup_unconfirmed"
    assert observed["cleanup_confirmed"] is False
    assert observed["available"] is False and observed["version"] is None
    assert observed["output_truncated"] is True
    assert len(observed["stdout"]) <= policy["retained_characters"]
    cleanup = observed["cleanup_observation"]
    assert cleanup["attempted"] is True and cleanup["method"] == "taskkill"
    assert cleanup["child_returncode_before"] is None
    if failure == "exited_before_taskkill":
        assert cleanup["returncode"] == 128
        assert cleanup["child_returncode_after"] == 0 and observed["returncode"] == 0
        assert cleanup["error"] is None
    else:
        expected_type = "PermissionError" if failure == "terminate_error" else "TimeoutExpired"
        assert cleanup["error"] == {
            "operation": "wait" if failure == "wait_timeout" else "terminate",
            "type": expected_type, "code": 5 if failure == "terminate_error" else None,
        }
        assert cleanup["returncode"] == (0 if failure == "wait_timeout" else None)
    assert "private fixture detail" not in str(cleanup)


def test_timeout_retains_partial_output_and_cleans_owned_child(tmp_path, monkeypatch, policy, record_property):
    script = _script(tmp_path, "import time\nprint('pending', flush=True)\ntime.sleep(30)\n")
    monkeypatch.setattr(owner.shutil, "which", lambda _command: sys.executable)
    policy["timeout_seconds"]["npm"] = .5
    observed = owner._command_observation("npm", [str(script)], policy=policy)
    assert observed["status"] == "timed_out", _diagnostic(observed, record_property)
    assert observed["path"] == sys.executable
    assert observed["available"] is False and observed["version"] is None
    assert observed["stdout"].strip() == "pending"
    assert observed["cleanup_confirmed"] is True


@pytest.mark.skipif(os.name != "nt", reason="Actual Windows process-tree cleanup")
def test_timeout_cleans_actual_owned_windows_grandchild(tmp_path, monkeypatch, policy, record_property):
    grandchild = tmp_path / "grandchild.py"
    grandchild.write_text("import time\ntime.sleep(30)\n", encoding="ascii")
    pidfile = tmp_path / "child.pid"
    script = _script(tmp_path, "import subprocess,sys,time\nfrom pathlib import Path\n"
                     "child=subprocess.Popen([sys.executable,sys.argv[1]])\n"
                     "Path(sys.argv[2]).write_text(str(child.pid))\ntime.sleep(30)\n")
    monkeypatch.setattr(owner.shutil, "which", lambda _command: sys.executable)
    policy["timeout_seconds"]["npm"] = 2
    result = []
    thread = threading.Thread(target=lambda: result.append(owner._command_observation(
        "npm", [str(script), str(grandchild), str(pidfile)], policy=policy)))
    thread.start()
    deadline = time.monotonic() + 1.8
    while not pidfile.exists() and time.monotonic() < deadline:
        time.sleep(.02)
    assert pidfile.exists()
    kernel = ctypes.windll.kernel32
    kernel.OpenProcess.restype = ctypes.c_void_p
    handle = kernel.OpenProcess(0x100000, False, int(pidfile.read_text()))
    assert handle
    try:
        thread.join(15)
        assert not thread.is_alive()
        assert kernel.WaitForSingleObject(ctypes.c_void_p(handle), 0) == 0
    finally:
        kernel.CloseHandle(ctypes.c_void_p(handle))
    assert result[0]["status"] == "timed_out" and result[0]["cleanup_confirmed"] is True, _diagnostic(result[0], record_property)


@pytest.mark.parametrize("command", ["node", "npm"])
def test_plan_timeout_does_not_recommend_installing_present_runtime(command):
    machine = _machine()
    machine[command].update(available=False, path=f"C:/runtime/{command}", status="timed_out")
    plan = owner.build_installation_plan("C:/fixture", target_preflight=_target(languages={"typescript": 3}),
                                         machine=machine, bundled_typescript_available=False)
    assert plan["summary"]["status"] == "BLOCKED"
    blocker = plan["blockers"][0]
    assert blocker["id"] == command + "_version_query_not_verified"
    assert blocker["outcome_class"] == "TIMED_OUT"
    assert "install " not in blocker["required_action"]
    assert blocker["automatic_install"] is False
    assert "query_status=timed_out" in "\n".join(owner.render_console_lines(plan))


def test_machine_observes_each_command_once_with_one_policy(monkeypatch):
    calls = []
    def record(command, args, *, policy):
        calls.append((command, args, id(policy)))
        return {"available": True, "version": "fixture"}
    monkeypatch.setattr(owner, "_command_observation", record)
    features = {key: True for key in owner.installation_feature_dependencies()}
    owner.observe_machine(feature_availability=features)
    assert [row[0] for row in calls] == ["node", "npm", "git"]
    assert len({row[2] for row in calls}) == 1


@pytest.mark.parametrize("value", [None, False, 0, -1, 1000, "30"])
def test_policy_refuses_missing_or_unbounded_limit(policy, value):
    policy["timeout_seconds"]["npm"] = value
    with pytest.raises(ValueError):
        owner.command_observation_policy({"command_observation": policy})
