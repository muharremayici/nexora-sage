from __future__ import annotations

from typing import Any


def _nonempty(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return value is not None


def _scope_values(value: Any) -> set[str]:
    if isinstance(value, dict):
        return {item for child in value.values() for item in _scope_values(child)}
    if isinstance(value, list):
        return {item for child in value for item in _scope_values(child)}
    text = str(value or "").replace("\\", "/").strip()
    return {text} if text else set()


def _field_names(value: Any) -> set[str]:
    if isinstance(value, dict):
        return {
            str(key)
            for key in value
        } | {
            field
            for child in value.values()
            for field in _field_names(child)
        }
    if isinstance(value, list):
        return {field for child in value for field in _field_names(child)}
    return set()


def validate_actor_proposal_payload(
    proposal: dict[str, Any],
    *,
    proposal_contract: dict[str, Any],
    request_id: str,
    request_actor_id: str,
    request_purpose: str,
    repository_snapshot: str,
    declared_scope: dict[str, Any],
) -> dict[str, Any]:
    """Validate proposal-envelope conformance without granting authority or judging correctness."""

    required_fields = [str(item) for item in proposal_contract.get("required_fields", []) if str(item)]
    missing_fields = [field for field in required_fields if field not in proposal or proposal.get(field) is None]
    nonempty_fields = [str(item) for item in proposal_contract.get("nonempty_fields", []) if str(item)]
    empty_fields = [field for field in nonempty_fields if not _nonempty(proposal.get(field))]
    claim_boundary = str(proposal_contract.get("claim_boundary") or "")
    if missing_fields or empty_fields:
        return {
            "status": "INCOMPLETE_EVIDENCE",
            "blocking": True,
            "missing_fields": missing_fields,
            "empty_fields": empty_fields,
            "claim_boundary": claim_boundary,
        }

    identity_mismatches = []
    expected_identity = {
        "request_id": request_id,
        "actor_id": request_actor_id,
        "purpose": request_purpose,
        "repository_snapshot": repository_snapshot,
    }
    for field, expected in expected_identity.items():
        if proposal.get(field) != expected:
            identity_mismatches.append(field)
    if identity_mismatches:
        return {
            "status": "INVALID_CONTEXT",
            "blocking": True,
            "identity_mismatches": identity_mismatches,
            "claim_boundary": claim_boundary,
        }

    required_state = str(proposal_contract.get("required_state") or "PROPOSED")
    if proposal.get("interaction_state") != required_state:
        return {
            "status": "BLOCKED",
            "blocking": True,
            "invalid_interaction_state": proposal.get("interaction_state"),
            "required_interaction_state": required_state,
            "claim_boundary": claim_boundary,
        }

    forbidden_fields = {str(item).casefold() for item in proposal_contract.get("forbidden_authority_fields", []) if str(item)}
    forbidden_fragments = {
        str(item).casefold()
        for item in proposal_contract.get("forbidden_authority_key_fragments", [])
        if str(item)
    }
    smuggled_fields = sorted(
        field
        for field in _field_names(proposal)
        if field.casefold() in forbidden_fields
        or any(fragment in field.casefold() for fragment in forbidden_fragments)
    )
    if smuggled_fields:
        return {
            "status": "BLOCKED",
            "blocking": True,
            "forbidden_authority_fields": smuggled_fields,
            "claim_boundary": claim_boundary,
        }

    affected_scope = proposal.get("affected_scope")
    if not isinstance(affected_scope, dict):
        return {
            "status": "INCOMPLETE_EVIDENCE",
            "blocking": True,
            "invalid_fields": ["affected_scope"],
            "claim_boundary": claim_boundary,
        }
    expanded_scope = []
    for scope_key, proposed_value in affected_scope.items():
        proposed_values = _scope_values(proposed_value)
        declared_values = _scope_values(declared_scope.get(str(scope_key)))
        if not proposed_values or not proposed_values.issubset(declared_values):
            expanded_scope.append(str(scope_key))
    if expanded_scope:
        return {
            "status": "BLOCKED",
            "blocking": True,
            "scope_expansion_keys": sorted(expanded_scope),
            "claim_boundary": claim_boundary,
        }

    list_fields = [str(item) for item in proposal_contract.get("list_fields", []) if str(item)]
    invalid_list_fields = [
        field
        for field in list_fields
        if not isinstance(proposal.get(field), list)
    ]
    if invalid_list_fields:
        return {
            "status": "INCOMPLETE_EVIDENCE",
            "blocking": True,
            "invalid_fields": invalid_list_fields,
            "claim_boundary": claim_boundary,
        }

    return {
        "status": "PROPOSAL_CONFORMANT",
        "blocking": False,
        "proposal_id": str(proposal["proposal_id"]),
        "request_id": request_id,
        "actor_id": request_actor_id,
        "repository_snapshot": repository_snapshot,
        "affected_scope": affected_scope,
        "required_validations": proposal["required_validations"],
        "authorization_granted": False,
        "validation_completed": False,
        "claim_boundary": claim_boundary,
    }
