from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.capability_registry import iter_capabilities, load_capability_registry
from tools.core.config import CONFIG_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file

VOCABULARY_PATH = CONFIG_DIR / "evidence_vocabulary.json"
CONTRACT_PATH = CONFIG_DIR / "engine_signal_contracts.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check(name: str, passed: bool, details: Any = None) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _ids(rows: list[Any]) -> set[str]:
    values: set[str] = set()
    for row in rows:
        if isinstance(row, dict):
            value = row.get("id")
        else:
            value = row
        if isinstance(value, str) and value.strip():
            values.add(value.strip())
    return values


def _contract_set(contract: dict[str, Any], field: str) -> set[str]:
    values = contract.get(field, [])
    return {str(item) for item in values if str(item).strip()} if isinstance(values, list) else set()


def _load_payload(path: Path) -> dict[str, Any]:
    payload = load_json_file(path, {})
    return payload if isinstance(payload, dict) else {}


def validate_engine_signal_contracts() -> dict[str, Any]:
    vocabulary = _load_payload(VOCABULARY_PATH)
    contracts_payload = _load_payload(CONTRACT_PATH)
    validation_contract = (
        contracts_payload.get("validation_contract", {})
        if isinstance(contracts_payload.get("validation_contract"), dict)
        else {}
    )
    registry = load_capability_registry()

    confidence_levels = _ids(_as_list(vocabulary.get("confidence_levels")))
    evidence_kinds = _ids(_as_list(vocabulary.get("evidence_kinds")))
    runtime_statuses = _ids(_as_list(vocabulary.get("runtime_proof_statuses")))
    actionability = _ids(_as_list(vocabulary.get("actionability_levels")))
    authorities = _ids(_as_list(vocabulary.get("decision_authorities")))
    guardrails = vocabulary.get("guardrails") if isinstance(vocabulary.get("guardrails"), dict) else {}
    valid_emits = _contract_set(validation_contract, "valid_emits")
    valid_gate_effects = _contract_set(validation_contract, "valid_gate_effects")
    required_confidence = _contract_set(validation_contract, "required_confidence")
    required_evidence_terms = _contract_set(validation_contract, "required_evidence")
    required_authorities = _contract_set(validation_contract, "required_authorities")
    required_actionability = _contract_set(validation_contract, "required_actionability")
    blocking_authorities = _contract_set(validation_contract, "blocking_authorities")
    roadmap_boundary_markers = _contract_set(validation_contract, "roadmap_claim_boundary_markers")

    capabilities = {str(item.get("id")): item for item in iter_capabilities(registry) if item.get("id")}
    production_or_mvp = {
        cap_id
        for cap_id, cap in capabilities.items()
        if cap.get("maturity") in {"production_candidate", "policy_backed_mvp"}
        or any(str(artifact).endswith("_validation") for artifact in cap.get("artifacts", []) or [])
    }
    contracts = [item for item in _as_list(contracts_payload.get("contracts")) if isinstance(item, dict)]
    contract_by_capability = {str(item.get("capability_id")): item for item in contracts if item.get("capability_id")}

    unknown_capabilities: list[str] = []
    invalid_contracts: list[dict[str, Any]] = []
    dangerous_gate_contracts: list[dict[str, Any]] = []
    runtime_mismatches: list[dict[str, Any]] = []
    roadmap_claim_mismatches: list[dict[str, Any]] = []
    missing_required_contracts = sorted(production_or_mvp - set(contract_by_capability))

    for contract in contracts:
        cap_id = str(contract.get("capability_id") or "")
        capability = capabilities.get(cap_id)
        if not capability:
            unknown_capabilities.append(cap_id)

        emits = str(contract.get("emits") or "")
        authority = str(contract.get("decision_authority") or "")
        gate_effect = str(contract.get("quality_gate_effect") or "")
        runtime_status = str(contract.get("runtime_proof_status") or "")
        allowed_confidence = {str(item) for item in _as_list(contract.get("allowed_confidence"))}
        contract_required_evidence = {str(item) for item in _as_list(contract.get("required_evidence_kinds"))}

        errors: list[str] = []
        if emits not in valid_emits:
            errors.append(f"invalid_emits:{emits}")
        if authority not in authorities:
            errors.append(f"invalid_decision_authority:{authority}")
        if gate_effect not in valid_gate_effects:
            errors.append(f"invalid_quality_gate_effect:{gate_effect}")
        if runtime_status not in runtime_statuses:
            errors.append(f"invalid_runtime_proof_status:{runtime_status}")
        unknown_conf = sorted(allowed_confidence - confidence_levels)
        if unknown_conf:
            errors.append(f"unknown_confidence:{','.join(unknown_conf)}")
        unknown_evidence = sorted(contract_required_evidence - evidence_kinds)
        if unknown_evidence:
            errors.append(f"unknown_evidence:{','.join(unknown_evidence)}")
        if not str(contract.get("claim_scope") or "").strip():
            errors.append("empty_claim_scope")
        if errors:
            invalid_contracts.append({"capability_id": cap_id, "errors": errors})

        if guardrails.get("engine_signal_only_must_not_block") and authority == "engine_signal_only" and gate_effect == "block":
            dangerous_gate_contracts.append({"capability_id": cap_id, "reason": "engine_signal_only_blocks"})
        if gate_effect == "block" and authority not in blocking_authorities:
            dangerous_gate_contracts.append({"capability_id": cap_id, "reason": "block_without_decision_authority"})
        if runtime_status == "needs_runtime_proof" and "needs_runtime_proof" not in allowed_confidence:
            runtime_mismatches.append({"capability_id": cap_id, "reason": "runtime_sensitive_without_confidence_label"})
        if capability and capability.get("maturity") == "roadmap":
            claim_scope = str(contract.get("claim_scope") or "").lower()
            claim_boundary = str(capability.get("claim_boundary") or "").lower()
            normalized_boundary = claim_boundary.replace("-", " ")
            boundary_marks_roadmap = any(marker in normalized_boundary for marker in roadmap_boundary_markers)
            if "not_v1_scope" not in allowed_confidence or not boundary_marks_roadmap:
                roadmap_claim_mismatches.append(
                    {
                        "capability_id": cap_id,
                        "reason": "roadmap_contract_must_not_enter_v1_claims",
                        "allowed_confidence": sorted(allowed_confidence),
                        "claim_boundary": capability.get("claim_boundary"),
                    }
                )
            if "not_v1" not in claim_scope and "not_v1_scope" not in allowed_confidence:
                roadmap_claim_mismatches.append({"capability_id": cap_id, "reason": "claim_scope_not_marked_roadmap"})

    checks = [
        _check("evidence_vocabulary_exists", VOCABULARY_PATH.exists(), str(VOCABULARY_PATH)),
        _check("engine_signal_contracts_exists", CONTRACT_PATH.exists(), str(CONTRACT_PATH)),
        _check(
            "engine_signal_validation_contract_is_declared",
            bool(valid_emits)
            and bool(valid_gate_effects)
            and bool(required_confidence)
            and bool(required_evidence_terms)
            and bool(required_authorities)
            and bool(required_actionability)
            and bool(blocking_authorities)
            and bool(roadmap_boundary_markers),
            validation_contract,
        ),
        _check("confidence_vocabulary_has_required_terms", required_confidence.issubset(confidence_levels), sorted(required_confidence - confidence_levels)),
        _check("evidence_vocabulary_has_required_terms", required_evidence_terms.issubset(evidence_kinds), sorted(required_evidence_terms - evidence_kinds)),
        _check("decision_authorities_are_declared", required_authorities.issubset(authorities), sorted(required_authorities - authorities)),
        _check("actionability_levels_are_declared", required_actionability.issubset(actionability), sorted(required_actionability - actionability)),
        _check("contracts_cover_release_relevant_capabilities", not missing_required_contracts, missing_required_contracts),
        _check("contracts_reference_known_capabilities", not unknown_capabilities, unknown_capabilities),
        _check("contracts_use_known_vocabulary", not invalid_contracts, invalid_contracts),
        _check("engine_signals_do_not_block_directly", not dangerous_gate_contracts, dangerous_gate_contracts),
        _check("runtime_sensitive_contracts_require_runtime_label", not runtime_mismatches, runtime_mismatches),
        _check("roadmap_capabilities_stay_out_of_v1_claims", not roadmap_claim_mismatches, roadmap_claim_mismatches),
    ]
    status = "PASS" if all(check["passed"] for check in checks) else "FAIL"
    summary = {
        "status": status,
        "checks": len(checks),
        "passed": sum(1 for check in checks if check["passed"]),
        "capabilities": len(capabilities),
        "contracts": len(contracts),
        "release_relevant_capabilities": len(production_or_mvp),
        "confidence_terms": len(confidence_levels),
        "evidence_kinds": len(evidence_kinds),
    }
    payload = {
        "meta": {
            "kind": "engine_signal_contract_validation",
            "version": "v1",
            "generated_at": _utc_now(),
            "generator": "tools.validate_engine_signal_contracts",
            "sources": ["config/evidence_vocabulary.json", "config/engine_signal_contracts.json", "config/capability_registry.json"],
        },
        "summary": summary,
        "checks": checks,
        "contract_summary": [
            {
                "capability_id": contract.get("capability_id"),
                "emits": contract.get("emits"),
                "decision_authority": contract.get("decision_authority"),
                "quality_gate_effect": contract.get("quality_gate_effect"),
                "runtime_proof_status": contract.get("runtime_proof_status"),
                "claim_scope": contract.get("claim_scope"),
            }
            for contract in contracts
        ],
    }
    save_json_atomic(RAW_DIR / "engine_signal_contract_validation.json", payload)
    save_text_atomic(REPORTS_DIR / "engine_signal_contract_validation.md", render_report(payload))
    return payload


