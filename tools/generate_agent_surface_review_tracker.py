#!/usr/bin/env python3
"""Generate a review tracker for target-repository agent-facing packet families."""

from __future__ import annotations

import sys
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.agent_surface_seal_contract import load_agent_surface_seal_contract
from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file


OUTPUT_JSON = RAW_DIR / "agent_surface_review_tracker.json"
OUTPUT_MD = REPORTS_DIR / "agent_surface_review_tracker.md"
WORK_ITEM_REGISTRY_PATH = ROOT / "config" / "sage_work_item_registry.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sample_map(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = payload.get("samples", []) if isinstance(payload, dict) else []
    return {str(row["name"]): row for row in rows if isinstance(row, dict) and row.get("name")}


def _review_map(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = payload.get("reviews", []) if isinstance(payload, dict) else []
    return {str(row["name"]): row for row in rows if isinstance(row, dict) and row.get("name")}


def _manual_family_map(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = payload.get("families", []) if isinstance(payload, dict) else []
    return {str(row["id"]): row for row in rows if isinstance(row, dict) and row.get("id")}


def _manual_sample_map(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for family in payload.get("families", []) if isinstance(payload, dict) else []:
        if not isinstance(family, dict):
            continue
        for row in family.get("sample_excerpts", []):
            if isinstance(row, dict) and row.get("name"):
                result[str(row["name"])] = row
    return result


def _matrix_family_counts(payload: dict[str, Any]) -> dict[str, int]:
    summary = payload.get("summary", {}) if isinstance(payload, dict) else {}
    counts = summary.get("family_counts", {}) if isinstance(summary, dict) else {}
    if not isinstance(counts, dict):
        return {}
    result: dict[str, int] = {}
    for key, value in counts.items():
        try:
            result[str(key)] = int(value)
        except (TypeError, ValueError):
            result[str(key)] = 0
    return result


def _family_followups(family_id: str, followups: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for row in followups:
        if not isinstance(row, dict) or str(row.get("family") or "") != family_id:
            continue
        rows.append(
            {
                "id": str(row.get("id") or ""),
                "priority": str(row.get("priority") or ""),
                "status": str(row.get("status") or ""),
                "summary": str(row.get("summary") or row.get("problem") or ""),
                "next_action": str(row.get("next_action") or ""),
            }
        )
    return rows


def _agent_surface_work_items() -> list[dict[str, Any]]:
    payload = load_json_file(WORK_ITEM_REGISTRY_PATH, {})
    rows = payload.get("work_items", []) if isinstance(payload, dict) else []
    return [
        row
        for row in rows
        if isinstance(row, dict)
        and str(row.get("source") or "") == "agent_surface_review_tracker"
        and str(row.get("status") or "").lower() != "closed"
    ]


def _variant_kind(name: str) -> str:
    lowered = name.lower()
    if "missing_target" in lowered:
        return "fail_closed_missing_target"
    if "external_target" in lowered and "invalid" in lowered:
        return "fail_closed_invalid_external_target"
    if "negative" in lowered:
        return "negative_policy_probe"
    if "watchdog_session_missing" in lowered:
        return "no_active_session"
    if "path_resolution" in lowered:
        return "path_resolution"
    if lowered.endswith("_filled") or "_filled" in lowered:
        return "filled"
    if "roundtrip" in lowered:
        return "roundtrip_followup"
    if lowered.endswith("_json"):
        return "json_projection"
    if "all_projects" in lowered:
        return "all_projects_scope"
    if "supporting" in lowered:
        return "supporting_projection"
    if "missing" in lowered or "invalid" in lowered:
        return "fail_closed"
    return "default"


def _package_key(name: str) -> str:
    suffixes = [
        "_missing_target",
        "_path_resolution",
        "_negative",
        "_filled",
        "_json",
        "_supporting",
        "_invalid",
    ]
    key = name
    for suffix in suffixes:
        if key.endswith(suffix):
            key = key[: -len(suffix)]
            break
    if key.startswith("work_queue_roundtrip_"):
        return "work_queue_roundtrip"
    if key == "domain_ui_all_projects_work_queue":
        return "domain_ui_work_queue"
    return key


def _body_for_sample(sample: dict[str, Any]) -> str:
    return str(sample.get("body") or sample.get("excerpt") or "")


def _extract_target_files(body: str) -> list[str]:
    values: list[str] = []
    for match in re.finditer(r"^\s*(?:target_file|file):\s*\"?([^\"\n]+)\"?\s*$", body, re.MULTILINE):
        value = match.group(1).replace("\\", "/").strip().strip("/")
        if value and "::" not in value and value not in values:
            values.append(value)
    return values


def _extract_symbols(body: str) -> list[str]:
    values: list[str] = []
    for match in re.finditer(r"^\s*(?:symbol|symbol_name|name):\s*\"?([^\"\n]+)\"?\s*$", body, re.MULTILINE):
        value = match.group(1).strip().strip('"').strip("'")
        if value and value not in values:
            values.append(value)
    return values


def _sample_status(
    name: str,
    samples: dict[str, dict[str, Any]],
    reviews: dict[str, dict[str, Any]],
    manual_samples: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    review = reviews.get(name, {})
    sample = samples.get(name, {})
    checks = review.get("checks", {}) if isinstance(review, dict) else {}
    body = _body_for_sample(sample)
    target_files = _extract_target_files(body)
    symbols = _extract_symbols(body)
    manual = manual_samples.get(name, {})
    return {
        "name": name,
        "package": _package_key(name),
        "variant_kind": _variant_kind(name),
        "sample_present": name in samples,
        "automated_review_present": name in reviews,
        "automated_passed": bool(review.get("passed")) if isinstance(review, dict) else False,
        "selected_for_manual_review": name in manual_samples,
        "manual_review_status": str(manual.get("agent_manual_review_status") or "not_selected"),
        "manual_evidence_status": str(manual.get("manual_evidence_status") or "not_selected"),
        "estimated_tokens": sample.get("estimated_tokens") or sample.get("token_estimate"),
        "target_files": target_files[:5],
        "target_files_omitted": max(0, len(target_files) - 5),
        "symbols": symbols[:5],
        "symbols_omitted": max(0, len(symbols) - 5),
        "has_target_file": bool(target_files),
        "has_target_ref": "target_ref:" in body,
        "target_visibility_check": bool(checks.get("path_contract_defines_analysis_root")) if isinstance(checks, dict) else False,
    }


def _coverage_status(
    family: dict[str, Any],
    sample_rows: list[dict[str, Any]],
    manual_row: dict[str, Any],
    blocking_followups: list[dict[str, str]],
) -> str:
    if blocking_followups:
        return "blocked_by_followup"
    if family.get("required", True) and any(not row["automated_review_present"] for row in sample_rows):
        return "missing_review_sample"
    if sample_rows and all(row["automated_passed"] for row in sample_rows):
        manual_status = str(manual_row.get("agent_manual_review_status") or "")
        if manual_status == "reviewed_by_codex_agent":
            return "agent_reviewed"
        return "automated_passed_needs_agent_review"
    return "needs_review"


def build_tracker() -> dict[str, Any]:
    contract = load_agent_surface_seal_contract()
    quality = load_json_file(RAW_DIR / "agent_surface_quality_review.json", {})
    manual = load_json_file(RAW_DIR / "agent_surface_manual_seal_pack.json", {})
    matrix = load_json_file(RAW_DIR / "mcp_surface_command_matrix.json", {})

    samples = _sample_map(quality if isinstance(quality, dict) else {})
    reviews = _review_map(quality if isinstance(quality, dict) else {})
    manual_families = _manual_family_map(manual if isinstance(manual, dict) else {})
    manual_samples = _manual_sample_map(manual if isinstance(manual, dict) else {})
    matrix_counts = _matrix_family_counts(matrix if isinstance(matrix, dict) else {})

    policy = contract.get("review_tracker_policy", {}) if isinstance(contract, dict) else {}
    blocking_priorities = {str(item) for item in policy.get("blocking_priorities", ["P0", "P0.5"])}
    followups = _agent_surface_work_items()

    families: list[dict[str, Any]] = []
    missing_samples: list[dict[str, str]] = []
    blocking_followups: list[dict[str, str]] = []
    declared_sample_names: set[str] = set()

    for family in contract.get("families", []) if isinstance(contract, dict) else []:
        if not isinstance(family, dict) or not family.get("id"):
            continue
        family_id = str(family["id"])
        declared_samples = [str(name) for name in family.get("samples", []) if str(name).strip()]
        declared_sample_names.update(declared_samples)
        sample_rows = [_sample_status(name, samples, reviews, manual_samples) for name in declared_samples]
        family_followups = _family_followups(family_id, followups)
        family_blockers = [
            row
            for row in family_followups
            if row["priority"] in blocking_priorities and row["status"] != "closed"
        ]
        family_targets = sorted({
            target
            for row in sample_rows
            for target in row.get("target_files", [])
        })
        family_symbols = sorted({
            symbol
            for row in sample_rows
            for symbol in row.get("symbols", [])
        })
        blocking_followups.extend(family_blockers)
        for row in sample_rows:
            if not row["automated_review_present"]:
                missing_samples.append({"family": family_id, "sample": row["name"]})

        manual_row = manual_families.get(family_id, {})
        families.append(
            {
                "id": family_id,
                "label": str(family.get("label") or family_id),
                "required": bool(family.get("required", True)),
                "role": str(family.get("role") or ""),
                "declared_samples": declared_samples,
                "packages": sorted({row["package"] for row in sample_rows}),
                "variant_kinds": sorted({row["variant_kind"] for row in sample_rows}),
                "variant_counts": {
                    variant: sum(1 for row in sample_rows if row["variant_kind"] == variant)
                    for variant in sorted({row["variant_kind"] for row in sample_rows})
                },
                "distinct_target_files": family_targets[:12],
                "distinct_target_files_omitted": max(0, len(family_targets) - 12),
                "distinct_target_file_count": len(family_targets),
                "distinct_symbols": family_symbols[:12],
                "distinct_symbols_omitted": max(0, len(family_symbols) - 12),
                "distinct_symbol_count": len(family_symbols),
                "automated_reviewed_samples": [row["name"] for row in sample_rows if row["automated_review_present"]],
                "manually_reviewed_representative_samples": [
                    row["name"]
                    for row in sample_rows
                    if row["manual_review_status"] == "reviewed_by_codex_agent"
                ],
                "missing_samples": [row["name"] for row in sample_rows if not row["automated_review_present"]],
                "sample_statuses": sample_rows,
                "manual_review_status": str(manual_row.get("agent_manual_review_status") or "unknown"),
                "human_seal_status": str(manual_row.get("human_seal_status") or "unknown"),
                "matrix_family_tool_count": matrix_counts.get(family_id, 0),
                "open_followups": family_followups,
                "coverage_status": _coverage_status(family, sample_rows, manual_row, family_blockers),
            }
        )

    open_followups = [
        {
            "id": str(row.get("id") or ""),
            "priority": str(row.get("priority") or ""),
            "family": str(row.get("family") or ""),
            "status": str(row.get("status") or ""),
            "summary": str(row.get("summary") or row.get("problem") or ""),
            "next_action": str(row.get("next_action") or ""),
        }
        for row in followups
        if str(row.get("status") or "") != "closed"
    ]
    untracked_samples = sorted(set(samples) - declared_sample_names)
    status = "AGENT_MANUAL_REVIEW_REQUIRED" if any(
        row["coverage_status"] == "automated_passed_needs_agent_review" for row in families
    ) else "PASS"
    if blocking_followups or missing_samples or untracked_samples:
        status = "BLOCKED"
    distinct_review_targets = sorted({
        target
        for row in families
        for target in row.get("distinct_target_files", [])
    })

    return {
        "meta": {
            "kind": "agent_surface_review_tracker",
            "version": "1.0.0",
            "generated_at": _utc_now(),
            "generator": "tools.generate_agent_surface_review_tracker",
        },
        "summary": {
            "status": status,
            "families": len(families),
            "declared_samples": sum(len(row["declared_samples"]) for row in families),
            "automated_reviewed_samples": sum(len(row["automated_reviewed_samples"]) for row in families),
            "manually_reviewed_representative_samples": sum(
                len(row["manually_reviewed_representative_samples"]) for row in families
            ),
            "distinct_review_target_files": len(distinct_review_targets),
            "missing_samples": len(missing_samples),
            "untracked_samples": len(untracked_samples),
            "open_followups": len(open_followups),
            "blocking_followups": len(blocking_followups),
            "human_seal_status": (manual.get("summary", {}) if isinstance(manual, dict) else {}).get("human_seal_status"),
            "next_review_family": next(
                (row["id"] for row in families if row["coverage_status"] != "agent_reviewed"),
                next((row["family"] for row in open_followups if row["priority"] == "P1"), ""),
            ),
        },
        "families": families,
        "missing_samples": missing_samples,
        "untracked_samples": untracked_samples,
        "open_followups": open_followups,
        "evidence": {
            "contract": "config/agent_surface_seal_contract.json",
            "work_item_registry": "config/sage_work_item_registry.json",
            "quality_review": "output/.raw/agent_surface_quality_review.json",
            "manual_seal_pack": "output/.raw/agent_surface_manual_seal_pack.json",
            "mcp_surface_command_matrix": "output/.raw/mcp_surface_command_matrix.json",
        },
    }


def render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# Agent Surface Review Tracker",
        "",
        f"- status: `{summary.get('status')}`",
        f"- families: `{summary.get('families')}`",
        f"- declared_samples: `{summary.get('declared_samples')}`",
        f"- automated_reviewed_samples: `{summary.get('automated_reviewed_samples')}`",
        f"- manually_reviewed_representative_samples: `{summary.get('manually_reviewed_representative_samples')}`",
        f"- distinct_review_target_files: `{summary.get('distinct_review_target_files')}`",
        f"- missing_samples: `{summary.get('missing_samples')}`",
        f"- untracked_samples: `{summary.get('untracked_samples')}`",
        f"- open_followups: `{summary.get('open_followups')}`",
        f"- blocking_followups: `{summary.get('blocking_followups')}`",
        f"- human_seal_status: `{summary.get('human_seal_status')}`",
        f"- next_review_family: `{summary.get('next_review_family') or 'none'}`",
        "",
        "## Families",
        "",
        "| Family | Coverage | Samples | Targets | Followups |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    for family in payload.get("families", []):
        variant_text = ", ".join(family.get("variant_kinds", [])) or "none"
        lines.append(
            "| {id} | `{coverage}` | {reviewed}/{declared} | {targets} | {followups} |".format(
                id=family.get("id"),
                coverage=family.get("coverage_status"),
                reviewed=len(family.get("manually_reviewed_representative_samples", [])),
                declared=len(family.get("declared_samples", [])),
                targets=family.get("distinct_target_file_count", 0),
                followups=len(family.get("open_followups", [])),
            )
        )
        lines.append(f"  - variants: `{variant_text}`")
        target_text = ", ".join(family.get("distinct_target_files", [])[:5]) or "none"
        omitted = int(family.get("distinct_target_files_omitted") or 0)
        if omitted:
            target_text += f", ... {omitted} omitted"
        lines.append(f"  - targets: `{target_text}`")

    lines.extend(["", "## Open Followups", ""])
    followups = payload.get("open_followups", [])
    if not followups:
        lines.append("- none")
    for row in followups:
        lines.append(
            f"- `{row.get('priority')}` `{row.get('id')}` ({row.get('family')}): {row.get('summary')}"
        )
        if row.get("next_action"):
            lines.append(f"  - next_action: {row.get('next_action')}")

    lines.extend(["", "## Untracked Samples", ""])
    untracked = payload.get("untracked_samples", [])
    if not untracked:
        lines.append("- none")
    for name in untracked:
        lines.append(f"- `{name}`")

    lines.extend(["", "## Evidence", ""])
    for key, value in payload.get("evidence", {}).items():
        lines.append(f"- {key}: `{value}`")
    return "\n".join(lines) + "\n"


def main() -> int:
    payload = build_tracker()
    save_json_atomic(OUTPUT_JSON, payload)
    save_text_atomic(OUTPUT_MD, render_report(payload))
    summary = payload["summary"]
    print(
        "agent surface review tracker: "
        f"{summary['status']} "
        f"families={summary['families']} "
        f"automated_reviewed_samples={summary['automated_reviewed_samples']}/{summary['declared_samples']} "
        f"manually_reviewed_representative_samples={summary['manually_reviewed_representative_samples']} "
        f"open_followups={summary['open_followups']} "
        f"blocking_followups={summary['blocking_followups']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
