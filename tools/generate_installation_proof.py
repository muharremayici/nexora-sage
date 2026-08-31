from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.init_execution_contract import init_mode_contract, installation_proof_init_mode
from tools.core.installation_authority import resolve_installation_authority_profile
from tools.core.operational_limits import install_proof_step_timeout_seconds
from tools.core.python_runtime_env import python_subprocess_env
from tools.core.subprocess_telemetry import run_observed_subprocess


RAW_OUTPUT_PATH = RAW_DIR / "installation_proof.json"
REPORT_OUTPUT_PATH = REPORTS_DIR / "installation_proof.md"
INSTALLATION_CONTRACT_PATH = ROOT / "config" / "installation_preflight_contract.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bounded_output_excerpt(output: str, *, limit: int = 4000) -> str:
    if limit <= 0:
        return ""
    if len(output) <= limit:
        return output
    marker = "\n...[output truncated; preserving head and tail]...\n"
    if limit <= len(marker):
        return output[-limit:]
    remaining = max(0, limit - len(marker))
    head_length = remaining // 2
    tail_length = remaining - head_length
    return (
        output[:head_length]
        + marker
        + output[-tail_length:]
    )


def _safe_env() -> dict[str, str]:
    keep = {
        "PATH",
        "SYSTEMROOT",
        "COMSPEC",
        "TEMP",
        "TMP",
        "PATHEXT",
        "PYTHONPATH",
        "APPDATA",
        "LOCALAPPDATA",
        "USERPROFILE",
        "HOME",
        "HOMEPATH",
        "HOMEDRIVE",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
    }
    env = {key: value for key, value in os.environ.items() if key in keep or key.startswith("CODEMAPS_")}
    return python_subprocess_env(env, code_maps_dir=ROOT, vendor_paths=[])


def _step(step_id: str, label: str, command: list[str], *, timeout: int) -> dict[str, Any]:
    result, duration = run_observed_subprocess(
        command,
        cwd=ROOT,
        env=_safe_env(),
        label=step_id,
        timeout=timeout,
        log=lambda message: print(f"[install-proof] {message}", flush=True),
    )
    output = "\n".join(part for part in [result.stdout.strip(), result.stderr.strip()] if part)
    return {
        "id": step_id,
        "label": label,
        "command": command,
        "passed": result.returncode == 0,
        "returncode": result.returncode,
        "duration_seconds": duration,
        "output_excerpt": _bounded_output_excerpt(output),
    }


def _installation_proof_authority(
    *,
    public_distribution: bool | None = None,
) -> tuple[str, frozenset[str]]:
    contract = json.loads(INSTALLATION_CONTRACT_PATH.read_text(encoding="utf-8"))
    authority_profile = resolve_installation_authority_profile(
        ROOT,
        public_distribution=public_distribution,
    )
    profile = contract.get("validation_profiles", {}).get(authority_profile)
    if not isinstance(profile, dict):
        raise ValueError(f"Unknown installation authority profile: {authority_profile}")
    omissions = frozenset(
        str(step_id).strip()
        for step_id in profile.get("installation_proof_omit_steps", [])
        if str(step_id).strip()
    )
    return authority_profile, omissions


