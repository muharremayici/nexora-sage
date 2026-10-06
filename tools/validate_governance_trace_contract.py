"""Validate the declarative local governance trace contract."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "governance_trace_contract.json"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.governance_trace_contract import (
    _load as load_trace_contract,
    evaluate_governance_trace_contract,
)
from tools.core.work_package_receipts import propose_work_package_evidence, record_work_package_operation_safely

RAW_OUTPUT_PATH = RAW_DIR / "governance_trace_contract_validation.json"
REPORT_OUTPUT_PATH = REPORTS_DIR / "governance_trace_contract_validation.md"


def _load() -> dict[str, Any]:
    return load_trace_contract(CONTRACT_PATH)


def run() -> dict[str, Any]:
    return evaluate_governance_trace_contract(contract_loader=_load)


if __name__ == "__main__":
    started = time.perf_counter()
    result = run()
    save_json_atomic(RAW_OUTPUT_PATH, result)
    lines = ["# Governance Trace Contract Validation", "", f"status: `{result['status']}`", ""]
    for check in result["checks"]:
        lines.append(f"- {'PASS' if check.get('ok') else 'FAIL'}: `{check['id']}`")
    save_text_atomic(REPORT_OUTPUT_PATH, "\n".join(lines) + "\n")
    record_work_package_operation_safely(
        operation_id="governance_trace_contract_validation",
        result_status=result["status"],
        started=started,
        evidence_artifact="output/.raw/governance_trace_contract_validation.json",
    )
    save_json_atomic(RAW_DIR / "work_package_evidence_proposal.json", propose_work_package_evidence())
    print(json.dumps(result, indent=2, ensure_ascii=False))
    raise SystemExit(0 if result["status"] == "PASS" else 1)
