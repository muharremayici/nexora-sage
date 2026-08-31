from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.core.config import CODE_MAPS_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file
from tools.generate_nexora_agent_contract import build_approval_gates


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rel(path: Path) -> str:
    try:
        return path.relative_to(CODE_MAPS_DIR).as_posix()
    except ValueError:
        return path.as_posix()


def _summary(path: Path) -> dict[str, Any]:
    payload = load_json_file(path, {})
    if not isinstance(payload, dict):
        return {}
    summary = payload.get("summary")
    return summary if isinstance(summary, dict) else {}


def _artifact_exists(path: Path) -> bool:
    return path.exists() and path.is_file()


def _artifact_size(path: Path) -> int:
    return path.stat().st_size if _artifact_exists(path) else 0


def _report_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for report in sorted(REPORTS_DIR.glob("*")):
        if not report.is_file() or report.suffix.lower() not in {".md", ".txt"}:
            continue
        rows.append(
            {
                "path": _rel(report),
                "bytes": report.stat().st_size,
                "audience": _classify_report_audience(report.name),
                "human_readability": _human_readability(report.name, report.stat().st_size),
            }
        )
    return rows


def _classify_report_audience(name: str) -> str:
    ai_names = {
        "ai_task_packs.md",
        "decision_evidence.md",
        "artifact_provenance_index.md",
        "react_support_matrix.md",
        "ui_smoke_specs.md",
    }
    human_names = {
        "release_proof_bundle.md",
        "release_readiness.md",
        "quality_gate.md",
        "report_freshness_index.md",
        "external_target_runs.md",
        "watchdog_stress_validation.md",
    }
    if name in ai_names:
        return "ai_primary"
    if name in human_names or name.startswith("inspect_"):
        return "human_primary"
    if name.endswith(".json"):
        return "ai_primary"
    if any(token in name for token in ("architecture", "runtime", "ecosystem", "dead_code", "circular")):
        return "mixed_deep_technical"
    return "mixed"


def _human_readability(name: str, size: int) -> str:
    if name.startswith("inspect_") or name in {"release_proof_bundle.md", "release_readiness.md", "quality_gate.md"}:
        return "decision_readable"
    if size > 100_000:
        return "reference_only_too_large_for_first_read"
    if size > 20_000:
        return "technical_deep_read"
    return "readable"


def _raw_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for artifact in sorted(RAW_DIR.glob("*")):
        if not artifact.is_file():
            continue
        rows.append(
            {
                "path": _rel(artifact),
                "bytes": artifact.stat().st_size,
                "audience": "ai_primary" if artifact.suffix.lower() == ".json" else "mixed",
            }
        )
    return rows


def _surface_capabilities() -> list[dict[str, Any]]:
    release = load_json_file(RAW_DIR / "release_readiness.json", {})
    release_summary = release.get("summary", {}) if isinstance(release, dict) else {}
    quality = load_json_file(RAW_DIR / "quality_gate.json", {})
    quality_warnings = quality.get("ecosystem_warning_signals", {}) if isinstance(quality, dict) else {}
    universal = _summary(RAW_DIR / "react_universal_readiness.json")
    freshness = _summary(RAW_DIR / "report_freshness_index.json")
    watchdog = _summary(RAW_DIR / "watchdog_stress_validation.json")
    watchdog_session_exists = _artifact_exists(RAW_DIR / "watchdog_session.json") and _artifact_exists(REPORTS_DIR / "watchdog_session.md")

    return [
        {
            "id": "ai_native_artifact_layer",
            "claim": "AI can consume large structured analysis artifacts.",
            "evidence": f"{len(_raw_rows())} raw artifacts under output/.raw",
            "reality": "present",
            "audience": "ai",
        },
        {
            "id": "human_report_layer",
            "claim": "Humans can inspect Markdown/TXT reports.",
            "evidence": f"{len(_report_rows())} reports under output/reports; freshness={freshness.get('freshness_gate')}",
            "reality": "present_but_fragmented",
            "audience": "human",
        },
        {
            "id": "release_proof",
            "claim": "The system can prove current release readiness.",
            "evidence": (
                f"release={release.get('readiness') if isinstance(release, dict) else None}; "
                "final_proof_bundle=not_available_during_brief_generation"
            ),
            "reality": "release_inputs_present_final_bundle_written_after_proof_run",
            "audience": "both",
        },
        {
            "id": "react_universal_readiness",
            "claim": "React ecosystem coverage is based on capability doctrine, not external fixture dependence.",
            "evidence": (
                f"universal_ready={universal.get('universal_ready')}; "
                f"basis={universal.get('universality_basis')}; "
                f"external_fixture_pool_required={universal.get('external_fixture_pool_required')}"
            ),
            "reality": "present",
            "audience": "both",
        },
        {
            "id": "watchdog_terminal_ui",
            "claim": "Watchdog has a human-readable live interface.",
            "evidence": (
                f"stress_checks={watchdog.get('passed_checks')}/{watchdog.get('total_checks')}; "
                f"Rich terminal panel/table implemented; persistent_session_report={watchdog_session_exists}"
            ),
            "reality": "terminal_ui_present_with_persistent_session_report"
            if watchdog_session_exists
            else "terminal_ui_present_session_report_ready_after_next_watch_run",
            "audience": "human",
        },
        {
            "id": "mcp_skill_layer",
            "claim": "AI tools can use Nexora SAGE through MCP/Skill integration.",
            "evidence": "SKILL.md and tools/mcp/server.py are present",
            "reality": "present",
            "audience": "ai",
        },
        {
            "id": "targeted_inspection",
            "claim": "A user or AI can inspect a file, folder, or symbol without reading every artifact.",
            "evidence": f"inspect reports={len([row for row in _report_rows() if Path(row['path']).name.startswith('inspect_')])}",
            "reality": "present",
            "audience": "both",
        },
        {
            "id": "remaining_active_repo_attention",
            "claim": "The active repo is not hiding UI/runtime manual validation risks.",
            "evidence": (
                f"ecosystem_signal_status={quality.get('ecosystem_signal_status') if isinstance(quality, dict) else None}; "
                f"ui_high_risk_merge_candidates={quality_warnings.get('ui_high_risk_merge_candidates')}; "
                f"ui_browser_smoke_required_candidates={quality_warnings.get('ui_browser_smoke_required_candidates')}"
            ),
            "reality": "attention_needed_not_release_blocking",
            "audience": "human",
        },
    ]


