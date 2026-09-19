from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tools.core.artifact_store import ArtifactStore
from tools.core.atlas_integrity import build_atlas_commit
from tools.core.audit_finding_generation import evaluate_finding_manifest
from tools.core.db import SQLiteManager
from tools.mcp import server


def _atlas(root: Path, *, a_hash: str, b_hash: str) -> dict:
    return {
        "MAIN": {
            "root_path": str(root),
            "project_type": "typescript",
            "files": {
                "a.ts": {
                    "language": "typescript",
                    "size": 20,
                    "hash": a_hash,
                    "symbols": [],
                },
                "b.ts": {
                    "language": "typescript",
                    "size": 20,
                    "hash": b_hash,
                    "symbols": [],
                },
            },
            "dependencies": {"a.ts": [], "b.ts": []},
            "symbols": [],
        }
    }


def _audit(
    *,
    snapshot_id: str,
    violations: list[dict],
    changed_files: list[str] | None = None,
    deleted_files: list[str] | None = None,
) -> dict:
    scoped = changed_files is not None
    files = sorted(changed_files or [])
    scope = {
        "scope_kind": "scoped_change" if scoped else "full_repository",
        "full_repository_claim": not scoped,
        "atlas_project_count": 1,
        "audited_project_count": 1,
        "audited_projects": ["MAIN"],
        "violation_project_count": len(
            {row["project"] for row in violations if row.get("project")}
        ),
    }
    if scoped:
        deleted = sorted(deleted_files or [])
        audited = sorted(set(files) - set(deleted))
        scope.update(
            {
                "requested_file_count": len(files),
                "requested_files": files,
                "audited_file_count": len(audited),
                "audited_files": audited,
                "unresolved_requested_files": deleted,
                "scope_status": "complete" if not deleted else "partial" if audited else "empty",
            }
        )
    return {
        "meta": {
            "kind": "watchdog_audit_report" if scoped else "audit_report",
            "version": "v15-atlas-pure",
        },
        "artifact_identity": {
            "status": "BOUND",
            "atlas_snapshot_id": snapshot_id,
        },
        "audit_scope": scope,
        "violations": violations,
    }


def _violation(file_name: str, detail: str) -> dict:
    return {
        "project": "MAIN",
        "project_key": "MAIN",
        "file": file_name,
        "rule": "proof",
        "detail": detail,
    }


def _store(root: Path) -> ArtifactStore:
    store = ArtifactStore()
    store.use_sqlite = True
    store.backend = "hybrid_sqlite"
    store.db_manager = SQLiteManager(root / "codemaps.db")
    store._schema_initialized = False
    store._ensure_schema()
    return store


def _finding_messages(store: ArtifactStore) -> dict[str, str]:
    with store.db_manager.get_connection() as conn:
        rows = conn.execute(
            """
            SELECT f.rel_path, fi.message
            FROM findings fi
            JOIN files f ON f.file_id = fi.file_id
            WHERE fi.engine_name = 'audit_report'
            ORDER BY f.rel_path;
            """
        ).fetchall()
    return {
        str(row["rel_path"]): str(json.loads(row["message"])["detail"])
        for row in rows
    }


def _manifest(store: ArtifactStore) -> dict:
    with store.db_manager.get_connection() as conn:
        return store._audit_findings_manifest(conn)


def test_composed_finding_generation_does_not_require_stale_canonical_audit() -> None:
    required = {
        "artifact_present:atlas",
        "artifact_present:analysis_scope_authority",
        "sqlite_primary:atlas",
        "sqlite_primary:analysis_scope_authority",
        "scope_authority:present",
        "scope_authority:claim_usable",
        "freshness:scope_not_older_than_atlas",
    }
    trust = {
        "checks": [
            *[
                {"name": name, "passed": True, "severity": "error"}
                for name in sorted(required)
            ],
            {
                "name": "freshness:audit_not_older_than_atlas",
                "passed": False,
                "severity": "error",
            },
        ]
    }
    generation = {
        "status": "PASS",
        "snapshot_match": True,
        "coverage_match": True,
        "generation_mode": "scoped_composition",
    }

    projected = server._audit_queue_trust_projection(trust, generation)

    assert projected["status"] == "PASS"
    assert not any(
        row.get("name") == "freshness:audit_not_older_than_atlas"
        for row in projected["checks"]
    )
    assert projected["finding_generation"] == generation


