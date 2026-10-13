from __future__ import annotations

import io
import sys
from types import SimpleNamespace

import pytest

from tools.core import dependency_acquisition
from tools.core import bootstrap_env
from tools.core.dependency_acquisition import (
    DependencyAcquisitionError,
    execute_dependency_action,
    select_dependency_budget,
)


def _action(command: list[str]) -> dict:
    return {
        "id": "install_sage_node_ast_dependencies",
        "command": command,
        "cwd": ".",
        "dependency_authority": "sage_runtime",
        "operation_class": "network_package_acquisition",
        "telemetry_identifier": "dependency fixture completed",
        "progress_relative_paths": [],
        "stall_authority_sources": ["filesystem"],
        "target_repository_mutation": False,
    }


def _budget(*, hard: float = 1.0, stall: float = 0.2) -> dict:
    return {
        "hard_timeout_seconds": hard,
        "stall_timeout_seconds": stall,
        "poll_interval_seconds": 0.01,
        "heartbeat_seconds": 0.05,
        "telemetry_identifier": "dependency fixture completed",
        "basis": "fixture",
    }


@pytest.fixture
def acquisition_boundary(monkeypatch):
    """Control scheduling, not progress detection or outcome classification."""
    def install(*, marker=None, stdout="", startup_seconds=0.0):
        state = SimpleNamespace(now=0.0, terminated=[], waited=[], joined=[])

        def sleep(seconds):
            state.now = round(state.now + seconds, 6)

        class Process:
            returncode = None

            def __init__(self, command, **kwargs):
                state.command, state.kwargs = command, kwargs
                # Popen follows the real filesystem-signature baseline.
                state.now += startup_seconds
                if marker is not None:
                    marker.parent.mkdir(parents=True)
                    marker.write_text("{}", encoding="utf-8")
                self.stdout, self.stderr = io.StringIO(stdout), io.StringIO()

            def poll(self):
                return self.returncode

            def wait(self, timeout):
                state.waited.append(timeout)
                return self.returncode

        class StreamThread:
            def __init__(self, *, target, args, daemon):
                assert daemon is True
                self.target, self.args = target, args

            def start(self):
                # Run the real stream pump before polling; no scheduler race.
                self.target(*self.args)

            def join(self, timeout):
                state.joined.append(timeout)

        def terminate(proc):
            state.terminated.append(proc)
            proc.returncode = -1

        original = dependency_acquisition.subprocess
        monkeypatch.setattr(dependency_acquisition, "subprocess", SimpleNamespace(
            Popen=Process, PIPE=original.PIPE, TimeoutExpired=original.TimeoutExpired))
        monkeypatch.setattr(dependency_acquisition, "time", SimpleNamespace(
            perf_counter=lambda: state.now, sleep=sleep))
        monkeypatch.setattr(dependency_acquisition, "threading", SimpleNamespace(Thread=StreamThread))
        monkeypatch.setattr(dependency_acquisition, "terminate_process_tree", terminate)
        return state

    return install


def _assert_forced_cleanup(result, state):
    assert result["returncode"] == 124
    assert result["cleanup_status"] == "process_tree_terminated"
    assert len(state.terminated) == 1
    assert state.waited == [5]
    assert state.joined == [1, 1]


def test_dependency_budget_bootstraps_then_adapts_from_success_only():
    policy = {
        "dependency_acquisition_budgets": {
            "network_package_acquisition": {
                "bootstrap_hard_timeout_seconds": 600,
                "maximum_hard_timeout_seconds": 900,
                "stall_timeout_seconds": 180,
                "minimum_success_samples": 3,
                "sample_limit": 20,
                "observed_p95_multiplier": 2.0,
            }
        }
    }
    action = _action([sys.executable, "-c", "pass"])
    bootstrap = select_dependency_budget(
        action, policy=policy, successful_durations_seconds=[200, 220]
    )
    adaptive = select_dependency_budget(
        action, policy=policy, successful_durations_seconds=[310, 320, 330]
    )
    clamped = select_dependency_budget(
        action, policy=policy, successful_durations_seconds=[800, 820, 840]
    )

    assert bootstrap["hard_timeout_seconds"] == 600
    assert bootstrap["basis"] == "bootstrap_insufficient_success_samples"
    assert adaptive["hard_timeout_seconds"] == 658
    assert adaptive["basis"] == "successful_local_p95_clamped"
    assert clamped["hard_timeout_seconds"] == 900


