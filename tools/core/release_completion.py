from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.core.json_io import load_json_file


def completion_gap(requirement: dict[str, Any], summary: dict[str, Any]) -> dict[str, Any] | None:
    field = str(requirement.get("summary_field") or "")
    expected = requirement.get("expected_value")
    observed = summary.get(field) if field else None
    if field and observed == expected:
        return None
    return {
        "id": str(requirement.get("id") or "incomplete_required_work"),
        "title": str(requirement.get("title") or "Required release work is incomplete."),
        "priority": str(requirement.get("priority") or "P0.5"),
        "owner": str(requirement.get("owner") or "SAGE/human"),
        "evidence": {
            "artifact": str(requirement.get("artifact") or ""),
            "summary_field": field,
            "expected_value": expected,
            "observed_value": observed,
        },
        "next_step": str(requirement.get("next_step") or "Complete the required work before release sealing."),
    }


def completion_gaps(contract: dict[str, Any], raw_dir: Path) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    for requirement in contract.get("completion_requirements", []):
        if not isinstance(requirement, dict):
            continue
        artifact = str(requirement.get("artifact") or "")
        payload = load_json_file(raw_dir / f"{artifact}.json", {})
        summary = payload.get("summary", {}) if isinstance(payload, dict) else {}
        gap = completion_gap(requirement, summary if isinstance(summary, dict) else {})
        if gap:
            gaps.append(gap)
    return gaps
