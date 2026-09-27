from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path
from unittest.mock import patch

from tools import inspect_target
from tools.mcp import server
from tools.validate_mcp_agent_surface import (
    _confidence_emitted_action_keys,
    _patch_validation_emitted_next_actions,
    _skill_declares_supporting_context_profile,
)


def test_patch_action_contract_extractor_covers_branches_without_collecting_decoys() -> None:
    source = '''
def unrelated():
    next_action = "decoy"

def _render_patch_validation_brief(payload):
    if payload:
        next_action = "repair"
    else:
        next_action = "apply" if payload is None else "revise"
    return next_action
'''

    assert _patch_validation_emitted_next_actions(source) == {"repair", "apply", "revise"}


def test_confidence_action_key_extractor_covers_normalizer_branches_without_decoys() -> None:
    source = '''
def unrelated():
    return _confidence_recommended_action("decoy")

def _normalize_confidence_payload_for_agent(payload):
    action = _confidence_recommended_action("target_not_grounded")
    if payload:
        action = _confidence_recommended_action("dependency_evidence_unavailable")
    return _confidence_recommended_action("elevated_risk") if payload else _confidence_recommended_action("bounded_patch")
'''

    assert _confidence_emitted_action_keys(source) == {
        "target_not_grounded",
        "dependency_evidence_unavailable",
        "elevated_risk",
        "bounded_patch",
    }


def test_supporting_context_skill_guidance_requires_profile_and_retry_boundary() -> None:
    complete = """
Tools classified as `supporting_context` are not part of
`target_repository_default`. After a concrete target exists, switch to
`target_repository_followup`; do not retry the same call unchanged.
"""
    assert _skill_declares_supporting_context_profile(complete)
    assert not _skill_declares_supporting_context_profile(
        "Supporting tools are available when needed."
    )


def test_confidence_recommended_action_is_contract_driven(monkeypatch) -> None:
    monkeypatch.setattr(
        server,
        "_confidence_brief_policy",
        lambda: {
            "recommended_actions": {
                "target_not_grounded": "contract_target_refresh",
                "dependency_evidence_unavailable": "contract_dependency_refresh",
                "elevated_risk": "contract_elevated_review",
                "bounded_patch": "contract_bounded_patch",
            },
            "missing_policy_action": "contract_missing_policy",
        },
    )
    normalized = server._normalize_confidence_payload_for_agent(
        {
            "target_exists": True,
            "target_indexed": True,
            "confidence_matrix": {
                "merge_safety": "SAFE",
                "architecture_drift_certainty": 0.95,
                "dead_code_confidence": 0.97,
            },
            "input_evidence": {"circular_deps": {"status": "PASS"}},
        }
    )

    assert normalized["recommended_action"] == "contract_elevated_review"

    bounded = server._normalize_confidence_payload_for_agent(
        {
            "target_exists": True,
            "target_indexed": True,
            "confidence_matrix": {
                "merge_safety": "SAFE",
                "architecture_drift_certainty": 0.1,
                "dead_code_confidence": 0.95,
            },
            "input_evidence": {"circular_deps": {"status": "PASS"}},
        }
    )

    assert bounded["recommended_action"] == "contract_bounded_patch"


def test_confidence_recommended_action_fails_closed_when_mapping_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        server,
        "_confidence_brief_policy",
        lambda: {
            "recommended_actions": {},
            "missing_policy_action": "   ",
        },
    )

    assert server._confidence_recommended_action("elevated_risk") == "confidence_action_unavailable_due_to_invalid_contract"


def _confidence_engine_result(target: str) -> dict:
    return {
        "target": target,
        "confidence_matrix": {
            "dead_code_confidence": 0.97,
            "merge_safety": "SAFE",
            "architecture_drift_certainty": 0.1,
            "dynamic_magic_hazard": 0.0,
        },
        "metrics": {
            "loc": 12,
            "blast_radius_dependents": 0,
            "cyclic_member": False,
            "reflection_indicators_found": [],
            "risk_mitigation_reasons": [
                "File adheres completely to standard static and architectural safety constraints."
            ],
        },
        "input_evidence": {
            "circular_deps": {"status": "PASS", "source": "sqlite", "shape_status": "valid"}
        },
        "verdict": "Low Risk Level",
        "decision_boundary": "Confidence is a risk estimate, not a standalone merge or deploy approval.",
    }


def test_default_confidence_resolves_repo_path_before_engine_evaluation() -> None:
    canonical_node = "MAIN::platform/ai/ai/schemas.ts"
    context = {
        "repo_relative_path": "src/platform/ai/ai/schemas.ts",
        "atlas_relative_path": "platform/ai/ai/schemas.ts",
        "atlas_node": canonical_node,
    }
    target_status = {"exists": True, "indexed": True}
    with (
        patch.object(server, "_raw_dir_for_target", return_value=Path("unused")),
        patch.object(server, "_resolve_target_node_from_raw", return_value=(canonical_node, context)),
        patch.object(server, "_target_path_status", return_value=target_status),
        patch.object(
            server,
            "_calibrate_confidence_with_sqlite_impact",
            side_effect=lambda _raw, _target, _root, result: result,
        ),
        patch.object(
            server,
            "_record_mcp_call_result",
            side_effect=lambda _tool, _started, result, **_kwargs: result,
        ),
        patch(
            "tools.engines.confidence_engine.evaluate_file_confidence",
            return_value=_confidence_engine_result(canonical_node),
        ) as evaluate,
    ):
        payload = json.loads(
            server.get_confidence_score(
                "src/platform/ai/ai/schemas.ts",
                format="json",
            )
        )

    evaluate.assert_called_once_with(canonical_node)
    assert payload["target_ref"] == "MAIN::src/platform/ai/ai/schemas.ts"
    assert payload["target_file"] == "src/platform/ai/ai/schemas.ts"
    assert payload["target_grounding_status"] == "grounded"


def test_default_confidence_missing_target_does_not_borrow_an_indexed_file() -> None:
    missing_node = "MAIN::src/missing.ts"
    context = {
        "repo_relative_path": "src/missing.ts",
        "atlas_relative_path": "src/missing.ts",
        "atlas_node": missing_node,
    }
    with (
        patch.object(server, "_raw_dir_for_target", return_value=Path("unused")),
        patch.object(server, "_resolve_target_node_from_raw", return_value=(missing_node, context)),
        patch.object(server, "_target_path_status", return_value={"exists": False, "indexed": False}),
        patch.object(
            server,
            "_calibrate_confidence_with_sqlite_impact",
            side_effect=lambda _raw, _target, _root, result: result,
        ),
        patch.object(
            server,
            "_record_mcp_call_result",
            side_effect=lambda _tool, _started, result, **_kwargs: result,
        ),
        patch(
            "tools.engines.confidence_engine.evaluate_file_confidence",
            return_value=_confidence_engine_result(missing_node),
        ) as evaluate,
    ):
        payload = json.loads(server.get_confidence_score("src/missing.ts", format="json"))

    evaluate.assert_called_once_with(missing_node)
    assert payload["target"] == missing_node
    assert payload["target_exists"] is False
    assert payload["target_indexed"] is False
    assert payload["target_grounding_status"] == "missing_or_unindexed"
    assert payload["confidence_matrix"]["merge_safety"] == "UNKNOWN_TARGET_NOT_GROUNDED"
    assert payload["recommended_action"] == "refresh_target_analysis_before_confidence_decision"


def test_sqlite_confidence_reason_is_present_when_engine_already_matches_risk() -> None:
    result = _confidence_engine_result("MAIN::shared/utils/text.ts")
    result["confidence_matrix"]["merge_safety"] = "CRITICAL"
    result["reasons"] = ["Highly volatile dependent count (Blast radius dependents: 20)"]
    with (
        patch.object(
            server,
            "_sqlite_impact_radius_from_raw",
            return_value={"direct_dependents_count": 20, "blast_radius_size": 155},
        ),
        patch.object(
            server,
            "require_doctrine_mapping",
            return_value={"critical_dep_threshold": 10, "low_dep_threshold": 5},
        ),
    ):
        calibrated = server._calibrate_confidence_with_sqlite_impact(
            Path("unused"), "src/shared/utils/text.ts", "", result
        )

    sqlite_reason = "SQLite impact radius reports 20 direct dependents and 155 total impacted files."
    assert calibrated["confidence_matrix"]["merge_safety"] == "CRITICAL"
    assert calibrated["metrics"]["confidence_dependency_source"] == "sqlite_dependencies"
    assert calibrated["reasons"].count(sqlite_reason) == 1
    assert sqlite_reason not in result["reasons"]


def test_sqlite_confidence_zero_dependents_does_not_invent_risk_reason() -> None:
    result = _confidence_engine_result("MAIN::src/isolated.ts")
    with (
        patch.object(
            server,
            "_sqlite_impact_radius_from_raw",
            return_value={"direct_dependents_count": 0, "blast_radius_size": 0},
        ),
        patch.object(
            server,
            "require_doctrine_mapping",
            return_value={"critical_dep_threshold": 10, "low_dep_threshold": 5},
        ),
    ):
        calibrated = server._calibrate_confidence_with_sqlite_impact(
            Path("unused"), "src/isolated.ts", "", result
        )

    assert calibrated["confidence_matrix"]["merge_safety"] == "SAFE"
    assert not any("SQLite impact radius reports" in reason for reason in calibrated["reasons"])


def test_agent_artifact_query_is_read_only_when_evidence_is_stale(tmp_path: Path) -> None:
    stale = {
        "status": "FAIL",
        "failures": [{"name": "freshness:audit_not_older_than_atlas"}],
        "warnings": [],
    }
    with (
        patch.object(server, "build_artifact_trust_summary", return_value=stale),
        patch.object(server, "_run_python_script") as runner,
    ):
        payload = server._ensure_agent_artifact_chain_current(tmp_path)

    assert payload["status"] == "FAIL"
    assert payload["auto_refresh"] == {
        "attempted": False,
        "reason": "read_only_query_surface",
        "required_action": "Refresh evidence explicitly, then repeat this query.",
        "command": 'python sage.py run --step "Quality Gates" --refresh',
    }
    runner.assert_not_called()


def test_external_agent_artifact_query_returns_explicit_refresh_command(tmp_path: Path) -> None:
    with patch.object(
        server,
        "build_artifact_trust_summary",
        return_value={"status": "FAIL", "failures": [], "warnings": []},
    ):
        payload = server._ensure_agent_artifact_chain_current(tmp_path, target_root="C:/target repo")

    assert payload["auto_refresh"]["attempted"] is False
    assert payload["auto_refresh"]["reason"] == "read_only_query_surface"
    assert payload["auto_refresh"]["command"] == (
        'python sage.py run --step "Quality Gates" --target-root "C:/target repo" --refresh'
    )


def test_violation_queue_returns_consumer_specific_bounded_recovery(tmp_path: Path) -> None:
    stale = {
        "status": "FAIL",
        "checks": [],
        "failures": [{"name": "freshness:audit_not_older_than_atlas"}],
        "warnings": [],
    }
    with (
        patch.object(server, "_raw_dir_for_target", return_value=tmp_path),
        patch.object(server, "_ensure_agent_artifact_chain_current", return_value=stale),
    ):
        payload = json.loads(
            server.get_violation_work_queue(
                project="MAIN",
                target_root="C:/target repo",
                format="json",
            )
        )

    plan = payload["recovery_plan"]
    assert payload["status"] == "INVALID_CONTEXT"
    assert plan["consumer"] == "get_violation_work_queue"
    assert plan["authority_chain"] == ["atlas", "audit_report"]
    assert plan["requested_projects"] == ["MAIN"]
    assert plan["command_argv"] == [
        "python",
        "sage.py",
        "run",
        "--step",
        "Audit",
        "--projects",
        "MAIN",
        "--target-root",
        "C:/target repo",
    ]
    assert "--refresh" not in plan["command_argv"]
    assert "Quality Gates" not in plan["command_argv"]
    assert plan["predicted_steps"] == ["Atlas if stale", "Nuclear Sequencing", "Audit"]
    assert plan["predicted_duration"]["status"] == "unavailable"


def test_blast_radius_accepts_canonical_target_ref_and_explains_machine_result(tmp_path: Path) -> None:
    artifact = {
        "blast_radius": [
            {
                "file": "MAIN::platform/export/ExportManager.ts",
                "direct_dependents": 2,
                "transitive_dependents": 127,
                "total_impact_score": 68.1,
            }
        ]
    }
    with (
        patch.object(server, "_raw_dir_for_target", return_value=tmp_path),
        patch.object(server, "_artifact_or_missing", return_value=(artifact, None)),
        patch.object(
            server,
            "_resolve_target_node_from_raw",
            return_value=(
                "MAIN::platform/export/ExportManager.ts",
                {"repo_relative_path": "src/platform/export/ExportManager.ts"},
            ),
        ),
        patch.object(server, "_default_agent_scope_allows", return_value=True),
    ):
        payload = json.loads(
            server.get_blast_radius(
                "MAIN::src/platform/export/ExportManager.ts",
                format="json",
            )
        )

    assert payload["status"] == "ok"
    assert payload["query_mode"] == "canonical_target"
    assert payload["resolved_target"] == "MAIN::platform/export/ExportManager.ts"
    assert payload["items"][0]["transitive_dependents"] == 127
    assert "get_impact_radius" in payload["required_follow_up"]


def test_blast_radius_machine_result_explains_empty_query(tmp_path: Path) -> None:
    with (
        patch.object(server, "_raw_dir_for_target", return_value=tmp_path),
        patch.object(server, "_artifact_or_missing", return_value=({"blast_radius": []}, None)),
        patch.object(
            server,
            "_resolve_target_node_from_raw",
            return_value=("MAIN::missing.ts", {"repo_relative_path": "src/missing.ts"}),
        ),
    ):
        payload = json.loads(server.get_blast_radius("src/missing.ts", format="json"))

    assert payload["status"] == "no_actionable_items"
    assert payload["items"] == []
    assert payload["resolved_target_ref"] == "MAIN::src/missing.ts"
    assert payload["required_follow_up"] == (
        "Inspect or refresh a concrete target before requesting impact radius."
    )


def test_file_inspection_reads_blast_rows_instead_of_top_level_artifact_keys() -> None:
    matches = inspect_target._blast_matches_for_context(
        {
            "metrics": {"files_analyzed": 1},
            "blast_radius": [
                {
                    "file": "MAIN::platform/export/ExportManager.ts",
                    "direct_dependents": 2,
                    "transitive_dependents": 127,
                }
            ],
        },
        [{"atlas_node": "MAIN::platform/export/ExportManager.ts"}],
        "src/platform/export/ExportManager.ts",
    )

    assert len(matches) == 1
    assert matches[0]["key"] == "MAIN::platform/export/ExportManager.ts"
    assert matches[0]["value"]["direct_dependents"] == 2


def test_patch_brief_distinguishes_unchanged_resolved_and_baseline_counts() -> None:
    brief = server._render_patch_validation_brief(
        {
            "status": "PASS",
            "analysis_snapshot_id": "snapshot-17",
            "violations": [],
            "existing_violations": [],
            "resolved_violations": [{"rule": "deep_imports"}],
            "target_exists": True,
            "target_indexed": True,
            "target_file": "src/example.ts",
            "target_ref": "MAIN::src/example.ts",
            "current_audit_baseline": {
                "status": "available",
                "finding_count": 2,
            },
        },
        "src/example.ts",
    )

    assert "unchanged_sage_governance_violation_count: 0" in brief
    assert "resolved_sage_governance_violation_count: 1" in brief
    assert "patch_validator_baseline_violation_count: 1" in brief
    assert 'current_audit_baseline_status: "available"' in brief
    assert "current_audit_finding_count: 2" in brief
    assert 'analysis_snapshot_id: "snapshot-17"' in brief


def test_patch_brief_resolves_applicability_failure_through_declared_action_contract() -> None:
    brief = server._render_patch_validation_brief(
        {
            "status": "FAIL",
            "violations": [],
            "target_exists": True,
            "target_indexed": True,
            "target_file": "src/example.ts",
            "target_ref": "MAIN::src/example.ts",
            "patch_applicability": {
                "status": "FAIL",
                "oracle": "git_apply_check",
                "reason": "patch_does_not_apply",
            },
            "target_native_policy_validation": {"status": "NOT_RUN"},
        },
        "src/example.ts",
    )

    assert 'next_action: "repair_exact_patch_payload_before_apply"' in brief
    assert "Missing patch_validation_next_actions contract entry" not in brief
    assert "safe_to_apply: false" in brief


def test_grounded_patch_validation_fails_closed_without_snapshot_identity(tmp_path: Path) -> None:
    with (
        patch.object(
            server,
            "_target_path_status",
            return_value={
                "target_ref": "MAIN::src/example.ts",
                "target_file": "src/example.ts",
                "exists": True,
                "indexed": True,
                "inside_root": True,
            },
        ),
        patch.object(server, "_analysis_snapshot_id", return_value=""),
        patch.object(server, "_operator_privileged_projection_allowed", return_value=False),
        patch.object(
            server,
            "resolve_execution_identity",
            return_value={"system_scope": "target_repository", "subject_root": str(tmp_path)},
        ),
        patch(
            "tools.engines.mcp_governance_engine.validate_proposed_patch",
            return_value={"status": "PASS", "violations": []},
        ),
        patch(
            "tools.core.seal_impact_guard.mutating_agent_surface_block",
            return_value={"blocked": False},
        ),
    ):
        payload = server.validate_patch(
            "src/example.ts",
            "@@ -1,1 +1,1 @@\n-old\n+new",
            format="json",
        )

    parsed = __import__("json").loads(payload)
    assert parsed["status"] == "INVALID_CONTEXT"
    assert parsed["safe_to_apply"] is False
    assert parsed["violations"][-1]["rule"] == "analysis_snapshot_identity_missing"


def test_patch_validation_attaches_current_audit_baseline_from_same_snapshot(tmp_path: Path) -> None:
    audit_finding = {
        "target_project": "MAIN",
        "target_file": "src/example.ts",
        "rule": "deep_imports",
    }
    with (
        patch.object(
            server,
            "_target_path_status",
            return_value={
                "target_ref": "MAIN::src/example.ts",
                "target_file": "src/example.ts",
                "exists": True,
                "indexed": True,
                "inside_root": True,
            },
        ),
        patch.object(server, "_analysis_snapshot_id", return_value="snapshot-18"),
        patch.object(
            server,
            "_audit_violation_work_items_from_sqlite",
            return_value=([audit_finding], 1, True),
        ) as audit_query,
        patch.object(server, "_operator_privileged_projection_allowed", return_value=False),
        patch.object(
            server,
            "resolve_execution_identity",
            return_value={"system_scope": "target_repository", "subject_root": str(tmp_path)},
        ),
        patch(
            "tools.engines.mcp_governance_engine.validate_proposed_patch",
            return_value={
                "status": "PASS",
                "safe_to_apply": True,
                "violations": [],
                "existing_violation_count": 0,
                "resolved_violation_count": 0,
            },
        ),
        patch(
            "tools.core.seal_impact_guard.mutating_agent_surface_block",
            return_value={"blocked": False},
        ),
        patch.object(
            server,
            "_check_exact_patch_applicability",
            return_value={
                "status": "PASS",
                "oracle": "git_apply_check",
                "mutation_performed": False,
            },
        ),
    ):
        payload = server.validate_patch(
            "src/example.ts",
            "@@ -1,1 +1,1 @@\n-old\n+new",
            format="json",
        )

    parsed = __import__("json").loads(payload)
    assert parsed["analysis_snapshot_id"] == "snapshot-18"
    assert parsed["status"] == "INCOMPLETE_EVIDENCE"
    assert parsed["overall_status"] == "INCOMPLETE_EVIDENCE"
    assert parsed["governance_status"] == "PASS"
    assert parsed["governance_validation_passed"] is True
    assert parsed["patch_applicability"]["status"] == "PASS"
    assert parsed["target_native_policy_validation"]["status"] == "NOT_RUN"
    assert parsed["safe_to_apply"] is False
    assert parsed["application_readiness"] == "APPLICABLE_REQUIRES_TARGET_NATIVE_VALIDATION"
    assert parsed["patch_validator_baseline_violation_count"] == 0
    assert parsed["current_audit_baseline"] == {
        "status": "available",
        "analysis_snapshot_id": "snapshot-18",
        "finding_count": 1,
        "findings": [audit_finding],
        "semantics": (
            "Current SQLite Audit findings for the grounded target file and project. "
            "This is broader current-state context, not the patch validator delta or "
            "target-repository-native enforcement truth."
        ),
    }
    audit_query.assert_called_once_with(
        server._raw_dir_for_target(""),
        page=1,
        page_size=100,
        project="MAIN",
        file_path="src/example.ts",
    )


def test_patch_validation_exposes_reachable_write_lease_profile_transition(tmp_path: Path) -> None:
    default_tools = server.project_mcp_tool_names(
        server.BASE_DIR,
        "target_repository_default",
    )["visible_tools"]
    with (
        patch.object(
            server,
            "_target_path_status",
            return_value={
                "target_ref": "MAIN::src/example.ts",
                "target_file": "src/example.ts",
                "exists": True,
                "indexed": True,
                "inside_root": True,
                "source_snapshot_hash": "snapshot-hash",
            },
        ),
        patch.object(server, "_analysis_snapshot_id", return_value="snapshot-lease"),
        patch.object(server, "_operator_privileged_projection_allowed", return_value=False),
        patch.object(
            server,
            "resolve_execution_identity",
            return_value={"system_scope": "target_repository", "subject_root": str(tmp_path)},
        ),
        patch(
            "tools.engines.mcp_governance_engine.validate_proposed_patch",
            return_value={
                "status": "PASS",
                "safe_to_apply": True,
                "violations": [],
                "existing_violation_count": 0,
                "resolved_violation_count": 0,
            },
        ),
        patch(
            "tools.core.target_write_lease.inspect_target_write_lease",
            return_value={"status": "not_found", "lease": None},
        ),
        patch(
            "tools.core.seal_impact_guard.mutating_agent_surface_block",
            return_value={"blocked": False},
        ),
        patch.object(
            server,
            "_check_exact_patch_applicability",
            return_value={"status": "PASS", "mutation_performed": False},
        ),
        patch.object(server.mcp, "active_tool_profile", "target_repository_default"),
        patch.object(server.mcp, "_visible_tool_names", frozenset(default_tools)),
    ):
        payload = server.validate_patch(
            "src/example.ts",
            "@@ -1,1 +1,1 @@\n-old\n+new",
            format="json",
            actor_id="agent-a",
        )

    parsed = json.loads(payload)
    lease_violation = next(
        row
        for row in parsed["violations"]
        if row.get("rule") == "target_write_lease_not_current"
    )
    action = lease_violation["recommended_action_contract"]
    assert parsed["safe_to_apply"] is False
    assert action["tool"] == "manage_target_write_lease"
    assert action["availability"] == "profile_transition_required"
    assert action["required_profile"] == "target_repository_followup"
    assert action["callable_in_required_profile"] is True
    assert action["arguments"] == {
        "action": "acquire",
        "target_file": "src/example.ts",
        "actor_id": "agent-a",
        "target_root": "",
    }
    assert "target_repository_followup" in lease_violation["recommended_action"]


def test_patch_validation_blocks_governance_pass_when_exact_payload_is_not_applicable(tmp_path: Path) -> None:
    with (
        patch.object(
            server,
            "_target_path_status",
            return_value={
                "target_ref": "MAIN::src/example.ts",
                "target_file": "src/example.ts",
                "exists": True,
                "indexed": True,
                "inside_root": True,
            },
        ),
        patch.object(server, "_analysis_snapshot_id", return_value="snapshot-19"),
        patch.object(server, "_operator_privileged_projection_allowed", return_value=False),
        patch.object(
            server,
            "resolve_execution_identity",
            return_value={"system_scope": "target_repository", "subject_root": str(tmp_path)},
        ),
        patch(
            "tools.engines.mcp_governance_engine.validate_proposed_patch",
            return_value={
                "status": "PASS",
                "safe_to_apply": True,
                "violations": [],
                "existing_violation_count": 0,
                "resolved_violation_count": 0,
            },
        ),
        patch(
            "tools.core.seal_impact_guard.mutating_agent_surface_block",
            return_value={"blocked": False},
        ),
        patch.object(
            server,
            "_check_exact_patch_applicability",
            return_value={
                "status": "FAIL",
                "oracle": "git_apply_check",
                "returncode": 1,
                "mutation_performed": False,
                "stderr_excerpt": "patch does not apply",
            },
        ),
    ):
        payload = server.validate_patch(
            "src/example.ts",
            "@@ -1,1 +1,1 @@\n-old\n+new",
            format="json",
        )

    parsed = json.loads(payload)
    assert parsed["status"] == "FAIL"
    assert parsed["governance_validation_passed"] is True
    assert parsed["safe_to_apply"] is False
    assert parsed["application_readiness"] == "NOT_APPLICABLE"
    assert parsed["violations"][-1]["rule"] == "exact_patch_not_applicable"


