import argparse
import json
import os
from pathlib import Path

import codemaps
import pytest
from tools.core import bootstrap_env
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
