from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.config import CONFIG_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_object_strict
from tools.core.release_proof_steps import load_release_proof_steps


RAW_PATH = RAW_DIR / "manual_validator_execution_plan_validation.json"
REPORT_PATH = REPORTS_DIR / "manual_validator_execution_plan_validation.md"
POLICY_PATH = CONFIG_DIR / "pipeline_execution_policy.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check(name: str, passed: bool, details: Any = None) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _policy() -> dict[str, Any]:
    payload = load_json_object_strict(POLICY_PATH, label="Pipeline execution policy")
    policy = payload.get("manual_validator_execution_policy") if isinstance(payload, dict) else {}
    if not isinstance(policy, dict):
        return {}
    return policy


def _selected_step_ids(policy: dict[str, Any], explicit_steps: str | None) -> list[str]:
    if explicit_steps:
        return [item.strip() for item in explicit_steps.split(",") if item.strip()]
    rows = policy.get("default_probe_steps", [])
    return [str(item) for item in rows if str(item).strip()] if isinstance(rows, list) else []


def _step_map() -> dict[str, dict[str, Any]]:
    return {str(step.get("id") or ""): step for step in load_release_proof_steps() if step.get("id")}


def _dependency_closure(steps_by_id: dict[str, dict[str, Any]], selected: list[str]) -> tuple[list[str], list[str]]:
    missing: list[str] = []
    seen: set[str] = set()
    ordered: list[str] = []

    def visit(step_id: str) -> None:
        if step_id in seen:
            return
        step = steps_by_id.get(step_id)
        if not step:
            missing.append(step_id)
            return
        seen.add(step_id)
        depends_on = step.get("depends_on", [])
        for dep in depends_on if isinstance(depends_on, list) else []:
            visit(str(dep))
        ordered.append(step_id)

    for item in selected:
        visit(item)
    return ordered, sorted(set(missing))


def _is_sqlite_writer(step: dict[str, Any]) -> bool:
    # Release-proof validators that persist a raw artifact also write state_payloads through
    # the artifact store. That makes them SQLite writers even when their primary output is JSON.
    return bool(step.get("raw_artifact"))


def _has_dependency_edge(steps_by_id: dict[str, dict[str, Any]], selected_set: set[str]) -> bool:
    for step_id in selected_set:
        deps = steps_by_id.get(step_id, {}).get("depends_on", [])
        for dep in deps if isinstance(deps, list) else []:
            if str(dep) in selected_set:
                return True
    return False


def _render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    plan = payload.get("serial_execution_plan", [])
    lines = [
        "# Manual Validator Execution Plan Validation",
        "",
        f"- status: `{summary.get('status')}`",
        f"- selected_steps: `{summary.get('selected_steps')}`",
        f"- serial_required: `{summary.get('serial_required')}`",
        f"- parallel_allowed: `{summary.get('parallel_allowed')}`",
        f"- sqlite_writers: `{summary.get('sqlite_writers')}`",
        f"- dependency_edges_present: `{summary.get('dependency_edges_present')}`",
        "",
        "## Serial Execution Plan",
        "",
    ]
    for index, row in enumerate(plan, start=1):
        lines.append(
            f"{index}. `{row.get('id')}` - sqlite_writer=`{row.get('sqlite_writer')}`, "
            f"raw_artifact=`{row.get('raw_artifact')}`"
        )
    lines.extend(["", "## Checks", "", "| Check | Result | Details |", "|---|---|---|"])
    for check in payload.get("checks", []):
        details = json.dumps(check.get("details"), ensure_ascii=False, sort_keys=True)[:900]
        lines.append(f"| `{check.get('name')}` | `{'PASS' if check.get('passed') else 'FAIL'}` | `{details}` |")
    return "\n".join(lines) + "\n"