def _commands_for_level(
    level: str,
    *,
    skip_deps: bool,
    max_doctor_seconds: int,
    target_root: str | None = None,
    projects: str | None = None,
    public_distribution: bool | None = None,
) -> list[dict[str, Any]]:
    py = sys.executable
    steps: list[dict[str, Any]] = [
        {
            "id": "installation_contract",
            "label": "Installation contract",
            "command": [py, "tools/validate_installation_contract.py"],
            "timeout": install_proof_step_timeout_seconds("installation_contract"),
        },
        {
            "id": "doctor",
            "label": "Doctor health check",
            "command": [py, "sage.py", "doctor", "--skip-release-proof"],
            "timeout": max(1, int(max_doctor_seconds or install_proof_step_timeout_seconds("doctor"))),
        },
        {
            "id": "mcp_config",
            "label": "MCP config render",
            "command": [py, "sage.py", "mcp", "--print-config"],
            "timeout": install_proof_step_timeout_seconds("mcp_config"),
        },
        {
            "id": "release_language",
            "label": "Release language hygiene",
            "command": [py, "tools/validate_release_language.py"],
            "timeout": install_proof_step_timeout_seconds("release_language"),
        },
        {
            "id": "final_consistency",
            "label": "Final consistency snapshot",
            "command": [py, "tools/validate_final_consistency.py"],
            "timeout": install_proof_step_timeout_seconds("final_consistency"),
        },
    ]
    if level in {"daily", "release"}:
        init_command = [py, "sage.py", "init"]
        init_mode = init_mode_contract(installation_proof_init_mode(level))
        public_flag = str(init_mode.get("public_flag") or "")
        if public_flag:
            init_command.append(public_flag)
        if skip_deps:
            init_command.append("--skip-deps")
        if target_root:
            init_command.extend(["--target-root", str(target_root)])
        steps.insert(
            0,
            {
                "id": "init",
                "label": "Initialize workspace",
                "command": init_command,
                "timeout": install_proof_step_timeout_seconds("init"),
            },
        )
        daily_command = [py, "sage.py", "run", "--profile", "daily"]
        if target_root:
            daily_command.extend(["--target-root", str(target_root)])
        if projects:
            daily_command.extend(["--projects", str(projects)])
        steps.append(
            {
                "id": "daily_run",
                "label": "Daily profile run",
                "command": daily_command,
                "timeout": install_proof_step_timeout_seconds("daily_run"),
            }
        )
    if level == "release":
        steps.append(
            {
                "id": "installed_distribution_surface",
                "label": "Installed distribution surface",
                "command": [
                    py,
                    "tools/validate_distribution_hardening.py",
                    "--scope",
                    "installed-package",
                ],
                "timeout": install_proof_step_timeout_seconds("installed_distribution_surface"),
            }
        )
    _, omitted_step_ids = _installation_proof_authority(
        public_distribution=public_distribution
    )
    known_step_ids = {str(step["id"]) for step in steps}
    unknown_omissions = sorted(omitted_step_ids - known_step_ids)
    if unknown_omissions:
        raise ValueError(
            f"Installation proof profile omits unknown steps: {unknown_omissions}"
        )
    return [step for step in steps if step["id"] not in omitted_step_ids]


def build_installation_proof(
    level: str,
    *,
    skip_deps: bool,
    max_doctor_seconds: int,
    target_root: str | None = None,
    projects: str | None = None,
    public_distribution: bool | None = None,
) -> dict[str, Any]:
    authority_profile, omitted_step_ids = _installation_proof_authority(
        public_distribution=public_distribution
    )
    steps = []
    for spec in _commands_for_level(
        level,
        skip_deps=skip_deps,
        max_doctor_seconds=max_doctor_seconds,
        target_root=target_root,
        projects=projects,
        public_distribution=public_distribution,
    ):
        print(f"[install-proof] START {spec['label']}", flush=True)
        row = _step(spec["id"], spec["label"], spec["command"], timeout=int(spec["timeout"]))
        print(f"[install-proof] {'PASS' if row['passed'] else 'FAIL'} {spec['label']} ({row['duration_seconds']}s)", flush=True)
        steps.append(row)
        if not row["passed"]:
            break
    status = "PASS" if steps and all(row.get("passed") for row in steps) else "FAIL"
    return {
        "meta": {
            "kind": "installation_proof",
            "version": "v1",
            "generated_at": _utc_now(),
            "generator": "tools.generate_installation_proof",
        },
        "environment": {
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "cwd": str(ROOT),
        },
        "target_scope": {
            "target_root": str(target_root) if target_root else None,
            "projects": str(projects) if projects else None,
        },
        "summary": {
            "status": status,
            "level": level,
            "steps": len(steps),
            "passed": sum(1 for row in steps if row.get("passed")),
            "skip_deps": bool(skip_deps),
            "target_governance": "NOT_EVALUATED",
            "authority_profile": authority_profile,
            "omitted_maintainer_steps": sorted(omitted_step_ids),
        },
        "claim_boundary": {
            "proves": "machine_local_installation_and_bounded_public_surface_execution",
            "does_not_prove": "target_repository_governance_pass_or_public_release_authority",
            "target_governance_is_separate": True,
        },
        "steps": steps,
        "interpretation": [
            "This proof validates that the local SAGE installation can render setup, health, MCP, target analysis, and installed-distribution evidence on this machine.",
            "The release level is the friend-machine proof candidate and validates installed-package distribution boundaries without inheriting source-checkout Git visibility requirements.",
            "Installation PASS and target-repository governance are separate results; a target PASS, FAIL, INCOMPLETE_EVIDENCE, or NOT_EVALUATED result does not retroactively change whether SAGE installed and executed correctly.",
            "A PASS here does not replace human review of the generated reports before sealing a public release.",
        ],
    }


