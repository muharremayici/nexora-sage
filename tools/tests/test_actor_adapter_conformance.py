from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from tools.mcp import server
from tools.validate_mcp_agent_surface import _mcp_tool_metadata


def _request(*, operation: str = "inspect", target_scope: dict | None = None) -> dict:
    return {
        "request_id": "req-17",
        "principal_id": "organization:nexora",
        "actor_id": "agent:test",
        "actor_type": "machine_reasoning_actor",
        "adapter_type": "mcp",
        "purpose": "Inspect current repository debt",
        "operation": operation,
        "target_scope": target_scope or {"repository": "analyzed_repository", "projects": ["MAIN"]},
        "requested_mode": operation,
    }


def test_stale_artifact_trust_blocks_target_queue_before_items_are_read() -> None:
    stale = {
        "status": "FAIL",
        "scope": "external_target",
        "freshness": {"audit": "stale"},
        "failures": [{"name": "freshness:audit_not_older_than_atlas"}],
        "warnings": [],
    }
    with (
        patch.object(server, "_raw_dir_for_target", return_value=server.RAW_DIR),
        patch.object(server, "_ensure_agent_artifact_chain_current", return_value=stale),
        patch.object(server, "_audit_violation_work_items_from_sqlite") as queue_reader,
        patch.object(server, "_record_mcp_call_result", side_effect=lambda _name, _started, result, **_kwargs: result),
    ):
        payload = json.loads(server.get_violation_work_queue(target_root="C:/isolated", format="json"))

    assert payload["status"] == "INVALID_CONTEXT"
    assert payload["blocking"] is True
    assert payload["artifact_trust"]["status"] == "FAIL"
    assert "rerun analysis" in payload["required_action"]
    queue_reader.assert_not_called()


def test_current_artifact_trust_does_not_block_actor_context() -> None:
    assert server._artifact_trust_blocks_actor_context({"status": "PASS"}) is False
    assert server._artifact_trust_blocks_actor_context({"status": "WARN"}) is True
    assert server._artifact_trust_blocks_actor_context({}) is True


def test_clean_work_queue_json_uses_shared_no_action_projection() -> None:
    with (
        patch.object(server, "_raw_dir_for_target", return_value=server.RAW_DIR),
        patch.object(server, "_ensure_agent_artifact_chain_current", return_value={"status": "PASS"}),
        patch.object(server, "_audit_queue_trust_projection", return_value={"status": "PASS"}),
        patch.object(server, "_audit_violation_work_items_from_sqlite", return_value=([], 0, True)),
        patch.object(server, "_analysis_snapshot_id", return_value="snapshot-18"),
        patch.object(server, "_record_mcp_call_result", side_effect=lambda _name, _started, result, **_kwargs: result),
    ):
        payload = json.loads(server.get_violation_work_queue(format="json"))

    assert payload["authority_projection"]["actionability"] == "no_action"
    assert payload["authority_projection"]["mutation_proposed"] is False
    assert payload["authority_projection"]["mutation_authority"] == "not_granted_by_this_directive"
    assert payload["authority_projection"]["approval_is_not_mutation_authority"] is True
    assert payload["status"] == "clean_within_sage_audit"
    assert payload["coverage"] == {
        "status": "partial",
        "sage_audit": "evaluated",
        "target_native": "not_evaluated",
        "combined_verdict": "not_available",
        "clean_scope": "sage_audit_only",
        "meaning": (
            "This queue contains SAGE Audit findings only. It does not establish compliance "
            "with target-native lint, compiler, test, policy, security or runtime authorities."
        ),
    }


def test_mcp_inventory_discovers_async_tools() -> None:
    metadata = _mcp_tool_metadata()
    assert "dispatch_actor_request" in metadata
    assert {"request", "tool_name", "tool_arguments"}.issubset(metadata["dispatch_actor_request"]["parameters"])


def test_profile_authority_denial_routes_failure_trace() -> None:
    instance = server.ProfiledFastMCP("actor-conformance-authority")
    instance.set_visible_tools("target_repository_default", {"allowed_tool"})
    with patch.object(instance, "_record_governance_trace") as trace_recorder:
        with pytest.raises(ValueError, match="unavailable in profile"):
            asyncio.run(instance.call_tool("sage_internal_tool", {}))

    assert trace_recorder.call_args.args[4:6] == ("failure", "tool_visibility")


