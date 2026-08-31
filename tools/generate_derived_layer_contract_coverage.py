"""Report advisory layer-evidence drift from existing declarative contracts."""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_object_strict
from tools.core.source_layer_classifier import classify_source_layer


POLICY_PATH = ROOT / "config" / "derived_layer_contract_coverage_policy.json"
RAW_PATH = RAW_DIR / "derived_layer_contract_coverage.json"
REPORT_PATH = REPORTS_DIR / "derived_layer_contract_coverage.md"


def _contract_path(policy: dict[str, Any], key: str) -> Path:
    path = str((policy.get("inputs") or {}).get(key) or "")
    if not path:
        raise ValueError(f"coverage policy missing inputs.{key}")
    candidate = ROOT / path
    if not candidate.is_file() or not candidate.is_relative_to(ROOT):
        raise ValueError(f"coverage policy input must be an existing local file: {path}")
    return candidate


def _source_layer(path_text: str, exclusions: set[str]) -> str | None:
    path = ROOT / path_text
    if not path.is_file() or not path.is_relative_to(ROOT):
        return None
    layer, _reason = classify_source_layer(path, root=ROOT)
    return None if layer in exclusions else layer


def _suggestion(category: str, entity_id: str, source: str, proof_id: str, layer: str, owner_layers: set[str]) -> dict[str, Any]:
    return {
        "category": category,
        "entity_id": entity_id,
        "source": source,
        "source_layer": layer,
        "missing_layer_projection": "proof_ids",
        "suggested_value": proof_id,
        "existing_owner_layers": sorted(owner_layers),
        "disposition": "manual_review_required" if not owner_layers else "cross_layer_realization",
        "action": "Review the suggested layer projection; do not apply it automatically or treat it as a claim/seal change.",
    }


def _aggregate_observations(rows: list[dict[str, Any]], category_priority: list[str]) -> list[dict[str, Any]]:
    """Keep one candidate per proof/source and retain every independent observation."""
    priority = {category: index for index, category in enumerate(category_priority)}
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        key = (str(row["source"]), str(row["source_layer"]), str(row["suggested_value"]))
        grouped.setdefault(key, []).append(row)
    aggregated: list[dict[str, Any]] = []
    for observed in grouped.values():
        primary = min(observed, key=lambda row: priority.get(str(row["category"]), len(priority)))
        merged = dict(primary)
        merged["observed_categories"] = sorted({str(row["category"]) for row in observed}, key=lambda value: priority.get(value, len(priority)))
        merged["observed_entity_ids"] = sorted({str(row["entity_id"]) for row in observed})
        aggregated.append(merged)
    return aggregated