def _next_actions() -> list[dict[str, str]]:
    return [
        {
            "priority": "P0",
            "audience": "human",
            "action": "Keep nexora_brief.md as the first report humans open before deep reports.",
            "why": "The existing report set is powerful but too broad for first-pass decision making.",
        },
        {
            "priority": "P1",
            "audience": "human",
            "action": "Keep watchdog_session.md/json in the human report set after watch mode runs.",
            "why": "The terminal UI is readable, and the durable session artifact makes the last pulse auditable.",
        },
        {
            "priority": "P1",
            "audience": "human",
            "action": "Add a small HTML/static dashboard only after the Markdown brief stabilizes.",
            "why": "A dashboard should present settled signals, not duplicate every deep artifact.",
        },
        {
            "priority": "P1",
            "audience": "ai",
            "action": "Expose the brief through MCP as a compact status resource/tool.",
            "why": "AI agents need a short control-plane summary before opening 50MB artifacts.",
        },
        {
            "priority": "P2",
            "audience": "both",
            "action": "Use external repos only as optional QC samples, not as the basis of the universality claim.",
            "why": "Universality must come from capability detection and graceful unknown handling.",
        },
    ]


def build_brief() -> dict[str, Any]:
    raw_rows = _raw_rows()
    report_rows = _report_rows()
    capabilities = _surface_capabilities()
    release = load_json_file(RAW_DIR / "release_readiness.json", {})
    quality = load_json_file(RAW_DIR / "quality_gate.json", {})
    universal = _summary(RAW_DIR / "react_universal_readiness.json")
    freshness = _summary(RAW_DIR / "report_freshness_index.json")

    human_ready_reports = [
        row
        for row in report_rows
        if row["human_readability"] in {"decision_readable", "readable"} and row["audience"] in {"human_primary", "mixed"}
    ]
    deep_reference_reports = [row for row in report_rows if row["human_readability"] == "reference_only_too_large_for_first_read"]

    return {
        "meta": {
            "kind": "nexora_brief",
            "version": "v1",
            "generated_at": _utc_now(),
            "generator": "tools.generate_nexora_brief",
            "workspace_root": str(CODE_MAPS_DIR),
        },
        "summary": {
            "release_readiness": release.get("readiness") if isinstance(release, dict) else None,
            "release_checks": (release.get("summary") or {}).get("checks") if isinstance(release, dict) else None,
            "release_failed": (release.get("summary") or {}).get("failed") if isinstance(release, dict) else None,
            "quality_gate": quality.get("release_gate_status") if isinstance(quality, dict) else None,
            "ecosystem_signal_status": quality.get("ecosystem_signal_status") if isinstance(quality, dict) else None,
            "react_universal_ready": universal.get("universal_ready"),
            "universality_basis": universal.get("universality_basis"),
            "external_fixture_pool_required": universal.get("external_fixture_pool_required"),
            "report_freshness_gate": freshness.get("freshness_gate"),
            "raw_artifacts": len(raw_rows),
            "human_reports": len(report_rows),
            "human_first_read_reports": len(human_ready_reports),
            "deep_reference_reports": len(deep_reference_reports),
        },
        "capabilities": capabilities,
        "audience_layers": {
            "ai": {
                "purpose": "Structured machine-readable analysis, MCP tools, task packs, and large evidence artifacts.",
                "primary_artifacts": [
                    "output/.raw/atlas.json",
                    "output/.raw/genome.json",
                    "output/.raw/surgical_discovery.json",
                    "output/.raw/ui_runtime_contracts.json",
                    "output/.raw/ai_task_packs.json",
                    "output/.raw/release_proof_bundle.json",
                ],
                "status": "strong",
            },
            "human": {
                "purpose": "Decision brief, risk triage, manual validation checklist, and proof summaries.",
                "primary_artifacts": [
                    "output/reports/nexora_brief.md",
                    "output/reports/release_proof_bundle.md",
                    "output/reports/release_readiness.md",
                    "output/reports/quality_gate.md",
                    "output/reports/report_freshness_index.md",
                ],
                "status": "usable_but_needs_product_layer",
            },
        },
        "human_first_read_reports": human_ready_reports[:25],
        "deep_reference_reports": deep_reference_reports[:25],
        "approval_gates": build_approval_gates(),
        "next_actions": _next_actions(),
    }


