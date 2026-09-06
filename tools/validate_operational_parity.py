from __future__ import annotations

import json
import argparse
import os
import sys
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
CODE_MAPS_DIR = TOOLS_DIR.parent
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))

from tools.core.json_io import load_json_file, load_text_file
from tools.core.config import CODE_MAPS_DIR, OUTPUT_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.heartbeat_cadence import local_duration_guidance
from tools.core.operational_limits import cli_command_timeout_seconds, cli_pipeline_refresh_timeout_seconds
from tools.core.pipeline_registry import validator_execution_contract
from tools.core.subprocess_telemetry import run_observed_subprocess
from tools.core.release_proof_steps import load_release_proof_scope_contract, load_release_proof_steps
from tools.core.analysis_snapshot_lineage import load_atlas_commit, receipt_binding
from tools.core.artifact_registry import ARTIFACT_SCHEMAS
from tools.core.artifact_validator import validate_against_schema


def _release_replay_commands(scope: dict[str, Any]) -> list[list[str]]:
    """Reuse producer commands; only forced acquisition becomes cached replay."""
    steps = {step["id"]: step for step in load_release_proof_steps()}
    live = scope["live_repository_execution"]
    governance = scope["final_governance_execution"]
    projects = live["required_project_filter"]
    if len(projects) != 1 or projects != governance["required_project_filter"]:
        raise ValueError("Parity requires one shared declared repository scope")
    commands = []
    for owner in (live, governance):
        command = list(steps[owner["step_id"]]["command"])
        if command.count("--projects") != 1 or command[command.index("--projects") + 1:][:1] != projects:
            raise ValueError("Parity producer command does not preserve declared project scope")
        if owner is live:
            if command.count("--profile") != 1 or command[command.index("--profile") + 1:][:1] != [live["required_execution_profile"]]:
                raise ValueError("Parity producer command does not preserve declared profile")
            command = [argument for argument in command if argument != "--force"]
        commands.append(command)
    return commands


def _release_snapshot(scope: dict[str, Any], stage: str) -> tuple[dict[str, Any], dict[str, Any]]:
    commit = load_atlas_commit(RAW_DIR)
    snapshot_id = str(commit.get("snapshot_id") or "")
    projects = scope["live_repository_execution"]["required_project_filter"]
    errors = []
    if not snapshot_id or commit.get("state") != "complete" or commit.get("projects") != projects:
        errors.append("missing_incomplete_or_out_of_scope_atlas_commit")
        return {"atlas_snapshot_id": snapshot_id}, {
            "stage": stage, "passed": False, "errors": errors,
            "atlas_snapshot_id": snapshot_id, "projects": projects,
        }
    paths_by_artifact = scope["operational_parity_execution"]["semantic_paths"]
    if not isinstance(paths_by_artifact, dict) or not paths_by_artifact:
        raise ValueError("Release parity semantic paths are missing")
    snapshot: dict[str, Any] = {"atlas_snapshot_id": snapshot_id}
    for artifact, paths in paths_by_artifact.items():
        payload = load_json_file(RAW_DIR / f"{artifact}.json", {})
        schema_errors = validate_against_schema(ARTIFACT_SCHEMAS[artifact], artifact, payload)
        if schema_errors:
            errors.append({"artifact": artifact, "schema_errors": schema_errors})
        binding, _observed, binding_errors = receipt_binding(
            raw_dir=RAW_DIR, artifact_id=artifact, artifact_payload=payload,
            expected_snapshot_id=snapshot_id,
        )
        if binding != "BOUND":
            errors.append({"artifact": artifact, "binding": binding, "errors": binding_errors})
        if not isinstance(paths, list) or not paths:
            raise ValueError(f"Release parity semantic fields are missing: {artifact}")
        values = {}
        for path in paths:
            value = payload
            for part in path.split("."):
                if not isinstance(value, dict) or part not in value:
                    errors.append({"artifact": artifact, "missing_field": path})
                    value = None
                    break
                value = value[part]
            values[path] = value
        snapshot[artifact] = values
    return snapshot, {"stage": stage, "passed": not errors, "errors": errors,
                      "atlas_snapshot_id": snapshot_id, "projects": projects}


