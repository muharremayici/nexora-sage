"""Bounded MCP v1 runtime compatibility preparation."""

from __future__ import annotations

from typing import Any


CONTRACT_ID = "mcp_v1_fastmcp_settings_lifespan_v1"


class MCPV1SettingsCompatibilityError(RuntimeError):
    """Raised when the supported FastMCP v1 Settings model cannot be completed."""


def ensure_fastmcp_v1_settings_complete(settings_model: type[Any]) -> dict[str, Any]:
    """Complete FastMCP Settings after module import and before server construction."""

    rebuild = getattr(settings_model, "model_rebuild", None)
    if not callable(rebuild):
        raise MCPV1SettingsCompatibilityError(
            "FastMCP v1 Settings does not expose the required public model_rebuild hook"
        )

    complete_before = getattr(settings_model, "__pydantic_complete__", None)
    try:
        rebuild_result = rebuild()
    except Exception as exc:
        raise MCPV1SettingsCompatibilityError(
            "FastMCP v1 Settings.model_rebuild() failed before server construction"
        ) from exc

    complete_after = getattr(settings_model, "__pydantic_complete__", None)
    if complete_after is not True:
        raise MCPV1SettingsCompatibilityError(
            "FastMCP v1 Settings remained incomplete after model_rebuild()"
        )

    return {
        "contract": CONTRACT_ID,
        "status": "already_complete" if complete_before is True else "rebuilt",
        "complete_before": complete_before,
        "complete_after": complete_after,
        "rebuild_result": rebuild_result,
        "warning_suppression": False,
    }