def test_mcp_preserves_allowed_tool_result_and_routes_success_trace() -> None:
    instance = server.ProfiledFastMCP("actor-conformance-result")

    @instance.tool(name="echo_validation")
    def echo_validation(status: str, evidence_id: str) -> dict[str, str]:
        return {"status": status, "evidence_id": evidence_id}

    instance.set_visible_tools("target_repository_default", {"echo_validation"})
    with patch.object(instance, "_record_governance_trace") as trace_recorder:
        result = asyncio.run(instance.call_tool("echo_validation", {"status": "BLOCKED", "evidence_id": "ev-17"}))

    content, structured = result
    rendered = "".join(str(getattr(item, "text", "")) for item in content)
    assert "BLOCKED" in rendered
    assert "ev-17" in rendered
    assert structured == {"status": "BLOCKED", "evidence_id": "ev-17"}
    assert trace_recorder.call_args.args[4:6] == ("success", "none")


def test_actor_gateway_rejects_missing_purpose_without_dispatch() -> None:
    request = _request()
    request["purpose"] = ""
    dispatch = AsyncMock()
    with (
        patch.object(server.mcp, "active_tool_profile", "target_repository_default", create=True),
        patch.object(server.mcp, "_visible_tool_names", frozenset({"get_violation_work_queue"}), create=True),
        patch.object(server.mcp._tool_manager, "call_tool", dispatch),
    ):
        payload = json.loads(asyncio.run(server.dispatch_actor_request(request, "get_violation_work_queue", {})))

    assert payload["status"] == "BLOCKED"
    assert payload["failure_code"] == "missing_required_fields"
    dispatch.assert_not_awaited()


def test_actor_gateway_rejects_scope_smuggling() -> None:
    request = _request(operation="validate", target_scope={"repository": "analyzed_repository", "files": ["src/Alpha.tsx"]})
    dispatch = AsyncMock()
    with (
        patch.object(server.mcp, "active_tool_profile", "target_repository_default", create=True),
        patch.object(server.mcp, "_visible_tool_names", frozenset({"validate_patch"}), create=True),
        patch.object(server.mcp._tool_manager, "call_tool", dispatch),
    ):
        payload = json.loads(
            asyncio.run(
                server.dispatch_actor_request(
                    request,
                    "validate_patch",
                    {"target_file": "src/Unrelated.tsx", "patch_content": "+change"},
                )
            )
        )

    assert payload["status"] == "BLOCKED"
    assert payload["failure_code"] == "scope_argument_mismatch"
    dispatch.assert_not_awaited()


def test_actor_gateway_rejects_external_root_outside_declared_repository_scope() -> None:
    request = _request(target_scope={"repository": "C:/targets/declared", "projects": ["MAIN"]})
    dispatch = AsyncMock()
    with (
        patch.object(server.mcp, "active_tool_profile", "target_repository_default", create=True),
        patch.object(server.mcp, "_visible_tool_names", frozenset({"get_violation_work_queue"}), create=True),
        patch.object(server.mcp._tool_manager, "call_tool", dispatch),
    ):
        payload = json.loads(
            asyncio.run(
                server.dispatch_actor_request(
                    request,
                    "get_violation_work_queue",
                    {"target_root": "C:/targets/undeclared"},
                )
            )
        )

    assert payload["status"] == "BLOCKED"
    assert payload["failure_code"] == "scope_argument_mismatch"
    dispatch.assert_not_awaited()


def test_actor_gateway_binds_effective_project_default_to_declared_scope() -> None:
    request = _request(target_scope={"repository": "analyzed_repository", "projects": ["VARIATION"]})
    dispatch = AsyncMock()
    with (
        patch.object(server.mcp, "active_tool_profile", "target_repository_default", create=True),
        patch.object(server.mcp, "_visible_tool_names", frozenset({"get_violation_work_queue"}), create=True),
        patch.object(server.mcp._tool_manager, "call_tool", dispatch),
    ):
        payload = json.loads(asyncio.run(server.dispatch_actor_request(request, "get_violation_work_queue", {})))

    assert payload["status"] == "BLOCKED"
    assert payload["failure_code"] == "scope_argument_mismatch"
    assert payload["details"]["arguments"] == ["project"]
    dispatch.assert_not_awaited()


