import argparse
import subprocess
from unittest.mock import Mock

import codemaps
from tools import generate_installation_proof
from tools.core import bootstrap_env, init_execution_contract
from tools.governance_sync import _workspace_scoped_override_seed


def _contract():
    return {
        "default_mode": "full",
        "duration_sample_limit": 3,
        "installation_proof_init_modes": {"daily": "setup_only", "release": "setup_only"},
        "modes": {
            "full": {
                "public_flag": "--full",
                "bootstrap_mode": "full",
                "analysis_profile": "release-deep",
                "force": True,
                "cost_tier": "high",
                "telemetry_identifier": "bootstrap_full_pipeline",
                "generated_scope": "full evidence",
                "operator_guidance": "heavy",
            },
            "setup_only": {
                "public_flag": "",
                "bootstrap_mode": "setup",
                "analysis_profile": "none",
                "force": False,
                "cost_tier": "low",
                "telemetry_identifier": "bootstrap_setup_only",
                "generated_scope": "configuration only",
                "operator_guidance": "run analysis later",
            },
        },
    }


def test_fresh_workspace_governance_enables_sqlite_authority_by_default():
    seeded = _workspace_scoped_override_seed(
        {
            "workspace_root": "../repo",
            "variations": {"MAIN": "../repo"},
            "project_roles": {"MAIN": "host"},
        },
        {},
    )

    assert seeded["use_sqlite"] is True


def test_explicit_workspace_sqlite_choice_is_preserved():
    seeded = _workspace_scoped_override_seed(
        {
            "workspace_root": "../repo",
            "use_sqlite": True,
            "variations": {"MAIN": "../repo"},
            "project_roles": {"MAIN": "host"},
        },
        {"use_sqlite": False},
    )

    assert seeded["use_sqlite"] is False


def test_init_preflight_is_honest_without_local_duration_samples(monkeypatch):
    monkeypatch.setattr(init_execution_contract, "init_execution_contract", _contract)
    monkeypatch.setattr(init_execution_contract, "load_json_file", lambda *_args: {"traces": []})
    monkeypatch.setattr(init_execution_contract, "resolve_runtime_projects", lambda _root: {"MAIN": _root})

    preflight = init_execution_contract.build_init_preflight("full")

    assert preflight["analysis_profile"] == "release-deep"
    assert preflight["project_count"] == 1
    assert preflight["duration"]["status"] == "unavailable"
    assert preflight["duration"]["median_seconds"] is None


def test_init_preflight_uses_only_exact_mode_local_samples(monkeypatch):
    monkeypatch.setattr(init_execution_contract, "init_execution_contract", _contract)
    monkeypatch.setattr(
        init_execution_contract,
        "load_json_file",
        lambda *_args: {
            "traces": [
                {"type": "subprocess_execution", "identifier": "bootstrap_full_pipeline", "execution_ms": 10_000},
                {"type": "subprocess_execution", "identifier": "bootstrap_full_pipeline", "execution_ms": 20_000},
                {"type": "subprocess_execution", "identifier": "other", "execution_ms": 999_000},
            ]
        },
    )
    monkeypatch.setattr(init_execution_contract, "resolve_runtime_projects", lambda _root: {})

    preflight = init_execution_contract.build_init_preflight("full")

    assert preflight["duration"] == {
        "status": "available",
        "basis": "local_exact_mode_median",
        "sample_count": 2,
        "median_seconds": 15.0,
    }


def test_installation_proof_initializes_without_duplicate_heavy_analysis():
    steps = generate_installation_proof._commands_for_level(
        "release",
        skip_deps=True,
        max_doctor_seconds=30,
    )

    init_step = next(row for row in steps if row["id"] == "init")
    assert "--setup-only" not in init_step["command"]
    assert "--full" not in init_step["command"]
    assert "--skip-deps" in init_step["command"]
    assert any(row["id"] == "daily_run" for row in steps)
    install_surface = next(row for row in steps if row["id"] == "installed_distribution_surface")
    assert install_surface["command"][-2:] == ["--scope", "installed-package"]
    assert install_surface["timeout"] == 180
    assert not any(row["id"] == "release_check_v1" for row in steps)


def test_public_installation_proof_omits_only_private_final_consistency():
    steps = generate_installation_proof._commands_for_level(
        "release",
        skip_deps=True,
        max_doctor_seconds=30,
        public_distribution=True,
    )

    step_ids = [row["id"] for row in steps]
    assert "final_consistency" not in step_ids
    assert step_ids == [
        "init",
        "installation_contract",
        "doctor",
        "mcp_config",
        "release_language",
        "daily_run",
        "installed_distribution_surface",
    ]


def test_private_installation_proof_retains_final_consistency():
    steps = generate_installation_proof._commands_for_level(
        "release",
        skip_deps=True,
        max_doctor_seconds=30,
        public_distribution=False,
    )

    assert "final_consistency" in [row["id"] for row in steps]


