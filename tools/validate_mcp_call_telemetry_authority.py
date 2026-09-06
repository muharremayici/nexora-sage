from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.mcp_call_telemetry import MCP_OPERATIONAL_DB_ENV, load_mcp_call_telemetry


RAW_OUTPUT_PATH = RAW_DIR / "mcp_call_telemetry_authority_validation.json"
REPORT_OUTPUT_PATH = REPORTS_DIR / "mcp_call_telemetry_authority_validation.md"
CHILD_SCRIPT = r"""
import asyncio
import json
import os
from pathlib import Path

from tools.mcp import server

runtime_root = Path(os.environ["SAGE_MCP_AUTHORITY_RUNTIME_ROOT"])
mode = os.environ["SAGE_MCP_AUTHORITY_MODE"]
target_root = os.environ.get("SAGE_MCP_AUTHORITY_TARGET_ROOT", "")
server.BASE_DIR = runtime_root / "sage"
server.RAW_DIR = server.BASE_DIR / "output" / ".raw"
server.REPORTS_DIR = server.BASE_DIR / "output" / "reports"
server.mcp.set_visible_tools("target_repository_default", {"get_watchdog_session"})
arguments = {"target_root": target_root} if target_root else {}
result = asyncio.run(server.mcp.call_tool("get_watchdog_session", arguments))
print(json.dumps({"mode": mode, "result_blocks": len(result)}, ensure_ascii=False))
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _manifest(root: Path) -> dict[str, dict[str, Any]]:
    manifest: dict[str, dict[str, Any]] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        content = path.read_bytes()
        manifest[relative] = {
            "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
    return manifest


def _changed(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    return sorted(key for key in set(before) | set(after) if before.get(key) != after.get(key))


def _check(name: str, passed: bool, details: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def build_validation() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="sage_mcp_authority_") as temp_dir:
        runtime_root = Path(temp_dir).resolve()
        target_a = runtime_root / "target_a"
        target_b = runtime_root / "target_b"
        target_a.mkdir(parents=True)
        target_b.mkdir(parents=True)
        operational_db = runtime_root / "operational" / "mcp_call_telemetry.db"
        modes = [
            ("default_repository", ""),
            ("explicit_target_a", str(target_a)),
            ("explicit_target_b", str(target_b)),
        ]
        transitions: list[dict[str, Any]] = []
        child_results: list[dict[str, Any]] = []
        before = _manifest(runtime_root)

        for mode, target_root in modes:
            env = dict(os.environ)
            env.update(
                {
                    "PYTHONIOENCODING": "utf-8",
                    "PYTHONUTF8": "1",
                    "PYTHONPATH": os.pathsep.join(filter(None, [str(ROOT), env.get("PYTHONPATH", "")])),
                    MCP_OPERATIONAL_DB_ENV: str(operational_db),
                    "SAGE_MCP_AUTHORITY_RUNTIME_ROOT": str(runtime_root),
                    "SAGE_MCP_AUTHORITY_MODE": mode,
                    "SAGE_MCP_AUTHORITY_TARGET_ROOT": target_root,
                }
            )
            completed = subprocess.run(
                [sys.executable, "-c", CHILD_SCRIPT],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                check=False,
            )
            after = _manifest(runtime_root)
            transitions.append({"mode": mode, "changed_files": _changed(before, after)})
            child_results.append(
                {
                    "mode": mode,
                    "returncode": completed.returncode,
                    "stdout_tail": completed.stdout[-500:],
                    "stderr_tail": completed.stderr[-500:],
                }
            )
            before = after

        previous_override = os.environ.get(MCP_OPERATIONAL_DB_ENV)
        os.environ[MCP_OPERATIONAL_DB_ENV] = str(operational_db)
        try:
            ledger = load_mcp_call_telemetry()
        finally:
            if previous_override is None:
                os.environ.pop(MCP_OPERATIONAL_DB_ENV, None)
            else:
                os.environ[MCP_OPERATIONAL_DB_ENV] = previous_override

        entries = ledger.get("entries") if isinstance(ledger.get("entries"), list) else []
        persisted_bytes = operational_db.read_bytes() if operational_db.exists() else b""
        sidecars = sorted(
            path.relative_to(runtime_root).as_posix()
            for path in runtime_root.rglob("*")
            if path.is_file() and path.name.endswith(("-wal", "-shm"))
        )
        expected_change = ["operational/mcp_call_telemetry.db"]
        checks = [
            _check(
                "all_mcp_subprocess_calls_complete",
                all(row["returncode"] == 0 for row in child_results),
                child_results,
            ),
            _check(
                "default_and_explicit_targets_write_only_product_operational_db",
                all(row["changed_files"] == expected_change for row in transitions),
                transitions,
            ),
            _check(
                "one_authoritative_event_per_call",
                len(entries) == 3 and len({row.get("trace_id") for row in entries}) == 3,
                {"entry_count": len(entries), "trace_ids": [row.get("trace_id") for row in entries]},
            ),
            _check(
                "target_modes_are_declared_without_target_identity",
                [row.get("target_mode") for row in entries]
                == ["default_repository", "explicit_target", "explicit_target"],
                [row.get("target_mode") for row in entries],
            ),
            _check(
                "target_paths_and_response_bodies_are_excluded",
                str(target_a).encode("utf-8") not in persisted_bytes
                and str(target_b).encode("utf-8") not in persisted_bytes
                and b"Watchdog Session" not in persisted_bytes,
                {"target_paths_absent": True, "response_marker_absent": True},
            ),
            _check(
                "target_namespaces_remain_file_clean",
                not any(target_a.rglob("*")) and not any(target_b.rglob("*")),
                {"target_a_files": _manifest(target_a), "target_b_files": _manifest(target_b)},
            ),
            _check(
                "subprocess_closeout_leaves_no_sqlite_sidecars",
                not sidecars,
                sidecars,
            ),
        ]
        failures = [row for row in checks if not row["passed"]]
        return {
            "meta": {
                "kind": "mcp_call_telemetry_authority_validation",
                "version": "v1",
                "generated_at": _utc_now(),
                "generator": "tools.validate_mcp_call_telemetry_authority",
            },
            "summary": {
                "status": "PASS" if not failures else "FAIL",
                "checks": len(checks),
                "passed": len(checks) - len(failures),
                "failed": len(failures),
                "subprocess_calls": len(child_results),
            },
            "checks": checks,
        }


def render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    lines = [
        "# MCP Call Telemetry Authority Validation",
        "",
        f"- status: `{summary.get('status')}`",
        f"- subprocess calls: `{summary.get('subprocess_calls')}`",
        f"- checks: `{summary.get('passed')}/{summary.get('checks')}`",
        "",
        "| Check | Result |",
        "|---|---|",
    ]
    for check in payload.get("checks", []):
        lines.append(f"| `{check.get('name')}` | {'PASS' if check.get('passed') else 'FAIL'} |")
    return "\n".join(lines) + "\n"


def run() -> dict[str, Any]:
    payload = build_validation()
    save_json_atomic(RAW_OUTPUT_PATH, payload)
    save_text_atomic(REPORT_OUTPUT_PATH, render_report(payload))
    return payload


def main() -> int:
    payload = run()
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload.get("summary", {}).get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
