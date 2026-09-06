from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
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
                    "command": "python",
                    "args": [str(mcp_script)],
                    "env": public_env,
                }
            }
        },
    }
