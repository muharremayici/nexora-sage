from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.artifact_registry import artifact_metadata, artifact_path_for_storage_root
from tools.core.artifact_validator import validate_payload
from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_object_strict
from tools.core.target_repository_action_memory import CONTRACT_PATH, _PROJECTORS, build_target_repository_action_memory


RAW_OUTPUT = RAW_DIR / "target_repository_action_memory_validation.json"
REPORT_OUTPUT = REPORTS_DIR / "target_repository_action_memory_validation.md"


def _check(check_id: str, passed: bool, details: object) -> dict[str, object]:
    return {"id": check_id, "passed": bool(passed), "details": details}


def _write(raw_dir: Path, artifact_id: str, payload: object) -> None:
    path = artifact_path_for_storage_root(raw_dir, artifact_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _pulse_ledger_artifacts(value: object) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        pulse = value.get("pulse_ledger")
        if isinstance(pulse, dict):
            found.append(str(pulse.get("artifact") or ""))
        for child in value.values():
            found.extend(_pulse_ledger_artifacts(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_pulse_ledger_artifacts(child))
    return found


def run() -> dict[str, object]:
    contract = load_json_object_strict(CONTRACT_PATH, label="target repository action memory contract")
    registry = artifact_metadata()
    checks: list[dict[str, object]] = []
    source_ids = [str(row.get("id") or "") for row in contract.get("sources", []) if isinstance(row, dict)]
    artifact_ids = [str(row.get("artifact_id") or "") for row in contract.get("sources", []) if isinstance(row, dict)]
    checks.append(_check("source_ids_are_nonempty_and_unique", bool(source_ids) and len(source_ids) == len(set(source_ids)), source_ids))
    checks.append(_check("every_declared_source_has_one_projector", set(source_ids) == set(_PROJECTORS), {"declared": source_ids, "implemented": sorted(_PROJECTORS)}))
    checks.append(_check("all_source_artifacts_are_centrally_registered", all(value in registry for value in artifact_ids), artifact_ids))
    canonical_pulse_name = Path(registry["watchdog_pulse_ledger"]["path"]).name
    declared_pulse_names = []
    for policy_path in (ROOT / "config/pipeline_execution_policy.json", ROOT / "config/reality_target_profiles.json"):
        declared_pulse_names.extend(_pulse_ledger_artifacts(load_json_object_strict(policy_path, label=policy_path.name)))
    checks.append(_check("watchdog_profiles_use_registered_pulse_ledger", bool(declared_pulse_names) and all(value == canonical_pulse_name for value in declared_pulse_names), {"canonical": canonical_pulse_name, "declared": declared_pulse_names}))
    checks.append(_check("cross_source_deduplication_is_forbidden", contract.get("deduplication", {}).get("cross_source_deduplication") is False, contract.get("deduplication")))
    checks.append(_check("sage_product_memory_is_forbidden_input", set(contract.get("authority", {}).get("forbidden_inputs", [])) >= {"sage_work_item_registry", "sage_roadmap", "sage_execution_waves"}, contract.get("authority", {}).get("forbidden_inputs")))

    with tempfile.TemporaryDirectory() as temp:
        target_root = Path(temp)
        raw_dir = target_root / ".raw"
        now = "2026-07-22T12:00:00+00:00"
        _write(raw_dir, "audit_report", {"meta": {"kind": "audit_report", "version": "v1", "generated_at": now}, "summary": {"total": 0, "by_rule": {}, "audit_scope": {}, "rule_taxonomy": {}, "remediation_backlog": []}, "audit_scope": {"scope_kind": "full_repository", "full_repository_claim": True, "atlas_project_count": 1, "audited_project_count": 1, "audited_projects": ["MAIN"], "violation_project_count": 0}, "atlas_project_count": 1, "audited_project_count": 1, "audited_projects": ["MAIN"], "violation_project_count": 0, "violations": [], "report_sections": []})
        partial = build_target_repository_action_memory(target_root=target_root, raw_dir=raw_dir)
        checks.append(_check("missing_sources_are_partial_not_empty", partial["summary"]["status"] == "PARTIAL" and len(partial["unknowns"]) == 2, partial["summary"]))

        _write(raw_dir, "watchdog_pulse_ledger", {"meta": {"kind": "watchdog_pulse_ledger", "version": "v1", "generated_at": now, "generator": "tools.orchestrators.watchdog"}, "policy": {"unread_status": "unresolved_unread"}, "latest_pulse": {}, "summary": {"status": "CLEAR", "entries": 0, "unresolved_unread_count": 0, "current_violation_count": 0}, "proof_debt_state": {"status": "not_due"}, "entries": []})
        _write(raw_dir, "merge_decision_cockpit", {"meta": {"kind": "merge_decision_cockpit", "version": "v1", "generated_at": now}, "summary": {"candidates": 0, "actions": {}, "by_source": {}}, "decisions": []})
        empty = build_target_repository_action_memory(target_root=target_root, raw_dir=raw_dir)
        checks.append(_check("all_present_without_actions_is_explicit_empty", empty["summary"]["status"] == "EMPTY" and not empty["unknowns"], empty["summary"]))
        checks.append(_check("projection_schema_is_valid", validate_payload("target_repository_action_memory", empty) == [], validate_payload("target_repository_action_memory", empty)))
    source_text = (ROOT / "tools/core/target_repository_action_memory.py").read_text(encoding="utf-8")
    forbidden_inputs = [str(value) for value in contract.get("authority", {}).get("forbidden_inputs", [])]
    checks.append(_check("producer_does_not_read_sage_product_memory", all(token not in source_text for token in forbidden_inputs), forbidden_inputs))
    failed = [str(row["id"]) for row in checks if not row["passed"]]
    return {
        "meta": {"kind": "target_repository_action_memory_validation", "version": "v1"},
        "summary": {"status": "PASS" if not failed else "FAIL", "total_checks": len(checks), "passed_checks": len(checks) - len(failed), "failed_checks": failed},
        "checks": checks,
    }


def main() -> int:
    payload = run()
    save_json_atomic(RAW_OUTPUT, payload)
    lines = ["# Target Repository Action Memory Validation", ""]
    lines.extend(f"- {'PASS' if row['passed'] else 'FAIL'}: `{row['id']}`" for row in payload["checks"])
    save_text_atomic(REPORT_OUTPUT, "\n".join(lines) + "\n")
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