def test_generation_bound_queue_refuses_json_fallback_when_sqlite_read_fails() -> None:
    generation = {"status": "PASS", "snapshot_match": True, "coverage_match": True}
    with (
        patch.object(server, "_raw_dir_for_target", return_value=server.RAW_DIR),
        patch.object(server, "_audit_findings_generation_projection", return_value=generation),
        patch.object(server, "_ensure_agent_artifact_chain_current", return_value={}),
        patch.object(server, "_audit_queue_trust_projection", return_value={"status": "PASS", "failures": []}),
        patch.object(server, "_audit_violation_work_items_from_sqlite", return_value=([], 0, False)),
        patch.object(server, "_load_json") as json_fallback,
        patch.object(server, "_record_mcp_call_result", side_effect=lambda _name, _started, result, **_kwargs: result),
    ):
        payload = json.loads(server.get_violation_work_queue(format="json"))

    assert payload["status"] == "INVALID_CONTEXT"
    assert payload["artifact_trust"]["failures"][0]["name"] == "sqlite_findings:generation_payload_available"
    json_fallback.assert_not_called()


def test_module_integrity_uses_same_generation_gate_and_refuses_json_fallback() -> None:
    generation = {"status": "PASS", "snapshot_match": True, "coverage_match": True}
    with (
        patch.object(server, "_raw_dir_for_target", return_value=server.RAW_DIR),
        patch.object(server, "_audit_findings_generation_projection", return_value=generation) as generation_reader,
        patch.object(server, "_ensure_agent_artifact_chain_current", return_value={}),
        patch.object(server, "_audit_queue_trust_projection", return_value={"status": "PASS", "failures": []}),
        patch.object(server, "_module_integrity_items_from_sqlite", return_value=([], 0, False)),
        patch.object(server, "_artifact_or_missing") as json_fallback,
    ):
        payload = json.loads(
            server.check_module_integrity(
                "OTHER::src/domain",
                format="json",
            )
        )

    assert payload["status"] == "INVALID_CONTEXT"
    assert payload["artifact_trust"]["failures"][0]["name"] == "sqlite_findings:generation_payload_available"
    generation_reader.assert_called_once_with(server.RAW_DIR, project="OTHER")
    json_fallback.assert_not_called()


def test_scoped_audit_composes_exact_file_and_preserves_unrelated_findings(
    tmp_path: Path,
) -> None:
    (tmp_path / "a.ts").write_text("export const A = 1;\n", encoding="utf-8")
    (tmp_path / "b.ts").write_text("export const B = 1;\n", encoding="utf-8")
    store = _store(tmp_path)
    baseline_atlas = _atlas(tmp_path, a_hash="a1", b_hash="b1")
    baseline_commit = build_atlas_commit(baseline_atlas)
    store._save_atlas_to_sqlite(baseline_atlas)
    store._save_payload_to_state_table("atlas", baseline_atlas)
    store._save_payload_to_state_table("atlas_commit", baseline_commit)
    store._save_audit_findings_to_sqlite(
        _audit(
            snapshot_id=baseline_commit["snapshot_id"],
            violations=[
                _violation("a.ts", "old-a"),
                _violation("b.ts", "preserve-b"),
            ],
        )
    )

    next_atlas = _atlas(tmp_path, a_hash="a2", b_hash="b1")
    next_commit = build_atlas_commit(
        next_atlas,
        generation_mode="surgical",
        parent_snapshot_id=baseline_commit["snapshot_id"],
        changed_files=["MAIN::a.ts"],
    )
    store._save_payload_to_state_table("atlas", next_atlas)
    store._save_payload_to_state_table("atlas_commit", next_commit)
    result = store._compose_scoped_audit_findings_to_sqlite(
        _audit(
            snapshot_id=next_commit["snapshot_id"],
            violations=[_violation("a.ts", "new-a")],
            changed_files=["MAIN::a.ts"],
        )
    )

    assert result["status"] == "COMPOSED"
    assert result["files_replaced"] == 1
    assert _finding_messages(store) == {
        "a.ts": "new-a",
        "b.ts": "preserve-b",
    }
    manifest = _manifest(store)
    assert evaluate_finding_manifest(
        manifest,
        expected_snapshot_id=next_commit["snapshot_id"],
        requested_project="MAIN",
    )["status"] == "PASS"
    assert manifest["composition_depth"] == 1
    assert manifest["last_changed_files"] == ["MAIN::a.ts"]
    assert manifest["full_repository_claim"] is False
    assert evaluate_finding_manifest(
        manifest,
        expected_snapshot_id=next_commit["snapshot_id"],
        requested_project="*",
    )["status"] == "FAIL"


