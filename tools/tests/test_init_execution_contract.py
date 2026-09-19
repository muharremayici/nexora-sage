import argparse
import json
import subprocess
from pathlib import Path
from unittest.mock import Mock

import codemaps
from tools import external_target_preflight, generate_installation_proof
from tools.core import bootstrap_env, config as core_config, init_execution_contract
from tools.core.unmanaged_atomic_io import native_filesystem_path
from tools.governance_sync import _workspace_scoped_override_seed


def _contract():
    return {
        "default_mode": "full",
        "duration_sample_limit": 3,
        "installation_proof_init_modes": {"daily": "setup_only", "release": "setup_only"},
        "target_state_modes": {
            "embedded_default": {
                "id": "configured_default_maintenance",
                "writes_shared_workspace_truth": True,
            },
            "explicit_target": {
                "id": "configured_default_adoption",
                "writes_shared_workspace_truth": True,
            },
            "isolated_external_analysis": {
                "id": "isolated_target_acquisition",
                "writes_shared_workspace_truth": False,
            },
        },
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


def test_explicit_init_preflight_names_configured_default_adoption(monkeypatch):
    monkeypatch.setattr(init_execution_contract, "init_execution_contract", _contract)
    monkeypatch.setattr(init_execution_contract, "load_json_file", lambda *_args: {"traces": []})
    monkeypatch.setattr(init_execution_contract, "resolve_runtime_projects", lambda _root: {})

    preflight = init_execution_contract.build_init_preflight(
        "setup_only",
        target_root=r"C:\target",
    )

    assert preflight["target_state"] == {
        "selector": "explicit_target",
        "id": "configured_default_adoption",
        "writes_shared_workspace_truth": True,
    }


def test_central_cli_contract_distinguishes_adoption_from_isolated_acquisition():
    contract = init_execution_contract.init_execution_contract()

    assert contract["target_state_modes"]["explicit_target"]["id"] == (
        "configured_default_adoption"
    )
    assert contract["target_state_modes"]["explicit_target"][
        "writes_shared_workspace_truth"
    ] is True
    assert contract["target_state_modes"]["isolated_external_analysis"]["id"] == (
        "isolated_target_acquisition"
    )
    assert contract["target_state_modes"]["isolated_external_analysis"][
        "writes_shared_workspace_truth"
    ] is False


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
    assert init_step["command"][-4:] == ["--target-root", r"C:\target", "--projects", "MAIN"]
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


def test_installation_proof_separates_installation_from_target_governance(monkeypatch, tmp_path):
    runtime_config_dir = tmp_path / "runtime-config"
    runtime_config_dir.mkdir()
    for attribute, filename in (
        ("DISCOVERY_FILE", "codemaps.discovery.json"),
        ("OVERRIDES_FILE", "codemaps.overrides.json"),
        ("CONFIG_FILE", "codemaps.config.json"),
    ):
        path = runtime_config_dir / filename
        path.write_text("{}\n", encoding="utf-8")
        monkeypatch.setattr(generate_installation_proof, attribute, path)
    monkeypatch.setattr(
        generate_installation_proof,
        "_step",
        lambda step_id, label, command, timeout, env: {
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


def test_fresh_installation_smoke_fails_before_steps_with_one_actionable_prerequisite(
    monkeypatch,
    tmp_path,
):
    missing_config_dir = tmp_path / "missing-runtime-config"
    monkeypatch.setattr(
        generate_installation_proof,
        "DISCOVERY_FILE",
        missing_config_dir / "codemaps.discovery.json",
    )
    monkeypatch.setattr(
        generate_installation_proof,
        "OVERRIDES_FILE",
        missing_config_dir / "codemaps.overrides.json",
    )
    monkeypatch.setattr(
        generate_installation_proof,
        "CONFIG_FILE",
        missing_config_dir / "codemaps.config.json",
    )
    observed = []
    monkeypatch.setattr(
        generate_installation_proof,
        "_step",
        lambda *args, **kwargs: observed.append((args, kwargs)),
    )

    payload = generate_installation_proof.build_installation_proof(
        "smoke",
        skip_deps=True,
        max_doctor_seconds=30,
        public_distribution=True,
    )

    prerequisite = payload["prerequisite"]
    assert observed == []
    assert payload["summary"]["status"] == "FAIL"
    assert payload["summary"]["steps"] == 0
    assert payload["summary"]["prerequisite_cause_count"] == 1
    assert prerequisite["status"] == "PREREQUISITE_REQUIRED"
    assert len(prerequisite["missing_files"]) == 3
    assert prerequisite["canonical_command"] == (
        "python sage.py install-proof --level release --skip-deps "
        "--target-root <repository> --projects MAIN"
    )
    assert prerequisite["canonical_command"] in prerequisite["action_message"]
    assert "## Prerequisite" in generate_installation_proof.render_report(payload)


def test_installation_proof_transports_init_preflight_to_daily_run(monkeypatch):
    observed = []
    monkeypatch.setattr(
        generate_installation_proof,
        "_installation_preflight_reuse_transport",
        lambda _target: {
            "path": "C:/receipts/runs/preflight-init.json",
            "sha256": "a" * 64,
            "run_id": "preflight-init",
        },
    )

    def passing_step(step_id, label, command, timeout, env):
        observed.append((step_id, dict(env or {})))
        return {
            "id": step_id,
            "label": label,
            "command": command,
            "passed": True,
            "returncode": 0,
            "duration_seconds": 0.01,
            "output_excerpt": "",
        }

    monkeypatch.setattr(generate_installation_proof, "_step", passing_step)

    payload = generate_installation_proof.build_installation_proof(
        "daily",
        skip_deps=True,
        max_doctor_seconds=30,
        target_root=r"C:\target",
        projects="MAIN",
    )

    daily_env = next(env for step_id, env in observed if step_id == "daily_run")
    assert payload["summary"]["status"] == "PASS"
    assert payload["preflight_reuse"]["status"] == "VERIFIED_REUSE"
    assert daily_env["CODEMAPS_TARGET_PREFLIGHT_REUSE_MODE"] == "installation_proof"
    assert daily_env["CODEMAPS_TARGET_PROJECTS"] == "MAIN"
    assert daily_env["CODEMAPS_TARGET_PREFLIGHT_RECEIPT_SHA256"] == "a" * 64


def test_installation_proof_fails_closed_when_init_receipt_cannot_be_bound(monkeypatch):
    observed = []
    monkeypatch.setattr(
        generate_installation_proof,
        "_installation_preflight_reuse_transport",
        lambda _target: (_ for _ in ()).throw(RuntimeError("missing receipt")),
    )

    def passing_step(step_id, label, command, timeout, env):
        observed.append(step_id)
        return {
            "id": step_id,
            "label": label,
            "command": command,
            "passed": True,
            "returncode": 0,
            "duration_seconds": 0.01,
            "output_excerpt": "",
        }

    monkeypatch.setattr(generate_installation_proof, "_step", passing_step)

    payload = generate_installation_proof.build_installation_proof(
        "daily",
        skip_deps=True,
        max_doctor_seconds=30,
        target_root=r"C:\target",
    )

    assert observed == ["init"]
    assert payload["summary"]["status"] == "FAIL"
    assert payload["preflight_reuse"]["status"] == "FAILED"
    assert payload["steps"][-1]["id"] == "preflight_receipt_reuse"


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
    monkeypatch.setattr(codemaps, "render_init_preflight", lambda _mode, **_kwargs: ["preflight"])
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
    monkeypatch.setattr(codemaps, "render_init_preflight", lambda _mode, **_kwargs: ["preflight"])
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
    monkeypatch.setattr(codemaps, "render_init_preflight", lambda _mode, **_kwargs: ["preflight"])
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


def test_public_watch_forwards_repeated_paths_as_one_once_command(monkeypatch):
    commands = []
    monkeypatch.setattr(codemaps, "_target_root_env", lambda _args: None)
    monkeypatch.setattr(codemaps, "ensure_runtime_truth", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(codemaps, "import_target_available", lambda *_args: True)
    monkeypatch.setattr(codemaps, "python_subprocess_env", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(codemaps, "run_command", lambda command, **_kwargs: commands.append(command) or 0)

    result = codemaps.cmd_watch(
        argparse.Namespace(
            path=["src/feature.ts", "src/feature.test.ts"],
            debounce=0.5,
            once=True,
            target_root=None,
        )
    )

    assert result == 0
    assert commands == [[
        "python",
        "-m",
        "tools.orchestrators.watchdog",
        "--path",
        "src/feature.ts",
        "--path",
        "src/feature.test.ts",
        "--debounce",
        "0.5",
        "--once",
    ]]


def test_public_watch_rejects_repeated_paths_in_live_mode_before_target_side_effects(monkeypatch):
    target_env = Mock(side_effect=AssertionError("target setup must not start"))
    monkeypatch.setattr(codemaps, "_target_root_env", target_env)

    result = codemaps.cmd_watch(
        argparse.Namespace(
            path=["src/feature.ts", "src/feature.test.ts"],
            debounce=0.5,
            once=False,
            target_root=None,
        )
    )

    assert result == 2
    target_env.assert_not_called()


def test_external_watch_transports_one_preflight_receipt_to_watchdog(monkeypatch, tmp_path):
    target_env = {"CODEMAPS_TARGET_ROOT": str(tmp_path.resolve())}
    preflight_calls = []
    command_envs = []
    monkeypatch.setattr(codemaps, "_target_root_env", lambda _args: target_env)
    monkeypatch.setattr(
        codemaps,
        "_begin_external_target_generation",
        lambda runtime_env, **_kwargs: runtime_env.update(
            {"CODEMAPS_EXTERNAL_RUN_ID": "sage-watch-fixture"}
        ) or (tmp_path / "out", "sage-watch-fixture"),
    )
    closed = []
    monkeypatch.setattr(
        "tools.core.external_target_generation.close_external_target_generation_without_promotion",
        lambda *args, **kwargs: closed.append((args, kwargs)) or {},
    )

    def preflight(_args, runtime_env):
        preflight_calls.append(runtime_env)
        runtime_env["CODEMAPS_TARGET_PREFLIGHT_RECEIPT"] = "C:/receipt/runs/preflight.json"
        runtime_env["CODEMAPS_TARGET_PREFLIGHT_RECEIPT_SHA256"] = "a" * 64
        return 0

    monkeypatch.setattr(codemaps, "_run_external_target_preflight_if_needed", preflight)
    monkeypatch.setattr(codemaps, "ensure_runtime_truth", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(codemaps, "import_target_available", lambda *_args: True)
    monkeypatch.setattr(codemaps, "python_subprocess_env", lambda source, **_kwargs: dict(source))
    monkeypatch.setattr(
        codemaps,
        "run_command",
        lambda _command, **kwargs: command_envs.append(kwargs["env"]) or 0,
    )

    result = codemaps.cmd_watch(
        argparse.Namespace(path="", debounce=None, once=True, target_root=str(tmp_path))
    )

    assert result == 0
    assert preflight_calls == [target_env]
    assert command_envs[0]["CODEMAPS_TARGET_PREFLIGHT_RECEIPT_SHA256"] == "a" * 64
    assert command_envs[0]["CODEMAPS_EXTERNAL_RUN_ID"] == "sage-watch-fixture"
    assert closed[0][1]["reason"] == "external_watch_is_not_atomic_current_eligible"


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


def test_external_target_preflight_transports_exact_receipt_to_runtime(monkeypatch, tmp_path):
    sage_root = tmp_path / "sage"
    target_root = tmp_path / "target"
    target_root.mkdir()
    receipt_path = (
        sage_root
        / "output"
        / "external_targets"
        / "fixture"
        / ".raw"
        / "external_target_preflight.json"
    )
    payload = {
        "meta": {"kind": "external_target_preflight", "run_id": "preflight-fixture"},
        "target": {
            "root": str(target_root.resolve()),
            "output_dir": str(receipt_path.parents[1]),
        },
    }

    def run_command(_command):
        receipt_path.parent.mkdir(parents=True)
        receipt_path.write_text(json.dumps(payload), encoding="utf-8")
        immutable_path = receipt_path.parent / "runs" / "preflight-fixture.json"
        immutable_path.parent.mkdir(parents=True)
        immutable_path.write_text(json.dumps(payload), encoding="utf-8")
        return 0

    monkeypatch.setattr(codemaps, "run_command", run_command)
    monkeypatch.setattr(codemaps, "CODE_MAPS_DIR", sage_root)
    monkeypatch.setattr(codemaps, "_target_output_slug", lambda _target: "fixture")
    args = argparse.Namespace(
        target_root=str(target_root),
        skip_target_preflight=False,
        projects="MAIN",
    )
    runtime_env = {
        "CODEMAPS_TARGET_ROOT": str(target_root.resolve()),
        "CODEMAPS_TARGET_PROJECTS": "MAIN",
    }

    result = codemaps._run_external_target_preflight_if_needed(args, runtime_env)

    assert result == 0
    assert runtime_env["CODEMAPS_TARGET_PREFLIGHT_RECEIPT"] == str(
        (receipt_path.parent / "runs" / "preflight-fixture.json").resolve()
    )
    assert len(runtime_env["CODEMAPS_TARGET_PREFLIGHT_RECEIPT_SHA256"]) == 64


def test_installation_proof_preflight_reuse_rebinds_into_current_generation(
    monkeypatch,
    tmp_path,
):
    target_root = tmp_path / "target"
    target_root.mkdir()
    receipt = {
        "meta": {
            "kind": "external_target_preflight",
            "run_id": "preflight-init",
            "artifact_semantics": {"immutable_run": "old"},
        },
        "target": {"root": str(target_root.resolve()), "output_dir": "C:/old-output"},
        "summary": {"status": "PASS"},
    }
    observed = {}

    def load_receipt(target_path, *, source_env, require_freshness):
        observed["load"] = (target_path, source_env, require_freshness)
        return receipt

    def persist(payload):
        observed["persisted"] = payload
        payload["meta"]["run_id"] = "preflight-rebound"
        return payload

    monkeypatch.setattr(core_config, "load_external_target_preflight_receipt", load_receipt)
    monkeypatch.setattr(external_target_preflight, "persist_preflight", persist)
    monkeypatch.setattr(
        external_target_preflight,
        "preflight_receipt_transport",
        lambda _payload: {
            "path": "C:/current/runs/preflight-rebound.json",
            "sha256": "b" * 64,
            "run_id": "preflight-rebound",
        },
    )
    monkeypatch.setattr(
        codemaps,
        "run_command",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("fresh receipt reuse must not launch a second Preflight")
        ),
    )
    args = argparse.Namespace(
        target_root=str(target_root),
        projects="MAIN",
        skip_target_preflight=False,
    )
    runtime_env = {
        "CODEMAPS_TARGET_ROOT": str(target_root.resolve()),
        "CODEMAPS_TARGET_PROJECTS": "MAIN",
        "CODEMAPS_TARGET_PREFLIGHT_RECEIPT": "C:/init/runs/preflight-init.json",
        "CODEMAPS_TARGET_PREFLIGHT_RECEIPT_SHA256": "a" * 64,
        "CODEMAPS_TARGET_PREFLIGHT_REUSE_MODE": "installation_proof",
        "CODEMAPS_EXTERNAL_RUN_ID": "sage-run-current",
    }

    result = codemaps._run_external_target_preflight_if_needed(args, runtime_env)

    assert result == 0
    assert observed["load"][2] is True
    rebound = observed["persisted"]
    assert rebound["meta"]["receipt_reuse"]["reused_from_run_id"] == "preflight-init"
    assert "artifact_semantics" not in rebound["meta"]
    rebound_output_dir = Path(rebound["target"]["output_dir"])
    assert rebound_output_dir.name == "sage-run-current"
    assert rebound_output_dir.parent.name == "generations"
    assert runtime_env["CODEMAPS_TARGET_PREFLIGHT_RECEIPT_SHA256"] == "b" * 64


def test_installation_proof_preflight_reuse_fails_closed_when_target_is_stale(
    monkeypatch,
    tmp_path,
):
    target_root = tmp_path / "target"
    target_root.mkdir()
    monkeypatch.setattr(
        core_config,
        "load_external_target_preflight_receipt",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("stale target")),
    )
    monkeypatch.setattr(
        codemaps,
        "run_command",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("stale reuse must not silently launch another Preflight")
        ),
    )
    args = argparse.Namespace(
        target_root=str(target_root),
        projects=None,
        skip_target_preflight=False,
    )
    runtime_env = {
        "CODEMAPS_TARGET_ROOT": str(target_root.resolve()),
        "CODEMAPS_TARGET_PREFLIGHT_RECEIPT": "C:/init/runs/preflight-init.json",
        "CODEMAPS_TARGET_PREFLIGHT_RECEIPT_SHA256": "a" * 64,
        "CODEMAPS_TARGET_PREFLIGHT_REUSE_MODE": "installation_proof",
        "CODEMAPS_EXTERNAL_RUN_ID": "sage-run-current",
    }

    assert codemaps._run_external_target_preflight_if_needed(args, runtime_env) == 2


def test_installation_proof_preflight_reuse_persists_real_generation_receipt(
    monkeypatch,
    tmp_path,
):
    sage_root = tmp_path / f"sage-{'x' * 180}"
    target_root = tmp_path / "target"
    target_root.mkdir()
    (target_root / "package.json").write_text(
        json.dumps({"devDependencies": {"typescript": "5.7.0"}}),
        encoding="utf-8",
    )
    (target_root / "app.ts").write_text(
        "export const app = 1;\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(codemaps, "CODE_MAPS_DIR", sage_root)
    monkeypatch.setattr(
        external_target_preflight,
        "EXTERNAL_TARGETS_DIR",
        sage_root / "output" / "external_targets",
    )
    initial = external_target_preflight.build_preflight(
        target_root,
        projects="MAIN",
    )
    external_target_preflight.persist_preflight(initial)
    transport = external_target_preflight.preflight_receipt_transport(initial)
    args = argparse.Namespace(
        target_root=str(target_root),
        projects="MAIN",
        skip_target_preflight=False,
    )
    runtime_env = {
        "CODEMAPS_TARGET_ROOT": str(target_root.resolve()),
        "CODEMAPS_TARGET_PROJECTS": "MAIN",
        "CODEMAPS_TARGET_PREFLIGHT_RECEIPT": transport["path"],
        "CODEMAPS_TARGET_PREFLIGHT_RECEIPT_SHA256": transport["sha256"],
        "CODEMAPS_TARGET_PREFLIGHT_REUSE_MODE": "installation_proof",
        "CODEMAPS_EXTERNAL_RUN_ID": "sage-run-current",
    }

    result = codemaps._run_external_target_preflight_if_needed(args, runtime_env)

    assert result == 0
    rebound_path = Path(runtime_env["CODEMAPS_TARGET_PREFLIGHT_RECEIPT"])
    assert len(str(rebound_path)) > 260
    assert Path(native_filesystem_path(rebound_path)).is_file()
    assert "sage-run-current" in rebound_path.parts
    rebound = json.loads(
        Path(native_filesystem_path(rebound_path)).read_text(encoding="utf-8")
    )
    assert rebound["meta"]["receipt_reuse"]["reused_from_run_id"] == transport["run_id"]
    assert rebound["target"]["root"] == str(target_root.resolve())


def test_target_root_env_preserves_only_authorized_installation_proof_transport(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv(
        "CODEMAPS_TARGET_PREFLIGHT_RECEIPT",
        "C:/receipts/runs/preflight-init.json",
    )
    monkeypatch.setenv("CODEMAPS_TARGET_PREFLIGHT_RECEIPT_SHA256", "a" * 64)
    monkeypatch.setenv("CODEMAPS_TARGET_PREFLIGHT_REUSE_MODE", "installation_proof")
    monkeypatch.setenv("CODEMAPS_TARGET_PROJECTS", "MAIN")
    args = argparse.Namespace(target_root=str(tmp_path), projects="MAIN")

    runtime_env = codemaps._target_root_env(args)

    assert runtime_env["CODEMAPS_TARGET_PREFLIGHT_REUSE_MODE"] == "installation_proof"
    assert runtime_env["CODEMAPS_TARGET_PREFLIGHT_RECEIPT_SHA256"] == "a" * 64
    assert runtime_env["CODEMAPS_TARGET_PROJECTS"] == "MAIN"