def test_actor_gateway_preserves_stale_result_and_active_trace() -> None:
    stale_result = json.dumps({"status": "INVALID_CONTEXT", "blocking": True, "evidence_id": "freshness-3"})
    with (
        patch.object(server.mcp, "active_tool_profile", "target_repository_default", create=True),
        patch.object(server.mcp, "_visible_tool_names", frozenset({"get_violation_work_queue"}), create=True),
        patch.object(server.mcp._tool_manager, "call_tool", AsyncMock(return_value=stale_result)),
        patch("tools.core.governance_trace.current_trace_id", return_value="sage-trace-envelope"),
    ):
        payload = json.loads(asyncio.run(server.dispatch_actor_request(_request(), "get_violation_work_queue", {})))

    assert payload["request_id"] == "req-17"
    assert payload["status"] == "INVALID_CONTEXT"
    assert payload["interaction_state"] == "INVALID_CONTEXT"
    assert payload["completed_states"] == ["REQUESTED"]
    assert payload["trace_id"] == "sage-trace-envelope"
    assert payload["tool_result"] == stale_result
    assert len(payload["request_fingerprint"]) == 64


def test_actor_gateway_maps_validation_failure_without_losing_raw_result() -> None:
    failed_result = json.dumps({"status": "FAIL", "violations": [{"rule": "layer_purity"}]})
    dispatch = AsyncMock(return_value=failed_result)
    request = _request(operation="validate", target_scope={"repository": "analyzed_repository", "files": ["src/Alpha.tsx"]})
    with (
        patch.object(server.mcp, "active_tool_profile", "target_repository_default", create=True),
        patch.object(server.mcp, "_visible_tool_names", frozenset({"validate_patch"}), create=True),
        patch.object(server.mcp._tool_manager, "call_tool", dispatch),
    ):
        payload = json.loads(
            asyncio.run(
                server.dispatch_actor_request(
                    request,
                    "validate_patch",
                    {"target_file": "src/Alpha.tsx", "patch_content": "+change"},
                )
            )
        )

    assert payload["status"] == "BLOCKED"
    assert payload["interaction_state"] == "BLOCKED"
    assert payload["completed_states"][-1] == "VALIDATED"
    assert payload["tool_result"] == failed_result
    assert dispatch.await_args.args[1]["format"] == "json"


def test_actor_gateway_dispatches_snapshot_bound_advice_with_canonical_terminal_state() -> None:
    result = json.dumps({"status": "ACCEPTED", "input_evidence": {"status": "PASS"}})
    dispatch = AsyncMock(return_value=result)
    request = _request(operation="advise")
    with (
        patch.object(server.mcp, "active_tool_profile", "target_repository_default", create=True),
        patch.object(server.mcp, "_visible_tool_names", frozenset({"get_surgical_operation_packet"}), create=True),
        patch.object(server.mcp._tool_manager, "call_tool", dispatch),
    ):
        payload = json.loads(asyncio.run(server.dispatch_actor_request(request, "get_surgical_operation_packet", {})))

    assert payload["status"] == "ACCEPTED"
    assert payload["completed_states"][-1] == "PROPOSED"
    assert payload["tool_result"] == result
    assert dispatch.await_args.args[1]["format"] == "json"


def test_actor_gateway_dispatches_proposal_conformance_without_authority() -> None:
    result = json.dumps(
        {
            "status": "PROPOSAL_CONFORMANT",
            "authorization_granted": False,
            "validation_completed": False,
        }
    )
    dispatch = AsyncMock(return_value=result)
    request = _request(
        operation="propose",
        target_scope={"repository": "analyzed_repository", "files": ["src/validation/a.ts"]},
    )
    request.update(
        {
            "repository_snapshot": "commit-abc123",
            "constraints": ["no public API change"],
            "expected_outcome": "Shared validation implementation",
        }
    )
    proposal = {
        "proposal_id": "prop-17",
        "request_id": request["request_id"],
        "actor_id": request["actor_id"],
        "purpose": request["purpose"],
        "repository_snapshot": request["repository_snapshot"],
        "affected_scope": request["target_scope"],
        "intended_changes": ["Centralize validation"],
        "expected_effects": ["Preserve behavior"],
        "known_risks": [],
        "required_validations": ["regression_suite"],
        "evidence_used": ["ctx-551"],
        "interaction_state": "PROPOSED",
    }
    with (
        patch.object(server.mcp, "active_tool_profile", "target_repository_default", create=True),
        patch.object(server.mcp, "_visible_tool_names", frozenset({"validate_actor_proposal"}), create=True),
        patch.object(server.mcp._tool_manager, "call_tool", dispatch),
    ):
        payload = json.loads(
            asyncio.run(
                server.dispatch_actor_request(
                    request,
                    "validate_actor_proposal",
                    {"proposal": proposal},
                )
            )
        )

    assert payload["status"] == "PROPOSAL_CONFORMANT"
    assert payload["completed_states"][-1] == "PROPOSED"
    assert payload["tool_result"] == result
    effective = dispatch.await_args.args[1]
    assert effective["request_id"] == request["request_id"]
    assert effective["request_actor_id"] == request["actor_id"]
    assert effective["declared_scope"] == request["target_scope"]


