from __future__ import annotations

import copy

from tools.validate_actor_interaction_contract import ENGINE_CONTRACT_PATH, CONTRACT_PATH, _load, validate_contract


def _failed(contract: dict) -> set[str]:
    return {row["id"] for row in validate_contract(contract, _load(ENGINE_CONTRACT_PATH)) if not row.get("ok")}


def test_canonical_contract_passes_semantic_checks() -> None:
    assert not _failed(_load(CONTRACT_PATH))


def test_engine_signal_cannot_be_promoted_to_blocking() -> None:
    contract = copy.deepcopy(_load(CONTRACT_PATH))
    contract["finding_authority"]["levels"]["engine_signal_only"]["may_block"] = True
    assert {"blocking_authority_matches_engine_contract", "engine_signal_cannot_block"}.issubset(_failed(contract))


def test_adapter_cannot_silently_upgrade_privilege() -> None:
    contract = copy.deepcopy(_load(CONTRACT_PATH))
    contract["adapter_contract"]["privilege_upgrade"] = "allowed"
    assert "adapter_privilege_upgrade_prohibited" in _failed(contract)


def test_mutation_cannot_skip_authorization_or_validation() -> None:
    contract = copy.deepcopy(_load(CONTRACT_PATH))
    contract["operation_profiles"]["mutate"]["required_states"] = ["REQUESTED", "EXECUTED"]
    assert "mutation_requires_authorization_execution_validation" in _failed(contract)


def test_human_authority_trace_cannot_collapse_into_operational_trace() -> None:
    contract = copy.deepcopy(_load(CONTRACT_PATH))
    contract["trace_contract"]["sources"]["human_authority_decisions"] = "config/governance_trace_contract.json"
    assert "trace_authority_is_separated_and_privacy_is_bounded" in _failed(contract)


def test_partial_adapter_cannot_exist_without_implementation_evidence() -> None:
    contract = copy.deepcopy(_load(CONTRACT_PATH))
    contract["adapter_contract"]["implementation_evidence"].pop("mcp")
    assert "implemented_adapters_have_existing_evidence" in _failed(contract)


def test_available_adapter_requires_all_promotion_dimensions() -> None:
    contract = copy.deepcopy(_load(CONTRACT_PATH))
    contract["adapter_contract"]["implementation_status"]["mcp"] = "available"
    contract["adapter_contract"]["conformance_evidence"]["adapters"]["mcp"]["status"] = "available"
    assert "available_adapter_requires_all_promotion_dimensions" in _failed(contract)


def test_conformance_status_cannot_drift_from_adapter_status() -> None:
    contract = copy.deepcopy(_load(CONTRACT_PATH))
    contract["adapter_contract"]["conformance_evidence"]["adapters"]["mcp"]["status"] = "available"
    assert "adapter_conformance_evidence_is_complete_and_status_bound" in _failed(contract)


def test_available_scoped_surface_requires_every_promotion_dimension() -> None:
    contract = copy.deepcopy(_load(CONTRACT_PATH))
    surface = contract["adapter_contract"]["conformance_evidence"]["adapters"]["mcp"]["available_surfaces"]["dispatch_actor_request"]
    surface["promotion_dimensions"].remove("stale_context_rejection")
    assert "available_scoped_surface_requires_registry_bound_proof" in _failed(contract)


def test_available_scoped_surface_cannot_claim_unknown_tool() -> None:
    contract = copy.deepcopy(_load(CONTRACT_PATH))
    surface = contract["adapter_contract"]["conformance_evidence"]["adapters"]["mcp"]["available_surfaces"]["dispatch_actor_request"]
    surface["eligible_tools"].append("imaginary_actor_tool")
    assert "available_scoped_surface_requires_registry_bound_proof" in _failed(contract)


def test_available_scoped_surface_requires_at_least_one_eligible_tool() -> None:
    contract = copy.deepcopy(_load(CONTRACT_PATH))
    surface = contract["adapter_contract"]["conformance_evidence"]["adapters"]["mcp"]["available_surfaces"]["dispatch_actor_request"]
    surface["eligible_tools"] = []
    assert "available_scoped_surface_requires_registry_bound_proof" in _failed(contract)


def test_available_scoped_surface_rejects_non_terminal_result_mapping(monkeypatch) -> None:
    from tools import validate_actor_interaction_contract as validator

    roles = copy.deepcopy(_load(validator.MCP_TOOL_ROLES_PATH))
    roles["tools"]["validate_patch"]["actor_contract"]["result_status_map"]["FAIL"] = "EXECUTED"
    original_load = validator._load

    def load_with_invalid_mapping(path):
        return roles if path == validator.MCP_TOOL_ROLES_PATH else original_load(path)

    monkeypatch.setattr(validator, "_load", load_with_invalid_mapping)
    assert "available_scoped_surface_requires_registry_bound_proof" in {
        row["id"]
        for row in validator.validate_contract(original_load(CONTRACT_PATH), original_load(ENGINE_CONTRACT_PATH))
        if not row.get("ok")
    }
