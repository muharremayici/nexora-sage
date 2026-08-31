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
from tools.core.target_repository_lesson_projection import CONTRACT_PATH, build_target_repository_lesson_projection


RAW_OUTPUT = RAW_DIR / "target_repository_lesson_projection_validation.json"
REPORT_OUTPUT = REPORTS_DIR / "target_repository_lesson_projection_validation.md"


def _check(check_id: str, passed: bool, details: object) -> dict[str, object]:
    return {"id": check_id, "passed": bool(passed), "details": details}


def _write(raw_dir: Path, artifact_id: str, payload: object) -> None:
    path = artifact_path_for_storage_root(raw_dir, artifact_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def run() -> dict[str, object]:
    contract = load_json_object_strict(CONTRACT_PATH, label="target repository lesson projection contract")
    registry = artifact_metadata()
    recurrence = contract.get("recurrence_admission", {})
    human = contract.get("human_admission", {})
    artifact_ids = [str(contract.get("target_binding", {}).get("proof_artifact_id") or ""), str(recurrence.get("source_artifact_id") or ""), str(human.get("source_artifact_id") or "")]
    checks = [
        _check("source_artifacts_are_centrally_registered", all(value in registry for value in artifact_ids), artifact_ids),
        _check("recurrence_requires_multiple_observations_and_pulses", int(recurrence.get("minimum_observations") or 0) > 1 and int(recurrence.get("minimum_distinct_pulses") or 0) > 1, recurrence),
        _check("human_scope_is_target_bound", "{target_id}" in str(contract.get("target_binding", {}).get("human_scope_template") or ""), contract.get("target_binding")),
        _check("cross_authority_deduplication_is_forbidden", contract.get("deduplication", {}).get("cross_authority_deduplication") is False, contract.get("deduplication")),
        _check("sage_product_memory_is_forbidden", set(contract.get("authority", {}).get("forbidden_inputs", [])) >= {"audit_lesson_registry", "sage_work_item_registry", "sage_roadmap", "sage_execution_waves"}, contract.get("authority", {}).get("forbidden_inputs")),
    ]
    with tempfile.TemporaryDirectory() as temp:
        target_root = Path(temp)
        raw_dir = target_root / ".raw"
        now = "2026-07-22T12:00:00+00:00"
        _write(raw_dir, "target_repository_proof_bundle", {"meta": {"kind": "target_repository_proof_bundle", "version": "v1", "generated_at": now, "generator": "tools.generate_target_repository_proof_bundle", "contract_identity": "a" * 64}, "subject": {"scope": "target_repository", "root": str(target_root.resolve()), "analysis_snapshot_kind": "atlas_commit", "analysis_snapshot_id": "snapshot-a", "root_binding": "BOUND", "repository_reference_kind": "explicit", "repository_reference_id": "fixture", "working_tree_status": "not_applicable"}, "claim_boundary": "Fixture target binding.", "summary": {"mode": "baseline", "verdict": "PASS", "required_evidence": 1, "required_ready": 1, "optional_evidence": 0, "optional_ready": 0}, "evidence": [{"artifact_id": "atlas", "required": True, "path": "atlas.json", "availability": "PRESENT", "freshness": "NOT_APPLICABLE", "snapshot_binding": "BOUND", "bound_snapshot_id": "snapshot-a", "content_sha256": "b" * 64, "source_verdict": "PRESENT", "human_decision": False}], "statistics": [], "unknowns": [], "human_decisions": []})
        _write(raw_dir, "watchdog_pulse_ledger", {"meta": {"kind": "watchdog_pulse_ledger", "version": "v1", "generated_at": now, "generator": "tools.orchestrators.watchdog"}, "policy": {"unread_status": "unresolved_unread"}, "latest_pulse": {}, "summary": {"status": "CLEAR", "entries": 0, "unresolved_unread_count": 0, "current_violation_count": 0}, "proof_debt_state": {"status": "not_due"}, "entries": []})
        partial = build_target_repository_lesson_projection(target_id="fixture-target", target_root=target_root, raw_dir=raw_dir, integrity_validator=lambda _payload: {"status": "PASS"})
        checks.append(_check("missing_human_source_is_partial", partial["summary"]["status"] == "PARTIAL", partial["summary"]))
        _write(raw_dir, "hitl_approval_ledger", {"meta": {"kind": "hitl_approval_ledger", "version": "v1"}, "entries": []})
        empty = build_target_repository_lesson_projection(target_id="fixture-target", target_root=target_root, raw_dir=raw_dir, integrity_validator=lambda _payload: {"status": "PASS"})
        errors = validate_payload("target_repository_lesson_projection", empty)
        checks.append(_check("all_present_without_evidence_is_empty", empty["summary"]["status"] == "EMPTY", empty["summary"]))
        checks.append(_check("projection_schema_is_valid", not errors, errors))
    source_text = (ROOT / "tools/core/target_repository_lesson_projection.py").read_text(encoding="utf-8")
    forbidden = [str(value) for value in contract.get("authority", {}).get("forbidden_inputs", [])]
    checks.append(_check("producer_does_not_read_sage_product_memory", all(value not in source_text for value in forbidden), forbidden))
    failed = [str(row["id"]) for row in checks if not row["passed"]]
    return {"meta": {"kind": "target_repository_lesson_projection_validation", "version": "v1"}, "summary": {"status": "PASS" if not failed else "FAIL", "total_checks": len(checks), "passed_checks": len(checks) - len(failed), "failed_checks": failed}, "checks": checks}


def main() -> int:
    payload = run()
    save_json_atomic(RAW_OUTPUT, payload)
    save_text_atomic(REPORT_OUTPUT, "# Target Repository Lesson Projection Validation\n\n" + "\n".join(f"- {'PASS' if row['passed'] else 'FAIL'}: `{row['id']}`" for row in payload["checks"]) + "\n")
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
