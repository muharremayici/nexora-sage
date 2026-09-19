"""Capability, pipeline and provenance handlers for the MCP composition root."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from tools.core.capability_registry import (
    build_agent_capability_map,
    capabilities_for_artifact,
    get_capability,
    load_capability_registry,
)
from tools.engines.capability_activation_planner import run as run_capability_activation_plan
from tools.engines.capability_registry_report import run_capability_registry_report
from tools.generate_nexora_surface_inventory import run as run_surface_inventory
from tools.validate_engine_signal_contracts import validate_engine_signal_contracts
from tools.validate_pipeline_execution_contract import validate_pipeline_execution_contract


@dataclass(frozen=True)
class CapabilityToolRuntime:
    """Composition-owned paths and adapters used by capability MCP handlers."""

    raw_dir: Path
    reports_dir: Path
    load_json: Callable[[Path], Any]
    read_json_artifact: Callable[[Path, str], str]
    read_text_artifact: Callable[[Path, str], str]
    run_cli: Callable[..., str]


def get_surface_inventory(
    runtime: CapabilityToolRuntime,
    *,
    human_report: bool = False,
    regenerate: bool = False,
) -> str:
    if regenerate:
        run_surface_inventory()
    if human_report:
        return runtime.read_text_artifact(
            runtime.reports_dir / "nexora_surface_inventory.md",
            "Surface inventory report not found.",
        )
    return runtime.read_json_artifact(
        runtime.raw_dir / "nexora_surface_inventory.json",
        "Surface inventory artifact not found.",
    )


def get_capability_registry(
    runtime: CapabilityToolRuntime,
    *,
    human_report: bool = False,
    regenerate: bool = False,
) -> str:
    if regenerate or not (runtime.raw_dir / "capability_registry.json").exists():
        run_capability_registry_report()
    if human_report:
        return runtime.read_text_artifact(
            runtime.reports_dir / "capability_registry.md",
            "Capability registry report not found.",
        )
    return runtime.read_json_artifact(
        runtime.raw_dir / "capability_registry.json",
        "Capability registry artifact not found.",
    )


def get_capability_contract(*, capability_id: str = "", artifact: str = "") -> str:
    registry = load_capability_registry()
    if capability_id:
        capability = get_capability(registry, capability_id)
        if not capability:
            return json.dumps(
                {
                    "status": "not_found",
                    "capability_id": capability_id,
                    "available_capabilities": build_agent_capability_map(registry).get("summary", {}),
                },
                indent=2,
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "status": "found",
                "source": "config/capability_registry.json",
                "capability": capability,
                "agent_guidance": {
                    "artifacts_to_trust": capability.get("artifacts", []),
                    "validators_to_run": capability.get("validators", []),
                    "claim_boundary": capability.get("claim_boundary"),
                    "language_scope": capability.get("language_scope", []),
                    "framework_scope": capability.get("framework_scope", []),
                },
            },
            indent=2,
            ensure_ascii=False,
        )
    if artifact:
        matches = capabilities_for_artifact(registry, artifact)
        return json.dumps(
            {
                "status": "found" if matches else "not_found",
                "artifact": artifact,
                "source": "config/capability_registry.json",
                "capabilities": matches,
                "agent_guidance": [
                    {
                        "capability_id": item.get("id"),
                        "validators_to_run": item.get("validators", []),
                        "claim_boundary": item.get("claim_boundary"),
                    }
                    for item in matches
                ],
            },
            indent=2,
            ensure_ascii=False,
        )
    return json.dumps(build_agent_capability_map(registry), indent=2, ensure_ascii=False)


def get_capability_activation_plan(
    runtime: CapabilityToolRuntime,
    *,
    refresh: bool = False,
    human_report: bool = False,
) -> str:
    if refresh or not (runtime.raw_dir / "capability_activation_plan.json").exists():
        run_capability_activation_plan()
    if human_report:
        return runtime.read_text_artifact(
            runtime.reports_dir / "capability_activation_plan.md",
            "Capability activation plan report not found.",
        )
    return runtime.read_json_artifact(
        runtime.raw_dir / "capability_activation_plan.json",
        "Capability activation plan artifact not found.",
    )


def get_pipeline_execution_contract(
    runtime: CapabilityToolRuntime,
    *,
    refresh: bool = False,
    human_report: bool = False,
) -> str:
    if refresh or not (runtime.raw_dir / "pipeline_execution_contract_validation.json").exists():
        validate_pipeline_execution_contract()
    if human_report:
        return runtime.read_text_artifact(
            runtime.reports_dir / "pipeline_execution_contract_validation.md",
            "Pipeline execution contract report not found.",
        )
    return runtime.read_json_artifact(
        runtime.raw_dir / "pipeline_execution_contract_validation.json",
        "Pipeline execution contract artifact not found.",
    )


def get_pipeline_step_invocation(
    runtime: CapabilityToolRuntime,
    *,
    step: str = "",
    refresh: bool = False,
) -> str:
    if refresh or not (runtime.raw_dir / "pipeline_step_registry.json").exists():
        runtime.run_cli("run", "--list-steps")
    registry = runtime.load_json(runtime.raw_dir / "pipeline_step_registry.json") or {}
    steps = registry.get("steps", []) if isinstance(registry, dict) else []
    steps = [item for item in steps if isinstance(item, dict)]

    if not str(step or "").strip():
        return json.dumps(
            {
                "status": "list_steps",
                "tool": "get_pipeline_step_invocation",
                "available_steps": [
                    {
                        "name": item.get("name"),
                        "slug": item.get("slug"),
                        "category": item.get("category"),
                        "heavy": item.get("heavy"),
                        "full_only": item.get("full_only"),
                    }
                    for item in steps
                ],
            },
            indent=2,
            ensure_ascii=False,
        )

    query = re.sub(r"[^a-z0-9]+", "", str(step or "").lower())
    matches = [
        item
        for item in steps
        if query
        in {
            re.sub(r"[^a-z0-9]+", "", str(item.get("name") or "").lower()),
            re.sub(r"[^a-z0-9]+", "", str(item.get("slug") or "").lower()),
        }
    ]
    if not matches:
        partial = [
            item
            for item in steps
            if query and query in re.sub(r"[^a-z0-9]+", "", str(item.get("name") or "").lower())
        ][:10]
        return json.dumps(
            {
                "status": "not_found",
                "query": step,
                "suggestions": [{"name": item.get("name"), "slug": item.get("slug")} for item in partial],
            },
            indent=2,
            ensure_ascii=False,
        )

    selected = matches[0]
    contract = selected.get("invocation_contract") if isinstance(selected.get("invocation_contract"), dict) else {}
    execution = selected.get("execution_contract") if isinstance(selected.get("execution_contract"), dict) else {}
    return json.dumps(
        {
            "status": "found",
            "step": {
                "name": selected.get("name"),
                "slug": selected.get("slug"),
                "category": selected.get("category"),
                "depends_on": selected.get("depends_on", []),
                "heavy": selected.get("heavy"),
                "full_only": selected.get("full_only"),
            },
            "invocation": contract,
            "execution": {
                "scheduler_class": execution.get("scheduler_class"),
                "parallel_safe_after_dependencies": execution.get("parallel_safe_after_dependencies"),
                "sqlite_writer": execution.get("sqlite_writer"),
                "reasons": execution.get("reasons", []),
            },
        },
        indent=2,
        ensure_ascii=False,
    )


def get_engine_signal_contracts(
    runtime: CapabilityToolRuntime,
    *,
    refresh: bool = False,
    human_report: bool = False,
) -> str:
    if refresh or not (runtime.raw_dir / "engine_signal_contract_validation.json").exists():
        validate_engine_signal_contracts()
    if human_report:
        return runtime.read_text_artifact(
            runtime.reports_dir / "engine_signal_contract_validation.md",
            "Engine signal contract report not found.",
        )
    return runtime.read_json_artifact(
        runtime.raw_dir / "engine_signal_contract_validation.json",
        "Engine signal contract artifact not found.",
    )


def get_artifact_provenance(
    runtime: CapabilityToolRuntime,
    *,
    human_report: bool = False,
) -> str:
    if human_report:
        return runtime.read_text_artifact(
            runtime.reports_dir / "artifact_provenance_index.md",
            "Artifact provenance report not found.",
        )
    return runtime.read_json_artifact(
        runtime.raw_dir / "artifact_provenance_index.json",
        "Artifact provenance artifact not found.",
    )