def render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    env = payload.get("environment", {})
    target_scope = payload.get("target_scope", {})
    lines = [
        "# Installation Proof",
        "",
        f"- status: `{summary.get('status')}`",
        f"- level: `{summary.get('level')}`",
        f"- steps: `{summary.get('passed')}/{summary.get('steps')}`",
        f"- skip_deps: `{summary.get('skip_deps')}`",
        f"- platform: `{env.get('platform')}`",
        f"- python: `{env.get('python')}`",
        f"- target_root: `{target_scope.get('target_root')}`",
        f"- projects: `{target_scope.get('projects')}`",
        f"- target_governance: `{summary.get('target_governance')}`",
        f"- authority_profile: `{summary.get('authority_profile')}`",
        f"- omitted_maintainer_steps: `{', '.join(summary.get('omitted_maintainer_steps', [])) or 'none'}`",
        "",
        "## Steps",
        "",
        "| Step | Status | Duration | Command |",
        "|---|---:|---:|---|",
    ]
    for step in payload.get("steps", []):
        command = " ".join(str(part) for part in step.get("command", []))
        command = command.replace("|", "\\|")
        lines.append(
            f"| `{step.get('id')}` | {'PASS' if step.get('passed') else 'FAIL'} | `{step.get('duration_seconds')}` | `{command}` |"
        )
    lines.extend(["", "## Interpretation", ""])
    for item in payload.get("interpretation", []):
        lines.append(f"- {item}")
    failing = [row for row in payload.get("steps", []) if not row.get("passed")]
    if failing:
        lines.extend(["", "## First Failure", ""])
        first = failing[0]
        lines.extend(
            [
                f"- step: `{first.get('id')}`",
                f"- returncode: `{first.get('returncode')}`",
                "",
                "```text",
                str(first.get("output_excerpt") or "").replace("```", "'''"),
                "```",
            ]
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a machine-local Nexora SAGE installation proof report.")
    parser.add_argument("--level", choices=["smoke", "daily", "release"], default="smoke")
    parser.add_argument("--skip-deps", action="store_true", help="Pass --skip-deps to init when level is daily or release.")
    parser.add_argument("--max-doctor-seconds", type=int, default=90)
    parser.add_argument("--target-root", help="Repository root to initialize and analyze for daily/release proof levels.")
    parser.add_argument("--projects", help="Comma-separated project filter forwarded to the daily analysis step.")
    args = parser.parse_args()

    payload = build_installation_proof(
        args.level,
        skip_deps=bool(args.skip_deps),
        max_doctor_seconds=int(args.max_doctor_seconds or 90),
        target_root=args.target_root,
        projects=args.projects,
    )
    save_json_atomic(RAW_OUTPUT_PATH, payload)
    save_text_atomic(REPORTS_DIR / "installation_proof.md", render_report(payload))
    print(json.dumps(payload.get("summary", {}), ensure_ascii=False))
    return 0 if payload.get("summary", {}).get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
