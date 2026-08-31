"""Conservatively derive manual-audit micro cursors from their declared order sources."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.config import CONFIG_DIR, RAW_DIR, save_json_atomic
from tools.core.json_io import load_json_object_strict
from tools.core.manual_audit_contract_identity import contract_rows_for_scope


PROGRESS_PATH = CONFIG_DIR / "manual_adversarial_audit_progress.json"
RELEASE_STEPS_PATH = CONFIG_DIR / "release_proof_steps_contract.json"
PIPELINE_REGISTRY_PATH = RAW_DIR / "pipeline_step_registry.json"


def _contracts_for_scope(scope: str) -> list[dict[str, str]] | None:
    return contract_rows_for_scope(
        scope,
        pipeline_registry_path=PIPELINE_REGISTRY_PATH,
        release_steps_path=RELEASE_STEPS_PATH,
    )


def _order_for_scope(scope: str) -> list[str] | None:
    contracts = _contracts_for_scope(scope)
    return None if contracts is None else [row["id"] for row in contracts]


def _project_scope(
    row: dict[str, Any],
    order: list[str],
    current_fingerprints: dict[str, str] | None = None,
) -> dict[str, Any]:
    original_completed = list(dict.fromkeys(str(item) for item in row.get("completed_item_ids", []) if str(item) in order))
    recorded_fingerprints = {
        str(item_id): str(fingerprint)
        for item_id, fingerprint in (row.get("completed_item_fingerprints") or {}).items()
        if str(item_id) in order and str(fingerprint)
    }
    if current_fingerprints is None:
        completed = original_completed
        stale_completed: list[str] = []
    else:
        completed = [
            item
            for item in original_completed
            if recorded_fingerprints.get(item) == current_fingerprints.get(item)
        ]
        stale_completed = [item for item in original_completed if item not in completed]
    completed = [item for item in order if item in completed]
    completed_fingerprints = {
        item: current_fingerprints[item]
        for item in completed
        if current_fingerprints is not None and item in current_fingerprints
    }
    prior_superseded = list(dict.fromkeys(str(item) for item in row.get("superseded_completed_item_ids", []) if str(item) in order))
    missing = [item for item in order if item not in completed]
    status = str(row.get("status") or "not_started")
    changed = False
    if status == "complete" and missing:
        status = "in_progress"
        changed = True
    elif not missing and order and status != "complete":
        status = "complete"
        changed = True
    current = str(row.get("current_item_id") or "")
    if status == "in_progress" and missing:
        current = missing[0]
    elif status == "complete" and order:
        current = order[-1]
    if current not in order and order:
        current = missing[0] if missing else order[-1]
    index = order.index(current) if current in order else -1
    superseded = [
        item
        for item in order
        if item in set([*prior_superseded, *stale_completed]) and item not in completed
    ]
    remaining_after_current = [item for item in missing if item != current]
    next_ids = [] if status == "complete" else remaining_after_current[:2]
    projected = dict(row)
    projected.update(
        {
            "status": status,
            "current_item_id": current,
            "current_item_index": index,
            "completed_item_ids": completed,
            "completed_item_fingerprints": completed_fingerprints,
            "next_item_ids": next_ids,
            "superseded_completed_item_ids": superseded,
        }
    )
    return {
        "scope": str(row.get("scope") or ""),
        "changed": changed or projected != row,
        "projected": projected,
        "missing_item_ids": missing,
        "review_candidates": [
            {
                "item_id": item_id,
                "contract_fingerprint": (current_fingerprints or {}).get(item_id, ""),
                "reason": "new_or_changed_contract_requires_manual_review",
            }
            for item_id in missing[:1]
        ],
    }


def run(*, apply: bool) -> dict[str, Any]:
    progress = load_json_object_strict(PROGRESS_PATH, label="manual adversarial audit progress")
    coverage = progress.get("source_layer_coverage_progress") if isinstance(progress.get("source_layer_coverage_progress"), dict) else {}
    micro = coverage.get("micro_walkthrough_progress") if isinstance(coverage.get("micro_walkthrough_progress"), dict) else {}
    rows = micro.get("scopes") if isinstance(micro.get("scopes"), list) else []
    projections = []
    unavailable_scopes = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        scope = str(row.get("scope") or "")
        contracts = _contracts_for_scope(scope)
        if contracts is None:
            unavailable_scopes.append(
                {
                    "scope": scope,
                    "order_source": str(row.get("order_source") or ""),
                    "status": "not_available_in_current_runtime",
                }
            )
            continue
        if not contracts:
            continue
        order = [item["id"] for item in contracts]
        fingerprints = {item["id"]: item["fingerprint"] for item in contracts}
        projections.append(_project_scope(row, order, fingerprints))
    changed = [row for row in projections if row["changed"]]
    current_micro_status = str(micro.get("status") or "not_started")
    projected_micro_status = (
        "complete"
        if projections and all(row["projected"].get("status") == "complete" for row in projections)
        else "in_progress"
    )
    micro_status_changed = current_micro_status != projected_micro_status
    needs_sync = bool(changed) or micro_status_changed
    if apply and needs_sync:
        projected_by_scope = {row["scope"]: row["projected"] for row in projections}
        micro["scopes"] = [projected_by_scope.get(str(row.get("scope") or ""), row) if isinstance(row, dict) else row for row in rows]
        micro["status"] = projected_micro_status
        coverage["micro_walkthrough_progress"] = micro
        progress["source_layer_coverage_progress"] = coverage
        save_json_atomic(PROGRESS_PATH, progress, bypass_proxy=True)
    return {
        "status": "PASS" if apply or not needs_sync else "NEEDS_SYNC",
        "claim_boundary": "A PASS confirms persisted cursors match their available declared order sources; unavailable generated sources are reported as source-clean projection and never inferred. NEEDS_SYNC reports stale projection without inferring manual completion.",
        "apply": apply,
        "runtime_projection_status": "source_clean_projection_without_generated_runtime_registry" if unavailable_scopes else "live_registry_projection",
        "unavailable_scopes": unavailable_scopes,
        "changed_scopes": [row["scope"] for row in changed],
        "micro_walkthrough_status_projection": {
            "current": current_micro_status,
            "projected": projected_micro_status,
            "changed": micro_status_changed,
        },
        "review_candidates": [
            {
                "scope": row["scope"],
                **candidate,
            }
            for row in projections
            for candidate in row["review_candidates"]
        ],
        "projections": projections,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write conservative cursor projections to the progress ledger.")
    args = parser.parse_args()
    result = run(apply=args.apply)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["status"] == "PASS" else 1)