def _run_release_parity(timing_guidance: dict[str, Any]) -> dict[str, Any]:
    scope = load_release_proof_scope_contract()
    commands = _release_replay_commands(scope)
    before, freshness_before = _release_snapshot(scope, "before_cached_run")
    after = None
    freshness_after = None
    replay_status = "not_run_stale_baseline"
    error = None
    if freshness_before["passed"]:
        for command in commands:
            result, _duration = run_observed_subprocess(
                command, cwd=str(CODE_MAPS_DIR), label="operational parity bounded cached replay",
                timeout=cli_pipeline_refresh_timeout_seconds(), log=print,
            )
            if result.returncode != 0:
                error = result.stderr or result.stdout or "bounded cached replay failed"
                replay_status = "failed"
                break
        else:
            replay_status = "completed"
            after, freshness_after = _release_snapshot(scope, "after_cached_run")
    diff = _diff_snapshots(before, after) if after is not None else {}
    fresh = freshness_before["passed"] and bool(freshness_after and freshness_after["passed"])
    passed = replay_status == "completed" and fresh and not diff
    return {
        "meta": {"kind": "operational_parity_validation", "version": "v1", "scope": "release_scope"},
        "summary": {
            "passed": passed, "changed_sections": len(diff),
            "semantic_parity_status": ("FAIL" if diff else "PASS") if after is not None else "NOT_RUN",
            "semantic_freshness_status": "PASS" if fresh else "FAIL",
            "cached_replay_status": replay_status,
            "claim_boundary": scope["operational_parity_execution"]["claim_boundary"],
        },
        "semantic_freshness_evidence": {"before_cached_run": freshness_before, "after_cached_run": freshness_after},
        "execution_timing_guidance": timing_guidance, "commands": commands,
        "before": before, "after": after, "diff": diff,
        **({"error": error} if error else {}),
    }


