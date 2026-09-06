from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.core.config import CODE_MAPS_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic


POLICY_PATH = CODE_MAPS_DIR / "config" / "engine_data_access_policy.json"


def _string_set(value: object) -> set[str]:
    return {str(item) for item in value if str(item).strip()} if isinstance(value, list) else set()


def _validation_contract(policy: dict) -> dict:
    contract = policy.get("validation_contract", {}) if isinstance(policy, dict) else {}
    return contract if isinstance(contract, dict) else {}


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _is_filesystem_traversal(node: ast.Call, traversal_names: set[str]) -> bool:
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr == "walk":
        return "walk" in traversal_names and isinstance(func.value, ast.Name) and func.value.id == "os"
    return isinstance(func, ast.Attribute) and func.attr in traversal_names


def validate_engine_data_access() -> dict:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    validation_contract = _validation_contract(policy)
    scan_roots = [
        CODE_MAPS_DIR / str(item)
        for item in validation_contract.get("scan_roots", [])
        if str(item).strip()
    ]
    traversal_names = _string_set(validation_contract.get("traversal_names"))
    ssot_review_classes = _string_set(validation_contract.get("ssot_review_classes"))
    contract_errors = []
    if not scan_roots:
        contract_errors.append("missing_scan_roots")
    if not {"glob", "rglob", "walk"}.issubset(traversal_names):
        contract_errors.append("missing_traversal_names")
    if not {"artifact_reader", "execution_adapter", "materialization_adapter"}.issubset(ssot_review_classes):
        contract_errors.append("missing_ssot_review_classes")
    declarations = policy.get("filesystem_inventory_allowlist", {}) or {}
    findings = []
    for root in scan_roots:
        for path in root.rglob("*.py"):
            rel = path.relative_to(CODE_MAPS_DIR).as_posix()
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=rel)
            except SyntaxError as exc:
                findings.append({"file": rel, "line": exc.lineno or 0, "kind": "syntax_error", "declared": False})
                continue
            calls = [
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.Call) and _is_filesystem_traversal(node, traversal_names)
            ]
            if not calls:
                continue
            declaration = declarations.get(rel)
            for node in calls:
                findings.append(
                    {
                        "file": rel,
                        "line": int(getattr(node, "lineno", 0) or 0),
                        "kind": f"filesystem_{_call_name(node)}",
                        "declared": isinstance(declaration, dict),
                        "access_class": (declaration or {}).get("class") if isinstance(declaration, dict) else None,
                        "status": (declaration or {}).get("status") if isinstance(declaration, dict) else "undeclared",
                        "reason": (declaration or {}).get("reason") if isinstance(declaration, dict) else None,
                    }
                )
    undeclared = [item for item in findings if not item["declared"]]
    migration = [item for item in findings if item.get("status") == "migration_required"]
    ssot_review_candidates = [
        item
        for item in findings
        if item.get("declared")
        and item.get("status") == "accepted"
        and item.get("access_class") in ssot_review_classes
    ]
    by_class: dict[str, int] = {}
    for item in findings:
        key = str(item.get("access_class") or "undeclared")
        by_class[key] = by_class.get(key, 0) + 1
    payload = {
        "meta": {"kind": "engine_data_access_validation", "version": "v1"},
        "summary": {
            "status": "PASS" if not undeclared else "FAIL",
            "filesystem_traversals": len(findings),
            "undeclared": len(undeclared),
            "migration_required": len(migration),
            "accepted": len(findings) - len(undeclared) - len(migration),
            "ssot_review_candidates": len(ssot_review_candidates),
            "by_access_class": by_class,
            "scan_roots": len(scan_roots),
            "traversal_names": len(traversal_names),
            "contract_errors": contract_errors,
        },
        "validation_contract": validation_contract,
        "principles": policy.get("principles", {}),
        "findings": findings,
        "ssot_review_candidates": ssot_review_candidates,
    }
    payload["summary"]["status"] = "PASS" if not undeclared and not contract_errors else "FAIL"
    save_json_atomic(RAW_DIR / "engine_data_access_validation.json", payload)
    save_text_atomic(REPORTS_DIR / "engine_data_access_validation.md", render_report(payload))
    return payload


def render_report(payload: dict) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# Engine Data Access Validation",
        "",
        f"- status: `{summary.get('status')}`",
        f"- filesystem traversals: `{summary.get('filesystem_traversals')}`",
        f"- undeclared: `{summary.get('undeclared')}`",
        f"- migration required: `{summary.get('migration_required')}`",
        f"- SSOT review candidates: `{summary.get('ssot_review_candidates')}`",
        "",
        "## Access Classes",
        "",
        "| Class | Count |",
        "|---|---:|",
    ]
    for key, value in sorted((summary.get("by_access_class") or {}).items()):
        lines.append(f"| `{key}` | `{value}` |")
    candidates = payload.get("ssot_review_candidates", [])
    lines.extend(
        [
            "",
            "## SSOT Review Candidates",
            "",
            "These traversals are declared and accepted, but they are still useful hardening targets for future Atlas/SQLite projections.",
            "",
            "| File | Line | Class | Reason |",
            "|---|---:|---|---|",
        ]
    )
    for item in candidates[:50]:
        lines.append(
            f"| `{item.get('file')}` | `{item.get('line')}` | `{item.get('access_class')}` | {item.get('reason') or ''} |"
        )
    if len(candidates) > 50:
        lines.append(f"| `_omitted_` |  |  | {len(candidates) - 50} more candidates omitted from report preview. |")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    result = validate_engine_data_access()
    print(json.dumps(result["summary"], indent=2))
    raise SystemExit(0 if result["summary"]["status"] == "PASS" else 1)
