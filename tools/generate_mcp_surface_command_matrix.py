#!/usr/bin/env python3
"""Generate a per-command MCP surface matrix from the role registry and server."""

from __future__ import annotations

import ast
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.config import CODE_MAPS_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file


ROLE_REGISTRY_PATH = CODE_MAPS_DIR / "config" / "mcp_tool_roles.json"
SERVER_PATH = CODE_MAPS_DIR / "tools" / "mcp" / "server.py"
MATRIX_CONTRACT_PATH = CODE_MAPS_DIR / "config" / "mcp_surface_command_matrix_contract.json"


def _string_set(value: Any) -> set[str]:
    return {str(item).strip() for item in value if str(item).strip()} if isinstance(value, list) else set()


def _matrix_contract() -> dict[str, Any]:
    payload = load_json_file(MATRIX_CONTRACT_PATH, {})
    return payload if isinstance(payload, dict) else {}


def _role_group(name: str) -> set[str]:
    groups = _matrix_contract().get("role_groups", {})
    groups = groups if isinstance(groups, dict) else {}
    return _string_set(groups.get(name))


def _quality_sample_aliases() -> dict[str, list[str]]:
    aliases = _matrix_contract().get("quality_sample_aliases", {})
    if not isinstance(aliases, dict):
        return {}
    return {
        str(tool): [str(item) for item in values if str(item).strip()]
        for tool, values in aliases.items()
        if isinstance(values, list)
    }


