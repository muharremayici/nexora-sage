"""Render the human-readable actor interaction contract from its JSON authority."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.config import save_text_atomic


CONTRACT_PATH = ROOT / "config" / "actor_interaction_contract.json"
DOC_PATH = ROOT / "docs" / "SAGE_ACTOR_INTERACTION_CONTRACT.md"


def load_contract() -> dict[str, Any]:
    payload = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("actor interaction contract must be an object")
    return payload


def render(contract: dict[str, Any]) -> str:
    meta = contract["meta"]
    hierarchy = contract["constitutional_hierarchy"]
    ontology = contract["ontology"]
    boundary = contract["sage_boundary"]
    lines = [
        "# SAGE Actor Interaction Contract",
        "",
        "> Generated from `config/actor_interaction_contract.json`. Do not edit this document directly.",
        f"> Contract SHA-256: `{hashlib.sha256(CONTRACT_PATH.read_bytes()).hexdigest()}`",
        "",
        f"Version: `{meta['version']}`",
        f"Status: `{meta['status']}`",
        "",
        "## Purpose",
        "",
        str(meta["purpose"]),
        "",
        f"Claim boundary: {meta['claim_boundary']}",
        "",
        "## Constitutional Position",
        "",
        f"- Supreme authority: `{hierarchy['supreme_human_readable_authority']}`",
        f"- Precedence: `{hierarchy['precedence']}`",
        f"- Conflict rule: {hierarchy['conflict_rule']}",
        "",
        "## Actor Model",
        "",
        f"- Principals: {', '.join(f'`{item}`' for item in ontology['principal_types'])}",
        f"- Actors: {', '.join(f'`{item}`' for item in ontology['actor_types'])}",
        f"- Adapters: {', '.join(f'`{item}`' for item in ontology['adapter_types'])}",
        "",
    ]
    lines.extend(f"- {rule}" for rule in ontology["separation_rules"])
    lines.extend(["", "## SAGE Boundary", "", "SAGE is:", ""])
    lines.extend(f"- `{item}`" for item in boundary["is"])
    lines.extend(["", "SAGE is not:", ""])
    lines.extend(f"- `{item}`" for item in boundary["is_not"])
    lines.extend(["", f"Capability honesty: {boundary['capability_honesty_rule']}", "", "## Canonical Invariants", ""])
    for row in contract["canonical_invariants"]:
        lines.append(f"- **{row['level']} `{row['id']}`:** {row['rule']}")
    lines.extend(["", "## Finding Authority", ""])
    for authority, row in contract["finding_authority"]["levels"].items():
        lines.append(f"- `{authority}`: {row['meaning']} Blocking capability: `{str(row['may_block']).lower()}`.")
    lines.extend(["", "## Operation Profiles", ""])
    for name, row in contract["operation_profiles"].items():
        states = " -> ".join(row["required_states"])
        terminals = ", ".join(row["terminal_states"])
        domains = ", ".join(row["allowed_mutation_domains"]) or "none"
        lines.append(f"- `{name}`: state mutation `{str(row['mutation_allowed']).lower()}`; allowed mutation domains `{domains}`; required flow `{states}`; terminals `{terminals}`.")
    lines.extend(["", "## Freshness", ""])
    lines.append("Invalidation signals: " + ", ".join(f"`{item}`" for item in contract["freshness_contract"]["invalidated_by"]) + ".")
    lines.append("")
    lines.append("Required response: " + " -> ".join(f"`{item}`" for item in contract["freshness_contract"]["on_invalidation"]) + ".")
    lines.extend(["", "## Request Contract", ""])
    lines.append("Common fields: " + ", ".join(f"`{item}`" for item in contract["request_contract"]["required_common_fields"]) + ".")
    lines.append("")
    for operation, fields in contract["request_contract"]["operation_required_fields"].items():
        lines.append(f"Additional `{operation}` fields: " + ", ".join(f"`{item}`" for item in fields) + ".")
    lines.append("")
    lines.extend(f"- {rule}" for rule in contract["request_contract"]["rules"])
    proposal = contract["proposal_contract"]
    lines.extend(["", "## Proposal Contract", ""])
    lines.append("Required fields: " + ", ".join(f"`{item}`" for item in proposal["required_fields"]) + ".")
    lines.append("")
    lines.append("Non-empty fields: " + ", ".join(f"`{item}`" for item in proposal["nonempty_fields"]) + ".")
    lines.append("")
    lines.append(
        "Forbidden authority-key fragments: "
        + ", ".join(f"`{item}`" for item in proposal["forbidden_authority_key_fragments"])
        + "."
    )
    lines.append("")
    lines.append(f"Required interaction state: `{proposal['required_state']}`.")
    lines.append("")
    lines.append(f"Scope rule: {proposal['scope_rule']}")
    lines.append("")
    lines.append(f"Identity rule: {proposal['identity_rule']}")
    lines.append("")
    lines.append(f"Claim boundary: {proposal['claim_boundary']}")
    lines.extend(["", "## Adapter Status", ""])
    for adapter, status in contract["adapter_contract"]["implementation_status"].items():
        evidence = contract["adapter_contract"]["implementation_evidence"].get(adapter, [])
        evidence_text = ", ".join(f"`{item}`" for item in evidence) or "no implementation evidence"
        lines.append(f"- `{adapter}`: `{status}`; {evidence_text}")
    lines.extend(["", "Adapter conformance:", ""])
    conformance_adapters = contract["adapter_contract"]["conformance_evidence"]["adapters"]
    for adapter, row in conformance_adapters.items():
        dimensions = row.get("dimensions", {}) if isinstance(row, dict) else {}
        dimension_text = ", ".join(
            f"`{name}={details.get('status', 'not_assessed')}`"
            for name, details in dimensions.items()
            if isinstance(details, dict)
        ) or "no assessed promotion dimensions"
        surfaces = row.get("available_surfaces", {}) if isinstance(row, dict) else {}
        surface_text = ", ".join(f"`{name}`" for name in surfaces) or "none"
        lines.append(
            f"- `{adapter}`: `{row.get('status', 'not_available')}`; dimensions: {dimension_text}; "
            f"available surfaces: {surface_text}. Claim boundary: {row.get('claim_boundary', 'not declared')}"
        )
    lines.extend(["", f"Promotion rule: {contract['adapter_contract']['promotion_rule']}"])
    lines.extend(["", "## Trace And Privacy", "", contract["trace_contract"]["privacy_rule"], ""])
    for trace_kind, source in contract["trace_contract"]["sources"].items():
        lines.append(f"- `{trace_kind}`: `{source}`")
    lines.append("")
    lines.append("A generated narrative is not the canonical trace. Structured events and evidence identifiers are authoritative within their declared scope.")
    lines.extend(["", "## Conformance", "", "Minimum conformance:", ""])
    lines.extend(f"- `{item}`" for item in contract["conformance"]["minimum"])
    lines.extend(["", "Full conformance additionally requires:", ""])
    lines.extend(f"- `{item}`" for item in contract["conformance"]["full"])
    lines.extend(["", "## Canonical Summary", ""])
    lines.extend([
        "Actors may reason, navigate, propose, modify or decide according to their role and granted authority.",
        "SAGE supplies deterministic context, constraints, evidence and validation.",
        "Neither actor fluency nor execution success may replace engineering proof.",
        "",
    ])
    return "\n".join(lines)


def run() -> None:
    save_text_atomic(DOC_PATH, render(load_contract()))
    print(f"[actor-contract] wrote {DOC_PATH.relative_to(ROOT).as_posix()}")


if __name__ == "__main__":
    run()
