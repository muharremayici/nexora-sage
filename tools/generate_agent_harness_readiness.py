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
from tools.core.evidence_status import normalize_evidence_status
from tools.core.json_io import load_json_file
from tools.core.release_proof_steps import load_release_proof_artifact_requiredness


RAW_OUTPUT = RAW_DIR / "agent_harness_readiness.json"
REPORT_OUTPUT = REPORTS_DIR / "agent_harness_readiness.md"
HARNESS_CONTRACT_PATH = CODE_MAPS_DIR / "config" / "agent_harness_contract.json"
MCP_TOOL_ROLES_PATH = CODE_MAPS_DIR / "config" / "mcp_tool_roles.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _contract() -> dict[str, Any]:
    payload = load_json_file(HARNESS_CONTRACT_PATH, {})
    return payload if isinstance(payload, dict) else {}


def _mcp_tool_roles() -> dict[str, dict[str, Any]]:
    payload = load_json_file(MCP_TOOL_ROLES_PATH, {})
    tools = payload.get("tools", {}) if isinstance(payload, dict) else {}
    return {str(name): row for name, row in tools.items() if isinstance(row, dict)} if isinstance(tools, dict) else {}


def _contract_list(payload: dict[str, Any], key: str) -> list[str]:
    values = payload.get(key, []) if isinstance(payload, dict) else []
    return [str(value) for value in values if str(value).strip()] if isinstance(values, list) else []


def _layers(contract: dict[str, Any]) -> list[dict[str, Any]]:
    values = contract.get("layers", []) if isinstance(contract, dict) else []
    return [value for value in values if isinstance(value, dict)] if isinstance(values, list) else []


def _load(name: str) -> dict[str, Any]:
    payload = load_json_file(RAW_DIR / name, {})
    return payload if isinstance(payload, dict) else {}


def _summary(name: str) -> dict[str, Any]:
    summary = _load(name).get("summary", {})
    return summary if isinstance(summary, dict) else {}


def _artifact_state(name: str, *, required_for_release: bool | None = None) -> dict[str, Any]:
    path = RAW_DIR / name
    size = path.stat().st_size if path.exists() and path.is_file() else 0
    payload = _load(name)
    summary = payload.get("summary", {}) if isinstance(payload.get("summary"), dict) else {}
    verdict = normalize_evidence_status(payload)
    passed = verdict.get("passed")
    status = str(verdict.get("status") or "").upper()
    state = {
        "artifact": name,
        "exists": path.exists(),
        "size_bytes": size,
        "estimated_tokens": int(size / 4) if size else 0,
        "validated": passed,
        "status": status or summary.get("status") or "",
        "status_source": verdict.get("source"),
    }
    if required_for_release is not None:
        state["required_for_release"] = required_for_release
    return state


def _decorator_name(node: ast.AST) -> str:
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    if isinstance(node, ast.Attribute):
        base = _decorator_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _mcp_tool_rows(classification: dict[str, Any]) -> list[dict[str, Any]]:
    role_registry = _mcp_tool_roles()
    role_category_map = {
        str(key): str(value)
        for key, value in (classification.get("role_category_map", {}) if isinstance(classification, dict) else {}).items()
        if str(key).strip() and str(value).strip()
    }
    mutation_hints = tuple(_contract_list(classification, "mutation_hints"))
    hitl_hints = tuple(_contract_list(classification, "hitl_hints"))
    context_hints = tuple(_contract_list(classification, "context_hints"))
    readiness_hints = tuple(_contract_list(classification, "readiness_hints"))
    server_path = CODE_MAPS_DIR / "tools" / "mcp" / "server.py"
    tree = ast.parse(server_path.read_text(encoding="utf-8", errors="replace"))
    rows: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        decorators = {_decorator_name(decorator) for decorator in node.decorator_list}
        if "mcp.tool" not in decorators:
            continue
        name = node.name
        doc = ast.get_docstring(node) or ""
        role_row = role_registry.get(name, {})
        role = str(role_row.get("role") or "").strip()
        audience = str(role_row.get("audience") or "").strip()
        lowered = f"{name} {doc}".lower()
        mutation_capable = bool(role_row.get("mutates")) or any(hint in lowered for hint in mutation_hints)
        hitl_relevant = role in {"human_hitl"} or bool(role_row.get("requires_approval")) or any(hint in lowered for hint in hitl_hints)
        if role == "human_hitl" or bool(role_row.get("requires_approval")):
            permission = "human_authority"
        elif role in {"mutating", "heavy_validation"} or mutation_capable:
            permission = "guarded_or_readiness_only"
        else:
            permission = "read_only"
        if role in role_category_map:
            category = role_category_map[role]
        elif any(hint in lowered for hint in context_hints):
            category = "context"
        elif any(hint in lowered for hint in readiness_hints):
            category = "verification"
        elif hitl_relevant:
            category = "governance_security"
        elif mutation_capable:
            category = "execution"
        else:
            category = "tooling"
        rows.append(
            {
                "name": name,
                "category": category,
                "permission": permission,
                "mutation_capable": mutation_capable,
                "human_approval_relevant": hitl_relevant,
                "role": role or "unregistered",
                "audience": audience or "unknown",
                "doc": doc,
            }
        )
    return sorted(rows, key=lambda row: row["name"])