def _triage_decisions(policy: dict[str, Any]) -> dict[tuple[str, str, str], dict[str, str]]:
    triage = load_json_object_strict(_contract_path(policy, "triage_contract"), label="Derived layer coverage triage")
    rows = triage.get("decisions")
    if not isinstance(rows, list):
        raise ValueError("derived layer coverage triage decisions must be a list")
    decisions: dict[tuple[str, str, str], dict[str, str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("derived layer coverage triage decision must be an object")
        source = str(row.get("source") or "")
        layer = str(row.get("source_layer") or "")
        proof_id = str(row.get("proof_id") or "")
        rationale = str(row.get("rationale") or "")
        disposition = str(row.get("disposition") or "")
        if not source or not layer or not proof_id or not rationale or disposition != "supporting_layer_evidence":
            raise ValueError("derived layer coverage triage decision requires source, source_layer, proof_id, rationale and supporting disposition")
        key = (source, layer, proof_id)
        if key in decisions:
            raise ValueError(f"duplicate derived layer coverage triage decision: {source} -> {proof_id}")
        decisions[key] = {"id": str(row.get("id") or ""), "rationale": rationale}
    return decisions


def build_report() -> dict[str, Any]:
    policy = load_json_object_strict(POLICY_PATH, label="Derived layer coverage policy")
    mode = str((policy.get("policy") or {}).get("mode") or "")
    if mode != "advisory_only" or (policy.get("policy") or {}).get("automatic_matrix_mutation") is not False:
        raise ValueError("derived coverage policy must remain advisory-only with automatic matrix mutation disabled")
    exclusions = {str(item) for item in (policy.get("policy") or {}).get("source_layer_exclusions", [])}
    background_layers = {str(item) for item in (policy.get("policy") or {}).get("background_only_source_layers", [])}
    category_priority = [str(item) for item in (policy.get("policy") or {}).get("category_priority", [])]
    triage_decisions = _triage_decisions(policy)
    matrix = load_json_object_strict(_contract_path(policy, "layer_matrix_contract"), label="Layer release matrix contract")
    release = load_json_object_strict(_contract_path(policy, "release_proof_contract"), label="Release proof contract")
    spine = load_json_object_strict(_contract_path(policy, "system_spine_registry"), label="System spine registry")
    governance = load_json_object_strict(_contract_path(policy, "governance_registry"), label="Governance registry")
    layer_rules = matrix.get("layer_rules") if isinstance(matrix.get("layer_rules"), dict) else {}
    proof_owner_layers: dict[str, set[str]] = {}
    for layer_id, rule in layer_rules.items():
        if isinstance(rule, dict):
            for proof_id in rule.get("proof_ids", []):
                proof_owner_layers.setdefault(str(proof_id), set()).add(str(layer_id))

    suggestions: list[dict[str, str]] = []
    for step in release.get("steps", []):
        if not isinstance(step, dict):
            continue
        proof_id = str(step.get("id") or "")
        command = step.get("command") if isinstance(step.get("command"), list) else []
        paths = [str(part).split("${code_maps}/", 1)[1] for part in command if isinstance(part, str) and "${code_maps}/" in part]
        for source in paths:
            layer = _source_layer(source, exclusions)
            rule = layer_rules.get(layer, {}) if layer else {}
            if layer and proof_id and proof_id not in set(rule.get("proof_ids", [])):
                suggestions.append(_suggestion("release_step_missing_layer_proof", proof_id, source, proof_id, layer, proof_owner_layers.get(proof_id, set())))

    for category, payload, rows_key in (
        ("spine_node_missing_layer_proof", spine, "spine_nodes"),
        ("governance_surface_missing_layer_proof", governance, "governance_surfaces"),
    ):
        for row in payload.get(rows_key, []):
            if not isinstance(row, dict):
                continue
            proof_id = str(row.get("release_proof_step") or "")
            source = str(row.get("source") or "")
            layer = _source_layer(source, exclusions)
            rule = layer_rules.get(layer, {}) if layer else {}
            if layer and proof_id and proof_id not in set(rule.get("proof_ids", [])):
                suggestions.append(_suggestion(category, str(row.get("id") or ""), source, proof_id, layer, proof_owner_layers.get(proof_id, set())))

    aggregated = _aggregate_observations(suggestions, category_priority)
    observed_keys = {(str(row["source"]), str(row["source_layer"]), str(row["suggested_value"])) for row in aggregated}
    stale_decisions = sorted(set(triage_decisions) - observed_keys)
    if stale_decisions:
        raise ValueError(f"derived layer coverage triage has stale decisions: {stale_decisions}")
    for row in aggregated:
        decision = triage_decisions.get((str(row["source"]), str(row["source_layer"]), str(row["suggested_value"])))
        if decision:
            row["disposition"] = "supporting_layer_evidence"
            row["triage_decision"] = decision
    visible = [row for row in aggregated if row["source_layer"] not in background_layers and row["disposition"] == "manual_review_required"]
    background = [row for row in aggregated if row not in visible]
    category_counts = dict(sorted(Counter(category for row in aggregated for category in row["observed_categories"]).items()))
    visible_by_layer = dict(sorted(Counter(row["source_layer"] for row in visible).items()))
    return {
        "meta": {"kind": "derived_layer_contract_coverage", "version": "v1", "claim_boundary": "Advisory candidates only; this report does not modify layer ownership, release claims or human seals."},
        "summary": {"status": "ATTENTION" if visible else "PASS", "ownership_gaps": len(visible), "cross_layer_realizations": len([row for row in background if row["disposition"] == "cross_layer_realization"]), "accepted_supporting_candidates": len([row for row in background if row["disposition"] == "supporting_layer_evidence"]), "background_candidates": len(background), "category_counts": category_counts, "visible_by_layer": visible_by_layer, "automatic_mutation": False},
        "suggestions": sorted(visible, key=lambda row: (row["source_layer"], row["category"], row["entity_id"])),
        "background_candidates": sorted(background, key=lambda row: (row["source_layer"], row["category"], row["entity_id"])),
    }


def main() -> int:
    report = build_report()
    save_json_atomic(RAW_PATH, report, indent=2)
    lines = ["# Derived Layer Contract Coverage", "", f"- status: `{report['summary']['status']}`", f"- ownership gaps: `{report['summary']['ownership_gaps']}`", f"- cross-layer realizations: `{report['summary']['cross_layer_realizations']}`", f"- background candidates: `{report['summary']['background_candidates']}`", "", "This is advisory only: it cannot mutate ownership, claims or seals."]
    for row in report["suggestions"]:
        lines.append(f"- `{row['category']}`: `{row['entity_id']}` suggests `{row['suggested_value']}` for `{row['source_layer']}`.")
    save_text_atomic(REPORT_PATH, "\n".join(lines) + "\n")
    print(json.dumps(report["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
