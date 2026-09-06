from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CODE_MAPS_DIR = Path(__file__).resolve().parent.parent
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))

from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.agent_surface_seal_contract import derive_seal_currentness, is_effective_human_seal
from tools.core.evidence_status import normalize_evidence_status
from tools.core.json_io import load_json_file
from tools.core.release_proof_steps import load_release_proof_steps


RAW_PATH = RAW_DIR / "system_health_check.json"
REPORT_PATH = REPORTS_DIR / "system_health_check.md"
LEGACY_RAW_PATH = RAW_DIR / "v1_system_health_check.json"
LEGACY_REPORT_PATH = REPORTS_DIR / "v1_system_health_check.md"


def _artifact(name: str) -> dict[str, Any]:
    payload = load_json_file(RAW_DIR / f"{name}.json", {})
    return payload if isinstance(payload, dict) else {}


def _summary(name: str) -> dict[str, Any]:
    payload = _artifact(name)
    summary = payload.get("summary", {})
    return summary if isinstance(summary, dict) else {}


def _status(name: str) -> str:
    payload = _artifact(name)
    summary = payload.get("summary", {}) if isinstance(payload, dict) else {}
    explicit = ""
    if isinstance(payload, dict):
        explicit = str(payload.get("status") or "")
    if not explicit and isinstance(summary, dict):
        explicit = str(summary.get("status") or "")
    explicit = explicit.upper()
    if explicit == "PASS":
        return str(normalize_evidence_status(payload).get("status") or "UNKNOWN")
    if explicit in {"FAIL", "ATTENTION", "UNKNOWN"}:
        return explicit
    if explicit == "HUMAN_SEALED":
        return "PASS"
    if explicit in {"READY_FOR_HUMAN_REVIEW", "READY_FOR_HUMAN_SEAL"}:
        return "ATTENTION"
    if explicit in {"BLOCKED", "ERROR"}:
        return "FAIL"
    if name == "source_layer_inventory" and isinstance(summary, dict):
        return "PASS" if int(summary.get("unknown_or_review", 1) or 0) == 0 else "ATTENTION"
    if name == "performance_ledger" and isinstance(payload.get("runs"), list):
        return "PASS" if payload.get("runs") else "UNKNOWN"
    return str(normalize_evidence_status(payload).get("status") or "UNKNOWN")


def _pass_bool(name: str) -> bool:
    return _status(name) == "PASS"


