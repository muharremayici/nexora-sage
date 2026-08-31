from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
ROOT = TOOLS_DIR.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.artifact_validator import ARTIFACT_PATHS
from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file
from tools.core.source_layer_taxonomy import source_layer_descriptions, source_layer_order
from tools.run_release_proof_bundle import PROOF_STEPS


RAW_OUTPUT_PATH = RAW_DIR / "layer_release_matrix.json"
REPORT_OUTPUT_PATH = REPORTS_DIR / "layer_release_matrix.md"


LAYER_RELEASE_CONTRACT_PATH = ROOT / "config" / "layer_release_matrix_contract.json"
LAYER_ORDER = source_layer_order()
LAYER_DESCRIPTIONS = source_layer_descriptions()


def _layer_release_contract() -> dict[str, Any]:
    payload = load_json_file(LAYER_RELEASE_CONTRACT_PATH, {})
    return payload if isinstance(payload, dict) else {}


def _layer_rules() -> dict[str, dict[str, Any]]:
    rules = _layer_release_contract().get("layer_rules", {})
    if not isinstance(rules, dict):
        return {}
    return {str(key): value for key, value in rules.items() if isinstance(value, dict)}


def _rel(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _artifact_exists(name: str) -> bool:
    path = ARTIFACT_PATHS.get(name)
    if path is None:
        path = RAW_DIR / f"{name}.json"
    return path.exists()


def _validator_exists(path_text: str) -> bool:
    return (ROOT / path_text).exists()


def _proof_ids() -> set[str]:
    return {str(step.get("id")) for step in PROOF_STEPS}


def _proof_step_positions() -> dict[str, int]:
    return {
        str(step.get("id")): index
        for index, step in enumerate(PROOF_STEPS)
        if str(step.get("id") or "").strip()
    }


def _proof_artifact_producer_positions() -> dict[str, int]:
    producers: dict[str, int] = {}
    for index, step in enumerate(PROOF_STEPS):
        raw_artifact = str(step.get("raw_artifact") or "").replace("\\", "/")
        if not raw_artifact:
            continue
        artifact_name = Path(raw_artifact.rsplit("/", 1)[-1]).stem
        if artifact_name:
            if artifact_name in producers:
                raise SystemExit(
                    f"Release proof declares multiple producers for artifact '{artifact_name}'."
                )
            producers[artifact_name] = index
    return producers


def _artifact_phase_check(
    items: list[str],
    *,
    producer_positions: dict[str, int],
    matrix_position: int,
) -> dict[str, Any]:
    materialized: list[str] = []
    deferred_to_later_producer: list[str] = []
    missing: list[str] = []
    for item in items:
        producer_position = producer_positions.get(item)
        if producer_position is not None and producer_position > matrix_position:
            deferred_to_later_producer.append(item)
        elif _artifact_exists(item):
            materialized.append(item)
        else:
            missing.append(item)
    return {
        "expected": items,
        "present": materialized,
        "materialized": materialized,
        "deferred_to_later_producer": deferred_to_later_producer,
        "missing": missing,
        "passed": not missing,
        "presence_semantics": (
            "Artifacts produced before this matrix must be materialized now. "
            "Artifacts with a declared later release-proof producer are validated by producer identity "
            "and are not read as stale pre-existing evidence."
        ),
    }


def _proof_validator_paths_by_id() -> dict[str, set[str]]:
    proof_validators: dict[str, set[str]] = {}
    for step in PROOF_STEPS:
        proof_id = str(step.get("id") or "").strip()
        command = step.get("command") if isinstance(step, dict) else []
        if not proof_id or not isinstance(command, list):
            continue
        paths: set[str] = set()
        for part in command:
            text = str(part or "").replace("\\", "/")
            marker = "${code_maps}/"
            if marker not in text:
                continue
            rel_path = text.split(marker, 1)[1]
            if rel_path.startswith("tools/validate") and rel_path.endswith(".py"):
                paths.add(rel_path)
        if paths:
            proof_validators[proof_id] = paths
    return proof_validators


def _release_evidence_text() -> str:
    path = ROOT / "docs" / "RELEASE_EVIDENCE_BUNDLE.md"
    return path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""


def _mcp_tools() -> set[str]:
    payload = load_json_file(RAW_DIR / "mcp_agent_surface_validation.json", {})
    tools = payload.get("all_tools") if isinstance(payload, dict) else []
    return {str(tool) for tool in tools or []}


def _mcp_tool_roles() -> dict[str, dict[str, Any]]:
    payload = load_json_file(ROOT / "config" / "mcp_tool_roles.json", {})
    rows = payload.get("tools", {}) if isinstance(payload, dict) else {}
    return {str(name): row for name, row in rows.items() if isinstance(row, dict)}


def _is_target_agent_tool(tool: str, roles: dict[str, dict[str, Any]]) -> bool:
    row = roles.get(str(tool), {})
    role = str(row.get("role") or "")
    audience = str(row.get("audience") or "")
    return role in {"primary_agent", "supporting_context", "human_hitl"} and "target_repo_agent" in audience


def _is_debug_mcp_tool(tool: str, roles: dict[str, dict[str, Any]]) -> bool:
    return str((roles.get(str(tool), {}) or {}).get("role") or "") == "debug_provenance"


def _is_mutating_mcp_tool(tool: str, roles: dict[str, dict[str, Any]]) -> bool:
    row = roles.get(str(tool), {})
    role = str(row.get("role") or "")
    audience = str(row.get("audience") or "")
    return role in {"mutating", "heavy_validation"} or "sage_operator" in audience


def _sample_files(layer_payload: dict[str, Any], limit: int = 6) -> list[str]:
    files = layer_payload.get("files") or []
    sample: list[str] = []
    for item in files[:limit]:
        if isinstance(item, dict):
            sample.append(str(item.get("path", "")))
    return [item for item in sample if item]


def _check_items(items: list[str], predicate) -> dict[str, Any]:
    present = [item for item in items if predicate(item)]
    missing = [item for item in items if item not in present]
    return {
        "expected": items,
        "present": present,
        "missing": missing,
        "passed": not missing,
    }


def build_matrix() -> dict[str, Any]:
    inventory = load_json_file(RAW_DIR / "source_layer_inventory.json", {})
    if not inventory:
        raise SystemExit("source_layer_inventory.json is missing. Run tools/generate_source_layer_inventory.py first.")

    layers_payload = inventory.get("layers") or {}
    proof_ids = _proof_ids()
    proof_step_positions = _proof_step_positions()
    matrix_position = proof_step_positions.get("layer_release_matrix")
    if matrix_position is None:
        raise SystemExit("layer_release_matrix is missing from the release proof contract.")
    artifact_producer_positions = _proof_artifact_producer_positions()
    proof_validator_paths_by_id = _proof_validator_paths_by_id()
    evidence_text = _release_evidence_text()
    mcp_tools = _mcp_tools()
    mcp_tool_roles = _mcp_tool_roles()
    layer_rules = _layer_rules()

    rows: list[dict[str, Any]] = []
    attention: list[dict[str, Any]] = []

    for layer in LAYER_ORDER:
        layer_payload = layers_payload.get(layer) or {}
        rule = layer_rules.get(layer, {})
        artifact_check = _artifact_phase_check(
            rule.get("artifacts", []),
            producer_positions=artifact_producer_positions,
            matrix_position=matrix_position,
        )
        proof_check = _check_items(rule.get("proof_ids", []), lambda item: item in proof_ids)
        declared_validators = [str(item) for item in rule.get("validators", [])]
        expected_proof_validators = sorted(
            {
                validator_path
                for proof_id in rule.get("proof_ids", [])
                for validator_path in proof_validator_paths_by_id.get(str(proof_id), set())
            }
        )
        proof_validator_check = _check_items(
            expected_proof_validators,
            lambda item: item in declared_validators,
        )
        doc_check = _check_items(rule.get("docs", []), lambda item: item in evidence_text)
        mcp_check = _check_items(
            rule.get("mcp_tools", []),
            lambda item: item in mcp_tools and _is_target_agent_tool(item, mcp_tool_roles),
        )
        debug_mcp_check = _check_items(
            rule.get("debug_mcp_tools", []),
            lambda item: item in mcp_tools and _is_debug_mcp_tool(item, mcp_tool_roles),
        )
        mutating_mcp_check = _check_items(
            rule.get("mutating_mcp_tools", []),
            lambda item: item in mcp_tools and _is_mutating_mcp_tool(item, mcp_tool_roles),
        )
        validator_check = _check_items(declared_validators, _validator_exists)
        count = int(layer_payload.get("count") or 0)

        checks = {
            "artifacts": artifact_check,
            "release_proof_steps": proof_check,
            "release_proof_validators_declared": proof_validator_check,
            "release_evidence_docs": doc_check,
            "mcp_agent_tools": mcp_check,
            "mcp_debug_tools": debug_mcp_check,
            "mcp_mutating_tools": mutating_mcp_check,
            "validators": validator_check,
        }
        failed_groups = [name for name, check in checks.items() if not check["passed"]]
        if layer == "unknown_or_review":
            status = "PASS" if count == 0 else "ATTENTION"
            if count:
                failed_groups.append("unclassified_files")
        elif count == 0:
            status = "ATTENTION"
            failed_groups.append("layer_has_no_files")
        else:
            status = "PASS" if not failed_groups else "ATTENTION"

        row = {
            "layer": layer,
            "description": LAYER_DESCRIPTIONS.get(layer, ""),
            "file_count": count,
            "sample_files": _sample_files(layer_payload),
            "status": status,
            "failed_groups": failed_groups,
            "checks": checks,
        }
        rows.append(row)
        if status != "PASS":
            attention.append(row)

    summary = {
        "layers": len(rows),
        "passing_layers": len([row for row in rows if row["status"] == "PASS"]),
        "attention_layers": len(attention),
        "unknown_or_review_files": int((layers_payload.get("unknown_or_review") or {}).get("count") or 0),
        "generated_runtime_artifacts": int((layers_payload.get("generated_runtime_artifact") or {}).get("count") or 0),
        "status": "PASS" if not attention else "ATTENTION",
    }

    return {
        "meta": {
            "kind": "layer_release_matrix",
            "version": "v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "generator": "tools.generate_layer_release_matrix",
        },
        "summary": summary,
        "layers": rows,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    rows = payload.get("layers") or []
    lines = [
        "# Nexora SAGE Layer Release Matrix",
        "",
        "This report maps the source layer inventory to release evidence, artifact contracts, validators and MCP/agent surfaces.",
        "",
        "## Summary",
        "",
        f"- Status: `{summary.get('status')}`",
        f"- Layers: `{summary.get('layers')}`",
        f"- Passing layers: `{summary.get('passing_layers')}`",
        f"- Attention layers: `{summary.get('attention_layers')}`",
        f"- Unknown/review files: `{summary.get('unknown_or_review_files')}`",
        f"- Generated/runtime artifacts in development workspace: `{summary.get('generated_runtime_artifacts')}`",
        "",
        "## Matrix",
        "",
        "| Layer | Files | Status | Missing groups | Evidence sample |",
        "|---|---:|---|---|---|",
    ]
    for row in rows:
        checks = row.get("checks") or {}
        evidence_sample: list[str] = []
        for group in ("artifacts", "release_proof_steps", "mcp_agent_tools", "mcp_debug_tools", "mcp_mutating_tools"):
            present = (checks.get(group) or {}).get("present") or []
            evidence_sample.extend([f"`{item}`" for item in present[:2]])
            if group == "artifacts":
                deferred = (checks.get(group) or {}).get("deferred_to_later_producer") or []
                evidence_sample.extend([f"`{item}` (later producer)" for item in deferred[:2]])
        missing = ", ".join(f"`{item}`" for item in row.get("failed_groups") or []) or "-"
        lines.append(
            f"| `{row.get('layer')}` | {row.get('file_count')} | `{row.get('status')}` | {missing} | {', '.join(evidence_sample[:5]) or '-'} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- `PASS` means the layer has files, registered evidence and expected gate/docs/agent linkage for its role.",
            "- Artefacts produced before this matrix must already be materialized; artefacts with a declared later proof producer are checked by producer identity and labelled `later producer` rather than read from a stale prior run.",
            "- `ATTENTION` means the layer needs release-owner review before widening public claims.",
            "- `generated_runtime_artifact` is allowed in a development workspace; clean distribution validation is the guard that keeps it out of packages.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    payload = build_matrix()
    save_json_atomic(RAW_OUTPUT_PATH, payload)
    save_text_atomic(REPORT_OUTPUT_PATH, render_markdown(payload))
    summary = payload["summary"]
    print(
        f"[layer-release-matrix] status={summary['status']} "
        f"layers={summary['layers']} attention={summary['attention_layers']} "
        f"unknown={summary['unknown_or_review_files']}"
    )
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