def test_scoped_tombstone_removes_deleted_file_and_preserves_unrelated_findings(
    tmp_path: Path,
) -> None:
    (tmp_path / "a.ts").write_text("export const A = 1;\n", encoding="utf-8")
    (tmp_path / "b.ts").write_text("export const B = 1;\n", encoding="utf-8")
    store = _store(tmp_path)
    baseline_atlas = _atlas(tmp_path, a_hash="a1", b_hash="b1")
    baseline_commit = build_atlas_commit(baseline_atlas)
    store._save_atlas_to_sqlite(baseline_atlas)
    store._save_payload_to_state_table("atlas", baseline_atlas)
    store._save_payload_to_state_table("atlas_commit", baseline_commit)
    store._save_audit_findings_to_sqlite(
        _audit(
            snapshot_id=baseline_commit["snapshot_id"],
            violations=[
                _violation("a.ts", "preserve-a"),
                _violation("b.ts", "remove-b"),
            ],
        )
    )

    next_atlas = _atlas(tmp_path, a_hash="a1", b_hash="b1")
    next_atlas["MAIN"]["files"].pop("b.ts")
    next_atlas["MAIN"]["dependencies"].pop("b.ts")
    (tmp_path / "b.ts").unlink()
    next_commit = build_atlas_commit(
        next_atlas,
        generation_mode="surgical",
        parent_snapshot_id=baseline_commit["snapshot_id"],
        changed_files=["MAIN::b.ts"],
        deleted_files=["MAIN::b.ts"],
    )
    relational = store._save_scoped_atlas_to_sqlite(
        next_atlas,
        {"MAIN": {"b.ts"}},
        {},
    )
    assert relational is not None
    store._save_payload_to_state_table("atlas", next_atlas)
    store._save_payload_to_state_table("atlas_commit", next_commit)
    result = store._compose_scoped_audit_findings_to_sqlite(
        _audit(
            snapshot_id=next_commit["snapshot_id"],
            violations=[],
            changed_files=["MAIN::b.ts"],
            deleted_files=["MAIN::b.ts"],
        )
    )

    assert result["status"] == "COMPOSED"
    assert _finding_messages(store) == {"a.ts": "preserve-a"}
    manifest = _manifest(store)
    assert manifest["last_deleted_files"] == ["MAIN::b.ts"]
    assert manifest["snapshot_id"] == next_commit["snapshot_id"]


def test_scoped_audit_parent_mismatch_rolls_back_without_relabeling_findings(
    tmp_path: Path,
) -> None:
    (tmp_path / "a.ts").write_text("export const A = 1;\n", encoding="utf-8")
    (tmp_path / "b.ts").write_text("export const B = 1;\n", encoding="utf-8")
    store = _store(tmp_path)
    atlas = _atlas(tmp_path, a_hash="a1", b_hash="b1")
    baseline_commit = build_atlas_commit(atlas)
    store._save_atlas_to_sqlite(atlas)
    store._save_payload_to_state_table("atlas", atlas)
    store._save_payload_to_state_table("atlas_commit", baseline_commit)
    store._save_audit_findings_to_sqlite(
        _audit(
            snapshot_id=baseline_commit["snapshot_id"],
            violations=[_violation("a.ts", "baseline")],
        )
    )
    baseline_manifest = _manifest(store)

    next_atlas = _atlas(tmp_path, a_hash="a2", b_hash="b1")
    invalid_parent_commit = build_atlas_commit(
        next_atlas,
        generation_mode="surgical",
        parent_snapshot_id="wrong-parent",
        changed_files=["MAIN::a.ts"],
    )
    store._save_payload_to_state_table("atlas_commit", invalid_parent_commit)
    with pytest.raises(ValueError, match="parent snapshot"):
        store._compose_scoped_audit_findings_to_sqlite(
            _audit(
                snapshot_id=invalid_parent_commit["snapshot_id"],
                violations=[_violation("a.ts", "must-not-land")],
                changed_files=["MAIN::a.ts"],
            )
        )

    assert _finding_messages(store) == {"a.ts": "baseline"}
    assert _manifest(store) == baseline_manifest