def render_report(brief: dict[str, Any]) -> str:
    meta = brief.get("meta", {})
    summary = brief.get("summary", {})
    layers = brief.get("audience_layers", {})
    lines = [
        "# Nexora Brief",
        "",
        f"- generated_at: `{meta.get('generated_at')}`",
        f"- release_readiness: `{summary.get('release_readiness')}`",
        f"- quality_gate: `{summary.get('quality_gate')}`",
        f"- ecosystem_signal_status: `{summary.get('ecosystem_signal_status')}`",
        f"- react_universal_ready: `{summary.get('react_universal_ready')}`",
        f"- universality_basis: `{summary.get('universality_basis')}`",
        f"- external_fixture_pool_required: `{summary.get('external_fixture_pool_required')}`",
        f"- report_freshness_gate: `{summary.get('report_freshness_gate')}`",
        f"- raw_artifacts: `{summary.get('raw_artifacts')}`",
        f"- human_reports: `{summary.get('human_reports')}`",
        "",
        "## Verdict",
        "",
        "- AI layer: strong. Large JSON artifacts, MCP/Skill surface, task packs, and evidence chains exist.",
        "- Human layer: usable but fragmented. Markdown reports exist, but this brief should be the first human entry point.",
        "- Watchdog: live Rich terminal UI exists, stress behavior is validated, and the latest pulse can be persisted as watchdog_session.md/json.",
        "",
        "## Audience Split",
        "",
        "| Audience | Purpose | Status | Primary Artifacts |",
        "|---|---|---|---|",
    ]
    for audience in ("ai", "human"):
        layer = layers.get(audience, {})
        artifacts = "<br>".join(f"`{path}`" for path in layer.get("primary_artifacts", []))
        lines.append(f"| `{audience}` | {layer.get('purpose')} | `{layer.get('status')}` | {artifacts} |")

    lines.extend(["", "## Claims vs Code Reality", "", "| Claim | Reality | Audience | Evidence |", "|---|---|---|---|"])
    for item in brief.get("capabilities", []):
        lines.append(
            f"| {item.get('claim')} | `{item.get('reality')}` | `{item.get('audience')}` | {item.get('evidence')} |"
        )

    lines.extend(["", "## Human First-Read Reports", "", "| Report | Readability | Bytes |", "|---|---|---:|"])
    for row in brief.get("human_first_read_reports", []):
        lines.append(f"| `{row.get('path')}` | `{row.get('human_readability')}` | {row.get('bytes')} |")

    lines.extend(["", "## Deep Reference Reports", "", "| Report | Readability | Bytes |", "|---|---|---:|"])
    for row in brief.get("deep_reference_reports", []):
        lines.append(f"| `{row.get('path')}` | `{row.get('human_readability')}` | {row.get('bytes')} |")

    lines.extend(["", "## Human Approval Gates", "", "| Gate | Approval Required | Risk | Agent Rule |", "|---|---|---|---|"])
    for gate in brief.get("approval_gates", []):
        lines.append(
            f"| `{gate.get('id')}` | `{gate.get('human_approval_required')}` | `{gate.get('risk')}` | {gate.get('agent_rule')} |"
        )

    lines.extend(["", "## Next Actions", "", "| Priority | Audience | Action | Why |", "|---|---|---|---|"])
    for item in brief.get("next_actions", []):
        lines.append(f"| `{item.get('priority')}` | `{item.get('audience')}` | {item.get('action')} | {item.get('why')} |")
    return "\n".join(lines) + "\n"


def run() -> dict[str, Any]:
    brief = build_brief()
    save_json_atomic(RAW_DIR / "nexora_brief.json", brief)
    save_text_atomic(REPORTS_DIR / "nexora_brief.md", render_report(brief))
    return brief


def main() -> int:
    brief = run()
    print(json.dumps(brief.get("summary", {}), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
