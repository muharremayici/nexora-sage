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

from tools.core.config import (
    CONFIG_FILE,
    DISCOVERY_FILE,
    OVERRIDES_FILE,
    RAW_DIR,
    REPORTS_DIR,
    _target_output_slug,
    save_json_atomic,
    save_text_atomic,
)
from tools.core.init_execution_contract import init_mode_contract, installation_proof_init_mode
from tools.core.installation_authority import resolve_installation_authority_profile
from tools.core.operational_limits import install_proof_step_timeout_seconds
from tools.core.python_runtime_env import python_subprocess_env
from tools.core.subprocess_telemetry import run_observed_subprocess


RAW_OUTPUT_PATH = RAW_DIR / "installation_proof.json"
REPORT_OUTPUT_PATH = REPORTS_DIR / "installation_proof.md"
INSTALLATION_CONTRACT_PATH = ROOT / "config" / "installation_preflight_contract.json"
CLI_COMMAND_CONTRACT_PATH = ROOT / "config" / "cli_command_contract.json"


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


def _step(
    step_id: str,
    label: str,
    command: list[str],
    *,
    timeout: int,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    runtime_env = _safe_env()
    if isinstance(env, dict):
        runtime_env.update(env)
    result, duration = run_observed_subprocess(
        command,
        cwd=ROOT,
        env=runtime_env,
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


def _installation_preflight_reuse_transport(target_root: str | Path) -> dict[str, str]:
    from tools.external_target_preflight import preflight_receipt_transport

    target = Path(target_root).expanduser()
    target = (Path.cwd() / target).resolve() if not target.is_absolute() else target.resolve()
    latest_path = (
        ROOT
        / "output"
        / "external_targets"
        / _target_output_slug(str(target))
        / ".raw"
        / "external_target_preflight.json"
    )
    try:
        payload = json.loads(latest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"init did not leave a readable external target Preflight projection: {latest_path}"
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError("init external target Preflight projection is not an object")
    return preflight_receipt_transport(payload)


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


def _canonical_install_proof_command() -> str:
    contract = json.loads(CLI_COMMAND_CONTRACT_PATH.read_text(encoding="utf-8"))
    commands = contract.get("commands", []) if isinstance(contract, dict) else []
    matches = [
        row
        for row in commands
        if isinstance(row, dict)
        and row.get("id") == "install_proof"
        and row.get("canonical") is True
        and str(row.get("surface") or "").strip()
    ]
    if len(matches) != 1:
        raise ValueError(
            "CLI command contract must declare exactly one canonical install_proof surface."
        )
    return str(matches[0]["surface"]).strip()


def _runtime_configuration_prerequisite(level: str) -> dict[str, Any]:
    if level != "smoke":
        return {
            "status": "COMPOSED_BY_PLAN",
            "basis": "daily_and_release_plans_initialize_before_runtime_consumers",
            "missing_files": [],
        }

    required_paths = (DISCOVERY_FILE, OVERRIDES_FILE, CONFIG_FILE)
    missing_paths = [path for path in required_paths if not path.is_file()]
    if not missing_paths:
        return {
            "status": "SATISFIED",
            "basis": "smoke_runtime_configuration_is_present",
            "missing_files": [],
        }

    missing_files = []
    for path in missing_paths:
        try:
            missing_files.append(path.relative_to(ROOT).as_posix())
        except ValueError:
            missing_files.append(str(path))
    canonical_command = _canonical_install_proof_command()
    return {
        "status": "PREREQUISITE_REQUIRED",
        "basis": "smoke_runtime_configuration_is_missing",
        "missing_files": missing_files,
        "canonical_command": canonical_command,
        "action_message": (
            "Fresh smoke requires generated runtime configuration; no proof steps were "
            "started. Run the canonical target-aware installation proof command: "
            f"{canonical_command}"
        ),
    }


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
        if projects:
            init_command.extend(["--projects", str(projects)])
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
    step_env: dict[str, str] = {}
    preflight_reuse: dict[str, Any] = {
        "status": "NOT_APPLICABLE",
        "basis": "installation_proof_has_no_explicit_target_init",
    }
    prerequisite = _runtime_configuration_prerequisite(level)
    if prerequisite.get("status") == "PREREQUISITE_REQUIRED":
        preflight_reuse = {
            "status": "NOT_STARTED",
            "basis": "runtime_configuration_prerequisite_failed_before_step_execution",
        }
        step_specs: list[dict[str, Any]] = []
    else:
        step_specs = _commands_for_level(
            level,
            skip_deps=skip_deps,
            max_doctor_seconds=max_doctor_seconds,
            target_root=target_root,
            projects=projects,
            public_distribution=public_distribution,
        )
    for spec in step_specs:
        print(f"[install-proof] START {spec['label']}", flush=True)
        row = _step(
            spec["id"],
            spec["label"],
            spec["command"],
            timeout=int(spec["timeout"]),
            env=step_env if spec["id"] == "daily_run" else None,
        )
        print(f"[install-proof] {'PASS' if row['passed'] else 'FAIL'} {spec['label']} ({row['duration_seconds']}s)", flush=True)
        steps.append(row)
        if spec["id"] == "daily_run" and preflight_reuse.get("status") == "TRANSPORT_BOUND":
            preflight_reuse = {
                **preflight_reuse,
                "status": "VERIFIED_REUSE" if row["passed"] else "REUSE_NOT_PROVEN",
                "basis": (
                    "daily_run_accepted_fresh_receipt_and_completed"
                    if row["passed"]
                    else "daily_run_failed_before_receipt_reuse_could_be_proven"
                ),
            }
        if not row["passed"]:
            break
        if spec["id"] == "init" and target_root:
            try:
                transport = _installation_preflight_reuse_transport(target_root)
                step_env.update(
                    {
                        "CODEMAPS_TARGET_PREFLIGHT_RECEIPT": transport["path"],
                        "CODEMAPS_TARGET_PREFLIGHT_RECEIPT_SHA256": transport["sha256"],
                        "CODEMAPS_TARGET_PREFLIGHT_REUSE_MODE": "installation_proof",
                    }
                )
                if projects:
                    step_env["CODEMAPS_TARGET_PROJECTS"] = str(projects)
                preflight_reuse = {
                    "status": "TRANSPORT_BOUND",
                    "basis": "init_receipt_transport_with_deferred_target_freshness_check",
                    "source_run_id": transport["run_id"],
                    "source_sha256": transport["sha256"],
                }
            except Exception as exc:
                preflight_reuse = {
                    "status": "FAILED",
                    "basis": "init_receipt_transport_unavailable",
                    "error": f"{type(exc).__name__}:{exc}",
                }
                steps.append(
                    {
                        "id": "preflight_receipt_reuse",
                        "label": "Bind init Preflight receipt for daily run",
                        "command": [],
                        "passed": False,
                        "returncode": 2,
                        "duration_seconds": 0.0,
                        "output_excerpt": preflight_reuse["error"],
                    }
                )
                break
    status = (
        "PASS"
        if prerequisite.get("status") != "PREREQUISITE_REQUIRED"
        and steps
        and all(row.get("passed") for row in steps)
        else "FAIL"
    )
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
            "prerequisite_cause_count": (
                1 if prerequisite.get("status") == "PREREQUISITE_REQUIRED" else 0
            ),
        },
        "claim_boundary": {
            "proves": "machine_local_installation_and_bounded_public_surface_execution",
            "does_not_prove": "target_repository_governance_pass_or_public_release_authority",
            "target_governance_is_separate": True,
        },
        "preflight_reuse": preflight_reuse,
        "prerequisite": prerequisite,
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
        f"- preflight_reuse: `{payload.get('preflight_reuse', {}).get('status')}`",
        f"- prerequisite: `{payload.get('prerequisite', {}).get('status')}`",
        "",
    ]
    prerequisite = payload.get("prerequisite", {})
    if prerequisite.get("status") == "PREREQUISITE_REQUIRED":
        lines.extend(
            [
                "## Prerequisite",
                "",
                str(prerequisite.get("action_message") or ""),
                "",
                "Missing runtime files:",
                "",
                *[f"- `{path}`" for path in prerequisite.get("missing_files", [])],
                "",
            ]
        )
    lines.extend(
        [
            "## Steps",
            "",
            "| Step | Status | Duration | Command |",
            "|---|---:|---:|---|",
        ]
    )
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
    prerequisite = payload.get("prerequisite", {})
    if prerequisite.get("status") == "PREREQUISITE_REQUIRED":
        print(
            f"[install-proof] PREREQUISITE_REQUIRED {prerequisite.get('action_message')}",
            flush=True,
        )
    print(json.dumps(payload.get("summary", {}), ensure_ascii=False))
    return 0 if payload.get("summary", {}).get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
