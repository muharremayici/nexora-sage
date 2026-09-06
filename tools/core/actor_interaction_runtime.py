from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from tools.core.json_io import load_json_file


class ActorInteractionError(ValueError):
    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


def _fingerprint(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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


def validate_mcp_actor_request(
    root: Path,
    request: dict[str, Any],
    *,
    tool_name: str,
    tool_arguments: dict[str, Any],
    profile: str,
    visible_tools: set[str],
) -> dict[str, Any]:
    contract = load_json_file(root / "config" / "actor_interaction_contract.json", {})
    roles = load_json_file(root / "config" / "mcp_tool_roles.json", {})
    request_contract = contract.get("request_contract") if isinstance(contract.get("request_contract"), dict) else {}
    ontology = contract.get("ontology") if isinstance(contract.get("ontology"), dict) else {}
    profiles = contract.get("operation_profiles") if isinstance(contract.get("operation_profiles"), dict) else {}
    tool_rows = roles.get("tools") if isinstance(roles.get("tools"), dict) else {}
    tool = tool_rows.get(tool_name) if isinstance(tool_rows.get(tool_name), dict) else {}
    actor_contract = tool.get("actor_contract") if isinstance(tool.get("actor_contract"), dict) else {}

    if not isinstance(request, dict):
        raise ActorInteractionError("invalid_request", "Actor request must be a structured object.")
    missing = [field for field in request_contract.get("required_common_fields", []) if not _nonempty(request.get(str(field)))]
    if missing:
        raise ActorInteractionError("missing_required_fields", "Actor request is incomplete.", details={"fields": missing})
    if request.get("adapter_type") != "mcp":
        raise ActorInteractionError("adapter_mismatch", "Actor request adapter_type must be mcp.")
    if request.get("actor_type") not in set(ontology.get("actor_types") or []):
        raise ActorInteractionError("unknown_actor_type", "Actor request uses an unknown actor_type.")
    operation = str(request.get("operation") or "")
    if operation not in profiles or request.get("requested_mode") != operation:
        raise ActorInteractionError("operation_mode_mismatch", "operation and requested_mode must name one canonical profile.")
    if tool_name not in visible_tools:
        raise ActorInteractionError("tool_not_visible", "Requested tool is outside the active MCP profile.")
    allowed_operations = {str(item) for item in actor_contract.get("operations", []) if str(item)}
    if operation not in allowed_operations:
        raise ActorInteractionError(
            "tool_operation_not_declared",
            "Requested tool is not declared for this actor operation.",
            details={"tool": tool_name, "operation": operation, "allowed": sorted(allowed_operations)},
        )
    profile_contract = profiles.get(operation) if isinstance(profiles.get(operation), dict) else {}
    mutation_domain_contracts = ontology.get("mutation_domains") if isinstance(ontology.get("mutation_domains"), dict) else {}
    canonical_mutation_domains = {str(item) for item in mutation_domain_contracts if str(item)}
    allowed_mutation_domains = {str(item) for item in profile_contract.get("allowed_mutation_domains", []) if str(item)}
    tool_mutation_domains = {str(item) for item in tool.get("mutation_domains", []) if str(item)}
    if not allowed_mutation_domains.issubset(canonical_mutation_domains):
        raise ActorInteractionError("invalid_operation_mutation_domain", "Operation profile declares an unknown mutation domain.")
    invalid_operation_domains = sorted(
        domain
        for domain in allowed_mutation_domains
        if operation not in {str(item) for item in (mutation_domain_contracts.get(domain) or {}).get("allowed_operations", []) if str(item)}
    )
    if invalid_operation_domains:
        raise ActorInteractionError(
            "invalid_operation_mutation_domain",
            "Operation profile uses a mutation domain that does not authorize this operation.",
            details={"domains": invalid_operation_domains},
        )
    if bool(tool.get("mutates")):
        if not bool(profile_contract.get("mutation_allowed")) or not tool_mutation_domains:
            raise ActorInteractionError("authority_upgrade_blocked", "A mutating tool lacks an allowed, explicit mutation domain.")
        if not tool_mutation_domains.issubset(allowed_mutation_domains):
            raise ActorInteractionError(
                "mutation_domain_blocked",
                "Tool mutation domains exceed the operation profile authority.",
                details={"tool_domains": sorted(tool_mutation_domains), "allowed_domains": sorted(allowed_mutation_domains)},
            )
    elif tool_mutation_domains:
        raise ActorInteractionError("invalid_tool_mutation_domain", "A non-mutating tool cannot declare mutation domains.")
    operation_required_fields = request_contract.get("operation_required_fields")
    if not isinstance(operation_required_fields, dict):
        operation_required_fields = {}
    operation_missing = [
        str(field)
        for field in operation_required_fields.get(operation, [])
        if not _nonempty(request.get(str(field)))
    ]
    if operation_missing:
        if operation == "mutate":
            raise ActorInteractionError("missing_mutation_authority", "Mutation request lacks required authority fields.", details={"fields": operation_missing})
        raise ActorInteractionError(
            "missing_operation_fields",
            "Actor request lacks fields required by the selected operation.",
            details={"operation": operation, "fields": operation_missing},
        )

    target_scope = request.get("target_scope")
    if not isinstance(target_scope, dict):
        raise ActorInteractionError("invalid_target_scope", "target_scope must be a structured object.")
    missing_scope_keys = [key for key in actor_contract.get("required_scope_keys", []) if not _nonempty(target_scope.get(str(key)))]
    if missing_scope_keys:
        raise ActorInteractionError("missing_scope_keys", "Actor request target scope is incomplete.", details={"keys": missing_scope_keys})
    scope_bindings = actor_contract.get("scope_bindings") if isinstance(actor_contract.get("scope_bindings"), dict) else {}
    unbound_arguments = []
    invalid_bindings = []
    for argument, binding in scope_bindings.items():
        if not isinstance(binding, dict) or not str(binding.get("scope_key") or ""):
            invalid_bindings.append(str(argument))
            continue
        scope_key = str(binding["scope_key"])
        declared_scope_values = _scope_values(target_scope.get(scope_key))
        supplied = tool_arguments.get(str(argument))
        effective_value = supplied if _nonempty(supplied) else binding.get("default")
        effective_values = _scope_values(effective_value)
        if effective_values and not effective_values.issubset(declared_scope_values):
            unbound_arguments.append(str(argument))
    if invalid_bindings:
        raise ActorInteractionError(
            "invalid_scope_binding_contract",
            "Tool scope binding contract is invalid.",
            details={"arguments": invalid_bindings},
        )
    if unbound_arguments:
        raise ActorInteractionError("scope_argument_mismatch", "Tool arguments expand beyond declared target_scope.", details={"arguments": unbound_arguments})

    effective_arguments = dict(tool_arguments)
    request_bindings = actor_contract.get("request_bindings") if isinstance(actor_contract.get("request_bindings"), dict) else {}
    allowed_request_fields = {
        str(field)
        for field in request_contract.get("required_common_fields", [])
    } | {
        str(field)
        for fields in operation_required_fields.values()
        if isinstance(fields, list)
        for field in fields
    }
    invalid_request_bindings = [
        str(argument)
        for argument, field in request_bindings.items()
        if not str(field) or str(field) not in allowed_request_fields or not _nonempty(request.get(str(field)))
    ]
    if invalid_request_bindings:
        raise ActorInteractionError(
            "invalid_request_binding_contract",
            "Tool request binding contract refers to a missing actor request field.",
            details={"arguments": invalid_request_bindings},
        )
    request_binding_conflicts = [
        str(argument)
        for argument, field in request_bindings.items()
        if argument in effective_arguments and effective_arguments[argument] != request.get(str(field))
    ]
    if request_binding_conflicts:
        raise ActorInteractionError(
            "request_binding_conflict",
            "Tool arguments conflict with canonical actor request identity.",
            details={"arguments": request_binding_conflicts},
        )
    effective_arguments.update({str(argument): request[str(field)] for argument, field in request_bindings.items()})
    fixed_arguments = actor_contract.get("fixed_arguments") if isinstance(actor_contract.get("fixed_arguments"), dict) else {}
    fixed_argument_conflicts = [
        str(name)
        for name, value in fixed_arguments.items()
        if name in effective_arguments and effective_arguments[name] != value
    ]
    if fixed_argument_conflicts:
        raise ActorInteractionError(
            "fixed_argument_conflict",
            "Tool arguments conflict with the canonical actor surface contract.",
            details={"arguments": fixed_argument_conflicts},
        )
    effective_arguments.update(fixed_arguments)
    required_states = [str(item) for item in profile_contract.get("required_states", []) if str(item)]
    terminal_states = {str(item) for item in profile_contract.get("terminal_states", []) if str(item)}
    result_status_map = {
        str(key): str(value)
        for key, value in (actor_contract.get("result_status_map") or {}).items()
        if str(key) and str(value)
    } if isinstance(actor_contract.get("result_status_map"), dict) else {}
    unknown_result_state = str(actor_contract.get("unknown_result_state") or "INCOMPLETE_EVIDENCE")
    invalid_outcomes = sorted({*result_status_map.values(), unknown_result_state} - terminal_states)
    if not required_states or invalid_outcomes:
        raise ActorInteractionError(
            "invalid_result_transition_contract",
            "Tool result transition contract is incomplete or incompatible with the operation profile.",
            details={"invalid_outcomes": invalid_outcomes},
        )

    normalized = {str(key): request[key] for key in sorted(request)}
    return {
        "request": normalized,
        "request_fingerprint": _fingerprint({"request": normalized, "tool": tool_name, "arguments": effective_arguments}),
        "operation_profile": operation,
        "mutation_domains": sorted(tool_mutation_domains),
        "profile": profile,
        "tool": tool_name,
        "tool_arguments": effective_arguments,
        "required_states": required_states,
        "result_status_map": result_status_map,
        "unknown_result_state": unknown_result_state,
        "contract_version": str((contract.get("meta") or {}).get("version") or "unknown"),
    }


def actor_interaction_failure(exc: ActorInteractionError, *, request_id: str = "") -> dict[str, Any]:
    return {
        "status": "BLOCKED",
        "blocking": True,
        "request_id": str(request_id or "not_available"),
        "failure_code": exc.code,
        "message": str(exc),
        "details": exc.details,
        "claim_boundary": "No tool was dispatched and no repository authority was granted.",
    }
