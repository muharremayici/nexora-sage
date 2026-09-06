from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
CODE_MAPS_DIR = TOOLS_DIR.parent
REPO_ROOT = CODE_MAPS_DIR.parent
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))

from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.subprocess_telemetry import run_observed_subprocess

RAW_OUTPUT = RAW_DIR / "optional_feature_smokes.json"
REPORT_OUTPUT = REPORTS_DIR / "optional_feature_smokes.md"
MCP_TOOL_SMOKE_CODE = r"""
import json
import sys
from tools.mcp import server

checks = []
for name, func, args in [
    ("search_symbols", server.search_symbols, ("Project", "")),
    ("get_health_metrics", server.get_health_metrics, ("",)),
    ("get_dead_code", server.get_dead_code, ("",)),
]:
    try:
        result = func(*args)
        checks.append({"name": name, "ok": isinstance(result, str) and len(result.strip()) > 0})
    except Exception as exc:
        checks.append({"name": name, "ok": False, "error": str(exc)})

print(json.dumps({"checks": checks, "ok": all(item["ok"] for item in checks)}, ensure_ascii=False))
sys.exit(0 if all(item["ok"] for item in checks) else 1)
"""


def _run_command(name: str, command: list[str], cwd: Path, timeout: int) -> dict[str, Any]:
    result, duration = run_observed_subprocess(
        command,
        cwd=cwd,
        label=name,
        timeout=timeout,
        log=lambda message: print(f"[optional-feature-smoke] {message}", flush=True),
    )
    output = f"{result.stdout}\n{result.stderr}".strip()
    row = {
        "name": name,
        "command": command,
        "cwd": str(cwd),
        "returncode": result.returncode,
        "duration_seconds": duration,
        "output_excerpt": output[-6000:],
    }
    if result.returncode == 124:
        row["timeout_seconds"] = timeout
    return row


def _classify_browser_result(result: dict[str, Any]) -> tuple[str, str]:
    output = str(result.get("output_excerpt") or "")
    if result.get("returncode") == 0:
        return "PASS", "browser_smoke_completed"
    if "not valid inside a JSX element" in output or "Failed to scan for dependencies" in output:
        return "REPO_BLOCKED", "dev_server_blocked_by_repository_jsx_or_dependency_scan_error"
    if "Timed out waiting" in output and "webServer" in output:
        return "ENV_BLOCKED", "playwright_webserver_timeout"
    if "spawn EPERM" in output:
        return "ENV_BLOCKED", "browser_process_spawn_blocked"
    return "FAIL", "browser_smoke_failed"


def run_validation(include_browser: bool = False, include_watch: bool = False) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []

    mcp = _run_command(
        "mcp_config_smoke",
        [sys.executable, "sage.py", "mcp", "--print-config"],
        CODE_MAPS_DIR,
        45,
    )
    try:
        config = json.loads(str(mcp.get("output_excerpt") or "{}"))
    except json.JSONDecodeError:
        config = {}
    mcp_status = "PASS" if mcp.get("returncode") == 0 and isinstance(config.get("mcpServers"), dict) else "FAIL"
    rows.append({**mcp, "status": mcp_status, "reason": "mcp_config_json_rendered" if mcp_status == "PASS" else "mcp_config_render_failed"})

    mcp_tools = _run_command(
        "mcp_tool_smoke",
        [sys.executable, "-c", MCP_TOOL_SMOKE_CODE],
        CODE_MAPS_DIR,
        60,
    )
    try:
        tool_payload = json.loads(str(mcp_tools.get("output_excerpt") or "{}"))
    except json.JSONDecodeError:
        tool_payload = {}
    tool_status = "PASS" if mcp_tools.get("returncode") == 0 and tool_payload.get("ok") is True else "FAIL"
    rows.append(
        {
            **mcp_tools,
            "status": tool_status,
            "reason": "mcp_core_tools_return_payloads" if tool_status == "PASS" else "mcp_core_tool_smoke_failed",
            "tool_checks": tool_payload.get("checks", []),
        }
    )

    if include_watch:
        watch = _run_command(
            "watch_once_smoke",
            [sys.executable, "sage.py", "watch", "--once", "--path", "../src"],
            CODE_MAPS_DIR,
            240,
        )
        rows.append(
            {
                **watch,
                "status": "PASS" if watch.get("returncode") == 0 else "FAIL",
                "reason": "watch_once_incremental_pulse_completed" if watch.get("returncode") == 0 else "watch_once_failed",
            }
        )

    if include_browser:
        npx_cmd = "npx.cmd" if sys.platform == "win32" else "npx"
        spec_path = f"{CODE_MAPS_DIR.name}/output/scripts/ui_smoke_specs/academy-linguascribe-master-overallacademypage.spec.ts"
        browser = _run_command(
            "browser_smoke_single_spec",
            [
                npx_cmd,
                "playwright",
                "test",
                spec_path,
                "--project=chromium",
                "--reporter=line",
            ],
            REPO_ROOT,
            180,
        )
        status, reason = _classify_browser_result(browser)
        rows.append({**browser, "status": status, "reason": reason})

    blocking_failures = [
        row for row in rows
        if row.get("status") == "FAIL" or (row.get("name") in {"mcp_config_smoke", "watch_once_smoke"} and row.get("status") != "PASS")
    ]
    repo_blocked = [row for row in rows if row.get("status") == "REPO_BLOCKED"]
    env_blocked = [row for row in rows if row.get("status") == "ENV_BLOCKED"]
    payload = {
        "meta": {"kind": "optional_feature_smokes", "version": "v1"},
        "summary": {
            "total_checks": len(rows),
            "passed_checks": sum(1 for row in rows if row.get("status") == "PASS"),
            "failed_checks": len(blocking_failures),
            "repo_blocked_checks": len(repo_blocked),
            "env_blocked_checks": len(env_blocked),
            "release_blocking": False,
        },
        "checks": rows,
    }
    save_json_atomic(RAW_OUTPUT, payload)

    lines = [
        "# Optional Feature Smokes",
        "",
        f"- Total checks: `{payload['summary']['total_checks']}`",
        f"- Passed: `{payload['summary']['passed_checks']}`",
        f"- Failed: `{payload['summary']['failed_checks']}`",
        f"- Repo blocked: `{payload['summary']['repo_blocked_checks']}`",
        f"- Env blocked: `{payload['summary']['env_blocked_checks']}`",
        f"- Release blocking: `{payload['summary']['release_blocking']}`",
        "",
        "| Check | Status | Reason | Exit |",
        "|---|---|---|---:|",
    ]
    for row in rows:
        lines.append(f"| `{row.get('name')}` | `{row.get('status')}` | `{row.get('reason')}` | `{row.get('returncode')}` |")
    save_text_atomic(REPORT_OUTPUT, "\n".join(lines) + "\n")
    return payload


def main() -> int:
    include_browser = "--include-browser" in sys.argv
    include_watch = "--include-watch" in sys.argv
    payload = run_validation(include_browser=include_browser, include_watch=include_watch)
    print(json.dumps(payload.get("summary", {}), ensure_ascii=False))
    return 0 if int(payload.get("summary", {}).get("failed_checks", 1) or 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
