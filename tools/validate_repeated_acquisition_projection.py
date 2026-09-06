from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.core.acquisition_audit import repeated_acquisitions
from tools.core.config import CODE_MAPS_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic


POLICY_PATH = CODE_MAPS_DIR / "config" / "engine_data_access_policy.json"


def _audit_contract(policy: dict) -> dict:
    validation = policy.get("validation_contract", {}) if isinstance(policy, dict) else {}
    contract = validation.get("repeated_acquisition_audit", {}) if isinstance(validation, dict) else {}
    return contract if isinstance(contract, dict) else {}


def _classify_candidates(candidates: list[dict], declared: dict) -> tuple[list[dict], list[str]]:
    live_keys = {str(item.get("key") or "") for item in candidates}
    for item in candidates:
        declaration = declared.get(item["key"])
        item["classification"] = "declared_independent" if isinstance(declaration, dict) else "review_required"
        item["reason"] = declaration.get("reason") if isinstance(declaration, dict) else None
    unused_declarations = sorted(str(key) for key in declared if str(key) not in live_keys)
    return candidates, unused_declarations


def validate_repeated_acquisition_projection() -> dict:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    contract = _audit_contract(policy)
    scan_roots = [str(item) for item in contract.get("scan_roots", []) if str(item).strip()]
    excludes = [str(item) for item in contract.get("exclude_path_prefixes", []) if str(item).strip()]
    methods = {str(item) for item in contract.get("acquisition_methods", []) if str(item).strip()}
    minimum = int(contract.get("minimum_occurrences", 0) or 0)
    declared = contract.get("declared_independent_reads", {})
    declared = declared if isinstance(declared, dict) else {}
    contract_errors = []
    if not scan_roots:
        contract_errors.append("missing_scan_roots")
    if not methods:
        contract_errors.append("missing_acquisition_methods")
    if minimum < 2:
        contract_errors.append("invalid_minimum_occurrences")
    malformed_declarations = sorted(
        str(key)
        for key, value in declared.items()
        if not isinstance(value, dict) or not str(value.get("reason") or "").strip()
    )
    if malformed_declarations:
        contract_errors.append("malformed_independent_read_declarations")
    candidates, parse_errors = repeated_acquisitions(
        CODE_MAPS_DIR,
        scan_roots=scan_roots,
        exclude_path_prefixes=excludes,
        methods=methods,
        minimum_occurrences=max(2, minimum),
    )
    candidates, unused_declarations = _classify_candidates(candidates, declared)
    review_required = [item for item in candidates if item["classification"] == "review_required"]
    status = (
        "FAIL"
        if contract_errors or parse_errors or unused_declarations
        else "REVIEW_REQUIRED"
        if review_required
        else "PASS"
    )
    payload = {
        "meta": {"kind": "repeated_acquisition_projection_validation", "version": "v1"},
        "summary": {
            "status": status,
            "candidates": len(candidates),
            "review_required": len(review_required),
            "declared_independent": len(candidates) - len(review_required),
            "parse_errors": len(parse_errors),
            "unused_declarations": len(unused_declarations),
            "contract_errors": contract_errors,
        },
        "validation_contract": contract,
        "candidates": candidates,
        "unused_declarations": unused_declarations,
        "malformed_declarations": malformed_declarations,
        "parse_errors": parse_errors,
    }
    save_json_atomic(RAW_DIR / "repeated_acquisition_projection_validation.json", payload)
    save_text_atomic(REPORTS_DIR / "repeated_acquisition_projection_validation.md", render_report(payload))
    return payload


def render_report(payload: dict) -> str:
    summary = payload["summary"]
    lines = [
        "# Repeated Acquisition Projection Validation",
        "",
        f"- status: `{summary['status']}`",
        f"- candidates: `{summary['candidates']}`",
        f"- review required: `{summary['review_required']}`",
        f"- declared independent: `{summary['declared_independent']}`",
        f"- parse errors: `{summary['parse_errors']}`",
        f"- unused declarations: `{summary['unused_declarations']}`",
        "",
        "A candidate is not automatically a defect. Consolidate only when source identity, freshness boundary and failure isolation are the same.",
        "",
        "| File | Function | Method | Source | Count | Lines | Classification |",
        "|---|---|---|---|---:|---|---|",
    ]
    for item in payload.get("candidates", []):
        lines.append(
            f"| `{item['file']}` | `{item['function']}` | `{item['method']}` | "
            f"`{item['source_expression']}` | `{item['occurrences']}` | "
            f"`{','.join(str(line) for line in item['lines'])}` | `{item['classification']}` |"
        )
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    result = validate_repeated_acquisition_projection()
    print(json.dumps(result["summary"], indent=2))
    raise SystemExit(0 if result["summary"]["status"] == "PASS" else 1)
