from __future__ import annotations

import copy
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tools.core.actor_proposal_contract import validate_actor_proposal_payload
from tools.core.actor_interaction_runtime import ActorInteractionError, validate_mcp_actor_request


ROOT = Path(__file__).resolve().parents[2]


def _proposal_contract() -> dict:
    return json.loads((ROOT / "config" / "actor_interaction_contract.json").read_text(encoding="utf-8"))["proposal_contract"]


def _proposal() -> dict:
    return {
        "proposal_id": "prop-17",
        "request_id": "req-proposal",
        "actor_id": "tool:test",
        "purpose": "Centralize duplicated validation behavior",
        "repository_snapshot": "commit-abc123",
        "affected_scope": {"repository": "analyzed_repository", "files": ["src/validation/a.ts"]},
        "intended_changes": ["Introduce one shared internal validation function"],
        "expected_effects": ["Preserve exported behavior"],
        "known_risks": ["Previous implementations may differ"],
        "required_validations": ["source_contract_validation", "regression_suite"],
        "evidence_used": ["ctx-551"],
        "interaction_state": "PROPOSED",
    }


def _request(operation: str, scope: dict) -> dict:
    return {
        "request_id": "req-runtime",
        "principal_id": "human:maintainer",
        "actor_id": "tool:test",
        "actor_type": "tool_actor",
        "adapter_type": "mcp",
        "purpose": "Validate one bounded patch",
        "operation": operation,
        "target_scope": scope,
        "requested_mode": operation,
    }


def test_runtime_accepts_declared_validation_scope() -> None:
    result = validate_mcp_actor_request(
        ROOT,
        _request("validate", {"repository": "analyzed_repository", "files": ["src/FeaturePanel.tsx"]}),
        tool_name="validate_patch",
        tool_arguments={"target_file": "src/FeaturePanel.tsx", "patch_content": "+change"},
        profile="target_repository_default",
        visible_tools={"validate_patch"},
    )
    assert result["operation_profile"] == "validate"
    assert result["required_states"] == ["REQUESTED", "CONTEXT_RESOLVED", "SCOPE_RESOLVED", "VALIDATED"]
    assert result["tool_arguments"]["format"] == "json"
    assert result["result_status_map"]["FAIL"] == "BLOCKED"


def test_proposal_contract_accepts_bounded_identity_bound_proposal_without_authority() -> None:
    proposal = _proposal()
    proposal["known_risks"] = []
    result = validate_actor_proposal_payload(
        proposal,
        proposal_contract=_proposal_contract(),
        request_id=proposal["request_id"],
        request_actor_id=proposal["actor_id"],
        request_purpose=proposal["purpose"],
        repository_snapshot=proposal["repository_snapshot"],
        declared_scope=proposal["affected_scope"],
    )
    assert result["status"] == "PROPOSAL_CONFORMANT"
    assert result["authorization_granted"] is False
    assert result["validation_completed"] is False
    assert "does not prove technical correctness" in result["claim_boundary"]


@pytest.mark.parametrize("field", ["request_id", "actor_id", "purpose", "repository_snapshot"])
def test_proposal_contract_rejects_envelope_identity_or_freshness_mismatch(field: str) -> None:
    proposal = _proposal()
    expected = {
        "request_id": proposal["request_id"],
        "request_actor_id": proposal["actor_id"],
        "request_purpose": proposal["purpose"],
        "repository_snapshot": proposal["repository_snapshot"],
    }
    argument = {
        "request_id": "request_id",
        "actor_id": "request_actor_id",
        "purpose": "request_purpose",
        "repository_snapshot": "repository_snapshot",
    }[field]
    expected[argument] = "different"
    result = validate_actor_proposal_payload(
        proposal,
        proposal_contract=_proposal_contract(),
        declared_scope=proposal["affected_scope"],
        **expected,
    )
    assert result["status"] == "INVALID_CONTEXT"
    assert field in result["identity_mismatches"]


def test_proposal_contract_rejects_category_specific_scope_expansion() -> None:
    proposal = _proposal()
    proposal["affected_scope"]["files"] = ["src/validation/unrelated.ts"]
    result = validate_actor_proposal_payload(
        proposal,
        proposal_contract=_proposal_contract(),
        request_id=proposal["request_id"],
        request_actor_id=proposal["actor_id"],
        request_purpose=proposal["purpose"],
        repository_snapshot=proposal["repository_snapshot"],
        declared_scope={"repository": "analyzed_repository", "files": ["src/validation/a.ts"]},
    )
    assert result["status"] == "BLOCKED"
    assert result["scope_expansion_keys"] == ["files"]


def test_proposal_contract_rejects_nested_authority_smuggling() -> None:
    proposal = _proposal()
    proposal["metadata"] = {"authorization": {"approved": True}}
    result = validate_actor_proposal_payload(
        proposal,
        proposal_contract=_proposal_contract(),
        request_id=proposal["request_id"],
        request_actor_id=proposal["actor_id"],
        request_purpose=proposal["purpose"],
        repository_snapshot=proposal["repository_snapshot"],
        declared_scope=proposal["affected_scope"],
    )
    assert result["status"] == "BLOCKED"
    assert result["forbidden_authority_fields"] == ["approved", "authorization"]


