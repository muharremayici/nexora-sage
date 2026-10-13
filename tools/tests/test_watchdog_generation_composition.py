from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tools.core.artifact_store import ArtifactStore
from tools.core.atlas_integrity import build_atlas_commit
from tools.core.audit_finding_generation import evaluate_finding_manifest
from tools.core import audit_finding_generation as finding_order
from tools.core import contextos_mcp
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


def _ordered_violation(path: str, detail: str = "source-bound review", **fields) -> dict:
    return {**_violation(path, detail), "atlas_rel_path":path, **fields}


def _presentation_queue(root: Path, rows: list[dict], *, page: int = 1, size: int = 50):
    store = _store(root)
    atlas = _atlas(root, a_hash="a1", b_hash="b1")
    # Exercise the real indexed mapping and relational projection, including Unicode paths.
    for row in rows:
        atlas["MAIN"]["files"].setdefault(row["atlas_rel_path"], dict(atlas["MAIN"]["files"]["a.ts"]))
    commit = build_atlas_commit(atlas)
    store._save_atlas_to_sqlite(atlas)
    store._save_payload_to_state_table("atlas", atlas)
    store._save_payload_to_state_table("atlas_commit", commit)
    store._save_audit_findings_to_sqlite(_audit(snapshot_id=commit["snapshot_id"], violations=rows))
    return server._audit_violation_work_items_from_sqlite(root, page=page, page_size=size, project="MAIN")


@pytest.mark.parametrize("fields", [{"mode":"critical"}, {"severity":"high"}, {"level":"warning"}])
def test_presentation_ranks_before_queue_page_and_packet_cap(tmp_path, fields):
    rows = [_ordered_violation("a.ts", "early advisory", mode="advisory"), _ordered_violation("b.ts", "late elevated row", **fields)]
    queue, total, ok = _presentation_queue(tmp_path, rows, size=1)
    directives = contextos_mcp.build_agent_action_directives(audit_report={"violations":rows}, max_items=1)
    assert ok and total == 2 and queue[0]["target_file"] == "b.ts"
    assert directives[0]["evidence"] == rows[1]["detail"]
    assert directives[0]["presentation_order"] == queue[0]["presentation_order"]
    assert directives[0]["mutation_authority"] == "separate_actor_policy_and_approval"
    assert queue[0]["presentation_order"]["causal_relation"] == "not_evaluated"


def test_unknown_ties_unicode_duplicates_and_dictionary_order_match_queue(tmp_path):
    rows = [_ordered_violation("İ.ts"), _ordered_violation("Ö.ts"), _ordered_violation("Z.ts"),
        _ordered_violation("a.ts", "z"), _ordered_violation("a.ts", "a"), _ordered_violation("a.ts", "a")]
    rows[4] = dict(reversed(list(rows[4].items())))
    queue, total, ok = _presentation_queue(tmp_path, rows)
    selected, summary = finding_order.select_audit_presentation_rows(rows, limit=20)
    assert ok and total == len(queue) == len(selected) == 6
    assert [(r["target_file"],r["evidence"]) for r in queue] == [(r["atlas_rel_path"],r["detail"]) for r in selected]
    assert [r["audit_severity"] for r in queue] == ["unknown"] * 6
    assert summary["unknown_row_level"] == "preserved"
    assert summary["candidate_universe_equality"] == "not_evaluated"
    reverse, _ = finding_order.select_audit_presentation_rows(list(reversed(rows)), limit=20)
    assert reverse == selected


def test_mode_precedence_and_canonical_identity_not_workspace_path(tmp_path):
    rows = [_ordered_violation("b.ts", mode="advisory", severity="critical"),
        _ordered_violation("a.ts", file="workspace/z.ts", severity="high")]
    queue, total, ok = _presentation_queue(tmp_path, rows)
    selected, _ = finding_order.select_audit_presentation_rows(rows, limit=1)
    assert ok and total == 2 and queue[0]["target_file"] == selected[0]["atlas_rel_path"] == "a.ts"
    assert queue[1]["audit_severity"] == "advisory"
    other_page, _, _ = server._audit_violation_work_items_from_sqlite(tmp_path, page=2, page_size=1, project="MAIN")
    assert other_page[0]["id"] == queue[1]["id"]


