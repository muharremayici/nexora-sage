#!/usr/bin/env python3
"""Validate the generated MCP per-command surface matrix."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.generate_mcp_surface_command_matrix import build_matrix, render_report as render_matrix_report


TARGET_AGENT_RINGS = {"H1_TARGET_REPO_AGENT", "H1_HITL_GOVERNANCE", "H1_MUTATION_GATE", "H1_HEAVY_VALIDATION_GATE"}
INTERNAL_RINGS = {"H2_SAGE_INTERNAL", "H2_HITL_AUDIT", "H2_OPERATOR_MUTATION_GATE"}


def _row_checks(row: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    tool = str(row.get("tool") or "")
    ring = str(row.get("surface_ring") or "")
    role = str(row.get("role") or "")
    family = str(row.get("surface_family") or "")
    obligations = set(row.get("review_obligations") or [])

    if family in {"", "unclassified"}:
        issues.append("tool_missing_surface_family")
    if role == "debug_provenance" and family != "sage_internal_or_readiness":
        issues.append("debug_provenance_tool_masked_as_target_repo_family")
    if ring in TARGET_AGENT_RINGS and "no_sage_internal_noise" not in obligations and role in {"primary_agent", "supporting_context"}:
        issues.append("target_agent_tool_missing_internal_noise_obligation")
    if ring in INTERNAL_RINGS and row.get("default_target_agent_context") is True:
        issues.append("internal_tool_marked_default_target_context")
    if role == "primary_agent" and "manual_agent_surface_sample" not in obligations:
        issues.append("primary_agent_missing_manual_sample_obligation")
    if "manual_agent_surface_sample" in obligations:
        quality = row.get("quality_review") if isinstance(row.get("quality_review"), dict) else {}
        if not quality.get("sample_aliases"):
            issues.append("manual_agent_surface_sample_alias_missing")
    if row.get("mutates") is True and row.get("safe_generated_artifact_write") is not True:
        if not row.get("requires_approval") and role != "mutating" and not str(ring).startswith("H2_"):
            issues.append("unsafe_mutating_tool_without_gate")
    if tool in {"inspect_file", "inspect_symbol", "get_surgical_operation_packet", "get_violation_work_queue"}:
        if "source_grounding_or_explicit_missing_status" not in obligations:
            issues.append("source_grounding_obligation_missing")
    if tool in {"get_impact_radius", "get_blast_radius", "simulate_change_impact"}:
        if "bounded_depth_or_omission_notice" not in obligations:
            issues.append("impact_tool_missing_bounded_depth_obligation")
    if tool == "validate_patch" and "fail_closed_on_full_replacement_or_missing_target" not in obligations:
        issues.append("patch_validation_missing_fail_closed_obligation")
    return issues


def build_validation() -> dict[str, Any]:
    matrix = build_matrix()
    rows = matrix.get("commands", []) if isinstance(matrix, dict) else []
    row_issues = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        issues = list(row.get("issues") or [])
        issues.extend(_row_checks(row))
        if issues:
            row_issues.append({"tool": row.get("tool"), "issues": sorted(set(issues))})
    summary = matrix.get("summary", {}) if isinstance(matrix, dict) else {}
    missing = summary.get("missing_role_rows") or []
    stale = summary.get("stale_role_rows") or []
    status = "PASS" if not missing and not stale and not row_issues else "FAIL"
    return {
        "meta": {
            "kind": "mcp_surface_command_matrix_validation",
            "version": "v1",
            "generator": "tools.validate_mcp_surface_command_matrix",
        },
        "summary": {
            "status": status,
            "matrix_status": summary.get("status"),
            "total_server_tools": summary.get("total_server_tools"),
            "matrix_rows": summary.get("matrix_rows"),
            "missing_role_rows": missing,
            "stale_role_rows": stale,
            "row_issue_count": len(row_issues),
            "ring_counts": summary.get("ring_counts"),
            "family_counts": summary.get("family_counts"),
        },
        "row_issues": row_issues,
        "matrix": matrix,
    }


def render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# MCP Surface Command Matrix Validation",
        "",
        f"- status: `{summary.get('status')}`",
        f"- matrix_status: `{summary.get('matrix_status')}`",
        f"- total_server_tools: `{summary.get('total_server_tools')}`",
        f"- matrix_rows: `{summary.get('matrix_rows')}`",
        f"- row_issue_count: `{summary.get('row_issue_count')}`",
        f"- ring_counts: `{summary.get('ring_counts')}`",
        f"- family_counts: `{summary.get('family_counts')}`",
    ]
    if payload.get("row_issues"):
        lines.extend(["", "## Row Issues", ""])
        for row in payload.get("row_issues", []):
            lines.append(f"- `{row.get('tool')}`: `{row.get('issues')}`")
    return "\n".join(lines) + "\n"


def main() -> int:
    payload = build_validation()
    matrix = payload.get("matrix") if isinstance(payload.get("matrix"), dict) else {}
    save_json_atomic(RAW_DIR / "mcp_surface_command_matrix.json", matrix)
    save_text_atomic(REPORTS_DIR / "mcp_surface_command_matrix.md", render_matrix_report(matrix))
    save_json_atomic(RAW_DIR / "mcp_surface_command_matrix_validation.json", payload)
    save_text_atomic(REPORTS_DIR / "mcp_surface_command_matrix_validation.md", render_report(payload))
    print(json.dumps(payload.get("summary", {}), ensure_ascii=False))
    return 0 if payload.get("summary", {}).get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
