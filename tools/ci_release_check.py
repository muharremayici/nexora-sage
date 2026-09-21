from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
CODE_MAPS_DIR = TOOLS_DIR.parent
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))

from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file
from tools.core.operational_limits import (
    ci_release_check_default_timeout_seconds,
    ci_release_check_release_timeout_seconds,
)
from tools.core.subprocess_telemetry import run_observed_subprocess


def _run(name: str, command: list[str]) -> dict[str, Any]:
    timeout_seconds = (
        ci_release_check_release_timeout_seconds()
        if "release-check" in command
        else ci_release_check_default_timeout_seconds()
    )
    proc, duration = run_observed_subprocess(
        command,
        cwd=CODE_MAPS_DIR,
        label=name,
        timeout=timeout_seconds,
        log=lambda message: print(f"[ci-release-check] {message}", flush=True),
    )
    return {
        "name": name,
        "command": command,
        "returncode": proc.returncode,
        "passed": proc.returncode == 0,
        "duration_seconds": duration,
        "stdout_tail": (proc.stdout or "")[-4000:],
        "stderr_tail": (proc.stderr or "")[-4000:],
    }


def run_ci_release_check(skip_release_check: bool = False) -> dict[str, Any]:
    release_check_mode = "skipped_existing_artifact" if skip_release_check else "executed"
    steps = [
        _run(
            "repository_test_contract",
            [
                sys.executable,
                "-B",
                str(CODE_MAPS_DIR / "tools" / "run_release_proof_bundle.py"),
                "--only",
                "engine_contract_tests",
                "--release-phase",
                "development",
                "--trigger",
                "explicit_focused_validation_selected",
            ],
        ),
    ]
    if not skip_release_check:
        steps.append(
            _run(
                "release_check",
                [sys.executable, str(CODE_MAPS_DIR / "sage.py"), "release-check"],
            )
        )
    steps.append(_run("phase_status", [sys.executable, str(CODE_MAPS_DIR / "sage.py"), "phase-status"]))

    readiness = load_json_file(RAW_DIR / "release_readiness.json", {})
    phase_status = load_json_file(RAW_DIR / "release_phase_status.json", {})
    payload = {
        "meta": {"kind": "ci_release_check", "version": "v1"},
        "summary": {
            "total_steps": len(steps),
            "passed_steps": sum(1 for step in steps if step.get("passed")),
            "failed_steps": sum(1 for step in steps if not step.get("passed")),
            "release_check_mode": release_check_mode,
            "release_readiness_source": str(RAW_DIR / "release_readiness.json"),
            "release_readiness": readiness.get("readiness") if isinstance(readiness, dict) else None,
            "phase_status": phase_status.get("summary", {}) if isinstance(phase_status, dict) else {},
        },
        "steps": steps,
    }
    save_json_atomic(RAW_DIR / "ci_release_check.json", payload)

    lines = [
        "# CI Release Check",
        "",
        f"- Total steps: `{payload['summary']['total_steps']}`",
        f"- Passed: `{payload['summary']['passed_steps']}`",
        f"- Failed: `{payload['summary']['failed_steps']}`",
        f"- Release-check mode: `{payload['summary']['release_check_mode']}`",
        f"- Release readiness source: `{payload['summary']['release_readiness_source']}`",
        f"- Release readiness: `{payload['summary']['release_readiness']}`",
        f"- Phase status: `{payload['summary']['phase_status']}`",
        "",
        "| Step | Result | Return code |",
        "|---|---|---:|",
    ]
    if skip_release_check:
        lines.extend(
            [
                "",
                "> Note: `--skip-release-check` uses the existing `release_readiness.json` snapshot. It does not prove a fresh release-check execution.",
                "",
            ]
        )
    for step in steps:
        lines.append(f"| `{step['name']}` | {'PASS' if step['passed'] else 'FAIL'} | {step['returncode']} |")
    save_text_atomic(REPORTS_DIR / "ci_release_check.md", "\n".join(lines) + "\n")
    return payload


def main() -> int:
    skip_release = "--skip-release-check" in sys.argv
    payload = run_ci_release_check(skip_release_check=skip_release)
    print(json.dumps(payload.get("summary", {}), ensure_ascii=False))
    return 0 if payload.get("summary", {}).get("failed_steps", 1) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