def test_dependency_action_streams_success_and_records_only_completed_duration(monkeypatch):
    recorded = []
    monkeypatch.setattr(
        dependency_acquisition,
        "record_execution_duration",
        lambda *args: recorded.append(args),
    )
    stdout = io.StringIO()
    result = execute_dependency_action(
        _action([sys.executable, "-c", "print('progress', flush=True)"]),
        budget=_budget(),
        stdout_sink=stdout,
        stderr_sink=io.StringIO(),
    )

    assert result["status"] == "SUCCESS"
    assert "progress" in stdout.getvalue()
    assert result["progress"]["event_count"] >= 1
    assert recorded[0][1] == "dependency fixture completed"


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("getaddrinfo failed", "OFFLINE"),
        ("package integrity mismatch", "INSTALL_FAILED"),
    ],
)
def test_dependency_action_preserves_failure_class(message, expected):
    command = [
        sys.executable,
        "-c",
        f"import sys; sys.stderr.write({message!r} + '\\n'); sys.exit(1)",
    ]
    with pytest.raises(DependencyAcquisitionError) as captured:
        execute_dependency_action(
            _action(command),
            budget=_budget(),
            stdout_sink=io.StringIO(),
            stderr_sink=io.StringIO(),
        )

    assert captured.value.result["status"] == expected
    assert captured.value.result["returncode"] == 1
    assert "output_excerpt" not in captured.value.result


def test_silent_dependency_action_is_timeout_not_invented_stall(acquisition_boundary):
    state = acquisition_boundary()
    with pytest.raises(DependencyAcquisitionError) as captured:
        execute_dependency_action(
            _action([sys.executable, "-c", "import time; time.sleep(1)"]),
            budget=_budget(hard=0.15, stall=0.05),
            stdout_sink=io.StringIO(),
            stderr_sink=io.StringIO(),
        )

    assert captured.value.result["status"] == "TIMED_OUT"
    assert captured.value.result["progress"]["event_count"] == 0
    assert captured.value.result["progress"]["stall_event_count"] == 0
    assert captured.value.result["duration_seconds"] == 0.15
    _assert_forced_cleanup(captured.value.result, state)


def test_output_only_progress_does_not_invent_a_stall(acquisition_boundary):
    state = acquisition_boundary(stdout="started\n")
    command = [
        sys.executable,
        "-c",
        "import time; print('started', flush=True); time.sleep(1)",
    ]
    with pytest.raises(DependencyAcquisitionError) as captured:
        execute_dependency_action(
            _action(command),
            budget=_budget(hard=0.6, stall=0.12),
            stdout_sink=io.StringIO(),
            stderr_sink=io.StringIO(),
        )

    assert captured.value.result["status"] == "TIMED_OUT"
    assert captured.value.result["progress"]["event_count"] == 1
    assert captured.value.result["progress"]["stall_event_count"] == 0
    assert captured.value.result["duration_seconds"] == 0.6
    _assert_forced_cleanup(captured.value.result, state)


def test_silent_dependency_action_observes_declared_runtime_path_progress(tmp_path, acquisition_boundary):
    marker = tmp_path / "node_modules/typescript/package.json"
    state = acquisition_boundary(marker=marker)
    action = _action(
        [
            sys.executable,
            "-c",
            (
                "import pathlib,time; "
                "p=pathlib.Path('node_modules/typescript'); "
                "p.mkdir(parents=True); "
                "(p/'package.json').write_text('{}'); "
                "time.sleep(1)"
            ),
        ]
    )
    action["cwd"] = str(tmp_path)
    action["progress_relative_paths"] = ["node_modules"]
    action["stall_authority_sources"] = ["filesystem"]
    with pytest.raises(DependencyAcquisitionError) as captured:
        execute_dependency_action(
            action,
            budget=_budget(hard=0.6, stall=0.12),
            stdout_sink=io.StringIO(),
            stderr_sink=io.StringIO(),
        )

    assert captured.value.result["status"] == "STALLED"
    assert "filesystem" in captured.value.result["progress"]["sources"]
    assert captured.value.result["progress"]["event_count"] == 1
    assert captured.value.result["progress"]["stall_event_count"] == 1
    assert captured.value.result["duration_seconds"] == 0.12
    assert marker.read_text(encoding="utf-8") == "{}"
    _assert_forced_cleanup(captured.value.result, state)