def test_actor_gateway_blocks_forged_proposal_actor_binding_before_dispatch() -> None:
    request = _request(
        operation="propose",
        target_scope={"repository": "analyzed_repository", "files": ["src/validation/a.ts"]},
    )
    request.update(
        {
            "repository_snapshot": "commit-abc123",
            "constraints": ["no public API change"],
            "expected_outcome": "Shared validation implementation",
        }
    )
    dispatch = AsyncMock()
    with (
        patch.object(server.mcp, "active_tool_profile", "target_repository_default", create=True),
        patch.object(server.mcp, "_visible_tool_names", frozenset({"validate_actor_proposal"}), create=True),
        patch.object(server.mcp._tool_manager, "call_tool", dispatch),
    ):
        payload = json.loads(
            asyncio.run(
                server.dispatch_actor_request(
                    request,
                    "validate_actor_proposal",
                    {"proposal": {}, "request_actor_id": "agent:forged"},
                )
            )
        )

    assert payload["status"] == "BLOCKED"
    assert payload["failure_code"] == "request_binding_conflict"
    dispatch.assert_not_awaited()


def test_actor_proposal_tool_fails_closed_outside_canonical_gateway() -> None:
    payload = json.loads(
        server.validate_actor_proposal(
            {},
            request_id="self-asserted",
            request_actor_id="agent:self",
            request_purpose="Self attest",
            repository_snapshot="self-asserted",
            declared_scope={"repository": "analyzed_repository"},
        )
    )
    assert payload["status"] == "BLOCKED"
    assert payload["failure_code"] == "actor_gateway_required"


def test_actor_gateway_context_reaches_real_proposal_tool() -> None:
    request = _request(
        operation="propose",
        target_scope={"repository": "analyzed_repository", "files": ["src/validation/a.ts"]},
    )
    request.update(
        {
            "repository_snapshot": "commit-abc123",
            "constraints": ["no public API change"],
            "expected_outcome": "Shared validation implementation",
        }
    )
    proposal = {
        "proposal_id": "prop-real",
        "request_id": request["request_id"],
        "actor_id": request["actor_id"],
        "purpose": request["purpose"],
        "repository_snapshot": request["repository_snapshot"],
        "affected_scope": request["target_scope"],
        "intended_changes": ["Centralize validation"],
        "expected_effects": ["Preserve behavior"],
        "known_risks": [],
        "required_validations": ["regression_suite"],
        "evidence_used": ["ctx-551"],
        "interaction_state": "PROPOSED",
    }
    with (
        patch.object(server.mcp, "active_tool_profile", "target_repository_default", create=True),
        patch.object(server.mcp, "_visible_tool_names", frozenset({"validate_actor_proposal"}), create=True),
    ):
        payload = json.loads(
            asyncio.run(
                server.dispatch_actor_request(
                    request,
                    "validate_actor_proposal",
                    {"proposal": proposal},
                )
            )
        )

    assert payload["status"] == "PROPOSAL_CONFORMANT"
    assert json.loads(payload["tool_result"])["authorization_granted"] is False


def test_merge_decision_support_rejects_scope_expansion_without_dispatch() -> None:
    request = _request(operation="decision_support", target_scope={"repository": "C:/targets/declared"})
    dispatch = AsyncMock()
    with (
        patch.object(server.mcp, "active_tool_profile", "target_repository_default", create=True),
        patch.object(server.mcp, "_visible_tool_names", frozenset({"get_merge_review_queue"}), create=True),
        patch.object(server.mcp._tool_manager, "call_tool", dispatch),
    ):
        payload = json.loads(
            asyncio.run(
                server.dispatch_actor_request(
                    request,
                    "get_merge_review_queue",
                    {"target_root": "C:/targets/undeclared"},
                )
            )
        )

    assert payload["status"] == "BLOCKED"
    assert payload["failure_code"] == "scope_argument_mismatch"
    dispatch.assert_not_awaited()