def test_installation_proof_unknown_profile_omission_fails_closed(monkeypatch):
    monkeypatch.setattr(
        generate_installation_proof,
        "_installation_proof_authority",
        lambda **_kwargs: ("public_target_repository", frozenset({"unknown_step"})),
    )

    try:
        generate_installation_proof._commands_for_level(
            "release",
            skip_deps=True,
            max_doctor_seconds=30,
            public_distribution=True,
        )
    except ValueError as exc:
        assert "unknown_step" in str(exc)
    else:
        raise AssertionError("Unknown installation-proof omission must fail closed")


def test_installation_proof_forwards_explicit_target_and_project_scope():
    steps = generate_installation_proof._commands_for_level(
        "release",
        skip_deps=True,
        max_doctor_seconds=30,
        target_root=r"C:\target",
        projects="MAIN",
    )

    init_step = next(row for row in steps if row["id"] == "init")
    daily_step = next(row for row in steps if row["id"] == "daily_run")
    assert init_step["command"][-2:] == ["--target-root", r"C:\target"]
    assert daily_step["command"][-4:] == ["--target-root", r"C:\target", "--projects", "MAIN"]


def test_installation_proof_excerpt_preserves_failure_head_and_tail(monkeypatch):
    output = "HEAD:" + ("x" * 5000) + ":TAIL"
    completed = subprocess.CompletedProcess(
        args=["python"],
        returncode=1,
        stdout=output,
        stderr="",
    )
    monkeypatch.setattr(
        generate_installation_proof,
        "run_observed_subprocess",
        lambda *_args, **_kwargs: (completed, 0.1),
    )

    row = generate_installation_proof._step(
        "daily_run", "Daily profile run", ["python"], timeout=1
    )

    assert len(row["output_excerpt"]) == 4000
    assert row["output_excerpt"].startswith("HEAD:")
    assert row["output_excerpt"].endswith(":TAIL")
    assert "preserving head and tail" in row["output_excerpt"]
    assert generate_installation_proof._bounded_output_excerpt("abcdef", limit=3) == "def"
    assert generate_installation_proof._bounded_output_excerpt("abcdef", limit=0) == ""


def test_installation_proof_separates_installation_from_target_governance(monkeypatch):
    monkeypatch.setattr(
        generate_installation_proof,
        "_step",
        lambda step_id, label, command, timeout: {
            "id": step_id,
            "label": label,
            "command": command,
            "passed": True,
            "returncode": 0,
            "duration_seconds": 0.01,
            "output_excerpt": "",
        },
    )

    payload = generate_installation_proof.build_installation_proof(
        "smoke",
        skip_deps=True,
        max_doctor_seconds=30,
    )

    assert payload["summary"]["status"] == "PASS"
    assert payload["summary"]["target_governance"] == "NOT_EVALUATED"
    assert payload["claim_boundary"] == {
        "proves": "machine_local_installation_and_bounded_public_surface_execution",
        "does_not_prove": "target_repository_governance_pass_or_public_release_authority",
        "target_governance_is_separate": True,
    }


def test_public_install_proof_cli_forwards_target_and_project_scope(monkeypatch):
    commands = []
    monkeypatch.setattr(codemaps, "run_command", lambda command, **_kwargs: commands.append(command) or 0)

    result = codemaps.cmd_install_proof(
        argparse.Namespace(
            level="release",
            skip_deps=True,
            max_doctor_seconds=180,
            target_root=r"C:\target",
            projects="MAIN",
        )
    )

    assert result == 0
    assert commands[0][-4:] == ["--target-root", r"C:\target", "--projects", "MAIN"]


def test_public_setup_only_init_selects_setup_bootstrap(monkeypatch):
    commands = []
    monkeypatch.setattr(codemaps, "cmd_doctor", lambda _args: 0)
    monkeypatch.setattr(codemaps, "render_init_preflight", lambda _mode: ["preflight"])
    monkeypatch.setattr(
        codemaps,
        "init_mode_contract",
        lambda mode: {"bootstrap_mode": "setup" if mode == "setup_only" else "full"},
    )
    monkeypatch.setattr(codemaps, "setup_wizard_step_timeout_seconds", lambda: 1200)
    monkeypatch.setattr(codemaps, "run_command", lambda command, **kwargs: commands.append((command, kwargs)) or 0)

    result = codemaps.cmd_init(
        argparse.Namespace(
            setup_only=True,
            full=False,
            skip_deps=True,
            target_root=r"C:\target",
        )
    )

    assert result == 0
    assert commands and commands[0][0][2:4] == ["--mode", "setup"]
    assert "--skip-deps" in commands[0][0]
    assert commands[0][1]["timeout"] == 1200


