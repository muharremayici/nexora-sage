from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.artifact_validator import ARTIFACT_PATHS, ARTIFACT_SCHEMAS
from tools.core.config import CONFIG_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file, load_json_object_strict
from tools.core.release_proof_steps import load_release_proof_steps


RAW_OUTPUT_PATH = RAW_DIR / "system_connectivity_map.json"
REPORT_OUTPUT_PATH = REPORTS_DIR / "system_connectivity_map.md"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _artifact_id_from_raw(raw_artifact: str) -> str:
    name = Path(str(raw_artifact or "")).name
    return name[:-5] if name.endswith(".json") else Path(name).stem


def _load_or_build_pipeline_registry() -> dict[str, Any]:
    from tools.core.pipeline_registry import (
        catalog_args_from_execution_policy,
        load_pipeline_execution_policy,
        step_registry_from_catalog,
    )
    from tools.orchestrators.orchestrator import build_step_catalog

    args = catalog_args_from_execution_policy(load_pipeline_execution_policy())
    payload = step_registry_from_catalog(build_step_catalog(args))
    save_json_atomic(RAW_DIR / "pipeline_step_registry.json", payload)
    return payload


def _release_steps() -> dict[str, dict[str, Any]]:
    return {str(step.get("id") or ""): step for step in load_release_proof_steps()}


def _layer_rows(layer_matrix: dict[str, Any]) -> list[dict[str, Any]]:
    rows = layer_matrix.get("layers") if isinstance(layer_matrix, dict) else []
    return [row for row in rows if isinstance(row, dict)]


def _expected(row: dict[str, Any], group: str) -> list[str]:
    checks = row.get("checks") if isinstance(row.get("checks"), dict) else {}
    payload = checks.get(group) if isinstance(checks.get(group), dict) else {}
    return [str(item) for item in payload.get("expected", []) or []]


def _present(row: dict[str, Any], group: str) -> list[str]:
    checks = row.get("checks") if isinstance(row.get("checks"), dict) else {}
    payload = checks.get(group) if isinstance(checks.get(group), dict) else {}
    return [str(item) for item in payload.get("present", []) or []]


def _step_raw_artifact_ids(steps: dict[str, dict[str, Any]]) -> set[str]:
    ids: set[str] = set()
    for step in steps.values():
        raw = str(step.get("raw_artifact") or "")
        if raw:
            ids.add(_artifact_id_from_raw(raw))
    return ids


def _artifact_registered(artifact_id: str, release_raw_artifacts: set[str]) -> bool:
    return artifact_id in ARTIFACT_SCHEMAS or artifact_id in ARTIFACT_PATHS or artifact_id in release_raw_artifacts


def _artifact_consumers(pipeline_steps: list[dict[str, Any]]) -> dict[str, list[str]]:
    consumers: dict[str, list[str]] = {}
    for step in pipeline_steps:
        name = str(step.get("name") or "")
        for artifact in step.get("reads_artifacts", []) or []:
            if str(artifact) == "*":
                continue
            consumers.setdefault(str(artifact), []).append(name)
    return {key: sorted(set(values)) for key, values in sorted(consumers.items())}