def render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# Engine Signal Contract Validation",
        "",
        "Validates the boundary between engine signals, calibrated evidence, governance verdicts and human-approved actions.",
        "",
        f"- status: `{summary.get('status')}`",
        f"- contracts: `{summary.get('contracts')}`",
        f"- release_relevant_capabilities: `{summary.get('release_relevant_capabilities')}`",
        f"- evidence_kinds: `{summary.get('evidence_kinds')}`",
        "",
        "| Check | Passed |",
        "|---|---|",
    ]
    for check in payload.get("checks", []):
        lines.append(f"| `{check.get('name')}` | `{check.get('passed')}` |")
    lines.extend(["", "## Contract Summary", "", "| Capability | Emits | Authority | Gate | Runtime |", "|---|---|---|---|---|"])
    for row in payload.get("contract_summary", []):
        lines.append(
            "| `{capability_id}` | `{emits}` | `{decision_authority}` | `{quality_gate_effect}` | `{runtime_proof_status}` |".format(
                **{key: row.get(key, "") for key in ("capability_id", "emits", "decision_authority", "quality_gate_effect", "runtime_proof_status")}
            )
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    payload = validate_engine_signal_contracts()
    print(json.dumps(payload.get("summary", {}), ensure_ascii=False))
    return 0 if payload.get("summary", {}).get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
