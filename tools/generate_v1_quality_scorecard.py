from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

CODE_MAPS_DIR = Path(__file__).resolve().parent.parent
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))

from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.evidence_status import normalize_evidence_status
from tools.core.json_io import load_json_file


RAW_PATH = RAW_DIR / "v1_quality_scorecard.json"
REPORT_PATH = REPORTS_DIR / "v1_quality_scorecard.md"


def _summary(name: str) -> dict[str, Any]:
    payload = load_json_file(RAW_DIR / f"{name}.json", {})
    return payload.get("summary", {}) if isinstance(payload, dict) else {}


def _status_from_summary(summary: dict[str, Any]) -> str:
    return str(normalize_evidence_status({"summary": summary}).get("status") or "UNKNOWN")


def _release_summary() -> dict[str, Any]:
    return _summary("release_proof_bundle")


def _performance_action() -> dict[str, Any]:
    perf = load_json_file(RAW_DIR / "performance_budget_validation.json", {})
    bottlenecks = load_json_file(RAW_DIR / "performance_bottlenecks.json", {})
    perf_summary = perf.get("summary", {}) if isinstance(perf, dict) else {}
    metrics = perf.get("metrics", {}) if isinstance(perf, dict) else {}
    recommendations = bottlenecks.get("recommendations", []) if isinstance(bottlenecks, dict) else []
    failed = normalize_evidence_status(perf).get("passed") is not True
    return {
        "id": "performance_budget_release_deep",
        "priority": "P0" if failed else "P2",
        "category": "performance",
        "blocking_for_v1_seal": failed,
        "problem": (
            "Full/release-deep or forced rebuild pipeline budget is above the current hard-fail threshold."
            if failed
            else "Performance budget is currently green; remaining work is bottleneck reduction, not v1 seal blocking."
        ),
        "evidence": {
            "performance_budget_status": _status_from_summary(perf_summary),
            "pipeline_total_seconds": metrics.get("pipeline_total_seconds"),
            "latest_release_deep_total_seconds": metrics.get("latest_release_deep_total_seconds"),
            "latest_forced_full_total_seconds": metrics.get("latest_forced_full_total_seconds"),
            "effective_budget_seconds": (perf.get("budgets", {}) or {}).get("full_pipeline_total_seconds_effective") if isinstance(perf, dict) else None,
            "release_deep_effective_budget_seconds": (perf.get("budgets", {}) or {}).get("release_deep_pipeline_total_seconds_effective") if isinstance(perf, dict) else None,
            "forced_full_effective_budget_seconds": (perf.get("budgets", {}) or {}).get("forced_full_pipeline_total_seconds_effective") if isinstance(perf, dict) else None,
            "performance_evidence_status": metrics.get("performance_evidence_status"),
            "release_deep_budget_evaluated": metrics.get("release_deep_budget_evaluated"),
            "forced_full_budget_evaluated": metrics.get("forced_full_budget_evaluated"),
        },
        "next_actions": [
            "Keep daily/watchdog profiles slim and preserve cached release-deep speed as a separate green signal.",
            "Treat forced/cache-bypass full rebuild as the remaining P0 performance risk only when the forced budget check fails.",
            "Promote cache-by-Atlas-snapshot for React Support Matrix, Keyword Scanner/Stats and Gem Scorer.",
            "Benchmark profile-aware project-level Atlas workers separately before changing the default worker policy.",
            "Re-run performance budget after profile/cache changes and require a PASS or explicit human-sealed exception before v1.0.0 sealing.",
        ],
        "recommendations": recommendations[:8],
    }