def test_merge_decision_support_preserves_human_approval_terminal_state() -> None:
    result = json.dumps(
        {
            "status": "APPROVAL_REQUIRED",
            "policy_boundary": {
                "human_approval_required": True,
                "mutation_allowed_by_this_packet": False,
            },
            "target_proof": {"verdict": "REVIEW_REQUIRED"},
        }
    )
    dispatch = AsyncMock(return_value=result)
    request = _request(operation="decision_support", target_scope={"repository": "analyzed_repository"})
    with (
        patch.object(server.mcp, "active_tool_profile", "target_repository_default", create=True),
        patch.object(server.mcp, "_visible_tool_names", frozenset({"get_merge_review_queue"}), create=True),
        patch.object(server.mcp._tool_manager, "call_tool", dispatch),
    ):
        payload = json.loads(asyncio.run(server.dispatch_actor_request(request, "get_merge_review_queue", {})))

    assert payload["status"] == "APPROVAL_REQUIRED"
    assert payload["completed_states"][-1] == "POLICY_EVALUATED"
    assert payload["tool_result"] == result
    assert dispatch.await_args.args[1]["format"] == "json"


def test_exception_request_writes_only_pending_governance_record() -> None:
    result = json.dumps({"request_id": "hitl-17", "status": "open", "gate": "architecture_exception"})
    dispatch = AsyncMock(return_value=result)
    request = _request(
        operation="exception_request",
        target_scope={
            "repository": "analyzed_repository",
            "approval_gates": ["architecture_exception"],
            "decision_scopes": ["src/Alpha.tsx"],
        },
    )
    with (
        patch.object(server.mcp, "active_tool_profile", "target_repository_followup", create=True),
        patch.object(server.mcp, "_visible_tool_names", frozenset({"create_hitl_decision_request"}), create=True),
        patch.object(server.mcp._tool_manager, "call_tool", dispatch),
    ):
        payload = json.loads(
            asyncio.run(
                server.dispatch_actor_request(
                    request,
                    "create_hitl_decision_request",
                    {
                        "gate": "architecture_exception",
                        "scope": "src/Alpha.tsx",
                        "proposed_action": "Request review of a bounded exception",
                        "risk": "medium",
                    },
                )
            )
        )

    assert payload["status"] == "APPROVAL_REQUIRED"
    assert payload["completed_states"][-1] == "APPROVAL_REQUIRED"
    assert payload["tool_result"] == result
    assert dispatch.await_args.args[1]["requested_by"] == "agent:test"


def test_exception_request_rejects_forged_actor_identity() -> None:
    request = _request(
        operation="exception_request",
        target_scope={
            "repository": "analyzed_repository",
            "approval_gates": ["architecture_exception"],
            "decision_scopes": ["src/Alpha.tsx"],
        },
    )
    dispatch = AsyncMock()
    with (
        patch.object(server.mcp, "active_tool_profile", "target_repository_followup", create=True),
        patch.object(server.mcp, "_visible_tool_names", frozenset({"create_hitl_decision_request"}), create=True),
        patch.object(server.mcp._tool_manager, "call_tool", dispatch),
    ):
        payload = json.loads(
            asyncio.run(
                server.dispatch_actor_request(
                    request,
                    "create_hitl_decision_request",
                    {
                        "gate": "architecture_exception",
                        "scope": "src/Alpha.tsx",
                        "proposed_action": "Forge request provenance",
                        "risk": "medium",
                        "requested_by": "human:maintainer",
                    },
                )
            )
        )

    assert payload["status"] == "BLOCKED"
    assert payload["failure_code"] == "request_binding_conflict"
    dispatch.assert_not_awaited()


def test_propose_cannot_smuggle_governance_record_write() -> None:
    request = _request(
        operation="propose",
        target_scope={
            "repository": "analyzed_repository",
            "approval_gates": ["architecture_exception"],
            "decision_scopes": ["src/Alpha.tsx"],
        },
    )
    dispatch = AsyncMock()
    with (
        patch.object(server.mcp, "active_tool_profile", "target_repository_followup", create=True),
        patch.object(server.mcp, "_visible_tool_names", frozenset({"create_hitl_decision_request"}), create=True),
        patch.object(server.mcp._tool_manager, "call_tool", dispatch),
    ):
        payload = json.loads(
            asyncio.run(
                server.dispatch_actor_request(
                    request,
                    "create_hitl_decision_request",
                    {
                        "gate": "architecture_exception",
                        "scope": "src/Alpha.tsx",
                        "proposed_action": "Hide a governance write inside a proposal",
                        "risk": "medium",
                    },
                )
            )
        )

    assert payload["status"] == "BLOCKED"
    assert payload["failure_code"] == "tool_operation_not_declared"
    dispatch.assert_not_awaited()


