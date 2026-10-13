"""Development-only adapter for controller-verified external observations."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from tools.core import work_package_receipts as receipts
from tools.core.governance_trace import load_trace_events
from tools.core.governance_trace_contract import validate_external_result_receipt_policy


def record_verified_external_operation(
    *, operation_id: str, result_status: str, binding: dict[str, str],
    external_evidence_fingerprint: str, db_path: Path | None = None,
) -> dict[str, Any]:
    """Import an existing controller-verified result, not execute or certify it."""
    current = receipts.work_package_operation_binding()
    validate_external_result_receipt_policy(receipts._operation_profile(operation_id).get("external_result_receipt"))
    if binding != current:
        raise ValueError("External operation package/source binding is stale")
    if result_status not in receipts._operation_profile(operation_id)["accepted_statuses"]:
        raise ValueError("External result did not pass")
    digest = external_evidence_fingerprint
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("External evidence content identity is unavailable")
    # Bounded reuse is an optimization only: a miss records another observation,
    # never reruns cleanup or invents an execution. No raw result enters SQLite.
    for event in load_trace_events(event_type="work_package_operation", limit=200,
                                  db_path=db_path, initialize_schema=False):
        details = event.get("details", {})
        if (event.get("task_fingerprint") == current["package_identity"]
            and event.get("context_fingerprint") == current["source_identity"]
            and event.get("tool_name") == operation_id and event.get("outcome") == "success"
            and details.get("package_id") == current["package_id"]
            and details.get("result_status") == result_status
            and details.get("operation_provenance") == "verified_external_result"
            and details.get("external_evidence_fingerprint") == digest):
            if receipts.work_package_operation_binding() != current:
                raise ValueError("External operation package/source binding changed during reuse")
            return {"status": "reused", "trace_id": event["trace_id"],
                    "operation_id": operation_id, "operation_executed": False}
    event = receipts.record_work_package_operation(
        operation_id=operation_id, result_status=result_status, started=time.perf_counter(),
        expected_binding=binding, external_evidence_fingerprint=digest, db_path=db_path)
    return {"status": "recorded", "trace_id": event["trace_id"],
            "operation_id": operation_id, "operation_executed": False}