def test_full_replacement_reaches_human_review_before_applicability_gate(tmp_path: Path) -> None:
    target = tmp_path / "src" / "example.ts"
    target.parent.mkdir(parents=True)
    target.write_text("export const oldValue = 1;\n", encoding="utf-8")
    with (
        patch.object(
            server,
            "_target_path_status",
            return_value={
                "target_ref": "MAIN::src/example.ts",
                "target_file": "src/example.ts",
                "target_abs": str(target),
                "exists": True,
                "indexed": True,
                "inside_root": True,
            },
        ),
        patch.object(server, "_analysis_snapshot_id", return_value="snapshot-full"),
        patch.object(server, "_operator_privileged_projection_allowed", return_value=False),
        patch.object(
            server,
            "resolve_execution_identity",
            return_value={"system_scope": "target_repository", "subject_root": str(tmp_path)},
        ),
        patch(
            "tools.engines.mcp_governance_engine.validate_proposed_patch",
            return_value={"status": "PASS", "safe_to_apply": True, "violations": []},
        ),
        patch(
            "tools.core.seal_impact_guard.mutating_agent_surface_block",
            return_value={"blocked": False},
        ),
    ):
        payload = server.validate_patch(
            "src/example.ts",
            "export const newValue = 2;\n",
            format="json",
        )

    parsed = json.loads(payload)
    assert parsed["patch_applicability"]["status"] == "NOT_RUN"
    assert parsed["full_replacement_patch"] is True
    assert parsed["status"] == "REVIEW_REQUIRED"
    assert parsed["safe_to_apply"] is False
    assert parsed["human_approval_required"] is True
    assert parsed["application_readiness"] == "HUMAN_APPROVAL_REQUIRED"
    assert parsed["violations"][-1]["rule"] == "mcp_full_replacement_requires_human_approval"


def test_already_current_full_replacement_is_terminal_no_op_without_mutation_gates(
    tmp_path: Path,
) -> None:
    target = tmp_path / "src" / "example.ts"
    target.parent.mkdir(parents=True)
    current_content = "export const value = 1;\n"
    target.write_text(current_content, encoding="utf-8")
    with (
        patch.object(
            server,
            "_target_path_status",
            return_value={
                "target_ref": "MAIN::src/example.ts",
                "target_file": "src/example.ts",
                "target_abs": str(target),
                "exists": True,
                "indexed": True,
                "inside_root": True,
            },
        ),
        patch.object(server, "_analysis_snapshot_id", return_value="snapshot-no-op"),
        patch.object(server, "_operator_privileged_projection_allowed", return_value=False),
        patch.object(
            server,
            "resolve_execution_identity",
            return_value={"system_scope": "target_repository", "subject_root": str(tmp_path)},
        ),
        patch(
            "tools.engines.mcp_governance_engine.validate_proposed_patch",
            return_value={
                "status": "NO_OP",
                "safe_to_apply": False,
                "no_op_patch": True,
                "violations": [],
            },
        ),
        patch(
            "tools.core.seal_impact_guard.mutating_agent_surface_block",
            return_value={"blocked": True, "message": "stale seal"},
        ),
    ):
        payload = server.validate_patch(
            "src/example.ts",
            current_content,
            actor_id="agent-a",
            format="json",
        )

    parsed = json.loads(payload)
    assert parsed["status"] == "NO_OP"
    assert parsed["safe_to_apply"] is False
    assert parsed["application_readiness"] == "NO_CHANGE"
    assert parsed["full_replacement_patch"] is True
    assert parsed["human_approval_required"] is False
    assert parsed["target_write_lease"]["status"] == "not_required_no_op"
    assert parsed["seal_impact_guard"]["status"] == "not_required_no_op"
    assert parsed["seal_impact_guard"]["blocked"] is False
    assert parsed["non_actionable_seal_observation"]["blocked"] is True
    assert parsed["violations"] == []
    assert parsed["non_actionable_patch_facts"] == {
        "full_replacement_transport_detected": True,
        "write_lease_required": False,
        "seal_revalidation_required": False,
    }


def test_exact_patch_applicability_preserves_oracle_failure_without_mutation(
    tmp_path: Path,
) -> None:
    completed = subprocess.CompletedProcess(
        ["git", "apply"],
        returncode=1,
        stdout="",
        stderr="patch failed: mixed line endings",
    )
    with patch.object(server, "run_observed_subprocess", return_value=(completed, 0.02)):
        result = server._check_exact_patch_applicability(
            "diff --git a/src/example.ts b/src/example.ts\n@@ -1 +1 @@\n-old\n+new\n",
            tmp_path,
        )

    assert result["status"] == "FAIL"
    assert result["returncode"] == 1
    assert result["mutation_performed"] is False
    assert "mixed line endings" in result["stderr_excerpt"]


def test_live_colocated_test_scan_finds_untracked_direct_import(tmp_path: Path) -> None:
    source = tmp_path / "src" / "feature.ts"
    direct_test = tmp_path / "src" / "feature.test.ts"
    naming_only = tmp_path / "src" / "feature.spec.ts"
    source.parent.mkdir(parents=True)
    source.write_text("export const feature = true;\n", encoding="utf-8")
    direct_test.write_text(
        "import { feature } from './feature';\ntest('feature', () => expect(feature).toBe(true));\n",
        encoding="utf-8",
    )
    naming_only.write_text("test('unrelated', () => expect(true).toBe(true));\n", encoding="utf-8")

    rows = server._direct_colocated_test_candidates(tmp_path, "src/feature.ts")

    assert [row["repo_relative_path"] for row in rows] == ["src/feature.test.ts"]
    assert rows[0]["type"] == "Direct Co-located Static Import"
    assert rows[0]["confidence"] == 1.0
    assert rows[0]["source"] == "live_bounded_sibling_scan"


def test_live_colocated_test_scan_budget_ignores_unrelated_siblings(tmp_path: Path) -> None:
    source = tmp_path / "src" / "z_feature.ts"
    source.parent.mkdir(parents=True)
    source.write_text("export const feature = true;\n", encoding="utf-8")
    for index in range(220):
        (source.parent / f"a_unrelated_{index:03d}.ts").write_text(
            "export const unrelated = true;\n",
            encoding="utf-8",
        )
    direct_test = source.parent / "z_feature.test.ts"
    direct_test.write_text(
        "import { feature } from './z_feature';\ntest('feature', () => expect(feature).toBe(true));\n",
        encoding="utf-8",
    )

    rows = server._direct_colocated_test_candidates(tmp_path, "src/z_feature.ts")

    assert [row["repo_relative_path"] for row in rows] == ["src/z_feature.test.ts"]


def test_default_repository_test_impact_merges_live_untracked_direct_test(tmp_path: Path) -> None:
    source = tmp_path / "src" / "platform" / "projectValidator.ts"
    direct_test = source.with_name("projectValidator.test.ts")
    source.parent.mkdir(parents=True)
    source.write_text("export const validate = () => true;\n", encoding="utf-8")
    direct_test.write_text(
        "import { validate } from './projectValidator';\n"
        "test('validates', () => expect(validate()).toBe(true));\n",
        encoding="utf-8",
    )
    transitive = {
        "target": "MAIN::src/platform/projectValidator.ts",
        "impacted_tests": [
            {
                "file": "src/integration.test.ts",
                "repo_relative_path": "src/integration.test.ts",
                "confidence": 0.6,
            }
        ],
    }
    with (
        patch("tools.engines.test_impact_matcher.find_impacted_tests", return_value=transitive),
        patch.object(server, "_raw_dir_for_target", return_value=tmp_path / ".raw"),
        patch.object(
            server,
            "_resolve_target_node_from_raw",
            return_value=(
                "MAIN::src/platform/projectValidator.ts",
                {"repo_relative_path": "src/platform/projectValidator.ts", "project": "MAIN"},
            ),
        ),
        patch.object(server, "_analysis_root_display", return_value=str(tmp_path)),
        patch.object(
            server,
            "_target_path_status",
            return_value={"exists": True, "indexed": True},
        ),
        patch.object(server, "_attach_test_source_snippets", side_effect=lambda _raw, payload: payload),
    ):
        payload = json.loads(
            server.get_test_impact(
                "src/platform/projectValidator.ts",
                format="json",
            )
        )

    assert payload["impacted_tests"][0]["file"] == "src/platform/projectValidator.test.ts"
    assert payload["impacted_tests"][0]["confidence"] == 1.0
    assert payload["impacted_tests"][0]["source"] == "live_bounded_sibling_scan"
    assert payload["impacted_tests"][1]["file"] == "src/integration.test.ts"


def test_stale_directive_moves_to_refresh_lane_not_operation_queue(tmp_path: Path) -> None:
    directives = [{"id": "old", "target_files": ["src/Old.tsx"], "intent": "fix"}]
    with patch.object(
        server,
        "_target_path_status",
        return_value={
            "target_ref": "MAIN::src/Old.tsx",
            "target_file": "src/Old.tsx",
            "exists": True,
            "indexed": True,
            "source_snapshot_status": "ok",
            "drift_check_status": "mismatch",
        },
    ):
        actionable, refresh_required = server._partition_directives_by_source_freshness(
            tmp_path,
            directives,
        )

    assert actionable == []
    assert refresh_required[0]["queue_lane"] == "refresh_required"
    assert refresh_required[0]["mutation_allowed"] is False
    assert refresh_required[0]["refresh_command_argv"] == [
        "python",
        "sage.py",
        "watch",
        "--once",
        "--path",
        "src/Old.tsx",
    ]


def test_stale_external_directive_preserves_target_root_in_exact_refresh(tmp_path: Path) -> None:
    directives = [{"id": "old", "target_files": ["src/Old.tsx"], "intent": "fix"}]
    target_root = str(tmp_path / "target")
    with patch.object(
        server,
        "_target_path_status",
        return_value={
            "target_ref": "MAIN::src/Old.tsx",
            "target_file": "src/Old.tsx",
            "exists": True,
            "indexed": True,
            "source_snapshot_status": "ok",
            "drift_check_status": "mismatch",
        },
    ):
        _, refresh_required = server._partition_directives_by_source_freshness(
            tmp_path,
            directives,
            target_root=target_root,
        )

    assert refresh_required[0]["refresh_command_argv"] == [
        "python",
        "sage.py",
        "watch",
        "--once",
        "--target-root",
        target_root,
        "--path",
        "src/Old.tsx",
    ]


def test_watchdog_reader_fails_closed_on_incomplete_execution_identity(tmp_path: Path) -> None:
    session_path = tmp_path / "watchdog_session.json"
    session_path.write_text("{}", encoding="utf-8")
    incomplete_session = {
        "meta": {"generated_at": "2026-07-29T00:00:00+00:00"},
        "summary": {"input_origin": "smoke_sample"},
        "target_descriptor": {
            "proof_debt_field": "target_repository_deep_proof_debt",
        },
        "proof_boundary": {
            "recommended_deep_proof": "not_available_policy_contract_incomplete",
        },
        "input_origin": "smoke_sample",
        "input_files": ["src/App.tsx"],
        "changed_files": [],
        "sample_files": ["src/App.tsx"],
        "violations": [],
    }

    def load_fixture(path: Path):
        return incomplete_session if Path(path).name == "watchdog_session.json" else {}

    with (
        patch.object(server, "_watchdog_session_roots", return_value=(tmp_path, tmp_path, "")),
        patch.object(server, "_load_json", side_effect=load_fixture),
        patch.object(server, "_raw_artifact_source_mtime", return_value=1.0),
        patch.object(
            server,
            "_record_mcp_call_result",
            side_effect=lambda _name, _started, result, **_kwargs: result,
        ),
    ):
        rendered = server.get_watchdog_session()

    assert "status: watchdog_session_identity_incomplete" in rendered
    assert 'status: "incomplete"' in rendered
    assert '"system_scope"' in rendered
    assert 'status: "not_created"' in rendered


def test_watchdog_reader_preserves_filesystem_acquisition_decision(tmp_path: Path) -> None:
    session_path = tmp_path / "watchdog_session.json"
    session_path.write_text("{}", encoding="utf-8")
    session = {
        "meta": {"generated_at": "2026-09-13T00:00:00+00:00"},
        "summary": {
            "input_origin": "filesystem_event",
            "analysis_status": "operator_confirmation_required",
            "input_files": 0,
            "changed_files": 0,
            "integrity": "UNKNOWN",
            "violation_count": 0,
            "claim_boundary": "surgical_change_context",
            "raw_event_count": 680,
            "deduplicated_path_count": 680,
            "metadata_only_count": 0,
            "unknown_path_count": 680,
            "scope_decision_status": "operator_confirmation_required",
        },
        "target_descriptor": {
            "system_scope": "SAGE_ON_REPOSITORY",
            "acquisition_mode": "DEFAULT_WORKSPACE",
            "profile_id": "target_repository_default",
            "subject_root": str(tmp_path),
            "artifact_strategy": "primary_workspace",
            "proof_debt_field": "target_repository_deep_proof_debt",
        },
        "producer": {"kind": "live_filesystem_observer", "command": ["python", "sage.py", "watch"]},
        "proof_boundary": {"recommended_deep_proof": "python sage.py run --profile release-deep"},
        "path_contract": {"analysis_root": str(tmp_path)},
        "input_origin": "filesystem_event",
        "input_files": [],
        "changed_files": [],
        "sample_files": [],
        "acquisition": {
            "held_file_count": 680,
            "scope_decision": {
                "status": "operator_confirmation_required",
                "reason": "large_event_scope_contains_unresolved_content_identity",
                "unknown_path_count": 680,
                "selected_path_count": 0,
                "held_path_count": 680,
                "silent_scope_truncation": False,
            },
            "filesystem_event_provenance": {
                "raw_event_count": 680,
                "deduplicated_path_count": 680,
                "duplicate_event_count": 0,
            },
        },
        "target_repository_deep_proof_debt": {"status": "current"},
        "watchdog_pulse_ledger": {"status": "CLEAR", "unresolved_unread_count": 0},
        "violations": [],
    }

    def load_fixture(path: Path):
        return session if Path(path).name == "watchdog_session.json" else {}

    with (
        patch.object(server, "_watchdog_session_roots", return_value=(tmp_path, tmp_path, "")),
        patch.object(server, "_load_json", side_effect=load_fixture),
        patch.object(server, "_raw_artifact_source_mtime", return_value=1.0),
        patch.object(
            server,
            "_record_mcp_call_result",
            side_effect=lambda _name, _started, result, **_kwargs: result,
        ),
    ):
        rendered = server.get_watchdog_session()

    assert "status: watchdog_session_available" in rendered
    assert 'analysis_status: "operator_confirmation_required"' in rendered
    assert 'scope_decision_status: "operator_confirmation_required"' in rendered
    assert "raw_event_count: 680" in rendered
    assert "held_path_count: 680" in rendered
    assert "silent_scope_truncation: false" in rendered


def _state_focus_atlas():
    return {
        "MAIN": {"files": {
            "store.ts": {
                "workspace_rel": "src/store.ts", "hash": "a" * 64,
                "features": ["ZustandStore", "QueryKey:projects"],
                "symbols": [{"name": "useProjects", "line": 4, "end_line": 8,
                             "features": [], "dependencies": ["persist"],
                             "dependency_imports": [{"source": "./repository", "localName": "persist"}]}],
            },
            "other.ts": {"symbols": [{"name": "other"}]},
        }},
        "COMPANION": {"files": {"store.ts": {"symbols": [{"name": "useProjects"}]}}},
    }


def _state_focus(atlas=None, **selectors):
    from tools.core.state_flow import project_state_flow_focus
    return project_state_flow_focus(
        atlas if atlas is not None else _state_focus_atlas(),
        project=selectors.pop("project", "MAIN"), file=selectors.pop("file", ""),
        symbol=selectors.pop("symbol", ""), max_items=selectors.pop("max_items", 3),
        scan_limit=selectors.pop("scan_limit", 100), **selectors,
    )


def test_state_flow_focus_separates_file_signals_from_symbol_references():
    result = _state_focus(file="src/store.ts", symbol="useProjects")
    assert result["status"] == "selected"
    assert result["target"]["atlas_ref"] == "MAIN::store.ts"
    assert result["target"]["target_ref"] == "MAIN::src/store.ts"
    assert result["target"]["target_file"] == "src/store.ts"
    assert result["target"]["start_line"] == 4
    assert result["file_context"]["signals"]["has_zustand_store"] is True
    assert result["symbol_context"]["signals"]["has_zustand_store"] is False
    assert result["symbol_context"]["identifier_references"]["items"] == ["persist"]
    assert result["symbol_context"]["import_references"]["items"][0]["source"] == "./repository"
    assert result["unknowns"]["runtime_execution"] == "not_performed"
    assert result["unknowns"]["upstream_downstream_trace"] == "unavailable"
    assert result["unknowns"]["state_flow_artifact_join"] == "not_source_bound"


def test_state_flow_focus_is_exact_and_collision_preserving():
    atlas = _state_focus_atlas()
    assert _state_focus(atlas, symbol="useProjects")["status"] == "selected"
    atlas["MAIN"]["files"]["other.ts"]["symbols"] = [{"name": "useProjects", "line": 12}]
    ambiguous = _state_focus(atlas, symbol="useProjects", max_items=1)
    assert ambiguous["status"] == "ambiguous"
    assert ambiguous["matches"] == 2 and ambiguous["omitted"] == 1
    assert "file_context" not in ambiguous
    assert _state_focus(atlas, project="main", symbol="useProjects")["status"] == "project_not_found"
    assert _state_focus(atlas, file="store", symbol="useProjects")["status"] == "not_found"
    assert _state_focus(atlas, file="store.ts", symbol="useProject")["status"] == "not_found"


def test_state_flow_focus_budget_and_missing_are_not_absence_proof():
    result = _state_focus(symbol="useProjects", scan_limit=1)
    assert result["status"] == "incomplete_search"
    assert result["search_complete"] is False
    assert "file_context" not in result
    assert _state_focus({}, file="store.ts")["status"] == "atlas_unavailable"
    for file in ("../store.ts", "/store.ts", "C:/store.ts", "src/../store.ts", "COMPANION::store.ts"):
        assert _state_focus(file=file)["status"] == "invalid_selector"
    assert _state_focus(project="*", symbol="useProjects")["status"] == "invalid_selector"


def test_state_flow_focus_duplicate_symbols_and_unsafe_workspace_paths():
    atlas = _state_focus_atlas()
    record = atlas["MAIN"]["files"]["store.ts"]
    record["symbols"] *= 2
    assert _state_focus(atlas, file="store.ts", symbol="useProjects")["status"] == "ambiguous"
    record["workspace_rel"] = "../escape.ts"
    assert _state_focus(atlas, file="store.ts")["status"] == "evidence_unavailable"


def test_state_flow_focus_bounds_each_evidence_list():
    atlas = _state_focus_atlas()
    row = atlas["MAIN"]["files"]["store.ts"]
    row["features"] = [f"QueryKey:{index}" for index in range(8)]
    result = _state_focus(atlas, file="store.ts", max_items=2)
    assert len(result["file_context"]["signals"]["query_keys"]) == 2
    assert result["file_context"]["omitted_signals"]["query_keys"] == 6


def test_state_flow_focus_alias_collision_and_missing_signals_stay_explicit():
    atlas = _state_focus_atlas()
    assert _state_focus(atlas, file="MAIN::store.ts")["status"] == "selected"
    other = _state_focus(atlas, project="COMPANION", file="store.ts", symbol="useProjects")
    assert other["target"]["atlas_ref"] == "COMPANION::store.ts"
    assert other["file_context"]["status"] == "unavailable"
    atlas["MAIN"]["files"]["other.ts"]["workspace_rel"] = "store.ts"
    assert _state_focus(atlas, file="store.ts")["status"] == "ambiguous"
    atlas["MAIN"]["files"]["store.ts"]["workspace_rel"] = ["invalid"]
    assert _state_focus(atlas, file="store.ts")["status"] == "evidence_unavailable"


def _wire_state_focus(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "_raw_dir_for_target", lambda _target: tmp_path)
    monkeypatch.setattr(server, "_analysis_root_display", lambda _target="": str(tmp_path))
    monkeypatch.setattr(server, "_atlas", lambda **_kw: _state_focus_atlas())
    monkeypatch.setattr(server, "_target_path_status", lambda *_a, **_kw: {
        "resolved_node": "MAIN::store.ts", "target_file": "src/store.ts",
        "target_ref": "MAIN::src/store.ts", "inside_root": True,
        "source_snapshot_hash": "a" * 64, "source_snapshot_status": "ok",
        "drift_check_status": "match",
    })


def test_state_flow_focus_mcp_never_bypasses_selectors_or_loads_unbound_artifact(monkeypatch, tmp_path):
    _wire_state_focus(monkeypatch, tmp_path)
    original_load = server._load_json
    def forbidden(path, *_a, **_kw):
        assert Path(path).name != "state_flow.json", "Focused mode must not read the legacy state-flow artifact"
        return original_load(path, *_a, **_kw)
    monkeypatch.setattr(server, "_load_json", forbidden)
    monkeypatch.setattr(server, "_read_json_artifact", forbidden)
    for kwargs in ({"format": "json"}, {"format": "machine"}, {"full": True}):
        result = json.loads(server.get_state_flow(file="store.ts", symbol="useProjects", **kwargs))
        assert result["source_binding"] == "snapshot_and_live_match"
        assert result["target"]["target_ref"].startswith("MAIN::")
        assert "COMPANION" not in json.dumps(result)
    rendered = server.get_state_flow(file="store.ts", symbol="useProjects")
    assert "# State Flow Brief" in rendered and '"mode": "focused_orientation"' in rendered
    assert "file_only_not_selected_symbol" in rendered
    assert "not_source_bound" in rendered


def test_state_flow_focus_invalid_selector_does_not_materialize_atlas(monkeypatch, tmp_path):
    _wire_state_focus(monkeypatch, tmp_path)
    monkeypatch.setattr(server, "_atlas", lambda **_kw: (_ for _ in ()).throw(
        AssertionError("Invalid focus must not materialize the Atlas")))
    for file in ("../escape.ts", "COMPANION::store.ts"):
        result = json.loads(server.get_state_flow(file=file, symbol="useProjects", format="json"))
        assert result["status"] == "invalid_selector"
        assert result["visited_records"] == 0
        assert result["source_binding"] == "not_checked"


def test_state_flow_focus_mcp_rejects_snapshot_or_resolver_identity_mismatch(monkeypatch, tmp_path):
    _wire_state_focus(monkeypatch, tmp_path)
    for status in (
        {"resolved_node": "COMPANION::store.ts", "source_snapshot_status": "ok"},
        {"resolved_node": "MAIN::store.ts", "target_file": "src/store.ts", "inside_root": True,
         "source_snapshot_status": "ok", "source_snapshot_hash": "b" * 64, "drift_check_status": "match"},
        {"resolved_node": "MAIN::store.ts", "target_file": "src/store.ts", "inside_root": False,
         "source_snapshot_status": "ok", "source_snapshot_hash": "a" * 64, "drift_check_status": "match"},
    ):
        monkeypatch.setattr(server, "_target_path_status", lambda *_a, **_kw: status)
        result = json.loads(server.get_state_flow(file="store.ts", format="json"))
        assert result["source_binding"] == "unavailable"
        assert result["next_action"].startswith("Refresh")


def test_state_flow_focus_mcp_central_budget_survives_full_json(monkeypatch, tmp_path):
    _wire_state_focus(monkeypatch, tmp_path)
    atlas = _state_focus_atlas()
    atlas["MAIN"]["files"]["store.ts"]["symbols"][0]["dependencies"] = ["one", "two", "three"]
    monkeypatch.setattr(server, "_atlas", lambda **_kw: atlas)
    monkeypatch.setattr(server, "_state_flow_brief_policy", lambda: {"focus_max_items": 1, "focus_scan_limit": 100})
    result = json.loads(server.get_state_flow(file="store.ts", symbol="useProjects", full=True, max_items=9999))
    assert result["symbol_context"]["identifier_references"]["returned"] == 1
    assert result["symbol_context"]["identifier_references"]["omitted"] == 2
    monkeypatch.setattr(server, "_state_flow_brief_policy", lambda: {"focus_max_items": 1, "focus_scan_limit": 1})
    result = json.loads(server.get_state_flow(symbol="useProjects", full=True))
    assert result["status"] == "incomplete_search" and result["source_binding"] == "not_checked"


