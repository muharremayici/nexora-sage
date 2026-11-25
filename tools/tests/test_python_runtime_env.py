import argparse
import json
import os
import subprocess
import sys
from types import SimpleNamespace
from pathlib import Path

import codemaps
import pytest
from tools.core import bootstrap_env, mcp_runtime_config, vendor_bootstrap
from tools.core.mcp_tool_profiles import (
    available_mcp_tool_profiles,
    project_mcp_tool_names,
    resolve_mcp_tool_profile,
)
from tools.core.python_runtime_env import (
    compose_pythonpath,
    isolated_python_subprocess_env,
    python_subprocess_env,
    utf8_subprocess_env,
)


def test_compose_pythonpath_drops_empty_and_duplicate_entries_preserving_order():
    value = compose_pythonpath(
        ["root", "vendor"],
        "root;;other;vendor;",
        separator=";",
    )

    assert value == "root;vendor;other"


def test_python_subprocess_env_does_not_mutate_source_and_sets_utf8():
    root = Path("project")
    vendor = Path("vendor")
    source = {"PYTHONPATH": f"other{os.pathsep}{os.pathsep}project", "KEEP": "yes"}

    env = python_subprocess_env(
        source,
        code_maps_dir=root,
        vendor_paths=[vendor],
    )

    assert source["PYTHONPATH"] == f"other{os.pathsep}{os.pathsep}project"
    assert env["PYTHONPATH"].split(os.pathsep) == [str(root), str(vendor), "other"]
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert env["PYTHONUTF8"] == "1"
    assert env["KEEP"] == "yes"