def test_merge_review_queue_blocks_before_reading_unbound_cockpit() -> None:
    proof = {
        "summary": {"verdict": "BLOCKED"},
        "subject": {"analysis_snapshot_id": "snapshot-a", "root_binding": "BOUND"},
        "evidence": [
            {
                "artifact_id": "merge_decision_cockpit",
                "availability": "PRESENT",
                "freshness": "CURRENT",
                "snapshot_binding": "MISMATCH",
                "bound_snapshot_id": "snapshot-old",
                "content_sha256": "a" * 64,
                "required": True,
                "source_verdict": "PRESENT",
            }
        ],
        "unknowns": ["merge_decision_cockpit:snapshot_mismatch"],
        "human_decisions": ["review_merge_decision_cockpit"],
        "claim_boundary": "bounded target evidence",
    }
    with (
        patch.object(server, "_raw_dir_for_target", return_value=server.RAW_DIR),
        patch.object(server, "_ensure_agent_artifact_chain_current", return_value={"status": "PASS"}),
        patch("tools.core.target_repository_proof.build_target_repository_proof", return_value=proof),
        patch.object(server, "_load_json") as queue_reader,
    ):
        payload = json.loads(server.get_merge_review_queue(format="json"))

    assert payload["status"] == "INCOMPLETE_EVIDENCE"
    assert payload["items"] == []
    assert payload["target_proof"]["cockpit"]["snapshot_binding"] == "MISMATCH"
    assert payload["target_proof"]["blocking_evidence"][0]["artifact_id"] == "merge_decision_cockpit"
    assert "producer lineage" in payload["required_action"]
    queue_reader.assert_not_called()


def test_merge_review_queue_returns_snapshot_bound_human_review_only_evidence() -> None:
    from tools.core.analysis_snapshot_lineage import payload_sha256

    cockpit = {
        "decisions": [
            {
                "candidate": "candidate-a",
                "action": "Import With Review",
                "confidence": {"tier": "MEDIUM", "score": 70},
                "source": "VARIATION",
                "target_path": "src/a.ts",
                "evidence": {},
            }
        ]
    }
    proof = {
        "summary": {"verdict": "REVIEW_REQUIRED"},
        "subject": {"analysis_snapshot_id": "snapshot-a", "root_binding": "BOUND"},
        "evidence": [
            {
                "artifact_id": "merge_decision_cockpit",
                "availability": "PRESENT",
                "freshness": "CURRENT",
                "snapshot_binding": "BOUND",
                "bound_snapshot_id": "snapshot-a",
                "content_sha256": payload_sha256(cockpit),
                "required": True,
                "source_verdict": "PRESENT",
            }
        ],
        "unknowns": [],
        "human_decisions": ["review_merge_decision_cockpit"],
        "claim_boundary": "bounded target evidence",
    }
    with (
        patch.object(server, "_raw_dir_for_target", return_value=server.RAW_DIR),
        patch.object(server, "_ensure_agent_artifact_chain_current", return_value={"status": "PASS"}),
        patch("tools.core.target_repository_proof.build_target_repository_proof", return_value=proof),
        patch.object(Path, "exists", return_value=True),
        patch.object(server, "_load_json", return_value=cockpit),
        patch.object(server, "_target_path_status", return_value={}),
    ):
        payload = json.loads(server.get_merge_review_queue(format="json"))

    assert payload["status"] == "APPROVAL_REQUIRED"
    assert payload["target_proof"]["cockpit"]["snapshot_binding"] == "BOUND"
    assert payload["policy_boundary"]["mutation_allowed_by_this_packet"] is False
    assert payload["policy_boundary"]["human_approval_required"] is True
    assert len(payload["items"]) == 1


def test_merge_review_queue_rejects_cockpit_changed_after_proof() -> None:
    proof = {
        "summary": {"verdict": "REVIEW_REQUIRED"},
        "subject": {"analysis_snapshot_id": "snapshot-a", "root_binding": "BOUND"},
        "evidence": [
            {
                "artifact_id": "merge_decision_cockpit",
                "availability": "PRESENT",
                "freshness": "CURRENT",
                "snapshot_binding": "BOUND",
                "bound_snapshot_id": "snapshot-a",
                "content_sha256": "a" * 64,
                "required": True,
                "source_verdict": "PRESENT",
            }
        ],
        "unknowns": [],
        "human_decisions": ["review_merge_decision_cockpit"],
        "claim_boundary": "bounded target evidence",
    }
    changed_cockpit = {"decisions": [{"candidate": "unproved-candidate"}]}
    with (
        patch.object(server, "_raw_dir_for_target", return_value=server.RAW_DIR),
        patch.object(server, "_ensure_agent_artifact_chain_current", return_value={"status": "PASS"}),
        patch("tools.core.target_repository_proof.build_target_repository_proof", return_value=proof),
        patch.object(Path, "exists", return_value=True),
        patch.object(server, "_load_json", return_value=changed_cockpit),
        patch.object(server, "_target_path_status") as target_status,
    ):
        payload = json.loads(server.get_merge_review_queue(format="json"))

    assert payload["status"] == "INCOMPLETE_EVIDENCE"
    assert payload["items"] == []
    assert payload["total_candidates"] == 0
    assert "content changed" in payload["required_action"]
    target_status.assert_not_called()


