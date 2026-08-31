from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tools.core.analysis_snapshot_lineage import write_current_atlas_lineage
from tools.core.atlas_io import load_atlas_data
from tools.core.config import CODE_MAPS_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file
from tools.core.logger import logger


def _has_playwright_config() -> bool:
    for name in ("playwright.config.ts", "playwright.config.js", "playwright.config.mjs"):
        if (CODE_MAPS_DIR.parent / name).exists() or (CODE_MAPS_DIR / name).exists():
            return True
    return False


def _execution_command(spec_path: str) -> str:
    normalized = str(spec_path or "").replace("\\", "/")
    return f"npx playwright test {normalized}" if normalized else "npx playwright test"


def run_ui_smoke_execution_report() -> dict[str, Any]:
    logger.info("Building UI smoke execution readiness report...")
    atlas = load_atlas_data()
    smoke = load_json_file(RAW_DIR / "ui_smoke_specs.json", {})
    specs = smoke.get("specs", []) if isinstance(smoke, dict) else []
    has_config = _has_playwright_config()

    rows: list[dict[str, Any]] = []
    for spec in specs if isinstance(specs, list) else []:
        if not isinstance(spec, dict):
            continue
        spec_path = CODE_MAPS_DIR / str(spec.get("spec_path") or "")
        executable = has_config and spec_path.exists()
        rows.append(
            {
                "name": spec.get("name") or spec.get("candidate"),
                "candidate": spec.get("candidate"),
                "source": spec.get("source"),
                "target_path": spec.get("target_path"),
                "source_contract_file": spec.get("source_contract_file"),
                "route": spec.get("suggested_route"),
                "spec_path": spec.get("spec_path"),
                "spec_exists": spec_path.exists(),
                "status": "ready_to_run" if executable else "generated_not_executed",
                "execution_command": _execution_command(str(spec.get("spec_path") or "")),
                "browser_gate": "required" if spec.get("required") or spec.get("recommended_gate") == "browser_smoke_required" else "advisory",
                "reason": "playwright_config_detected" if has_config else "no_local_playwright_config_for_codemaps_generated_specs",
            }
        )

    payload = {
        "meta": {"kind": "ui_smoke_execution", "version": "v1"},
        "summary": {
            "specs": len(rows),
            "ready_to_run": sum(1 for row in rows if row["status"] == "ready_to_run"),
            "generated_not_executed": sum(1 for row in rows if row["status"] == "generated_not_executed"),
            "playwright_config_detected": has_config,
            "browser_gate_required": sum(1 for row in rows if row.get("browser_gate") == "required"),
            "next_action": (
                "run_ready_specs_with_playwright"
                if has_config and any(row["status"] == "ready_to_run" for row in rows)
                else "add_or_point_to_playwright_config_before_browser_execution"
            ),
        },
        "runs": rows,
    }
    save_json_atomic(RAW_DIR / "ui_smoke_execution.json", payload)
    write_current_atlas_lineage(
        artifact_id="ui_smoke_execution",
        producer="tools.engines.ui_smoke_execution_report",
        artifact_payload=payload,
        atlas=atlas,
        dependency_payloads={"ui_smoke_specs": smoke},
    )

    lines = [
        "# UI Smoke Execution Readiness",
        "",
        f"- Specs: `{payload['summary']['specs']}`",
        f"- Ready to run: `{payload['summary']['ready_to_run']}`",
        f"- Generated/not executed: `{payload['summary']['generated_not_executed']}`",
        f"- Playwright config detected: `{payload['summary']['playwright_config_detected']}`",
        f"- Browser gate required: `{payload['summary']['browser_gate_required']}`",
        f"- Next action: `{payload['summary']['next_action']}`",
        "",
        "| Name | Route | Status | Browser Gate | Command | Reason |",
        "|---|---|---|---|---|---|",
    ]
    for row in rows[:120]:
        lines.append(
            f"| `{row['name']}` | `{row['route']}` | `{row['status']}` | `{row['browser_gate']}` | "
            f"`{row['execution_command']}` | {row['reason']} |"
        )
    save_text_atomic(REPORTS_DIR / "ui_smoke_execution.md", "\n".join(lines) + "\n")
    return payload


if __name__ == "__main__":
    run_ui_smoke_execution_report()