def test_state_flow_focus_mcp_keeps_external_failure_closed_and_legacy_default(monkeypatch, tmp_path):
    _wire_state_focus(monkeypatch, tmp_path)
    def invalid(_target):
        raise ValueError("invalid target")
    monkeypatch.setattr(server, "_raw_dir_for_target", invalid)
    monkeypatch.setattr(server, "_atlas", lambda **_kw: (_ for _ in ()).throw(AssertionError("fallback")))
    assert "invalid" in server.get_state_flow(file="store.ts", target_root="missing").lower()
    monkeypatch.setattr(server, "_raw_dir_for_target", lambda _target: tmp_path)
    monkeypatch.setattr(server, "_load_json", lambda _path: {"zustand_stores": {}})
    full = json.loads(server.get_state_flow(full=True))
    assert full["zustand_stores"] == {}
    assert full["mcp_consumer_binding"]["status"] == "unavailable"
    brief = server.get_state_flow()
    assert "# State Flow Brief" in brief and 'status: "unavailable"' in brief
    assert 'artifact_binding:' in brief and 'section": "zustand_stores"' in server.get_state_flow(max_items=1)


def test_state_flow_overview_uses_compact_owned_policy_without_replaying_focus_manual(monkeypatch):
    from tools.core.agent_packet_budget import BOUNDED_AGENT_PACKET_TOKENS, estimate_tokens
    policy = server._state_flow_brief_policy()
    brief = server._render_supporting_context_brief('State Flow Brief', {
        'surface': 'state_flow', 'filter': 'MAIN', 'analysis_root': 'C:/repo',
        'artifact_binding': {'status': 'stored_atlas_snapshot_match'},
        'items': [{'section': 'stores', 'summary': {
            'sample_keys': ['MAIN::src/store.ts'], 'sample_keys_omitted': 4,
            'sample_targets': [{'target_file': 'src/store.ts', 'target_ref': 'MAIN::src/store.ts'}],
            'sample_targets_omitted': 0,
        }}],
    })
    assert len(brief) <= 8000 and estimate_tokens(brief) <= BOUNDED_AGENT_PACKET_TOKENS
    assert policy['overview_max_items_semantics'] in brief
    assert policy['overview_agent_rule'] in brief
    assert policy['agent_rule'] not in brief
    assert 'max_items limits returned top-level state-flow sections' in brief
    assert 'sample_keys are graph references, not filesystem paths' in brief
    assert 'artifact_binding:' in brief and 'sample_keys_omitted' in brief
    assert 'config/agent_surface_contract.json:state_flow_brief_policy' in brief
    focus = server._render_supporting_context_brief('State Flow Brief', {
        'surface': 'state_flow', 'mode': 'focused_orientation', 'items': [],
    })
    assert policy['max_items_semantics'] in focus and policy['agent_rule'] in focus
    assert policy['overview_agent_rule'] not in focus and 'policy_scope: "focused"' in focus


def test_state_flow_compact_overview_does_not_rewrite_machine_evidence(monkeypatch, tmp_path):
    _wire_state_focus(monkeypatch, tmp_path)
    data = {'zustand_stores': {}, 'legacy_evidence': {'unknown': True}}
    monkeypatch.setattr(server, '_load_json', lambda _path: data)
    machine = json.loads(server.get_state_flow(full=True))
    assert all(machine[key] == value for key, value in data.items())
    assert machine['mcp_consumer_binding']['status'] == 'unavailable'
    brief = server.get_state_flow(max_items=1)
    assert 'status: "unavailable"' in brief and 'artifact_binding:' in brief
    assert 'section": "zustand_stores"' in brief and 'legacy_evidence' not in brief


def test_state_flow_missing_compact_policy_is_explicit_not_a_long_policy_fallback(monkeypatch):
    monkeypatch.setattr(server, '_agent_surface_contract', lambda: {
        'state_flow_brief_policy': {'agent_rule': 'long_decoy' * 1000}})
    policy = server._state_flow_brief_policy()
    assert 'missing' in policy['overview_agent_rule'].lower()
    assert 'missing' in policy['overview_max_items_semantics'].lower()
    assert 'long_decoy' not in policy['overview_agent_rule']


def test_state_flow_broad_mcp_requires_current_stored_atlas_snapshot(monkeypatch, tmp_path):
    from tools.core.atlas_integrity import build_atlas_commit, source_fingerprint
    from tools.core.state_flow import TRANSITIVE_HOOK_COVERAGE

    _wire_state_focus(monkeypatch, tmp_path)
    atlas = {
        "MAIN": {"files": {"store.ts": {"hash": "a" * 64, "size": 10}}},
        "COMPANION": {"files": {"store.ts": {"hash": "b" * 64, "size": 10}}},
    }
    commit = build_atlas_commit(atlas)
    data = {
        "zustand_stores": {"MAIN::store.ts": ["zustand_store"]},
        "transitive_hook_coverage": TRANSITIVE_HOOK_COVERAGE,
        "by_project": {"MAIN": {"zustand_store_files": 1}},
        "run_meta": {
            "atlas_source_binding": "source_inventory_match",
            "atlas_snapshot_id": commit["snapshot_id"],
            "analyzed_source_fingerprint": source_fingerprint({"MAIN": atlas["MAIN"]}),
            "execution_scope": {"analyzed_projects": ["MAIN"]},
        },
    }
    monkeypatch.setattr(server, "_load_json", lambda path: commit if Path(path).name == "atlas_commit.json" else data)
    full = json.loads(server.get_state_flow(full=True))
    assert full["mcp_consumer_binding"]["status"] == "stored_atlas_snapshot_match"
    assert full["mcp_consumer_binding"]["live_source_status"] == "not_checked"
    assert 'status: "ok"' in server.get_state_flow()
    out_of_scope = json.loads(server.get_state_flow(project="COMPANION", full=True))
    assert out_of_scope["mcp_consumer_binding"]["status"] == "unavailable"
    assert out_of_scope["mcp_consumer_binding"]["reason"] == "requested_project_not_analyzed"
    assert 'status: "unavailable"' in server.get_state_flow(project="COMPANION")
    assert json.loads(server.get_state_flow(project="all", full=True))["mcp_consumer_binding"]["status"] == "stored_atlas_snapshot_match"
    old_hook_semantics = dict(data)
    old_hook_semantics.pop("transitive_hook_coverage")
    monkeypatch.setattr(server, "_load_json", lambda path: commit if Path(path).name == "atlas_commit.json" else old_hook_semantics)
    legacy = json.loads(server.get_state_flow(full=True))["mcp_consumer_binding"]
    assert legacy["status"] == "unavailable" and legacy["reason"] == "legacy_hook_consumer_coverage"
    monkeypatch.setattr(server, "_load_json", lambda path: commit if Path(path).name == "atlas_commit.json" else data)

    stale_commit = dict(commit, snapshot_id="0" * 64)
    monkeypatch.setattr(server, "_load_json", lambda path: stale_commit if Path(path).name == "atlas_commit.json" else data)
    stale = json.loads(server.get_state_flow(format="json"))
    assert stale["mcp_consumer_binding"]["status"] == "unavailable"
    assert 'status: "unavailable"' in server.get_state_flow()

    malformed = dict(data, run_meta=dict(data["run_meta"],
                                         execution_scope={"analyzed_projects": [{"MAIN": True}]}))
    monkeypatch.setattr(server, "_load_json", lambda path: commit if Path(path).name == "atlas_commit.json" else malformed)
    assert json.loads(server.get_state_flow(full=True))["mcp_consumer_binding"]["status"] == "unavailable"


def test_state_flow_focus_actual_sqlite_snapshot_and_live_drift(monkeypatch, tmp_path):
    import hashlib
    from tools.core import artifact_store
    from tools.core.artifact_store import ArtifactStore
    from tools.core.db import SQLiteManager

    root = tmp_path / "repo"
    root.mkdir()
    source = root / "store.ts"
    content = "export function useProjects() { return persist(); }\n"
    source.write_text(content, encoding="utf-8", newline="")
    record = _state_focus_atlas()["MAIN"]["files"]["store.ts"]
    record.update(workspace_rel="store.ts", hash=hashlib.sha256(content.encode()).hexdigest(),
                  language="typescript", size=len(content))
    record["symbols"][0].update(line=1, end_line=1, type="Function")
    atlas = {"MAIN": {"root_path": ".", "project_type": "typescript",
                      "files": {"store.ts": record}, "dependencies": {}, "symbols": []}}
    monkeypatch.setattr(artifact_store, "ROOT", root)
    raw_dir = tmp_path / ".raw"
    raw_dir.mkdir()
    store = ArtifactStore(raw_dir=raw_dir)
    store.use_sqlite = True
    store.backend = "hybrid_sqlite"
    store.db_manager = SQLiteManager(raw_dir / "codemaps.db")
    store._schema_initialized = False
    store._ensure_schema()
    store.save_raw("atlas", atlas)
    monkeypatch.setattr(server, "_raw_dir_for_target", lambda target: raw_dir if target == str(root) else None)
    monkeypatch.setattr(server, "_analysis_root_display", lambda _target="": str(root))
    # Real Atlas read, exact SQLite identity, stored-text hash and live-file comparison.
    result = json.loads(server.get_state_flow(file="store.ts", symbol="useProjects",
                                              target_root=str(root), format="json"))
    assert result["source_binding"] == "snapshot_and_live_match"
    assert result["source_grounding"]["target_spans"][0]["symbol"] == "useProjects"
    source.write_text("// changed after analysis\n", encoding="utf-8")
    drifted = json.loads(server.get_state_flow(file="store.ts", target_root=str(root), format="json"))
    assert drifted["source_binding"] == "snapshot_match_live_unverified"
    assert drifted["source_grounding"]["drift_check_status"] == "mismatch"
    with store.db_manager.get_connection() as conn:
        conn.execute("UPDATE source_snapshots SET content = 'corrupt';")
    corrupt = json.loads(server.get_state_flow(file="store.ts", target_root=str(root), format="json"))
    assert corrupt["source_binding"] == "unavailable"
    assert corrupt["source_grounding"]["source_snapshot_status"] == "content_mismatch"


def _state_member_atlas():
    reference = {"source": "./repository", "localName": "save", "importedName": "persist", "kind": "named"}
    return {"MAIN": {"files": {
        "store.ts": {
            "hash": "a" * 64, "features": ["ZustandStore"],
            "import_records": [{"raw_source": "./repository", "source": "repository.ts",
                                "name": "persist", "kind": "named", "scope": "top_level"}],
            "symbols": [{"name": "ProjectStore", "line": 2, "end_line": 8, "type": "Class",
                         "member_details": [
                             {"name": "importProjects", "kind": "method", "dependencies": ["save"],
                              "dependencyImports": [reference]},
                             {"name": "clear", "kind": "method", "dependencies": ["erase"],
                              "dependencyImports": [{"source": "./erase", "localName": "erase"}]},
                         ]}],
        },
        "repository.ts": {"hash": "b" * 64, "symbols": [{"name": "persist"}]},
    }}, "COMPANION": {"files": {"repository.ts": {"hash": "c" * 64, "symbols": []}}}}


def test_state_flow_focus_qualified_member_uses_only_its_own_imports():
    result = _state_focus(_state_member_atlas(), symbol="ProjectStore.importProjects")
    assert result["status"] == "selected"
    assert result["target"]["declaring_symbol"] == "ProjectStore"
    assert result["target"]["start_line"] is None  # Parent line 2 is not the member span.
    assert result["symbol_context"]["identifier_references"]["items"] == ["save"]
    candidates = result["import_candidates"]
    assert candidates["runtime_call"] == "not_established"
    assert candidates["attribution"].endswith("lexical_binding_unverified")
    assert candidates["returned"] == 1
    assert candidates["items"][0]["target"]["atlas_ref"] == "MAIN::repository.ts"
    assert "erase" not in json.dumps(result)
    assert _state_focus(_state_member_atlas(), symbol="importProjects")["status"] == "not_found"


def test_state_flow_focus_member_collisions_and_budget_are_not_guessed():
    atlas = _state_member_atlas()
    rows = atlas["MAIN"]["files"]["store.ts"]["symbols"]
    rows[0]["member_details"] *= 2
    result = _state_focus(atlas, symbol="ProjectStore.importProjects", max_items=1)
    assert result["status"] == "ambiguous" and result["omitted"] == 1
    assert "import_candidates" not in result
    assert _state_focus(atlas, symbol="ProjectStore.importProjects", scan_limit=2)["status"] == "incomplete_search"
    atlas = _state_member_atlas()
    row = atlas["MAIN"]["files"]["store.ts"]["symbols"][0]
    row.pop("member_details")
    row.update(name="projectStore", type="Variable", dependencies=["importProjects"])
    assert _state_focus(atlas, symbol="projectStore.importProjects")["status"] == "not_found"


def test_state_flow_focus_import_candidates_reject_unbound_module_or_target():
    mutations = [
        (lambda files, entries: entries[0].update(kind="type"), "type_only_or_incompatible_import_scope"),
        (lambda files, entries: entries[0].update(scope="local"), "type_only_or_incompatible_import_scope"),
        (lambda files, entries: entries.append(dict(entries[0])), "ambiguous_module_import"),
        (lambda files, entries: entries[0].update(source="COMPANION::repository.ts"), "no_exact_same_project_target"),
        (lambda files, entries: files.pop("repository.ts"), "no_exact_same_project_target"),
        (lambda files, entries: files.update({"collision.ts": {"workspace_rel": "repository.ts", "symbols": []}}), "ambiguous_target_file"),
        (lambda files, entries: entries.clear(), "module_import_not_recorded"),
    ]
    for mutate, reason in mutations:
        atlas = _state_member_atlas()
        files = atlas["MAIN"]["files"]
        mutate(files, files["store.ts"]["import_records"])
        result = _state_focus(atlas, symbol="ProjectStore.importProjects")
        candidate = result["import_candidates"]["items"][0]
        assert candidate["status"] == "unresolved" and candidate["reason"] == reason
        assert "target" not in candidate


def test_state_flow_focus_import_budget_is_shared_and_omissions_are_explicit():
    atlas = _state_member_atlas()
    record = atlas["MAIN"]["files"]["store.ts"]
    # Five selection records (two files, one class, two members) plus module/ref work.
    limited = _state_focus(atlas, file="store.ts", symbol="ProjectStore.importProjects", scan_limit=5)
    assert limited["status"] == "selected"
    assert limited["import_candidates"]["status"] == "incomplete_scan"
    assert limited["import_candidates"]["omitted"] == 1
    assert limited["visited_records"] == 5
    record["symbols"][0]["member_details"][0]["dependencyImports"] *= 4
    result = _state_focus(atlas, file="store.ts", symbol="ProjectStore.importProjects", max_items=2)
    assert result["import_candidates"]["returned"] == 2
    assert result["import_candidates"]["omitted"] == 2
    record["import_records"] = [None]
    assert _state_focus(atlas, symbol="ProjectStore.importProjects")["import_candidates"]["status"] == "unavailable"


def test_state_flow_focus_member_endpoint_binding_is_independent_and_cached(monkeypatch, tmp_path):
    _wire_state_focus(monkeypatch, tmp_path)
    atlas = _state_member_atlas()
    atlas["MAIN"]["files"]["store.ts"]["symbols"][0]["member_details"][0]["dependencyImports"] *= 2
    monkeypatch.setattr(server, "_atlas", lambda **_kw: atlas)
    calls = []
    target_state = {"drift_check_status": "match"}
    def status(_raw, ref, **kw):
        calls.append((ref, kw.get("preferred_symbols")))
        rel = ref.split("::")[1]
        base = {"resolved_node": ref, "target_file": rel, "inside_root": True,
                "source_snapshot_status": "ok", "source_snapshot_hash": atlas["MAIN"]["files"][rel]["hash"],
                "drift_check_status": "match"}
        if rel == "repository.ts":
            base.update(target_state)
        return base
    monkeypatch.setattr(server, "_target_path_status", status)
    for change, binding in (
        ({"drift_check_status": "match"}, "snapshot_and_live_match"),
        ({"drift_check_status": "mismatch"}, "snapshot_match_live_unverified"),
        ({"source_snapshot_hash": "c" * 64}, "unavailable"),
        ({"resolved_node": "COMPANION::repository.ts"}, "unavailable"),
    ):
        calls.clear()
        target_state.clear()
        target_state.update(change)
        result = json.loads(server.get_state_flow(file="store.ts", symbol="ProjectStore.importProjects", format="json"))
        assert result["source_binding"] == "snapshot_and_live_match"
        assert result["source_grounding_scope"] == "declaring_symbol_not_member_span"
        assert calls == [("MAIN::store.ts", {"ProjectStore"}), ("MAIN::repository.ts", None)]
        candidate = result["import_candidates"]["items"][0]
        assert candidate["source_binding"] == binding
        assert (candidate["endpoint_content_binding"] == "both_snapshot_and_live_match") == (binding == "snapshot_and_live_match")
        assert result["unknowns"]["upstream_downstream_trace"] == "unavailable"