def test_scope_filter_precedes_presentation_and_source_groups_remain_separate():
    rows = [_ordered_violation("a.ts", project="COMPANION", project_key="COMPANION", mode="critical"),
        _ordered_violation("b.ts", mode="advisory")]
    directives = contextos_mcp.build_agent_action_directives(audit_report={"violations":rows}, project_scope="MAIN",
        priority_pack={"items":[{"file":"attention.ts", "risk_tier":"critical"}]}, max_items=2)
    assert len(directives) == 2 and directives[0]["id"] == "audit_violation_1"
    assert directives[1]["id"] == "priority_finding_2"
    assert directives[0]["presentation_order"]["status"] == "applied"
    assert directives[1]["actionability"] == "orientation_only"


@pytest.mark.parametrize("path", [None, "", "../a.ts", "MAIN::a.ts", "a\\b.ts", "/a.ts"])
def test_missing_recorded_identity_preserves_visible_findings(path):
    rows = [_ordered_violation("b.ts", "first"), _ordered_violation("a.ts", "second", mode="critical")]
    rows[0]["atlas_rel_path"] = path
    selected, summary = finding_order.select_audit_presentation_rows(rows, limit=1)
    assert selected == rows[:1] and summary["status"] == "unavailable"
    directives = contextos_mcp.build_agent_action_directives(audit_report={"violations":rows}, max_items=1)
    assert directives[0]["evidence"] == "first" and directives[0]["presentation_order"]["status"] == "unavailable"


def test_missing_descriptor_keeps_packet_visibility_but_refuses_sqlite_fallback(tmp_path, monkeypatch):
    rows = [_ordered_violation("a.ts")]
    _presentation_queue(tmp_path, rows)
    monkeypatch.setattr(finding_order, "AUDIT_PRESENTATION_CONTRACT_PATH", tmp_path / "missing-contract.json")
    selected, summary = finding_order.select_audit_presentation_rows(rows, limit=1)
    assert selected == rows and summary["status"] == "unavailable"
    assert server._audit_violation_work_items_from_sqlite(tmp_path, page=1, page_size=10) == ([], 0, False)


@pytest.mark.parametrize("bad", ["unsafe_name'; DROP TABLE findings;--", True, -1])
def test_descriptor_rejects_invalid_sql_literals(tmp_path, monkeypatch, bad):
    order = finding_order.load_audit_presentation_order()
    if isinstance(bad, str):
        order["severity_tiers"][bad] = 0
    else:
        order["unknown_tier"] = bad
    path = tmp_path / "policy.json"
    path.write_text(json.dumps({"work_queue_brief_policy":{"audit_presentation_order":order}}), encoding="utf-8")
    monkeypatch.setattr(finding_order, "AUDIT_PRESENTATION_CONTRACT_PATH", path)
    with pytest.raises(ValueError):
        finding_order.load_audit_presentation_order()


@pytest.mark.parametrize("field,value", [("version", "v2"), ("basis", "causal_priority"),
    ("keys", ["stored_row_level_tier"]), ("authority_rule", None), ("authority_rule", " ")])