def _build_layer_connectivity(
    layer_matrix: dict[str, Any],
    release_steps: dict[str, dict[str, Any]],
    release_raw_artifacts: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    layer_nodes: list[dict[str, Any]] = []
    attention: list[dict[str, Any]] = []
    for row in _layer_rows(layer_matrix):
        layer = str(row.get("layer") or "")
        artifacts = _expected(row, "artifacts")
        proof_steps = _expected(row, "release_proof_steps")
        validators = _expected(row, "validators")
        mcp_tools = _expected(row, "mcp_agent_tools")
        missing_artifact_registry = [
            artifact for artifact in artifacts if not _artifact_registered(artifact, release_raw_artifacts)
        ]
        missing_release_steps = [step_id for step_id in proof_steps if step_id not in release_steps]
        missing_validators = [path for path in validators if not (ROOT / path).exists()]
        node = {
            "id": layer,
            "type": "source_layer",
            "file_count": int(row.get("file_count") or 0),
            "status": str(row.get("status") or "UNKNOWN"),
            "expected_artifacts": artifacts,
            "present_artifacts": _present(row, "artifacts"),
            "expected_release_steps": proof_steps,
            "present_release_steps": _present(row, "release_proof_steps"),
            "validators": validators,
            "mcp_tools": mcp_tools,
            "connectivity_warnings": {
                "missing_artifact_registry": missing_artifact_registry,
                "missing_release_steps": missing_release_steps,
                "missing_validators": missing_validators,
            },
        }
        if missing_artifact_registry or missing_release_steps or missing_validators:
            attention.append({"layer": layer, "warnings": node["connectivity_warnings"]})
        layer_nodes.append(node)
    return layer_nodes, attention


def _build_spine_connectivity(
    spine_registry: dict[str, Any],
    release_steps: dict[str, dict[str, Any]],
    release_raw_artifacts: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    nodes = spine_registry.get("spine_nodes", []) if isinstance(spine_registry.get("spine_nodes"), list) else []
    spine_nodes: list[dict[str, Any]] = []
    attention: list[dict[str, Any]] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or "")
        step_id = str(node.get("release_proof_step") or "")
        raw_artifact = str(node.get("raw_artifact") or "")
        artifact_id = _artifact_id_from_raw(raw_artifact)
        missing = {
            "source": bool(node.get("source")) and not (ROOT / str(node.get("source"))).exists(),
            "validator": bool(node.get("validator")) and not (ROOT / str(node.get("validator"))).exists(),
            "release_step": bool(step_id) and step_id not in release_steps,
            "artifact_registry": bool(artifact_id) and not _artifact_registered(artifact_id, release_raw_artifacts),
        }
        spine_node = {
            "id": node_id,
            "type": "spine_node",
            "role": str(node.get("role") or ""),
            "source": str(node.get("source") or ""),
            "validator": str(node.get("validator") or ""),
            "release_proof_step": step_id,
            "raw_artifact": raw_artifact,
            "missing_links": {key: value for key, value in missing.items() if value},
        }
        if spine_node["missing_links"]:
            attention.append({"spine_node": node_id, "missing_links": spine_node["missing_links"]})
        spine_nodes.append(spine_node)
    return spine_nodes, attention


def build_connectivity_map() -> dict[str, Any]:
    source_inventory = load_json_file(RAW_DIR / "source_layer_inventory.json", {})
    layer_matrix = load_json_file(RAW_DIR / "layer_release_matrix.json", {})
    pipeline_registry = _load_or_build_pipeline_registry()
    spine_registry = load_json_object_strict(CONFIG_DIR / "system_spine_registry.json", label="System spine registry")
    pipeline_policy = load_json_object_strict(CONFIG_DIR / "pipeline_execution_policy.json", label="Pipeline execution policy")
    release_steps = _release_steps()
    release_raw_artifacts = _step_raw_artifact_ids(release_steps)
    external_inputs = (
        pipeline_policy.get("external_input_artifacts", {}).get("items", {})
        if isinstance(pipeline_policy.get("external_input_artifacts"), dict)
        else {}
    )
    external_input_ids = {str(key) for key in external_inputs if key}
    pipeline_steps = pipeline_registry.get("steps", []) if isinstance(pipeline_registry.get("steps"), list) else []
    pipeline_artifact_ownership = pipeline_registry.get("artifact_ownership", {}) if isinstance(pipeline_registry, dict) else {}
    pipeline_writers = (
        pipeline_artifact_ownership.get("writers", {})
        if isinstance(pipeline_artifact_ownership.get("writers"), dict)
        else {}
    )
    pipeline_consumers = _artifact_consumers(pipeline_steps)
    broader_system_consumers = {
        artifact: sorted({str(path) for path in row.get("broader_system_consumers", []) if str(path).strip()})
        for artifact, row in external_inputs.items()
        if isinstance(row, dict) and isinstance(row.get("broader_system_consumers"), list)
    }

    layer_nodes, layer_attention = _build_layer_connectivity(layer_matrix, release_steps, release_raw_artifacts)
    spine_nodes, spine_attention = _build_spine_connectivity(spine_registry, release_steps, release_raw_artifacts)
    unowned_consumed_artifacts = sorted(
        artifact
        for artifact in pipeline_consumers
        if artifact not in pipeline_writers
        and artifact not in ARTIFACT_PATHS
        and artifact not in release_raw_artifacts
        and artifact not in external_input_ids
    )
    consumed_external_inputs = sorted(artifact for artifact in pipeline_consumers if artifact in external_input_ids)
    broader_consumed_external_inputs = sorted(
        artifact for artifact in external_input_ids if broader_system_consumers.get(artifact)
    )
    declared_but_unconsumed_anywhere = sorted(
        artifact
        for artifact in external_input_ids
        if artifact not in pipeline_consumers and not broader_system_consumers.get(artifact)
    )
    invalid_broader_system_consumers = [
        {"artifact": artifact, "consumer": consumer}
        for artifact, consumers in broader_system_consumers.items()
        for consumer in consumers
        if not (ROOT / consumer).exists()
    ]
    writer_conflicts = (
        pipeline_artifact_ownership.get("write_conflicts", {})
        if isinstance(pipeline_artifact_ownership.get("write_conflicts"), dict)
        else {}
    )
    critical_failures = []
    if not source_inventory:
        critical_failures.append("missing_source_layer_inventory")
    if not layer_matrix:
        critical_failures.append("missing_layer_release_matrix")
    if not pipeline_registry:
        critical_failures.append("missing_pipeline_step_registry")
    if spine_attention:
        critical_failures.append("spine_missing_links")
    if layer_attention:
        critical_failures.append("layer_missing_links")
    if writer_conflicts:
        critical_failures.append("artifact_writer_conflicts")
    if unowned_consumed_artifacts:
        critical_failures.append("unowned_consumed_artifacts")
    if declared_but_unconsumed_anywhere:
        critical_failures.append("declared_external_inputs_unconsumed_anywhere")
    if invalid_broader_system_consumers:
        critical_failures.append("invalid_broader_system_consumers")

    payload = {
        "meta": {
            "kind": "system_connectivity_map",
            "version": "v1",
            "generated_at": _utc_now(),
            "generator": "tools.generate_system_connectivity_map",
            "source_artifacts": [
                "source_layer_inventory.json",
                "layer_release_matrix.json",
                "pipeline_step_registry.json",
                "config/system_spine_registry.json",
                "config/pipeline_execution_policy.json",
                "config/release_proof_steps_contract.json",
                "tools/core/artifact_validator.py",
            ],
        },
        "summary": {
            "status": "PASS" if not critical_failures else "FAIL",
            "critical_failures": critical_failures,
            "source_layers": len(layer_nodes),
            "pipeline_steps": len(pipeline_steps),
            "spine_nodes": len(spine_nodes),
            "artifact_writers": len(pipeline_writers),
            "artifact_consumers": len(pipeline_consumers),
            "external_input_artifacts": len(external_input_ids),
            "consumed_external_inputs": len(consumed_external_inputs),
            "broader_consumed_external_inputs": len(broader_consumed_external_inputs),
            "declared_but_unconsumed_anywhere": len(declared_but_unconsumed_anywhere),
            "writer_conflicts": len(writer_conflicts),
            "layer_attention_items": len(layer_attention),
            "spine_attention_items": len(spine_attention),
            "unowned_consumed_artifacts": len(unowned_consumed_artifacts),
        },
        "connectivity": {
            "spine_nodes": spine_nodes,
            "source_layers": layer_nodes,
            "pipeline_steps": [
                {
                    "id": str(step.get("name") or ""),
                    "reads_artifacts": sorted(str(item) for item in step.get("reads_artifacts", []) or []),
                    "writes_artifacts": sorted(str(item) for item in step.get("writes_artifacts", []) or []),
                }
                for step in pipeline_steps
                if str(step.get("name") or "")
            ],
            "pipeline_artifacts": {
                "writers": pipeline_writers,
                "consumers": pipeline_consumers,
                "writer_conflicts": writer_conflicts,
                "external_inputs": {
                    key: {
                        **external_inputs.get(key, {}),
                        "runtime_pipeline_consumers": pipeline_consumers.get(key, []),
                        "broader_system_consumers": broader_system_consumers.get(key, []),
                    }
                    for key in sorted(set(consumed_external_inputs) | set(broader_consumed_external_inputs))
                },
                "declared_but_unconsumed_anywhere": declared_but_unconsumed_anywhere,
                "invalid_broader_system_consumers": invalid_broader_system_consumers,
                "unowned_consumed_artifacts": unowned_consumed_artifacts,
            },
        },
        "attention": {
            "layers": layer_attention,
            "spine": spine_attention,
            "unowned_consumed_artifacts_sample": unowned_consumed_artifacts[:40],
            "consumed_external_inputs": consumed_external_inputs,
            "broader_consumed_external_inputs": broader_consumed_external_inputs,
            "declared_but_unconsumed_anywhere": declared_but_unconsumed_anywhere,
            "invalid_broader_system_consumers": invalid_broader_system_consumers,
        },
    }
    save_json_atomic(RAW_OUTPUT_PATH, payload)
    save_text_atomic(REPORT_OUTPUT_PATH, render_markdown(payload))
    return payload


def render_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# System Connectivity Map",
        "",
        "This generated map connects SAGE's existing spine, layer, pipeline, artifact and release-proof evidence.",
        "It is an evidence map, not a replacement for local registries.",
        "",
        "## Summary",
        "",
        f"- status: `{summary.get('status')}`",
        f"- source_layers: `{summary.get('source_layers')}`",
        f"- pipeline_steps: `{summary.get('pipeline_steps')}`",
        f"- spine_nodes: `{summary.get('spine_nodes')}`",
        f"- artifact_writers: `{summary.get('artifact_writers')}`",
        f"- artifact_consumers: `{summary.get('artifact_consumers')}`",
        f"- external_input_artifacts: `{summary.get('external_input_artifacts')}`",
        f"- consumed_external_inputs: `{summary.get('consumed_external_inputs')}`",
        f"- broader_consumed_external_inputs: `{summary.get('broader_consumed_external_inputs')}`",
        f"- declared_but_unconsumed_anywhere: `{summary.get('declared_but_unconsumed_anywhere')}`",
        f"- writer_conflicts: `{summary.get('writer_conflicts')}`",
        f"- layer_attention_items: `{summary.get('layer_attention_items')}`",
        f"- spine_attention_items: `{summary.get('spine_attention_items')}`",
        f"- unowned_consumed_artifacts: `{summary.get('unowned_consumed_artifacts')}`",
        "",
        "## Spine Nodes",
        "",
        "| Node | Role | Release Step | Missing Links |",
        "|---|---|---|---|",
    ]
    for node in (payload.get("connectivity", {}).get("spine_nodes", []) or []):
        missing = node.get("missing_links") or {}
        lines.append(
            f"| `{node.get('id')}` | `{node.get('role')}` | `{node.get('release_proof_step')}` | "
            f"{', '.join(f'`{key}`' for key in missing) or '-'} |"
        )
    lines.extend(["", "## Layer Connectivity Attention", ""])
    for item in (payload.get("attention", {}).get("layers", []) or [])[:40]:
        lines.append(f"- `{item.get('layer')}`: `{json.dumps(item.get('warnings'), ensure_ascii=False, sort_keys=True)}`")
    if not payload.get("attention", {}).get("layers"):
        lines.append("- none")
    lines.extend(["", "## Unowned Consumed Artifact Sample", ""])
    sample = payload.get("attention", {}).get("unowned_consumed_artifacts_sample", []) or []
    if sample:
        for artifact in sample:
            lines.append(f"- `{artifact}`")
    else:
        lines.append("- none")
    lines.extend(["", "## Consumed External Inputs", ""])
    external_inputs = payload.get("attention", {}).get("consumed_external_inputs", []) or []
    if external_inputs:
        for artifact in external_inputs:
            lines.append(f"- `{artifact}`")
    else:
        lines.append("- none")
    return "\n".join(lines) + "\n"


def main() -> int:
    payload = build_connectivity_map()
    summary = payload["summary"]
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