def test_state_flow_focus_real_parser_member_alias_and_shadow_stay_syntax(monkeypatch, tmp_path):
    import copy
    import hashlib
    import shutil
    import pytest
    from tools.engines.generate_atlas import _normalize_polyglot_symbols
    from tools.core.polyglot_imports import extract_typescript_import_evidence
    from tools.core.path_engine import resolve_project_import
    from tools.core.artifact_validator import _validate
    from tools.core.state_flow_import_index import build_resolved_module_importer_index
    from tools.core import artifact_store
    from tools.core.artifact_store import ArtifactStore
    from tools.core.db import SQLiteManager

    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the real producer control")
    source = '''import { create as makeStore } from "zustand";
import { createStore as makeVanilla } from "zustand/vanilla";
import { persist as persistMw } from "zustand/middleware";
import { devtools as devtoolsMw } from "zustand/middleware";
import { persist as fakePersist } from "./fake-middleware";
import { create as fakeCreate } from "./fake-store";
import type { create as typeStoreFactory } from "zustand";
import { persist as save } from "./repository";
import { erase } from "./erase";
import * as repo from "./repository";
import p from "./repository";
import type { TypeOnly } from "./repository";
import { value as valueCall, overloaded as overloadCall, missing as missingCall } from "./repository";
import { forwarded as forwardedCall } from "./barrel";
export class ProjectStore {
    importProjects() { return save(); }
    clear() { return erase(); }
    shadow(save: () => void) { return save(); }
    arrow = () => save();
    get value() { return save(); }
}
export const actions = {
    importProjects() { return save(); },
    clear: () => erase(),
} satisfies Record<string, unknown>;
export const useProjects = create<State>()(persist((set) => ({
    importProjects: () => save(),
    clear() { erase(); },
    update: () => set(() => ({ phantom: 1 })),
}), { name: 'cache', configOnly() { erase(); } }));
export const branches = whatever(() => {
    function helper() { return { nestedOnly() { erase(); } }; }
    const nested = {
        method() { return { methodOnly() { erase(); } }; },
        get value() { return { getterOnly() { erase(); } }; },
    };
    if (flag) return { choose() { save(); } };
    return { choose() { erase(); } };
});
export const mixed = { ...defaults, [dynamicName]: () => erase(), 'save': () => save() };
export const opaque = create(externalFactory);
export const nonliteral = create(() => flag ? { conditionalOnly() {} } : {});
export function referenceOnly() { return save; }
export function directCalls() { save(); (save as () => void)?.(); repo.persist(); p(); }
export function unresolvedCalls() { valueCall(); overloadCall('x'); missingCall(); forwardedCall(); }
export function nestedCalls() { const callback = () => save(); function helper() { save(); } }
export function localShadow() { save(); const save = () => {}; }
export function destructuredShadow({ save }: any) { save(); }
export function blockAndCatch() { { const save = () => {}; save(); } try {} catch (save) { save(); } save(); }
export function unsupportedCalls() { const alias = save; alias(); repo['persist'](); TypeOnly(); }
export const scoped = create((save) => ({ action: () => save() }));
export const simpleStore = makeStore((set) => ({
    update: () => { set({ ready: true, 'label': 'ok', nested: { child: 1 } }); set?.({ ready: false }); },
    updater: () => set((previous) => ({ count: previous.count + 1 })),
    spread: () => set({ known: true, ...patch }),
    computed: () => set({ known: true, [key]: 1 }),
    shorthand: () => set({ ready }),
    special: () => set({ '__proto__': value }),
    nonobject: () => set(nextState),
    empty: () => set({}),
    actionShadow: (set) => set({ notFactory: true }),
    nested: () => { const later = () => set({ later: true }); return later; },
    blockShadow: () => { { const set = () => {}; set(); } set({ outer: true }); },
    read: () => 1,
}));
export function invokeStoreAction() {
    simpleStore.getState().update();
    fakeStore.getState().update();
    const alias = simpleStore; alias.getState().update();
    simpleStore.getState()['update']();
    simpleStore.getState()?.update();
    const deferred = () => simpleStore.getState().update();
    return deferred;
}
export function invokeDestructuredAction() {
    const { update } = simpleStore.getState();
    update();
    const { update: alias } = fakeStore.getState(); alias();
    const nested = () => { const { update: later } = simpleStore.getState(); later(); };
    return nested;
}
export function shadowStoreAction(simpleStore: any) { simpleStore.getState().update(); }
export function selectHookAction() {
    const selected = simpleStore((state) => state.update);
    selected();
    const alias = selected; alias();
    selected?.();
    const deferredCall = () => selected();
    let mutable = selected; mutable();
    simpleStore((state) => state.read);
    const alias = simpleStore; alias((state) => state.update);
    simpleStore?.((state) => state.update);
    simpleStore((state) => state['update']);
    const deferred = () => simpleStore((state) => state.update);
    return selected;
}
export function shadowHookAction(simpleStore: any) { return simpleStore((state: any) => state.update); }
export const fakeStore = fakeCreate((set) => ({ update: () => set({ fake: true }) }));
export const vanillaStore = makeVanilla((set) => ({ sync() { set({ synced: true }); } }));
export const curriedStore = makeStore<State>()((set) => ({ update: () => set({ curried: true }) }));
export const persistedStore = makeStore(persistMw((set) => ({ update: () => set({ persisted: true }) }), { name: 'cache' }));
export const curriedPersistedStore = makeStore<State>()(persistMw((set) => ({ update: () => set({ both: true }) }), { name: 'cache' }));
export const fakeMiddlewareStore = makeStore(fakePersist((set) => ({ update: () => set({ fakeMiddleware: true }) })));
export const fakeCurriedStore = fakeCreate<State>()((set) => ({ update: () => set({ fakeFactory: true }) }));
export const typeOnlyStore = typeStoreFactory((set) => ({ update: () => set({ typeOnly: true }) }));
export const nonemptyCurriedStore = makeStore({ unexpected: true })((set) => ({ update: () => set({ nonempty: true }) }));
export const chainedMiddlewareStore = makeStore(devtoolsMw(persistMw((set) => ({ update: () => set({ chained: true }) }), { name: 'cache' })));
export const named = function save() { save(); };
export const first = () => save(), second = () => erase();
throw new Error('analysis must not execute this file');
'''
    source = "// Unicode coordinate control: 🧭\r\n" + source.replace("\n", "\r\n")
    path = tmp_path / "store.ts"
    path.write_text(source, encoding="utf-8", newline="")
    repository = tmp_path / "repository.ts"
    repository_source = '''import { write as adapterWrite } from "./adapter";
export function persist() { adapterWrite(); adapterWrite(); return true; }
export default function fallback() { return true; }
export const value = 3;
export function overloaded(value: string): void;
export function overloaded(value: any) { return value; }
'''
    repository.write_text(repository_source, encoding="utf-8", newline="")
    adapter = tmp_path / "adapter.ts"
    adapter_source = 'export function write() { return true; }\n'
    adapter.write_text(adapter_source, encoding="utf-8", newline="")
    barrel = tmp_path / "barrel.ts"
    barrel_source = 'export { persist as forwarded } from "./repository";\n'
    barrel.write_text(barrel_source, encoding="utf-8", newline="")
    caller_path = tmp_path / "caller.ts"
    caller_source = '''import { simpleStore as chosen } from "./store";
import { simpleStore as wrong } from "./other-store";
import type { simpleStore as TypeOnly } from "./store";
export function callSelected() {
    chosen.getState().update();
    wrong.getState().update();
    const alias = chosen; alias.getState().update();
    chosen.getState()['update']();
    chosen.getState()?.update();
    const deferred = () => chosen.getState().update();
    return deferred;
}
export function callDestructured() {
    const { update } = chosen.getState();
    update();
    const { update: wrongAction } = wrong.getState(); wrongAction();
}
export function shadowSelected(chosen: any) { chosen.getState().update(); }
export function blockShadowSelected() { { const chosen = wrong; chosen.getState().update(); } }
export function typeOnlyCall() { TypeOnly.getState().update(); }
export function selectImportedHook() { const selected = chosen((state) => state.update); selected(); return selected; }
export function selectWrongHook() { return wrong((state) => state.update); }
export function shadowImportedHook(chosen: any) { return chosen((state: any) => state.update); }
export function typeOnlyHook() { return TypeOnly((state: any) => state.update); }
'''
    caller_path.write_text(caller_source, encoding="utf-8", newline="")
    other_path = tmp_path / "other-store.ts"
    other_source = 'export const simpleStore = { getState: () => ({ update() {} }) };\n'
    other_path.write_text(other_source, encoding="utf-8", newline="")
    root = Path(__file__).resolve().parents[2]
    proc = subprocess.run([node, str(root / "tools/engines/ast_sequencer.cjs"), str(path)],
                          cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert proc.returncode == 0, proc.stderr
    raw = json.loads(proc.stdout)
    symbols = _normalize_polyglot_symbols(raw, source, language="typescript")
    evidence = extract_typescript_import_evidence(raw)
    atlas = _state_member_atlas()
    record = atlas["MAIN"]["files"]["store.ts"]
    record["symbols"] = symbols
    record["features"] = next(entry.get("features", []) for entry in raw if entry.get("name") == "__file_meta__")
    record["import_records"] = [dict(entry, raw_source=entry["source"], source=resolve_project_import(
        entry["source"], str(tmp_path), str(tmp_path), str(tmp_path))) for entry in evidence["records"]]
    for rel, content in (("repository.ts", repository_source), ("adapter.ts", adapter_source),
                         ("barrel.ts", barrel_source),
                         ("caller.ts", caller_source), ("other-store.ts", other_source)):
        parsed = subprocess.run([node, str(root / "tools/engines/ast_sequencer.cjs"), str(tmp_path / rel)],
                                cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=30)
        assert parsed.returncode == 0, parsed.stderr
        parsed_raw = json.loads(parsed.stdout)
        parsed_imports = extract_typescript_import_evidence(parsed_raw)
        atlas["MAIN"]["files"][rel] = {
            "hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "symbols": _normalize_polyglot_symbols(parsed_raw, content, language="typescript"),
            "import_records": [dict(entry, raw_source=entry["source"], source=resolve_project_import(
                entry["source"], str(tmp_path), str(tmp_path), str(tmp_path))) for entry in parsed_imports["records"]],
        }
    selected = _state_focus(atlas, symbol="ProjectStore.importProjects")
    assert selected["status"] == "selected"
    assert selected["import_candidates"]["items"][0]["target"]["atlas_ref"] == "MAIN::repository.ts"
    assert "erase" not in json.dumps(selected)
    shadow = _state_focus(atlas, symbol="ProjectStore.shadow")
    # Existing producer is lexical-shadow blind. Candidate != call or proven binding.
    assert shadow["import_candidates"]["runtime_call"] == "not_established"
    assert shadow["import_candidates"]["attribution"].endswith("lexical_binding_unverified")
    assert shadow["import_candidates"]["items"][0]["status"] == "target_candidate"
    # Separate parser-owned call evidence must not inherit the legacy name-only match.
    assert shadow["symbol_context"]["import_calls"]["items"] == []
    assert selected["symbol_context"]["import_calls"]["items"][0]["localName"] == "save"
    callee = selected["direct_callees"]["items"][0]
    assert callee["status"] == "target_candidate" and callee["target"]["symbol"] == "persist"
    assert callee["target"]["atlas_ref"] == "MAIN::repository.ts"
    for name in ("ProjectStore.arrow", "ProjectStore.value"):
        assert _state_focus(atlas, symbol=name)["symbol_context"]["import_calls"]["items"][0]["localName"] == "save"
    for name in ("referenceOnly", "nestedCalls", "localShadow", "destructuredShadow", "scoped.action", "named"):
        calls = _state_focus(atlas, symbol=name)["symbol_context"]["import_calls"]
        assert calls["status"] == "observed", (name, calls)
        assert calls["items"] == [], name
    direct = _state_focus(atlas, symbol="directCalls", max_items=8)["symbol_context"]["import_calls"]
    assert [entry["localName"] for entry in direct["items"]] == ["save", "save", "repo", "p"]
    assert direct["items"][1]["optional"] is True
    assert direct["items"][2]["member"] == "persist"
    assert direct["binding_scope"] == "single_file_lexical_import"
    assert direct["runtime_execution"] == "not_established"
    linked = _state_focus(atlas, symbol="directCalls", max_items=8, scan_limit=1000)["direct_callees"]
    assert all(item["status"] == "target_candidate" for item in linked["items"]), linked
    assert [item["target"]["symbol"] for item in linked["items"]] == ["persist", "persist", "persist", "fallback"]
    assert [item["target_context_index"] for item in linked["items"]] == [0, 0, 0, 1]
    assert [context["target"]["symbol"] for context in linked["target_symbol_contexts"]] == ["persist", "fallback"]
    persist_context = linked["target_symbol_contexts"][0]
    assert persist_context["traversal"] == "one_target_context_plus_one_bounded_outbound_candidate_hop"
    assert persist_context["attribution"] == "direct_target_symbol_syntax_not_execution"
    assert persist_context["import_calls"]["items"][0]["localName"] == "adapterWrite"
    assert persist_context["import_calls"]["returned"] == 2
    outbound = persist_context["outbound_calls"]
    assert outbound["status"] == "observed" and outbound["returned"] == 2
    assert {item["target"]["atlas_ref"] for item in outbound["items"]} == {"MAIN::adapter.ts"}
    assert {item["target"]["symbol"] for item in outbound["items"]} == {"write"}
    assert all(item["runtime_execution"] == "not_established" for item in outbound["items"])
    assert "target_symbol_contexts" not in outbound  # Exactly two bounded syntax hops.
    missing_adapter = copy.deepcopy(atlas)
    missing_adapter["MAIN"]["files"].pop("adapter.ts")
    unavailable_adapter = _state_focus(
        missing_adapter, symbol="ProjectStore.importProjects", scan_limit=1000)[
            "direct_callees"]["target_symbol_contexts"][0]["outbound_calls"]
    assert all(item["reason"] == "no_exact_same_project_target"
               for item in unavailable_adapter["items"])
    assert all("target" not in item for item in unavailable_adapter["items"])
    wrong_adapter = copy.deepcopy(atlas)
    adapter_import = next(entry for entry in wrong_adapter["MAIN"]["files"]["repository.ts"]["import_records"]
                          if entry.get("raw_source") == "./adapter")
    adapter_import["source"] = "other-store.ts"
    wrong_outbound = _state_focus(wrong_adapter, symbol="ProjectStore.importProjects", scan_limit=1000)[
        "direct_callees"]["target_symbol_contexts"][0]["outbound_calls"]
    assert all(item["reason"] == "target_export_not_indexed" for item in wrong_outbound["items"])
    assert all("target" not in item for item in wrong_outbound["items"])
    old_target = copy.deepcopy(atlas)
    next(row for row in old_target["MAIN"]["files"]["repository.ts"]["symbols"]
         if row["name"] == "persist").pop("import_call_evidence")
    old_outbound = _state_focus(old_target, symbol="ProjectStore.importProjects", scan_limit=1000)[
        "direct_callees"]["target_symbol_contexts"][0]["outbound_calls"]
    assert old_outbound["status"] == "unavailable" and old_outbound["items"] == []
    unresolved = _state_focus(atlas, symbol="unresolvedCalls", max_items=8, scan_limit=1000)["direct_callees"]
    assert [item["reason"] for item in unresolved["items"]] == [
        "export_not_direct_callable", "ambiguous_target_export", "target_export_not_indexed", "reexport_or_alias_unresolved"]
    assert all("target" not in item for item in unresolved["items"])
    assert unresolved["target_symbol_contexts"] == []
    for call in direct["items"]:
        assert call["source"] == "./repository"
        assert call["line"] == call["end_line"]
        assert "directCalls" in source.splitlines()[call["line"] - 1]
    assert len(_state_focus(atlas, symbol="blockAndCatch")["symbol_context"]["import_calls"]["items"]) == 1
    unsupported = _state_focus(atlas, symbol="unsupportedCalls")["symbol_context"]["import_calls"]
    assert unsupported["items"] == []
    assert "type_only_import_not_runtime_callee" in unsupported["limitations"]
    assert "nested_callable_excluded" in _state_focus(atlas, symbol="nestedCalls")["symbol_context"]["import_calls"]["limitations"]
    upstream = _state_focus(atlas, symbol="simpleStore.update", max_items=8, scan_limit=500)["upstream_action_calls"]
    assert upstream["status"] == "observed", upstream
    assert upstream["scope"] == "selected_file_direct_or_const_destructured_getstate_only"
    assert [item["caller_symbol"] for item in upstream["items"]] == [
        "invokeStoreAction", "invokeDestructuredAction"]
    assert [item["call_form"] for item in upstream["items"]] == [
        "direct_getstate_action", "const_destructured_getstate_action"]
    same_destructured = next(row for row in atlas["MAIN"]["files"]["store.ts"]["symbols"]
                             if row["name"] == "invokeDestructuredAction")
    assert [(call["store"], call["action"], call["call_form"])
            for call in same_destructured["same_file_store_action_call_evidence"]["calls"]] == [
        ("simpleStore", "update", "const_destructured_getstate_action")]
    assert all(item["status"] == "target_candidate" for item in upstream["items"])
    assert upstream["runtime_execution"] == "not_established"
    cross_file = _state_focus(atlas, symbol="simpleStore.update", max_items=8, scan_limit=1000)["cross_file_action_calls"]
    assert cross_file["status"] == "observed", cross_file
    assert cross_file["returned"] == 2 and cross_file["omitted"] == 0
    assert cross_file["items"][0]["caller"]["atlas_ref"] == "MAIN::caller.ts"
    assert cross_file["items"][0]["caller_symbol"] == "callSelected"
    assert (cross_file["items"][0]["local_store"], cross_file["items"][0]["imported_store"]) == ("chosen", "simpleStore")
    assert [(item["caller_symbol"], item["call_form"]) for item in cross_file["items"]] == [
        ("callSelected", "direct_getstate_action"),
        ("callDestructured", "const_destructured_getstate_action")]
    imported_destructured = next(row for row in atlas["MAIN"]["files"]["caller.ts"]["symbols"]
                                if row["name"] == "callDestructured")
    assert [(call["store"], call["action"], call["call_form"])
            for call in imported_destructured["same_file_store_action_call_evidence"]["calls"]] == [
        ("chosen", "update", "const_destructured_getstate_action")]
    assert cross_file["runtime_execution"] == "not_established"
    assert cross_file["importer_lookup"] == "bounded_file_import_scan"
    forged_form = copy.deepcopy(atlas)
    forged_caller = next(row for row in forged_form["MAIN"]["files"]["caller.ts"]["symbols"]
                         if row["name"] == "callDestructured")
    forged_caller["same_file_store_action_call_evidence"]["calls"][0]["call_form"] = "invented"
    rejected = _state_focus(forged_form, symbol="simpleStore.update", scan_limit=1000)["cross_file_action_calls"]
    assert rejected["status"] == "unavailable" and rejected["items"] == []
    hook = _state_focus(atlas, symbol="simpleStore.update", max_items=8, scan_limit=1000)["hook_selector_candidates"]
    assert hook["status"] == "observed" and hook["returned"] == 2, hook
    assert {item["caller_symbol"] for item in hook["items"]} == {"selectHookAction", "selectImportedHook"}
    assert all(item["subscription"] == "not_established" for item in hook["items"])
    assert all(len(item["selected_result_direct_calls"]) == 1 for item in hook["items"])
    assert all(item["selected_result_direct_calls"][0]["line"] >= item["call_line"]
               for item in hook["items"])
    assert hook["runtime_execution"] == "not_established"
    assert _state_focus(atlas, symbol="simpleStore.update")["symbol_context"]["setter_calls"]["factory_api"] == "react_bound_hook"
    assert _state_focus(atlas, symbol="vanillaStore.sync")["symbol_context"]["setter_calls"]["factory_api"] == "vanilla_store"
    assert _state_focus(atlas, symbol="vanillaStore.sync", scan_limit=1000)["hook_selector_candidates"]["status"] == "not_applicable"
    assert next(item for item in hook["items"] if item["caller_symbol"] == "selectImportedHook")["caller"]["atlas_ref"] == "MAIN::caller.ts"
    for rel, names in (("store.ts", ("shadowHookAction",)),
                       ("caller.ts", ("shadowImportedHook", "typeOnlyHook"))):
        for name in names:
            evidence = next(row for row in atlas["MAIN"]["files"][rel]["symbols"]
                            if row["name"] == name)["store_hook_selector_evidence"]
            assert evidence["status"] == "observed" and evidence["calls"] == [], name
    wrong_hook = next(row for row in atlas["MAIN"]["files"]["caller.ts"]["symbols"]
                      if row["name"] == "selectWrongHook")["store_hook_selector_evidence"]
    assert wrong_hook["calls"][0]["module_source"] == "./other-store"
    assert "selected_result_direct_calls" not in wrong_hook["calls"][0]
    forged_invocation = copy.deepcopy(atlas)
    forged_selector = next(row for row in forged_invocation["MAIN"]["files"]["caller.ts"]["symbols"]
                           if row["name"] == "selectImportedHook")
    forged_selector["store_hook_selector_evidence"]["calls"][0]["selected_result_direct_calls"][0]["line"] = 0
    rejected_invocation = _state_focus(forged_invocation, symbol="simpleStore.update", scan_limit=1000)["hook_selector_candidates"]
    assert rejected_invocation["status"] == "unavailable" and rejected_invocation["items"] == []
    atlas["MAIN"]["resolved_module_importer_index"] = build_resolved_module_importer_index(atlas["MAIN"]["files"])
    indexed_cross = _state_focus(atlas, symbol="simpleStore.update", max_items=8, scan_limit=1000)["cross_file_action_calls"]
    assert indexed_cross["items"] == cross_file["items"]
    assert indexed_cross["importer_lookup"] == "same_snapshot_resolved_module_importer_index"
    indexed_hook = _state_focus(atlas, symbol="simpleStore.update", max_items=8, scan_limit=1000)["hook_selector_candidates"]
    assert indexed_hook["items"] == hook["items"]
    assert indexed_hook["importer_lookup"] == "same_snapshot_resolved_module_importer_index"
    old_hook_atlas = copy.deepcopy(atlas)
    next(row for row in old_hook_atlas["MAIN"]["files"]["caller.ts"]["symbols"]
         if row["name"] == "selectImportedHook").pop("store_hook_selector_evidence")
    old_hook = _state_focus(old_hook_atlas, symbol="simpleStore.update", scan_limit=1000)["hook_selector_candidates"]
    assert old_hook["status"] == "unavailable" and old_hook["items"] == []
    malformed_factory = copy.deepcopy(atlas)
    malformed_action = next(member for member in next(row for row in malformed_factory["MAIN"]["files"]["store.ts"]["symbols"]
        if row["name"] == "simpleStore")["initializer_member_evidence"]["members"] if member["name"] == "update")
    malformed_action["zustand_setter_call_evidence"]["factory_api"] = "unknown_api"
    assert _state_focus(malformed_factory, symbol="simpleStore.update", scan_limit=1000)["hook_selector_candidates"]["status"] == "unavailable"
    sparse_large_atlas = copy.deepcopy(atlas)
    sparse_files = sparse_large_atlas["MAIN"]["files"]
    for index in range(40000):
        rel = f"unrelated/{index:05d}.ts"
        sparse_files[rel] = {"workspace_rel": rel, "hash": "a" * 64,
                             "import_records": [], "symbols": []}
    sparse_focus = _state_focus(sparse_large_atlas, symbol="simpleStore.update", scan_limit=50000)
    assert sparse_focus["status"] == "selected" and sparse_focus["search_complete"]
    assert sparse_focus["cross_file_action_calls"]["status"] == "observed"
    assert sparse_focus["cross_file_action_calls"]["returned"] == 2
    assert sparse_focus["visited_records"] < 50000
    dense_atlas = copy.deepcopy(atlas)
    dense_files = {}
    unrelated_import = {"source": "unrelated-module", "raw_source": "unrelated-module",
                        "name": "value", "kind": "named", "scope": "top_level"}
    for index in range(40000):
        rel = f"dense/{index:05d}.ts"
        dense_files[rel] = {"workspace_rel": rel, "hash": "a" * 64,
                            "import_records": [unrelated_import] * 5, "symbols": []}
    dense_files.update(dense_atlas["MAIN"]["files"])
    dense_atlas["MAIN"]["files"] = dense_files
    dense_atlas["MAIN"].pop("resolved_module_importer_index")
    dense_fallback = _state_focus(dense_atlas, symbol="simpleStore.update", scan_limit=50000)
    assert dense_fallback["cross_file_action_calls"]["status"] == "incomplete_scan"
    assert dense_fallback["cross_file_action_calls"]["importer_lookup"] == "bounded_file_import_scan"
    dense_atlas["MAIN"]["resolved_module_importer_index"] = build_resolved_module_importer_index(dense_files)
    dense_indexed = _state_focus(dense_atlas, symbol="simpleStore.update", scan_limit=50000)
    assert dense_indexed["cross_file_action_calls"]["status"] == "observed"
    assert dense_indexed["cross_file_action_calls"]["returned"] == 2
    assert dense_indexed["cross_file_action_calls"]["importer_lookup"] == "same_snapshot_resolved_module_importer_index"
    assert dense_indexed["hook_selector_candidates"]["status"] == "observed"
    assert dense_indexed["hook_selector_candidates"]["returned"] == 2
    assert dense_indexed["visited_records"] < 50000
    dense_atlas["MAIN"]["resolved_module_importer_index"]["indexed_file_count"] -= 1
    assert _state_focus(dense_atlas, symbol="simpleStore.update", scan_limit=50000)["cross_file_action_calls"]["status"] == "incomplete_scan"
    dense_atlas["MAIN"]["resolved_module_importer_index"]["indexed_file_count"] += 1
    dense_files["caller.ts"]["hash"] = "c" * 64
    same_count_changed_source = _state_focus(dense_atlas, symbol="simpleStore.update", scan_limit=50000)
    assert same_count_changed_source["cross_file_action_calls"]["status"] == "incomplete_scan"
    assert same_count_changed_source["cross_file_action_calls"]["importer_lookup"] == "bounded_file_import_scan"
    assert _state_focus(atlas, symbol="fakeStore.update", scan_limit=500)["upstream_action_calls"]["status"] == "not_applicable"
    assert _state_focus(atlas, symbol="simpleStore.read", scan_limit=500)["upstream_action_calls"]["items"] == []
    limited = _state_focus(atlas, symbol="directCalls", max_items=1)["symbol_context"]["import_calls"]
    assert limited["returned"] == 1 and limited["omitted"] == 3
    capped_callees = _state_focus(atlas, symbol="directCalls", max_items=1)["direct_callees"]
    assert capped_callees["returned"] == 1 and capped_callees["omitted"] == 3
    assert len(capped_callees["target_symbol_contexts"]) == 1
    assert capped_callees["target_symbol_contexts"][0]["import_calls"]["returned"] == 1
    assert capped_callees["target_symbol_contexts"][0]["import_calls"]["omitted"] == 1
    assert [call["localName"] for call in _state_focus(atlas, symbol="first")["symbol_context"]["import_calls"]["items"]] == ["save"]
    assert [call["localName"] for call in _state_focus(atlas, symbol="second")["symbol_context"]["import_calls"]["items"]] == ["erase"]
    from jsonschema import validate
    schema = json.loads((root / "config/schemas/atlas.schema.json").read_text(encoding="utf-8"))
    importer_index_schema = schema["additionalProperties"]["properties"]["resolved_module_importer_index"]
    validate(atlas["MAIN"]["resolved_module_importer_index"], importer_index_schema)
    from tools.core.artifact_validator import _validate
    bad_call_form = copy.deepcopy(imported_destructured["same_file_store_action_call_evidence"])
    bad_call_form["calls"][0]["call_form"] = "invented"
    form_errors = []
    _validate(bad_call_form, schema["$defs"]["same_file_store_action_call_evidence"],
              schema, ["store_action_calls"], form_errors)
    assert form_errors, "Production Atlas schema must reject an invented action call form"
    malformed_index = dict(atlas["MAIN"]["resolved_module_importer_index"], indexed_file_count="many")
    index_errors = []
    _validate(malformed_index, importer_index_schema, schema, ["resolved_module_importer_index"], index_errors)
    assert index_errors, "Production schema must reject a malformed importer index."
    missing_identity = dict(atlas["MAIN"]["resolved_module_importer_index"])
    missing_identity.pop("file_identity_sha256")
    index_errors = []
    _validate(missing_identity, importer_index_schema, schema, ["resolved_module_importer_index"], index_errors)
    assert index_errors, "Production schema must require the importer source-identity digest."
    evidence_schema = schema["additionalProperties"]["properties"]["files"]["additionalProperties"]["properties"]["symbols"]["items"]["properties"]["initializer_member_evidence"]
    for symbol in symbols:
        validate(symbol["initializer_member_evidence"], dict(evidence_schema, **{"$defs": schema["$defs"]}))
        validate(symbol["import_call_evidence"], schema["$defs"]["import_call_evidence"])
        if symbol["type"] in {"Function", "Arrow", "Hook", "Component"}:
            validate(symbol["same_file_store_action_call_evidence"], schema["$defs"]["same_file_store_action_call_evidence"])
            validate(symbol["store_hook_selector_evidence"], schema["$defs"]["store_hook_selector_evidence"])
        else:
            assert "same_file_store_action_call_evidence" not in symbol
            assert "store_hook_selector_evidence" not in symbol
        from tools.core.artifact_validator import _validate
        errors = []
        _validate(symbol["initializer_member_evidence"], evidence_schema, schema, ["initializer"], errors)
        _validate(symbol["import_call_evidence"], schema["$defs"]["import_call_evidence"], schema, ["calls"], errors)
        if "same_file_store_action_call_evidence" in symbol:
            _validate(symbol["same_file_store_action_call_evidence"], schema["$defs"]["same_file_store_action_call_evidence"], schema, ["store_action_calls"], errors)
            _validate(symbol["store_hook_selector_evidence"], schema["$defs"]["store_hook_selector_evidence"], schema, ["hook_selectors"], errors)
        assert not errors
    errors = []
    _validate({"status": "made_up"}, evidence_schema, schema, ["initializer"], errors)
    assert errors, "Production validator must enforce nullable object properties, not only its outer type"
    errors = []
    _validate({"status": "made_up"}, schema["$defs"]["import_call_evidence"], schema, ["calls"], errors)
    assert errors
    errors = []
    _validate({"status": "observed", "calls": [{"store": "simpleStore", "action": "update", "line": 0,
                                                 "end_line": 1}], "limitations": [],
               "binding_scope": "single_file_lexical_exported_store", "runtime_execution": "not_established"},
              schema["$defs"]["same_file_store_action_call_evidence"], schema, ["upstream"], errors)
    assert errors, "Production schema must reject an invalid store action call span"
    errors = []
    _validate({"status": "observed", "calls": [{"store": "chosen", "action": "update", "line": 1,
                                                 "end_line": 1, "binding_kind": "named_import"}],
               "limitations": [], "binding_scope": "single_file_lexical_store_or_named_import",
               "runtime_execution": "not_established"},
              schema["$defs"]["same_file_store_action_call_evidence"], schema, ["cross_file"], errors)
    assert errors, "Named-import call evidence must require an exact module and export identity"
    errors = []
    _validate({"status": "observed", "calls": [{"store": "chosen", "selected_member": "update",
               "selector_form": "direct_arrow_property", "binding_kind": "named_import", "line": 1, "end_line": 1}],
               "limitations": [], "binding_scope": "single_file_lexical_store_or_named_import",
               "runtime_execution": "not_established", "subscription": "not_established"},
              schema["$defs"]["store_hook_selector_evidence"], schema, ["hook_selectors"], errors)
    assert errors, "Named-import hook selector evidence needs a module and exported store."
    for name, attribution in (("actions", "direct_object_initializer"), ("useProjects", "returned_object_candidate")):
        focused = _state_focus(atlas, symbol=name + ".importProjects")
        assert focused["status"] == "selected"
        assert focused["target"]["selection_kind"] == "initializer_member_candidate"
        assert focused["target"]["owner_attribution"] == attribution
        assert focused["target"]["runtime_owner_binding"] == "not_established"
        line, end = focused["target"]["start_line"], focused["target"]["end_line"]
        assert "importProjects" in "\n".join(source.splitlines()[line - 1:end])
        assert focused["target"]["span_scope"] == "initializer_member_syntax"
        assert focused["import_candidates"]["items"][0]["target"]["atlas_ref"] == "MAIN::repository.ts"
        assert focused["symbol_context"]["import_calls"]["items"][0]["localName"] == "save"
        assert "erase" not in json.dumps(focused)
        # These candidates cannot leak into class/member metrics or Genome ownership.
        assert next(row for row in symbols if row["name"] == name)["member_details"] == []
    update = _state_focus(atlas, symbol="simpleStore.update", max_items=1)
    assert update["status"] == "selected"
    setter_calls = update["symbol_context"]["setter_calls"]
    assert setter_calls["status"] == "observed"
    assert setter_calls["relation"] == "lexically_bound_zustand_setter_call_syntax_not_state_transition"
    assert setter_calls["returned"] == 1 and setter_calls["omitted"] == 1
    assert setter_calls["items"][0]["parameter"] == "set"
    assert setter_calls["runtime_execution"] == "not_established"
    keys = setter_calls["items"][0]["literal_state_keys"]
    assert keys["status"] == "incomplete_scan" and keys["returned"] == 1 and keys["omitted"] == 2
    assert [item["name"] for item in keys["items"]] == ["ready"]
    all_keys = _state_focus(atlas, symbol="simpleStore.update", scan_limit=1000)["symbol_context"]["setter_calls"]["items"][0]["literal_state_keys"]
    assert all_keys["status"] == "observed" and all_keys["omitted"] == 0
    assert [item["name"] for item in all_keys["items"]] == ["ready", "label", "nested"]
    assert "child" not in json.dumps(all_keys)
    for member, reason in (("updater", "updater_callback_not_resolved"),
                           ("spread", "spread_or_computed_key_not_resolved"),
                           ("computed", "spread_or_computed_key_not_resolved"),
                           ("shorthand", "unsupported_object_member_not_resolved"),
                           ("special", "special_object_key_not_resolved"),
                           ("nonobject", "argument_not_object_literal")):
        item = _state_focus(atlas, symbol="simpleStore." + member)["symbol_context"]["setter_calls"]["items"][0]
        assert item["literal_state_keys"]["status"] == "unavailable"
        assert item["literal_state_keys"]["reason"] == reason
        assert item["literal_state_keys"]["items"] == []
    empty_keys = _state_focus(atlas, symbol="simpleStore.empty")["symbol_context"]["setter_calls"]["items"][0]["literal_state_keys"]
    assert empty_keys["status"] == "observed" and empty_keys["items"] == []
    block = _state_focus(atlas, symbol="simpleStore.blockShadow")
    assert block["symbol_context"]["setter_calls"]["returned"] == 1
    for member in ("actionShadow", "nested", "read"):
        calls = _state_focus(atlas, symbol="simpleStore." + member)["symbol_context"]["setter_calls"]
        assert calls["items"] == []
    assert "nested_callable_excluded" in _state_focus(atlas, symbol="simpleStore.nested")["symbol_context"]["setter_calls"]["limitations"]
    assert _state_focus(atlas, symbol="fakeStore.update")["symbol_context"]["setter_calls"]["status"] == "unavailable"
    assert _state_focus(atlas, symbol="vanillaStore.sync")["symbol_context"]["setter_calls"]["returned"] == 1
    for owner, factory_form, middleware_form in (
        ("curriedStore", "curried", "none"),
        ("persistedStore", "direct", "persist"),
        ("curriedPersistedStore", "curried", "persist"),
    ):
        calls = _state_focus(atlas, symbol=owner + ".update")["symbol_context"]["setter_calls"]
        assert calls["status"] == "observed" and calls["returned"] == 1
        assert calls["runtime_execution"] == "not_established"
        assert (calls["factory_form"], calls["middleware_form"]) == (factory_form, middleware_form)
    for owner in ("fakeMiddlewareStore", "fakeCurriedStore", "typeOnlyStore",
                  "nonemptyCurriedStore", "chainedMiddlewareStore"):
        assert _state_focus(atlas, symbol=owner + ".update")["symbol_context"]["setter_calls"]["status"] == "unavailable"
    assert _state_focus(atlas, project="COMPANION", symbol="simpleStore.update")["status"] == "not_found"
    for symbol in ("useProjects.phantom", "useProjects.configOnly", "branches.nestedOnly",
                   "branches.methodOnly", "branches.getterOnly",
                   "opaque.importProjects", "nonliteral.conditionalOnly", "mixed.dynamicName"):
        assert _state_focus(atlas, symbol=symbol)["status"] == "not_found"
    assert _state_focus(atlas, symbol="branches.choose")["status"] == "ambiguous"
    mixed = _state_focus(atlas, symbol="mixed.save")
    assert "spread_or_computed_member_not_resolved" in mixed["initializer_member_coverage"]["limitations"]
    assert _state_focus(atlas, symbol="useProjects.importProjects", scan_limit=2)["status"] == "incomplete_search"
    fully_scanned = _state_focus(atlas, symbol="simpleStore.update", scan_limit=500)
    before_setter = (fully_scanned["visited_records"]
                     - fully_scanned["symbol_context"]["setter_calls"]["visited_records"]
                     - fully_scanned["direct_callees"]["visited_records"]
                     - fully_scanned["upstream_action_calls"]["visited_records"]
                     - fully_scanned["cross_file_action_calls"]["visited_records"]
                     - fully_scanned["hook_selector_candidates"]["visited_records"])
    before_upstream = (fully_scanned["visited_records"]
                       - fully_scanned["upstream_action_calls"]["visited_records"]
                       - fully_scanned["cross_file_action_calls"]["visited_records"]
                       - fully_scanned["hook_selector_candidates"]["visited_records"])
    before_cross = (fully_scanned["visited_records"]
                    - fully_scanned["cross_file_action_calls"]["visited_records"]
                    - fully_scanned["hook_selector_candidates"]["visited_records"])
    before_hook = fully_scanned["visited_records"] - fully_scanned["hook_selector_candidates"]["visited_records"]
    limited_hook = _state_focus(atlas, symbol="simpleStore.update", scan_limit=before_hook + 1)
    assert limited_hook["hook_selector_candidates"]["status"] == "incomplete_scan"
    limited_cross = _state_focus(atlas, symbol="simpleStore.update", scan_limit=before_cross + 1)
    assert limited_cross["cross_file_action_calls"]["status"] == "incomplete_scan"
    assert limited_cross["cross_file_action_calls"]["omitted"] is None
    limited_upstream = _state_focus(atlas, symbol="simpleStore.update", scan_limit=before_upstream + 1)
    assert limited_upstream["upstream_action_calls"]["status"] == "incomplete_scan"
    assert limited_upstream["visited_records"] == before_upstream + 1
    budgeted = _state_focus(atlas, symbol="simpleStore.update", scan_limit=before_setter)
    assert budgeted["symbol_context"]["setter_calls"]["status"] == "incomplete_scan"
    assert budgeted["symbol_context"]["setter_calls"]["items"] == []
    key_budgeted = _state_focus(atlas, symbol="simpleStore.update", scan_limit=before_setter + 2)
    key_projection = key_budgeted["symbol_context"]["setter_calls"]["items"][0]["literal_state_keys"]
    assert key_projection["status"] == "incomplete_scan" and key_projection["items"] == []
    assert key_projection["omitted"] == 3
    conflicting_module = copy.deepcopy(atlas)
    conflicting_module["MAIN"]["files"]["caller.ts"]["import_records"].append(
        dict(next(entry for entry in conflicting_module["MAIN"]["files"]["caller.ts"]["import_records"]
                  if entry.get("raw_source") == "./store" and entry.get("name") == "simpleStore")))
    conflict = _state_focus(conflicting_module, symbol="simpleStore.update", scan_limit=1000)["cross_file_action_calls"]
    assert conflict["status"] == "ambiguous" and conflict["items"] == []
    ambiguous_file = copy.deepcopy(atlas)
    ambiguous_file["MAIN"]["files"]["alias.ts"] = copy.deepcopy(ambiguous_file["MAIN"]["files"]["other-store.ts"])
    ambiguous_file["MAIN"]["files"]["alias.ts"]["workspace_rel"] = "store.ts"
    file_conflict = _state_focus(ambiguous_file, symbol="simpleStore.update", scan_limit=1000)["cross_file_action_calls"]
    assert file_conflict["status"] == "ambiguous" and file_conflict["reason"] == "ambiguous_imported_store_file"
    assert file_conflict["items"] == []
    ambiguous_file["MAIN"]["resolved_module_importer_index"] = build_resolved_module_importer_index(
        ambiguous_file["MAIN"]["files"])
    indexed_file_conflict = _state_focus(ambiguous_file, symbol="simpleStore.update", scan_limit=1000)["cross_file_action_calls"]
    assert indexed_file_conflict["status"] == "ambiguous"
    assert indexed_file_conflict["importer_lookup"] == "same_snapshot_resolved_module_importer_index"
    legacy_importer = copy.deepcopy(atlas)
    next(row for row in legacy_importer["MAIN"]["files"]["caller.ts"]["symbols"]
         if row["name"] == "callSelected")["same_file_store_action_call_evidence"]["binding_scope"] = "single_file_lexical_exported_store"
    legacy_cross = _state_focus(legacy_importer, symbol="simpleStore.update", scan_limit=1000)["cross_file_action_calls"]
    assert legacy_cross["status"] == "unavailable" and legacy_cross["items"] == []
    legacy_upstream = copy.deepcopy(atlas)
    for caller in legacy_upstream["MAIN"]["files"]["store.ts"]["symbols"]:
        caller.pop("same_file_store_action_call_evidence", None)
    assert _state_focus(legacy_upstream, symbol="simpleStore.update", scan_limit=500)["upstream_action_calls"]["status"] == "unavailable"
    partial_upstream = copy.deepcopy(atlas)
    next(caller for caller in partial_upstream["MAIN"]["files"]["store.ts"]["symbols"]
         if caller["name"] == "shadowStoreAction").pop("same_file_store_action_call_evidence")
    partial_result = _state_focus(partial_upstream, symbol="simpleStore.update", scan_limit=500)["upstream_action_calls"]
    assert partial_result["status"] == "unavailable" and partial_result["items"] == []
    malformed_schema = copy.deepcopy(next(row for row in symbols if row["name"] == "simpleStore")["initializer_member_evidence"])
    malformed_schema["members"][0]["zustand_setter_call_evidence"]["status"] = "invented"
    schema_errors = []
    _validate(malformed_schema, evidence_schema, schema, ["initializer"], schema_errors)
    assert schema_errors, "Production schema validator must reject invented setter evidence status"
    malformed_schema["members"][0]["zustand_setter_call_evidence"]["status"] = "observed"
    malformed_schema["members"][0]["zustand_setter_call_evidence"]["factory_form"] = "invented"
    schema_errors = []
    _validate(malformed_schema, evidence_schema, schema, ["initializer"], schema_errors)
    assert schema_errors, "Production schema validator must reject invented factory shape"
    malformed_schema["members"][0]["zustand_setter_call_evidence"]["factory_form"] = "direct"
    malformed_schema["members"][0]["zustand_setter_call_evidence"]["calls"][0]["literal_key_evidence"]["status"] = "invented"
    schema_errors = []
    _validate(malformed_schema, evidence_schema, schema, ["initializer"], schema_errors)
    assert schema_errors, "Production setter-call schema must reject invented literal-key status"
    malformed = copy.deepcopy(atlas)
    broken = next(row for row in malformed["MAIN"]["files"]["store.ts"]["symbols"] if row["name"] == "useProjects")
    broken["initializer_member_evidence"]["members"][0]["end_line"] = broken["end_line"] + 1
    assert _state_focus(malformed, symbol="useProjects.importProjects")["status"] == "evidence_unavailable"
    malformed_setter = copy.deepcopy(atlas)
    store_symbol = next(row for row in malformed_setter["MAIN"]["files"]["store.ts"]["symbols"]
                        if row["name"] == "simpleStore")
    update_member = next(row for row in store_symbol["initializer_member_evidence"]["members"]
                         if row["name"] == "update")
    update_member["zustand_setter_call_evidence"]["calls"][0]["line"] = 0
    rejected = _state_focus(malformed_setter, symbol="simpleStore.update")["symbol_context"]["setter_calls"]
    assert rejected["status"] == "unavailable" and rejected["items"] == []
    assert rejected["reason"] == "malformed_action_setter_call_site"
    malformed_key = copy.deepcopy(atlas)
    malformed_key_member = next(row for row in malformed_key["MAIN"]["files"]["store.ts"]["symbols"]
                                if row["name"] == "simpleStore")["initializer_member_evidence"]["members"][0]
    malformed_key_member["zustand_setter_call_evidence"]["calls"][0]["literal_key_evidence"]["keys"][0]["line"] = 0
    rejected_key = _state_focus(malformed_key, symbol="simpleStore.update")["symbol_context"]["setter_calls"]["items"][0]["literal_state_keys"]
    assert rejected_key["status"] == "unavailable" and rejected_key["items"] == []
    assert rejected_key["reason"] == "malformed_literal_key_site"
    missing_key = copy.deepcopy(atlas)
    missing_key_member = next(row for row in missing_key["MAIN"]["files"]["store.ts"]["symbols"]
                              if row["name"] == "simpleStore")["initializer_member_evidence"]["members"][0]
    del missing_key_member["zustand_setter_call_evidence"]["calls"][0]["literal_key_evidence"]
    legacy_key = _state_focus(missing_key, symbol="simpleStore.update")["symbol_context"]["setter_calls"]["items"][0]["literal_state_keys"]
    assert legacy_key["status"] == "unavailable" and legacy_key["reason"] == "missing_literal_key_evidence"
    malformed_form = copy.deepcopy(atlas)
    form_member = next(row for row in malformed_form["MAIN"]["files"]["store.ts"]["symbols"]
                       if row["name"] == "curriedPersistedStore")["initializer_member_evidence"]["members"][0]
    form_member["zustand_setter_call_evidence"]["factory_form"] = "invented"
    rejected_form = _state_focus(malformed_form, symbol="curriedPersistedStore.update")["symbol_context"]["setter_calls"]
    assert rejected_form["status"] == "unavailable" and rejected_form["reason"] == "malformed_action_setter_evidence"
    legacy_form = copy.deepcopy(atlas)
    legacy_member = next(row for row in legacy_form["MAIN"]["files"]["store.ts"]["symbols"]
                         if row["name"] == "curriedPersistedStore")["initializer_member_evidence"]["members"][0]
    del legacy_member["zustand_setter_call_evidence"]["factory_form"]
    del legacy_member["zustand_setter_call_evidence"]["middleware_form"]
    legacy_calls = _state_focus(legacy_form, symbol="curriedPersistedStore.update")["symbol_context"]["setter_calls"]
    assert legacy_calls["status"] == "observed"
    assert (legacy_calls["factory_form"], legacy_calls["middleware_form"]) == ("not_recorded", "not_recorded")
    malformed_call = copy.deepcopy(atlas)
    caller = next(row for row in malformed_call["MAIN"]["files"]["store.ts"]["symbols"] if row["name"] == "directCalls")
    caller["import_call_evidence"]["calls"][0]["line"] = 0
    assert _state_focus(malformed_call, symbol="directCalls")["symbol_context"]["import_calls"]["status"] == "unavailable"
    caller.pop("import_call_evidence")
    assert _state_focus(malformed_call, symbol="directCalls")["symbol_context"]["import_calls"]["reason"] == "missing_parser_call_evidence"
    malformed_target = copy.deepcopy(atlas)
    target_symbol = next(row for row in malformed_target["MAIN"]["files"]["repository.ts"]["symbols"]
                         if row["name"] == "persist")
    target_symbol["import_call_evidence"]["calls"][0]["line"] = 0
    target_context = _state_focus(malformed_target, symbol="directCalls", max_items=8)["direct_callees"]["target_symbol_contexts"][0]
    assert target_context["import_calls"]["status"] == "unavailable"
    assert target_context["import_calls"]["items"] == []
    invalid_span = copy.deepcopy(atlas)
    next(row for row in invalid_span["MAIN"]["files"]["repository.ts"]["symbols"]
         if row["name"] == "persist")["end_line"] = 0
    bad_target = _state_focus(invalid_span, symbol="ProjectStore.importProjects")["direct_callees"]
    assert bad_target["items"][0]["reason"] == "target_symbol_span_unavailable"
    assert bad_target["target_symbol_contexts"] == []
    all_calls = _state_focus(atlas, symbol="directCalls", max_items=8)
    before_calls = (all_calls["visited_records"]
                    - all_calls["symbol_context"]["import_calls"]["visited_records"]
                    - all_calls["direct_callees"]["visited_records"])
    capped = _state_focus(atlas, symbol="directCalls", max_items=8, scan_limit=before_calls + 1)
    assert capped["symbol_context"]["import_calls"]["status"] == "incomplete_scan"
    assert capped["symbol_context"]["import_calls"]["items"] == []
    assert capped["visited_records"] == before_calls + 1
    assert capped["direct_callees"]["status"] == "incomplete_scan"

    # Same real producer output through SQLite and the public MCP consumer.
    atlas.pop("COMPANION")
    atlas["MAIN"].update(root_path=".", project_type="typescript", dependencies={})
    for rel, content in (("store.ts", source), ("repository.ts", repository_source),
                         ("adapter.ts", adapter_source),
                         ("barrel.ts", barrel_source), ("caller.ts", caller_source),
                         ("other-store.ts", other_source)):
        atlas["MAIN"]["files"][rel].update(
            workspace_rel=rel, hash=hashlib.sha256(content.encode()).hexdigest(),
            language="typescript", size=len(content.encode("utf-8")))
    atlas["MAIN"]["resolved_module_importer_index"] = build_resolved_module_importer_index(
        atlas["MAIN"]["files"])
    monkeypatch.setattr(artifact_store, "ROOT", tmp_path)
    raw_dir = tmp_path / ".raw"
    raw_dir.mkdir()
    store = ArtifactStore(raw_dir=raw_dir)
    store.use_sqlite = True
    store.backend = "hybrid_sqlite"
    store.db_manager = SQLiteManager(raw_dir / "codemaps.db")
    store._schema_initialized = False
    store._ensure_schema()
    store.save_raw("atlas", atlas)
    monkeypatch.setattr(server, "_raw_dir_for_target", lambda _target: raw_dir)
    monkeypatch.setattr(server, "_analysis_root_display", lambda _target="": str(tmp_path))
    def query(symbol="ProjectStore.importProjects"):
        return json.loads(server.get_state_flow(file="store.ts", symbol=symbol,
                                               target_root=str(tmp_path), format="json"))
    current = query()
    assert current["source_binding"] == "snapshot_and_live_match"
    assert current["source_grounding"]["target_spans"][0]["symbol"] == "ProjectStore"
    assert current["import_candidates"]["items"][0]["endpoint_content_binding"] == "both_snapshot_and_live_match"
    assert current["direct_callees"]["items"][0]["endpoint_content_binding"] == "both_snapshot_and_live_match"
    assert current["direct_callees"]["items"][0]["target"]["symbol"] == "persist"
    current_context = current["direct_callees"]["target_symbol_contexts"][0]
    assert current_context["endpoint_content_binding"] == "both_snapshot_and_live_match"
    assert current_context["import_calls"]["source_binding"] == "snapshot_and_live_match"
    current_outbound = current_context["outbound_calls"]
    assert current_outbound["status"] == "observed" and current_outbound["returned"] == 2
    assert all(item["endpoint_content_binding"] == "all_three_snapshot_and_live_match"
               for item in current_outbound["items"])
    adapter.write_text("// adapter changed\n", encoding="utf-8")
    drifted_adapter = query()["direct_callees"]["target_symbol_contexts"][0]["outbound_calls"]
    assert all(item["endpoint_content_binding"] == "not_currently_bound"
               for item in drifted_adapter["items"])
    adapter.write_text(adapter_source, encoding="utf-8", newline="")
    current_setter = query("simpleStore.update")["symbol_context"]["setter_calls"]
    current_upstream = query("simpleStore.update")["upstream_action_calls"]
    assert current_upstream["status"] == "observed" and current_upstream["returned"] == 2
    assert current_upstream["source_binding"] == "snapshot_and_live_match"
    assert current_upstream["items"][0]["endpoint_content_binding"] == "same_file_snapshot_and_live_match"
    current_cross = query("simpleStore.update")["cross_file_action_calls"]
    assert current_cross["status"] == "observed" and current_cross["returned"] == 2
    assert current_cross["importer_lookup"] == "same_snapshot_resolved_module_importer_index"
    assert current_cross["items"][0]["endpoint_content_binding"] == "both_snapshot_and_live_match"
    current_hook = query("simpleStore.update")["hook_selector_candidates"]
    assert current_hook["status"] == "observed" and current_hook["returned"] == 2
    assert all(item["endpoint_content_binding"] == "both_snapshot_and_live_match"
               for item in current_hook["items"])
    caller_path.write_text("// caller changed\n", encoding="utf-8")
    drifted_cross = query("simpleStore.update")["cross_file_action_calls"]
    assert drifted_cross["items"][0]["source_binding"] == "snapshot_match_live_unverified"
    assert drifted_cross["items"][0]["endpoint_content_binding"] == "not_currently_bound"
    drifted_hook = query("simpleStore.update")["hook_selector_candidates"]
    assert next(item for item in drifted_hook["items"] if item["caller_symbol"] == "selectImportedHook")["endpoint_content_binding"] == "not_currently_bound"
    caller_path.write_text(caller_source, encoding="utf-8", newline="")
    assert current_setter["source_binding"] == "snapshot_and_live_match"
    assert current_setter["returned"] == 2 and current_setter["omitted"] == 0
    current_keys = current_setter["items"][0]["literal_state_keys"]
    assert current_keys["source_binding"] == "snapshot_and_live_match"
    assert [item["name"] for item in current_keys["items"]] == ["ready", "label", "nested"]
    persisted_setter = query("curriedPersistedStore.update")["symbol_context"]["setter_calls"]
    assert persisted_setter["source_binding"] == "snapshot_and_live_match"
    assert (persisted_setter["factory_form"], persisted_setter["middleware_form"]) == ("curried", "persist")
    assert persisted_setter["returned"] == 1 and persisted_setter["runtime_execution"] == "not_established"
    setter_brief = server.get_state_flow(file="store.ts", symbol="simpleStore.update",
                                         target_root=str(tmp_path), format="brief")
    assert "setter_calls" in setter_brief and "literal_state_keys" in setter_brief
    assert "hook_selector_candidates" in setter_brief and "not_established" in setter_brief
    assert "not_state_write" in setter_brief and "not_established" in setter_brief
    rendered = server.get_state_flow(file="store.ts", symbol="ProjectStore.importProjects",
                                     target_root=str(tmp_path))
    assert "# State Flow Brief" in rendered
    assert '"target_symbol_contexts"' in rendered and '"adapterWrite"' in rendered
    factory = query("useProjects.importProjects")
    assert factory["status"] == "selected"
    assert factory["source_binding"] == "snapshot_and_live_match"
    assert factory["symbol_context"]["import_calls"]["items"][0]["localName"] == "save"
    assert factory["symbol_context"]["import_calls"]["source_binding"] == "snapshot_and_live_match"
    assert factory["source_grounding_scope"] == "declaring_symbol_not_member_span"
    assert factory["source_grounding"]["target_spans"][0]["symbol"] == "useProjects"
    assert factory["target"]["owner_attribution"] == "returned_object_candidate"
    assert factory["import_candidates"]["items"][0]["endpoint_content_binding"] == "both_snapshot_and_live_match"
    repository.write_text("// target changed\n", encoding="utf-8")
    drifted = query()
    assert drifted["source_binding"] == "snapshot_and_live_match"
    assert drifted["import_candidates"]["items"][0]["source_binding"] == "snapshot_match_live_unverified"
    assert drifted["import_candidates"]["items"][0]["endpoint_content_binding"] == "not_currently_bound"
    assert drifted["direct_callees"]["items"][0]["endpoint_content_binding"] == "not_currently_bound"
    assert drifted["direct_callees"]["target_symbol_contexts"][0]["endpoint_content_binding"] == "not_currently_bound"
    assert all(item["endpoint_content_binding"] == "not_currently_bound"
               for item in drifted["direct_callees"]["target_symbol_contexts"][0]["outbound_calls"]["items"])
    with store.db_manager.get_connection() as conn:
        conn.execute("UPDATE source_snapshots SET content = 'corrupt' WHERE project_key = 'MAIN' AND rel_path = 'repository.ts';")
    assert query()["import_candidates"]["items"][0]["source_binding"] == "unavailable"
    assert query()["direct_callees"]["items"][0]["source_binding"] == "unavailable"
    assert query()["direct_callees"]["target_symbol_contexts"][0]["source_binding"] == "unavailable"
    path.write_text("// source changed\n", encoding="utf-8")
    assert query()["symbol_context"]["import_calls"]["source_binding"] == "snapshot_match_live_unverified"
    assert query("simpleStore.update")["symbol_context"]["setter_calls"]["source_binding"] == "snapshot_match_live_unverified"
    drifted_upstream = query("simpleStore.update")["upstream_action_calls"]
    assert drifted_upstream["source_binding"] == "snapshot_match_live_unverified"
    assert drifted_upstream["items"][0]["endpoint_content_binding"] == "not_currently_bound"
    drifted_key = query("simpleStore.update")["symbol_context"]["setter_calls"]["items"][0]["literal_state_keys"]
    assert drifted_key["source_binding"] == "snapshot_match_live_unverified"


def test_state_flow_const_destructured_action_parser_excludes_unsupported_bindings(tmp_path):
    import shutil
    import pytest
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the real producer control")
    path = tmp_path / "store.ts"
    path.write_text('''import { create } from "zustand";
export const store = create((set) => ({ update: () => set({ ready: true }) }));
export function direct() { const { update } = store.getState(); update(); }
export function renamed() { const { update: alias } = store.getState(); alias(); }
export function mutable() { let { update } = store.getState(); update(); }
export function optional() { const { update } = store?.getState(); update(); }
export function shadow(store: any) { const { update } = store.getState(); update(); }
export function nested() { const later = () => { const { update } = store.getState(); update(); }; return later; }
''', encoding="utf-8")
    root = Path(__file__).resolve().parents[2]
    proc = subprocess.run([node, str(root / "tools/engines/ast_sequencer.cjs"), str(path)],
                          cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert proc.returncode == 0, proc.stderr
    symbols = {row["name"]: row for row in json.loads(proc.stdout) if row.get("name")}
    assert [(call["store"], call["action"], call["call_form"])
            for call in symbols["direct"]["same_file_store_action_call_evidence"]["calls"]] == [
        ("store", "update", "const_destructured_getstate_action")]
    for name in ("renamed", "mutable", "optional", "shadow", "nested"):
        evidence = symbols[name]["same_file_store_action_call_evidence"]
        assert evidence["status"] == "observed" and evidence["calls"] == [], name


def test_state_flow_hook_result_calls_cap_and_lexical_exclusions(tmp_path):
    import shutil
    import pytest
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the real producer control")
    path = tmp_path / "hook.ts"
    path.write_text('''import { create } from "zustand";
export const store = create((set) => ({ update: () => set({ ready: true }) }));
export function run() {
    const selected = store((state) => state.update);
    selected(); selected(); selected(); selected(); selected();
    selected(); selected(); selected(); selected(); selected();
    const alias = selected; alias(); selected?.();
    { const selected = () => 1; selected(); }
    const deferred = () => selected();
    return deferred;
}
''', encoding="utf-8")
    root = Path(__file__).resolve().parents[2]
    proc = subprocess.run([node, str(root / "tools/engines/ast_sequencer.cjs"), str(path)],
                          cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert proc.returncode == 0, proc.stderr
    raw = json.loads(proc.stdout)
    calls = next(row for row in raw if row.get("name") == "run")["store_hook_selector_evidence"]["calls"]
    assert len(calls) == 1
    assert len(calls[0]["selected_result_direct_calls"]) == 8
    assert calls[0]["selected_result_direct_calls_omitted"] == 2
    assert {entry["line"] for entry in calls[0]["selected_result_direct_calls"]} == {5, 6}
    from tools.core.artifact_validator import _validate
    schema = json.loads((root / "config/schemas/atlas.schema.json").read_text(encoding="utf-8"))
    evidence = next(row for row in raw if row.get("name") == "run")["store_hook_selector_evidence"]
    evidence["calls"][0]["selected_result_direct_calls"].append(
        evidence["calls"][0]["selected_result_direct_calls"][0])
    errors = []
    _validate(evidence, schema["$defs"]["store_hook_selector_evidence"],
              schema, ["hook_selectors"], errors)
    assert any("at most 8 items" in error for error in errors)


def test_state_flow_exported_hook_direct_return_owner_is_positive_syntax_only(tmp_path, monkeypatch):
    import copy
    import hashlib
    import shutil
    import pytest
    from tools.engines.generate_atlas import _normalize_polyglot_symbols
    from tools.core.polyglot_imports import extract_typescript_import_evidence
    from tools.core.path_engine import resolve_project_import
    from tools.core import artifact_store
    from tools.core.artifact_store import ArtifactStore
    from tools.core.db import SQLiteManager

    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the real producer control")
    source = '''import { create } from "zustand";
export const store = create((set) => ({ update: () => set({ ready: true }) }));
export function useDirect() { return store((state) => state.update); }
export const useConcise = () => store((state) => state.update);
export function useIndirect() { const selected = store((state) => state.update); return selected; }
export function useConditional(flag: boolean) { return flag ? store((state) => state.update) : undefined; }
export function useGuarded(flag: boolean) { if (flag) return store((state) => state.update); return undefined; }
export async function useAsync() { return store((state) => state.update); }
export function notAHook() { return store((state) => state.update); }
export let declaredOnly: () => void;
export function SameFileScreen() { useDirect(); useDirect?.(); declaredOnly(); const later = () => useDirect(); return later; }
export function ShadowSameFile(useDirect: () => void) { useDirect(); }
export function WrongSameFile() { useConcise(); }
'''
    path = tmp_path / "hook-return.ts"
    path.write_text(source, encoding="utf-8", newline="")
    root = Path(__file__).resolve().parents[2]
    proc = subprocess.run([node, str(root / "tools/engines/ast_sequencer.cjs"), str(path)],
                          cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert proc.returncode == 0, proc.stderr
    raw = json.loads(proc.stdout)
    symbols = _normalize_polyglot_symbols(raw, source, language="typescript")
    from tools.core.artifact_validator import _validate
    schema = json.loads((root / "config/schemas/atlas.schema.json").read_text(encoding="utf-8"))
    hook_schema = schema["$defs"]["store_hook_selector_evidence"]
    for symbol in symbols:
        if "store_hook_selector_evidence" in symbol:
            errors = []
            _validate(symbol["store_hook_selector_evidence"], hook_schema, schema, ["hook_selectors"], errors)
            assert not errors, (symbol["name"], errors)
        if "same_file_direct_call_evidence" in symbol:
            errors = []
            _validate(symbol["same_file_direct_call_evidence"],
                      schema["$defs"]["same_file_direct_call_evidence"], schema,
                      ["same_file_direct_calls"], errors)
            assert not errors, (symbol["name"], errors)
    atlas = {"MAIN": {"files": {"hook-return.ts": {
        "workspace_rel": "hook-return.ts", "hash": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        "symbols": symbols, "import_records": [], "features": [],
    }}}}
    consumer_source = '''import { useDirect as useSelection } from "./hook-return.js";
import { useDirect as useFake } from "./lookalike";
import type { useDirect as TypeHook } from "./hook-return";
export function Screen() {
  useSelection(); useFake(); useSelection?.(); TypeHook();
  const later = () => useSelection();
  return later;
}
export function shadow(useSelection: () => void) { useSelection(); }
'''
    (tmp_path / "lookalike.ts").write_text('export function useDirect() {}\n', encoding="utf-8", newline="")
    (tmp_path / "consumer.ts").write_text(consumer_source, encoding="utf-8", newline="")
    consumer_proc = subprocess.run([node, str(root / "tools/engines/ast_sequencer.cjs"),
                                    str(tmp_path / "consumer.ts")], cwd=root,
                                   capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert consumer_proc.returncode == 0, consumer_proc.stderr
    consumer_raw = json.loads(consumer_proc.stdout)
    atlas["MAIN"]["files"]["consumer.ts"] = {
        "workspace_rel": "consumer.ts", "hash": hashlib.sha256(consumer_source.encode()).hexdigest(),
        "symbols": _normalize_polyglot_symbols(consumer_raw, consumer_source, language="typescript"),
        "import_records": [dict(entry, raw_source=entry["source"],
                                source=resolve_project_import(entry["source"], str(tmp_path),
                                                              str(tmp_path), str(tmp_path)))
                           for entry in extract_typescript_import_evidence(consumer_raw)["records"]],
        "features": [],
    }
    focused = _state_focus(atlas, file="hook-return.ts", symbol="store.update",
                           max_items=8, scan_limit=300)
    hooks = focused["hook_selector_candidates"]
    assert hooks["status"] == "observed", hooks
    by_name = {item["caller_symbol"]: item for item in hooks["items"]}
    assert by_name["useDirect"]["hook_return_owner"]["return_form"] == "return_statement"
    assert by_name["useConcise"]["hook_return_owner"]["return_form"] == "arrow_expression"
    assert by_name["useDirect"]["hook_return_owner"]["runtime_execution"] == "not_established"
    direct_focus = _state_focus(atlas, file="hook-return.ts", symbol="useDirect",
                                max_items=8, scan_limit=300)
    direct_hook = direct_focus["upstream_direct_import_calls"]
    same_file = direct_focus["same_file_direct_calls"]
    assert same_file["status"] == "observed", same_file
    assert [(item["caller_symbol"], item["call_line"]) for item in same_file["items"]] == [
        ("SameFileScreen", 11)]
    assert same_file["items"][0]["runtime_execution"] == "not_established"
    assert direct_hook["status"] == "observed", direct_hook
    assert [(item["caller_symbol"], item["call_line"]) for item in direct_hook["items"]] == [
        ("Screen", 5)]
    assert direct_hook["items"][0]["runtime_execution"] == "not_established"
    legacy_same_file = copy.deepcopy(atlas)
    next(row for row in legacy_same_file["MAIN"]["files"]["hook-return.ts"]["symbols"]
         if row["name"] == "SameFileScreen").pop("same_file_direct_call_evidence")
    assert _state_focus(legacy_same_file, file="hook-return.ts", symbol="useDirect",
                        max_items=8, scan_limit=300)["same_file_direct_calls"]["status"] == "unavailable"
    forged_same_file = copy.deepcopy(atlas)
    forged_call = next(row for row in forged_same_file["MAIN"]["files"]["hook-return.ts"]["symbols"]
                       if row["name"] == "SameFileScreen")["same_file_direct_call_evidence"]["calls"][0]
    forged_call["line"] = 0
    errors = []
    _validate(next(row for row in forged_same_file["MAIN"]["files"]["hook-return.ts"]["symbols"]
                   if row["name"] == "SameFileScreen")["same_file_direct_call_evidence"],
              schema["$defs"]["same_file_direct_call_evidence"], schema, ["same_file"], errors)
    assert errors
    assert _state_focus(forged_same_file, file="hook-return.ts", symbol="useDirect",
                        max_items=8, scan_limit=300)["same_file_direct_calls"]["status"] == "unavailable"
    before_lane = direct_focus["visited_records"] - direct_hook["visited_records"]
    capped = _state_focus(atlas, file="hook-return.ts", symbol="useDirect",
                          max_items=8, scan_limit=before_lane + 1)["upstream_direct_import_calls"]
    assert capped["status"] == "incomplete_scan" and capped["omitted"] is None
    wrong_module = copy.deepcopy(atlas)
    for entry in wrong_module["MAIN"]["files"]["consumer.ts"]["import_records"]:
        if entry.get("raw_source") == "./hook-return.js":
            entry["source"] = "lookalike.ts"
    assert _state_focus(wrong_module, file="hook-return.ts", symbol="useDirect",
                        max_items=8, scan_limit=300)["upstream_direct_import_calls"]["items"] == []
    malformed_caller = copy.deepcopy(atlas)
    screen = next(row for row in malformed_caller["MAIN"]["files"]["consumer.ts"]["symbols"]
                  if row["name"] == "Screen")
    screen["import_call_evidence"]["calls"][0]["line"] = 0
    assert _state_focus(malformed_caller, file="hook-return.ts", symbol="useDirect",
                        max_items=8, scan_limit=300)["upstream_direct_import_calls"]["status"] == "unavailable"
    missing_source = copy.deepcopy(atlas)
    missing_source["MAIN"]["files"]["hook-return.ts"]["hash"] = ""
    assert _state_focus(missing_source, file="hook-return.ts", symbol="useDirect",
                        max_items=8, scan_limit=300)["upstream_direct_import_calls"]["status"] == "unavailable"
    legacy_caller = copy.deepcopy(atlas)
    screen = next(row for row in legacy_caller["MAIN"]["files"]["consumer.ts"]["symbols"]
                  if row["name"] == "Screen")
    screen.pop("import_call_evidence")
    assert _state_focus(legacy_caller, file="hook-return.ts", symbol="useDirect",
                        max_items=8, scan_limit=300)["upstream_direct_import_calls"]["status"] == "unavailable"
    ambiguous_file = copy.deepcopy(atlas)
    ambiguous_file["MAIN"]["files"]["shadow-copy.ts"] = dict(
        atlas["MAIN"]["files"]["consumer.ts"], workspace_rel="hook-return.ts", symbols=[])
    assert _state_focus(ambiguous_file, file="hook-return.ts", symbol="useDirect",
                        max_items=8, scan_limit=300)["upstream_direct_import_calls"]["status"] == "ambiguous"
    for name in ("useIndirect", "useConditional", "useGuarded", "useAsync", "notAHook"):
        assert "hook_return_owner" not in by_name[name]
    forged = copy.deepcopy(atlas)
    row = next(item for item in forged["MAIN"]["files"]["hook-return.ts"]["symbols"]
               if item["name"] == "useDirect")
    row["store_hook_selector_evidence"]["calls"][0]["direct_return_form"] = "invented"
    errors = []
    _validate(row["store_hook_selector_evidence"], hook_schema, schema, ["hook_selectors"], errors)
    assert errors, "Production Atlas schema must reject a forged hook return form"
    rejected = _state_focus(forged, file="hook-return.ts", symbol="store.update",
                            max_items=8, scan_limit=300)["hook_selector_candidates"]
    assert rejected["status"] == "unavailable" and rejected["items"] == []

    atlas["MAIN"].update(root_path=".", project_type="typescript", dependencies={})
    for rel, record in atlas["MAIN"]["files"].items():
        record.update(language="typescript", size=(tmp_path / rel).stat().st_size)
    monkeypatch.setattr(artifact_store, "ROOT", tmp_path)
    raw_dir = tmp_path / ".raw"
    raw_dir.mkdir()
    store = ArtifactStore(raw_dir=raw_dir)
    store.use_sqlite = True
    store.backend = "hybrid_sqlite"
    store.db_manager = SQLiteManager(raw_dir / "codemaps.db")
    store._schema_initialized = False
    store._ensure_schema()
    store.save_raw("atlas", atlas)
    monkeypatch.setattr(server, "_raw_dir_for_target", lambda _target: raw_dir)
    monkeypatch.setattr(server, "_analysis_root_display", lambda _target="": str(tmp_path))
    def query_hook():
        return json.loads(server.get_state_flow(file="hook-return.ts", symbol="useDirect",
                                               target_root=str(tmp_path), format="json"))
    current = query_hook()["upstream_direct_import_calls"]["items"][0]
    assert current["endpoint_content_binding"] == "both_snapshot_and_live_match"
    assert query_hook()["same_file_direct_calls"]["items"][0][
        "endpoint_content_binding"] == "both_snapshot_and_live_match"
    hook_brief = server.get_state_flow(file="hook-return.ts", symbol="useDirect",
                                       target_root=str(tmp_path), format="brief")
    assert "upstream_direct_import_calls" in hook_brief and "not_established" in hook_brief
    (tmp_path / "consumer.ts").write_text("// changed\n", encoding="utf-8", newline="")
    assert query_hook()["upstream_direct_import_calls"]["items"][0][
        "endpoint_content_binding"] == "not_currently_bound"
    (tmp_path / "hook-return.ts").write_text("// changed\n", encoding="utf-8", newline="")
    assert query_hook()["same_file_direct_calls"]["items"][0][
        "endpoint_content_binding"] == "not_currently_bound"


def test_state_flow_same_file_direct_calls_cap_is_incomplete_not_absent(tmp_path):
    import hashlib
    import shutil
    import pytest
    from tools.engines.generate_atlas import _normalize_polyglot_symbols

    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the real producer control")
    source = "export function useTarget() {}\nexport function caller() {\n" + (
        "  useTarget();\n" * 70) + "}\n"
    path = tmp_path / "many.ts"
    path.write_text(source, encoding="utf-8", newline="")
    root = Path(__file__).resolve().parents[2]
    proc = subprocess.run([node, str(root / "tools/engines/ast_sequencer.cjs"), str(path)],
                          cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert proc.returncode == 0, proc.stderr
    symbols = _normalize_polyglot_symbols(json.loads(proc.stdout), source, language="typescript")
    evidence = next(row for row in symbols if row["name"] == "caller")["same_file_direct_call_evidence"]
    assert evidence["status"] == "incomplete_scan"
    assert len(evidence["calls"]) == 64 and evidence["omitted"] == 6
    from tools.core.artifact_validator import _validate
    schema = json.loads((root / "config/schemas/atlas.schema.json").read_text(encoding="utf-8"))
    forged = dict(evidence, calls=evidence["calls"] + [evidence["calls"][0]])
    errors = []
    _validate(forged, schema["$defs"]["same_file_direct_call_evidence"],
              schema, ["same_file_direct_call_evidence"], errors)
    assert any("at most 64 items" in error for error in errors)
    atlas = {"MAIN": {"files": {"many.ts": {
        "workspace_rel": "many.ts", "hash": hashlib.sha256(source.encode()).hexdigest(),
        "symbols": symbols, "import_records": [], "features": [],
    }}}}
    lane = _state_focus(atlas, file="many.ts", symbol="useTarget",
                        max_items=3, scan_limit=300)["same_file_direct_calls"]
    assert lane["status"] == "incomplete_scan" and lane["omitted"] is None
    assert lane["returned"] == 3 and lane["reason"] == "parser_call_output_capped"


def test_state_flow_import_calls_fail_closed_on_parser_errors(tmp_path):
    import shutil
    import pytest
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the real producer control")
    path = tmp_path / "broken.ts"
    path.write_text('import { save } from "./unread"; import { create } from "zustand"; '
                    'export const store = create((set) => ({ update: () => set({ value: 1 }) })); '
                    'export function run() { save(); const bad = ; }', encoding="utf-8")
    root = Path(__file__).resolve().parents[2]
    proc = subprocess.run([node, str(root / "tools/engines/ast_sequencer.cjs"), str(path)],
                          cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert proc.returncode == 0, proc.stderr
    raw = json.loads(proc.stdout)
    evidence = next(symbol for symbol in raw if symbol["name"] == "run")["import_call_evidence"]
    upstream_evidence = next(symbol for symbol in raw if symbol["name"] == "run")["same_file_store_action_call_evidence"]
    assert upstream_evidence["status"] == "unavailable" and upstream_evidence["calls"] == []
    hook_evidence = next(symbol for symbol in raw if symbol["name"] == "run")["store_hook_selector_evidence"]
    assert hook_evidence["status"] == "unavailable" and hook_evidence["calls"] == []
    assert evidence["status"] == "unavailable"
    assert evidence["calls"] == []
    assert evidence["limitations"] == ["source_parse_diagnostics"]
    store = next(symbol for symbol in raw if symbol["name"] == "store")
    assert all(member.get("zustand_setter_call_evidence", {}).get("status") != "observed"
               for member in store["initializerMemberEvidence"]["members"])
    assert next(symbol for symbol in raw if symbol["name"] == "__file_meta__")["parserStatus"] == "degraded"
def test_state_flow_imported_event_emitter_uses_real_parser_and_exact_module(tmp_path, monkeypatch):
    import copy
    import hashlib
    import shutil
    import subprocess
    import pytest
    from tools.engines.generate_atlas import _normalize_polyglot_symbols
    from tools.core.polyglot_imports import extract_typescript_import_evidence
    from tools.core.path_engine import resolve_project_import
    from tools.core.artifact_validator import _validate
    from tools.core import artifact_store
    from tools.core.artifact_store import ArtifactStore
    from tools.core.db import SQLiteManager

    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the real producer control")
    root = Path(__file__).resolve().parents[2]
    sources = {
        "bus.ts": 'import { EventEmitter } from "node:events";\nexport const eventBus = new EventEmitter<{ ready: [] }>();\n',
        "alias-bus.ts": 'import { EventEmitter as Emitter } from "events";\nexport const aliasedBus = new Emitter();\n',
        "mutable.ts": 'import { EventEmitter } from "node:events";\nexport let eventBus = new EventEmitter();\n',
        "wrong.ts": 'export const eventBus = { emit(_key: string) {} };\n',
        "consumer.ts": '''import { eventBus as bus } from "./bus.js";
import { eventBus as wrong } from "./wrong";
import type { eventBus as TypeBus } from "./bus";
export function start() {
  bus.on("ready", () => {});
  bus.emit("ready");
  wrong.emit("wrong");
  bus.emit?.("optional");
  bus.emit(dynamicName);
  TypeBus.emit("type-only");
  const alias = bus; alias.emit("alias");
  const later = () => bus.emit("nested");
  const cleanup = () => bus.off("ready", () => {});
  return later;
}
export function shadow(bus: { emit(key: string): void }) { bus.emit("shadow"); }
''',
        "members.ts": '''import { eventBus as bus } from "./bus.js";
import type { eventBus as TypeBus } from "./bus";
const aliasBus = bus;
export class Worker {
  start() { bus.on("class-ready", () => {}); this.eventBus.emit("not-imported"); }
  stop = () => bus.emit("field-ready");
}
export const operations = {
  dispatch() { bus.emit("object-ready"); },
  ignored: 1,
};
export const createOperations = () => ({ dispatch() { bus.emit("returned-owner"); } });
export class FieldOwner {
  private readonly held = bus;
  start() { this.held.emit("field-owner"); this.held.emit?.("optional");
    this["held"].emit("computed-owner"); this.held.emit(dynamicName);
    const later = () => this.held.emit("nested-owner"); return later; }
}
export class MutableField { private held = bus; start() { this.held.emit("mutable-owner"); } }
export class ReassignedField {
  private readonly held = bus;
  start() { this.held = wrong; this.held.emit("reassigned-owner"); }
}
export class WrongField { private readonly held = wrong; start() { this.held.emit("wrong-owner"); } }
export class TypeOnlyField { private readonly held = TypeBus; start() { this.held.emit("type-owner"); } }
export class AliasField { private readonly held = aliasBus; start() { this.held.emit("alias-owner"); } }
''',
    }
    files = {}
    for rel, source in sources.items():
        path = tmp_path / rel
        path.write_text(source, encoding="utf-8", newline="")
        parsed = subprocess.run([node, str(root / "tools/engines/ast_sequencer.cjs"), str(path)],
                                cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=30)
        assert parsed.returncode == 0, parsed.stderr
        raw = json.loads(parsed.stdout)
        imports = extract_typescript_import_evidence(raw)["records"]
        files[rel] = {
            "workspace_rel": rel, "hash": hashlib.sha256(source.encode()).hexdigest(),
            "symbols": _normalize_polyglot_symbols(raw, source, language="typescript"),
            "import_records": [dict(entry, raw_source=entry["source"],
                                    source=resolve_project_import(entry["source"], str(tmp_path),
                                                                  str(tmp_path), str(tmp_path)))
                               for entry in imports],
        }
    atlas = {"MAIN": {"files": files}}
    schema = json.loads((root / "config/schemas/atlas.schema.json").read_text(encoding="utf-8"))
    proof = next(row for row in files["bus.ts"]["symbols"] if row["name"] == "eventBus")[
        "event_emitter_singleton_evidence"]
    assert proof["status"] == "observed", proof
    errors = []
    _validate(proof, schema["$defs"]["event_emitter_singleton_evidence"], schema, ["bus"], errors)
    assert errors == []
    malformed = dict(proof)
    malformed.pop("module_source")
    _validate(malformed, schema["$defs"]["event_emitter_singleton_evidence"],
              schema, ["malformed_bus"], errors)
    assert errors
    member_schema = schema["additionalProperties"]["properties"]["files"]["additionalProperties"][
        "properties"]["symbols"]["items"]["properties"]["member_details"]["items"]
    worker_member = next(row for row in files["members.ts"]["symbols"]
                         if row["name"] == "Worker")["member_details"][0]
    field_owner = next(row for row in files["members.ts"]["symbols"]
                       if row["name"] == "FieldOwner")
    assert [(call["owner_field"], call["caller_member"], call["first_literal_argument"])
            for call in field_owner["class_instance_event_call_evidence"]["calls"]] == [
                ("held", "start", "field-owner")]
    field_schema = schema["$defs"]["class_instance_event_call_evidence"]
    field_errors = []
    _validate(field_owner["class_instance_event_call_evidence"], field_schema,
              schema, ["instance_field"], field_errors)
    assert field_errors == []
    forged_field = copy.deepcopy(field_owner["class_instance_event_call_evidence"])
    forged_field["calls"][0]["member_line"] = 0
    _validate(forged_field, field_schema, schema, ["forged_instance_field"], field_errors)
    assert field_errors
    for class_name in ("MutableField", "ReassignedField", "TypeOnlyField", "AliasField"):
        assert next(row for row in files["members.ts"]["symbols"]
                    if row["name"] == class_name)["class_instance_event_call_evidence"]["calls"] == []
    member_errors = []
    _validate(worker_member, member_schema, schema, ["worker_member"], member_errors)
    assert member_errors == []
    _validate(dict(worker_member, line=0), member_schema, schema, ["invalid_member"], member_errors)
    assert member_errors
    result = _state_focus(atlas, file="bus.ts", symbol="eventBus", max_items=8, scan_limit=300)
    assert result["status"] == "selected"
    lane = result["event_bus_calls"]
    assert lane["status"] == "observed", lane
    assert [(item["method"], item["event"]) for item in lane["items"]] == [
        ("on", "ready"), ("emit", "ready"), ("emit", "nested"), ("off", "ready"),
        ("on", "class-ready"), ("emit", "field-ready"), ("emit", "object-ready"),
        ("emit", "field-owner")]
    assert [item["callable_nesting"] for item in lane["items"]] == [
        "direct_caller_body_syntax", "direct_caller_body_syntax",
        "nested_anonymous_callback_syntax", "nested_anonymous_callback_syntax",
        "direct_caller_body_syntax", "direct_caller_body_syntax", "direct_caller_body_syntax",
        "direct_caller_body_syntax"]
    assert [item["caller"]["atlas_ref"] for item in lane["items"]] == [
        "MAIN::consumer.ts"] * 4 + ["MAIN::members.ts"] * 4
    assert result["unknowns"]["runtime_execution"] == "not_performed"
    assert all(item["runtime_execution"] == "not_established" for item in lane["items"])
    assert [(item["caller_symbol"], item["caller_member"], item["event"])
            for item in lane["items"] if item["caller_member"]] == [
                ("Worker", "start", "class-ready"),
                ("Worker", "stop", "field-ready"),
                ("operations", "dispatch", "object-ready"),
                ("FieldOwner", "start", "field-owner")]
    assert all(item["caller_owner_kind"] in {"class_member_syntax", "direct_object_member_syntax",
                                             "readonly_class_field_initializer_syntax"}
               for item in lane["items"] if item["caller_member"])
    assert lane["instance_property_coverage"] == "parser_recorded_class_callers"
    assert lane["items"][-1]["runtime_owner_binding"] == "not_established"
    assert not any(item["event"] in {"not-imported", "returned-owner", "mutable-owner",
                                     "reassigned-owner", "wrong-owner", "type-owner", "alias-owner",
                                     "computed-owner", "nested-owner", "optional"}
                   for item in lane["items"])
    legacy_field = copy.deepcopy(atlas)
    next(row for row in legacy_field["MAIN"]["files"]["members.ts"]["symbols"]
         if row["name"] == "FieldOwner").pop("class_instance_event_call_evidence")
    old_lane = _state_focus(legacy_field, file="bus.ts", symbol="eventBus",
                            max_items=8, scan_limit=300)["event_bus_calls"]
    assert old_lane["instance_property_coverage"] == "partial_legacy_or_parser_unavailable"
    assert not any(item["event"] == "field-owner" for item in old_lane["items"])
    alias_result = _state_focus(atlas, file="alias-bus.ts", symbol="aliasedBus", max_items=8,
                                scan_limit=300)
    assert alias_result["event_bus_calls"]["status"] == "observed"
    assert "event_emitter_singleton_evidence" not in next(
        row for row in files["mutable.ts"]["symbols"] if row["name"] == "eventBus")
    assert _state_focus(atlas, file="wrong.ts", symbol="eventBus", max_items=8,
                        scan_limit=300)["event_bus_calls"]["items"] == []
    missing = copy.deepcopy(atlas)
    next(row for row in missing["MAIN"]["files"]["bus.ts"]["symbols"]
         if row["name"] == "eventBus").pop("event_emitter_singleton_evidence")
    assert _state_focus(missing, file="bus.ts", symbol="eventBus", max_items=8,
                        scan_limit=300)["event_bus_calls"]["status"] == "unavailable"
    wrong_module = copy.deepcopy(atlas)
    for caller_file in ("consumer.ts", "members.ts"):
        for entry in wrong_module["MAIN"]["files"][caller_file]["import_records"]:
            if entry.get("raw_source") == "./bus.js":
                entry["source"] = "wrong.ts"
    assert _state_focus(wrong_module, file="bus.ts", symbol="eventBus", max_items=8,
                        scan_limit=300)["event_bus_calls"]["items"] == []
    ambiguous = copy.deepcopy(atlas)
    ambiguous["MAIN"]["files"]["duplicate.ts"] = dict(files["wrong.ts"], workspace_rel="bus.ts",
                                                        symbols=[])
    assert _state_focus(ambiguous, file="bus.ts", symbol="eventBus", max_items=8,
                        scan_limit=300)["event_bus_calls"]["status"] == "ambiguous"
    malformed_call = copy.deepcopy(atlas)
    start = next(row for row in malformed_call["MAIN"]["files"]["consumer.ts"]["symbols"]
                 if row["name"] == "start")
    start["import_call_evidence"]["calls"][0]["nested_callable_depth"] = -1
    assert _state_focus(malformed_call, file="bus.ts", symbol="eventBus", max_items=8,
                        scan_limit=300)["event_bus_calls"]["status"] == "unavailable"
    malformed_member = copy.deepcopy(atlas)
    worker = next(row for row in malformed_member["MAIN"]["files"]["members.ts"]["symbols"]
                  if row["name"] == "Worker")
    worker["member_details"][0]["end_line"] = worker["end_line"] + 1
    assert _state_focus(malformed_member, file="bus.ts", symbol="eventBus", max_items=8,
                        scan_limit=300)["event_bus_calls"]["status"] == "unavailable"
    malformed_field = copy.deepcopy(atlas)
    field_caller = next(row for row in malformed_field["MAIN"]["files"]["members.ts"]["symbols"]
                        if row["name"] == "FieldOwner")
    field_caller["class_instance_event_call_evidence"]["calls"][0]["member_line"] = 0
    assert _state_focus(malformed_field, file="bus.ts", symbol="eventBus", max_items=8,
                        scan_limit=300)["event_bus_calls"]["status"] == "unavailable"
    malformed_object = copy.deepcopy(atlas)
    operations = next(row for row in malformed_object["MAIN"]["files"]["members.ts"]["symbols"]
                      if row["name"] == "operations")
    operations["initializer_member_evidence"]["members"][0]["attribution"] = "inferred_owner"
    assert _state_focus(malformed_object, file="bus.ts", symbol="eventBus", max_items=8,
                        scan_limit=300)["event_bus_calls"]["status"] == "unavailable"

    atlas["MAIN"].update(root_path=".", project_type="typescript", dependencies={})
    for rel, record in files.items():
        record.update(language="typescript", size=len(sources[rel].encode()))
    monkeypatch.setattr(artifact_store, "ROOT", tmp_path)
    raw_dir = tmp_path / ".raw"
    raw_dir.mkdir()
    store = ArtifactStore(raw_dir=raw_dir)
    store.use_sqlite = True
    store.backend = "hybrid_sqlite"
    store.db_manager = SQLiteManager(raw_dir / "codemaps.db")
    store._schema_initialized = False
    store._ensure_schema()
    store.save_raw("atlas", atlas)
    monkeypatch.setattr(server, "_raw_dir_for_target", lambda _target: raw_dir)
    monkeypatch.setattr(server, "_analysis_root_display", lambda _target="": str(tmp_path))
    def query():
        return json.loads(server.get_state_flow(file="bus.ts", symbol="eventBus",
                                               target_root=str(tmp_path), format="json"))
    current = query()
    assert all(item["endpoint_content_binding"] == "both_snapshot_and_live_match"
               for item in current["event_bus_calls"]["items"])
    (tmp_path / "consumer.ts").write_text("// changed\n", encoding="utf-8")
    assert query()["event_bus_calls"]["items"][0]["endpoint_content_binding"] == "not_currently_bound"
    assert query()["event_bus_calls"]["items"][-1]["endpoint_content_binding"] == "both_snapshot_and_live_match"
    (tmp_path / "members.ts").write_text("// changed\n", encoding="utf-8")
    assert query()["event_bus_calls"]["items"][-1]["endpoint_content_binding"] == "not_currently_bound"


def test_state_flow_instance_field_event_calls_cap_is_incomplete(tmp_path):
    import hashlib
    import shutil
    import pytest
    from tools.core.artifact_validator import _validate
    from tools.core.path_engine import resolve_project_import
    from tools.core.polyglot_imports import extract_typescript_import_evidence
    from tools.engines.generate_atlas import _normalize_polyglot_symbols

    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the real producer control")
    root = Path(__file__).resolve().parents[2]
    sources = {
        "bus.ts": 'import { EventEmitter } from "node:events";\nexport const eventBus = new EventEmitter();\n',
        "caller.ts": ('import { eventBus as bus } from "./bus";\n'
                      'export class Owner { private readonly held = bus; run() {\n'
                      + 'this.held.emit("ready");\n' * 70 + '} }\n'),
    }
    files = {}
    for rel, source in sources.items():
        path = tmp_path / rel
        path.write_text(source, encoding="utf-8", newline="")
        parsed = subprocess.run([node, str(root / "tools/engines/ast_sequencer.cjs"), str(path)],
                                cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=30)
        assert parsed.returncode == 0, parsed.stderr
        raw = json.loads(parsed.stdout)
        files[rel] = {
            "workspace_rel": rel, "hash": hashlib.sha256(source.encode()).hexdigest(),
            "symbols": _normalize_polyglot_symbols(raw, source, language="typescript"),
            "import_records": [dict(entry, raw_source=entry["source"],
                                    source=resolve_project_import(entry["source"], str(tmp_path),
                                                                  str(tmp_path), str(tmp_path)))
                               for entry in extract_typescript_import_evidence(raw)["records"]],
        }
    evidence = next(row for row in files["caller.ts"]["symbols"]
                    if row["name"] == "Owner")["class_instance_event_call_evidence"]
    assert evidence["status"] == "incomplete_scan"
    assert len(evidence["calls"]) == 64 and evidence["omitted"] == 6
    schema = json.loads((root / "config/schemas/atlas.schema.json").read_text(encoding="utf-8"))
    errors = []
    _validate(dict(evidence, calls=evidence["calls"] + [evidence["calls"][0]]),
              schema["$defs"]["class_instance_event_call_evidence"], schema,
              ["class_instance_event_call_evidence"], errors)
    assert any("at most 64 items" in error for error in errors)
    lane = _state_focus({"MAIN": {"files": files}}, file="bus.ts", symbol="eventBus",
                        max_items=3, scan_limit=300)["event_bus_calls"]
    assert lane["status"] == "incomplete_scan" and lane["omitted"] is None
    assert lane["returned"] == 3 and lane["reason"] == "instance_field_call_output_capped"


def test_state_flow_direct_class_event_emitter_field_is_syntax_only(tmp_path, monkeypatch):
    import copy
    import hashlib
    import shutil
    import subprocess
    import pytest
    from tools.engines.generate_atlas import _normalize_polyglot_symbols
    from tools.core.artifact_validator import _validate
    from tools.core import artifact_store
    from tools.core.artifact_store import ArtifactStore
    from tools.core.db import SQLiteManager

    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the real parser control")
    root = Path(__file__).resolve().parents[2]
    source = """import { EventEmitter } from "node:events";
import type { EventEmitter as TypeEmitter } from "node:events";
const Alias = EventEmitter;
export class RunEngine {
  eventBus = new EventEmitter<{ ready: [] }>();
  run() {
    this.eventBus.emit("ready");
    this.eventBus.emit?.("optional");
    this["eventBus"].emit("computed");
    const later = () => this.eventBus.emit("nested");
    return later;
  }
}
export class Reassigned {
  eventBus = new EventEmitter();
  run() { this.eventBus = new EventEmitter(); this.eventBus.emit("changed"); }
}
export class TypeOnly {
  eventBus = new TypeEmitter();
  run() { this.eventBus.emit("type-only"); }
}
export class Aliased {
  eventBus = new Alias();
  run() { this.eventBus.emit("alias"); }
}
export class Deep {
  eventBus = new EventEmitter();
  run() {
    const deeper = () => () => () => () => () => () => () => () => () => this.eventBus.emit("deep");
    return deeper;
  }
}
"""
    path = tmp_path / "engine.ts"
    path.write_text(source, encoding="utf-8", newline="")
    parsed = subprocess.run([node, str(root / "tools/engines/ast_sequencer.cjs"), str(path)],
                            cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert parsed.returncode == 0, parsed.stderr
    symbols = _normalize_polyglot_symbols(json.loads(parsed.stdout), source, language="typescript")
    run_engine = next(row for row in symbols if row["name"] == "RunEngine")
    field = next(row for row in run_engine["member_details"] if row["name"] == "eventBus")
    evidence = field["class_event_emitter_field_evidence"]
    assert evidence["status"] == "observed"
    assert evidence["module_source"] == "node:events"
    assert [(item["method"], item["first_literal_argument"], item["nested_callable_depth"])
            for item in evidence["calls"]] == [("emit", "ready", 0), ("emit", "nested", 1)]
    schema = json.loads((root / "config/schemas/atlas.schema.json").read_text(encoding="utf-8"))
    errors = []
    _validate(evidence, schema["$defs"]["class_event_emitter_field_evidence"],
              schema, ["field"], errors)
    assert errors == []
    atlas = {"MAIN": {"files": {"engine.ts": {
        "workspace_rel": "engine.ts", "hash": hashlib.sha256(source.encode()).hexdigest(),
        "symbols": symbols, "import_records": []}}}}
    result = _state_focus(atlas, file="engine.ts", symbol="RunEngine.eventBus", scan_limit=100)
    lane = result["class_field_event_calls"]
    assert result["status"] == "selected" and lane["status"] == "observed"
    assert [(item["method"], item["event"]) for item in lane["items"]] == [
        ("emit", "ready"), ("emit", "nested")]
    assert [item["callable_nesting"] for item in lane["items"]] == [
        "direct_caller_body_syntax", "lexical_arrow_callback_syntax"]
    assert lane["runtime_owner_binding"] == "not_established"
    assert lane["external_property_callers"] == "unresolved"
    forged = copy.deepcopy(atlas)
    forged_class = next(row for row in forged["MAIN"]["files"]["engine.ts"]["symbols"]
                        if row["name"] == "RunEngine")
    next(row for row in forged_class["member_details"] if row["name"] == "eventBus")[
        "class_event_emitter_field_evidence"]["calls"][0]["member_line"] = 0
    assert _state_focus(forged, file="engine.ts", symbol="RunEngine.eventBus")[
        "class_field_event_calls"]["status"] == "unavailable"
    forged_module = copy.deepcopy(atlas)
    forged_class = next(row for row in forged_module["MAIN"]["files"]["engine.ts"]["symbols"]
                        if row["name"] == "RunEngine")
    next(row for row in forged_class["member_details"] if row["name"] == "eventBus")[
        "class_event_emitter_field_evidence"]["module_source"] = "not-node-events"
    assert _state_focus(forged_module, file="engine.ts", symbol="RunEngine.eventBus")[
        "class_field_event_calls"]["status"] == "unavailable"
    assert _state_focus(atlas, file="engine.ts", symbol="RunEngine.eventBus", scan_limit=9)[
        "class_field_event_calls"]["status"] == "incomplete_scan"
    for name in ("TypeOnly", "Aliased"):
        cls = next(row for row in symbols if row["name"] == name)
        assert "class_event_emitter_field_evidence" not in next(
            row for row in cls["member_details"] if row["name"] == "eventBus")
    reassigned = next(row for row in symbols if row["name"] == "Reassigned")
    reassigned_field = next(row for row in reassigned["member_details"] if row["name"] == "eventBus")
    assert reassigned_field["class_event_emitter_field_evidence"]["status"] == "unavailable"
    assert _state_focus(atlas, file="engine.ts", symbol="Reassigned.eventBus")[
        "class_field_event_calls"]["items"] == []
    deep = next(row for row in symbols if row["name"] == "Deep")
    deep_field = next(row for row in deep["member_details"] if row["name"] == "eventBus")
    assert deep_field["class_event_emitter_field_evidence"]["status"] == "incomplete_scan"
    deep_lane = _state_focus(atlas, file="engine.ts", symbol="Deep.eventBus")[
        "class_field_event_calls"]
    assert deep_lane["status"] == "incomplete_scan"
    assert deep_lane["reason"] == "class_field_event_parser_incomplete"
    atlas["MAIN"].update(root_path=".", project_type="typescript", dependencies={})
    atlas["MAIN"]["files"]["engine.ts"].update(
        language="typescript", size=len(source.encode()))
    monkeypatch.setattr(artifact_store, "ROOT", tmp_path)
    raw_dir = tmp_path / ".raw"
    raw_dir.mkdir()
    store = ArtifactStore(raw_dir=raw_dir)
    store.use_sqlite = True
    store.backend = "hybrid_sqlite"
    store.db_manager = SQLiteManager(raw_dir / "codemaps.db")
    store._schema_initialized = False
    store._ensure_schema()
    store.save_raw("atlas", atlas)
    monkeypatch.setattr(server, "_raw_dir_for_target", lambda _target: raw_dir)
    monkeypatch.setattr(server, "_analysis_root_display", lambda _target="": str(tmp_path))
    def query():
        return json.loads(server.get_state_flow(
            file="engine.ts", symbol="RunEngine.eventBus",
            target_root=str(tmp_path), format="json"))
    current = query()["class_field_event_calls"]
    assert current["status"] == "observed"
    assert current["source_binding"] == "snapshot_and_live_match"
    path.write_text("// changed\n", encoding="utf-8")
    assert query()["class_field_event_calls"]["source_binding"] == "snapshot_match_live_unverified"


def test_state_flow_direct_class_event_field_producer_cap_is_incomplete(tmp_path):
    import hashlib
    import shutil
    import subprocess
    import pytest
    from tools.engines.generate_atlas import _normalize_polyglot_symbols

    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the real parser control")
    root = Path(__file__).resolve().parents[2]
    calls = "\n".join(f'    this.eventBus.emit("event-{index}");' for index in range(70))
    source = ('import { EventEmitter } from "node:events";\n'
              'export class Owner {\n  eventBus = new EventEmitter();\n'
              f'  run() {{\n{calls}\n  }}\n}}\n')
    path = tmp_path / "owner.ts"
    path.write_text(source, encoding="utf-8", newline="")
    parsed = subprocess.run([node, str(root / "tools/engines/ast_sequencer.cjs"), str(path)],
                            cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert parsed.returncode == 0, parsed.stderr
    symbols = _normalize_polyglot_symbols(json.loads(parsed.stdout), source, language="typescript")
    owner = next(row for row in symbols if row["name"] == "Owner")
    evidence = next(row for row in owner["member_details"] if row["name"] == "eventBus")[
        "class_event_emitter_field_evidence"]
    assert evidence["status"] == "incomplete_scan"
    assert len(evidence["calls"]) == 64 and evidence["omitted"] == 6
    atlas = {"MAIN": {"files": {"owner.ts": {
        "workspace_rel": "owner.ts", "hash": hashlib.sha256(source.encode()).hexdigest(),
        "symbols": symbols, "import_records": []}}}}
    lane = _state_focus(atlas, file="owner.ts", symbol="Owner.eventBus",
                        max_items=3, scan_limit=200)["class_field_event_calls"]
    assert lane["status"] == "incomplete_scan"
    assert lane["reason"] == "class_field_event_output_capped"
    assert lane["returned"] == 3 and lane["omitted"] is None


def test_state_flow_imported_property_event_calls_remain_value_syntax(tmp_path, monkeypatch):
    import copy
    import hashlib
    import shutil
    import subprocess
    import pytest
    from tools.engines.generate_atlas import _normalize_polyglot_symbols
    from tools.core.polyglot_imports import extract_typescript_import_evidence
    from tools.core.path_engine import resolve_project_import
    from tools.core.artifact_validator import _validate
    from tools.core import artifact_store
    from tools.core.artifact_store import ArtifactStore
    from tools.core.db import SQLiteManager

    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the real parser control")
    root = Path(__file__).resolve().parents[2]
    sources = {
        "engine.ts": 'export const engine = makeEngine();\n',
        "wrong.ts": 'export const engine = makeOther();\n',
        "consumer.ts": '''import { engine as importedEngine } from "./engine.js";
import { engine as wrongEngine } from "./wrong";
import type { engine as TypeEngine } from "./engine";
export function register() {
  importedEngine.eventBus.on("ready", () => {});
  const later = () => importedEngine.eventBus.off("ready", () => {});
  wrongEngine.eventBus.on("wrong", () => {});
  TypeEngine.eventBus.on("type-only", () => {});
  importedEngine.eventBus?.on("optional-owner", () => {});
  importedEngine.eventBus.on?.("optional-call", () => {});
  importedEngine["eventBus"].on("computed", () => {});
  importedEngine.eventBus.on(dynamicName, () => {});
  const alias = importedEngine; alias.eventBus.on("alias", () => {});
  return later;
}
export function shadow(importedEngine: { eventBus: { on(key: string): void } }) {
  importedEngine.eventBus.on("shadow");
}
''',
    }
    files = {}
    for rel, source in sources.items():
        path = tmp_path / rel
        path.write_text(source, encoding="utf-8", newline="")
        parsed = subprocess.run([node, str(root / "tools/engines/ast_sequencer.cjs"), str(path)],
                                cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=30)
        assert parsed.returncode == 0, parsed.stderr
        raw = json.loads(parsed.stdout)
        files[rel] = {
            "workspace_rel": rel, "hash": hashlib.sha256(source.encode()).hexdigest(),
            "symbols": _normalize_polyglot_symbols(raw, source, language="typescript"),
            "features": next(row for row in raw if row["name"] == "__file_meta__")["features"],
            "import_records": [dict(entry, raw_source=entry["source"],
                                    source=resolve_project_import(entry["source"], str(tmp_path),
                                                                  str(tmp_path), str(tmp_path)))
                               for entry in extract_typescript_import_evidence(raw)["records"]],
        }
    atlas = {"MAIN": {"files": files}}
    schema = json.loads((root / "config/schemas/atlas.schema.json").read_text(encoding="utf-8"))
    register = next(row for row in files["consumer.ts"]["symbols"] if row["name"] == "register")
    evidence = register["import_call_evidence"]["property_event_evidence"]
    shadow = next(row for row in files["consumer.ts"]["symbols"] if row["name"] == "shadow")
    assert "property_event_evidence" not in shadow["import_call_evidence"]
    assert "ParserEvidence:ImportedPropertyEventCallsV1" in files["consumer.ts"]["features"]
    errors = []
    _validate(evidence, schema["$defs"]["imported_property_event_call_evidence"],
              schema, ["property_event"], errors)
    assert errors == []
    result = _state_focus(atlas, file="engine.ts", symbol="engine", scan_limit=200)
    lane = result["imported_property_event_calls"]
    assert lane["status"] == "observed"
    assert [(item["method"], item["event"], item["owner_property"])
            for item in lane["items"]] == [
                ("on", "ready", "eventBus"), ("off", "ready", "eventBus")]
    assert [item["callable_nesting"] for item in lane["items"]] == [
        "direct_caller_body_syntax", "nested_callable_syntax"]
    assert all(item["runtime_owner_binding"] == "not_established" for item in lane["items"])
    assert lane["selected_export_value_origin"] == "unverified"
    wrong_module = copy.deepcopy(atlas)
    for entry in wrong_module["MAIN"]["files"]["consumer.ts"]["import_records"]:
        if entry.get("raw_source") == "./engine.js":
            entry["source"] = "wrong.ts"
    assert _state_focus(wrong_module, file="engine.ts", symbol="engine", scan_limit=200)[
        "imported_property_event_calls"]["items"] == []
    forged = copy.deepcopy(atlas)
    next(row for row in forged["MAIN"]["files"]["consumer.ts"]["symbols"]
         if row["name"] == "register")["import_call_evidence"]["property_event_evidence"][
             "calls"][0]["line"] = 0
    assert _state_focus(forged, file="engine.ts", symbol="engine", scan_limit=200)[
        "imported_property_event_calls"]["status"] == "unavailable"
    ambiguous = copy.deepcopy(atlas)
    ambiguous["MAIN"]["files"]["duplicate.ts"] = dict(files["wrong.ts"],
                                                        workspace_rel="engine.ts", symbols=[])
    assert _state_focus(ambiguous, file="engine.ts", symbol="engine", scan_limit=200)[
        "imported_property_event_calls"]["status"] == "ambiguous"
    legacy = copy.deepcopy(atlas)
    next(row for row in legacy["MAIN"]["files"]["consumer.ts"]["symbols"]
         if row["name"] == "register")["import_call_evidence"].pop("property_event_evidence")
    legacy["MAIN"]["files"]["consumer.ts"]["features"] = []
    assert _state_focus(legacy, file="engine.ts", symbol="engine", scan_limit=200)[
        "imported_property_event_calls"]["status"] == "incomplete_scan"
    atlas["MAIN"].update(root_path=".", project_type="typescript", dependencies={})
    for rel, source in sources.items():
        files[rel].update(language="typescript", size=len(source.encode()))
    monkeypatch.setattr(artifact_store, "ROOT", tmp_path)
    raw_dir = tmp_path / ".raw"
    raw_dir.mkdir()
    store = ArtifactStore(raw_dir=raw_dir)
    store.use_sqlite = True
    store.backend = "hybrid_sqlite"
    store.db_manager = SQLiteManager(raw_dir / "codemaps.db")
    store._schema_initialized = False
    store._ensure_schema()
    store.save_raw("atlas", atlas)
    monkeypatch.setattr(server, "_raw_dir_for_target", lambda _target: raw_dir)
    monkeypatch.setattr(server, "_analysis_root_display", lambda _target="": str(tmp_path))
    def query():
        return json.loads(server.get_state_flow(file="engine.ts", symbol="engine",
                                                target_root=str(tmp_path), format="json"))
    current = query()["imported_property_event_calls"]
    assert current["source_binding"] == "snapshot_and_live_match"
    assert all(item["endpoint_content_binding"] == "both_snapshot_and_live_match"
               for item in current["items"])
    (tmp_path / "consumer.ts").write_text("// changed\n", encoding="utf-8")
    assert all(item["endpoint_content_binding"] == "not_currently_bound"
               for item in query()["imported_property_event_calls"]["items"])
    (tmp_path / "consumer.ts").write_text(sources["consumer.ts"], encoding="utf-8", newline="")
    (tmp_path / "engine.ts").write_text("// changed\n", encoding="utf-8")
    selected_drift = query()
    assert selected_drift["source_binding"] != "snapshot_and_live_match"
    assert all(item["endpoint_content_binding"] == "not_currently_bound"
               for item in selected_drift["imported_property_event_calls"]["items"])


def test_state_flow_imported_property_event_call_cap_is_incomplete(tmp_path):
    import hashlib
    import shutil
    import subprocess
    import pytest
    from tools.engines.generate_atlas import _normalize_polyglot_symbols
    from tools.core.polyglot_imports import extract_typescript_import_evidence
    from tools.core.path_engine import resolve_project_import

    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the real parser control")
    root = Path(__file__).resolve().parents[2]
    sources = {
        "engine.ts": 'export const engine = makeEngine();\n',
        "consumer.ts": ('import { engine } from "./engine";\n'
                        'export function register() {\n'
                        + '\n'.join(f'engine.eventBus.on("event-{index}", () => {{}});'
                                    for index in range(70)) + '\n}\n'),
    }
    files = {}
    for rel, source in sources.items():
        path = tmp_path / rel
        path.write_text(source, encoding="utf-8", newline="")
        parsed = subprocess.run([node, str(root / "tools/engines/ast_sequencer.cjs"), str(path)],
                                cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=30)
        assert parsed.returncode == 0, parsed.stderr
        raw = json.loads(parsed.stdout)
        files[rel] = {
            "workspace_rel": rel, "hash": hashlib.sha256(source.encode()).hexdigest(),
            "features": next(row for row in raw if row["name"] == "__file_meta__")["features"],
            "symbols": _normalize_polyglot_symbols(raw, source, language="typescript"),
            "import_records": [dict(entry, raw_source=entry["source"],
                                    source=resolve_project_import(entry["source"], str(tmp_path),
                                                                  str(tmp_path), str(tmp_path)))
                               for entry in extract_typescript_import_evidence(raw)["records"]],
        }
    register = next(row for row in files["consumer.ts"]["symbols"] if row["name"] == "register")
    evidence = register["import_call_evidence"]["property_event_evidence"]
    assert evidence["status"] == "incomplete_scan"
    assert len(evidence["calls"]) == 64 and evidence["omitted"] == 6
    lane = _state_focus({"MAIN": {"files": files}}, file="engine.ts", symbol="engine",
                        max_items=3, scan_limit=300)["imported_property_event_calls"]
    assert lane["status"] == "incomplete_scan" and lane["omitted"] is None
    assert lane["returned"] == 3 and lane["reason"] == "property_event_parser_incomplete"


def test_state_flow_factory_construction_stays_source_only(tmp_path, monkeypatch):
    import copy
    import hashlib
    import shutil
    import pytest
    from tools.engines.generate_atlas import _normalize_polyglot_symbols
    from tools.core.polyglot_imports import extract_typescript_import_evidence
    from tools.core.artifact_validator import _validate
    from tools.core import artifact_store
    from tools.core.artifact_store import ArtifactStore
    from tools.core.db import SQLiteManager

    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the real parser control")
    source = '''import { RunEngine } from "@internal/run-engine";
import type { RunEngine as TypeEngine } from "@types/run-engine";
export const engine = singleton("RunEngine", createRunEngine);
function createRunEngine() {
  const value = new RunEngine({});
  return value;
}
engine.eventBus = replacement;
export const direct = createDirect();
function createDirect() { return new RunEngine({}); }
export const conditional = singleton("x", createConditional);
function createConditional() {
  if (flag) return new RunEngine({});
  return new RunEngine({});
}
export const shadowed = singleton("x", createShadowed);
function createShadowed(RunEngine) { return new RunEngine({}); }
export const typeOnly = singleton("x", createTypeOnly);
function createTypeOnly() { return new TypeEngine({}); }
export const indirect = singleton("x", createIndirect);
function createIndirect() { return makeEngine(); }
'''
    target = tmp_path / "engine.ts"
    target.write_text(source, encoding="utf-8", newline="")
    root = Path(__file__).resolve().parents[2]
    parsed = subprocess.run([node, str(root / "tools/engines/ast_sequencer.cjs"), str(target)],
                            cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert parsed.returncode == 0, parsed.stderr
    raw = json.loads(parsed.stdout)
    symbols = _normalize_polyglot_symbols(raw, source, language="typescript")
    rows = {row["name"]: row for row in symbols}
    candidate = rows["engine"]["factory_source_evidence"]
    schema = json.loads((root / "config/schemas/atlas.schema.json").read_text(encoding="utf-8"))
    errors = []
    _validate(candidate, schema["$defs"]["factory_source_evidence"], schema,
              ["factory_source"], errors)
    assert errors == []
    assert candidate["initializer_form"] == "factory_passed_as_argument"
    assert candidate["factory"] == "createRunEngine"
    assert candidate["constructed_imported_name"] == "RunEngine"
    assert candidate["constructor_module_source"] == "@internal/run-engine"
    assert candidate["return_form"] == "direct_const_new_return"
    assert candidate["factory_execution"] == "not_established"
    assert candidate["package_condition_resolution"] == "not_evaluated"
    assert candidate["public_field_mutation"] == "not_checked"
    assert rows["direct"]["factory_source_evidence"]["initializer_form"] == "direct_factory_call"
    assert rows["direct"]["factory_source_evidence"]["return_form"] == "direct_new_return"
    assert all("factory_source_evidence" not in rows[name] for name in (
        "conditional", "shadowed", "typeOnly", "indirect"))

    atlas = {"MAIN": {"files": {"engine.ts": {
        "workspace_rel": "engine.ts", "hash": hashlib.sha256(source.encode()).hexdigest(),
        "language": "typescript", "size": len(source.encode()), "symbols": symbols,
        "features": next(row for row in raw if row["name"] == "__file_meta__")["features"],
        "import_records": [dict(entry, raw_source=entry["source"])
                           for entry in extract_typescript_import_evidence(raw)["records"]],
    }}, "root_path": ".", "project_type": "typescript", "dependencies": {}}}
    focus = _state_focus(atlas, file="engine.ts", symbol="engine")
    assert focus["factory_source_candidate"]["status"] == "syntax_candidate"
    assert focus["factory_source_candidate"]["runtime_instance_identity"] == "not_established"
    assert focus["factory_source_candidate"]["package_condition_resolution"] == "not_evaluated"
    forged = copy.deepcopy(atlas)
    next(row for row in forged["MAIN"]["files"]["engine.ts"]["symbols"]
         if row["name"] == "engine")["factory_source_evidence"][
        "constructor_module_source"] = "@wrong/run-engine"
    assert _state_focus(forged, file="engine.ts", symbol="engine")[
        "factory_source_candidate"]["status"] == "unavailable"
    missing_import = copy.deepcopy(atlas)
    missing_import["MAIN"]["files"]["engine.ts"]["import_records"] = []
    assert _state_focus(missing_import, file="engine.ts", symbol="engine")[
        "factory_source_candidate"]["status"] == "unavailable"
    duplicate_import = copy.deepcopy(atlas)
    duplicate_import["MAIN"]["files"]["engine.ts"]["import_records"].append(
        duplicate_import["MAIN"]["files"]["engine.ts"]["import_records"][0].copy())
    assert _state_focus(duplicate_import, file="engine.ts", symbol="engine")[
        "factory_source_candidate"]["status"] == "ambiguous"
    assert _state_focus(atlas, file="engine.ts", symbol="conditional")[
        "factory_source_candidate"]["status"] == "unavailable"

    monkeypatch.setattr(artifact_store, "ROOT", tmp_path)
    raw_dir = tmp_path / ".raw"
    raw_dir.mkdir()
    store = ArtifactStore(raw_dir=raw_dir)
    store.use_sqlite = True
    store.backend = "hybrid_sqlite"
    store.db_manager = SQLiteManager(raw_dir / "codemaps.db")
    store._schema_initialized = False
    store._ensure_schema()
    store.save_raw("atlas", atlas)
    monkeypatch.setattr(server, "_raw_dir_for_target", lambda _target: raw_dir)
    monkeypatch.setattr(server, "_analysis_root_display", lambda _target="": str(tmp_path))
    def query():
        return json.loads(server.get_state_flow(file="engine.ts", symbol="engine",
                                                target_root=str(tmp_path), format="json"))
    assert query()["source_binding"] == "snapshot_and_live_match"
    assert query()["factory_source_candidate"]["status"] == "syntax_candidate"
    target.write_text("// changed\n", encoding="utf-8")
    assert query()["source_binding"] != "snapshot_and_live_match"


def test_state_flow_persist_storage_import_is_source_candidate_only(monkeypatch, tmp_path):
    import copy
    import hashlib
    import shutil
    import pytest
    from tools.core import artifact_store
    from tools.core.artifact_store import ArtifactStore
    from tools.core.artifact_validator import _validate
    from tools.core.db import SQLiteManager
    from tools.core.path_engine import resolve_project_import
    from tools.core.polyglot_imports import extract_typescript_import_evidence
    from tools.engines.generate_atlas import _normalize_polyglot_symbols

    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the real producer control")
    root = Path(__file__).resolve().parents[2]
    source = '''import { create as makeStore } from "zustand";
import { persist as persistMw } from "zustand/middleware";
import { persist as fakePersist } from "./fake-middleware";
import { onboardingIndexedDBStorage } from "./onboarding-storage";
import type { typeOnlyStorage } from "./onboarding-storage";
export const selected = makeStore<State>()(persistMw((set) => ({ update: () => set({ ready: true }) }),
  { name: 'onboarding', storage: onboardingIndexedDBStorage, skipHydration: true } as PersistOptions<State>));
export const dynamic = makeStore(persistMw((set) => ({ update: () => set({ ready: true }) }),
  { name: 'onboarding', storage: chooseStorage(), skipHydration: chooseHydration() }));
export const spread = makeStore(persistMw((set) => ({ update: () => set({ ready: true }) }),
  { name: 'onboarding', storage: onboardingIndexedDBStorage, skipHydration: true, ...overrides }));
export const typeOnly = makeStore(persistMw((set) => ({ update: () => set({ ready: true }) }),
  { name: 'onboarding', storage: typeOnlyStorage }));
export const literalFalse = makeStore(persistMw((set) => ({ update: () => set({ ready: true }) }),
  { name: 'onboarding', skipHydration: false }));
export const duplicateHydration = makeStore(persistMw((set) => ({ update: () => set({ ready: true }) }),
  { name: 'onboarding', skipHydration: true, skipHydration: false }));
export const fakeHydration = makeStore(fakePersist((set) => ({ update: () => set({ ready: true }) }),
  { name: 'onboarding', skipHydration: true }));
export const multiple = wrapper(
  makeStore(persistMw((set) => ({ first: () => set({ ready: true }) }),
    { name: 'first', storage: onboardingIndexedDBStorage, skipHydration: true })),
  makeStore(persistMw((set) => ({ second: () => set({ ready: true }) }),
    { name: 'second', storage: onboardingIndexedDBStorage, skipHydration: false })));
export const mixed = wrapper(
  makeStore(persistMw((set) => ({ persisted: () => set({ ready: true }) }),
    { name: 'persisted', storage: onboardingIndexedDBStorage, skipHydration: true })),
  makeStore((set) => ({ plain: () => set({ ready: true }) })));
'''
    storage_source = "export const onboardingIndexedDBStorage = { getItem() {}, setItem() {}, removeItem() {} };\n"
    store_path = tmp_path / "store.ts"
    storage_path = tmp_path / "onboarding-storage.ts"
    store_path.write_text(source, encoding="utf-8", newline="")
    storage_path.write_text(storage_source, encoding="utf-8", newline="")
    files = {}
    for rel, content in (("store.ts", source), ("onboarding-storage.ts", storage_source)):
        parsed = subprocess.run([node, str(root / "tools/engines/ast_sequencer.cjs"), str(tmp_path / rel)],
                                cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=30)
        assert parsed.returncode == 0, parsed.stderr
        raw = json.loads(parsed.stdout)
        imports = extract_typescript_import_evidence(raw)
        files[rel] = {
            "workspace_rel": rel, "hash": hashlib.sha256(content.encode()).hexdigest(),
            "language": "typescript", "size": len(content.encode()),
            "symbols": _normalize_polyglot_symbols(raw, content, language="typescript"),
            "import_records": [dict(entry, raw_source=entry["source"], source=resolve_project_import(
                entry["source"], str(tmp_path), str(tmp_path), str(tmp_path)))
                for entry in imports["records"]],
        }
    atlas = {"MAIN": {"root_path": ".", "project_type": "typescript",
                       "dependencies": {}, "files": files}}
    schema = json.loads((root / "config/schemas/atlas.schema.json").read_text(encoding="utf-8"))
    evidence_schema = schema["additionalProperties"]["properties"]["files"]["additionalProperties"]["properties"]["symbols"]["items"]["properties"]["initializer_member_evidence"]
    selected_row = next(row for row in files["store.ts"]["symbols"] if row["name"] == "selected")
    parser_evidence = selected_row["initializer_member_evidence"]
    errors = []
    _validate(parser_evidence, evidence_schema, schema, ["initializer"], errors)
    assert not errors
    selected_member = next(row for row in parser_evidence["members"] if row["name"] == "update")
    assert selected_member["persist_storage_option_import_evidence"]["status"] == "observed"
    assert selected_member["persist_hydration_option_evidence"]["skip_hydration"] is True
    multiple_row = next(row for row in files["store.ts"]["symbols"] if row["name"] == "multiple")
    multiple_members = multiple_row["initializer_member_evidence"]["members"]
    assert all(row["persist_storage_option_import_evidence"]["status"] == "observed"
               for row in multiple_members)
    assert next(row for row in multiple_members if row["name"] == "first")[
        "persist_hydration_option_evidence"]["skip_hydration"] is True
    assert next(row for row in multiple_members if row["name"] == "second")[
        "persist_hydration_option_evidence"]["skip_hydration"] is False
    malformed = copy.deepcopy(parser_evidence)
    malformed["members"][0]["persist_storage_option_import_evidence"]["runtime_execution"] = "established"
    errors = []
    _validate(malformed, evidence_schema, schema, ["initializer"], errors)
    assert errors
    malformed_hydration = copy.deepcopy(parser_evidence)
    malformed_hydration["members"][0]["persist_hydration_option_evidence"]["skip_hydration"] = "true"
    errors = []
    _validate(malformed_hydration, evidence_schema, schema, ["initializer"], errors)
    assert errors
    positive = _state_focus(atlas, file="store.ts", symbol="selected.update", scan_limit=1000)
    hydration = positive["persist_hydration_candidate"]
    assert hydration["status"] == "source_candidate"
    assert hydration["skip_hydration"] is True
    assert hydration["runtime_execution"] == "not_established"
    assert hydration["source_binding"] == "not_checked"
    candidate = positive["persist_storage_candidate"]
    assert candidate["status"] == "target_candidate"
    assert candidate["target"]["atlas_ref"] == "MAIN::onboarding-storage.ts"
    assert candidate["runtime_execution"] == "not_established"
    export = candidate["export_candidate"]
    assert export["status"] == "target_candidate"
    assert export["target"]["symbol"] == "onboardingIndexedDBStorage"
    assert export["target"]["start_line"] == 1
    assert export["runtime_execution"] == "not_established"
    assert _state_focus(atlas, file="store.ts", symbol="literalFalse.update", scan_limit=1000)[
        "persist_hydration_candidate"]["skip_hydration"] is False
    for owner, reason in (("dynamic", "skip_hydration_value_not_literal"),
                          ("spread", "spread_or_computed_option_not_resolved"),
                          ("typeOnly", "skip_hydration_option_absent"),
                          ("duplicateHydration", "duplicate_skip_hydration_option")):
        negative = _state_focus(atlas, file="store.ts", symbol=owner + ".update", scan_limit=1000)
        assert "persist_hydration_candidate" in negative, (owner, negative.get("status"))
        assert negative["persist_hydration_candidate"]["status"] == "unavailable"
        assert negative["persist_hydration_candidate"]["reason"] == reason
    assert _state_focus(atlas, file="store.ts", symbol="multiple.first", scan_limit=1000)[
        "persist_hydration_candidate"]["skip_hydration"] is True
    assert _state_focus(atlas, file="store.ts", symbol="multiple.second", scan_limit=1000)[
        "persist_hydration_candidate"]["skip_hydration"] is False
    for symbol in ("multiple.first", "multiple.second", "mixed.persisted"):
        assert _state_focus(atlas, file="store.ts", symbol=symbol, scan_limit=1000)[
            "persist_storage_candidate"]["status"] == "target_candidate"
    assert _state_focus(atlas, file="store.ts", symbol="mixed.persisted", scan_limit=1000)[
        "persist_hydration_candidate"]["skip_hydration"] is True
    assert _state_focus(atlas, file="store.ts", symbol="mixed.plain", scan_limit=1000)[
        "persist_hydration_candidate"]["reason"] == "missing_parser_hydration_option_evidence"
    assert _state_focus(atlas, file="store.ts", symbol="mixed.plain", scan_limit=1000)[
        "persist_storage_candidate"]["reason"] == "missing_parser_storage_option_evidence"
    fake = _state_focus(atlas, file="store.ts", symbol="fakeHydration.update", scan_limit=1000)
    assert fake["status"] == "selected"
    assert fake["persist_hydration_candidate"]["reason"] == "missing_parser_hydration_option_evidence"
    for owner, reason in (("dynamic", "storage_option_not_identifier"),
                          ("spread", "spread_or_computed_option_not_resolved"),
                          ("typeOnly", "storage_import_binding_unverified")):
        negative = _state_focus(atlas, file="store.ts", symbol=owner + ".update", scan_limit=1000)
        assert negative["persist_storage_candidate"]["status"] == "unavailable"
        assert negative["persist_storage_candidate"]["reason"] == reason
    ambiguous = copy.deepcopy(atlas)
    storage_import = next(row for row in ambiguous["MAIN"]["files"]["store.ts"]["import_records"]
                          if row["raw_source"] == "./onboarding-storage" and row["name"] == "onboardingIndexedDBStorage")
    ambiguous["MAIN"]["files"]["store.ts"]["import_records"].append(dict(storage_import))
    assert _state_focus(ambiguous, file="store.ts", symbol="selected.update", scan_limit=1000)[
        "persist_storage_candidate"]["reason"] == "ambiguous_module_import"
    missing_target = copy.deepcopy(atlas)
    del missing_target["MAIN"]["files"]["onboarding-storage.ts"]
    assert _state_focus(missing_target, file="store.ts", symbol="selected.update", scan_limit=1000)[
        "persist_storage_candidate"]["reason"] == "no_exact_same_project_target"
    missing_export = copy.deepcopy(atlas)
    missing_export["MAIN"]["files"]["onboarding-storage.ts"]["symbols"] = []
    assert _state_focus(missing_export, file="store.ts", symbol="selected.update", scan_limit=1000)[
        "persist_storage_candidate"]["export_candidate"]["reason"] == "target_export_not_indexed"
    reexport = copy.deepcopy(atlas)
    export_row = reexport["MAIN"]["files"]["onboarding-storage.ts"]["symbols"][0]
    export_row.update(type="ReExportedSymbol", export_kind="reexport")
    assert _state_focus(reexport, file="store.ts", symbol="selected.update", scan_limit=1000)[
        "persist_storage_candidate"]["export_candidate"]["reason"] == "reexport_or_alias_unresolved"
    duplicate_export = copy.deepcopy(atlas)
    rows = duplicate_export["MAIN"]["files"]["onboarding-storage.ts"]["symbols"]
    rows.append(dict(rows[0]))
    assert _state_focus(duplicate_export, file="store.ts", symbol="selected.update", scan_limit=1000)[
        "persist_storage_candidate"]["export_candidate"]["status"] == "ambiguous"
    from tools.core.state_flow import _focus_storage_direct_export
    assert _focus_storage_direct_export(candidate, atlas["MAIN"]["files"], "MAIN", 0)[
        "status"] == "incomplete_scan"
    from tools.core.state_flow import _focus_persist_hydration_option
    assert _focus_persist_hydration_option(selected_member, selected_row, 0)[
        "reason"] == "hydration_option_scan_budget_exhausted"
    legacy = copy.deepcopy(atlas)
    next(row for row in legacy["MAIN"]["files"]["store.ts"]["symbols"]
         if row["name"] == "selected")["initializer_member_evidence"]["members"][0].pop(
             "persist_storage_option_import_evidence")
    assert _state_focus(legacy, file="store.ts", symbol="selected.update", scan_limit=1000)[
        "persist_storage_candidate"]["reason"] == "missing_parser_storage_option_evidence"
    next(row for row in legacy["MAIN"]["files"]["store.ts"]["symbols"]
         if row["name"] == "selected")["initializer_member_evidence"]["members"][0].pop(
             "persist_hydration_option_evidence")
    assert _state_focus(legacy, file="store.ts", symbol="selected.update", scan_limit=1000)[
        "persist_hydration_candidate"]["reason"] == "missing_parser_hydration_option_evidence"

    monkeypatch.setattr(artifact_store, "ROOT", tmp_path)
    raw_dir = tmp_path / ".raw"
    raw_dir.mkdir()
    store = ArtifactStore(raw_dir=raw_dir)
    store.use_sqlite = True
    store.backend = "hybrid_sqlite"
    store.db_manager = SQLiteManager(raw_dir / "codemaps.db")
    store._schema_initialized = False
    store._ensure_schema()
    store.save_raw("atlas", atlas)
    monkeypatch.setattr(server, "_raw_dir_for_target", lambda _target: raw_dir)
    monkeypatch.setattr(server, "_analysis_root_display", lambda _target="": str(tmp_path))
    def query():
        return json.loads(server.get_state_flow(file="store.ts", symbol="selected.update",
                                               target_root=str(tmp_path), format="json"))
    current = query()["persist_storage_candidate"]
    assert current["endpoint_content_binding"] == "both_snapshot_and_live_match"
    assert current["export_candidate"]["endpoint_content_binding"] == "both_snapshot_and_live_match"
    current_hydration = query()["persist_hydration_candidate"]
    assert current_hydration["status"] == "source_candidate"
    assert current_hydration["skip_hydration"] is True
    assert current_hydration["source_binding"] == "snapshot_and_live_match"
    brief = server.get_state_flow(file="store.ts", symbol="selected.update",
                                  target_root=str(tmp_path), format="brief")
    assert "persist_storage_candidate" in brief and "persist_hydration_candidate" in brief
    assert "not_established" in brief
    storage_path.write_text("// changed\n", encoding="utf-8")
    drifted = query()["persist_storage_candidate"]
    assert drifted["target_source_binding"] == "snapshot_match_live_unverified"
    assert drifted["endpoint_content_binding"] == "not_currently_bound"
    assert drifted["export_candidate"]["endpoint_content_binding"] == "not_currently_bound"
    assert query()["persist_hydration_candidate"]["source_binding"] == "snapshot_and_live_match"
    store_path.write_text("// changed\n", encoding="utf-8")
    stale_hydration = query()["persist_hydration_candidate"]
    assert stale_hydration["status"] == "unavailable"
    assert stale_hydration["reason"] == "selected_source_not_currently_bound"
    assert "skip_hydration" not in stale_hydration
