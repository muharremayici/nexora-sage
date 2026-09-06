from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.core.config import CONFIG_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file


CONTRACT_PATH = CONFIG_DIR / "nexora_agent_response_contract.json"
TEMPLATE_PATH = RAW_DIR / "nexora_agent_response_template.json"
VALIDATION_PATH = RAW_DIR / "nexora_agent_response_validation.json"
REPORT_PATH = REPORTS_DIR / "nexora_agent_response_validation.md"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def response_template() -> dict[str, Any]:
    template = _load_contract().get("template", {})
    return template if isinstance(template, dict) else {}


def _check(name: str, passed: bool, details: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _string_set(value: Any) -> set[str]:
    return {str(item).strip() for item in value if str(item).strip()} if isinstance(value, list) else set()


def _load_contract() -> dict[str, Any]:
    payload = load_json_file(CONTRACT_PATH, {})
    return payload if isinstance(payload, dict) else {}


def response_contract_fingerprint() -> str:
    contract = _load_contract()
    canonical = json.dumps(contract, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validation_contract(contract: dict[str, Any]) -> dict[str, Any]:
    payload = contract.get("validation_contract", {})
    return payload if isinstance(payload, dict) else {}


def validate_response(response: dict[str, Any]) -> dict[str, Any]:
    contract = _load_contract()
    validation_contract = _validation_contract(contract)
    required_fields = _string_set(validation_contract.get("required_fields"))
    valid_confidence = _string_set(validation_contract.get("valid_confidence"))
    valid_risk = _string_set(validation_contract.get("valid_risk"))
    approval_risk = _string_set(validation_contract.get("approval_requires_source_risk"))
    fields = set(response.keys()) if isinstance(response, dict) else set()
    missing = sorted(required_fields - fields)
    evidence = response.get("evidence") if isinstance(response, dict) else None
    source_artifacts = response.get("source_artifacts") if isinstance(response, dict) else None
    human_approval = response.get("human_approval_required") if isinstance(response, dict) else None
    risk = response.get("risk") if isinstance(response, dict) else None
    confidence = response.get("confidence") if isinstance(response, dict) else None

    checks = [
        _check(
            "agent_response_contract_exists",
            CONTRACT_PATH.exists() and bool(required_fields) and bool(valid_confidence) and bool(valid_risk),
            {
                "path": str(CONTRACT_PATH),
                "required_fields": sorted(required_fields),
                "valid_confidence": sorted(valid_confidence),
                "valid_risk": sorted(valid_risk),
            },
        ),
        _check("response_is_object", isinstance(response, dict), {"type": type(response).__name__}),
        _check("required_fields_present", not missing, {"missing": missing, "required": sorted(required_fields)}),
        _check("confidence_valid", confidence in valid_confidence, {"confidence": confidence}),
        _check("risk_valid", risk in valid_risk, {"risk": risk}),
        _check("human_approval_boolean", isinstance(human_approval, bool), {"type": type(human_approval).__name__}),
        _check("evidence_is_non_empty_list", isinstance(evidence, list) and len(evidence) > 0, {"type": type(evidence).__name__, "count": len(evidence) if isinstance(evidence, list) else None}),
        _check("source_artifacts_is_non_empty_list", isinstance(source_artifacts, list) and len(source_artifacts) > 0, {"type": type(source_artifacts).__name__, "count": len(source_artifacts) if isinstance(source_artifacts, list) else None}),
        _check(
            "approval_requires_high_signal_sources",
            not human_approval or (risk in approval_risk and isinstance(source_artifacts, list) and len(source_artifacts) > 0),
            {"human_approval_required": human_approval, "risk": risk, "source_artifacts": source_artifacts},
        ),
    ]
    failed = [check for check in checks if not check["passed"]]
    return {
        "meta": {
            "kind": "nexora_agent_response_validation",
            "version": "v1",
            "generated_at": _utc_now(),
            "generator": "tools.validate_nexora_agent_response",
        },
        "summary": {
            "total_checks": len(checks),
            "passed_checks": len(checks) - len(failed),
            "failed_checks": len(failed),
            "status": "PASS" if not failed else "FAIL",
            "contract_source": str(CONTRACT_PATH),
            "contract_fingerprint": response_contract_fingerprint(),
        },
        "checks": checks,
        "response": response,
    }


def render_report(validation: dict[str, Any]) -> str:
    summary = validation.get("summary", {})
    lines = [
        "# Nexora Agent Response Validation",
        "",
        f"- status: `{summary.get('status')}`",
        f"- checks: `{summary.get('passed_checks')}/{summary.get('total_checks')}`",
        f"- failed_checks: `{summary.get('failed_checks')}`",
        "",
        "## Checks",
        "",
        "| Check | Result | Details |",
        "|---|---|---|",
    ]
    for check in validation.get("checks", []):
        result = "PASS" if check.get("passed") else "FAIL"
        lines.append(f"| `{check.get('name')}` | {result} | `{check.get('details')}` |")
    return "\n".join(lines) + "\n"


def run_template_smoke() -> dict[str, Any]:
    template = response_template()
    save_json_atomic(TEMPLATE_PATH, template)
    validation = validate_response(template)
    save_json_atomic(VALIDATION_PATH, validation)
    save_text_atomic(REPORT_PATH, render_report(validation))
    return validation


def run_file_validation(path: str | Path) -> dict[str, Any]:
    response = load_json_file(Path(path), {})
    validation = validate_response(response if isinstance(response, dict) else {})
    save_json_atomic(VALIDATION_PATH, validation)
    save_text_atomic(REPORT_PATH, render_report(validation))
    return validation


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Nexora agent response contract shape.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--template-smoke", action="store_true", help="Write and validate the canonical response template.")
    group.add_argument("--file", help="Validate an agent response JSON file.")
    args = parser.parse_args()

    validation = run_template_smoke() if args.template_smoke else run_file_validation(args.file)
    print(json.dumps(validation.get("summary", {}), ensure_ascii=False))
    return 0 if validation.get("summary", {}).get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
