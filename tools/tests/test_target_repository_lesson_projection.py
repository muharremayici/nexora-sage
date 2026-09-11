from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from tools.core.artifact_registry import artifact_path_for_storage_root
from tools.core.artifact_validator import validate_payload
from tools.core.target_repository_lesson_projection import build_target_repository_lesson_projection


NOW = "2026-07-22T12:00:00+00:00"


def _write(raw_dir: Path, artifact_id: str, payload: object) -> None:
    path = artifact_path_for_storage_root(raw_dir, artifact_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _raw_dir(target_root: Path) -> Path:
    return target_root / ".raw"


def _watchdog(*, seen_count: int = 3, same_pulse: bool = False) -> dict:
    return {
        "meta": {"kind": "watchdog_pulse_ledger", "version": "v1", "generated_at": NOW, "generator": "tools.orchestrators.watchdog"},
        "policy": {"unread_status": "unresolved_unread"},
        "latest_pulse": {},
        "summary": {"status": "HAS_UNRESOLVED", "entries": 1, "unresolved_unread_count": 1, "current_violation_count": 1},
        "proof_debt_state": {"status": "not_due"},
        "entries": [
            {
                "violation_hash": "repeat-relative-import",
                "status": "unresolved_unread",
                "first_seen_at": NOW,
                "last_seen_at": NOW,
                "first_seen_pulse_id": "pulse-a",
                "last_seen_pulse_id": "pulse-a" if same_pulse else "pulse-b",
                "seen_count": seen_count,
                "target_ref": "MAIN::src/layout/AppLayout.tsx",
                "repo_relative_path": "src/layout/AppLayout.tsx",
                "rule": "relative_imports_no_alias",
                "detail": "Use the declared project alias at this boundary.",
            }
        ],
    }


def _approvals(*, scope: str = "target_repository:example-main:lesson") -> dict:
    return {
        "meta": {"kind": "hitl_approval_ledger", "version": "v1"},
        "summary": {},
        "integrity": {"status": "PASS"},
        "entries": [
            {
                "id": "approved-local-boundary-lesson",
                "gate": "target_lesson_admission",
                "decision": "approved",
                "scope": scope,
                "actor": "repository_owner",
                "rationale": "Within this target, editor commands must cross the declared application port.",
                "evidence": ["target-proof:editor-boundary"],
            },
            {
                "id": "sage-release-seal",
                "gate": "architecture_doctrine_seal",
                "decision": "approved",
                "scope": "v1.0.0_agent_surface_human_seal",
                "actor": "repository_owner",
                "rationale": "SAGE release decision must not leak.",
                "evidence": ["sage-release-proof"],
            },
        ],
    }


def _proof(target_root: Path) -> dict:
    return {
        "meta": {"kind": "target_repository_proof_bundle", "version": "v1", "generated_at": NOW, "generator": "tools.generate_target_repository_proof_bundle", "contract_identity": "a" * 64},
        "subject": {"scope": "target_repository", "root": str(target_root.resolve()), "analysis_snapshot_kind": "atlas_commit", "analysis_snapshot_id": "snapshot-a", "root_binding": "BOUND", "repository_reference_kind": "explicit", "repository_reference_id": "fixture", "working_tree_status": "not_applicable"},
        "claim_boundary": "Fixture target proof binding only.",
        "summary": {"mode": "baseline", "verdict": "PASS", "required_evidence": 1, "required_ready": 1, "optional_evidence": 0, "optional_ready": 0},
        "evidence": [{"artifact_id": "atlas", "required": True, "path": "atlas.json", "availability": "PRESENT", "freshness": "NOT_APPLICABLE", "snapshot_binding": "BOUND", "bound_snapshot_id": "snapshot-a", "content_sha256": "b" * 64, "source_verdict": "PRESENT", "human_decision": False}],
        "statistics": [],
        "unknowns": [],
        "human_decisions": [],
    }


def _build(tmp_path: Path, *, integrity_status: str = "PASS") -> dict:
    return build_target_repository_lesson_projection(
        target_id="example-main",
        target_root=tmp_path,
        raw_dir=_raw_dir(tmp_path),
        integrity_validator=lambda _payload: {"status": integrity_status},
    )


def test_recurrence_and_exact_target_human_decision_are_separate_authorities(tmp_path: Path) -> None:
    _write(_raw_dir(tmp_path), "target_repository_proof_bundle", _proof(tmp_path))
    _write(_raw_dir(tmp_path), "watchdog_pulse_ledger", _watchdog())
    _write(_raw_dir(tmp_path), "hitl_approval_ledger", _approvals())
    payload = _build(tmp_path)
    assert payload["summary"] == {"status": "COMPLETE", "declared_sources": 3, "present_sources": 3, "lesson_count": 2, "recurrence_candidates": 1, "human_confirmed": 1}
    assert {row["authority_status"] for row in payload["lessons"]} == {"CANDIDATE_RECURRENCE", "HUMAN_CONFIRMED"}
    assert all("SAGE release decision" not in row["rule"] for row in payload["lessons"])
    assert validate_payload("target_repository_lesson_projection", payload) == []


def test_single_pulse_or_single_observation_does_not_become_lesson(tmp_path: Path) -> None:
    _write(_raw_dir(tmp_path), "target_repository_proof_bundle", _proof(tmp_path))
    _write(_raw_dir(tmp_path), "watchdog_pulse_ledger", _watchdog(seen_count=1, same_pulse=True))
    _write(_raw_dir(tmp_path), "hitl_approval_ledger", {"meta": {}, "entries": []})
    payload = _build(tmp_path)
    assert payload["summary"]["status"] == "EMPTY"
    assert payload["lessons"] == []


def test_wrong_target_scope_and_non_authoritative_chain_do_not_confirm_lesson(tmp_path: Path) -> None:
    _write(_raw_dir(tmp_path), "target_repository_proof_bundle", _proof(tmp_path))
    _write(_raw_dir(tmp_path), "watchdog_pulse_ledger", _watchdog())
    _write(_raw_dir(tmp_path), "hitl_approval_ledger", _approvals(scope="target_repository:different-target:lesson"))
    wrong_scope = _build(tmp_path)
    assert wrong_scope["summary"]["human_confirmed"] == 0
    invalid_chain = _build(tmp_path, integrity_status="FAIL")
    assert invalid_chain["summary"]["human_confirmed"] == 0
    assert "human_approval_ledger:integrity_not_authoritative" in invalid_chain["unknowns"]


def test_missing_source_is_partial_not_false_empty(tmp_path: Path) -> None:
    _write(_raw_dir(tmp_path), "target_repository_proof_bundle", _proof(tmp_path))
    _write(_raw_dir(tmp_path), "watchdog_pulse_ledger", _watchdog())
    payload = _build(tmp_path)
    assert payload["summary"]["status"] == "PARTIAL"
    assert "human_target_decision:missing" in payload["unknowns"]


def test_mismatched_target_proof_blocks_all_lesson_projection(tmp_path: Path) -> None:
    wrong_root = tmp_path / "different-target"
    wrong_root.mkdir()
    _write(_raw_dir(tmp_path), "target_repository_proof_bundle", _proof(wrong_root))
    _write(_raw_dir(tmp_path), "watchdog_pulse_ledger", _watchdog())
    _write(_raw_dir(tmp_path), "hitl_approval_ledger", _approvals())
    payload = _build(tmp_path)
    assert payload["summary"]["status"] == "PARTIAL"
    assert payload["lessons"] == []
    assert "target_binding:invalid" in payload["unknowns"]


def test_projection_source_does_not_read_sage_product_memory() -> None:
    source = Path("tools/core/target_repository_lesson_projection.py").read_text(encoding="utf-8")
    contract = json.loads(Path("config/target_repository_lesson_projection_contract.json").read_text(encoding="utf-8"))
    assert all(token not in source for token in contract["authority"]["forbidden_inputs"])


def test_external_target_projection_prefers_sqlite_payload_over_json_shadow(tmp_path: Path) -> None:
    raw_dir = _raw_dir(tmp_path)
    _write(raw_dir, "target_repository_proof_bundle", _proof(tmp_path / "wrong-shadow-root"))
    _write(raw_dir, "watchdog_pulse_ledger", _watchdog())
    _write(raw_dir, "hitl_approval_ledger", _approvals())
    with sqlite3.connect(raw_dir / "codemaps.db") as connection:
        connection.execute("CREATE TABLE state_payloads (name TEXT PRIMARY KEY, payload TEXT, payload_sha TEXT)")
        connection.execute(
            "INSERT INTO state_payloads(name, payload, payload_sha) VALUES (?, ?, ?)",
            ("target_repository_proof_bundle", json.dumps(_proof(tmp_path)), None),
        )
    payload = _build(tmp_path)
    assert payload["summary"]["status"] == "COMPLETE"
    assert payload["summary"]["lesson_count"] == 2


def test_same_violation_hash_on_distinct_paths_remains_two_target_lessons(tmp_path: Path) -> None:
    raw_dir = _raw_dir(tmp_path)
    watchdog = _watchdog()
    sibling = dict(watchdog["entries"][0])
    sibling["repo_relative_path"] = "src/editor/EditorShell.tsx"
    sibling["target_ref"] = "MAIN::src/editor/EditorShell.tsx"
    watchdog["entries"].append(sibling)
    watchdog["summary"]["entries"] = 2
    _write(raw_dir, "target_repository_proof_bundle", _proof(tmp_path))
    _write(raw_dir, "watchdog_pulse_ledger", watchdog)
    _write(raw_dir, "hitl_approval_ledger", {"meta": {}, "entries": []})
    payload = _build(tmp_path)
    recurrence = [row for row in payload["lessons"] if row["source_authority"] == "watchdog_recurrence"]
    assert len(recurrence) == 2
    assert len({row["source_identity"] for row in recurrence}) == 2