def test_canonical_unmapped_finding_rolls_back_without_relabeling_manifest(
    tmp_path: Path,
) -> None:
    (tmp_path / "a.ts").write_text("export const A = 1;\n", encoding="utf-8")
    (tmp_path / "b.ts").write_text("export const B = 1;\n", encoding="utf-8")
    store = _store(tmp_path)
    atlas = _atlas(tmp_path, a_hash="a1", b_hash="b1")
    commit = build_atlas_commit(atlas)
    store._save_atlas_to_sqlite(atlas)
    store._save_payload_to_state_table("atlas", atlas)
    store._save_payload_to_state_table("atlas_commit", commit)
    store._save_audit_findings_to_sqlite(
        _audit(
            snapshot_id=commit["snapshot_id"],
            violations=[_violation("a.ts", "baseline")],
        )
    )
    baseline_manifest = _manifest(store)

    with pytest.raises(ValueError, match="unmapped"):
        store._save_audit_findings_to_sqlite(
            _audit(
                snapshot_id=commit["snapshot_id"],
                violations=[_violation("missing.ts", "must-not-land")],
            )
        )

    assert _finding_messages(store) == {"a.ts": "baseline"}
    assert _manifest(store) == baseline_manifest


def test_scoped_audit_scope_mismatch_is_refused(tmp_path: Path) -> None:
    (tmp_path / "a.ts").write_text("export const A = 1;\n", encoding="utf-8")
    (tmp_path / "b.ts").write_text("export const B = 1;\n", encoding="utf-8")
    store = _store(tmp_path)
    baseline_atlas = _atlas(tmp_path, a_hash="a1", b_hash="b1")
    baseline_commit = build_atlas_commit(baseline_atlas)
    store._save_atlas_to_sqlite(baseline_atlas)
    store._save_payload_to_state_table("atlas", baseline_atlas)
    store._save_payload_to_state_table("atlas_commit", baseline_commit)
    store._save_audit_findings_to_sqlite(
        _audit(
            snapshot_id=baseline_commit["snapshot_id"],
            violations=[_violation("a.ts", "baseline-a")],
        )
    )

    next_atlas = _atlas(tmp_path, a_hash="a2", b_hash="b1")
    next_commit = build_atlas_commit(
        next_atlas,
        generation_mode="surgical",
        parent_snapshot_id=baseline_commit["snapshot_id"],
        changed_files=["MAIN::a.ts"],
    )
    store._save_payload_to_state_table("atlas_commit", next_commit)
    with pytest.raises(ValueError, match="exactly match"):
        store._compose_scoped_audit_findings_to_sqlite(
            _audit(
                snapshot_id=next_commit["snapshot_id"],
                violations=[_violation("b.ts", "wrong-scope")],
                changed_files=["MAIN::b.ts"],
            )
        )

    assert _finding_messages(store) == {"a.ts": "baseline-a"}


def test_scoped_audit_tampered_transition_hash_is_refused(tmp_path: Path) -> None:
    (tmp_path / "a.ts").write_text("export const A = 1;\n", encoding="utf-8")
    (tmp_path / "b.ts").write_text("export const B = 1;\n", encoding="utf-8")
    store = _store(tmp_path)
    baseline_atlas = _atlas(tmp_path, a_hash="a1", b_hash="b1")
    baseline_commit = build_atlas_commit(baseline_atlas)
    store._save_atlas_to_sqlite(baseline_atlas)
    store._save_payload_to_state_table("atlas", baseline_atlas)
    store._save_payload_to_state_table("atlas_commit", baseline_commit)
    store._save_audit_findings_to_sqlite(
        _audit(
            snapshot_id=baseline_commit["snapshot_id"],
            violations=[_violation("a.ts", "baseline-a")],
        )
    )

    next_atlas = _atlas(tmp_path, a_hash="a2", b_hash="b1")
    next_commit = build_atlas_commit(
        next_atlas,
        generation_mode="surgical",
        parent_snapshot_id=baseline_commit["snapshot_id"],
        changed_files=["MAIN::a.ts"],
    )
    next_commit["generation_transition"]["changed_files_sha256"] = "0" * 64
    store._save_payload_to_state_table("atlas_commit", next_commit)
    with pytest.raises(ValueError, match="signed scoped transition"):
        store._compose_scoped_audit_findings_to_sqlite(
            _audit(
                snapshot_id=next_commit["snapshot_id"],
                violations=[_violation("a.ts", "must-not-land")],
                changed_files=["MAIN::a.ts"],
            )
        )

    assert _finding_messages(store) == {"a.ts": "baseline-a"}


