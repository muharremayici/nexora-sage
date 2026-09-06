from __future__ import annotations

import json
from pathlib import Path

from tools.core.artifact_registry import artifact_path_for_storage_root
from tools.core.artifact_validator import validate_payload
from tools.core.target_repository_action_memory import build_target_repository_action_memory


NOW = "2026-07-22T12:00:00+00:00"


def _write(raw_dir: Path, artifact_id: str, payload: object) -> None:
    path = artifact_path_for_storage_root(raw_dir, artifact_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _raw_dir(target_root: Path) -> Path:
    return target_root / ".raw"


def _audit() -> dict:
    return {
        "meta": {"kind": "audit_report", "version": "v1", "generated_at": NOW},
        "summary": {"total": 2, "by_rule": {}, "audit_scope": {}, "rule_taxonomy": {}, "remediation_backlog": []},
        "audit_scope": {"scope_kind": "full_repository", "full_repository_claim": True, "atlas_project_count": 1, "audited_project_count": 1, "audited_projects": ["MAIN"], "violation_project_count": 1},
        "atlas_project_count": 1,
        "audited_project_count": 1,
        "audited_projects": ["MAIN"],
        "violation_project_count": 1,
        "violations": [
            {"project": "MAIN", "file": "src/auth/session.ts", "rule": "boundary_import", "detail": "UI imports infrastructure", "recommended_action": "Move the dependency behind the declared port.", "severity": "enforced"},
            {"project": "MAIN", "file": "src/catalog/pricing.ts", "rule": "oversized_function", "detail": "Function exceeds the declared LOC limit", "recommended_action": "Extract one cohesive calculation helper."},
        ],
        "report_sections": [],
    }


def _watchdog() -> dict:
    return {
        "meta": {"kind": "watchdog_pulse_ledger", "version": "v1", "generated_at": NOW, "generator": "tools.orchestrators.watchdog"},
        "policy": {"unread_status": "unresolved_unread"},
        "latest_pulse": {},
        "summary": {"status": "HAS_UNRESOLVED", "entries": 2, "unresolved_unread_count": 1, "current_violation_count": 1},
        "proof_debt_state": {"status": "due", "due_reasons": ["pulse_threshold"]},
        "entries": [
            {"violation_hash": "shared-looking-id", "status": "unresolved_unread", "first_seen_at": NOW, "last_seen_at": NOW, "target_ref": "MAIN::src/runtime/cache.ts", "repo_relative_path": "src/runtime/cache.ts", "rule": "silent_fallback", "detail": "Fallback hides a failed primary read."},
            {"violation_hash": "resolved-row", "status": "resolved_by_absence_in_next_pulse", "first_seen_at": NOW, "last_seen_at": NOW, "target_ref": "MAIN::src/old.ts", "repo_relative_path": "src/old.ts", "rule": "old", "detail": "resolved"},
        ],
    }


def _merge() -> dict:
    return {
        "meta": {"kind": "merge_decision_cockpit", "version": "v1", "generated_at": NOW},
        "summary": {"candidates": 2, "actions": {}, "by_source": {}},
        "decisions": [
            {"id": "shared-looking-id", "candidate": "src/editor/Panel.tsx", "source": "VARIANT_A", "target_path": "src/editor/Panel.tsx", "action": "Import With Review", "decision": "ASSISTED_IMPORT", "package_tier": "small", "closure_size": 1, "required_actions": ["Review behavior parity"], "reasons": ["Target has a local modification"], "route": {}, "evidence": {}},
            {"candidate": "src/search/index.ts", "source": "VARIANT_B", "target_path": "src/search/index.ts", "action": "Import Now", "decision": "SAFE_TO_IMPORT", "package_tier": "small", "closure_size": 1, "required_actions": [], "reasons": ["No target conflict"], "route": {}, "evidence": {}},
        ],
    }


def test_projection_preserves_authorities_and_schema(tmp_path: Path) -> None:
    raw_dir = _raw_dir(tmp_path)
    _write(raw_dir, "audit_report", _audit())
    _write(raw_dir, "watchdog_pulse_ledger", _watchdog())
    _write(raw_dir, "merge_decision_cockpit", _merge())
    payload = build_target_repository_action_memory(target_root=tmp_path, raw_dir=raw_dir)
    assert payload["summary"] == {"status": "COMPLETE", "declared_sources": 3, "present_sources": 3, "action_count": 6, "human_review_actions": 2}
    contract = json.loads(Path("config/target_repository_action_memory_contract.json").read_text(encoding="utf-8"))
    expected_authorities = {row["id"] for row in contract["sources"]}
    assert {row["source_authority"] for row in payload["actions"]} == expected_authorities
    assert len([row for row in payload["actions"] if row["source_identity"] == "shared-looking-id"]) == 2
    assert all(row["target_file"] != "src/old.ts" for row in payload["actions"])
    assert validate_payload("target_repository_action_memory", payload) == []


def test_missing_source_is_partial_not_false_clean(tmp_path: Path) -> None:
    audit = _audit()
    audit["violations"] = []
    audit["summary"]["total"] = 0
    audit["violation_project_count"] = 0
    audit["audit_scope"]["violation_project_count"] = 0
    raw_dir = _raw_dir(tmp_path)
    _write(raw_dir, "audit_report", audit)
    payload = build_target_repository_action_memory(target_root=tmp_path, raw_dir=raw_dir)
    assert payload["summary"]["status"] == "PARTIAL"
    assert payload["summary"]["action_count"] == 0
    assert payload["unknowns"] == ["merge_review:missing", "watchdog_proof_debt:missing"]


def test_missing_timestamp_stays_unknown_and_duplicate_source_identity_is_collapsed(tmp_path: Path) -> None:
    audit = _audit()
    audit["meta"].pop("generated_at")
    audit["violations"].append(dict(audit["violations"][0]))
    raw_dir = _raw_dir(tmp_path)
    _write(raw_dir, "audit_report", audit)
    _write(raw_dir, "watchdog_pulse_ledger", _watchdog())
    _write(raw_dir, "merge_decision_cockpit", _merge())
    payload = build_target_repository_action_memory(target_root=tmp_path, raw_dir=raw_dir)
    audit_rows = [row for row in payload["actions"] if row["source_authority"] == "audit_violations"]
    assert len(audit_rows) == 2
    assert all(row["freshness"] == "UNKNOWN" for row in audit_rows)
    assert "audit_violations:freshness_unknown" in payload["unknowns"]


def test_invalid_source_is_visible(tmp_path: Path) -> None:
    raw_dir = _raw_dir(tmp_path)
    _write(raw_dir, "audit_report", _audit())
    artifact_path_for_storage_root(raw_dir, "watchdog_pulse_ledger").write_text("{", encoding="utf-8")
    _write(raw_dir, "merge_decision_cockpit", _merge())
    payload = build_target_repository_action_memory(target_root=tmp_path, raw_dir=raw_dir)
    source = next(row for row in payload["sources"] if row["source_id"] == "watchdog_proof_debt")
    assert source["availability"] == "INVALID"
    assert "watchdog_proof_debt:invalid" in payload["unknowns"]


def test_projection_source_does_not_import_sage_product_memory() -> None:
    source = Path("tools/core/target_repository_action_memory.py").read_text(encoding="utf-8")
    contract = json.loads(Path("config/target_repository_action_memory_contract.json").read_text(encoding="utf-8"))
    assert all(token not in source for token in contract["authority"]["forbidden_inputs"])
