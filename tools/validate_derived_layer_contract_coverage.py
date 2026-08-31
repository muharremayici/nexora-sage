"""Validate the advisory derived layer-coverage projection contract."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.config import RAW_DIR, save_json_atomic
from tools.generate_derived_layer_contract_coverage import build_report


REPORT_PATH = RAW_DIR / "derived_layer_contract_coverage_validation.json"
EXPECTED_CATEGORIES = {
    "release_step_missing_layer_proof",
    "spine_node_missing_layer_proof",
    "governance_surface_missing_layer_proof",
}


def run_validation() -> dict[str, Any]:
    report = build_report()
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    suggestions = report.get("suggestions") if isinstance(report.get("suggestions"), list) else []
    background = report.get("background_candidates") if isinstance(report.get("background_candidates"), list) else []
    all_rows = [*suggestions, *background]
    checks = [
        {"id": "advisory_claim_boundary", "ok": report.get("meta", {}).get("claim_boundary", "").startswith("Advisory candidates only")},
        {"id": "automatic_mutation_is_disabled", "ok": summary.get("automatic_mutation") is False},
        {"id": "candidate_rows_are_complete", "ok": all(isinstance(row, dict) and all(str(row.get(key) or "") for key in ("category", "entity_id", "source", "source_layer", "suggested_value")) for row in all_rows)},
        {"id": "supporting_candidates_have_explicit_rationale", "ok": all(row.get("disposition") != "supporting_layer_evidence" or bool((row.get("triage_decision") or {}).get("rationale")) for row in all_rows)},
        {"id": "candidate_categories_are_known", "ok": set(summary.get("category_counts", {})).issubset(EXPECTED_CATEGORIES)},
        {"id": "background_candidates_are_explicit", "ok": isinstance(summary.get("background_candidates"), int) and summary.get("background_candidates") == len(background)},
    ]
    failed = [row["id"] for row in checks if not row["ok"]]
    return {"validator": "derived_layer_contract_coverage", "status": "PASS" if not failed else "FAIL", "checks": checks, "failed_checks": failed, "summary": summary}


def main() -> int:
    report = run_validation()
    save_json_atomic(REPORT_PATH, report, indent=2)
    print(json.dumps({"status": report["status"], "failed_checks": report["failed_checks"], "ownership_gaps": report["summary"].get("ownership_gaps")}, ensure_ascii=False))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
