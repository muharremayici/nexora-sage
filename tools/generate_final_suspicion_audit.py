#!/usr/bin/env python3
"""Generate the final skeptical V1 release audit from existing evidence."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.config import CONFIG_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.evidence_status import normalize_evidence_status
from tools.core.json_io import load_json_file
from tools.core.release_completion import completion_gaps


CONTRACT_PATH = CONFIG_DIR / "final_suspicion_audit_contract.json"
RAW_PATH = RAW_DIR / "final_suspicion_audit.json"
REPORT_PATH = REPORTS_DIR / "final_suspicion_audit.md"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _artifact(name: str) -> dict[str, Any]:
    payload = load_json_file(RAW_DIR / f"{name}.json", {})
    return payload if isinstance(payload, dict) else {}


def _summary(name: str) -> dict[str, Any]:
    payload = _artifact(name)
    summary = payload.get("summary", {})
    return summary if isinstance(summary, dict) else {}


def _status(name: str) -> str:
    return str(normalize_evidence_status(_artifact(name)).get("status") or "UNKNOWN")


def _missing_required_inputs(contract: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for name in contract.get("required_inputs", []):
        if isinstance(name, str) and not (RAW_DIR / f"{name}.json").exists():
            missing.append(name)
    return missing


def _failing_required_validations(contract: dict[str, Any]) -> list[dict[str, Any]]:
    failing: list[dict[str, Any]] = []
    for name in contract.get("required_inputs", []):
        if not isinstance(name, str) or not name.endswith("_validation"):
            continue
        status = _status(name)
        if status != "PASS":
            failing.append({"artifact": name, "status": status})
    return failing


def _domain_rows(contract: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for domain in contract.get("audit_domains", []):
        if not isinstance(domain, dict):
            continue
        artifacts = [str(item) for item in domain.get("evidence_artifacts", []) if isinstance(item, str)]
        artifact_statuses = {
            artifact: {
                "exists": (RAW_DIR / f"{artifact}.json").exists(),
                "status": _status(artifact),
            }
            for artifact in artifacts
        }
        evidence_ready = all(row["exists"] for row in artifact_statuses.values())
        rows.append(
            {
                "id": str(domain.get("id") or "unknown"),
                "manual_eye_required": bool(domain.get("manual_eye_required")),
                "failure_family_sweep_required": bool(domain.get("failure_family_sweep_required")),
                "evidence_ready": evidence_ready,
                "artifact_statuses": artifact_statuses,
            }
        )
    return rows


def _negative_probe_summary() -> dict[str, Any]:
    review = _artifact("agent_surface_quality_review")
    summary = review.get("summary", {}) if isinstance(review.get("summary"), dict) else {}
    probes = summary.get("negative_probe_samples", [])
    if not isinstance(probes, list):
        probes = []
    return {
        "status": summary.get("status", "UNKNOWN"),
        "intentional_negative_probe_count": len(probes),
        "intentional_negative_probe_ids": [
            str(row.get("id") or row.get("name") or "")
            for row in probes
            if isinstance(row, dict)
        ],
        "manual_eye_required": True,
    }


def build_payload() -> dict[str, Any]:
    contract = load_json_file(CONTRACT_PATH, {})
    seal = _artifact("release_seal_remaining_actions")
    seal_summary = seal.get("summary", {}) if isinstance(seal.get("summary"), dict) else {}
    health_summary = _summary("system_health_check")
    manual_summary = _summary("agent_surface_manual_seal_pack")
    missing = _missing_required_inputs(contract)
    failing_validations = _failing_required_validations(contract)
    incomplete_requirements = completion_gaps(contract, RAW_DIR)
    domains = _domain_rows(contract)

    machine_blocking: list[dict[str, Any]] = []
    if missing:
        machine_blocking.append(
            {
                "id": "missing_required_evidence",
                "priority": "P0",
                "owner": "SAGE",
                "evidence": {"missing_required_inputs": missing},
                "next_step": "Generate the missing required evidence artifact before claiming final audit readiness.",
            }
        )
    if failing_validations:
        machine_blocking.append(
            {
                "id": "failing_required_validation",
                "priority": "P0",
                "owner": "SAGE",
                "evidence": {"failing_required_validations": failing_validations},
                "next_step": "Fix failing required validations before claiming final audit readiness.",
            }
        )
    machine_blocking.extend(incomplete_requirements)

    public_blockers = [
        row
        for row in seal.get("actions", [])
        if isinstance(row, dict) and row.get("blocking_for_public_release") is True
    ]
    machine_blocking_actions = [
        row
        for row in seal.get("actions", [])
        if isinstance(row, dict) and row.get("blocking_for_machine_readiness") is True
    ]
    manual_domains = [row["id"] for row in domains if row.get("manual_eye_required")]
    advisory_actions = [
        row
        for row in seal.get("actions", [])
        if isinstance(row, dict)
        and row.get("blocking_for_machine_readiness") is False
        and row.get("blocking_for_public_release") is False
    ]

    public_release_ready = bool(seal_summary.get("public_release_ready"))
    status = "BLOCKED" if machine_blocking else "READY_FOR_FINAL_MANUAL_AUDIT"
    if not machine_blocking and public_release_ready:
        status = "PUBLIC_RELEASE_READY_PENDING_HUMAN_CONFIRMATION"

    return {
        "meta": {
            "kind": "final_suspicion_audit",
            "version": "1.0.0",
            "generated_at": _utc_now(),
            "contract": CONTRACT_PATH.relative_to(ROOT).as_posix(),
        },
        "summary": {
            "status": status,
            "release_version": seal_summary.get("release_version") or health_summary.get("release_version"),
            "machine_verdict": seal_summary.get("machine_verdict") or health_summary.get("machine_verdict"),
            "system_health_score": health_summary.get("overall_score"),
            "agent_surface_human_seal_status": manual_summary.get("effective_human_seal_status") or manual_summary.get("human_seal_status"),
            "agent_surface_historical_human_seal_status": manual_summary.get("human_seal_status"),
            "agent_surface_effective_human_seal_status": manual_summary.get("effective_human_seal_status"),
            "agent_surface_seal_currentness_status": manual_summary.get("seal_currentness_status"),
            "final_seal_state": seal_summary.get("final_seal_state"),
            "machine_blocking_findings": len(machine_blocking),
            "machine_blocking_actions": len(machine_blocking_actions),
            "public_release_ready": public_release_ready,
            "public_release_blockers": len(public_blockers),
            "advisory_findings": len(advisory_actions),
            "manual_eye_required_domains": len(manual_domains),
            "automated_pass_is_not_human_seal": True,
            "completion_requirements_satisfied": not incomplete_requirements,
        },
        "machine_blocking_findings": machine_blocking,
        "machine_blocking_actions": machine_blocking_actions,
        "human_public_release_blockers": public_blockers,
        "advisory_findings": advisory_actions,
        "manual_audit_required_domains": manual_domains,
        "audit_domains": domains,
        "negative_probe_review": _negative_probe_summary(),
        "manual_review_prompts": contract.get("manual_review_prompts", []),
        "source_evidence": {
            "contract": CONTRACT_PATH.relative_to(ROOT).as_posix(),
            "required_inputs": contract.get("required_inputs", []),
            "release_seal_remaining_actions": "output/.raw/release_seal_remaining_actions.json",
            "product_boundary_map": "output/.raw/product_boundary_map.json",
            "system_health_check": "output/.raw/system_health_check.json",
            "agent_surface_manual_seal_pack": "output/.raw/agent_surface_manual_seal_pack.json",
            "manual_adversarial_audit_progress_validation": "output/.raw/manual_adversarial_audit_progress_validation.json",
        },
    }


def _render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# Final Suspicion Audit",
        "",
        "This audit is a skeptical closure report. It does not replace human review.",
        "",
        f"- status: `{summary.get('status')}`",
        f"- release_version: `{summary.get('release_version')}`",
        f"- machine_verdict: `{summary.get('machine_verdict')}`",
        f"- system_health_score: `{summary.get('system_health_score')}`",
        f"- agent_surface_human_seal_status: `{summary.get('agent_surface_human_seal_status')}`",
        f"- agent_surface_historical_human_seal_status: `{summary.get('agent_surface_historical_human_seal_status')}`",
        f"- agent_surface_effective_human_seal_status: `{summary.get('agent_surface_effective_human_seal_status')}`",
        f"- agent_surface_seal_currentness_status: `{summary.get('agent_surface_seal_currentness_status')}`",
        f"- final_seal_state: `{summary.get('final_seal_state')}`",
        f"- machine_blocking_findings: `{summary.get('machine_blocking_findings')}`",
        f"- machine_blocking_actions: `{summary.get('machine_blocking_actions')}`",
        f"- public_release_ready: `{summary.get('public_release_ready')}`",
        f"- public_release_blockers: `{summary.get('public_release_blockers')}`",
        f"- advisory_findings: `{summary.get('advisory_findings')}`",
        f"- manual_eye_required_domains: `{summary.get('manual_eye_required_domains')}`",
        f"- automated_pass_is_not_human_seal: `{summary.get('automated_pass_is_not_human_seal')}`",
        "",
        "## Machine Blocking Findings",
    ]
    blocking = payload.get("machine_blocking_findings", [])
    if blocking:
        for row in blocking:
            lines.append(f"- `{row.get('id')}`: {row.get('next_step')}")
    else:
        lines.append("- None.")

    lines.extend(["", "## Machine Blocking Actions"])
    machine_actions = payload.get("machine_blocking_actions", [])
    if machine_actions:
        for row in machine_actions:
            lines.append(f"- `{row.get('id')}` ({row.get('priority')}): {row.get('title')}")
    else:
        lines.append("- None.")

    lines.extend(["", "## Human/Public Release Blockers"])
    public_blockers = payload.get("human_public_release_blockers", [])
    if public_blockers:
        for row in public_blockers:
            lines.append(f"- `{row.get('id')}` ({row.get('priority')}): {row.get('title')}")
    else:
        lines.append("- None.")

    lines.extend(["", "## Manual Audit Required Domains"])
    for domain in payload.get("manual_audit_required_domains", []):
        lines.append(f"- `{domain}`")

    lines.extend(["", "## Domain Evidence"])
    lines.append("| Domain | Evidence Ready | Manual Eye | Failure-Family Sweep |")
    lines.append("|---|---:|---:|---:|")
    for row in payload.get("audit_domains", []):
        lines.append(
            f"| `{row.get('id')}` | `{row.get('evidence_ready')}` | "
            f"`{row.get('manual_eye_required')}` | `{row.get('failure_family_sweep_required')}` |"
        )

    lines.extend(["", "## Manual Review Prompts"])
    for prompt in payload.get("manual_review_prompts", []):
        lines.append(f"- {prompt}")
    return "\n".join(lines) + "\n"


def main() -> int:
    payload = build_payload()
    save_json_atomic(RAW_PATH, payload)
    save_text_atomic(REPORT_PATH, _render_report(payload))
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["machine_blocking_findings"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