def test_public_init_defaults_to_setup_only(monkeypatch):
    commands = []
    monkeypatch.setattr(codemaps, "cmd_doctor", lambda _args: 0)
    monkeypatch.setattr(codemaps, "render_init_preflight", lambda _mode: ["preflight"])
    monkeypatch.setattr(
        codemaps,
        "init_mode_contract",
        lambda mode: {"bootstrap_mode": "setup" if mode == "setup_only" else "full"},
    )
    monkeypatch.setattr(codemaps, "setup_wizard_step_timeout_seconds", lambda: 1200)
    monkeypatch.setattr(codemaps, "run_command", lambda command, **kwargs: commands.append((command, kwargs)) or 0)

    result = codemaps.cmd_init(
        argparse.Namespace(
            setup_only=False,
            full=False,
            skip_deps=True,
            target_root=r"C:\target",
        )
    )

    assert result == 0
    assert commands and commands[0][0][2:4] == ["--mode", "setup"]
    assert commands[0][1]["timeout"] == 1200


def test_public_init_requires_explicit_full_flag_for_release_deep(monkeypatch):
    commands = []
    monkeypatch.setattr(codemaps, "cmd_doctor", lambda _args: 0)
    monkeypatch.setattr(codemaps, "render_init_preflight", lambda _mode: ["preflight"])
    monkeypatch.setattr(
        codemaps,
        "init_mode_contract",
        lambda mode: {"bootstrap_mode": "setup" if mode == "setup_only" else "full"},
    )
    monkeypatch.setattr(codemaps, "setup_wizard_step_timeout_seconds", lambda: 1200)
    monkeypatch.setattr(codemaps, "run_command", lambda command, **kwargs: commands.append((command, kwargs)) or 0)

    result = codemaps.cmd_init(
        argparse.Namespace(
            setup_only=False,
            full=True,
            skip_deps=True,
            target_root=r"C:\target",
        )
    )

    assert result == 0
    assert commands and commands[0][0][2:4] == ["--mode", "full"]
    assert commands[0][1]["timeout"] == 1200


def test_public_watch_launches_module_to_avoid_package_shadowing(monkeypatch):
    commands = []
    monkeypatch.setattr(codemaps, "_target_root_env", lambda _args: None)
    monkeypatch.setattr(codemaps, "ensure_runtime_truth", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(codemaps, "import_target_available", lambda *_args: True)
    monkeypatch.setattr(codemaps, "python_subprocess_env", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        codemaps,
        "run_command",
        lambda command, **_kwargs: commands.append(command) or 0,
    )

    result = codemaps.cmd_watch(
        argparse.Namespace(path="../src", debounce=1.5, once=True, target_root=None)
    )

    assert result == 0
    assert commands == [[
        "python",
        "-m",
        "tools.orchestrators.watchdog",
        "--path",
        "../src",
        "--debounce",
        "1.5",
        "--once",
    ]]


def test_bootstrap_timeout_terminates_owned_process_tree_and_records_receipt(monkeypatch):
    proc = Mock()
    proc.pid = 4242
    proc.wait.side_effect = [
        subprocess.TimeoutExpired(cmd=["python", "child.py"], timeout=1),
        0,
    ]
    receipts = []
    monkeypatch.setattr(bootstrap_env.subprocess, "Popen", lambda *_args, **_kwargs: proc)
    monkeypatch.setattr(bootstrap_env, "terminate_process_tree", Mock())
    monkeypatch.setattr(
        "tools.core.config.save_json_atomic",
        lambda path, payload: receipts.append((path, payload)),
    )

    try:
        bootstrap_env.run_command(["python", "child.py"], timeout_seconds=1)
    except subprocess.TimeoutExpired:
        pass
    else:
        raise AssertionError("timeout must remain visible to the bootstrap caller")

    bootstrap_env.terminate_process_tree.assert_called_once_with(proc)
    assert receipts
    assert receipts[0][1]["status"] == "TIMED_OUT"
    assert receipts[0][1]["child_pid"] == 4242
    assert receipts[0][1]["cleanup_status"] == "process_tree_terminated"


def test_bootstrap_children_use_the_shared_utf8_runtime_environment(monkeypatch):
    proc = Mock()
    proc.wait.return_value = 0
    observed = {}

    def popen(command, **kwargs):
        observed["command"] = command
        observed["env"] = kwargs["env"]
        return proc

    monkeypatch.setattr(bootstrap_env.subprocess, "Popen", popen)
    monkeypatch.setattr(bootstrap_env, "VENDOR_PATHS", [])

    assert bootstrap_env.run_command(["python", "child.py"]) is True
    assert observed["env"]["PYTHONIOENCODING"] == "utf-8"
    assert observed["env"]["PYTHONUTF8"] == "1"


def test_external_target_preflight_receives_runtime_project_filter(monkeypatch, tmp_path):
    commands = []
    monkeypatch.setattr(codemaps, "run_command", lambda command: commands.append(command) or 0)
    args = argparse.Namespace(
        target_root=str(tmp_path),
        skip_target_preflight=False,
        projects="MAIN,WEB",
    )

    result = codemaps._run_external_target_preflight_if_needed(args)

    assert result == 0
    assert commands == [[
        "python",
        str(codemaps.EXTERNAL_TARGET_PREFLIGHT),
        str(tmp_path),
        "--projects",
        "MAIN,WEB",
    ]]