def test_utf8_subprocess_env_copies_source_and_overrides_platform_defaults():
    source = {"PYTHONIOENCODING": "cp1254", "PYTHONUTF8": "0", "KEEP": "yes"}

    env = utf8_subprocess_env(source)

    assert source["PYTHONIOENCODING"] == "cp1254"
    assert source["PYTHONUTF8"] == "0"
    assert env == {"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "KEEP": "yes"}


def test_isolated_python_subprocess_env_preserves_runtime_identity_but_not_secrets():
    source = {
        "PATH": "runtime-bin",
        "APPDATA": "runtime-appdata",
        "USERPROFILE": "runtime-profile",
        "PYTHONUSERBASE": "runtime-userbase",
        "CODEMAPS_PROFILE": "sage_self",
        "CODEMAPS_TARGET_PREFLIGHT_RECEIPT": "fixture/runs/preflight.json",
        "CODEMAPS_TARGET_PREFLIGHT_RECEIPT_SHA256": "a" * 64,
        "CODEMAPS_TARGET_PREFLIGHT_REUSE_MODE": "installation_proof",
        "CODEMAPS_TARGET_PROJECTS": "MAIN",
        "CODEMAPS_HITL_SECRET": "must-not-cross-boundary",
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "safe.directory",
        "GIT_CONFIG_VALUE_0": "fixture-root",
        "GIT_CONFIG_KEY_1": "http.extraHeader",
        "GIT_CONFIG_VALUE_1": "Authorization: must-not-cross-boundary",
        "UNRELATED_SECRET": "must-not-cross-boundary",
        "API_TOKEN": "must-not-cross-boundary",
    }

    env = isolated_python_subprocess_env(
        source,
        code_maps_dir=Path("project"),
        vendor_paths=[Path("vendor")],
    )

    assert env["APPDATA"] == "runtime-appdata"
    assert env["USERPROFILE"] == "runtime-profile"
    assert env["PYTHONUSERBASE"] == "runtime-userbase"
    assert env["CODEMAPS_PROFILE"] == "sage_self"
    assert env["CODEMAPS_TARGET_PREFLIGHT_REUSE_MODE"] == "installation_proof"
    assert env["CODEMAPS_TARGET_PREFLIGHT_RECEIPT_SHA256"] == "a" * 64
    assert env["CODEMAPS_TARGET_PROJECTS"] == "MAIN"
    assert env["GIT_CONFIG_COUNT"] == "1"
    assert env["GIT_CONFIG_KEY_0"] == "safe.directory"
    assert env["GIT_CONFIG_VALUE_0"] == "fixture-root"
    assert env["PYTHONPATH"].split(os.pathsep) == ["project", "vendor"]
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert env["PYTHONUTF8"] == "1"
    assert "CODEMAPS_HITL_SECRET" not in env
    assert "GIT_CONFIG_KEY_1" not in env
    assert "GIT_CONFIG_VALUE_1" not in env
    assert "UNRELATED_SECRET" not in env
    assert "API_TOKEN" not in env


def test_mcp_print_config_exposes_the_deduplicated_runtime_path(monkeypatch, capsys):
    root = str(codemaps.CODE_MAPS_DIR)
    monkeypatch.setattr(
        codemaps.os,
        "environ",
        {
            "PYTHONPATH": f"{root}{os.pathsep}{os.pathsep}",
            "API_TOKEN": "must-not-enter-client-config",
        },
    )
    monkeypatch.setattr(codemaps, "VENDOR_PATHS", [])

    assert codemaps.cmd_mcp(argparse.Namespace(profile="target_repository_default", print_config=True)) == 0

    payload = json.loads(capsys.readouterr().out)
    public_env = payload["mcpServers"]["nexora-sage"]["env"]
    path_value = public_env["PYTHONPATH"]
    assert path_value == root
    assert public_env["PYTHONIOENCODING"] == "utf-8"
    assert public_env["PYTHONUTF8"] == "1"
    assert public_env["SAGE_MCP_TOOL_PROFILE"] == "target_repository_default"
    assert set(public_env) == {
        "PYTHONIOENCODING",
        "PYTHONUTF8",
        "PYTHONPATH",
        "SAGE_ACTOR_PROFILE",
        "SAGE_MCP_TOOL_PROFILE",
    }


def test_bootstrap_and_public_cli_emit_the_same_mcp_client_config(monkeypatch, capsys):
    root = str(codemaps.CODE_MAPS_DIR)
    source_env = {"PYTHONPATH": f"{root}{os.pathsep}{root}"}
    monkeypatch.setattr(codemaps.os, "environ", source_env)
    monkeypatch.setattr(bootstrap_env.os, "environ", source_env)
    monkeypatch.setattr(codemaps, "VENDOR_PATHS", [])
    monkeypatch.setattr(bootstrap_env, "VENDOR_PATHS", [])

    assert codemaps.cmd_mcp(
        argparse.Namespace(profile=None, print_config=True)
    ) == 0
    cli_config = json.loads(capsys.readouterr().out)

    bootstrap_env.generate_mcp_snippet()
    bootstrap_output = capsys.readouterr().out
    json_start = bootstrap_output.index("{")
    bootstrap_config, _ = json.JSONDecoder().raw_decode(bootstrap_output[json_start:])

    assert bootstrap_config == cli_config


def test_public_distribution_exposes_only_target_repository_mcp_profiles(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    for name in ("agent_surface_contract.json", "mcp_tool_roles.json"):
        (config_dir / name).write_text(
            (codemaps.CODE_MAPS_DIR / "config" / name).read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    (tmp_path / "PUBLIC_DISTRIBUTION_MANIFEST.json").write_text("{}\n", encoding="utf-8")

    assert available_mcp_tool_profiles(tmp_path) == [
        "target_repository_default",
        "target_repository_followup",
    ]
    assert (
        resolve_mcp_tool_profile(tmp_path, "target_repository_followup")
        == "target_repository_followup"
    )
    with pytest.raises(ValueError, match="Unknown MCP tool profile"):
        resolve_mcp_tool_profile(tmp_path, "sage_operator_debug")
    with pytest.raises(ValueError, match="Unknown MCP tool profile"):
        project_mcp_tool_names(tmp_path, "sage_operator_debug")


@pytest.mark.parametrize("profile", ["target_repository_default", "target_repository_followup"])
@pytest.mark.parametrize("path_case", ["ordinary", "venv_unicode", "long_literal"])
def test_mcp_config_binds_exact_running_interpreter_without_client_path_selection(monkeypatch, tmp_path, profile, path_case):
    # These are literal configuration values, not installed or executed runtimes.
    paths = {
        "ordinary": str(tmp_path / "python.exe"),
        "venv_unicode": str(tmp_path / "Türkçe O'Brien & $env" / ".venv" / "Scripts" / "python.exe"),
        "long_literal": str(tmp_path / ("long-component-" * 30) / "python.exe"),
    }
    command = paths[path_case]
    monkeypatch.setattr(mcp_runtime_config.sys, "executable", command)
    source = {"PATH": "unrelated-client-python", "PYTHONPATH": "custom-imports",
              "API_TOKEN": "synthetic-private", "CODEMAPS_HITL_SECRET": "synthetic-private"}
    before = dict(source)
    runtime = mcp_runtime_config.build_mcp_runtime_contract(source_env=source,
        code_maps_dir=codemaps.CODE_MAPS_DIR, vendor_paths=[], mcp_script=codemaps.MCP_SCRIPT, requested_profile=profile)
    client = runtime["client_config"]["mcpServers"]["nexora-sage"]
    assert client["command"] == command and client["args"] == [str(codemaps.MCP_SCRIPT)]
    assert json.loads(json.dumps(client, ensure_ascii=False))["command"] == command
    assert source == before and runtime["process_env"]["PATH"] == source["PATH"]
    assert set(client["env"]) == {"PYTHONIOENCODING", "PYTHONUTF8", "PYTHONPATH", "SAGE_MCP_TOOL_PROFILE", "SAGE_ACTOR_PROFILE"}
    assert client["env"]["SAGE_MCP_TOOL_PROFILE"] == client["env"]["SAGE_ACTOR_PROFILE"] == profile
    assert not set(client["env"]) & {"PATH", "VIRTUAL_ENV", "PYTHONHOME", "API_TOKEN", "CODEMAPS_HITL_SECRET"}


def test_mcp_interpreter_spelling_is_not_resolved_to_base_python(monkeypatch, tmp_path):
    command = str(tmp_path / ".venv" / "Scripts" / ".." / "Scripts" / "python.exe")
    def forbidden(*_a, **_k):
        raise AssertionError("No symlink/base-executable or filesystem-availability promotion")
    with monkeypatch.context() as patch:
        patch.setattr(mcp_runtime_config.sys, "executable", command)
        patch.setattr(Path, "resolve", forbidden)
        patch.setattr(Path, "is_file", forbidden)
        assert mcp_runtime_config._current_python_command() == command


@pytest.mark.parametrize("command", [None, "", " ", "python", "../python", "C:python.exe", "\x00", "\ud800", 3, []],
    ids=["none", "empty", "blank", "bare", "relative", "drive_relative", "nul", "surrogate", "number", "list"])
def test_unavailable_interpreter_refuses_config_without_fallback(monkeypatch, command):
    monkeypatch.setattr(mcp_runtime_config.sys, "executable", command)
    with pytest.raises(ValueError, match="Regenerate MCP configuration"):
        mcp_runtime_config.build_mcp_runtime_contract(source_env={}, code_maps_dir=codemaps.CODE_MAPS_DIR,
            vendor_paths=[], mcp_script=codemaps.MCP_SCRIPT, requested_profile="target_repository_default")


@pytest.mark.parametrize("invalid_unit", ["\ud800", "\udfff"], ids=["high_surrogate", "low_surrogate"])
def test_invalid_interpreter_encoding_has_explicit_refusal_cause(monkeypatch, tmp_path, invalid_unit):
    command = str(tmp_path / ("private-" + invalid_unit) / "python.exe")
    monkeypatch.setattr(mcp_runtime_config.sys, "executable", command)

    with pytest.raises(ValueError) as caught:
        mcp_runtime_config._current_python_command()

    assert isinstance(caught.value.__cause__, UnicodeEncodeError)
    assert str(caught.value) == (
        "Current Python executable identity is unavailable or invalid. "
        "Regenerate MCP configuration with the intended local Python interpreter."
    )
    assert "private-" not in str(caught.value)
    assert invalid_unit not in str(caught.value)


@pytest.mark.parametrize("print_config", [False, True])
def test_mcp_cli_reports_invalid_identity_before_dependency_probe_or_launch(monkeypatch, capsys, print_config):
    def forbidden(*_a, **_k):
        raise AssertionError("Config failure must not install, probe optional MCP or launch")
    monkeypatch.setattr(mcp_runtime_config.sys, "executable", None)
    monkeypatch.setattr(codemaps, "run_command", forbidden)
    monkeypatch.setattr(codemaps, "import_target_available", forbidden)
    assert codemaps.cmd_mcp(argparse.Namespace(profile="target_repository_default", print_config=print_config)) == 2
    output = capsys.readouterr().out
    assert "[ERROR]" in output and "Regenerate MCP configuration" in output
    assert '"mcpServers"' not in output



def vendor_case(tmp_path, monkeypatch, *, enabled=True, no_user_site=0):
    root = tmp_path / "product"
    roots = [root / "vendor_runtime", root.parent / "codemaps_vendor",
             root / ".vendor", root / ".vendor_user"]
    for path in roots:
        path.mkdir(parents=True)
    user_site = tmp_path / "user-site"
    user_site.mkdir()
    runtime = SimpleNamespace(path=["stdlib"], flags=SimpleNamespace(no_user_site=no_user_site))
    monkeypatch.setattr(vendor_bootstrap, "sys", runtime)
    monkeypatch.setattr(vendor_bootstrap, "site", SimpleNamespace(
        ENABLE_USER_SITE=enabled, getusersitepackages=lambda: str(user_site)))
    monkeypatch.delenv("PYTHONNOUSERSITE", raising=False)
    return root, roots, user_site, runtime


@pytest.mark.parametrize("empty_flag", [False, True])
def test_vendor_bootstrap_preserves_allowed_user_site_vendor_order_and_idempotence(
    tmp_path, monkeypatch, empty_flag
):
    root, roots, user_site, runtime = vendor_case(tmp_path, monkeypatch)
    if empty_flag:
        monkeypatch.setenv("PYTHONNOUSERSITE", "")
    assert vendor_bootstrap.inject_vendor_paths(root) == roots
    expected = [str(user_site), *(str(path) for path in roots), "stdlib"]
    assert runtime.path == expected
    assert vendor_bootstrap.inject_vendor_paths(root) == roots
    assert runtime.path == expected


@pytest.mark.parametrize("enabled,no_user_site,env_flag", [
    (False, 0, None), (None, 0, None), (True, 1, None),
    (True, 0, "1"), (True, 0, "0"),
])
def test_vendor_bootstrap_never_discovers_or_inserts_suppressed_user_site(
    tmp_path, monkeypatch, enabled, no_user_site, env_flag
):
    root, roots, user_site, runtime = vendor_case(
        tmp_path, monkeypatch, enabled=enabled, no_user_site=no_user_site)
    if env_flag is not None:
        monkeypatch.setenv("PYTHONNOUSERSITE", env_flag)

    def forbidden():
        raise AssertionError("Suppressed user-site discovery must not run")

    monkeypatch.setattr(vendor_bootstrap.site, "getusersitepackages", forbidden)
    assert vendor_bootstrap.inject_vendor_paths(root) == roots
    assert runtime.path == [*(str(path) for path in roots), "stdlib"]
    assert str(user_site) not in runtime.path


def test_vendor_bootstrap_retains_readable_vendors_when_a_vendor_is_locked(
    tmp_path, monkeypatch
):
    root, roots, _, runtime = vendor_case(tmp_path, monkeypatch, enabled=False)
    real_listdir = os.listdir

    def readable(path):
        if path == roots[0]:
            raise PermissionError("synthetic locked vendor")
        return real_listdir(path)

    monkeypatch.setattr(vendor_bootstrap, "os", SimpleNamespace(
        listdir=readable, environ=os.environ))
    assert vendor_bootstrap.inject_vendor_paths(root) == roots[1:]
    assert runtime.path == [*(str(path) for path in roots[1:]), "stdlib"]


@pytest.mark.parametrize("consumer", [
    "codemaps", "tools.core.bootstrap_env", "tools.mcp.server",
])
def test_bootstrap_consumers_keep_poisoned_user_site_out_of_spawn_children(
    tmp_path, consumer
):
    user_site = tmp_path / "user-site"
    user_site.mkdir()
    (user_site / "pathlib.py").write_text(
        "raise RuntimeError('synthetic user-site pathlib must not load')\n",
        encoding="utf-8",
    )
    worker = tmp_path / "isolated_worker.py"
    worker.write_text(
        "from pathlib import Path\n"
        "import pathlib\n"
        "def report(connection):\n"
        "    connection.send(str(Path(pathlib.__file__).resolve()))\n"
        "    connection.close()\n",
        encoding="utf-8",
    )
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import importlib, json, multiprocessing, site, sys\n"
        "from pathlib import Path\n"
        "def main():\n"
        "    root, poisoned, consumer = sys.argv[1:]\n"
        "    sys.path.insert(0, root)\n"
        "    site.getusersitepackages = lambda: poisoned\n"
        "    importlib.import_module(consumer)\n"
        "    assert poisoned not in sys.path, 'bootstrap reinserted user site'\n"
        "    from isolated_worker import report\n"
        "    ctx = multiprocessing.get_context('spawn')\n"
        "    parent, child = ctx.Pipe(False)\n"
        "    process = ctx.Process(target=report, args=(child,))\n"
        "    process.start(); child.close(); process.join(15)\n"
        "    if process.is_alive():\n"
        "        process.terminate(); process.join(5)\n"
        "        raise AssertionError('synthetic spawn did not settle')\n"
        "    assert process.exitcode == 0, process.exitcode\n"
        "    assert parent.poll(1), 'child did not report pathlib origin'\n"
        "    origin = parent.recv(); parent.close(); process.close()\n"
        "    assert not Path(origin).is_relative_to(Path(poisoned)), origin\n"
        "    print(json.dumps({'consumer': consumer, 'user_site_inserted': False,\n"
        "                      'child_exitcode': 0, 'pathlib_origin': origin}))\n"
        "if __name__ == '__main__':\n"
        "    main()\n",
        encoding="utf-8",
    )
    env = {**os.environ, "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run(
        [sys.executable, "-B", str(probe), str(codemaps.CODE_MAPS_DIR),
         str(user_site), consumer],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=40,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout.splitlines()[-1])
    assert report["consumer"] == consumer
    assert report["child_exitcode"] == 0
    assert report["user_site_inserted"] is False
