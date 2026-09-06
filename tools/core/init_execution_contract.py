from __future__ import annotations

from statistics import median
from typing import Any

from tools.core.config import CONFIG_DIR, RAW_DIR, ROOT
from tools.core.json_io import load_json_file, load_json_object_strict
from tools.core.projects_registry import resolve_runtime_projects


CONTRACT_PATH = CONFIG_DIR / "cli_command_contract.json"
TELEMETRY_PATH = RAW_DIR / "telemetry_traces.json"


def init_execution_contract() -> dict[str, Any]:
    contract = load_json_object_strict(CONTRACT_PATH, label="CLI command contract")
    init_contract = contract.get("init_execution")
    if not isinstance(init_contract, dict):
        raise ValueError("CLI command contract is missing init_execution.")
    return init_contract


def init_mode_contract(mode: str) -> dict[str, Any]:
    contract = init_execution_contract()
    modes = contract.get("modes")
    default_mode = str(contract.get("default_mode") or "").strip().lower()
    if not default_mode:
        raise ValueError("CLI init execution contract is missing default_mode.")
    normalized = str(mode or default_mode).strip().lower()
    selected = modes.get(normalized) if isinstance(modes, dict) else None
    if not isinstance(selected, dict):
        raise ValueError(f"CLI init execution contract has no mode '{normalized}'.")
    return {"id": normalized, **selected}


def installation_proof_init_mode(level: str) -> str:
    contract = init_execution_contract()
    mapping = contract.get("installation_proof_init_modes")
    normalized = str(level or "").strip().lower()
    if not normalized:
        raise ValueError("Installation-proof level is required for init mode selection.")
    selected = mapping.get(normalized) if isinstance(mapping, dict) else None
    if not selected:
        raise ValueError(f"CLI init execution contract has no installation-proof mode for '{normalized}'.")
    return str(selected)


def _local_duration_evidence(mode: dict[str, Any]) -> dict[str, Any]:
    identifier = str(mode.get("telemetry_identifier") or "")
    traces_payload = load_json_file(TELEMETRY_PATH, {})
    traces = traces_payload.get("traces", []) if isinstance(traces_payload, dict) else []
    durations = [
        int(row.get("execution_ms") or 0) / 1000.0
        for row in traces
        if isinstance(row, dict)
        and str(row.get("type") or "") == "subprocess_execution"
        and str(row.get("identifier") or "") == identifier
        and int(row.get("execution_ms") or 0) > 0
    ]
    if not durations:
        return {
            "status": "unavailable",
            "basis": "no_matching_local_runtime_samples",
            "sample_count": 0,
            "median_seconds": None,
        }
    sample_limit = max(1, int(init_execution_contract()["duration_sample_limit"]))
    selected = durations[-sample_limit:]
    return {
        "status": "available",
        "basis": "local_exact_mode_median",
        "sample_count": len(selected),
        "median_seconds": round(float(median(selected)), 1),
    }


def build_init_preflight(mode: str) -> dict[str, Any]:
    selected = init_mode_contract(mode)
    try:
        projects = resolve_runtime_projects(ROOT)
        project_count: int | None = len(projects)
        project_basis = "runtime_project_registry"
    except Exception as exc:
        project_count = None
        project_basis = f"unavailable:{type(exc).__name__}"
    return {
        "mode": selected["id"],
        "analysis_profile": selected.get("analysis_profile"),
        "force": bool(selected.get("force")),
        "cost_tier": selected.get("cost_tier"),
        "project_count": project_count,
        "project_count_basis": project_basis,
        "generated_scope": selected.get("generated_scope"),
        "duration": _local_duration_evidence(selected),
        "operator_guidance": selected.get("operator_guidance"),
    }


def render_init_preflight(mode: str) -> list[str]:
    preflight = build_init_preflight(mode)
    duration = preflight["duration"]
    project_count = preflight["project_count"]
    project_text = str(project_count) if project_count is not None else "unknown"
    duration_text = (
        f"median_seconds={duration['median_seconds']} samples={duration['sample_count']}"
        if duration["status"] == "available"
        else f"unknown basis={duration['basis']}"
    )
    return [
        (
            f"[INIT PREFLIGHT] mode={preflight['mode']} profile={preflight['analysis_profile']} "
            f"force={str(preflight['force']).lower()} cost_tier={preflight['cost_tier']} "
            f"projects={project_text} project_basis={preflight['project_count_basis']}"
        ),
        f"[INIT PREFLIGHT] expected_duration={duration_text}",
        f"[INIT PREFLIGHT] generated_scope={preflight['generated_scope']}",
        f"[INIT PREFLIGHT] guidance={preflight['operator_guidance']}",
    ]
