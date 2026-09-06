"""Validate actor interaction semantics against canonical SAGE authorities."""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.work_package_receipts import record_work_package_operation_safely
from tools.generate_actor_interaction_contract_doc import CONTRACT_PATH, DOC_PATH, render


ENGINE_CONTRACT_PATH = ROOT / "config" / "engine_signal_contracts.json"
MCP_TOOL_ROLES_PATH = ROOT / "config" / "mcp_tool_roles.json"
SCHEMA_PATH = ROOT / "config" / "schemas" / "actor_interaction_contract.schema.json"
RAW_PATH = RAW_DIR / "actor_interaction_contract_validation.json"
REPORT_PATH = REPORTS_DIR / "actor_interaction_contract_validation.md"


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} must contain an object")
    return payload


def validate_contract(contract: dict[str, Any], engine_contract: dict[str, Any]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    check = lambda check_id, ok, details=None: checks.append({"id": check_id, "ok": bool(ok), "details": details})
    meta = contract.get("meta") if isinstance(contract.get("meta"), dict) else {}
    hierarchy = contract.get("constitutional_hierarchy") if isinstance(contract.get("constitutional_hierarchy"), dict) else {}
    ontology = contract.get("ontology") if isinstance(contract.get("ontology"), dict) else {}
    invariants = contract.get("canonical_invariants") if isinstance(contract.get("canonical_invariants"), list) else []
    invariant_ids = [str(row.get("id") or "") for row in invariants if isinstance(row, dict)]
    authority = contract.get("finding_authority") if isinstance(contract.get("finding_authority"), dict) else {}
    levels = authority.get("levels") if isinstance(authority.get("levels"), dict) else {}
    engine_validation = engine_contract.get("validation_contract") if isinstance(engine_contract.get("validation_contract"), dict) else {}
    expected_authorities = {str(item) for item in engine_validation.get("required_authorities", []) if str(item)}
    expected_blocking = {str(item) for item in engine_validation.get("blocking_authorities", []) if str(item)}
    profiles = contract.get("operation_profiles") if isinstance(contract.get("operation_profiles"), dict) else {}
    request_contract = contract.get("request_contract") if isinstance(contract.get("request_contract"), dict) else {}
    proposal_contract = contract.get("proposal_contract") if isinstance(contract.get("proposal_contract"), dict) else {}
    adapters = contract.get("adapter_contract") if isinstance(contract.get("adapter_contract"), dict) else {}
    trace = contract.get("trace_contract") if isinstance(contract.get("trace_contract"), dict) else {}
    schema = _load(SCHEMA_PATH) if SCHEMA_PATH.exists() else {}

    check("contract_identity", meta.get("kind") == "nexora.sage_actor_interaction_contract" and meta.get("status") == "normative_draft")
    source_paths = [hierarchy.get("supreme_human_readable_authority"), *hierarchy.get("canonical_authority_sources", [])]
    missing_sources = [str(path) for path in source_paths if not path or not (ROOT / str(path)).exists()]
    check("constitutional_sources_exist", not missing_sources, missing_sources)
    check("constitution_precedes_adapter", "constitution" in str(hierarchy.get("precedence") or "") and str(hierarchy.get("precedence") or "").endswith("adapter_projection"))
    principal_types = set(map(str, ontology.get("principal_types") or []))
    actor_types = set(map(str, ontology.get("actor_types") or []))
    adapter_types = set(map(str, ontology.get("adapter_types") or []))
    mutation_domains = ontology.get("mutation_domains") if isinstance(ontology.get("mutation_domains"), dict) else {}
    check(
        "actor_principal_adapter_ontology_is_distinct",
        bool(principal_types)
        and bool(actor_types)
        and bool(adapter_types)
        and not principal_types & actor_types
        and not actor_types & adapter_types,
    )
    check("invariant_ids_unique", len(invariant_ids) == len(set(invariant_ids)) and len(invariant_ids) >= 13)
    check("authority_vocabulary_is_canonical", set(levels) == expected_authorities, {"actual": sorted(levels), "expected": sorted(expected_authorities)})
    actual_blocking = {name for name, row in levels.items() if isinstance(row, dict) and row.get("may_block") is True}
    check("blocking_authority_matches_engine_contract", actual_blocking == expected_blocking, {"actual": sorted(actual_blocking), "expected": sorted(expected_blocking)})
    check("engine_signal_cannot_block", isinstance(levels.get("engine_signal_only"), dict) and levels["engine_signal_only"].get("may_block") is False)
    required_profiles = {"inspect", "advise", "propose", "mutate", "validate", "exception_request", "decision_support"}
    check("operation_profile_family_complete", set(profiles) == required_profiles)
    declared_profile_domains = {
        name: {str(item) for item in (row or {}).get("allowed_mutation_domains", []) if str(item)}
        for name, row in profiles.items()
    }
    domain_operations = {
        str(domain): {str(item) for item in (row or {}).get("allowed_operations", []) if str(item)}
        for domain, row in mutation_domains.items()
        if isinstance(row, dict)
    }
    expected_profile_domains = {
        operation: {domain for domain, operations in domain_operations.items() if operation in operations}
        for operation in required_profiles
    }
    check(
        "mutation_domains_are_canonical_and_operation_bound",
        bool(mutation_domains)
        and set(domain_operations) == set(mutation_domains)
        and all(operations and operations.issubset(required_profiles) for operations in domain_operations.values())
        and declared_profile_domains == expected_profile_domains,
        {"declared": {name: sorted(values) for name, values in declared_profile_domains.items()}, "expected": {name: sorted(values) for name, values in expected_profile_domains.items()}},
    )
    mutate_states = set((profiles.get("mutate") or {}).get("required_states") or [])
    check(
        "mutation_requires_authorization_execution_validation",
        (profiles.get("mutate") or {}).get("mutation_allowed") is True
        and {"AUTHORIZED", "EXECUTED", "VALIDATED"}.issubset(mutate_states),
    )
    mutation_flags_match_domains = all(
        (row or {}).get("mutation_allowed") is bool(declared_profile_domains.get(name))
        for name, row in profiles.items()
    )
    check("mutation_flags_match_explicit_domains", mutation_flags_match_domains)
    operation_required_fields = request_contract.get("operation_required_fields") if isinstance(request_contract.get("operation_required_fields"), dict) else {}
    declared_request_fields = {
        str(item)
        for item in request_contract.get("required_common_fields", [])
        if str(item)
    } | {
        str(item)
        for fields in operation_required_fields.values()
        if isinstance(fields, list)
        for item in fields
        if str(item)
    }
    check(
        "operation_specific_request_fields_cover_propose_and_mutate",
        set(operation_required_fields) == {"propose", "mutate"}
        and {"repository_snapshot", "constraints", "expected_outcome"}.issubset(set(operation_required_fields.get("propose", [])))
        and {"repository_snapshot", "authority_scope", "constraints", "expected_outcome"}.issubset(set(operation_required_fields.get("mutate", []))),
    )
    proposal_required = {str(item) for item in proposal_contract.get("required_fields", []) if str(item)}
    proposal_nonempty = {str(item) for item in proposal_contract.get("nonempty_fields", []) if str(item)}
    forbidden_authority = {str(item) for item in proposal_contract.get("forbidden_authority_fields", []) if str(item)}
    forbidden_authority_fragments = {
        str(item)
        for item in proposal_contract.get("forbidden_authority_key_fragments", [])
        if str(item)
    }
    check(
        "proposal_contract_separates_conformance_from_authority",
        {
            "proposal_id",
            "request_id",
            "actor_id",
            "repository_snapshot",
            "affected_scope",
            "required_validations",
            "interaction_state",
        }.issubset(proposal_required)
        and proposal_nonempty.issubset(proposal_required)
        and "known_risks" not in proposal_nonempty
        and {"authorized", "execution_authorized", "validated", "accepted_result"}.issubset(forbidden_authority)
        and {"approval", "authoriz", "validated", "accepted_result"}.issubset(forbidden_authority_fragments)
        and proposal_contract.get("required_state") == "PROPOSED"
        and "does not prove technical correctness" in str(proposal_contract.get("claim_boundary") or ""),
    )
    check("adapter_privilege_upgrade_prohibited", adapters.get("privilege_upgrade") == "prohibited" and adapters.get("translation_failure") == "fail_closed")
    statuses = adapters.get("implementation_status") if isinstance(adapters.get("implementation_status"), dict) else {}
    check("every_adapter_has_explicit_status", set(statuses) == adapter_types and set(statuses.values()).issubset({"available", "partial", "not_available"}))
    implementation_evidence = adapters.get("implementation_evidence") if isinstance(adapters.get("implementation_evidence"), dict) else {}
    claimed_implemented = {name for name, status in statuses.items() if status in {"available", "partial"}}
    missing_adapter_evidence = sorted(name for name in claimed_implemented if not implementation_evidence.get(name))
    missing_adapter_evidence_paths = sorted(
        str(path)
        for name in claimed_implemented
        for path in implementation_evidence.get(name, [])
        if not (ROOT / str(path)).exists()
    )
    check(
        "implemented_adapters_have_existing_evidence",
        not missing_adapter_evidence and not missing_adapter_evidence_paths,
        {"missing_adapters": missing_adapter_evidence, "missing_paths": missing_adapter_evidence_paths},
    )
    conformance_evidence = adapters.get("conformance_evidence") if isinstance(adapters.get("conformance_evidence"), dict) else {}
    required_dimensions = {
        "request_translation",
        "authority_non_escalation",
        "stale_context_rejection",
        "trace_routing",
        "validation_outcome_preservation",
    }
    declared_dimensions = {str(item) for item in conformance_evidence.get("required_promotion_dimensions", []) if str(item)}
    adapter_conformance = conformance_evidence.get("adapters") if isinstance(conformance_evidence.get("adapters"), dict) else {}
    evidence_statuses_match = set(adapter_conformance) == adapter_types and all(
        isinstance(adapter_conformance.get(name), dict)
        and adapter_conformance[name].get("status") == statuses.get(name)
        for name in adapter_types
    )
    invalid_dimension_statuses: list[str] = []
    missing_conformance_paths: list[str] = []
    available_without_proof: list[str] = []
    invalid_available_surfaces: list[str] = []
    mcp_tool_rows = (_load(MCP_TOOL_ROLES_PATH).get("tools") or {}) if MCP_TOOL_ROLES_PATH.exists() else {}
    for name, row in adapter_conformance.items():
        if not isinstance(row, dict):
            invalid_dimension_statuses.append(str(name))
            continue
        dimensions = row.get("dimensions") if isinstance(row.get("dimensions"), dict) else {}
        for dimension, evidence in dimensions.items():
            if dimension not in required_dimensions or not isinstance(evidence, dict) or evidence.get("status") not in {"pass", "partial", "fail", "not_assessed"}:
                invalid_dimension_statuses.append(f"{name}:{dimension}")
                continue
            for path in evidence.get("evidence", []):
                if not (ROOT / str(path)).exists():
                    missing_conformance_paths.append(str(path))
        if statuses.get(name) == "available":
            passed_dimensions = {
                dimension
                for dimension, evidence in dimensions.items()
                if isinstance(evidence, dict) and evidence.get("status") == "pass"
            }
            if passed_dimensions != required_dimensions:
                available_without_proof.append(str(name))
        available_surfaces = row.get("available_surfaces") if isinstance(row.get("available_surfaces"), dict) else {}
        for surface_name, surface in available_surfaces.items():
            if not isinstance(surface, dict) or surface.get("status") != "available":
                invalid_available_surfaces.append(f"{name}:{surface_name}:status")
                continue
            promoted = {str(item) for item in surface.get("promotion_dimensions", []) if str(item)}
            if promoted != required_dimensions:
                invalid_available_surfaces.append(f"{name}:{surface_name}:promotion_dimensions")
            evidence_paths = [str(path) for path in surface.get("evidence", []) if str(path)]
            if not evidence_paths or any(not (ROOT / path).exists() for path in evidence_paths):
                invalid_available_surfaces.append(f"{name}:{surface_name}:evidence")
            operations = {str(item) for item in surface.get("operation_profiles", []) if str(item)}
            if not operations or not operations.issubset(profiles):
                invalid_available_surfaces.append(f"{name}:{surface_name}:operation_profiles")
            eligible_tools = {str(item) for item in surface.get("eligible_tools", []) if str(item)}
            if name == "mcp":
                if surface_name not in mcp_tool_rows:
                    invalid_available_surfaces.append(f"{name}:{surface_name}:surface_registry")
                if not eligible_tools:
                    invalid_available_surfaces.append(f"{name}:{surface_name}:eligible_tools")
                for tool_name in eligible_tools:
                    tool_row = mcp_tool_rows.get(tool_name) if isinstance(mcp_tool_rows, dict) else None
                    actor_contract = tool_row.get("actor_contract") if isinstance(tool_row, dict) else None
                    declared_operations = {
                        str(item) for item in actor_contract.get("operations", []) if str(item)
                    } if isinstance(actor_contract, dict) else set()
                    required_scope_keys = {
                        str(item) for item in actor_contract.get("required_scope_keys", []) if str(item)
                    } if isinstance(actor_contract, dict) else set()
                    scope_bindings = actor_contract.get("scope_bindings") if isinstance(actor_contract, dict) else None
                    request_bindings = actor_contract.get("request_bindings") if isinstance(actor_contract, dict) and isinstance(actor_contract.get("request_bindings"), dict) else {}
                    fixed_arguments = actor_contract.get("fixed_arguments") if isinstance(actor_contract, dict) else None
                    result_status_map = actor_contract.get("result_status_map") if isinstance(actor_contract, dict) else None
                    result_encoding = str(actor_contract.get("result_encoding") or "") if isinstance(actor_contract, dict) else ""
                    tool_mutation_domains = {
                        str(item) for item in tool_row.get("mutation_domains", []) if str(item)
                    } if isinstance(tool_row, dict) else set()
                    mapped_outcomes = {
                        str(value) for value in result_status_map.values() if str(value)
                    } if isinstance(result_status_map, dict) else set()
                    unknown_outcome = str(actor_contract.get("unknown_result_state") or "") if isinstance(actor_contract, dict) else ""
                    allowed_terminal_states = {
                        str(state)
                        for operation in declared_operations
                        for state in (profiles.get(operation) or {}).get("terminal_states", [])
                        if str(state)
                    }
                    binding_scope_keys = {
                        str(binding.get("scope_key") or "")
                        for binding in scope_bindings.values()
                        if isinstance(binding, dict)
                    } if isinstance(scope_bindings, dict) else set()
                    allowed_tool_mutation_domains = {
                        domain
                        for operation in declared_operations
                        for domain in declared_profile_domains.get(operation, set())
                    }
                    mutation_domain_contract_valid = isinstance(tool_row, dict) and (
                        (
                            bool(tool_row.get("mutates"))
                            and bool(tool_mutation_domains)
                            and tool_mutation_domains.issubset(allowed_tool_mutation_domains)
                        )
                        or (not bool(tool_row.get("mutates")) and not tool_mutation_domains)
                    )
                    if (
                        not isinstance(tool_row, dict)
                        or not declared_operations
                        or not declared_operations.issubset(operations)
                        or not required_scope_keys
                        or not isinstance(scope_bindings, dict)
                        or not scope_bindings
                        or not binding_scope_keys.issubset(required_scope_keys)
                        or not {str(value) for value in request_bindings.values()}.issubset(declared_request_fields)
                        or not isinstance(fixed_arguments, dict)
                        or (fixed_arguments.get("format") not in {"json", "machine"} and result_encoding != "json")
                        or not mutation_domain_contract_valid
                        or not isinstance(result_status_map, dict)
                        or not result_status_map
                        or not mapped_outcomes.issubset(allowed_terminal_states)
                        or unknown_outcome not in allowed_terminal_states
                    ):
                        invalid_available_surfaces.append(f"{name}:{surface_name}:eligible_tool:{tool_name}")
    check(
        "adapter_conformance_evidence_is_complete_and_status_bound",
        declared_dimensions == required_dimensions
        and evidence_statuses_match
        and not invalid_dimension_statuses
        and not missing_conformance_paths,
        {
            "declared_dimensions": sorted(declared_dimensions),
            "expected_dimensions": sorted(required_dimensions),
            "statuses_match": evidence_statuses_match,
            "invalid_dimension_statuses": sorted(invalid_dimension_statuses),
            "missing_paths": sorted(set(missing_conformance_paths)),
        },
    )
    check(
        "available_adapter_requires_all_promotion_dimensions",
        not available_without_proof,
        {"adapters": sorted(available_without_proof)},
    )
    check(
        "available_scoped_surface_requires_registry_bound_proof",
        not invalid_available_surfaces,
        {"surfaces": sorted(set(invalid_available_surfaces))},
    )
    trace_sources = trace.get("sources") if isinstance(trace.get("sources"), dict) else {}
    check(
        "trace_authority_is_separated_and_privacy_is_bounded",
        trace_sources.get("operational_events") == "config/governance_trace_contract.json"
        and trace_sources.get("human_authority_decisions") == "config/hitl_governance_contract.json"
        and trace.get("narrative_is_not_trace") is True
        and "raw source" in str(trace.get("privacy_rule") or ""),
    )
    schema_required = {str(item) for item in schema.get("required", []) if str(item)}
    schema_properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    missing_schema_fields = sorted(schema_required - set(contract))
    missing_section_fields = {
        section: sorted({str(item) for item in spec.get("required", []) if str(item)} - set(contract.get(section, {})))
        for section, spec in schema_properties.items()
        if isinstance(spec, dict) and isinstance(contract.get(section), dict)
    }
    missing_section_fields = {key: value for key, value in missing_section_fields.items() if value}
    check(
        "schema_required_fields_are_satisfied",
        bool(schema) and not missing_schema_fields and not missing_section_fields,
        {"missing_root": missing_schema_fields, "missing_sections": missing_section_fields},
    )
    check("generated_doc_matches_contract", DOC_PATH.exists() and DOC_PATH.read_text(encoding="utf-8") == render(contract))
    return checks


def run() -> dict[str, Any]:
    try:
        contract = _load(CONTRACT_PATH)
        checks = validate_contract(contract, _load(ENGINE_CONTRACT_PATH))
        contract_sha256 = hashlib.sha256(CONTRACT_PATH.read_bytes()).hexdigest()
    except Exception as exc:
        checks = [{"id": "contract_readable", "ok": False, "details": str(exc)}]
        contract_sha256 = "not_available"
    failed = [row["id"] for row in checks if not row.get("ok")]
    return {
        "validator": "actor_interaction_contract",
        "status": "PASS" if not failed else "FAIL",
        "claim_boundary": (
            "PASS proves contract structure, canonical authority alignment and generated-document parity; "
            "it does not prove every adapter implements full conformance or grant an actor authority."
        ),
        "contract_sha256": contract_sha256,
        "checks": checks,
        "failed_checks": failed,
    }


if __name__ == "__main__":
    started = time.perf_counter()
    result = run()
    save_json_atomic(RAW_PATH, result)
    lines = ["# Actor Interaction Contract Validation", "", f"status: `{result['status']}`", "", result["claim_boundary"], ""]
    lines.extend(f"- {'PASS' if row.get('ok') else 'FAIL'}: `{row['id']}`" for row in result["checks"])
    save_text_atomic(REPORT_PATH, "\n".join(lines) + "\n")
    record_work_package_operation_safely(
        operation_id="actor_interaction_contract_validation",
        result_status=result["status"],
        started=started,
        evidence_artifact="output/.raw/actor_interaction_contract_validation.json",
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    raise SystemExit(0 if result["status"] == "PASS" else 1)
