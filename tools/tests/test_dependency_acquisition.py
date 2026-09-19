from __future__ import annotations

import io
import sys

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


def test_silent_dependency_action_is_timeout_not_invented_stall():
    with pytest.raises(DependencyAcquisitionError) as captured:
        execute_dependency_action(
            _action([sys.executable, "-c", "import time; time.sleep(1)"]),
            budget=_budget(hard=0.15, stall=0.05),
            stdout_sink=io.StringIO(),
            stderr_sink=io.StringIO(),
        )

    assert captured.value.result["status"] == "TIMED_OUT"
    assert captured.value.result["progress"]["event_count"] == 0


def test_output_only_progress_does_not_invent_a_stall():
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
    assert captured.value.result["progress"]["event_count"] >= 1
    assert captured.value.result["progress"]["stall_event_count"] == 0


def test_silent_dependency_action_observes_declared_runtime_path_progress(tmp_path):
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