def test_scoped_composition_continues_after_store_restart(tmp_path: Path) -> None:
    (tmp_path / "a.ts").write_text("export const A = 1;\n", encoding="utf-8")
    (tmp_path / "b.ts").write_text("export const B = 1;\n", encoding="utf-8")
    first_store = _store(tmp_path)
    baseline_atlas = _atlas(tmp_path, a_hash="a1", b_hash="b1")
    baseline_commit = build_atlas_commit(baseline_atlas)
    first_store._save_atlas_to_sqlite(baseline_atlas)
    first_store._save_payload_to_state_table("atlas", baseline_atlas)
    first_store._save_payload_to_state_table("atlas_commit", baseline_commit)
    first_store._save_audit_findings_to_sqlite(
        _audit(
            snapshot_id=baseline_commit["snapshot_id"],
            violations=[
                _violation("a.ts", "a1"),
                _violation("b.ts", "b1"),
            ],
        )
    )

    second_atlas = _atlas(tmp_path, a_hash="a2", b_hash="b1")
    second_commit = build_atlas_commit(
        second_atlas,
        generation_mode="surgical",
        parent_snapshot_id=baseline_commit["snapshot_id"],
        changed_files=["MAIN::a.ts"],
    )
    first_store._save_payload_to_state_table("atlas_commit", second_commit)
    first_store._compose_scoped_audit_findings_to_sqlite(
        _audit(
            snapshot_id=second_commit["snapshot_id"],
            violations=[_violation("a.ts", "a2")],
            changed_files=["MAIN::a.ts"],
        )
    )

    restarted_store = _store(tmp_path)
    third_atlas = _atlas(tmp_path, a_hash="a2", b_hash="b2")
    third_commit = build_atlas_commit(
        third_atlas,
        generation_mode="surgical",
        parent_snapshot_id=second_commit["snapshot_id"],
        changed_files=["MAIN::b.ts"],
    )
    restarted_store._save_payload_to_state_table("atlas_commit", third_commit)
    restarted_store._compose_scoped_audit_findings_to_sqlite(
        _audit(
            snapshot_id=third_commit["snapshot_id"],
            violations=[_violation("b.ts", "b2")],
            changed_files=["MAIN::b.ts"],
        )
    )

    assert _finding_messages(restarted_store) == {"a.ts": "a2", "b.ts": "b2"}
    manifest = _manifest(restarted_store)
    assert manifest["snapshot_id"] == third_commit["snapshot_id"]
    assert manifest["composition_depth"] == 2


def test_out_of_order_surgical_pulse_cannot_relabel_prior_findings(tmp_path: Path) -> None:
    (tmp_path / "a.ts").write_text("export const A = 1;\n", encoding="utf-8")
    (tmp_path / "b.ts").write_text("export const B = 1;\n", encoding="utf-8")
    store = _store(tmp_path)
    baseline_atlas = _atlas(tmp_path, a_hash="a1", b_hash="b1")
    baseline_commit = build_atlas_commit(baseline_atlas)
    store._save_atlas_to_sqlite(baseline_atlas)
    store._save_payload_to_state_table("atlas", baseline_atlas)
    store._save_payload_to_state_table("atlas_commit", baseline_commit)
    store._save_audit_findings_to_sqlite(
        _audit(
            snapshot_id=baseline_commit["snapshot_id"],
            violations=[
                _violation("a.ts", "a1"),
                _violation("b.ts", "b1"),
            ],
        )
    )

    first_atlas = _atlas(tmp_path, a_hash="a2", b_hash="b1")
    first_commit = build_atlas_commit(
        first_atlas,
        generation_mode="surgical",
        parent_snapshot_id=baseline_commit["snapshot_id"],
        changed_files=["MAIN::a.ts"],
    )
    store._save_payload_to_state_table("atlas_commit", first_commit)
    store._compose_scoped_audit_findings_to_sqlite(
        _audit(
            snapshot_id=first_commit["snapshot_id"],
            violations=[_violation("a.ts", "a2")],
            changed_files=["MAIN::a.ts"],
        )
    )
    first_manifest = _manifest(store)

    competing_atlas = _atlas(tmp_path, a_hash="a1", b_hash="b2")
    competing_commit = build_atlas_commit(
        competing_atlas,
        generation_mode="surgical",
        parent_snapshot_id=baseline_commit["snapshot_id"],
        changed_files=["MAIN::b.ts"],
    )
    store._save_payload_to_state_table("atlas_commit", competing_commit)
    with pytest.raises(ValueError, match="parent snapshot"):
        store._compose_scoped_audit_findings_to_sqlite(
            _audit(
                snapshot_id=competing_commit["snapshot_id"],
                violations=[_violation("b.ts", "must-not-land")],
                changed_files=["MAIN::b.ts"],
            )
        )

    assert _finding_messages(store) == {"a.ts": "a2", "b.ts": "b1"}
    assert _manifest(store) == first_manifest
    assert evaluate_finding_manifest(
        first_manifest,
        expected_snapshot_id=competing_commit["snapshot_id"],
        requested_project="MAIN",
    )["status"] == "FAIL"
