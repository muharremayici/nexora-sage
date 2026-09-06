"""Validate the shared declarative policy contract used by repository validators."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.config import RAW_DIR, save_json_atomic
from tools.core.validator_policy_registry import (
    REGISTRY_PATH,
    hardcoded_decision_inventory_policy,
    load_validator_policy_registry,
    sqlite_proxy_coverage_policy,
)


REPORT_PATH = RAW_DIR / "validator_policy_registry_validation.json"


def _check(name: str, passed: bool, details: object) -> dict[str, object]:
    return {"name": name, "passed": bool(passed), "details": details}


def run_validation() -> dict[str, object]:
    payload = load_validator_policy_registry()
    inventory = hardcoded_decision_inventory_policy()
    sqlite_proxy = sqlite_proxy_coverage_policy()
    checks = [
        _check("registry_is_strictly_loadable", bool(payload), str(REGISTRY_PATH)),
        _check(
            "hardcoded_inventory_triage_actions_are_disjoint",
            not (set(inventory["triage_actions"]["actionable"]) & set(inventory["triage_actions"]["bulk_deferred"])),
            inventory["triage_actions"],
        ),
        _check(
            "hardcoded_inventory_structured_decision_table_policy_is_explicit",
            len(inventory["decision_table_row_field_markers"]) >= inventory["decision_table_min_matching_fields"]
            and inventory["decision_table_min_rows"] >= 2,
            {
                "field_markers": inventory["decision_table_row_field_markers"],
                "min_rows": inventory["decision_table_min_rows"],
                "min_matching_fields": inventory["decision_table_min_matching_fields"],
            },
        ),
        _check(
            "sqlite_proxy_allowed_files_are_scoped_to_runtime_roots",
            all(path.startswith("tools/") for path in sqlite_proxy["raw_disk_read_allowed_files"] + sqlite_proxy["raw_exists_gate_allowed_files"]),
            {"raw_disk_read_allowed_files": sqlite_proxy["raw_disk_read_allowed_files"], "raw_exists_gate_allowed_files": sqlite_proxy["raw_exists_gate_allowed_files"]},
        ),
        _check(
            "validator_policy_does_not_duplicate_artifact_level_sqlite_exceptions",
            "allowed_direct_path_references" not in sqlite_proxy,
            "artifact-level SQLite exception ownership remains in sqlite_first_access_policy.json",
        ),
    ]
    failed = [check for check in checks if not check["passed"]]
    report = {
        "summary": {"total_checks": len(checks), "passed_checks": len(checks) - len(failed), "failed_checks": len(failed), "status": "PASS" if not failed else "FAIL"},
        "checks": checks,
    }
    save_json_atomic(REPORT_PATH, report, indent=2)
    return report


def main() -> int:
    report = run_validation()
    print(json.dumps(report["summary"], ensure_ascii=False))
    return 0 if report["summary"]["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