def test_incomplete_descriptor_keeps_visibility_without_certifying_order(tmp_path, monkeypatch, field, value):
    order = finding_order.load_audit_presentation_order()
    order[field] = value
    path = tmp_path / "policy.json"
    path.write_text(json.dumps({"work_queue_brief_policy":{"audit_presentation_order":order}}), encoding="utf-8")
    monkeypatch.setattr(finding_order, "AUDIT_PRESENTATION_CONTRACT_PATH", path)
    rows = [_ordered_violation("b.ts"), _ordered_violation("a.ts", mode="critical")]
    selected, summary = finding_order.select_audit_presentation_rows(rows, limit=1)
    assert selected == rows[:1] and summary["status"] == "unavailable"
    brief = server._render_violation_work_queue_page({"artifact_trust":{"status":"FAIL"}}, visible_limit=1)
    assert "presentation_order_rule:" in brief and "ordering is not certified" in brief


def test_policy_tier_change_reaches_sql_packet_module_and_queue_brief(tmp_path, monkeypatch):
    raw_dir = tmp_path / ".raw"
    raw_dir.mkdir()
    rows = [_ordered_violation("a.ts", "critical row", mode="critical"),
        _ordered_violation("b.ts", "advisory row", mode="advisory")]
    queue, total, ok = _presentation_queue(raw_dir, rows, size=1)
    assert ok and total == 2 and queue[0]["target_file"] == "a.ts"
    order = finding_order.load_audit_presentation_order()
    order["severity_tiers"]["advisory"] = 0
    order["severity_tiers"]["critical"] = 3
    path = tmp_path / "policy.json"
    path.write_text(json.dumps({"work_queue_brief_policy":{"audit_presentation_order":order}}), encoding="utf-8")
    monkeypatch.setattr(finding_order, "AUDIT_PRESENTATION_CONTRACT_PATH", path)
    queue, total, ok = server._audit_violation_work_items_from_sqlite(raw_dir, page=1, page_size=1, project="MAIN")
    directives = contextos_mcp.build_agent_action_directives(audit_report={"violations":rows}, max_items=1)
    modules, module_total, module_ok = server._module_integrity_items_from_sqlite(raw_dir, "", max_items=1)
    assert ok and module_ok and total == module_total == 2
    assert queue[0]["evidence"] == directives[0]["evidence"] == modules[0]["evidence"] == "advisory row"
    assert directives[0]["presentation_order"] == queue[0]["presentation_order"]
    assert server._work_queue_brief_policy()["presentation_order_rule"] == order["authority_rule"]
    brief = server._render_violation_work_queue_page({"items":queue, "total_violations":2,
        "artifact_trust":{"status":"PASS"}}, visible_limit=1)
    assert "presentation_order_rule:" in brief and order["authority_rule"] in brief


def test_unique_omitted_paths_do_not_serialize_whole_rows_or_construct_directives(monkeypatch):
    rows = [_ordered_violation(f"file{i:04}.ts") for i in range(500, 0, -1)]
    with patch.object(finding_order, "canonical_audit_finding_message", wraps=finding_order.canonical_audit_finding_message) as serialize:
        selected, summary = finding_order.select_audit_presentation_rows(rows, limit=2)
    assert [r["atlas_rel_path"] for r in selected] == ["file0001.ts", "file0002.ts"]
    assert serialize.call_count == 1  # Descriptor identity only; no finding-prefix ties.
    with patch.object(contextos_mcp, "_directive_for_violation", wraps=contextos_mcp._directive_for_violation) as construct:
        directives = contextos_mcp.build_agent_action_directives(audit_report={"violations":rows}, max_items=2)
    assert len(directives) == construct.call_count == 2 and summary["status"] == "applied"


def test_public_operator_and_surgical_brief_keep_presentation_boundary():
    from tools.generate_nexora_operator_packet import _public_agent_directives
    rows = [_ordered_violation("a.ts")]
    directive = contextos_mcp.build_agent_action_directives(audit_report={"violations":rows}, max_items=1)[0]
    public = _public_agent_directives([directive])[0]
    assert public["presentation_order"]["candidate_universe_equality"] == "not_evaluated"
    assert "message" not in public["presentation_order"]
    assert "presentation_order" in contextos_mcp.render_surgical_operation_brief({"agent_action_directives":[directive]})


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