def _latest_release_proof_summary() -> dict[str, Any]:
    full_bundle = RAW_DIR / "release_proof_bundle.json"
    if full_bundle.exists():
        candidates = [full_bundle]
    else:
        candidates = sorted(RAW_DIR.glob("release_proof_bundle*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in candidates:
        payload = load_json_file(path, {})
        summary = payload.get("summary", {}) if isinstance(payload, dict) else {}
        if isinstance(summary, dict) and summary:
            return {
                **summary,
                "source": path.name,
                "context_role": "advisory_prior_run",
                "authoritative_for_current_health": False,
            }
    return {
        "status": "UNKNOWN",
        "source": None,
        "context_role": "advisory_prior_run",
        "authoritative_for_current_health": False,
    }


def _release_proof_step_required(step_id: str, default: bool = True) -> bool:
    try:
        for step in load_release_proof_steps():
            if step.get("id") == step_id:
                return bool(step.get("required"))
    except (FileNotFoundError, ValueError):
        return default
    return default


def _check(name: str, artifact: str, required: bool = True) -> dict[str, Any]:
    payload = _artifact(artifact)
    summary = payload.get("summary", {}) if isinstance(payload, dict) else {}
    status = _status(artifact)
    return {
        "name": name,
        "artifact": f"output/.raw/{artifact}.json",
        "required": required,
        "status": status,
        "exists": bool(payload),
        "summary": summary if isinstance(summary, dict) else {},
    }


def _seal_impact_check() -> dict[str, Any]:
    payload = _artifact("seal_impact_validation")
    summary = payload.get("summary", {}) if isinstance(payload, dict) else {}
    machine_blocking = bool(summary.get("blocking_for_machine_readiness"))
    proof_or_review_attention = summary.get("seal_impact_status") in {"human_review_required", "proof_refresh_required"}
    if not payload:
        status = "UNKNOWN"
    elif machine_blocking:
        status = "FAIL"
    else:
        status = "PASS"
    return {
        "name": "seal_impact_validation",
        "artifact": "output/.raw/seal_impact_validation.json",
        "required": True,
        "status": status,
        "attention": bool(proof_or_review_attention),
        "exists": bool(payload),
        "summary": summary if isinstance(summary, dict) else {},
    }


def _category(id_: str, label: str, checks: list[dict[str, Any]], weight: int = 1) -> dict[str, Any]:
    required = [item for item in checks if item.get("required")]
    required_passed = sum(1 for item in required if item.get("status") == "PASS")
    required_total = len(required)
    advisory = [item for item in checks if not item.get("required")]
    advisory_passed = sum(1 for item in advisory if item.get("status") == "PASS")
    if required_total and required_passed == required_total:
        status = "PASS"
    elif required_passed:
        status = "ATTENTION"
    else:
        status = "BLOCKED"
    required_score = (required_passed / max(1, required_total)) * 100
    advisory_score = (advisory_passed / max(1, len(advisory))) * 100 if advisory else 100
    score = round(required_score * 0.85 + advisory_score * 0.15)
    return {
        "id": id_,
        "label": label,
        "status": status,
        "score": score,
        "weight": weight,
        "required_passed": required_passed,
        "required_total": required_total,
        "advisory_passed": advisory_passed,
        "advisory_total": len(advisory),
        "checks": checks,
    }


def _attention_reason(check: dict[str, Any], check_summary: dict[str, Any]) -> str:
    name = str(check.get("name") or "")
    if name == "latest_release_proof":
        failed = check_summary.get("failed_required_steps")
        source = check_summary.get("source") or check.get("artifact")
        if isinstance(failed, list) and failed:
            return f"Full release proof is not sealed yet; failed_required_steps={failed}; source={source}."
        if check.get("exists") is False:
            return "Full release proof artifact is missing."
    if name == "seal_impact_validation":
        status = check_summary.get("seal_impact_status") or check_summary.get("status")
        action = check_summary.get("human_seal_action")
        if status or action:
            return f"Seal impact requires attention; seal_impact_status={status}; human_seal_action={action}."
    return (
        check_summary.get("status_reason")
        or check_summary.get("human_seal_status")
        or check_summary.get("budget_status")
        or "No reason provided."
    )


def _technical_debt() -> dict[str, Any]:
    quality = _artifact("quality_gate")
    dead = _artifact("dead_code")
    circular = _artifact("circular_deps")
    audit = _artifact("audit")
    return {
        "quality_gate": quality.get("summary", {}) if isinstance(quality.get("summary"), dict) else {},
        "dead_code": dead.get("summary", {}) if isinstance(dead.get("summary"), dict) else {},
        "circular_deps": circular.get("summary", {}) if isinstance(circular.get("summary"), dict) else {},
        "audit": audit.get("summary", {}) if isinstance(audit.get("summary"), dict) else {},
    }


def _performance() -> dict[str, Any]:
    budget = _artifact("performance_budget_validation")
    bottlenecks = _artifact("performance_bottlenecks")
    ledger = _artifact("performance_ledger")
    runs = ledger.get("runs", []) if isinstance(ledger.get("runs"), list) else []
    return {
        "budget_status": _status("performance_budget_validation"),
        "budget_summary": budget.get("summary", {}) if isinstance(budget.get("summary"), dict) else {},
        "metrics": budget.get("metrics", {}) if isinstance(budget.get("metrics"), dict) else {},
        "bottlenecks": bottlenecks.get("summary", {}) if isinstance(bottlenecks.get("summary"), dict) else {},
        "latest_ledger_run": runs[-1] if runs else {},
    }


def _human_seal() -> dict[str, Any]:
    manual_pack = _artifact("agent_surface_manual_seal_pack")
    summary = manual_pack.get("summary", {}) if isinstance(manual_pack.get("summary"), dict) else {}
    seal_impact = _summary("seal_impact_validation")
    currentness = derive_seal_currentness(seal_impact)
    historical_status = summary.get("human_seal_status") or summary.get("status")
    effective_status = summary.get("effective_human_seal_status") or historical_status
    human_authority_current = currentness["human_seal_authority_current"]
    return {
        "agent_surface_manual_seal_status": historical_status,
        "agent_surface_manual_pack_status": summary.get("status"),
        "historical_human_seal_status": historical_status,
        "effective_human_seal_status": effective_status,
        **currentness,
        "human_seal_satisfied": is_effective_human_seal(effective_status) and human_authority_current,
        "human_seal_ledger_entry": summary.get("human_seal_ledger_entry"),
        "human_seal_request_id": summary.get("human_seal_request_id"),
        "summary": summary,
        "rule": "The current release can be READY_FOR_HUMAN_SEAL from machine evidence, but SEALED requires explicit Progressive HITL approval.",
    }


def build_health_check() -> dict[str, Any]:
    release_summary = _latest_release_proof_summary()
    human_seal = _human_seal()
    categories = [
        _category(
            "release_governance",
            "Release governance and claim guard",
            [
                _check("release_readiness", "release_readiness"),
                _check("claim_guard", "claim_guard_validation"),
                _check("release_identity", "release_identity_validation"),
                _seal_impact_check(),
                _check("latest_release_proof", "release_proof_bundle", required=False),
            ],
            weight=3,
        ),
        _category(
            "react_v1_claim",
            "React v1 evidence envelope",
            [
                _check("react_universal_readiness", "react_universal_readiness"),
                _check("react_fixture_matrix", "react_fixture_matrix_validation"),
                _check("react_edge_cases", "react_edge_case_validation"),
                _check("react_analysis_chain", "react_analysis_chain_integrity_validation"),
            ],
            weight=3,
        ),
        _category(
            "agent_surface",
            "Target-repository agent surface",
            [
                _check("mcp_agent_surface", "mcp_agent_surface_validation"),
                _check("mcp_command_matrix", "mcp_surface_command_matrix_validation"),
                _check("agent_context_payloads", "agent_context_payload_validation"),
                _check("agent_semantic_smoke", "agent_semantic_contract_smoke"),
                _check("agent_surface_manual_seal_pack", "agent_surface_manual_seal_pack", required=False),
            ],
            weight=3,
        ),
        _category(
            "ssot_sqlite",
            "SQLite-first and artifact SSOT",
            [
                _check("sqlite_parity", "sqlite_artifact_parity_validation"),
                _check("sqlite_proxy_coverage", "sqlite_proxy_coverage_validation"),
                _check("atlas_sqlite_first", "atlas_sqlite_first_access_validation"),
                _check("genome_sqlite_first", "genome_sqlite_first_access_validation"),
                _check("fractal_sqlite_first", "fractal_sqlite_first_access_validation"),
                _check("large_artifact_sqlite_first", "large_artifact_sqlite_first_access_validation"),
            ],
            weight=2,
        ),
        _category(
            "honesty_telemetry",
            "Runtime honesty, exception honesty and telemetry",
            [
                _check("exception_honesty", "exception_honesty_validation"),
                _check("runtime_honesty", "runtime_honesty_validation"),
                _check("honesty_telemetry", "honesty_telemetry", required=False),
                _check("telemetry_traces", "telemetry_traces", required=False),
            ],
            weight=2,
        ),
        _category(
            "lineage_layers",
            "Lineage, layer map and self-map",
            [
                _check("data_lineage", "data_lineage_contract_validation"),
                _check("doctrine_lineage", "doctrine_rule_lineage_contract_validation"),
                _check("target_repo_lineage", "target_repo_lineage_contract_validation"),
                _check("source_layer_inventory", "source_layer_inventory"),
                _check("layer_release_matrix", "layer_release_matrix"),
                _check("sage_self_map", "sage_self_map"),
            ],
            weight=2,
        ),
        _category(
            "source_governance_decisions",
            "Source governance and central decision contracts",
            [
                _check("source_contracts", "source_contract_validation"),
                _check("governance_registry", "governance_registry_validation"),
                _check("cli_command_contract", "cli_command_contract_validation"),
                _check("hardcoded_decision_inventory", "hardcoded_decision_inventory_validation"),
                _check("sage_work_item_registry", "sage_work_item_registry_validation"),
                _check("audit_lesson_registry", "audit_lesson_registry_validation"),
                _check("manual_adversarial_audit_checklist", "manual_adversarial_audit_checklist_validation"),
                _check("manual_adversarial_audit_progress", "manual_adversarial_audit_progress_validation"),
                _check("pipeline_layer_chain_integrity", "pipeline_layer_chain_integrity_validation"),
            ],
            weight=2,
        ),
        _category(
            "installation_distribution",
            "Installation and clean distribution",
            [
                _check("installation_contract", "installation_contract_validation"),
                _check("installation_proof", "installation_proof"),
                _check("distribution_hardening", "distribution_hardening_validation"),
                _check("entrypoint_failure_drills", "entrypoint_failure_validation"),
            ],
            weight=2,
        ),
        _category(
            "performance",
            "Performance and workload profile",
            [
                _check(
                    "performance_budget",
                    "performance_budget_validation",
                    required=_release_proof_step_required("performance_budget"),
                ),
                _check("performance_profile_integrity", "performance_profile_integrity_validation"),
                _check("performance_ledger", "performance_ledger", required=False),
                _check("performance_bottlenecks", "performance_bottlenecks", required=False),
            ],
            weight=1,
        ),
    ]
    blocking = [item["id"] for item in categories if item["status"] != "PASS"]
    weighted_total = sum(item["score"] * item["weight"] for item in categories)
    weight_sum = sum(item["weight"] for item in categories)
    machine_verdict = "READY_FOR_HUMAN_SEAL" if not blocking else "BLOCKED"
    payload = {
        "meta": {
            "kind": "system_health_check",
            "version": "v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "generator": "tools.generate_system_health_check",
        },
        "summary": {
            "status": "PASS" if not blocking else "FAIL",
            "machine_verdict": machine_verdict,
            "overall_score": round(weighted_total / max(1, weight_sum), 2),
            "blocking_categories": blocking,
            "release_ready": release_summary.get("release_ready"),
            "release_readiness_artifact_ready": release_summary.get("release_readiness_artifact_ready"),
            "full_release_proof_ready": release_summary.get("full_release_proof_ready"),
            "prior_release_proof_status": release_summary.get("status"),
            "prior_release_proof_source": release_summary.get("source"),
            "release_proof_context_role": release_summary.get("context_role"),
            "release_proof_authoritative_for_current_health": release_summary.get(
                "authoritative_for_current_health"
            ),
            "allowed_release_claim": release_summary.get("allowed_release_claim"),
            "human_seal_required": True,
            "human_seal_satisfied": human_seal.get("human_seal_satisfied") is True,
        },
        "categories": categories,
        "release_proof": release_summary,
        "technical_debt": _technical_debt(),
        "performance": _performance(),
        "human_seal": human_seal,
        "next_actions": [
            "If machine_verdict is BLOCKED, fix blocking_categories before claiming current release readiness.",
            "If machine_verdict is READY_FOR_HUMAN_SEAL and human_seal_satisfied is false, perform Progressive HITL review before SEALED.",
            "Keep advisory performance and telemetry signals visible even when they do not block the release gate.",
        ],
    }
    return payload


def render_markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "# SAGE System Health Check",
        "",
        f"- status: `{summary['status']}`",
        f"- machine_verdict: `{summary['machine_verdict']}`",
        f"- overall_score: `{summary['overall_score']}`",
        f"- release_ready: `{summary.get('release_ready')}` (full proof compatibility alias)",
        f"- release_readiness_artifact_ready: `{summary.get('release_readiness_artifact_ready')}`",
        f"- full_release_proof_ready: `{summary.get('full_release_proof_ready')}`",
        f"- prior_release_proof_status: `{summary.get('prior_release_proof_status')}`",
        f"- prior_release_proof_source: `{summary.get('prior_release_proof_source')}`",
        f"- release_proof_context_role: `{summary.get('release_proof_context_role')}`",
        "- release_proof_authoritative_for_current_health: "
        f"`{str(summary.get('release_proof_authoritative_for_current_health')).lower()}`",
        f"- allowed_release_claim: `{summary.get('allowed_release_claim')}`",
        f"- human_seal_required: `{str(summary['human_seal_required']).lower()}`",
        f"- human_seal_satisfied: `{str(summary.get('human_seal_satisfied')).lower()}`",
        "",
        "## Scope Boundary",
        "",
        "- This is SAGE's own release health check, not a target repository health verdict.",
        "- Target-repository artifacts are used here only as evidence that SAGE can produce bounded, grounded, agent-facing governance surfaces.",
        "- A target repository release/health verdict must be emitted by a separate target-repo proof bundle.",
        "",
        "## Categories",
        "",
        "| Category | Status | Score | Required | Advisory |",
        "|---|---|---:|---:|---:|",
    ]
    for item in payload["categories"]:
        lines.append(
            f"| {item['label']} | `{item['status']}` | {item['score']} | "
            f"{item['required_passed']}/{item['required_total']} | {item['advisory_passed']}/{item['advisory_total']} |"
        )
    lines.extend(["", "## Blocking Categories", ""])
    if payload["summary"]["blocking_categories"]:
        for item in payload["summary"]["blocking_categories"]:
            lines.append(f"- `{item}`")
    else:
        lines.append("- None.")
    attention_items: list[tuple[str, dict[str, Any]]] = []
    for category in payload.get("categories", []):
        label = str(category.get("label") or category.get("id") or "unknown")
        for check in category.get("checks", []):
            if isinstance(check, dict) and check.get("status") != "PASS":
                attention_items.append((label, check))
    lines.extend(["", "## Advisory Attention Items", ""])
    if attention_items:
        lines.extend(["| Category | Check | Required | Status | Reason |", "|---|---|---:|---|---|"])
        for label, check in attention_items:
            check_summary = check.get("summary") if isinstance(check.get("summary"), dict) else {}
            reason = _attention_reason(check, check_summary)
            origins = check_summary.get("occurrences_by_origin")
            if isinstance(origins, dict) and origins:
                origin_text = ", ".join(f"{key}={value}" for key, value in sorted(origins.items()))
                reason = f"{reason} Origin split: {origin_text}."
            if check.get("name") == "honesty_telemetry":
                honesty_review = _artifact("runtime_honesty_review")
                review_classes = honesty_review.get("review_class_counts") if isinstance(honesty_review, dict) else {}
                if isinstance(review_classes, dict) and review_classes:
                    class_text = ", ".join(f"{key}={value}" for key, value in sorted(review_classes.items()))
                    reason = f"{reason} Review classes: {class_text}."
            lines.append(
                f"| {label} | `{check.get('name')}` | `{str(bool(check.get('required'))).lower()}` | "
                f"`{check.get('status')}` | {reason} |"
            )
    else:
        lines.append("- None.")
    lines.extend(["", "## Performance Snapshot", ""])
    perf = payload.get("performance", {})
    lines.append(f"- budget_status: `{perf.get('budget_status')}`")
    metrics = perf.get("metrics", {}) if isinstance(perf.get("metrics"), dict) else {}
    for key in ("pipeline_total_seconds", "latest_release_deep_total_seconds", "cached_pipeline_total_seconds", "performance_evidence_status"):
        if key in metrics:
            lines.append(f"- {key}: `{metrics.get(key)}`")
    latest = perf.get("latest_ledger_run", {}) if isinstance(perf.get("latest_ledger_run"), dict) else {}
    if latest:
        lines.append(f"- latest_ledger_run: `{latest.get('run_id')}` budget `{latest.get('budget_status')}` total `{latest.get('pipeline_total_seconds')}`")
    lines.extend(["", "## Human Seal", ""])
    seal = payload.get("human_seal", {})
    lines.append(f"- agent_surface_manual_seal_status: `{seal.get('agent_surface_manual_seal_status')}`")
    lines.append(f"- agent_surface_manual_pack_status: `{seal.get('agent_surface_manual_pack_status')}`")
    lines.append(f"- historical_human_seal_status: `{seal.get('historical_human_seal_status')}`")
    lines.append(f"- effective_human_seal_status: `{seal.get('effective_human_seal_status')}`")
    lines.append(f"- seal_current: `{str(seal.get('seal_current')).lower()}`")
    lines.append(f"- human_seal_satisfied: `{str(seal.get('human_seal_satisfied')).lower()}`")
    lines.append(f"- human_seal_ledger_entry: `{seal.get('human_seal_ledger_entry')}`")
    lines.append(f"- human_seal_request_id: `{seal.get('human_seal_request_id')}`")
    lines.append(f"- rule: {seal.get('rule')}")
    lines.extend(["", "## Next Actions", ""])
    for action in payload.get("next_actions", []):
        lines.append(f"- {action}")
    return "\n".join(lines) + "\n"


def main() -> int:
    payload = build_health_check()
    save_json_atomic(RAW_PATH, payload)
    save_text_atomic(REPORT_PATH, render_markdown(payload))
    save_json_atomic(LEGACY_RAW_PATH, payload)
    save_text_atomic(LEGACY_REPORT_PATH, render_markdown(payload))
    print(json.dumps(payload["summary"], ensure_ascii=False))
    # Generation success is separate from the health verdict. A BLOCKED health
    # report is valid evidence; release sealing is blocked by the remaining
    # actions gate, not by pretending the report failed to generate.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
