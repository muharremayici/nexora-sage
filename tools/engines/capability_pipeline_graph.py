from __future__ import annotations

from typing import Any

from tools.core.capability_registry import load_capability_registry, summarize_capabilities
from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file
from tools.core.logger import logger


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def run_capability_pipeline_graph() -> dict[str, Any]:
    logger.info("Building capability pipeline graph...")
    capability_summary = summarize_capabilities(load_capability_registry())
    pipeline = load_json_file(RAW_DIR / "pipeline_step_registry.json", {})
    steps = _as_list(pipeline.get("steps")) if isinstance(pipeline, dict) else []

    rows = []
    total_bound_steps = 0
    for capability in capability_summary.get("capabilities", []):
        cap_id = str(capability.get("id") or "")
        bound_steps = [
            step for step in steps
            if cap_id in {str(item) for item in _as_list(step.get("capabilities"))}
        ]
        total_bound_steps += len(bound_steps)
        reads = sorted({
            str(item)
            for step in bound_steps
            for item in _as_list(step.get("reads_artifacts"))
        })
        writes = sorted({
            str(item)
            for step in bound_steps
            for item in _as_list(step.get("writes_artifacts"))
        })
        scheduler_classes = sorted({
            str((step.get("execution_contract") or {}).get("scheduler_class") or "unknown")
            for step in bound_steps
        })
        declared_artifacts = {str(item) for item in _as_list(capability.get("artifacts"))}
        pipeline_artifacts = set(reads) | set(writes)
        rows.append(
            {
                "id": cap_id,
                "title": capability.get("title"),
                "domain": capability.get("domain"),
                "language_scope": capability.get("language_scope", []),
                "maturity": capability.get("maturity"),
                "claim_boundary": capability.get("claim_boundary"),
                "validators": capability.get("validators", []),
                "declared_artifacts": sorted(declared_artifacts),
                "pipeline_reads": reads,
                "pipeline_writes": writes,
                "pipeline_scheduler_classes": scheduler_classes,
                "declared_artifacts_without_pipeline_touch": sorted(declared_artifacts - pipeline_artifacts),
                "pipeline_artifacts_outside_contract": sorted(pipeline_artifacts - declared_artifacts),
                "steps": [
                    {
                        "index": step.get("index"),
                        "name": step.get("name"),
                        "category": step.get("category"),
                        "reads_artifacts": step.get("reads_artifacts", []),
                        "writes_artifacts": step.get("writes_artifacts", []),
                        "execution_contract": step.get("execution_contract", {}),
                    }
                    for step in bound_steps
                ],
            }
        )

    payload = {
        "meta": {
            "kind": "capability_pipeline_graph",
            "version": "v1",
            "source_artifacts": [
                "config/capability_registry.json",
                "output/.raw/pipeline_step_registry.json",
            ],
        },
        "summary": {
            "status": "PASS",
            "capabilities": len(rows),
            "pipeline_steps": len(steps),
            "capability_bound_step_links": total_bound_steps,
            "capabilities_without_pipeline_steps": [
                row["id"] for row in rows if not row["steps"]
            ],
        },
        "capabilities": rows,
    }
    save_json_atomic(RAW_DIR / "capability_pipeline_graph.json", payload)
    _write_report(payload)
    return payload


def _write_report(payload: dict[str, Any]) -> None:
    summary = payload.get("summary", {})
    lines = [
        "# Capability Pipeline Graph",
        "",
        "Capability-centered map of pipeline steps, trusted artifacts and claim boundaries.",
        "",
        f"- status: `{summary.get('status')}`",
        f"- capabilities: `{summary.get('capabilities')}`",
        f"- pipeline_steps: `{summary.get('pipeline_steps')}`",
        f"- capability_bound_step_links: `{summary.get('capability_bound_step_links')}`",
        f"- capabilities_without_pipeline_steps: `{summary.get('capabilities_without_pipeline_steps')}`",
        "",
        "| Capability | Domain | Steps | Writes | Claim Boundary |",
        "|---|---|---:|---|---|",
    ]
    for capability in payload.get("capabilities", []):
        writes = ", ".join(capability.get("pipeline_writes", []) or [])
        schedulers = ", ".join(capability.get("pipeline_scheduler_classes", []) or [])
        claim = str(capability.get("claim_boundary") or "").replace("|", "\\|")
        lines.append(
            f"| `{capability.get('id')}` | `{capability.get('domain')}` | "
            f"{len(capability.get('steps', []) or [])} | `{writes}`<br/>scheduler: `{schedulers}` | {claim} |"
        )
    save_text_atomic(REPORTS_DIR / "capability_pipeline_graph.md", "\n".join(lines) + "\n")


if __name__ == "__main__":
    run_capability_pipeline_graph()
