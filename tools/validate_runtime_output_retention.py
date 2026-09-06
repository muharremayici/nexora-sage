from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.release_proof_steps import load_release_proof_command_names, load_release_proof_step_ids


POLICY_PATH = ROOT / "config" / "runtime_output_retention_policy.json"
RAW_OUTPUT_PATH = RAW_DIR / "runtime_output_retention_validation.json"
REPORT_OUTPUT_PATH = REPORTS_DIR / "runtime_output_retention_validation.md"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def _check(name: str, passed: bool, details: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _number_after(pattern: str, text: str) -> int | None:
    match = re.search(pattern, text, flags=re.MULTILINE)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def build_validation() -> dict[str, Any]:
    policy = _load_json(POLICY_PATH)
    targets = [row for row in policy.get("retention_targets", []) if isinstance(row, dict)]
    target_by_id = {str(row.get("id") or ""): row for row in targets}
    logger_text = _read(ROOT / "tools" / "core" / "logger.py")
    honesty_text = _read(ROOT / "tools" / "core" / "honesty_telemetry.py")
    mcp_call_telemetry_text = _read(ROOT / "tools" / "core" / "mcp_call_telemetry.py")
    local_telemetry_text = _read(ROOT / "tools" / "engines" / "local_telemetry_engine.py")
    pipeline_receipt_text = _read(ROOT / "tools" / "core" / "pipeline_run_receipts.py")
    source_contracts_text = _read(ROOT / "tools" / "validate_source_contracts.py")
    external_retention_text = _read(ROOT / "tools" / "core" / "external_target_retention.py")
    external_retention_validator_text = _read(ROOT / "tools" / "validate_external_target_retention.py")
    release_proof_step_ids = load_release_proof_step_ids()
    release_proof_command_names = load_release_proof_command_names()

    pipeline_policy = target_by_id.get("pipeline_log", {})
    honesty_policy = target_by_id.get("honesty_telemetry", {})
    mcp_call_policy = target_by_id.get("mcp_call_telemetry", {})
    mcp_honesty_policy = target_by_id.get("mcp_honesty_telemetry", {})
    telemetry_traces_policy = target_by_id.get("telemetry_traces", {})
    pipeline_receipt_policy = target_by_id.get("pipeline_run_receipt_shadow", {})
    external_fixture_policy = target_by_id.get("external_target_generated_fixtures", {})

    pipeline_max_bytes = int(pipeline_policy.get("max_bytes") or 0)
    pipeline_backup_count = int(pipeline_policy.get("backup_count") or 0)
    honesty_max_events = int(honesty_policy.get("max_events") or 0)
    mcp_max_entries = int(mcp_call_policy.get("max_entries") or 0)
    mcp_max_summary_tools = int(mcp_call_policy.get("max_summary_tools") or 0)
    mcp_honesty_max_events = int(mcp_honesty_policy.get("max_events") or 0)
    telemetry_max_events = int(telemetry_traces_policy.get("max_events") or 0)
    external_fixture_prefixes = [
        str(prefix)
        for prefix in external_fixture_policy.get("generated_fixture_prefixes", [])
        if isinstance(prefix, str) and prefix
    ]
    external_producer_paths = [
        str(path)
        for path in external_fixture_policy.get("producer_validators", [])
        if isinstance(path, str) and path
    ]
    external_producer_texts = {
        path: _read(ROOT / path)
        for path in external_producer_paths
    }
    external_keep_per_prefix = int(external_fixture_policy.get("default_keep_per_prefix") or 0)

    logger_uses_rotation = (
        "RotatingFileHandler" in logger_text
        and "pipeline.log" in logger_text
        and "maxBytes=5 * 1024 * 1024" in logger_text
        and "backupCount=5" in logger_text
    )
    honesty_limit = _number_after(r"_MAX_EVENTS\s*=\s*(\d+)", honesty_text)
    honesty_bounded = (
        honesty_limit == honesty_max_events
        and "events[-_MAX_EVENTS:]" in honesty_text
        and "existing[\"count\"]" in honesty_text
    )

    checks = [
        _check(
            "runtime_retention_policy_exists",
            POLICY_PATH.exists() and policy.get("_meta", {}).get("kind") == "nexora.runtime_output_retention_policy",
            {"path": "config/runtime_output_retention_policy.json", "targets": sorted(target_by_id)},
        ),
        _check(
            "pipeline_log_rotation_matches_policy",
            logger_uses_rotation and pipeline_max_bytes == 5 * 1024 * 1024 and pipeline_backup_count == 5,
            {
                "policy_max_bytes": pipeline_max_bytes,
                "policy_backup_count": pipeline_backup_count,
                "implementation": "tools/core/logger.py",
            },
        ),
        _check(
            "honesty_telemetry_event_retention_matches_policy",
            honesty_bounded,
            {
                "policy_max_events": honesty_max_events,
                "implementation_max_events": honesty_limit,
                "implementation": "tools/core/honesty_telemetry.py",
            },
        ),
        _check(
            "mcp_call_telemetry_retention_matches_policy",
            "RETENTION_TARGET_ID = \"mcp_call_telemetry\"" in mcp_call_telemetry_text
            and "persist_completed_mcp_call" in mcp_call_telemetry_text
            and "build_summary(entries" in mcp_call_telemetry_text
            and "event_type=\"mcp_tool_call\"" in mcp_call_telemetry_text
            and "max_events=max(1, int(policy.get(\"max_entries\") or 200))" in mcp_call_telemetry_text
            and mcp_call_policy.get("path") == "output/.operational/mcp/mcp_call_telemetry.db"
            and mcp_call_policy.get("physical_ssot") == "SQLite governance_trace_events"
            and mcp_call_policy.get("storage_authority") == "product_global_operational"
            and mcp_call_policy.get("target_namespace_writes") == "forbidden"
            and mcp_max_entries == 200
            and mcp_max_summary_tools == 50,
            {
                "policy_max_entries": mcp_max_entries,
                "policy_max_summary_tools": mcp_max_summary_tools,
                "implementation": "tools/core/mcp_call_telemetry.py",
            },
        ),
        _check(
            "mcp_honesty_telemetry_is_call_local_and_bounded",
            "activate_honesty_telemetry_path" in honesty_text
            and "current_honesty_telemetry_path" in honesty_text
            and "def _physical_ssot" in honesty_text
            and 'else "json_file"' in honesty_text
            and mcp_honesty_policy.get("path") == "output/.operational/mcp/honesty_telemetry.json"
            and mcp_honesty_policy.get("storage_authority") == "product_global_operational"
            and mcp_honesty_policy.get("target_namespace_writes") == "forbidden"
            and mcp_honesty_max_events == honesty_max_events == 2000,
            {
                "policy_max_events": mcp_honesty_max_events,
                "implementation": "tools/core/honesty_telemetry.py",
            },
        ),
        _check(
            "local_telemetry_trace_retention_matches_policy",
            "def _max_trace_events" in local_telemetry_text
            and "telemetry_traces" in local_telemetry_text
            and "data[\"traces\"] = data[\"traces\"][-max_events:]" in local_telemetry_text
            and telemetry_max_events == 1000,
            {
                "policy_max_events": telemetry_max_events,
                "implementation": "tools/engines/local_telemetry_engine.py",
            },
        ),
        _check(
            "pipeline_run_receipt_shadow_is_single_latest_sqlite_projection",
            pipeline_receipt_policy.get("kind") == "single_latest_sqlite_projection"
            and int(pipeline_receipt_policy.get("max_files") or 0) == 1
            and pipeline_receipt_policy.get("path") == "output/.raw/pipeline_run_receipt.json"
            and 'DEFAULT_SHADOW_PATH = RAW_DIR / "pipeline_run_receipt.json"' in pipeline_receipt_text
            and '"source_of_truth": "SQLite governance_trace_events"' in pipeline_receipt_text
            and "save_json_atomic(path, projection, bypass_proxy=True)" in pipeline_receipt_text,
            {
                "policy_max_files": pipeline_receipt_policy.get("max_files"),
                "implementation": "tools/core/pipeline_run_receipts.py",
            },
        ),
        _check(
            "external_target_generated_fixture_retention_is_registered",
            "prune_generated_external_target_fixtures" in external_retention_text
            and "RETENTION_TARGET_ID = \"external_target_generated_fixtures\"" in external_retention_text
            and "generated_fixture_prefixes" in external_retention_text
            and "dry_run" in external_retention_validator_text
            and "retention_prune_preserves_user_scoped_probe" in external_retention_validator_text
            and "external_target_retention" in release_proof_step_ids
            and "external_targets:generated_fixture_retention_is_bounded" in source_contracts_text,
            {
                "policy_prefixes": external_fixture_prefixes,
                "policy_keep_per_prefix": external_keep_per_prefix,
                "implementation": "tools/core/external_target_retention.py",
                "validator": "tools/validate_external_target_retention.py",
            },
        ),
        _check(
            "external_target_fixture_producers_apply_retention",
            len(external_fixture_prefixes) >= 3
            and len(external_producer_paths) >= 3
            and external_keep_per_prefix >= 1
            and all("prune_generated_external_target_fixtures" in text for text in external_producer_texts.values())
            and all("DEFAULT_KEEP_PER_PREFIX" in text for text in external_producer_texts.values()),
            {
                "policy_prefixes": external_fixture_prefixes,
                "policy_keep_per_prefix": external_keep_per_prefix,
                "producer_validators": external_producer_paths,
            },
        ),
        _check(
            "retention_policy_is_release_proof_registered",
            "validate_runtime_output_retention.py" in release_proof_command_names
            and "runtime_output_retention" in release_proof_step_ids,
            "config/release_proof_steps_contract.json",
        ),
    ]
    failures = [row for row in checks if not row["passed"]]
    return {
        "meta": {
            "kind": "runtime_output_retention_validation",
            "version": "1.0.0",
            "generated_at": _utc_now(),
            "generator": "tools.validate_runtime_output_retention",
            "source": "config/runtime_output_retention_policy.json",
        },
        "summary": {
            "status": "PASS" if not failures else "FAIL",
            "targets": len(targets),
            "checks": len(checks),
            "passed": sum(1 for row in checks if row["passed"]),
            "failed": len(failures),
        },
        "checks": checks,
    }


def render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# Runtime Output Retention Validation",
        "",
        f"- status: `{summary.get('status')}`",
        f"- targets: `{summary.get('targets')}`",
        f"- checks: `{summary.get('checks')}`",
        f"- failed: `{summary.get('failed')}`",
        "",
        "## Checks",
        "",
        "| Check | Result | Details |",
        "|---|---|---|",
    ]
    for check in payload.get("checks", []) or []:
        details = check.get("details")
        if not isinstance(details, str):
            details = json.dumps(details, ensure_ascii=False)
        escaped_details = details.replace("|", "\\|")
        lines.append(f"| `{check.get('name')}` | {'PASS' if check.get('passed') else 'FAIL'} | {escaped_details} |")
    return "\n".join(lines) + "\n"


def run() -> dict[str, Any]:
    payload = build_validation()
    save_json_atomic(RAW_OUTPUT_PATH, payload)
    save_text_atomic(REPORT_OUTPUT_PATH, render_report(payload))
    return payload


def main() -> int:
    payload = run()
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload.get("summary", {}).get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