def _context_value(contract: dict[str, Any]) -> dict[str, Any]:
    context_contract = contract.get("context_value", {}) if isinstance(contract, dict) else {}
    raw_artifacts = _contract_list(context_contract, "raw_artifacts")
    distilled_artifacts = _contract_list(context_contract, "distilled_artifacts")
    raw_rows = [_artifact_state(name) for name in raw_artifacts]
    distilled_rows = [_artifact_state(name) for name in distilled_artifacts]
    raw_bytes = sum(row["size_bytes"] for row in raw_rows)
    distilled_bytes = sum(row["size_bytes"] for row in distilled_rows)
    reduction = round(1.0 - (distilled_bytes / raw_bytes), 4) if raw_bytes else 0.0
    return {
        "measurement_boundary": str(context_contract.get("measurement_boundary") or ""),
        "raw_artifacts": raw_rows,
        "distilled_artifacts": distilled_rows,
        "raw_bytes": raw_bytes,
        "distilled_bytes": distilled_bytes,
        "raw_estimated_tokens": int(raw_bytes / 4) if raw_bytes else 0,
        "distilled_estimated_tokens": int(distilled_bytes / 4) if distilled_bytes else 0,
        "estimated_artifact_projection_reduction_ratio": reduction,
        "estimated_artifact_projection_reduction_percent": round(reduction * 100, 2),
    }


def _classify_layer_evidence(evidence: list[dict[str, Any]]) -> tuple[str, list[str]]:
    required_evidence = [item for item in evidence if item.get("required_for_release") is True]
    blocking_evidence = [item for item in required_evidence if item.get("validated") is not True]
    advisory_attention = [
        str(item.get("artifact") or "")
        for item in evidence
        if item.get("required_for_release") is False and item.get("validated") is not True
    ]
    status = "PASS" if required_evidence and not blocking_evidence else "ATTENTION"
    return status, [name for name in advisory_attention if name]


def build_harness_readiness() -> dict[str, Any]:
    contract = _contract()
    layers_contract = _layers(contract)
    artifact_requiredness = load_release_proof_artifact_requiredness()
    classification = contract.get("mcp_tool_classification", {}) if isinstance(contract.get("mcp_tool_classification"), dict) else {}
    mcp_tools = _mcp_tool_rows(classification)
    artifact_lookup = {
        row["artifact"]: row
        for layer in layers_contract
        for name in layer.get("evidence", [])
        for row in [
            _artifact_state(
                str(name),
                required_for_release=artifact_requiredness.get(str(name), True),
            )
        ]
    }
    layers = []
    for layer in layers_contract:
        evidence = [artifact_lookup[str(name)] for name in layer.get("evidence", [])]
        evidence_status, advisory_attention = _classify_layer_evidence(evidence)
        layers.append(
            {
                **layer,
                "evidence_state": evidence,
                "evidence_status": evidence_status,
                "advisory_attention_artifacts": advisory_attention,
            }
        )

    tools_by_category: dict[str, int] = {}
    tools_by_permission: dict[str, int] = {}
    for tool in mcp_tools:
        tools_by_category[tool["category"]] = tools_by_category.get(tool["category"], 0) + 1
        tools_by_permission[tool["permission"]] = tools_by_permission.get(tool["permission"], 0) + 1

    failed_layers = [layer["id"] for layer in layers if layer["evidence_status"] != "PASS"]
    advisory_attention_artifacts = sorted(
        {
            artifact
            for layer in layers
            for artifact in layer.get("advisory_attention_artifacts", [])
        }
    )
    bounded_layers = [layer["id"] for layer in layers if layer.get("support_level") != "native"]
    if failed_layers:
        overall_status = "ATTENTION"
    elif bounded_layers:
        overall_status = "PASS_WITH_BOUNDARIES"
    else:
        overall_status = "PASS"
    context_value = _context_value(contract)
    evaluation_boundary = contract.get("evaluation_boundary", {}) if isinstance(contract.get("evaluation_boundary"), dict) else {}
    evaluation_baseline = contract.get("evaluation_baseline", {}) if isinstance(contract.get("evaluation_baseline"), dict) else {}
    positioning = contract.get("positioning", {}) if isinstance(contract.get("positioning"), dict) else {}
    return {
        "meta": {
            "kind": "agent_harness_readiness",
            "version": "v1",
            "generated_at": _utc_now(),
            "generator": "tools.generate_agent_harness_readiness",
            "framework": contract.get("framework"),
        },
        "summary": {
            "status": overall_status,
            "evidence_status": "PASS" if not failed_layers else "ATTENTION",
            "support_status": "PARTIAL_HARNESS_CONTROL_PLANE" if bounded_layers else "FULL_NATIVE_HARNESS",
            "total_layers": len(layers),
            "validated_layers": len(layers) - len(failed_layers),
            "failed_layers": failed_layers,
            "advisory_attention_artifacts": advisory_attention_artifacts,
            "bounded_layers": bounded_layers,
            "mcp_tools": len(mcp_tools),
            "mcp_tools_by_category": tools_by_category,
            "mcp_tools_by_permission": tools_by_permission,
            "artifact_projection_reduction_percent": context_value["estimated_artifact_projection_reduction_percent"],
            "institutional_memory_portable": True,
            "model_agnostic_control_plane": bool(evaluation_boundary.get("model_agnostic_control_plane")),
            "private_eval_learning_loop": bool(evaluation_boundary.get("private_eval_learning_loop")),
            "evaluation_claim_boundary": str(evaluation_boundary.get("claim_boundary") or "not_available"),
            "evaluation_baseline_status": str(evaluation_baseline.get("status") or "not_available"),
            "evaluation_baseline_opt_in": bool(evaluation_baseline.get("opt_in")),
        },
        "positioning": positioning,
        "layers": layers,
        "mcp_tool_readiness": mcp_tools,
        "context_value": context_value,
    }


