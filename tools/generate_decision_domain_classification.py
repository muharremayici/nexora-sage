from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file


RAW_OUTPUT_PATH = RAW_DIR / "decision_domain_classification.json"
REPORT_OUTPUT_PATH = REPORTS_DIR / "decision_domain_classification.md"


def _sha256_file(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _source_file_layers(source_inventory: dict[str, Any]) -> dict[str, str]:
    layers = source_inventory.get("layers", {}) if isinstance(source_inventory, dict) else {}
    mapping: dict[str, str] = {}
    for layer, payload in layers.items():
        files = payload.get("files", []) if isinstance(payload, dict) else []
        for item in files:
            if isinstance(item, dict) and item.get("path"):
                mapping[str(item["path"])] = str(layer)
    return mapping


def _layer_connectivity(connectivity_map: dict[str, Any]) -> dict[str, dict[str, Any]]:
    layers = connectivity_map.get("connectivity", {}).get("source_layers", []) if isinstance(connectivity_map, dict) else []
    return {str(item.get("id") or ""): item for item in layers if isinstance(item, dict)}


def _counter_rows(counter: Counter[str]) -> list[dict[str, Any]]:
    return [{"name": name, "count": count} for name, count in counter.most_common()]


def _python_work_plan(hardcoded_inventory: dict[str, Any]) -> list[dict[str, Any]]:
    inventory = hardcoded_inventory.get("inventory", {}) if isinstance(hardcoded_inventory, dict) else {}
    rows = inventory.get("work_plan", []) if isinstance(inventory, dict) else []
    return [row for row in rows if isinstance(row, dict)]


def _central_registry_owner_files(contributor_contract: dict[str, Any]) -> set[str]:
    surfaces = contributor_contract.get("extension_surfaces", []) if isinstance(contributor_contract, dict) else []
    owners: set[str] = set()
    for surface in surfaces:
        if not isinstance(surface, dict):
            continue
        registry = str(surface.get("registry_or_manifest") or "").replace("\\", "/").strip()
        if registry and not registry.endswith("/"):
            owners.add(registry)
        owner_files = surface.get("central_owner_files", [])
        if isinstance(owner_files, list):
            for owner_file in owner_files:
                normalized = str(owner_file or "").replace("\\", "/").strip()
                if normalized and not normalized.endswith("/"):
                    owners.add(normalized)
    return owners


def _reviewed_deferred_files(source_contract_policy: dict[str, Any]) -> dict[str, dict[str, Any]]:
    policy = source_contract_policy.get("decision_domain_classification", {}) if isinstance(source_contract_policy, dict) else {}
    rows = policy.get("reviewed_deferred_files", {}) if isinstance(policy, dict) else {}
    if not isinstance(rows, dict):
        return {}
    return {
        str(file).replace("\\", "/"): payload
        for file, payload in rows.items()
        if isinstance(payload, dict)
    }


def _non_python_numeric_inventory(non_python_inventory: dict[str, Any]) -> list[dict[str, Any]]:
    checks = non_python_inventory.get("checks", []) if isinstance(non_python_inventory, dict) else []
    for check in checks:
        if isinstance(check, dict) and check.get("name") == "numeric_policy_literals_are_visible_inventory":
            details = check.get("details", {}) if isinstance(check.get("details"), dict) else {}
            return [item for item in details.get("sample", []) if isinstance(item, dict)]
    return []


def _non_python_markdown_release_inventory(non_python_inventory: dict[str, Any]) -> list[dict[str, Any]]:
    checks = non_python_inventory.get("checks", []) if isinstance(non_python_inventory, dict) else []
    for check in checks:
        if isinstance(check, dict) and check.get("name") == "markdown_release_references_are_visible_inventory":
            details = check.get("details", {}) if isinstance(check.get("details"), dict) else {}
            return [item for item in details.get("sample", []) if isinstance(item, dict)]
    return []


def _recommended_domain_action(layer: str, candidate_count: int, action: str, *, central_registry_owner: bool = False) -> str:
    if central_registry_owner:
        return "keep_visible_central_registry_owner_and_validate_consumers"
    if action == "inspect_for_shared_contract":
        return "inspect_domain_registry_or_create_shared_contract"
    if layer in {"configuration_doctrine_policy", "validation_release_gates", "orchestration_pipeline"} and candidate_count >= 15:
        return "sample_large_family_then_promote_repeated_policy_to_registry"
    if layer == "contextos_watchdog_mcp":
        return "preserve_agent_surface_literals_unless_repeated_policy_or_budget"
    if layer == "react_surgical_intelligence":
        return "preserve_detector_vocabulary_unless_shared_react_policy"
    return "keep_visible_inventory_and_refactor_only_when_duplication_is_proven"


def build_classification() -> dict[str, Any]:
    hardcoded = load_json_file(RAW_DIR / "hardcoded_decision_inventory_validation.json", {})
    non_python = load_json_file(RAW_DIR / "non_python_decision_inventory_validation.json", {})
    source_inventory = load_json_file(RAW_DIR / "source_layer_inventory.json", {})
    connectivity_map = load_json_file(RAW_DIR / "system_connectivity_map.json", {})
    connectivity_validation = load_json_file(RAW_DIR / "system_connectivity_map_validation.json", {})
    contributor_contract = load_json_file(ROOT / "config" / "contributor_extension_contract.json", {})
    source_contract_policy = load_json_file(ROOT / "config" / "source_contract_policy.json", {})
    file_layers = _source_file_layers(source_inventory)
    connectivity_validation_summary = (
        connectivity_validation.get("summary", {}) if isinstance(connectivity_validation, dict) else {}
    )
    connectivity_source_evidence = (
        connectivity_validation.get("source_evidence", {})
        if isinstance(connectivity_validation.get("source_evidence"), dict)
        else {}
    )
    connectivity_map_sha256 = _sha256_file(RAW_DIR / "system_connectivity_map.json")
    connectivity_validation_sha256 = str(connectivity_source_evidence.get("sha256") or "")
    connectivity_authorized = (
        connectivity_validation_summary.get("status") == "PASS"
        and bool(connectivity_map_sha256)
        and connectivity_validation_sha256 == connectivity_map_sha256
    )
    layer_nodes = _layer_connectivity(connectivity_map) if connectivity_authorized else {}
    central_registry_owners = _central_registry_owner_files(contributor_contract)
    reviewed_deferred = _reviewed_deferred_files(source_contract_policy)

    python_rows: list[dict[str, Any]] = []
    by_layer: Counter[str] = Counter()
    by_action: Counter[str] = Counter()
    high_priority: list[dict[str, Any]] = []
    for row in _python_work_plan(hardcoded):
        file = str(row.get("file") or "")
        layer = file_layers.get(file, "unknown_or_review")
        candidate_count = int(row.get("candidate_count") or 0)
        action = str(row.get("recommended_action") or "")
        central_registry_owner = file in central_registry_owners
        reviewed_deferred_payload = reviewed_deferred.get(file)
        recommended = _recommended_domain_action(
            layer,
            candidate_count,
            action,
            central_registry_owner=central_registry_owner,
        )
        if reviewed_deferred_payload:
            recommended = str(reviewed_deferred_payload.get("action") or "keep_visible_reviewed_deferred_domain")
        enriched = {
            "file": file,
            "layer": layer,
            "candidate_count": candidate_count,
            "bucket": str(row.get("bucket") or ""),
            "inventory_action": action,
            "domain_action": recommended,
            "central_registry_owner": central_registry_owner,
            "reviewed_deferred": bool(reviewed_deferred_payload),
            "reviewed_deferred_reason": reviewed_deferred_payload.get("reason") if reviewed_deferred_payload else None,
            "layer_release_steps": (layer_nodes.get(layer, {}).get("expected_release_steps") or [])[:8],
            "layer_validators": (layer_nodes.get(layer, {}).get("validators") or [])[:8],
        }
        python_rows.append(enriched)
        by_layer[layer] += candidate_count
        by_action[recommended] += candidate_count
        if reviewed_deferred_payload:
            continue
        if recommended.startswith("inspect_domain_registry") or (
            candidate_count >= 15 and "promote_repeated_policy" in recommended
        ):
            high_priority.append(enriched)

    numeric_rows = _non_python_numeric_inventory(non_python)
    numeric_by_file = Counter(str(row.get("file") or "") for row in numeric_rows)
    markdown_release_rows = _non_python_markdown_release_inventory(non_python)
    markdown_release_by_file = Counter(str(row.get("file") or "") for row in markdown_release_rows)

    connectivity_summary = connectivity_map.get("summary", {}) if isinstance(connectivity_map, dict) else {}
    payload = {
        "meta": {
            "kind": "decision_domain_classification",
            "version": "v1",
            "generated_at": _utc_now(),
            "generator": "tools.generate_decision_domain_classification",
            "source_artifacts": [
                "hardcoded_decision_inventory_validation.json",
                "non_python_decision_inventory_validation.json",
                "source_layer_inventory.json",
                "system_connectivity_map.json",
                "system_connectivity_map_validation.json",
                "contributor_extension_contract.json",
            ],
        },
        "summary": {
            "status": "PASS" if connectivity_authorized else "FAIL",
            "python_decision_files": len(python_rows),
            "python_decision_candidates": sum(row["candidate_count"] for row in python_rows),
            "high_priority_domains": len(high_priority),
            "central_registry_owner_domains": sum(1 for row in python_rows if row.get("central_registry_owner")),
            "reviewed_deferred_domains": sum(1 for row in python_rows if row.get("reviewed_deferred")),
            "numeric_policy_inventory_sampled": len(numeric_rows),
            "markdown_release_inventory_sampled": len(markdown_release_rows),
            "connectivity_status": connectivity_validation_summary.get("status"),
            "connectivity_map_reported_status": connectivity_summary.get("status"),
            "connectivity_authorized": connectivity_authorized,
            "connectivity_map_sha256": connectivity_map_sha256,
            "connectivity_validation_source_sha256": connectivity_validation_sha256 or None,
            "connectivity_validation_matches_current_map": (
                bool(connectivity_map_sha256) and connectivity_validation_sha256 == connectivity_map_sha256
            ),
            "connectivity_unowned_consumed_artifacts": connectivity_summary.get("unowned_consumed_artifacts"),
            "rule": "Do not refactor literals solely because they are literals; promote only repeated decision policy, budgets, phase/claim boundaries, or cross-layer contracts into registries.",
        },
        "python_decision_domains": {
            "by_layer": _counter_rows(by_layer),
            "by_domain_action": _counter_rows(by_action),
            "high_priority": high_priority,
            "rows": python_rows,
        },
        "non_python_decision_domains": {
            "numeric_policy_by_file": _counter_rows(numeric_by_file),
            "markdown_release_by_file": _counter_rows(markdown_release_by_file),
        },
    }
    save_json_atomic(RAW_OUTPUT_PATH, payload)
    save_text_atomic(REPORT_OUTPUT_PATH, render_markdown(payload))
    return payload


def render_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    python_domains = payload.get("python_decision_domains", {})
    non_python_domains = payload.get("non_python_decision_domains", {})
    lines = [
        "# Decision Domain Classification",
        "",
        "This report classifies hardcoded-decision inventory through SAGE's source layer and connectivity map.",
        "It does not make literals failures by default; it identifies which decision domains deserve shared contracts.",
        "",
        "## Summary",
        "",
        f"- status: `{summary.get('status')}`",
        f"- python_decision_files: `{summary.get('python_decision_files')}`",
        f"- python_decision_candidates: `{summary.get('python_decision_candidates')}`",
        f"- high_priority_domains: `{summary.get('high_priority_domains')}`",
        f"- reviewed_deferred_domains: `{summary.get('reviewed_deferred_domains')}`",
        f"- connectivity_status: `{summary.get('connectivity_status')}`",
        f"- connectivity_unowned_consumed_artifacts: `{summary.get('connectivity_unowned_consumed_artifacts')}`",
        f"- rule: {summary.get('rule')}",
        "",
        "## Python Decision Candidates By Layer",
        "",
        "| Layer | Candidates |",
        "|---|---:|",
    ]
    for item in python_domains.get("by_layer", []):
        lines.append(f"| `{item.get('name')}` | {item.get('count')} |")
    lines.extend(["", "## Domain Actions", "", "| Action | Candidates |", "|---|---:|"])
    for item in python_domains.get("by_domain_action", []):
        lines.append(f"| `{item.get('name')}` | {item.get('count')} |")
    lines.extend(["", "## High Priority Queue", "", "| File | Layer | Candidates | Action |", "|---|---|---:|---|"])
    for row in python_domains.get("high_priority", [])[:40]:
        lines.append(
            f"| `{row.get('file')}` | `{row.get('layer')}` | {row.get('candidate_count')} | `{row.get('domain_action')}` |"
        )
    if not python_domains.get("high_priority"):
        lines.append("| - | - | 0 | - |")
    lines.extend(["", "## Non-Python Decision Samples", "", "### Numeric policy by file", "", "| File | Sampled literals |", "|---|---:|"])
    for item in non_python_domains.get("numeric_policy_by_file", [])[:25]:
        lines.append(f"| `{item.get('name')}` | {item.get('count')} |")
    lines.extend(["", "### Markdown release references by file", "", "| File | Sampled references |", "|---|---:|"])
    for item in non_python_domains.get("markdown_release_by_file", [])[:25]:
        lines.append(f"| `{item.get('name')}` | {item.get('count')} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    payload = build_classification()
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"].get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