@pytest.mark.parametrize("field", ["authorization_status", "is_approved", "VALIDATED_BY_ACTOR"])
def test_proposal_contract_rejects_semantic_authority_key_variants(field: str) -> None:
    proposal = _proposal()
    proposal["metadata"] = {field: "claimed"}
    result = validate_actor_proposal_payload(
        proposal,
        proposal_contract=_proposal_contract(),
        request_id=proposal["request_id"],
        request_actor_id=proposal["actor_id"],
        request_purpose=proposal["purpose"],
        repository_snapshot=proposal["repository_snapshot"],
        declared_scope=proposal["affected_scope"],
    )
    assert result["status"] == "BLOCKED"
    assert result["forbidden_authority_fields"] == [field]


def test_runtime_rejects_hidden_tool_before_dispatch() -> None:
    with pytest.raises(ActorInteractionError) as caught:
        validate_mcp_actor_request(
            ROOT,
            _request("validate", {"repository": "analyzed_repository", "files": ["src/FeaturePanel.tsx"]}),
            tool_name="validate_patch",
            tool_arguments={"target_file": "src/FeaturePanel.tsx"},
            profile="target_repository_default",
            visible_tools=set(),
        )
    assert caught.value.code == "tool_not_visible"


def test_runtime_rejects_operation_mode_mismatch() -> None:
    request = _request("inspect", {"repository": "analyzed_repository"})
    request["requested_mode"] = "mutate"
    with pytest.raises(ActorInteractionError) as caught:
        validate_mcp_actor_request(
            ROOT,
            request,
            tool_name="get_violation_work_queue",
            tool_arguments={},
            profile="target_repository_default",
            visible_tools={"get_violation_work_queue"},
        )
    assert caught.value.code == "operation_mode_mismatch"


def test_runtime_rejects_fixed_argument_override() -> None:
    with pytest.raises(ActorInteractionError) as caught:
        validate_mcp_actor_request(
            ROOT,
            _request("validate", {"repository": "analyzed_repository", "files": ["src/FeaturePanel.tsx"]}),
            tool_name="validate_patch",
            tool_arguments={"target_file": "src/FeaturePanel.tsx", "patch_content": "+change", "format": "brief"},
            profile="target_repository_default",
            visible_tools={"validate_patch"},
        )
    assert caught.value.code == "fixed_argument_conflict"


def test_runtime_allows_only_governance_record_for_exception_request() -> None:
    result = validate_mcp_actor_request(
        ROOT,
        _request(
            "exception_request",
            {
                "repository": "analyzed_repository",
                "approval_gates": ["architecture_exception"],
                "decision_scopes": ["src/FeaturePanel.tsx"],
            },
        ),
        tool_name="create_hitl_decision_request",
        tool_arguments={
            "gate": "architecture_exception",
            "scope": "src/FeaturePanel.tsx",
            "proposed_action": "Request a bounded architecture exception",
            "risk": "medium",
        },
        profile="target_repository_followup",
        visible_tools={"create_hitl_decision_request"},
    )
    assert result["mutation_domains"] == ["governance_record"]
    assert result["tool_arguments"]["requested_by"] == "tool:test"
    assert result["result_status_map"]["open"] == "APPROVAL_REQUIRED"


def test_runtime_binds_proposal_to_canonical_request_envelope() -> None:
    request = _request(
        "propose",
        {"repository": "analyzed_repository", "files": ["src/validation/a.ts"]},
    )
    request.update(
        {
            "request_id": "req-proposal",
            "purpose": "Centralize duplicated validation behavior",
            "repository_snapshot": "commit-abc123",
            "constraints": ["no public API change"],
            "expected_outcome": "Shared validation implementation",
        }
    )
    result = validate_mcp_actor_request(
        ROOT,
        request,
        tool_name="validate_actor_proposal",
        tool_arguments={"proposal": _proposal()},
        profile="target_repository_default",
        visible_tools={"validate_actor_proposal"},
    )
    assert result["operation_profile"] == "propose"
    assert result["tool_arguments"]["request_id"] == request["request_id"]
    assert result["tool_arguments"]["request_actor_id"] == request["actor_id"]
    assert result["tool_arguments"]["repository_snapshot"] == request["repository_snapshot"]
    assert result["tool_arguments"]["declared_scope"] == request["target_scope"]
    assert result["result_status_map"]["PROPOSAL_CONFORMANT"] == "PROPOSAL_CONFORMANT"


def test_runtime_blocks_repository_mutation_domain_through_exception_request() -> None:
    contract = json.loads((ROOT / "config" / "actor_interaction_contract.json").read_text(encoding="utf-8"))
    roles = json.loads((ROOT / "config" / "mcp_tool_roles.json").read_text(encoding="utf-8"))
    altered_roles = copy.deepcopy(roles)
    altered_roles["tools"]["create_hitl_decision_request"]["mutation_domains"] = ["repository_state"]

    def load_contract(path: Path, default: dict) -> dict:
        return contract if path.name == "actor_interaction_contract.json" else altered_roles

    with patch("tools.core.actor_interaction_runtime.load_json_file", side_effect=load_contract):
        with pytest.raises(ActorInteractionError) as caught:
            validate_mcp_actor_request(
                ROOT,
                _request(
                    "exception_request",
                    {
                        "repository": "analyzed_repository",
                        "approval_gates": ["architecture_exception"],
                        "decision_scopes": ["src/FeaturePanel.tsx"],
                    },
                ),
                tool_name="create_hitl_decision_request",
                tool_arguments={
                    "gate": "architecture_exception",
                    "scope": "src/FeaturePanel.tsx",
                    "proposed_action": "Request a bounded architecture exception",
                    "risk": "medium",
                },
                profile="target_repository_followup",
                visible_tools={"create_hitl_decision_request"},
            )
    assert caught.value.code == "mutation_domain_blocked"
