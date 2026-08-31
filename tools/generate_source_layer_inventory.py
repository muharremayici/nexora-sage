from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
ROOT = TOOLS_DIR.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.config import CONFIG_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_object_strict
from tools.core.source_layer_classifier import classify_source_layer, is_ignored
from tools.core.source_layer_taxonomy import source_layer_descriptions, source_layer_order
from tools.core.source_state_identity import iter_governed_source_files


RAW_OUTPUT_PATH = RAW_DIR / "source_layer_inventory.json"
REPORT_OUTPUT_PATH = REPORTS_DIR / "source_layer_inventory.md"
SOURCE_ROLE_CONTRACT_PATH = CONFIG_DIR / "source_role_obligation_contract.json"

LAYER_ORDER = source_layer_order()
LAYER_DESCRIPTIONS = source_layer_descriptions()


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _is_ignored(path: Path) -> bool:
    """Preserve the inventory API while keeping ignore truth centralized."""
    return is_ignored(path, root=ROOT)


def classify(path: Path) -> tuple[str, str]:
    return classify_source_layer(path, root=ROOT)


def classify_source_role(
    path: Path,
    contract: dict[str, Any] | None = None,
    source_layer: str = "",
) -> tuple[str, list[str]]:
    payload = contract or load_json_object_strict(SOURCE_ROLE_CONTRACT_PATH, label="Source role obligation contract")
    rel = path.relative_to(ROOT).as_posix()
    if source_layer in {str(value) for value in payload.get("excluded_source_layers", [])}:
        return "generated_runtime", []
    executable_extensions = {str(value) for value in payload.get("executable_extensions", [])}
    if path.suffix not in executable_extensions:
        return "non_executable", []
    for rule in payload.get("role_rules", []):
        if not isinstance(rule, dict):
            continue
        exact_paths = {str(value) for value in rule.get("exact_paths", [])}
        path_prefixes = [str(value) for value in rule.get("path_prefixes", [])]
        if rel in exact_paths or any(rel.startswith(prefix) for prefix in path_prefixes):
            return str(rule.get("role") or "undeclared_executable"), [
                str(value) for value in rule.get("obligations", []) if str(value).strip()
            ]
    return "undeclared_executable", []


def build_inventory() -> dict[str, Any]:
    files = iter_governed_source_files(ROOT, include_generated_runtime=True)
    role_contract = load_json_object_strict(SOURCE_ROLE_CONTRACT_PATH, label="Source role obligation contract")
    rows: list[dict[str, Any]] = []
    by_layer: dict[str, list[dict[str, Any]]] = defaultdict(list)
    ext_counter: Counter[str] = Counter()
    role_counter: Counter[str] = Counter()
    for path in files:
        layer, reason = classify(path)
        rel = _rel(path)
        source_role, role_obligations = classify_source_role(path, role_contract, layer)
        entry = {
            "path": rel,
            "layer": layer,
            "reason": reason,
            "extension": path.suffix or "<none>",
            "source_role": source_role,
            "role_obligations": role_obligations,
        }
        rows.append(entry)
        by_layer[layer].append(entry)
        ext_counter[path.suffix or "<none>"] += 1
        role_counter[source_role] += 1

    layer_counts = {layer: len(by_layer.get(layer, [])) for layer in LAYER_ORDER}
    unknown = by_layer.get("unknown_or_review", [])
    generated = by_layer.get("generated_runtime_artifact", [])
    undeclared_executable_roles = [row for row in rows if row.get("source_role") == "undeclared_executable"]
    return {
        "meta": {"kind": "source_layer_inventory", "version": "v1"},
        "summary": {
            "total_files": len(rows),
            "layers": len([layer for layer, count in layer_counts.items() if count]),
            "unknown_or_review": len(unknown),
            "generated_runtime_artifacts": len(generated),
            "extension_counts": dict(ext_counter.most_common()),
            "source_role_counts": dict(role_counter.most_common()),
            "undeclared_executable_roles": len(undeclared_executable_roles),
        },
        "layer_counts": layer_counts,
        "layers": {
            layer: {
                "description": LAYER_DESCRIPTIONS.get(layer, ""),
                "count": len(by_layer.get(layer, [])),
                "files": by_layer.get(layer, []),
            }
            for layer in LAYER_ORDER
            if by_layer.get(layer)
        },
        "unknown_or_review": unknown,
        "generated_runtime_artifacts": generated,
        "undeclared_executable_roles": undeclared_executable_roles,
        "source_role_contract": {
            "path": "config/source_role_obligation_contract.json",
            "role_inference_grants_activation": bool(role_contract.get("default_behavior", {}).get("role_inference_grants_activation")),
            "role_inference_expands_claims": bool(role_contract.get("default_behavior", {}).get("role_inference_expands_claims")),
        },
    }


def render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# Source Layer Inventory",
        "",
        f"- Total files: `{summary.get('total_files')}`",
        f"- Active layers: `{summary.get('layers')}`",
        f"- Unknown/review files: `{summary.get('unknown_or_review')}`",
        f"- Generated/runtime artifacts in development tree: `{summary.get('generated_runtime_artifacts')}`",
        f"- Undeclared executable roles: `{summary.get('undeclared_executable_roles')}`",
        "",
        "## Layer Counts",
        "",
        "| Layer | Count | Role |",
        "|---|---:|---|",
    ]
    layer_counts = payload.get("layer_counts", {})
    for layer in LAYER_ORDER:
        count = int(layer_counts.get(layer, 0) or 0)
        if not count:
            continue
        lines.append(f"| `{layer}` | {count} | {LAYER_DESCRIPTIONS.get(layer, '')} |")

    lines.extend(["", "## Source Role Counts", "", "| Source Role | Count |", "|---|---:|"])
    for role, count in (summary.get("source_role_counts") or {}).items():
        lines.append(f"| `{role}` | {int(count or 0)} |")

    unknown = payload.get("unknown_or_review", [])
    if unknown:
        lines.extend(["", "## Unknown Or Review", ""])
        for entry in unknown[:100]:
            lines.append(f"- `{entry['path']}` ({entry['reason']})")

    generated = payload.get("generated_runtime_artifacts", [])
    if generated:
        lines.extend(["", "## Generated Or Runtime Artifacts", ""])
        for entry in generated[:100]:
            lines.append(f"- `{entry['path']}` ({entry['reason']})")

    undeclared_roles = payload.get("undeclared_executable_roles", [])
    if undeclared_roles:
        lines.extend(["", "## Undeclared Executable Roles", ""])
        for entry in undeclared_roles[:100]:
            lines.append(f"- `{entry['path']}`")

    lines.extend(["", "## Interpretation", ""])
    lines.append("- `unknown_or_review` should trend toward zero or be consciously accepted.")
    lines.append("- `generated_runtime_artifact` is acceptable in the development workspace but must stay out of the clean distribution.")
    lines.append("- Variation/Merge, ContextOS/MCP, HITL/Agent, Installation/Onboarding and React Surgical layers are first-class layers, not incidental utilities.")
    lines.append("- Source-role inference creates obligations only; it never activates an engine or expands a release claim.")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    payload = build_inventory()
    save_json_atomic(RAW_OUTPUT_PATH, payload)
    save_text_atomic(REPORT_OUTPUT_PATH, render_report(payload))
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"].get("unknown_or_review", 0) == 0 and payload["summary"].get("undeclared_executable_roles", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