@pytest.mark.parametrize(("authority", "startup", "expected_stall_events"), [
    ([], 0.0, 0),
    (["filesystem"], 0.52, 1),
])
def test_filesystem_progress_without_an_expired_authoritative_window_is_timeout(
    tmp_path, acquisition_boundary, authority, startup, expected_stall_events,
):
    state = acquisition_boundary(marker=tmp_path / "node_modules/typescript/package.json",
                                 startup_seconds=startup)
    action = _action([sys.executable, "-c", "pass"])
    action.update(cwd=str(tmp_path), progress_relative_paths=["node_modules"],
                  stall_authority_sources=authority)
    with pytest.raises(DependencyAcquisitionError) as captured:
        execute_dependency_action(action, budget=_budget(hard=0.6, stall=0.12),
                                  stdout_sink=io.StringIO(), stderr_sink=io.StringIO())
    result = captured.value.result
    assert result["status"] == "TIMED_OUT"
    assert result["duration_seconds"] == 0.6
    assert result["progress"]["event_count"] == 1
    assert result["progress"]["sources"] == ["filesystem"]
    assert result["progress"]["stall_event_count"] == expected_stall_events
    _assert_forced_cleanup(result, state)


def test_missing_package_manager_is_structured_even_after_preflight():
    with pytest.raises(DependencyAcquisitionError) as captured:
        execute_dependency_action(
            _action(["sage-package-manager-does-not-exist", "--version"]),
            budget=_budget(),
            stdout_sink=io.StringIO(),
            stderr_sink=io.StringIO(),
        )

    assert captured.value.result["status"] == "MISSING_MANAGER"
    assert captured.value.result["cleanup_status"] == "not_started"


def test_target_native_dependency_action_cannot_execute_automatically():
    action = _action([sys.executable, "-c", "pass"])
    action["dependency_authority"] = "target_native_validation"

    with pytest.raises(ValueError, match="Only sage_runtime"):
        execute_dependency_action(action, budget=_budget())


def test_bootstrap_persists_dependency_outcome_without_using_global_timeout(monkeypatch):
    receipts = []
    action = _action([sys.executable, "-c", "pass"])
    monkeypatch.setattr(bootstrap_env, "select_dependency_budget", lambda _action: _budget())
    monkeypatch.setattr(
        bootstrap_env,
        "execute_dependency_action",
        lambda *_args, **_kwargs: {
            "action_id": action["id"],
            "status": "SUCCESS",
            "duration_seconds": 2.5,
        },
    )
    monkeypatch.setattr(
        "tools.core.config.save_json_atomic",
        lambda path, payload: receipts.append((path, payload)),
    )

    assert bootstrap_env.install_deps({"actions": [action]}) is True
    assert receipts[-1][1]["status"] == "PASS"
    assert receipts[-1][1]["outcomes"][0]["action_id"] == action["id"]


def test_bootstrap_projects_structured_dependency_failure_without_traceback(monkeypatch):
    receipts = []
    action = _action([sys.executable, "-c", "pass"])
    failure = {
        "action_id": action["id"],
        "status": "OFFLINE",
        "duration_seconds": 1.0,
    }
    monkeypatch.setattr(bootstrap_env, "select_dependency_budget", lambda _action: _budget())
    monkeypatch.setattr(
        bootstrap_env,
        "execute_dependency_action",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            DependencyAcquisitionError(failure)
        ),
    )
    monkeypatch.setattr(
        "tools.core.config.save_json_atomic",
        lambda path, payload: receipts.append((path, payload)),
    )

    assert bootstrap_env.install_deps({"actions": [action]}) is False
    assert receipts[-1][1]["status"] == "FAIL"
    assert receipts[-1][1]["outcomes"][0]["status"] == "OFFLINE"
