from __future__ import annotations

from collections import Counter
from typing import Any

from tools.core.capability_registry import load_capability_registry, summarize_capabilities
from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.logger import logger


def run_capability_roadmap_report() -> dict[str, Any]:
    logger.info("Building capability roadmap report...")
    registry = load_capability_registry()
    validation_contract = registry.get("_meta", {}).get("validation_contract", {})
    activation_contract_source = (
        validation_contract.get("roadmap_activation_contract", {})
        if isinstance(validation_contract, dict)
        else {}
    )
    required_conditions = (
        activation_contract_source.get("required_conditions", [])
        if isinstance(activation_contract_source, dict)
        else []
    )
    activation_contract = {
        f"requires_{condition}": True
        for condition in required_conditions
        if str(condition).strip()
    }
    release_claim_rule = str(activation_contract_source.get("release_claim_rule") or "").strip()
    contract_errors: list[str] = []
    if not activation_contract:
        contract_errors.append("roadmap_activation_contract.required_conditions_missing")
    if not release_claim_rule:
        contract_errors.append("roadmap_activation_contract.release_claim_rule_missing")
    summary = summarize_capabilities(registry)
    capabilities = summary.get("capabilities", [])
    roadmap = [
        {
            "id": capability.get("id"),
            "title": capability.get("title"),
            "domain": capability.get("domain"),
            "language_scope": capability.get("language_scope", []),
            "framework_scope": capability.get("framework_scope", []),
            "target_release": capability.get("target_release"),
            "priority": capability.get("priority"),
            "effort": capability.get("effort"),
            "value": capability.get("value"),
            "current_state": capability.get("current_state"),
            "roadmap_scope": capability.get("roadmap_scope"),
            "rationale": capability.get("rationale"),
            "claim_boundary": capability.get("claim_boundary"),
            "activation_contract": activation_contract,
        }
        for capability in capabilities
        if capability.get("maturity") == "roadmap"
    ]
    target_release_counts = dict(
        sorted(Counter(str(item.get("target_release") or "unassigned") for item in roadmap).items())
    )
    priority_counts = dict(
        sorted(Counter(str(item.get("priority") or "unassigned") for item in roadmap).items())
    )
    active = [
        capability.get("id")
        for capability in capabilities
        if capability.get("maturity") == "production_candidate"
    ]
    payload = {
        "meta": {
            "kind": "capability_roadmap",
            "version": "1.0.0",
            "source": "config/capability_registry.json",
        },
        "summary": {
            "status": "PASS" if not contract_errors else "FAIL",
            "active_capabilities": len(active),
            "roadmap_capabilities": len(roadmap),
            "roadmap_ids": [item["id"] for item in roadmap],
            "target_release_counts": target_release_counts,
            "priority_counts": priority_counts,
            "contract_errors": contract_errors,
        },
        "activation_contract_source": {
            "required_conditions": list(required_conditions) if isinstance(required_conditions, list) else [],
            "release_claim_rule": release_claim_rule,
        },
        "active_capability_ids": active,
        "roadmap": roadmap,
    }
    save_json_atomic(RAW_DIR / "capability_roadmap.json", payload)
    _write_report(payload)
    return payload


def _write_report(payload: dict[str, Any]) -> None:
    summary = payload.get("summary", {})
    lines = [
        "# Capability Roadmap",
        "",
        "Machine-readable roadmap derived from `config/capability_registry.json`.",
        "",
        f"- status: `{summary.get('status')}`",
        f"- active_capabilities: `{summary.get('active_capabilities')}`",
        f"- roadmap_capabilities: `{summary.get('roadmap_capabilities')}`",
        f"- target_release_counts: `{summary.get('target_release_counts')}`",
        f"- priority_counts: `{summary.get('priority_counts')}`",
        "",
        "| Roadmap Capability | Phase | Priority | Effort | Value | Current State | Roadmap Scope |",
        "|---|---|---|---|---|---|---|",
    ]
    for item in payload.get("roadmap", []):
        current = str(item.get("current_state") or "").replace("|", "\\|")
        scope = str(item.get("roadmap_scope") or "").replace("|", "\\|")
        lines.append(
            f"| `{item.get('id')}` | `{item.get('target_release')}` | `{item.get('priority')}` | "
            f"`{item.get('effort')}` | `{item.get('value')}` | {current} | {scope} |"
        )
    lines.extend(
        [
            "",
            "## Activation Contract",
            "",
            str(payload.get("activation_contract_source", {}).get("release_claim_rule") or ""),
        ]
    )
    save_text_atomic(REPORTS_DIR / "capability_roadmap.md", "\n".join(lines) + "\n")


if __name__ == "__main__":
    payload = run_capability_roadmap_report()
    raise SystemExit(0 if payload.get("summary", {}).get("status") == "PASS" else 1)