def _diff_snapshots(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    diff: dict[str, Any] = {}
    for key in sorted(set(before) | set(after)):
        if before.get(key) != after.get(key):
            diff[key] = {
                "before": before.get(key),
                "after": after.get(key),
            }
    return diff

def _semantic_snapshot() -> dict[str, Any]:
    ai_context = load_json_file(RAW_DIR / "ai_context.json", {})
    quality_gate = load_json_file(RAW_DIR / "quality_gate.json", {})
    dead_code_payload = load_json_file(RAW_DIR / "dead_code.json", {})
    dead_code = dead_code_payload.get("items", []) if isinstance(dead_code_payload, dict) else dead_code_payload
    state_flow = load_json_file(RAW_DIR / "state_flow.json", {})
    blast = load_json_file(RAW_DIR / "blast_radius.json", {})
    decision_evidence = load_json_file(RAW_DIR / "decision_evidence.json", {})
    readiness = load_json_file(RAW_DIR / "surgical_readiness.json", {})
    lifecycle_validation = load_json_file(RAW_DIR / "lifecycle_validation.json", {})
    step_validation = load_json_file(RAW_DIR / "step_isolation_validation.json", {})
    merge_script = load_text_file(OUTPUT_DIR / "scripts" / "auto_merge.ps1")

    top_decision_row = next(
        (row for row in (decision_evidence.get("studio_summary") or []) if isinstance(row, dict)),
        {},
    )
    readiness_rows = readiness.get("rows", []) if isinstance(readiness, dict) else (readiness if isinstance(readiness, list) else [])
    top_readiness_row = next((row for row in readiness_rows if isinstance(row, dict)), {})

    return {
        "ai_context": {
            "health": int((ai_context.get("health") or {}).get("overall", -1) or -1),
            "audit_total": int((ai_context.get("audit") or {}).get("total_violations", -1) or -1),
            "top_module_risk": ((ai_context.get("module_risks") or [{}])[0]).get("module"),
            "top_dead_hotspot": ((ai_context.get("dead_code") or {}).get("hotspots") or [{}])[0],
        },
        "quality_gate": {
            "passed": bool(quality_gate.get("passed")),
            "enforced_results": {
                check.get("name"): {
                    "passed": bool(check.get("passed")),
                    "actual": check.get("actual"),
                }
                for check in (quality_gate.get("checks") or [])
                if check.get("enforced", True)
            },
        },
        "dead_code": {
            "total": len(dead_code) if isinstance(dead_code, list) else -1,
            "top_files": sorted(
                (
                    ((ai_context.get("dead_code") or {}).get("hotspots") or [])
                    if isinstance(ai_context, dict)
                    else []
                ),
                key=lambda item: (-int(item.get("dead_exports", 0) or 0), item.get("file", "")),
            )[:5],
        },
        "state_flow": {
            "zustand_count": len((state_flow.get("zustand_stores") or {}).keys()) if isinstance(state_flow, dict) else -1,
        },
        "blast_radius": {
            "most_critical_node": (blast.get("metrics") or {}).get("most_critical_node"),
            "highest_impact_score": (blast.get("metrics") or {}).get("highest_impact_score"),
        },
        "decision_evidence": {
            "top_studio": top_decision_row.get("studio"),
            "top_main_files": top_decision_row.get("main_files"),
            "top_manual_review": top_decision_row.get("manual_review"),
        },
        "readiness": {
            "top_module": top_readiness_row.get("module"),
            "top_boundary_imports": sorted(((top_readiness_row.get("boundary_signals") or {}).get("member_side_effect_imports") or [])),
        },
        "merge_surface": {
            "script_length": len(merge_script),
            "has_copy_item": "Copy-Item" in merge_script,
        },
        "validation": {
            "lifecycle_passed": lifecycle_validation.get("summary", {}).get("failed_checks", -1) == 0,
            "step_isolation_passed": step_validation.get("summary", {}).get("failed_checks", -1) == 0,
        },
    }


def _run_cached_full_pipeline() -> None:
    result, _duration = run_observed_subprocess(
        [sys.executable, str(CODE_MAPS_DIR / "sage.py"), "run", "--full"],
        cwd=str(CODE_MAPS_DIR),
        label="operational parity cached full pipeline",
        timeout=cli_pipeline_refresh_timeout_seconds(),
        log=print,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or "cached full pipeline failed")


def _refresh_lifecycle_evidence(stage: str) -> dict[str, Any]:
    result, duration_seconds = run_observed_subprocess(
        [sys.executable, str(CODE_MAPS_DIR / "tools" / "validate_lifecycle.py")],
        cwd=str(CODE_MAPS_DIR),
        label=f"operational parity lifecycle validation {stage}",
        timeout=cli_command_timeout_seconds(),
        log=print,
    )
    payload = load_json_file(RAW_DIR / "lifecycle_validation.json", {})
    summary = payload.get("summary", {}) if isinstance(payload, dict) and isinstance(payload.get("summary"), dict) else {}
    failed_checks = summary.get("failed_checks", -1)
    passed = result.returncode == 0 and isinstance(failed_checks, int) and failed_checks == 0
    return {
        "stage": stage,
        "passed": passed,
        "returncode": result.returncode,
        "duration_seconds": round(duration_seconds, 3),
        "total_checks": summary.get("total_checks"),
        "failed_checks": summary.get("failed_checks"),
    }


def run_validation(*, release_scope: bool = False) -> dict[str, Any]:
    execution_contract = validator_execution_contract("validate_operational_parity")
    timing_guidance_id = str(execution_contract.get("local_duration_guidance_id") or "").strip()
    if not timing_guidance_id:
        raise ValueError("validate_operational_parity is missing local_duration_guidance_id.")
    timing_guidance = local_duration_guidance(timing_guidance_id)
    if os.getenv("CODEMAPS_INSIDE_PIPELINE"):
        return {
            "meta": {"kind": "operational_parity_validation", "version": "v1"},
            "summary": {"passed": False},
            "execution_timing_guidance": timing_guidance,
            "error": "Cannot run operational parity inside a pipeline (recursion guard).",
        }
    if release_scope:
        return _run_release_parity(timing_guidance)
    lifecycle_before = _refresh_lifecycle_evidence("before_cached_run")
    before = _semantic_snapshot()
    if not lifecycle_before["passed"]:
        return {
            "meta": {"kind": "operational_parity_validation", "version": "v1"},
            "summary": {
                "passed": False,
                "changed_sections": 0,
                "semantic_parity_status": "NOT_RUN_STALE_BASELINE",
                "semantic_freshness_status": "FAIL",
                "cached_replay_status": "not_run_stale_baseline",
                "claim_boundary": "PASS requires lifecycle-validated semantic freshness before cached replay; stale baselines fail closed without consuming a broad replay.",
            },
            "semantic_freshness_evidence": {"before_cached_run": lifecycle_before},
            "execution_timing_guidance": timing_guidance,
            "before": before,
            "after": None,
            "diff": {},
        }
    _run_cached_full_pipeline()
    lifecycle_after = _refresh_lifecycle_evidence("after_cached_run")
    after = _semantic_snapshot()
    diff = _diff_snapshots(before, after)
    semantic_parity_status = "PASS" if not diff else "FAIL"
    semantic_freshness_status = "PASS" if lifecycle_before["passed"] and lifecycle_after["passed"] else "FAIL"

    payload = {
        "meta": {
            "kind": "operational_parity_validation",
            "version": "v1",
        },
        "summary": {
            "passed": semantic_parity_status == "PASS" and semantic_freshness_status == "PASS",
            "changed_sections": len(diff),
            "semantic_parity_status": semantic_parity_status,
            "semantic_freshness_status": semantic_freshness_status,
            "cached_replay_status": "completed",
            "claim_boundary": "PASS requires both stable cached replay and lifecycle-validated semantic freshness before and after that replay.",
        },
        "semantic_freshness_evidence": {
            "before_cached_run": lifecycle_before,
            "after_cached_run": lifecycle_after,
        },
        "execution_timing_guidance": timing_guidance,
        "before": before,
        "after": after,
        "diff": diff,
    }
    return payload


def _write_report(payload: dict[str, Any]) -> None:
    summary = payload.get("summary", {}) if isinstance(payload, dict) else {}
    diff = payload.get("diff", {}) if isinstance(payload, dict) else {}
    lines = [
        "# Operational Parity Validation",
        "",
        "Compares semantic snapshots before and after the declared cached replay scope.",
        "",
        f"- passed: `{summary.get('passed')}`",
        f"- changed_sections: `{summary.get('changed_sections', 0)}`",
        f"- semantic_parity_status: `{summary.get('semantic_parity_status')}`",
        f"- semantic_freshness_status: `{summary.get('semantic_freshness_status')}`",
        f"- cached_replay_status: `{summary.get('cached_replay_status')}`",
        f"- local_timing_guidance: `{(payload.get('execution_timing_guidance') or {}).get('status', 'unknown')}`",
        f"- claim_boundary: {summary.get('claim_boundary')}",
        "",
        "| Section | Result |",
        "|---|---|",
    ]
    if isinstance(diff, dict) and diff:
        for key in sorted(diff):
            lines.append(f"| `{key}` | CHANGED |")
    else:
        lines.append(f"| `semantic_snapshot` | {summary.get('semantic_parity_status', 'NOT_RUN')} |")
    if payload.get("error"):
        lines.extend(["", f"- error: `{payload.get('error')}`"])
    save_text_atomic(REPORTS_DIR / "operational_parity_validation.md", "\n".join(lines) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate cached operational parity.")
    parser.add_argument("--release-scope", action="store_true", help="Use the central bounded release scope and snapshot lineage.")
    args = parser.parse_args()
    payload = run_validation(release_scope=args.release_scope)
    out_path = RAW_DIR / "operational_parity_validation.json"
    save_json_atomic(out_path, payload)
    _write_report(payload)
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
