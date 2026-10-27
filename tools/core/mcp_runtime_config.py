from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
import sys
from typing import Any

from tools.core.mcp_tool_profiles import MCP_TOOL_PROFILE_ENV, resolve_mcp_tool_profile
from tools.core.python_runtime_env import python_subprocess_env
from tools.core.execution_identity import (
    SAGE_ACTOR_PROFILE_ENV,
    SAGE_OPERATOR_ACTOR_PROFILE,
    SAGE_REALITY_TARGET_PROFILE_ENV,
    SAGE_SELF_REALITY_PROFILE,
)


MCP_SERVER_NAME = "nexora-sage"
MCP_PUBLIC_ENV_KEYS = (
    "PYTHONIOENCODING",
    "PYTHONUTF8",
    "PYTHONPATH",
    MCP_TOOL_PROFILE_ENV,
    SAGE_ACTOR_PROFILE_ENV,
    SAGE_REALITY_TARGET_PROFILE_ENV,
)


def _current_python_command() -> str:
    """Bind local config to this runtime without dereferencing its venv entry point."""
    command = sys.executable
    unavailable_message = (
        "Current Python executable identity is unavailable or invalid. "
        "Regenerate MCP configuration with the intended local Python interpreter."
    )
    valid = False
    if isinstance(command, str) and command and "\x00" not in command:
        try:
            command.encode("utf-8")
            valid = Path(command).is_absolute()
        except UnicodeError as exc:
            raise ValueError(unavailable_message) from exc
    if not valid:
        raise ValueError(unavailable_message)
    return command


def build_mcp_runtime_contract(
    *,
    source_env: Mapping[str, str],
    code_maps_dir: Path,
    vendor_paths: Iterable[Path],
    mcp_script: Path,
    requested_profile: str | None = None,
) -> dict[str, Any]:
    """Build one authoritative runtime and client configuration for the MCP server."""

    profile = resolve_mcp_tool_profile(code_maps_dir, requested_profile)
    python_command = _current_python_command()
    process_env = python_subprocess_env(
        source_env,
        code_maps_dir=code_maps_dir,
        vendor_paths=vendor_paths,
    )
    process_env[MCP_TOOL_PROFILE_ENV] = profile
    process_env[SAGE_ACTOR_PROFILE_ENV] = profile
    if profile == SAGE_OPERATOR_ACTOR_PROFILE:
        process_env[SAGE_REALITY_TARGET_PROFILE_ENV] = SAGE_SELF_REALITY_PROFILE
    else:
        process_env.pop(SAGE_REALITY_TARGET_PROFILE_ENV, None)
    public_env = {key: process_env[key] for key in MCP_PUBLIC_ENV_KEYS if key in process_env}
    return {
        "profile": profile,
        "process_env": process_env,
        "client_config": {
            "mcpServers": {
                MCP_SERVER_NAME: {
                    "command": python_command,
                    "args": [str(mcp_script)],
                    "env": public_env,
                }
            }
        },
    }