def render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# Agent Harness Readiness",
        "",
        f"- generated_at: `{payload.get('meta', {}).get('generated_at')}`",
        f"- status: `{summary.get('status')}`",
        f"- evidence_status: `{summary.get('evidence_status')}`",
        f"- support_status: `{summary.get('support_status')}`",
        f"- validated_layers: `{summary.get('validated_layers')}/{summary.get('total_layers')}`",
        f"- mcp_tools: `{summary.get('mcp_tools')}`",
        f"- artifact_projection_reduction_percent: `{summary.get('artifact_projection_reduction_percent')}`",
        f"- model_agnostic_control_plane: `{summary.get('model_agnostic_control_plane')}`",
        f"- private_eval_learning_loop: `{summary.get('private_eval_learning_loop')}`",
        f"- evaluation_claim_boundary: {summary.get('evaluation_claim_boundary')}",
        f"- evaluation_baseline_status: `{summary.get('evaluation_baseline_status')}`",
        f"- evaluation_baseline_opt_in: `{summary.get('evaluation_baseline_opt_in')}`",
        "",
        "## Positioning",
        "",
        f"- {payload.get('positioning', {}).get('product_category')}",
        f"- Not claimed: {payload.get('positioning', {}).get('not_claimed')}",
        f"- {payload.get('positioning', {}).get('meaning')}",
        f"- {payload.get('positioning', {}).get('model_swap_rule')}",
        f"- {payload.get('positioning', {}).get('human_capital_rule')}",
        "",
        "## Harness Layers",
        "",
        "| Layer | Evidence | Support | Claim Boundary | Purpose |",
        "|---|---|---|---|---|",
    ]
    for layer in payload.get("layers", []):
        evidence = "<br>".join(f"`{item.get('artifact')}`:{'OK' if item.get('exists') else 'MISSING'}" for item in layer.get("evidence_state", []))
        lines.append(
            f"| `{layer.get('id')}` | `{layer.get('evidence_status')}` | `{layer.get('support_level')}` | "
            f"{layer.get('claim_boundary')} | {layer.get('purpose')} |"
        )

    lines.extend(["", "## MCP Tool Readiness", "", "| Tool | Role | Audience | Category | Permission | Mutation | HITL |", "|---|---|---|---|---|---|---|"])
    for tool in payload.get("mcp_tool_readiness", []):
        lines.append(
            f"| `{tool.get('name')}` | `{tool.get('role')}` | `{tool.get('audience')}` | `{tool.get('category')}` | `{tool.get('permission')}` | `{tool.get('mutation_capable')}` | `{tool.get('human_approval_relevant')}` |"
        )

    context = payload.get("context_value", {})
    lines.extend(
        [
            "",
            "## ContextOS Value",
            "",
            f"- raw_bytes: `{context.get('raw_bytes')}`",
            f"- distilled_bytes: `{context.get('distilled_bytes')}`",
            f"- raw_estimated_tokens: `{context.get('raw_estimated_tokens')}`",
            f"- distilled_estimated_tokens: `{context.get('distilled_estimated_tokens')}`",
            f"- estimated_artifact_projection_reduction_percent: `{context.get('estimated_artifact_projection_reduction_percent')}`",
            f"- measurement_boundary: {context.get('measurement_boundary')}",
            "",
            "| Distilled Artifact | Size Bytes | Estimated Tokens |",
            "|---|---|---|",
        ]
    )
    for row in context.get("distilled_artifacts", []):
        lines.append(f"| `{row.get('artifact')}` | `{row.get('size_bytes')}` | `{row.get('estimated_tokens')}` |")
    return "\n".join(lines) + "\n"


def run() -> dict[str, Any]:
    payload = build_harness_readiness()
    save_json_atomic(RAW_OUTPUT, payload)
    save_text_atomic(REPORT_OUTPUT, render_report(payload))
    return payload


def main() -> int:
    payload = run()
    print(json.dumps(payload.get("summary", {}), ensure_ascii=False))
    return 0 if payload.get("summary", {}).get("evidence_status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