def _categories() -> list[dict[str, Any]]:
    release = _release_summary()
    perf = _summary("performance_budget_validation")
    clean = _summary("clean_distribution_validation")
    categories = [
        {
            "id": "architecture_spine",
            "label": "Architecture spine",
            "score": 8.8,
            "target_score": 9.2,
            "status": "strong",
            "evidence": ["pipeline_execution_contract_validation", "pipeline_layer_chain_integrity_validation", "modular_doctrine_registry_validation"],
            "raise_to_target": ["Keep pipeline registry as the single source for step IO and invocation contracts.", "Continue removing duplicate CLI/engine entry paths when discovered."],
        },
        {
            "id": "ssot_sqlite_artifact_store",
            "label": "SSOT / SQLite / artifact discipline",
            "score": 8.7,
            "target_score": 9.1,
            "status": "strong",
            "evidence": ["sqlite_artifact_parity_validation", "sqlite_proxy_coverage_validation", "artifact_contract_validation"],
            "raise_to_target": ["Keep raw JSON as shadow export only.", "Add new generated artifacts to artifact contracts before they enter release proof."],
        },
        {
            "id": "react_v1_claim",
            "label": "React V1 claim",
            "score": 8.6,
            "target_score": 9.0,
            "status": "strong",
            "evidence": ["react_universal_ready", "react_fixture_family_taxonomy_report", "react_analysis_chain_integrity_validation"],
            "raise_to_target": ["Keep the public claim static and evidence-backed.", "Promote only real external-repo edge cases into fixture families."],
        },
        {
            "id": "agent_surface",
            "label": "Target-repository agent surface",
            "score": 8.4,
            "target_score": 9.0,
            "status": "strong_but_high_risk",
            "evidence": ["mcp_agent_surface_validation", "agent_context_payload_validation", "agent_packet_chain_integrity_validation", "agent_semantic_contract_smoke"],
            "raise_to_target": ["Perform manual agent-eye review of primary MCP packets on MAIN and one external React repo.", "Keep SAGE-internal provenance out of default target-repo directives."],
        },
        {
            "id": "release_evaluation",
            "label": "Release proof / evaluation system",
            "score": 9.0,
            "target_score": 9.3,
            "status": "excellent",
            "evidence": ["release_proof_bundle", "claim_guard_validation", "release_readiness"],
            "raise_to_target": ["Treat eval suites as release IP.", "Preserve required vs advisory proof-step separation without hiding advisory red signals."],
        },
        {
            "id": "self_governance",
            "label": "Self-governance",
            "score": 8.5,
            "target_score": 9.0,
            "status": "strong",
            "evidence": ["self_architecture_hygiene_validation", "runtime_honesty_validation", "exception_honesty_validation"],
            "raise_to_target": ["Expand self-hygiene checks when new developer-facing surfaces are added.", "Keep SAGE built the way it governs."],
        },
        {
            "id": "installation_distribution",
            "label": "Installation / clean distribution",
            "score": 8.0,
            "target_score": 9.0,
            "status": "good_needs_external_proof",
            "evidence": ["installation_contract_validation", "installation_proof", "clean_distribution_validation"],
            "raise_to_target": ["Capture final friend-machine clean install transcript after v1 source freeze.", "Keep clean mirror validation green after every release-facing patch."],
        },
        {
            "id": "performance",
            "label": "Performance and profile discipline",
            "score": 6.8,
            "target_score": 8.5,
            "status": "weakest_link",
            "evidence": ["performance_budget_validation", "performance_bottlenecks", "performance_ledger"],
            "raise_to_target": _performance_action()["next_actions"],
        },
        {
            "id": "polyglot_future",
            "label": "Polyglot future substrate",
            "score": 7.8,
            "target_score": 8.6,
            "status": "solid_foundation_not_equal_depth",
            "evidence": ["language_agnostic_symbol_validation", "polyglot_capability_validation", "external_polyglot_smoke_validation"],
            "raise_to_target": ["Keep claim guard explicit: React is nanometric v1, other languages are substrate/structural unless proven otherwise.", "Add future language depth through registry-backed plugins, not core hardcoding."],
        },
        {
            "id": "security_honesty",
            "label": "Security / honesty / telemetry",
            "score": 8.0,
            "target_score": 8.8,
            "status": "good_scoped",
            "evidence": ["security_boundary_validation", "runtime_honesty_validation", "honesty_telemetry"],
            "raise_to_target": ["Keep security claims scoped to governance and agent safety, not full SAST.", "Prefer adapters for CodeQL/Semgrep/Snyk in v1.5+ instead of reimplementing them."],
        },
        {
            "id": "sustainability",
            "label": "Sustainability / anti-spaghetti",
            "score": 7.9,
            "target_score": 8.8,
            "status": "good_needs_continued_pressure",
            "evidence": ["source_layer_inventory", "layer_release_matrix", "self_architecture_hygiene_validation"],
            "raise_to_target": ["Continue splitting knowledge into doctrine/principle/capability packs.", "Watch for duplicate validators and command surfaces as the proof system grows."],
        },
        {
            "id": "developer_experience",
            "label": "Developer experience",
            "score": 7.7,
            "target_score": 8.8,
            "status": "good_but_sharp_edges",
            "evidence": ["entrypoint_failure_validation", "installation_proof", "cli_command_contract_validation"],
            "raise_to_target": ["Keep help flags non-destructive and cheap.", "Make official commands obvious: init, doctor, run profiles, release proof, clean sync."],
        },
    ]
    if _status_from_summary(perf) == "FAIL":
        categories = [
            {**item, "status": "weakest_link" if item["id"] == "performance" else item["status"]}
            for item in categories
        ]
    elif _status_from_summary(perf) == "PASS":
        categories = [
            {
                **item,
                "score": max(item["score"], 8.5),
                "status": "within_budget_watch_bottlenecks",
            }
            if item["id"] == "performance"
            else item
            for item in categories
        ]
    if _status_from_summary(clean) == "PASS":
        categories = [
            {**item, "score": max(item["score"], 8.1) if item["id"] == "installation_distribution" else item["score"]}
            for item in categories
        ]
    if release.get("status") == "PASS":
        categories = [
            {**item, "score": max(item["score"], 9.0) if item["id"] == "release_evaluation" else item["score"]}
            for item in categories
        ]
    return categories


