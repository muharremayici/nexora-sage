from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from tools.core.mcp_v1_compat import (
    CONTRACT_ID,
    MCPV1SettingsCompatibilityError,
    ensure_fastmcp_v1_settings_complete,
)
from tools.core.python_runtime_env import isolated_python_subprocess_env


ROOT = Path(__file__).resolve().parents[2]


def test_incomplete_settings_are_rebuilt_without_warning_filtering() -> None:
    class IncompleteSettings:
        __pydantic_complete__ = False
        calls = 0

        @classmethod
        def model_rebuild(cls):
            cls.calls += 1
            cls.__pydantic_complete__ = True
            return True

    receipt = ensure_fastmcp_v1_settings_complete(IncompleteSettings)

    assert receipt == {
        "contract": CONTRACT_ID,
        "status": "rebuilt",
        "complete_before": False,
        "complete_after": True,
        "rebuild_result": True,
        "warning_suppression": False,
    }
    assert IncompleteSettings.calls == 1


def test_complete_settings_keep_the_public_hook_idempotent() -> None:
    class CompleteSettings:
        __pydantic_complete__ = True
        calls = 0

        @classmethod
        def model_rebuild(cls):
            cls.calls += 1
            return None

    receipt = ensure_fastmcp_v1_settings_complete(CompleteSettings)

    assert receipt["status"] == "already_complete"
    assert receipt["complete_after"] is True
    assert CompleteSettings.calls == 1


def test_missing_or_failed_rebuild_is_not_silenced() -> None:
    class MissingRebuild:
        __pydantic_complete__ = False

    class FailedRebuild:
        __pydantic_complete__ = False

        @classmethod
        def model_rebuild(cls):
            raise TypeError("unresolved")

    class UnresolvedRebuild:
        __pydantic_complete__ = False

        @classmethod
        def model_rebuild(cls):
            return None

    with pytest.raises(MCPV1SettingsCompatibilityError, match="public model_rebuild"):
        ensure_fastmcp_v1_settings_complete(MissingRebuild)
    with pytest.raises(MCPV1SettingsCompatibilityError, match="failed before server"):
        ensure_fastmcp_v1_settings_complete(FailedRebuild)
    with pytest.raises(MCPV1SettingsCompatibilityError, match="remained incomplete"):
        ensure_fastmcp_v1_settings_complete(UnresolvedRebuild)


def test_supported_fastmcp_runtime_preserves_custom_lifespan() -> None:
    from mcp.server.fastmcp import FastMCP
    from mcp.server.fastmcp.server import Settings

    receipt = ensure_fastmcp_v1_settings_complete(Settings)
    events: list[tuple[str, bool]] = []

    @asynccontextmanager
    async def lifespan(server):
        events.append(("enter", server is instance))
        yield {"ready": True}
        events.append(("exit", server is instance))

    instance = FastMCP("mcp-v1-compat-test", lifespan=lifespan)

    async def exercise() -> None:
        async with instance._mcp_server.lifespan(instance._mcp_server):
            events.append(("body", True))

    asyncio.run(exercise())

    assert receipt["complete_after"] is True
    assert instance.settings.lifespan is lifespan
    assert events == [("enter", True), ("body", True), ("exit", True)]


def test_stdio_entrypoint_exits_cleanly_on_eof_with_warnings_as_errors() -> None:
    env = isolated_python_subprocess_env(
        os.environ,
        code_maps_dir=ROOT,
        vendor_paths=[],
    )
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-W",
            "error",
            str(ROOT / "tools" / "mcp" / "server.py"),
        ],
        cwd=ROOT,
        env=env,
        input="",
        text=True,
        capture_output=True,
        timeout=45,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""
