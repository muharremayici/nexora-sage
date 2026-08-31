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