def build_scorecard() -> dict[str, Any]:
    categories = _categories()
    weighted = sum(float(item["score"]) for item in categories) / max(1, len(categories))
    p0_actions = [_performance_action()]
    p1_actions = [
        {
            "id": "final_ssot_artifact_sweep",
            "priority": "P1",
            "category": "ssot_sqlite_artifact_store",
            "blocking_for_v1_seal": False,
            "problem": "SQLite/Atlas is the declared SSOT, so remaining source re-scans and stale artifact reads must stay bounded, logged and justified.",
            "current_score": next(item["score"] for item in categories if item["id"] == "ssot_sqlite_artifact_store"),
            "target_score": next(item["target_score"] for item in categories if item["id"] == "ssot_sqlite_artifact_store"),
            "next_actions": [
                "Run one final source-contract sweep for direct output/.raw filesystem reads and unbounded target-repo scans.",
                "Keep intentional shadow JSON fallbacks explicit in honesty telemetry.",
                "Require new artifacts to enter artifact_contract_validation before release proof accepts them.",
            ],
        },
        {
            "id": "manual_agent_surface_review",
            "priority": "P1",
            "category": "agent_surface",
            "blocking_for_v1_seal": False,
            "problem": "Mechanical tests pass, but target-repository agent packets need human/agent-eye sampling before final seal.",
            "current_score": next(item["score"] for item in categories if item["id"] == "agent_surface"),
            "target_score": next(item["target_score"] for item in categories if item["id"] == "agent_surface"),
            "next_actions": [
                "Sample primary MCP packets for one MAIN violation/work item and one external React corpus target.",
                "Check that each packet answers: target files, exact rule, evidence span, required action and validation command.",
                "Remove or hide SAGE-internal implementation terms from default target-repo directives.",
            ],
        },
        {
            "id": "friend_machine_clean_install",
            "priority": "P1",
            "category": "installation_distribution",
            "blocking_for_v1_seal": True,
            "problem": "Local clean mirror is green, but external clean-machine proof is still the strongest final distribution evidence.",
            "current_score": next(item["score"] for item in categories if item["id"] == "installation_distribution"),
            "target_score": next(item["target_score"] for item in categories if item["id"] == "installation_distribution"),
            "next_actions": [
                "After source freeze, collect doctor, daily run, release-check and install-proof transcript from another machine.",
            ],
        },
    ]
    p2_actions = [
        {
            "id": "developer_surface_simplification",
            "priority": "P2",
            "category": "developer_experience",
            "blocking_for_v1_seal": False,
            "problem": "SAGE has many entrypoints and proof commands; the official v1 path must stay obvious even while legacy wrappers exist.",
            "current_score": next(item["score"] for item in categories if item["id"] == "developer_experience"),
            "target_score": next(item["target_score"] for item in categories if item["id"] == "developer_experience"),
            "next_actions": [
                "Keep help commands cheap and non-destructive across public scripts.",
                "Document one canonical install -> doctor -> daily/full -> release proof path after source freeze.",
                "Track duplicate or confusing command surfaces as sustainability debt rather than hiding them.",
            ],
        },
        {
            "id": "polyglot_registry_guardrail",
            "priority": "P2",
            "category": "polyglot_future",
            "blocking_for_v1_seal": False,
            "problem": "The substrate is registry-backed, but v1 must not imply equal nanometric depth outside React/TypeScript.",
            "current_score": next(item["score"] for item in categories if item["id"] == "polyglot_future"),
            "target_score": next(item["target_score"] for item in categories if item["id"] == "polyglot_future"),
            "next_actions": [
                "Keep language/framework capability depth explicit in capability registry and public claims.",
                "Add future language depth through plugins and contracts, not one-off core hardcoding.",
                "Use external polyglot smokes as substrate evidence only until semantic engines exist.",
            ],
        },
        {
            "id": "sustainability_anti_spaghetti_sweep",
            "priority": "P2",
            "category": "sustainability",
            "blocking_for_v1_seal": False,
            "problem": "The proof system is growing; duplicate validators, overlapping commands and pack boundaries must not turn SAGE into the kind of spaghetti it prevents.",
            "current_score": next(item["score"] for item in categories if item["id"] == "sustainability"),
            "target_score": next(item["target_score"] for item in categories if item["id"] == "sustainability"),
            "next_actions": [
                "Preserve the separation: doctrine packs bind, principle packs guide, capability packs activate, runtime doctrine executes.",
                "Prefer small contract validators over broad ad-hoc scripts when adding new proof surfaces.",
                "Keep self-architecture hygiene in the required release proof path.",
            ],
        },
    ]
    return {
        "meta": {"kind": "v1_quality_scorecard", "version": "v1"},
        "summary": {
            "overall_score": round(weighted, 2),
            "target_overall_score_before_seal": 8.8,
            "release_proof_status": _release_summary().get("status"),
            "weakest_category": min(categories, key=lambda item: item["score"])["id"],
            "blocking_actions": [item["id"] for item in p0_actions + p1_actions if item.get("blocking_for_v1_seal")],
        },
        "categories": categories,
        "actions": p0_actions + p1_actions + p2_actions,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# SAGE V1 Quality Scorecard",
        "",
        f"- overall_score: `{payload['summary']['overall_score']}`",
        f"- target_overall_score_before_seal: `{payload['summary']['target_overall_score_before_seal']}`",
        f"- release_proof_status: `{payload['summary']['release_proof_status']}`",
        f"- weakest_category: `{payload['summary']['weakest_category']}`",
        f"- blocking_actions: `{payload['summary']['blocking_actions']}`",
        "",
        "## Category Scores",
        "",
        "| Category | Score | Target | Status | Evidence |",
        "|---|---:|---:|---|---|",
    ]
    for item in payload["categories"]:
        evidence = ", ".join(f"`{name}`" for name in item.get("evidence", []))
        lines.append(f"| {item['label']} | {item['score']} | {item['target_score']} | `{item['status']}` | {evidence} |")
    lines.extend(["", "## V1 Seal Actions", ""])
    for action in payload["actions"]:
        lines.append(f"### {action['priority']} `{action['id']}`")
        lines.append("")
        lines.append(f"- category: `{action['category']}`")
        lines.append(f"- blocking_for_v1_seal: `{str(action.get('blocking_for_v1_seal')).lower()}`")
        if "current_score" in action or "target_score" in action:
            lines.append(f"- score_path: `{action.get('current_score', '')}` -> `{action.get('target_score', '')}`")
        lines.append(f"- problem: {action['problem']}")
        if action.get("evidence"):
            lines.append("- evidence:")
            for key, value in action.get("evidence", {}).items():
                lines.append(f"  - {key}: `{value}`")
        lines.append("- next_actions:")
        for step in action.get("next_actions", []):
            lines.append(f"  - {step}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    payload = build_scorecard()
    save_json_atomic(RAW_PATH, payload)
    save_text_atomic(REPORT_PATH, render_markdown(payload) + "\n")
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