def validate_manual_validator_execution_plan(
    *,
    explicit_steps: str | None = None,
    parallel_intent: bool = False,
) -> dict[str, Any]:
    policy = _policy()
    steps_by_id = _step_map()
    selected = _selected_step_ids(policy, explicit_steps)
    default_probe_mode = explicit_steps is None
    closure, missing = _dependency_closure(steps_by_id, selected)
    closure_set = set(closure)
    sqlite_writer_ids = [step_id for step_id in closure if _is_sqlite_writer(steps_by_id.get(step_id, {}))]
    dependency_edges_present = _has_dependency_edge(steps_by_id, closure_set)
    max_parallel_sqlite_writers = policy.get("max_parallel_sqlite_writers")
    serial_required = bool(dependency_edges_present or len(sqlite_writer_ids) > int(max_parallel_sqlite_writers or 1))
    parallel_allowed = not serial_required
    unsafe_parallel_intent = bool(parallel_intent and not parallel_allowed)
    serial_plan = [
        {
            "id": step_id,
            "label": str(steps_by_id.get(step_id, {}).get("label") or ""),
            "command": [str(item) for item in steps_by_id.get(step_id, {}).get("command", [])],
            "depends_on": [str(item) for item in steps_by_id.get(step_id, {}).get("depends_on", [])],
            "raw_artifact": str(steps_by_id.get(step_id, {}).get("raw_artifact") or ""),
            "sqlite_writer": _is_sqlite_writer(steps_by_id.get(step_id, {})),
        }
        for step_id in closure
    ]
    required_fields = {
        "default_probe_steps",
        "mode",
        "sqlite_writer_rule",
        "parallel_intent_behavior",
        "max_parallel_sqlite_writers",
    }
    checks = [
        _check(
            "policy_is_declared",
            required_fields.issubset(set(policy.keys())),
            {"missing_fields": sorted(required_fields - set(policy.keys()))},
        ),
        _check("selected_steps_exist", not missing and bool(selected), {"selected": selected, "missing": missing}),
        _check(
            "source_snapshot_symbol_span_probe_is_covered",
            not default_probe_mode or {"source_snapshot_store", "symbol_span_integrity"}.issubset(set(selected)),
            {"mode": "default_probe" if default_probe_mode else "explicit_steps", "selected": selected},
        ),
        _check(
            "dependency_closure_orders_source_snapshots_before_symbol_spans",
            not default_probe_mode
            or (
                "source_snapshot_store" in closure
                and "symbol_span_integrity" in closure
                and closure.index("source_snapshot_store") < closure.index("symbol_span_integrity")
            ),
            {"mode": "default_probe" if default_probe_mode else "explicit_steps", "closure": closure},
        ),
        _check(
            "sqlite_writers_are_serialized_when_needed",
            (serial_required and not parallel_allowed) or (not serial_required and parallel_allowed),
            {
                "sqlite_writers": sqlite_writer_ids,
                "dependency_edges_present": dependency_edges_present,
                "serial_required": serial_required,
                "parallel_allowed": parallel_allowed,
            },
        ),
        _check(
            "parallel_intent_fails_closed",
            not unsafe_parallel_intent,
            {
                "parallel_intent": parallel_intent,
                "parallel_allowed": parallel_allowed,
                "message": "Use serial_execution_plan for SQLite-writing or dependency-linked validators.",
            },
        ),
    ]
    failed = [check for check in checks if not check["passed"]]
    payload = {
        "meta": {
            "kind": "manual_validator_execution_plan_validation",
            "version": "v1",
            "generated_at": _utc_now(),
            "generator": "tools.validate_manual_validator_execution_plan",
        },
        "summary": {
            "status": "PASS" if not failed else "FAIL",
            "checks": len(checks),
            "passed": len(checks) - len(failed),
            "selected_steps": len(selected),
            "closure_steps": len(closure),
            "sqlite_writers": len(sqlite_writer_ids),
            "dependency_edges_present": dependency_edges_present,
            "serial_required": serial_required,
            "parallel_allowed": parallel_allowed,
            "parallel_intent": parallel_intent,
        },
        "policy": {
            "mode": str(policy.get("mode") or ""),
            "sqlite_writer_rule": str(policy.get("sqlite_writer_rule") or ""),
            "parallel_intent_behavior": str(policy.get("parallel_intent_behavior") or ""),
            "max_parallel_sqlite_writers": max_parallel_sqlite_writers,
        },
        "selected_steps": selected,
        "serial_execution_plan": serial_plan,
        "checks": checks,
    }
    save_json_atomic(RAW_PATH, payload)
    save_text_atomic(REPORT_PATH, _render_report(payload))
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate manual validator execution ordering.")
    parser.add_argument("--steps", default=None, help="Comma-separated release proof step ids to plan.")
    parser.add_argument("--parallel-intent", action="store_true", help="Fail closed if selected steps cannot run safely in parallel.")
    args = parser.parse_args()
    payload = validate_manual_validator_execution_plan(explicit_steps=args.steps, parallel_intent=args.parallel_intent)
    print(json.dumps(payload["summary"], ensure_ascii=False, sort_keys=True))
    return 0 if payload["summary"]["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
