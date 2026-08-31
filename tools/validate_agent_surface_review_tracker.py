#!/usr/bin/env python3
"""Validate the generated target-repository agent surface review tracker."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.agent_surface_seal_contract import load_agent_surface_seal_contract
from tools.core.config import RAW_DIR, save_json_atomic
from tools.core.json_io import load_json_file


OUTPUT_JSON = RAW_DIR / "agent_surface_review_tracker_validation.json"
TRACKER_PATH = RAW_DIR / "agent_surface_review_tracker.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _status(name: str, passed: bool, detail: Any = None) -> dict[str, Any]:
    return {"name": name, "status": "PASS" if passed else "FAIL", "detail": detail}


def validate() -> dict[str, Any]:
    contract = load_agent_surface_seal_contract()
    tracker = load_json_file(TRACKER_PATH, {})
    policy = contract.get("review_tracker_policy", {}) if isinstance(contract, dict) else {}
    selection_policy = contract.get("manual_pack_selection_policy", {}) if isinstance(contract, dict) else {}
    allowed_statuses = {str(item) for item in policy.get("required_statuses", [])}
    blocking_priorities = {str(item) for item in policy.get("blocking_priorities", ["P0", "P0.5"])}
    min_distinct_targets = int(selection_policy.get("min_distinct_quality_review_targets") or 0)

    contract_family_ids = {
        str(row.get("id"))
        for row in (contract.get("families", []) if isinstance(contract, dict) else [])
        if isinstance(row, dict) and row.get("id")
    }
    tracker_family_ids = {
        str(row.get("id"))
        for row in (tracker.get("families", []) if isinstance(tracker, dict) else [])
        if isinstance(row, dict) and row.get("id")
    }
    followups = tracker.get("open_followups", []) if isinstance(tracker, dict) else []
    followup_rows = [row for row in followups if isinstance(row, dict)]
    invalid_followups = [
        row
        for row in followup_rows
        if not row.get("id")
        or not row.get("family")
        or str(row.get("family")) not in contract_family_ids
        or (allowed_statuses and str(row.get("status")) not in allowed_statuses)
    ]
    open_blockers = [
        row
        for row in followup_rows
        if str(row.get("priority")) in blocking_priorities and str(row.get("status")) != "closed"
    ]
    missing_samples = tracker.get("missing_samples", []) if isinstance(tracker, dict) else []
    untracked_samples = tracker.get("untracked_samples", []) if isinstance(tracker, dict) else []
    summary = tracker.get("summary", {}) if isinstance(tracker, dict) else {}
    family_rows = tracker.get("families", []) if isinstance(tracker, dict) else []
    sample_rows = [
        sample
        for family in family_rows
        if isinstance(family, dict)
        for sample in family.get("sample_statuses", [])
        if isinstance(sample, dict)
    ]
    samples_missing_variant = [
        sample.get("name")
        for sample in sample_rows
        if not sample.get("package") or not sample.get("variant_kind")
    ]
    distinct_target_count = int(summary.get("distinct_review_target_files") or 0)
    pending_manual_families = [
        row.get("id")
        for row in family_rows
        if isinstance(row, dict) and row.get("coverage_status") == "automated_passed_needs_agent_review"
    ]
    selected_manual_samples = [sample for sample in sample_rows if sample.get("selected_for_manual_review")]
    invalid_manual_evidence = [
        sample.get("name")
        for sample in selected_manual_samples
        if sample.get("manual_review_status") == "reviewed_by_codex_agent"
        and sample.get("manual_evidence_status") != "current"
    ]

    checks = [
        _status("tracker_artifact_exists", bool(tracker), str(TRACKER_PATH)),
        _status(
            "all_contract_families_tracked",
            contract_family_ids == tracker_family_ids,
            {
                "missing": sorted(contract_family_ids - tracker_family_ids),
                "extra": sorted(tracker_family_ids - contract_family_ids),
            },
        ),
        _status("declared_samples_have_review_rows", not missing_samples, missing_samples),
        _status("quality_review_samples_are_declared_in_family_contract", not untracked_samples, untracked_samples),
        _status("open_followups_are_well_formed", not invalid_followups, invalid_followups),
        _status("no_open_blocking_followups", not open_blockers, open_blockers),
        _status("samples_have_package_and_variant_inventory", not samples_missing_variant, samples_missing_variant),
        _status(
            "manual_review_status_requires_current_packet_evidence",
            not invalid_manual_evidence,
            invalid_manual_evidence,
        ),
        _status(
            "automated_and_manual_review_counts_are_separate",
            "automated_reviewed_samples" in summary
            and "manually_reviewed_representative_samples" in summary
            and "reviewed_samples" not in summary,
            {
                "automated_reviewed_samples": summary.get("automated_reviewed_samples"),
                "manually_reviewed_representative_samples": summary.get("manually_reviewed_representative_samples"),
            },
        ),
        _status(
            "review_tracker_has_target_diversity",
            distinct_target_count >= min_distinct_targets,
            {
                "distinct_review_target_files": distinct_target_count,
                "min_distinct_quality_review_targets": min_distinct_targets,
            },
        ),
        _status(
            "tracker_status_matches_blockers",
            summary.get("status") == (
                "BLOCKED"
                if open_blockers or missing_samples or untracked_samples
                else "AGENT_MANUAL_REVIEW_REQUIRED"
                if pending_manual_families
                else "PASS"
            ),
            {
                "summary_status": summary.get("status"),
                "open_blockers": len(open_blockers),
                "missing_samples": len(missing_samples),
                "untracked_samples": len(untracked_samples),
                "pending_manual_families": pending_manual_families,
            },
        ),
    ]
    failed = [row for row in checks if row["status"] != "PASS"]
    payload = {
        "meta": {
            "kind": "agent_surface_review_tracker_validation",
            "version": "1.0.0",
            "generated_at": _utc_now(),
            "generator": "tools.validate_agent_surface_review_tracker",
        },
        "summary": {
            "status": "PASS" if not failed else "FAIL",
            "checks": len(checks),
            "failed": len(failed),
            "open_followups": len(followup_rows),
            "blocking_followups": len(open_blockers),
            "missing_samples": len(missing_samples),
            "untracked_samples": len(untracked_samples),
        },
        "checks": checks,
    }
    save_json_atomic(OUTPUT_JSON, payload)
    return payload


def main() -> int:
    payload = validate()
    summary = payload["summary"]
    print(
        "agent surface review tracker validation: "
        f"{summary['status']} checks={summary['checks']} failed={summary['failed']} "
        f"open_followups={summary['open_followups']} blocking_followups={summary['blocking_followups']} "
        f"missing_samples={summary['missing_samples']} untracked_samples={summary['untracked_samples']}"
    )
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