def _tool_policy_map(name: str) -> dict[str, list[str]]:
    values = _matrix_contract().get(name, {})
    if not isinstance(values, dict):
        return {}
    return {
        str(key): [str(item) for item in items if str(item).strip()]
        for key, items in values.items()
        if isinstance(items, list)
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decorator_name(node: ast.AST) -> str:
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    if isinstance(node, ast.Attribute):
        base = _decorator_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _mcp_tools() -> set[str]:
    tree = ast.parse(SERVER_PATH.read_text(encoding="utf-8"))
    tools: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        decorators = {_decorator_name(decorator) for decorator in node.decorator_list}
        if "mcp.tool" in decorators:
            tools.add(node.name)
    return tools


def _role_rows() -> dict[str, dict[str, Any]]:
    payload = load_json_file(ROLE_REGISTRY_PATH, {})
    rows = payload.get("tools", {}) if isinstance(payload, dict) else {}
    return {str(name): row for name, row in rows.items() if isinstance(row, dict)}


def _surface_ring(role: str, audience: str) -> str:
    mutating_roles = _role_group("mutating_roles")
    hitl_roles = _role_group("hitl_roles")
    internal_roles = _role_group("internal_roles")
    target_agent_roles = _role_group("target_agent_roles")
    if role in mutating_roles:
        return "H1_MUTATION_GATE" if "target_repo_agent" in audience else "H2_OPERATOR_MUTATION_GATE"
    if role in hitl_roles:
        return "H1_HITL_GOVERNANCE" if "target_repo_agent" in audience else "H2_HITL_AUDIT"
    if role == "heavy_validation" and "target_repo_agent" in audience:
        return "H1_HEAVY_VALIDATION_GATE"
    if role in internal_roles or "sage_" in audience:
        return "H2_SAGE_INTERNAL"
    if role in target_agent_roles and "target_repo_agent" in audience:
        return "H1_TARGET_REPO_AGENT"
    return "UNCLASSIFIED_REVIEW_REQUIRED"


def _surface_family(tool: str, role: str, audience: str = "") -> str:
    internal_roles = _role_group("internal_roles")
    if role == "debug_provenance" or "sage_" in audience:
        return "sage_internal_or_readiness"
    for family, tools in _tool_policy_map("surface_family_policy").items():
        if tool in tools:
            return family
    if role == "supporting_context":
        return "supporting_context"
    if role == "human_hitl":
        return "hitl_governance"
    if role == "mutating":
        return "mutation_gate"
    if role in internal_roles:
        return "sage_internal_or_readiness"
    return "unclassified"


def _review_obligations(tool: str, role: str, audience: str, row: dict[str, Any]) -> list[str]:
    obligations: list[str] = []
    target_agent_roles = _role_group("target_agent_roles")
    internal_roles = _role_group("internal_roles")
    hitl_roles = _role_group("hitl_roles")
    mutating_roles = _role_group("mutating_roles")
    if role in target_agent_roles:
        obligations.extend([
            "target_root_isolation",
            "repo_relative_path_contract",
            "no_sage_internal_noise",
            "bounded_markdown_yaml_brief",
        ])
    if role == "primary_agent":
        obligations.append("manual_agent_surface_sample")
    obligations.extend(_tool_policy_map("tool_review_obligations").get(tool, []))
    if role in internal_roles:
        obligations.append("not_default_target_repo_context")
    if row.get("mutates") is True:
        obligations.append("mutation_intent_or_safe_generated_artifact_write_declared")
    if row.get("requires_approval") is True or role in hitl_roles:
        obligations.append("progressive_hitl_posture_explicit")
    if row.get("heavy") is True:
        obligations.append("not_preloaded_as_default_context")
    return sorted(set(obligations))


def _quality_status(tool: str) -> dict[str, Any]:
    aliases = _quality_sample_aliases().get(tool, [])
    return {
        "sample_aliases": aliases,
        "present_samples": [],
        "missing_samples": [],
        "failed_samples": [],
        "sample_status": "DEFERRED_TO_AGENT_SURFACE_QUALITY_REVIEW" if aliases else "NOT_REQUIRED",
    }


def _tool_row(tool: str, role_row: dict[str, Any]) -> dict[str, Any]:
    role = str(role_row.get("role") or "")
    audience = str(role_row.get("audience") or "")
    ring = _surface_ring(role, audience)
    default_target_agent_context = ring == "H1_TARGET_REPO_AGENT" and role == "primary_agent" and role_row.get("heavy") is not True
    quality = _quality_status(tool)
    issues: list[str] = []
    if ring == "UNCLASSIFIED_REVIEW_REQUIRED":
        issues.append("unclassified_surface_ring")
    if role == "debug_provenance" and "target_repo_agent" in audience:
        issues.append("internal_role_targets_repo_agent")
    mutating_roles = _role_group("mutating_roles")
    internal_roles = _role_group("internal_roles")
    if role_row.get("mutates") is True and role_row.get("safe_generated_artifact_write") is not True:
        if role_row.get("requires_approval") is not True and role not in mutating_roles and role not in internal_roles:
            issues.append("mutating_tool_without_gate")
    if role_row.get("heavy") is True and default_target_agent_context:
        issues.append("heavy_tool_marked_default_context")
    return {
        "tool": tool,
        "role": role,
        "audience": audience,
        "surface_ring": ring,
        "surface_family": _surface_family(tool, role, audience),
        "default_use": role_row.get("default_use"),
        "default_target_agent_context": default_target_agent_context,
        "mutates": bool(role_row.get("mutates")),
        "safe_generated_artifact_write": bool(role_row.get("safe_generated_artifact_write")),
        "mutates_repository": role_row.get("mutates_repository"),
        "heavy": bool(role_row.get("heavy")),
        "requires_approval": bool(role_row.get("requires_approval")),
        "review_obligations": _review_obligations(tool, role, audience, role_row),
        "quality_review": quality,
        "status": "PASS" if not issues else "FAIL",
        "issues": issues,
    }


def build_matrix() -> dict[str, Any]:
    server_tools = _mcp_tools()
    role_rows = _role_rows()
    missing_role_rows = sorted(server_tools - set(role_rows))
    stale_role_rows = sorted(set(role_rows) - server_tools)
    rows = [_tool_row(tool, role_rows[tool]) for tool in sorted(server_tools & set(role_rows))]
    failed_rows = [row["tool"] for row in rows if row.get("status") != "PASS"]
    ring_counts: dict[str, int] = {}
    family_counts: dict[str, int] = {}
    for row in rows:
        ring_counts[row["surface_ring"]] = ring_counts.get(row["surface_ring"], 0) + 1
        family_counts[row["surface_family"]] = family_counts.get(row["surface_family"], 0) + 1
    status = "PASS" if not missing_role_rows and not stale_role_rows and not failed_rows else "FAIL"
    return {
        "meta": {
            "kind": "mcp_surface_command_matrix",
            "version": "v1",
            "generated_at": _utc_now(),
            "generator": "tools.generate_mcp_surface_command_matrix",
            "sources": [
                "config/mcp_tool_roles.json",
                "config/agent_surface_taxonomy.json",
                "tools/mcp/server.py",
            ],
            "quality_evidence_phase": "agent_surface_quality_review_and_seal_matrix",
        },
        "summary": {
            "status": status,
            "total_server_tools": len(server_tools),
            "matrix_rows": len(rows),
            "missing_role_rows": missing_role_rows,
            "stale_role_rows": stale_role_rows,
            "failed_rows": failed_rows,
            "ring_counts": ring_counts,
            "family_counts": family_counts,
            "human_seal_status": "not_human_sealed",
            "human_seal_rule": "This matrix verifies role and command-surface contracts only; final Progressive HITL human seal remains separate.",
        },
        "commands": rows,
    }


def render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# MCP Surface Command Matrix",
        "",
        f"- status: `{summary.get('status')}`",
        f"- total_server_tools: `{summary.get('total_server_tools')}`",
        f"- matrix_rows: `{summary.get('matrix_rows')}`",
        f"- ring_counts: `{summary.get('ring_counts')}`",
        f"- family_counts: `{summary.get('family_counts')}`",
        f"- human seal: `{summary.get('human_seal_status')}`",
        "",
        "| Tool | Ring | Family | Role | Audience | Declared Sample Aliases | Obligations | Status |",
        "|---|---|---|---|---|---:|---:|---|",
    ]
    for row in payload.get("commands", []):
        quality = row.get("quality_review") or {}
        lines.append(
            f"| `{row.get('tool')}` | `{row.get('surface_ring')}` | `{row.get('surface_family')}` | "
            f"`{row.get('role')}` | `{row.get('audience')}` | "
            f"{len(quality.get('sample_aliases') or [])} | "
            f"{len(row.get('review_obligations') or [])} | `{row.get('status')}` |"
        )
    if summary.get("missing_role_rows") or summary.get("stale_role_rows") or summary.get("failed_rows"):
        lines.extend(["", "## Issues", ""])
        for key in ("missing_role_rows", "stale_role_rows", "failed_rows"):
            values = summary.get(key) or []
            if values:
                lines.append(f"- {key}: `{values}`")
    return "\n".join(lines) + "\n"


def main() -> int:
    payload = build_matrix()
    save_json_atomic(RAW_DIR / "mcp_surface_command_matrix.json", payload)
    save_text_atomic(REPORTS_DIR / "mcp_surface_command_matrix.md", render_report(payload))
    print(json.dumps(payload.get("summary", {}), ensure_ascii=False))
    return 0 if payload.get("summary", {}).get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