def test_merge_review_queue_fails_closed_when_target_proof_cannot_be_built() -> None:
    with (
        patch.object(server, "_raw_dir_for_target", return_value=server.RAW_DIR),
        patch.object(server, "_ensure_agent_artifact_chain_current", return_value={"status": "PASS"}),
        patch("tools.core.target_repository_proof.build_target_repository_proof", side_effect=ValueError("bad contract")),
        patch.object(server, "_load_json") as queue_reader,
    ):
        payload = json.loads(server.get_merge_review_queue(format="json"))

    assert payload["status"] == "INCOMPLETE_EVIDENCE"
    assert payload["items"] == []
    assert "bad contract" not in json.dumps(payload)
    queue_reader.assert_not_called()


def test_merge_review_brief_preserves_artifact_trust_precondition() -> None:
    with (
        patch.object(server, "_raw_dir_for_target", return_value=server.RAW_DIR),
        patch.object(server, "_ensure_agent_artifact_chain_current", return_value={"status": "FAIL"}),
    ):
        brief = server.get_merge_review_queue(format="brief")

    assert brief.startswith("# Invalid Repository Context")
    assert "status: INCOMPLETE_EVIDENCE" in brief
    assert "blocking: true" in brief
    assert "tool: get_merge_review_queue" in brief
    assert "required_action:" in brief
    assert "claim_boundary:" in brief


def test_actor_gateway_reports_dispatch_exception_as_incomplete_evidence() -> None:
    dispatch = AsyncMock(side_effect=RuntimeError("sensitive internal detail"))
    with (
        patch.object(server.mcp, "active_tool_profile", "target_repository_default", create=True),
        patch.object(server.mcp, "_visible_tool_names", frozenset({"get_violation_work_queue"}), create=True),
        patch.object(server.mcp._tool_manager, "call_tool", dispatch),
    ):
        payload = json.loads(asyncio.run(server.dispatch_actor_request(_request(), "get_violation_work_queue", {})))

    assert payload["status"] == "INCOMPLETE_EVIDENCE"
    assert payload["completed_states"] == ["REQUESTED"]
    assert payload["tool_result"]["status"] == "dispatch_failed"
    assert payload["tool_result"]["error_type"] == "RuntimeError"
    assert "sensitive internal detail" not in json.dumps(payload)


def test_surgical_packet_builder_reads_runtime_evidence_from_explicit_target_root() -> None:
    from tools.core.contextos_mcp import build_surgical_operation_packet

    with tempfile.TemporaryDirectory() as temp_dir:
        target_raw = Path(temp_dir).resolve()
        with patch("tools.core.contextos_mcp.load_json_file", return_value={}) as loader:
            build_surgical_operation_packet({"active_signals": []}, raw_dir=target_raw)

    runtime_names = {
        "pipeline_execution_contract_validation.json",
        "engine_signal_contract_validation.json",
        "architecture_oracle.json",
        "hitl_approval_ledger.json",
    }
    observed = {Path(call.args[0]).resolve() for call in loader.call_args_list if Path(call.args[0]).name in runtime_names}
    assert observed == {target_raw / name for name in runtime_names}


def test_surgical_packet_does_not_expose_sage_fixture_validation_as_target_evidence() -> None:
    from tools.core.contextos_mcp import build_surgical_operation_packet

    packet = build_surgical_operation_packet({"active_signals": []})

    assert "react_edge_case_gate" not in packet["summary"]


def test_surgical_packet_rejects_stale_context_before_building_packet() -> None:
    stale = {"status": "FAIL", "scope": "external_target", "failures": [{"name": "stale"}], "warnings": []}
    with (
        patch.object(server, "_raw_dir_for_target", return_value=server.RAW_DIR),
        patch.object(server, "_ensure_agent_artifact_chain_current", return_value=stale),
        patch("tools.core.contextos_mcp.build_surgical_operation_packet") as builder,
        patch.object(server, "_record_mcp_call_result", side_effect=lambda _name, _started, result, **_kwargs: result),
    ):
        payload = json.loads(server.get_surgical_operation_packet(format="json", target_root="C:/isolated"))

    assert payload["status"] == "INVALID_CONTEXT"
    assert payload["blocking"] is True
    assert payload["recovery_plan"]["reason"] == "packet_artifact_trust_recovery_required"
    assert payload["recovery_plan"]["blocked_required_inputs"] == ["artifact_trust_chain"]
    assert payload["recovery_plan"]["command_argv"] == [
        "python",
        "sage.py",
        "run",
        "--profile",
        "daily",
        "--projects",
        "MAIN",
        "--target-root",
        "C:/isolated",
        "--refresh",
    ]
    assert "Quality Gates" not in payload["refresh_command"]
    builder.assert_not_called()


def test_recommended_write_lease_action_names_required_profile_transition() -> None:
    default_tools = server.project_mcp_tool_names(
        server.BASE_DIR,
        "target_repository_default",
    )["visible_tools"]
    with (
        patch.object(server.mcp, "active_tool_profile", "target_repository_default"),
        patch.object(server.mcp, "_visible_tool_names", frozenset(default_tools)),
    ):
        action = server._mcp_recommended_tool_action(
            "manage_target_write_lease",
            {
                "action": "acquire",
                "target_file": "src/example.ts",
                "actor_id": "agent-a",
                "target_root": "",
            },
            required_profile="target_repository_followup",
        )

    assert action["availability"] == "profile_transition_required"
    assert action["callable_in_current_profile"] is False
    assert action["required_profile"] == "target_repository_followup"
    assert action["callable_in_required_profile"] is True
    assert action["profile_transition_required"] is True
    assert action["profile_config_command_argv"] == [
        "python",
        "sage.py",
        "mcp",
        "--profile",
        "target_repository_followup",
        "--print-config",
    ]
    assert action["return_profile"] == "target_repository_default"


def test_surgical_packet_rejects_unbound_required_input_before_building_packet() -> None:
    evidence = {"status": "BLOCKED", "blocked_inputs": ["signals"], "omitted_inputs": []}
    with (
        patch.object(server, "_raw_dir_for_target", return_value=server.RAW_DIR),
        patch.object(server, "_ensure_agent_artifact_chain_current", return_value={"status": "PASS"}),
        patch("tools.core.surgical_packet_inputs.evaluate_surgical_packet_inputs", return_value=(evidence, {})),
        patch("tools.core.contextos_mcp.build_surgical_operation_packet") as builder,
        patch.object(server, "_record_mcp_call_result", side_effect=lambda _name, _started, result, **_kwargs: result),
    ):
        payload = json.loads(server.get_surgical_operation_packet(format="json", target_root="C:/isolated"))

    assert payload["status"] == "INVALID_CONTEXT"
    assert payload["blocking"] is True
    assert payload["input_evidence"] == evidence
    assert payload["recovery_plan"]["reason"] == "required_packet_input_generation_mismatch"
    assert payload["refresh_command"]
    assert payload["recovery_plan"]["command_argv"] == [
        "python",
        "sage.py",
        "run",
        "--profile",
        "daily",
        "--projects",
        "MAIN",
        "--target-root",
        "C:/isolated",
        "--refresh",
    ]
    builder.assert_not_called()


def test_surgical_packet_reports_optional_input_omission() -> None:
    signals = {"active_signals": []}
    evidence = {
        "status": "PARTIAL_CONTEXT",
        "blocked_inputs": [],
        "omitted_inputs": ["circular_deps", "live_surface_priority_pack"],
    }
    packet = {"summary": {}, "agent_action_directives": []}
    with (
        patch.object(server, "_raw_dir_for_target", return_value=server.RAW_DIR),
        patch.object(server, "_ensure_agent_artifact_chain_current", return_value={"status": "PASS"}),
        patch("tools.core.surgical_packet_inputs.evaluate_surgical_packet_inputs", return_value=(evidence, {"signals": signals})),
        patch("tools.core.contextos_mcp.build_surgical_operation_packet", return_value=packet),
        patch.object(server, "_record_mcp_call_result", side_effect=lambda _name, _started, result, **_kwargs: result),
    ):
        payload = json.loads(server.get_surgical_operation_packet(format="json", target_root="C:/isolated"))

    assert payload["status"] == "INCOMPLETE_EVIDENCE"
    assert payload["input_evidence"] == evidence
