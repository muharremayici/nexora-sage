from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.config import RAW_DIR, REPORTS_DIR, ROOT as ANALYZED_REPOSITORY_ROOT, save_json_atomic, save_text_atomic
from tools.core.contextos_mcp import build_surgical_operation_packet
from tools.core.json_io import load_json_file
from tools.core.audit_rules import build_rule_taxonomy
from tools.core.agent_surface_seal_contract import (
    load_agent_surface_seal_contract,
    load_agent_surface_semantic_family_checks,
)
from tools.core.agent_command_contracts import command_contracts_for_agent
from tools.core.agent_packet_budget import COMPACT_AGENT_PACKET_TOKENS
from tools.generate_nexora_operator_packet import build_operator_packet
from tools.mcp import server as mcp_server


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check(name: str, passed: bool, details: Any = None) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _log(message: str) -> None:
    print(f"[agent-semantic-smoke] {message}", file=sys.stderr, flush=True)


def _rules(payload: dict[str, Any]) -> list[str]:
    return [str(row.get("rule") or "") for row in payload.get("violations", []) if isinstance(row, dict)]


def _blocked_by_active_human_seal(payload: dict[str, Any]) -> bool:
    guard = payload.get("seal_impact_guard") if isinstance(payload.get("seal_impact_guard"), dict) else {}
    return (
        "active_human_seal_impacted" in _rules(payload)
        and bool(guard.get("blocked")) is True
        and str(guard.get("highest_impact_level") or "") == "human_reseal_required"
    )


def _expected_representative_family_count() -> int:
    semantic_checks = load_agent_surface_semantic_family_checks()
    expected = {
        str(name)
        for names in semantic_checks.values()
        for name in names
        if str(name).strip()
    }
    return len(expected)


def _search_scope_project_coverage(
    default_search_rows: list[Any],
    all_project_search_rows: list[Any],
) -> dict[str, Any]:
    """The all-project view may equal a single-project default, but may not omit it."""
    default_projects = {
        str(row.get("project") or "")
        for row in default_search_rows
        if isinstance(row, dict) and str(row.get("project") or "").strip()
    }
    all_projects = {
        str(row.get("project") or "")
        for row in all_project_search_rows
        if isinstance(row, dict) and str(row.get("project") or "").strip()
    }
    missing_default_projects = sorted(default_projects - all_projects)
    return {
        "default_projects": sorted(default_projects),
        "all_projects": sorted(all_projects),
        "default_scope_observed": bool(default_projects),
        "all_scope_preserves_default_projects": not missing_default_projects,
        "missing_default_projects": missing_default_projects,
    }


def _semantic_scenario_registry() -> dict[str, dict[str, str]]:
    contract = load_agent_surface_seal_contract()
    rows = contract.get("semantic_scenario_registry", []) if isinstance(contract, dict) else []
    registry: dict[str, dict[str, str]] = {}
    for row in rows:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        registry[str(row["id"])] = {
            "producer": str(row.get("producer") or ""),
            "role": str(row.get("role") or ""),
        }
    return registry


def _semantic_scenarios_required_by_families() -> list[str]:
    semantic_checks = load_agent_surface_semantic_family_checks()
    ordered: list[str] = []
    for names in semantic_checks.values():
        for name in names:
            value = str(name)
            if value.strip() and value not in ordered:
                ordered.append(value)
    return ordered


def _semantic_smoke_telemetry_policy() -> dict[str, Any]:
    contract = load_json_file(ROOT / "config" / "agent_surface_contract.json", {})
    policy = contract.get("semantic_smoke_telemetry_policy") if isinstance(contract, dict) else {}
    if not isinstance(policy, dict):
        policy = {}
    return {
        "max_evidence_items": int(policy.get("max_evidence_items") or 4),
        "max_unknown_items": int(policy.get("max_unknown_items") or 4),
        "max_issue_items": int(policy.get("max_issue_items") or 3),
        "max_fallback_items": int(policy.get("max_fallback_items") or 3),
        "reason_fields": [str(item) for item in policy.get("reason_fields", []) if str(item).strip()],
        "count_fields": [str(item) for item in policy.get("count_fields", []) if str(item).strip()],
        "evidence_fields": [str(item) for item in policy.get("evidence_fields", []) if str(item).strip()],
        "fallback_fields": [str(item) for item in policy.get("fallback_fields", []) if str(item).strip()],
        "unknown_fields": [str(item) for item in policy.get("unknown_fields", []) if str(item).strip()],
        "agent_rule": str(policy.get("agent_rule") or ""),
    }


def _compact_value(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return f"list[{len(value)}]"
    if isinstance(value, dict):
        return f"dict[{len(value)}]"
    if isinstance(value, set):
        return f"set[{len(value)}]"
    return type(value).__name__


def _field_value(payload: dict[str, Any], field: str) -> Any:
    if field in payload:
        return payload.get(field)
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    if field in summary:
        return summary.get(field)
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    if field in meta:
        return meta.get(field)
    details = payload.get("details") if isinstance(payload.get("details"), dict) else {}
    if field in details:
        return details.get(field)
    return None


def _bounded_named_values(payload: dict[str, Any], fields: list[str], *, limit: int) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for field in fields:
        value = _field_value(payload, field)
        if value in (None, "", [], {}):
            continue
        values[field] = _compact_value(value)
        if len(values) >= max(1, int(limit or 1)):
            break
    return values


def _result_shape(result: Any) -> str:
    if isinstance(result, dict):
        return f"dict[{len(result)}]"
    if isinstance(result, list):
        return f"list[{len(result)}]"
    if isinstance(result, set):
        return f"set[{len(result)}]"
    if isinstance(result, str):
        return f"str[{len(result)}]"
    return type(result).__name__


def _semantic_step_reason(name: str, result: Any) -> dict[str, Any]:
    policy = _semantic_smoke_telemetry_policy()
    reason: dict[str, Any] = {
        "result_shape": _result_shape(result),
        "reason": "step_returned_payload",
    }
    payload = result if isinstance(result, dict) else {}
    if isinstance(result, set):
        reason["set_size"] = len(result)
    elif isinstance(result, list):
        reason["list_size"] = len(result)
    elif isinstance(result, str):
        reason["text_chars"] = len(result)

    if payload:
        reason_fields = _bounded_named_values(payload, policy["reason_fields"], limit=policy["max_evidence_items"])
        count_fields = _bounded_named_values(payload, policy["count_fields"], limit=policy["max_evidence_items"])
        evidence_fields = _bounded_named_values(payload, policy["evidence_fields"], limit=policy["max_evidence_items"])
        fallback_fields = _bounded_named_values(payload, policy["fallback_fields"], limit=policy["max_fallback_items"])
        unknown_fields = _bounded_named_values(payload, policy["unknown_fields"], limit=policy["max_unknown_items"])
        issues = payload.get("issues") if isinstance(payload.get("issues"), list) else []
        failures = payload.get("failures") if isinstance(payload.get("failures"), list) else []
        warnings = payload.get("warnings") if isinstance(payload.get("warnings"), list) else []
        if reason_fields:
            reason["signals"] = reason_fields
        if count_fields:
            reason["counts"] = count_fields
        if evidence_fields:
            reason["evidence"] = evidence_fields
        if fallback_fields:
            reason["fallback"] = fallback_fields
        if unknown_fields:
            reason["unknowns"] = unknown_fields
        if issues:
            reason["issue_sample"] = [_compact_value(item) for item in issues[: policy["max_issue_items"]]]
        if failures:
            reason["failure_sample"] = [_compact_value(item) for item in failures[: policy["max_issue_items"]]]
        if warnings:
            reason["warning_sample"] = [_compact_value(item) for item in warnings[: policy["max_issue_items"]]]
        if payload.get("ok") is False:
            reason["reason"] = "step_reported_not_ok"
        elif payload.get("issues"):
            reason["reason"] = "step_reported_issues"
        elif payload.get("summary"):
            reason["reason"] = "step_returned_summary"
    reason["agent_rule"] = policy.get("agent_rule") or "Use semantic smoke telemetry as diagnostic context, not target-repository proof."
    return reason


_MCP_CALL_CACHE: dict[str, str] = {}


def _cached_mcp_text(key: str, producer) -> str:
    if key not in _MCP_CALL_CACHE:
        _MCP_CALL_CACHE[key] = str(producer())
    return _MCP_CALL_CACHE[key]


def _cached_mcp_json(key: str, producer) -> Any:
    return json.loads(_cached_mcp_text(key, producer))


def _timed_step(name: str, producer, telemetry: list[dict[str, Any]]) -> Any:
    started = time.monotonic()
    _log(f"START {name}")
    try:
        result = producer()
    except Exception as exc:
        duration_ms = round((time.monotonic() - started) * 1000, 2)
        telemetry.append({
            "name": name,
            "status": "ERROR",
            "duration_ms": duration_ms,
            "error": str(exc),
            "reason": "step_raised_exception",
            "evidence": {"exception_type": type(exc).__name__},
        })
        _log(f"ERROR {name} ({duration_ms}ms): {exc}")
        raise
    duration_ms = round((time.monotonic() - started) * 1000, 2)
    semantic_reason = _semantic_step_reason(name, result)
    telemetry.append({"name": name, "status": "PASS", "duration_ms": duration_ms, **semantic_reason})
    reason_label = semantic_reason.get("reason") or semantic_reason.get("result_shape") or "ok"
    _log(f"PASS {name} ({duration_ms}ms) reason={reason_label}")
    return result


def _contract_map(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = payload.get("contract_summary", []) if isinstance(payload, dict) else []
    return {str(row.get("capability_id")): row for row in rows if isinstance(row, dict) and row.get("capability_id")}


def _config_contract_map() -> dict[str, dict[str, Any]]:
    payload = load_json_file(ROOT / "config" / "engine_signal_contracts.json", {})
    rows = payload.get("contracts", []) if isinstance(payload, dict) else []
    return {str(row.get("capability_id")): row for row in rows if isinstance(row, dict) and row.get("capability_id")}


def _vocabulary_confidence() -> set[str]:
    payload = load_json_file(ROOT / "config" / "evidence_vocabulary.json", {})
    values = set()
    for row in payload.get("confidence_levels", []) if isinstance(payload, dict) else []:
        if isinstance(row, dict) and row.get("id"):
            values.add(str(row["id"]))
    return values


def _summary(path: str) -> dict[str, Any]:
    payload = load_json_file(RAW_DIR / path, {})
    return payload.get("summary", {}) if isinstance(payload, dict) and isinstance(payload.get("summary"), dict) else {}


def _approval_required_for_mode(mode: str) -> bool:
    return str(mode or "").strip().lower() in {"enforced", "heal", "critical"}


def _work_queue_projection() -> dict[str, Any]:
    payload = _cached_mcp_json(
        "violation_work_queue:page_size=10:json",
        lambda: mcp_server.get_violation_work_queue(page_size=10, format="json"),
    )
    brief = _cached_mcp_text(
        "violation_work_queue:page_size=5:brief",
        lambda: mcp_server.get_violation_work_queue(page_size=5),
    )
    items = payload.get("items") if isinstance(payload, dict) and isinstance(payload.get("items"), list) else []
    return {"payload": payload if isinstance(payload, dict) else {}, "brief": brief, "items": items}


def _approval_consistency_report(operator_packet: dict[str, Any], work_queue: dict[str, Any]) -> dict[str, Any]:
    rule_profiles = (build_rule_taxonomy().get("profiles", {}) or {})
    mismatches: list[dict[str, Any]] = []
    projection_issues: list[dict[str, Any]] = []
    checked = 0

    def inspect_projection(surface: str, row: dict[str, Any], mode: str) -> None:
        reason = str(row.get("approval_reason") or "").strip()
        projected_mode = str(row.get("rule_mode") or "").strip().lower()
        decision_source = str(row.get("approval_decision_source") or "").strip().lower()
        if projected_mode != mode:
            projection_issues.append(
                {
                    "surface": surface,
                    "id": row.get("id"),
                    "issue": "rule_mode_projection_mismatch",
                    "expected": mode,
                    "actual": projected_mode,
                }
            )
        if not reason:
            projection_issues.append(
                {"surface": surface, "id": row.get("id"), "issue": "approval_reason_missing"}
            )
        elif len(reason) > COMPACT_AGENT_PACKET_TOKENS // 4:
            projection_issues.append(
                {
                    "surface": surface,
                    "id": row.get("id"),
                    "issue": "approval_reason_exceeds_shared_compact_budget",
                    "chars": len(reason),
                }
            )
        if decision_source not in {"rule_mode", "explicit_override"}:
            projection_issues.append(
                {
                    "surface": surface,
                    "id": row.get("id"),
                    "issue": "approval_decision_source_missing_or_unknown",
                    "actual": decision_source,
                }
            )

    for directive in (
        (operator_packet.get("target_repository_agent_surface") or {}).get("directives", [])
        if isinstance(operator_packet, dict)
        else []
    ):
        if not isinstance(directive, dict):
            continue
        explanation = directive.get("rule_explanation") if isinstance(directive.get("rule_explanation"), dict) else {}
        mode = str(explanation.get("mode") or "").lower()
        if not mode:
            continue
        checked += 1
        expected = _approval_required_for_mode(mode)
        actual = directive.get("human_approval_required") is True
        inspect_projection("operator_directive", directive, mode)
        override_is_declared = directive.get("approval_decision_source") == "explicit_override"
        if expected != actual and not override_is_declared:
            mismatches.append(
                {
                    "surface": "operator_directive",
                    "id": directive.get("id"),
                    "rule": directive.get("rule"),
                    "mode": mode,
                    "expected": expected,
                    "actual": actual,
                }
            )

    queue_payload = work_queue.get("payload") if isinstance(work_queue.get("payload"), dict) else {}
    for item in queue_payload.get("items", []) if isinstance(queue_payload, dict) else []:
        if not isinstance(item, dict):
            continue
        rule = str(item.get("rule") or "")
        profile = rule_profiles.get(rule, {}) if isinstance(rule_profiles, dict) else {}
        mode = str(item.get("mode") or "").lower()
        if mode == "unknown":
            mode = ""
        if not mode:
            mode = str(
                item.get("governance_mode")
                or profile.get("mode")
                or profile.get("default_mode")
                or ""
            ).lower()
        if not mode:
            continue
        checked += 1
        expected = _approval_required_for_mode(mode)
        actual = item.get("human_approval_required") is True
        inspect_projection("work_queue", item, mode)
        override_is_declared = item.get("approval_decision_source") == "explicit_override"
        if expected != actual and not override_is_declared:
            mismatches.append(
                {
                    "surface": "work_queue",
                    "id": item.get("id"),
                    "rule": rule,
                    "mode": mode,
                    "expected": expected,
                    "actual": actual,
                    "target_ref": item.get("target_ref"),
                }
            )

    operator_surface = (
        operator_packet.get("target_repository_agent_surface")
        if isinstance(operator_packet.get("target_repository_agent_surface"), dict)
        else {}
    )
    operator_brief = mcp_server._render_operator_agent_surface_brief(operator_surface)
    work_queue_brief = str(work_queue.get("brief") or "")
    brief_issues = [
        {"surface": name, "issue": "authority_projection_missing_from_brief", "marker": marker}
        for name, brief in (("operator_directive", operator_brief), ("work_queue", work_queue_brief))
        for marker in ("rule_mode:", "approval_decision_source:", "approval_reason:")
        if marker not in brief
    ]
    return {
        "checked": checked,
        "mismatches": mismatches,
        "projection_issues": projection_issues,
        "brief_issues": brief_issues,
    }


def _merge_queue_grounding_report() -> dict[str, Any]:
    try:
        payload = _cached_mcp_json(
            "merge_review_queue:max_items=20:json",
            lambda: mcp_server.get_merge_review_queue(max_items=20, format="json"),
        )
    except Exception as exc:
        return {"checked": 0, "issues": [{"error": str(exc)}]}

    root = Path(str(payload.get("analysis_root") or ROOT)).resolve()
    issues: list[dict[str, Any]] = []
    checked = 0
    items = payload.get("items", []) if isinstance(payload, dict) else []
    validation_policy = payload.get("validation") if isinstance(payload.get("validation"), dict) else {}
    validation_rows = [
        row
        for key in ("pre_approval_tools", "post_approval_tools")
        for row in (validation_policy.get(key) if isinstance(validation_policy.get(key), list) else [])
        if isinstance(row, dict)
    ]
    validation_tool_names = {str(row.get("tool") or "") for row in validation_rows}
    required_validation_tools = {"inspect_file", "get_impact_radius", "get_test_impact", "validate_patch"}
    incomplete_evidence = (
        isinstance(payload, dict)
        and payload.get("status") == "INCOMPLETE_EVIDENCE"
        and items == []
        and int(payload.get("total_candidates") or 0) == 0
        and payload.get("policy_boundary", {}).get("mutation_allowed_by_this_packet") is False
        and payload.get("policy_boundary", {}).get("human_approval_required") is True
        and bool(str(payload.get("required_action") or "").strip())
        and (payload.get("target_proof") or {}).get("verdict") in {"BLOCKED", "UNKNOWN"}
    )
    if incomplete_evidence:
        blocking_evidence = (payload.get("target_proof") or {}).get("blocking_evidence") or []
        if (payload.get("target_proof") or {}).get("verdict") == "BLOCKED" and not blocking_evidence:
            issues.append({"issue": "merge_review_blocked_proof_omits_required_blocker"})
        return {"checked": 1, "mode": "incomplete_evidence", "issues": issues}
    if validation_policy.get("mode") != "review_only_until_human_approval":
        issues.append({"issue": "merge_review_validation_mode_missing_or_unsafe", "validation": validation_policy})
    if not required_validation_tools.issubset(validation_tool_names):
        issues.append({"issue": "merge_review_validation_tools_incomplete", "missing": sorted(required_validation_tools - validation_tool_names)})
    if (
        isinstance(payload, dict)
        and items == []
        and int(payload.get("total_candidates") or 0) == 0
        and payload.get("policy_boundary", {}).get("mutation_allowed_by_this_packet") is False
        and payload.get("policy_boundary", {}).get("human_approval_required") is True
    ):
        return {"checked": 1, "mode": "empty_review_only_queue", "issues": issues}
    for item in items:
        if not isinstance(item, dict):
            continue
        checked += 1
        source_file = str(item.get("source_file") or "").replace("\\", "/")
        target_path = str(item.get("target_path") or "").replace("\\", "/")
        source_abs = (root / source_file).resolve() if source_file else None
        target_abs = (root / target_path).resolve() if target_path else None
        if not source_file or not source_abs or not source_abs.exists():
            issues.append({"candidate": item.get("candidate"), "issue": "missing_source_file", "source_file": source_file})
        if not target_path or not target_abs or root not in [target_abs, *target_abs.parents]:
            issues.append({"candidate": item.get("candidate"), "issue": "target_path_outside_root", "target_path": target_path})
        if payload.get("policy_boundary", {}).get("mutation_allowed_by_this_packet") is not False:
            issues.append({"candidate": item.get("candidate"), "issue": "merge_packet_allows_mutation"})

    return {"checked": checked, "issues": issues}


def _no_op_patch_report() -> dict[str, Any]:
    try:
        payload = _cached_mcp_json(
            "validate_patch:src/main.tsx:empty:json",
            lambda: mcp_server.validate_patch("src/main.tsx", "", format="json"),
        )
        no_effect = json.loads(
            mcp_server.validate_patch(
                "src/shared/utils/text.ts",
                "--- a/src/shared/utils/text.ts\n+++ b/src/shared/utils/text.ts\n@@ -10,7 +10,7 @@\n-  if (!html) return '';\n+  if (!html) return '';\n",
                format="json",
            )
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    no_effect_rules = _rules(no_effect)
    no_effect_status_ok = no_effect.get("status") == "NO_OP" or (
        no_effect.get("status") == "FAIL" and _blocked_by_active_human_seal(no_effect)
    )
    return {
        "ok": (
            payload.get("status") == "NO_OP"
            and payload.get("safe_to_apply") is False
            and payload.get("no_op_patch") is True
            and no_effect_status_ok
            and no_effect.get("safe_to_apply") is False
            and no_effect.get("no_op_patch") is True
            and "mcp_no_effect_patch" in no_effect_rules
        ),
        "status": payload.get("status"),
        "safe_to_apply": payload.get("safe_to_apply"),
        "no_op_patch": payload.get("no_op_patch"),
        "no_effect_status": no_effect.get("status"),
        "no_effect_safe_to_apply": no_effect.get("safe_to_apply"),
        "no_effect_no_op_patch": no_effect.get("no_op_patch"),
    }


def _patch_grounding_report() -> dict[str, Any]:
    try:
        missing = _cached_mcp_json(
            "validate_patch:src/does-not-exist.ts:sample-export:json",
            lambda: mcp_server.validate_patch("src/does-not-exist.ts", "export const x = 1;\n", format="json"),
        )
        negative = json.loads(
            mcp_server.validate_patch(
                "src/main.tsx",
                "import x from '../outside';\nexport default function Demo(){ return null; }\n",
                format="json",
            )
        )
        invalid = json.loads(
            mcp_server.validate_patch(
                "MAIN::src/App.tsx",
                "diff --git a/src/App.tsx b/src/App.tsx",
                format="json",
            )
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    missing_rules = _rules(missing)
    negative_rules = _rules(negative)
    invalid_rules = _rules(invalid)
    return {
        "ok": (
            missing.get("status") == "FAIL"
            and missing.get("safe_to_apply") is False
            and missing.get("target_exists") is False
            and missing.get("target_indexed") is False
            and "mcp_target_not_grounded" in missing_rules
            and negative.get("status") in {"FAIL", "REVIEW_REQUIRED"}
            and negative.get("safe_to_apply") is False
            and "relative_imports_no_alias" in negative_rules
            and invalid.get("status") == "FAIL"
            and invalid.get("safe_to_apply") is False
            and invalid.get("invalid_patch") is True
            and "mcp_invalid_patch_format" in invalid_rules
        ),
        "missing": {
            "status": missing.get("status"),
            "safe_to_apply": missing.get("safe_to_apply"),
            "target_exists": missing.get("target_exists"),
            "target_indexed": missing.get("target_indexed"),
            "rules": missing_rules,
        },
        "negative": {
            "status": negative.get("status"),
            "safe_to_apply": negative.get("safe_to_apply"),
            "rules": negative_rules,
        },
        "invalid": {
            "status": invalid.get("status"),
            "safe_to_apply": invalid.get("safe_to_apply"),
            "invalid_patch": invalid.get("invalid_patch"),
            "rules": invalid_rules,
        },
    }


def _patch_target_root_regression_report() -> dict[str, Any]:
    target_file = "src/App.tsx"
    target_abs = (Path(ANALYZED_REPOSITORY_ROOT) / target_file).resolve()
    if not target_abs.exists():
        return {"ok": False, "error": "target_file_missing", "target_abs": str(target_abs)}
    try:
        current = target_abs.read_text(encoding="utf-8", errors="replace")
        needle = "h-screen"
        replacement = "min-h-screen"
        if needle not in current:
            return {"ok": False, "error": "fixture_needle_missing", "target_file": target_file}
        proposed = current.replace(needle, replacement, 1)
        payload = json.loads(mcp_server.validate_patch(target_file, proposed, format="json"))
        brief = mcp_server.validate_patch(target_file, proposed, format="brief")
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    existing = payload.get("existing_violations") if isinstance(payload.get("existing_violations"), list) else []
    snippets = payload.get("proposed_change_snippets") if isinstance(payload.get("proposed_change_snippets"), list) else []
    snippet_text = json.dumps(snippets, ensure_ascii=False)
    expected_patch_gate_status = payload.get("status") == "REVIEW_REQUIRED" or (
        payload.get("status") == "FAIL" and _blocked_by_active_human_seal(payload)
    )
    expectations = {
        "expected_patch_gate_status": expected_patch_gate_status,
        "safe_to_apply_false": payload.get("safe_to_apply") is False,
        "human_approval_required": payload.get("human_approval_required") is True,
        "full_replacement_patch": payload.get("full_replacement_patch") is True,
        "target_file": payload.get("target_file") == target_file,
        "target_ref": payload.get("target_ref") == "MAIN::src/App.tsx",
        "snippet_status": "included_full_replacement_diff_hunk" in snippet_text,
        "snippet_old_line": f"-  <div" in snippet_text,
        "snippet_replacement": replacement in snippet_text,
        "brief_existing_debt": "existing_violations_context:" in brief,
        "brief_snippet_status": "included_full_replacement_diff_hunk" in brief,
        "brief_full_replacement_rule": (
            "mcp_full_replacement_requires_human_approval" in brief
            or (
                _blocked_by_active_human_seal(payload)
                and "active_human_seal_impacted" in brief
                and "Full-file replacement requires human approval" in brief
            )
        ),
        "brief_human_approval_action": "request_human_approval_or_provide_bounded_unified_diff" in brief,
        "brief_instruction": "instruction:" in brief,
        "brief_human_text": "Full-file replacement requires human approval" in brief,
        "brief_agent_rule": "agent_rule:" in brief,
    }
    return {
        "ok": all(expectations.values()),
        "status": payload.get("status"),
        "safe_to_apply": payload.get("safe_to_apply"),
        "full_replacement_patch": payload.get("full_replacement_patch"),
        "human_approval_required": payload.get("human_approval_required"),
        "target_file": payload.get("target_file"),
        "target_ref": payload.get("target_ref"),
        "rules": _rules(payload),
        "expectations": expectations,
        "existing_violation_count": len(existing),
        "snippet_statuses": [row.get("snippet_status") for row in snippets if isinstance(row, dict)],
    }


def _confidence_grounding_report() -> dict[str, Any]:
    try:
        payload = _cached_mcp_json(
            "confidence_score:src/main.tsx:json",
            lambda: mcp_server.get_confidence_score("src/main.tsx", format="json"),
        )
        brief = _cached_mcp_text("confidence_score:src/main.tsx:brief", lambda: mcp_server.get_confidence_score("src/main.tsx"))
        high_impact = _cached_mcp_json(
            "confidence_score:src/shared/utils/text.ts:json",
            lambda: mcp_server.get_confidence_score("src/shared/utils/text.ts", format="json"),
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    root = Path(str(payload.get("analysis_root") or ROOT)).resolve()
    target_file = str(payload.get("target_file") or "").replace("\\", "/").strip("/")
    target_exists = bool(target_file) and (root / target_file).exists()
    verdict = str(payload.get("verdict") or "")
    decision_boundary = str(payload.get("decision_boundary") or "")
    high_matrix = high_impact.get("confidence_matrix") if isinstance(high_impact.get("confidence_matrix"), dict) else {}
    high_metrics = high_impact.get("metrics") if isinstance(high_impact.get("metrics"), dict) else {}
    high_reasons = high_impact.get("reasons") if isinstance(high_impact.get("reasons"), list) else []
    return {
        "ok": (
            target_exists
            and payload.get("target_ref") == "MAIN::src/main.tsx"
            and "auto-deploy" not in verdict.lower()
            and "not a standalone merge or deploy approval" in decision_boundary.lower()
            and "decision_boundary:" in brief
            and "auto-deploy" not in brief.lower()
            and high_matrix.get("merge_safety") == "CRITICAL"
            and int(high_metrics.get("blast_radius_dependents") or 0) >= 10
            and high_metrics.get("confidence_dependency_source") == "sqlite_dependencies"
            and any("SQLite impact radius reports" in str(item) for item in high_reasons)
        ),
        "target_file": target_file,
        "target_exists": target_exists,
        "target_ref": payload.get("target_ref"),
        "verdict": verdict,
        "decision_boundary": decision_boundary,
        "high_impact_merge_safety": high_matrix.get("merge_safety"),
        "high_impact_dependents": high_metrics.get("blast_radius_dependents"),
        "high_impact_dependency_source": high_metrics.get("confidence_dependency_source"),
    }


def _search_and_inspection_grounding_report() -> dict[str, Any]:
    try:
        search_rows = _cached_mcp_json(
            "search_symbols:main:MAIN:json",
            lambda: mcp_server.search_symbols("main", project="MAIN", format="json"),
        )
        default_search_rows = _cached_mcp_json(
            "search_symbols:AppLayout:default:json",
            lambda: mcp_server.search_symbols("AppLayout", format="json"),
        )
        all_project_search_rows = _cached_mcp_json(
            "search_symbols:AppLayout:all:json",
            lambda: mcp_server.search_symbols("AppLayout", project="all", format="json"),
        )
        inspect_file_payload = _cached_mcp_json(
            "inspect_file:MAIN::src/main.tsx:json",
            lambda: mcp_server.inspect_file("MAIN::src/main.tsx", format="json"),
        )
        inspect_symbol_payload = _cached_mcp_json(
            "inspect_symbol:main:json",
            lambda: mcp_server.inspect_symbol("main", format="json"),
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    root = Path(str(inspect_file_payload.get("analysis_root") or mcp_server._analysis_root_display())).resolve()
    issues: list[dict[str, Any]] = []
    first = search_rows[0] if isinstance(search_rows, list) and search_rows else {}
    first_file = str(first.get("repo_relative_path") or first.get("file") or "").replace("\\", "/").strip("/") if isinstance(first, dict) else ""
    if first_file != "src/main.tsx":
        issues.append({"issue": "search_first_result_not_exact_main_file", "first_file": first_file, "first": first})
    if not (root / first_file).exists():
        issues.append({"issue": "search_first_result_not_openable", "first_file": first_file})

    search_scope_coverage = _search_scope_project_coverage(
        default_search_rows if isinstance(default_search_rows, list) else [],
        all_project_search_rows if isinstance(all_project_search_rows, list) else [],
    )
    default_projects = set(search_scope_coverage["default_projects"])
    default_non_main = sorted(project for project in default_projects if project and project.upper() != "MAIN")
    if default_non_main:
        issues.append({"issue": "search_symbols_default_leaks_variation_projects", "projects": default_non_main[:10]})
    all_projects = set(search_scope_coverage["all_projects"])
    if not search_scope_coverage["default_scope_observed"]:
        issues.append(
            {
                "issue": "search_symbols_default_scope_not_observed",
                **search_scope_coverage,
            }
        )
    elif not search_scope_coverage["all_scope_preserves_default_projects"]:
        issues.append(
            {
                "issue": "search_symbols_all_scope_omits_default_projects",
                **search_scope_coverage,
            }
        )

    file_contexts = inspect_file_payload.get("target_file_context") if isinstance(inspect_file_payload, dict) else []
    inspect_file_paths = [
        str(row.get("workspace_rel") or row.get("file") or "").replace("\\", "/").strip("/")
        for row in file_contexts
        if isinstance(row, dict)
    ]
    if "src/main.tsx" not in inspect_file_paths:
        issues.append({"issue": "inspect_file_missing_main_target_context", "paths": inspect_file_paths[:5]})
    for path in inspect_file_paths[:5]:
        if path and not (root / path).exists():
            issues.append({"issue": "inspect_file_path_not_openable", "path": path})

    symbol_rows = inspect_symbol_payload.get("atlas_symbols") if isinstance(inspect_symbol_payload, dict) else []
    symbol_paths = [
        str(row.get("workspace_rel") or row.get("file") or "").replace("\\", "/").strip("/")
        for row in symbol_rows
        if isinstance(row, dict)
    ]
    if not symbol_paths or symbol_paths[0] != "src/main.tsx":
        issues.append({"issue": "inspect_symbol_first_target_not_exact_main_file", "paths": symbol_paths[:5]})
    for path in symbol_paths[:10]:
        if path and not (root / path).exists():
            issues.append({"issue": "inspect_symbol_path_not_openable", "path": path})

    return {
        "ok": not issues,
        "issues": issues,
        "search_first": first_file,
        "default_search_projects": sorted(default_projects),
        "all_search_projects": sorted(all_projects),
        "inspect_file_paths": inspect_file_paths[:5],
        "inspect_symbol_paths": symbol_paths[:5],
    }


def _test_impact_grounding_report() -> dict[str, Any]:
    try:
        payload = _cached_mcp_json(
            "test_impact:src/shared/utils/text.ts:json",
            lambda: mcp_server.get_test_impact("src/shared/utils/text.ts", format="json"),
        )
    except Exception as exc:
        return {"checked": 0, "issues": [{"error": str(exc)}]}

    root = Path(str(payload.get("analysis_root") or ROOT)).resolve()
    tests = payload.get("impacted_tests") if isinstance(payload.get("impacted_tests"), list) else []
    issues: list[dict[str, Any]] = []
    checked = 0
    if not tests:
        issues.append({"issue": "missing_impacted_tests", "target": payload.get("target_ref") or payload.get("target")})

    for row in tests[:10]:
        if not isinstance(row, dict):
            continue
        checked += 1
        test_path = str(row.get("repo_relative_path") or row.get("file") or "").replace("\\", "/").strip("/")
        command = str(row.get("run_command") or "")
        if not test_path:
            issues.append({"issue": "missing_test_path", "row": row})
            continue
        if not (root / test_path).exists():
            issues.append({"issue": "test_file_not_openable", "test_path": test_path})
        if command and test_path not in command:
            issues.append({"issue": "run_command_does_not_target_openable_test_path", "test_path": test_path, "command": command})

    return {"checked": checked, "issues": issues}


def _select_bidirectional_dependency_target(circular_data: dict[str, Any]) -> str:
    outgoing: dict[str, set[str]] = {}
    incoming: dict[str, set[str]] = {}
    edges = circular_data.get("edges") if isinstance(circular_data.get("edges"), list) else []
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        source = str(edge.get("source") or "").strip()
        target = str(edge.get("target") or "").strip()
        if not source or not target or source == target:
            continue
        outgoing.setdefault(source, set()).add(target)
        incoming.setdefault(target, set()).add(source)

    candidates = set(outgoing).intersection(incoming)
    if not candidates:
        return ""
    return sorted(
        candidates,
        key=lambda node: (
            -min(len(outgoing[node]), len(incoming[node])),
            -(len(outgoing[node]) + len(incoming[node])),
            node,
        ),
    )[0]


def _upstream_trace_grounding_report(circular_data: dict[str, Any]) -> dict[str, Any]:
    target = _select_bidirectional_dependency_target(circular_data)
    if not target:
        return {
            "checked": 0,
            "selected_target": None,
            "issues": [{"issue": "no_bidirectional_dependency_candidate"}],
        }
    try:
        payload = _cached_mcp_json(
            f"trace_upstream:{target}:json",
            lambda: mcp_server.trace_upstream_cause(target, format="json"),
        )
        brief = _cached_mcp_text(f"trace_upstream:{target}:brief", lambda: mcp_server.trace_upstream_cause(target))
    except Exception as exc:
        return {"checked": 0, "selected_target": target, "issues": [{"error": str(exc)}]}

    root = Path(str(payload.get("analysis_root") or ROOT)).resolve()
    issues: list[dict[str, Any]] = []
    checked = 0
    upstream = payload.get("upstream_dependency_files") if isinstance(payload.get("upstream_dependency_files"), list) else []
    dependents = payload.get("direct_dependent_files") if isinstance(payload.get("direct_dependent_files"), list) else []
    if not upstream:
        issues.append({"issue": "missing_upstream_dependency_files"})
    if not dependents:
        issues.append({"issue": "missing_direct_dependent_files"})

    for path in [*upstream[:10], *dependents[:10]]:
        checked += 1
        rel = str(path or "").replace("\\", "/").strip("/")
        if not rel:
            issues.append({"issue": "empty_trace_path"})
        elif not (root / rel).exists():
            issues.append({"issue": "trace_path_not_openable", "path": rel})

    required_brief_markers = [
        "upstream_dependency_count:",
        "direct_dependent_count:",
        "direct_dependents_sample:",
    ]
    missing_markers = [marker for marker in required_brief_markers if marker not in brief]
    if missing_markers:
        issues.append({"issue": "brief_hides_trace_context", "missing_markers": missing_markers})
    if (
        "inspect_upstream_candidates_before_local_patch" not in brief
        and (
            "refresh_target_analysis_before_upstream_decision" not in brief
            or "target_grounding_status:" not in brief
        )
    ):
        issues.append({"issue": "brief_hides_safe_upstream_next_action"})

    return {"checked": checked, "selected_target": target, "issues": issues}


def _clean_sage_audit_queue_contract(payload: dict[str, Any]) -> dict[str, Any]:
    items = payload.get("items") if isinstance(payload.get("items"), list) else None
    coverage = payload.get("coverage") if isinstance(payload.get("coverage"), dict) else {}
    trust = payload.get("artifact_trust") if isinstance(payload.get("artifact_trust"), dict) else {}
    trust_scope = trust.get("scope") if isinstance(trust.get("scope"), dict) else {}
    authority = payload.get("authority_projection") if isinstance(payload.get("authority_projection"), dict) else {}
    filters = payload.get("filters") if isinstance(payload.get("filters"), dict) else {}
    requested_project = str(filters.get("project") or "").strip()
    audited_projects = {
        str(project) for project in trust_scope.get("audited_projects", []) if str(project).strip()
    }
    expectations = {
        "status": payload.get("status") == "clean_within_sage_audit",
        "items": items == [],
        "total_violations": int(payload.get("total_violations") or 0) == 0,
        "queue_source": payload.get("queue_source") in {"sqlite_findings", "audit_report_payload"},
        "analysis_root": bool(str(payload.get("analysis_root") or "").strip()),
        "analysis_snapshot_id": bool(str(payload.get("analysis_snapshot_id") or "").strip()),
        "requested_project": bool(requested_project),
        "requested_project_audited": requested_project in audited_projects,
        "coverage_status": coverage.get("status") == "partial",
        "sage_audit_evaluated": coverage.get("sage_audit") == "evaluated",
        "target_native_not_evaluated": coverage.get("target_native") == "not_evaluated",
        "combined_verdict_unavailable": coverage.get("combined_verdict") == "not_available",
        "clean_scope": coverage.get("clean_scope") == "sage_audit_only",
        "artifact_trust": str(trust.get("status") or "").upper() == "PASS",
        "artifact_trust_failures": trust.get("failures") == [],
        "actionability": authority.get("actionability") == "no_action",
        "mutation_not_proposed": authority.get("mutation_proposed") is False,
        "mutation_authority_not_granted": authority.get("mutation_authority") == "not_granted_by_this_directive",
    }
    return {"ok": all(expectations.values()), "expectations": expectations}


def _brief_has_scalar(brief: str, key: str, value: str) -> bool:
    return f'{key}: "{value}"' in brief or f"{key}: {value}" in brief


def _work_queue_grounding_report(work_queue: dict[str, Any]) -> dict[str, Any]:
    payload = work_queue.get("payload") if isinstance(work_queue.get("payload"), dict) else {}
    brief = str(work_queue.get("brief") or "")

    root = Path(str(payload.get("analysis_root") or ROOT)).resolve()
    issues: list[dict[str, Any]] = []
    checked = 0
    items = payload.get("items", []) if isinstance(payload, dict) else []
    status = str(payload.get("status") or "")
    total_violations = int(payload.get("total_violations") or 0)
    clean_contract = _clean_sage_audit_queue_contract(payload)
    if clean_contract["ok"]:
        if not _brief_has_scalar(brief, "status", "clean_within_sage_audit"):
            issues.append({"issue": "clean_queue_brief_missing_clean_status"})
        if "items:" not in brief:
            issues.append({"issue": "clean_queue_brief_missing_items_section"})
        if not _brief_has_scalar(brief, "combined_verdict", "not_available"):
            issues.append({"issue": "clean_queue_brief_hides_combined_verdict_boundary"})
        return {
            "checked": 0,
            "mode": "clean_within_sage_audit",
            "issues": issues,
            "clean_contract": clean_contract,
        }
    if not items and total_violations == 0:
        issues.append(
            {
                "issue": "empty_queue_missing_fail_closed_clean_contract",
                "status": status,
                "clean_contract": clean_contract,
            }
        )

    for item in items:
        if not isinstance(item, dict):
            continue
        checked += 1
        target_file = str(item.get("target_file") or "").replace("\\", "/").strip("/")
        target_ref = str(item.get("target_ref") or "")
        if not target_file:
            issues.append({"issue": "missing_target_file", "id": item.get("id")})
        elif not (root / target_file).exists():
            issues.append({"issue": "target_file_not_openable", "target_file": target_file, "id": item.get("id")})
        if not target_ref or not target_ref.endswith(target_file):
            issues.append({"issue": "target_ref_not_consistent_with_target_file", "target_file": target_file, "target_ref": target_ref})
        for path in item.get("inspect_first", []) if isinstance(item.get("inspect_first"), list) else []:
            inspect_path = str(path or "").replace("\\", "/").strip("/")
            if inspect_path and not (root / inspect_path).exists():
                issues.append({"issue": "inspect_first_not_openable", "path": inspect_path, "id": item.get("id")})
        validation_tools = item.get("validation_tools") if isinstance(item.get("validation_tools"), list) else []
        if not validation_tools:
            issues.append({"issue": "missing_target_repo_validation_tools", "id": item.get("id")})
    if "evidence_status:" in brief or "raw_returned:" in brief:
        issues.append({"issue": "brief_exposes_internal_artifact_accounting"})
    if "returned_work_items:" not in brief:
        issues.append({"issue": "brief_missing_operational_returned_work_items_count"})
    return {"checked": checked, "issues": issues}


def _agent_task_scenario_report(work_queue: dict[str, Any]) -> dict[str, Any]:
    """Follow one queued task through the MCP tools a coding agent would call."""
    queue = work_queue.get("payload") if isinstance(work_queue.get("payload"), dict) else {}

    root = Path(str(queue.get("analysis_root") or ROOT)).resolve()
    items = queue.get("items") if isinstance(queue.get("items"), list) else []
    if not items:
        status = str(queue.get("status") or "")
        total_violations = int(queue.get("total_violations") or 0)
        issues = []
        clean_contract = _clean_sage_audit_queue_contract(queue)
        if not clean_contract["ok"]:
            issues.append(
                {
                    "stage": "queue",
                    "issue": "no_work_queue_items_without_clean_contract",
                    "status": status,
                    "total_violations": total_violations,
                    "clean_contract": clean_contract,
                }
            )
        return {
            "checked": 0,
            "mode": "clean_within_sage_audit",
            "issues": issues,
            "clean_contract": clean_contract,
        }

    item = items[0]
    target_file = str(item.get("target_file") or "").replace("\\", "/").strip("/")
    target_ref = str(item.get("target_ref") or "")
    issues: list[dict[str, Any]] = []
    checked = 0

    if not target_file or not (root / target_file).exists():
        issues.append({"stage": "queue", "issue": "target_file_not_openable", "target_file": target_file})
    if not target_ref or not target_ref.endswith(target_file):
        issues.append({"stage": "queue", "issue": "target_ref_not_canonical_for_target_file", "target_ref": target_ref, "target_file": target_file})

    calls = {
        "inspect": lambda: _cached_mcp_json(f"inspect_file:{target_ref}:json", lambda: mcp_server.inspect_file(target_ref, format="json")),
        "impact": lambda: _cached_mcp_json(f"impact_radius:{target_ref}:json", lambda: mcp_server.get_impact_radius(target_ref, format="json")),
        "test": lambda: _cached_mcp_json(f"test_impact:{target_ref}:json", lambda: mcp_server.get_test_impact(target_ref, format="json")),
        "confidence": lambda: _cached_mcp_json(f"confidence_score:{target_ref}:json", lambda: mcp_server.get_confidence_score(target_ref, format="json")),
        "upstream": lambda: _cached_mcp_json(f"trace_upstream:{target_ref}:json", lambda: mcp_server.trace_upstream_cause(target_ref, format="json")),
        "validate_patch": lambda: _cached_mcp_json(f"validate_patch:{target_ref}:empty:json", lambda: mcp_server.validate_patch(target_ref, "", format="json")),
    }
    stage_payloads: dict[str, dict[str, Any]] = {}
    for stage, call in calls.items():
        try:
            payload = call()
        except Exception as exc:
            issues.append({"stage": stage, "issue": "call_failed", "error": str(exc)})
            continue
        checked += 1
        stage_payloads[stage] = payload if isinstance(payload, dict) else {}
        payload_file = str((payload if isinstance(payload, dict) else {}).get("target_file") or "").replace("\\", "/").strip("/")
        payload_ref = str((payload if isinstance(payload, dict) else {}).get("target_ref") or "")
        if stage == "inspect":
            files = payload.get("target_files") if isinstance(payload.get("target_files"), list) else []
            refs = payload.get("target_refs") if isinstance(payload.get("target_refs"), list) else []
            if target_file not in [str(path).replace("\\", "/").strip("/") for path in files]:
                issues.append({"stage": stage, "issue": "target_file_not_preserved", "target_file": target_file, "files": files[:5]})
            if target_ref not in [str(ref) for ref in refs]:
                issues.append({"stage": stage, "issue": "target_ref_not_preserved", "target_ref": target_ref, "refs": refs[:5]})
        else:
            if payload_file != target_file:
                issues.append({"stage": stage, "issue": "target_file_changed_across_tool", "expected": target_file, "actual": payload_file})
            if payload_ref and payload_ref != target_ref:
                issues.append({"stage": stage, "issue": "target_ref_changed_across_tool", "expected": target_ref, "actual": payload_ref})
        if payload_file and not (root / payload_file).exists():
            issues.append({"stage": stage, "issue": "payload_target_file_not_openable", "target_file": payload_file})

    patch = stage_payloads.get("validate_patch", {})
    if patch.get("status") != "NO_OP" or patch.get("safe_to_apply") is not False or patch.get("no_op_patch") is not True:
        issues.append(
            {
                "stage": "validate_patch",
                "issue": "empty_patch_must_not_be_apply_permission",
                "status": patch.get("status"),
                "safe_to_apply": patch.get("safe_to_apply"),
                "no_op_patch": patch.get("no_op_patch"),
            }
        )

    return {
        "checked": checked,
        "target_file": target_file,
        "target_ref": target_ref,
        "tool_chain": list(calls.keys()),
        "issues": issues,
    }


def _scenario_work_queue_architecture_violation(work_queue: dict[str, Any], root: Path, raw_dir: Path) -> dict[str, Any]:
    del raw_dir
    queue = work_queue.get("payload") if isinstance(work_queue.get("payload"), dict) else {}
    items = queue.get("items") if isinstance(queue.get("items"), list) else []
    clean_contract = _clean_sage_audit_queue_contract(queue)
    if not items and clean_contract["ok"]:
        return {
            "ok": True,
            "mode": "clean_within_sage_audit",
            "target_file": "",
            "target_ref": "",
            "inspect_first_count": 0,
            "clean_contract": clean_contract,
        }
    item = (items or [{}])[0] if isinstance(queue, dict) else {}
    target_file = str(item.get("target_file") or "").replace("\\", "/").strip("/")
    target_ref = str(item.get("target_ref") or "")
    inspect_first = item.get("inspect_first") if isinstance(item.get("inspect_first"), list) else []
    ok = bool(target_file and (root / target_file).exists() and target_ref.endswith(target_file))
    ok = ok and all((root / str(path).replace("\\", "/").strip("/")).exists() for path in inspect_first[:5])
    return {
        "ok": ok,
        "target_file": target_file,
        "target_ref": target_ref,
        "inspect_first_count": len(inspect_first),
    }


def _scenario_merge_review_queue(work_queue: dict[str, Any], root: Path, raw_dir: Path) -> dict[str, Any]:
    del work_queue, raw_dir
    merge = _cached_mcp_json(
        "merge_review_queue:max_items=1:json",
        lambda: mcp_server.get_merge_review_queue(max_items=1, format="json"),
    )
    item = (merge.get("items") or [{}])[0] if isinstance(merge, dict) else {}
    source_file = str(item.get("source_file") or "").replace("\\", "/").strip("/")
    source_ref = str(item.get("source_ref") or "")
    validation = merge.get("validation") if isinstance(merge.get("validation"), dict) else {}
    validation_tools = [
        row
        for key in ("pre_approval_tools", "post_approval_tools")
        for row in (validation.get(key) if isinstance(validation.get(key), list) else [])
        if isinstance(row, dict) and row.get("tool")
    ]
    brief = _cached_mcp_text("merge_review_queue:max_items=1:brief", lambda: mcp_server.get_merge_review_queue(max_items=1))
    incomplete_evidence = (
        isinstance(merge, dict)
        and merge.get("status") == "INCOMPLETE_EVIDENCE"
        and merge.get("items") == []
        and int(merge.get("total_candidates") or 0) == 0
        and merge.get("policy_boundary", {}).get("mutation_allowed_by_this_packet") is False
        and merge.get("policy_boundary", {}).get("human_approval_required") is True
        and bool(str(merge.get("required_action") or "").strip())
        and (merge.get("target_proof") or {}).get("verdict") in {"BLOCKED", "UNKNOWN"}
        and (
            (merge.get("target_proof") or {}).get("verdict") != "BLOCKED"
            or bool((merge.get("target_proof") or {}).get("blocking_evidence"))
        )
    )
    empty_review_only_queue = (
        isinstance(merge, dict)
        and merge.get("items") == []
        and int(merge.get("total_candidates") or 0) == 0
        and merge.get("policy_boundary", {}).get("mutation_allowed_by_this_packet") is False
        and merge.get("policy_boundary", {}).get("human_approval_required") is True
        and "Review merge candidates without applying file copies or imports automatically." in brief
    )
    ok = incomplete_evidence or empty_review_only_queue or bool(
        source_file
        and (root / source_file).exists()
        and source_ref.endswith(source_file)
        and len(validation_tools) >= 4
        and merge.get("policy_boundary", {}).get("mutation_allowed_by_this_packet") is False
        and "inspect_file(target=source_ref)" in brief
        and "get_impact_radius(target_node=source_ref)" in brief
    )
    return {
        "ok": ok,
        "mode": (
            "incomplete_evidence" if incomplete_evidence
            else "empty_review_only_queue" if empty_review_only_queue
            else "candidate_review"
        ),
        "source_file": source_file,
        "source_ref": source_ref,
        "validation_tools": len(validation_tools),
    }


def _scenario_dead_code_summary(work_queue: dict[str, Any], root: Path, raw_dir: Path) -> dict[str, Any]:
    del work_queue
    dead = _cached_mcp_json(
        "dead_code:path=src:max_items=1:json",
        lambda: mcp_server.get_dead_code(path="src", max_items=1, format="json"),
    )
    brief = _cached_mcp_text("dead_code:path=src:max_items=1:brief", lambda: mcp_server.get_dead_code(path="src", max_items=1))
    if dead == []:
        expectations = {
            "brief_status": _brief_has_scalar(brief, "status", "no_actionable_items"),
            "brief_filter": _brief_has_scalar(brief, "filter", "src"),
            "no_action": _brief_has_scalar(brief, "actionability", "no_action"),
            "candidate_evidence_fields_non_applicable": all(
                _brief_has_scalar(brief, field, "not_applicable_without_candidate")
                for field in ("file_imported", "symbol_seen_globally", "local_symbol_usage")
            ),
            "bounded_absence_claim": "no_candidate_in_filtered_sage_artifact_not_repository_clean" in brief,
            "mutation_not_inferred": "Do not edit from this summary alone." in brief,
        }
        return {
            "ok": all(expectations.values()),
            "mode": "no_actionable_items",
            "source_file": "",
            "target_file": "",
            "target_ref": "",
            "target_status": {},
            "expectations": expectations,
        }
    item = dead[0] if isinstance(dead, list) and dead else {}
    project = str(item.get("project") or "")
    source_file = str(item.get("file") or item.get("path") or "").replace("\\", "/").strip("/")
    target = mcp_server._target_context_from_project_file(raw_dir, project, source_file)
    target_file = str(target.get("target_file") or "").replace("\\", "/").strip("/")
    target_status = target.get("target_status") if isinstance(target.get("target_status"), dict) else {}
    ok = bool(target_file and target_status.get("exists") and (root / target_file).exists())
    ok = ok and "Do not edit from this summary alone." in brief and "public export" in brief
    return {
        "ok": ok,
        "source_file": source_file,
        "target_file": target_file,
        "target_ref": target.get("target_ref"),
        "target_status": target_status,
    }


def _scenario_circular_dependency_chain(work_queue: dict[str, Any], root: Path, raw_dir: Path) -> dict[str, Any]:
    del work_queue
    circular = _cached_mcp_json(
        "circular_dependencies:MAIN:max_items=1:json",
        lambda: mcp_server.get_circular_dependencies(module="MAIN", max_items=1, format="json"),
    )
    item = circular[0] if isinstance(circular, list) and circular else {}
    chain = [str(node) for node in (item.get("chain") or []) if str(node or "").strip()]
    targets = mcp_server._scoped_key_targets(raw_dir, chain[:4])
    brief = _cached_mcp_text(
        "circular_dependencies:MAIN:max_items=1:brief",
        lambda: mcp_server.get_circular_dependencies(module="MAIN", max_items=1),
    )
    if circular == []:
        raw = load_json_file(raw_dir / "circular_deps.json", {})
        by_project = raw.get("by_project") if isinstance(raw, dict) and isinstance(raw.get("by_project"), dict) else {}
        main = by_project.get("MAIN") if isinstance(by_project.get("MAIN"), dict) else {}
        meta = raw.get("meta") if isinstance(raw, dict) and isinstance(raw.get("meta"), dict) else {}
        execution_scope = meta.get("execution_scope") if isinstance(meta.get("execution_scope"), dict) else {}
        analyzed_projects = {
            str(project) for project in execution_scope.get("analyzed_projects", []) if str(project).strip()
        }
        expectations = {
            "raw_artifact": isinstance(raw, dict) and bool(raw),
            "cycles_list": isinstance(raw.get("cycles"), list) if isinstance(raw, dict) else False,
            "artifact_kind": meta.get("kind") == "circular_dependencies",
            "main_analyzed": "MAIN" in analyzed_projects,
            "target_decision_eligible": execution_scope.get("target_decision_eligible") is True,
            "main_summary": bool(main),
            "main_cycle_count": int(main.get("cycle_count") or 0) == 0,
            "main_has_cycles": main.get("has_cycles") is False,
            "brief_status": _brief_has_scalar(brief, "status", "no_actionable_items"),
            "brief_filter": _brief_has_scalar(brief, "filter", "MAIN"),
            "brief_items": "items:" in brief,
        }
        return {
            "ok": all(expectations.values()),
            "mode": "clean_release_scope",
            "chain_count": 0,
            "openable_chain_targets": [],
            "expectations": expectations,
        }
    openable = [
        str(target.get("target_file") or "")
        for target in targets
        if target.get("exists") and (root / str(target.get("target_file") or "")).exists()
    ]
    ok = bool(chain and openable and "chain_targets" in brief and "Treat this as context" in brief)
    return {
        "ok": ok,
        "chain_count": len(chain),
        "openable_chain_targets": openable[:4],
    }


def _scenario_test_impact_summary(work_queue: dict[str, Any], root: Path, raw_dir: Path) -> dict[str, Any]:
    del work_queue, raw_dir
    tests = _cached_mcp_json(
        "test_impact:src/shared/utils/text.ts:json",
        lambda: mcp_server.get_test_impact("src/shared/utils/text.ts", format="json"),
    )
    target_file = str(tests.get("target_file") or "").replace("\\", "/").strip("/")
    impacted = tests.get("impacted_tests") if isinstance(tests.get("impacted_tests"), list) else []
    openable_tests = [
        str(row.get("repo_relative_path") or row.get("file") or "").replace("\\", "/").strip("/")
        for row in impacted
        if isinstance(row, dict)
        and (root / str(row.get("repo_relative_path") or row.get("file") or "").replace("\\", "/").strip("/")).exists()
        and str(row.get("repo_relative_path") or row.get("file") or "").replace("\\", "/").strip("/") in str(row.get("run_command") or "")
    ]
    return {
        "ok": bool(target_file and (root / target_file).exists() and openable_tests),
        "target_file": target_file,
        "openable_test_count": len(openable_tests),
    }


def _scenario_confidence_score_summary(work_queue: dict[str, Any], root: Path, raw_dir: Path) -> dict[str, Any]:
    del work_queue, raw_dir
    confidence = _cached_mcp_json(
        "confidence_score:src/platform/ai/ai/schemas.ts:json",
        lambda: mcp_server.get_confidence_score("src/platform/ai/ai/schemas.ts", format="json"),
    )
    brief = _cached_mcp_text(
        "confidence_score:src/platform/ai/ai/schemas.ts:brief",
        lambda: mcp_server.get_confidence_score("src/platform/ai/ai/schemas.ts"),
    )
    target_file = str(confidence.get("target_file") or "").replace("\\", "/").strip("/")
    target_ref = str(confidence.get("target_ref") or "")
    grounded = str(confidence.get("target_grounding_status") or "") == "grounded"
    ok = bool(
        target_file
        and (root / target_file).exists()
        and target_ref.endswith(target_file)
        and grounded
        and "Confidence Brief" in brief
        and "target_grounding_status" in brief
        and "risk estimate" in brief
    )
    return {
        "ok": ok,
        "target_file": target_file,
        "target_ref": target_ref,
        "target_grounding_status": confidence.get("target_grounding_status"),
    }


SEMANTIC_SCENARIO_PRODUCERS = {
    "work_queue_architecture_violation": _scenario_work_queue_architecture_violation,
    "merge_review_queue": _scenario_merge_review_queue,
    "dead_code_summary": _scenario_dead_code_summary,
    "circular_dependency_chain": _scenario_circular_dependency_chain,
    "test_impact_summary": _scenario_test_impact_summary,
    "confidence_score_summary": _scenario_confidence_score_summary,
}


def _representative_agent_task_family_report(work_queue: dict[str, Any]) -> dict[str, Any]:
    """Check that the main agent-facing task families resolve to openable repo facts."""
    issues: list[dict[str, Any]] = []
    families: dict[str, dict[str, Any]] = {}

    try:
        raw_dir = mcp_server._raw_dir_for_target("")
        root = Path(str(mcp_server._analysis_root_display())).resolve()
    except Exception as exc:
        return {"checked": 0, "families": {}, "issues": [{"family": "setup", "error": str(exc)}]}

    def _record(name: str, ok: bool, details: dict[str, Any]) -> None:
        families[name] = {"ok": bool(ok), **details}
        if not ok:
            issues.append({"family": name, **details})

    registry = _semantic_scenario_registry()
    for scenario_id in _semantic_scenarios_required_by_families():
        scenario = registry.get(scenario_id, {})
        producer_name = scenario.get("producer", "")
        role = scenario.get("role", "")
        producer = SEMANTIC_SCENARIO_PRODUCERS.get(producer_name)
        if producer is None:
            _record(
                scenario_id,
                False,
                {
                    "error": "missing_semantic_scenario_producer",
                    "producer": producer_name,
                    "role": role,
                },
            )
            continue
        try:
            details = producer(work_queue, root, raw_dir)
            ok = bool(details.pop("ok", False))
            _record(scenario_id, ok, {"producer": producer_name, "role": role, **details})
        except Exception as exc:
            _record(scenario_id, False, {"error": str(exc), "producer": producer_name, "role": role})

    return {"checked": len(families), "families": families, "issues": issues}


def _surgical_brief_grounding_report() -> dict[str, Any]:
    try:
        brief = _cached_mcp_text(
            "surgical_operation_packet:max_signals=3:brief",
            lambda: mcp_server.get_surgical_operation_packet(max_signals=3),
        )
    except Exception as exc:
        return {"checked": 0, "issues": [{"error": str(exc)}]}

    issues: list[dict[str, Any]] = []
    required_markers = [
        "analysis_root:",
        "target_files:",
        "path_contract:",
        "open_files_with:",
        "target_refs_usage:",
        "validation:",
        "tools:",
        "get_test_impact(target_file)",
        "validate_patch(target_file, patch_content)",
        "completion_rule:",
    ]
    missing = [marker for marker in required_markers if marker not in brief]
    if missing:
        issues.append({"issue": "surgical_brief_missing_agent_operational_markers", "missing": missing})
    forbidden_default_markers = [
        "debug_internal_refs:",
        "output/.raw",
        "architecture_profile:",
        "atlas_nodes:",
        "validate_doctrine_audit_quality_integrity.py",
        "validate_contextos_contracts.py",
    ]
    leaked = [marker for marker in forbidden_default_markers if marker in brief]
    if leaked:
        issues.append({"issue": "surgical_brief_leaks_internal_default_markers", "markers": leaked})
    return {"checked": 1, "issues": issues}


def _supporting_context_grounding_report() -> dict[str, Any]:
    seal_contract = load_agent_surface_seal_contract()
    semantic_contract = seal_contract.get("supporting_context_semantic_contract", {})
    semantic_contract = semantic_contract if isinstance(semantic_contract, dict) else {}
    calls = {
        "module_integrity": lambda: _cached_mcp_text(
            "module_integrity:src:max_items=3:brief",
            lambda: mcp_server.check_module_integrity(module_path="src", max_items=3),
        ),
        "dead_code": lambda: _cached_mcp_text(
            "dead_code:path=src:max_items=3:brief",
            lambda: mcp_server.get_dead_code(path="src", max_items=3),
        ),
        "clones": lambda: _cached_mcp_text(
            "clones:symbol=main:max_items=3:brief",
            lambda: mcp_server.find_clones(symbol="main", max_items=3),
        ),
        "health_metrics": lambda: _cached_mcp_text(
            "health_metrics:path=MAIN:brief",
            lambda: mcp_server.get_health_metrics(path="MAIN"),
        ),
        "circular_dependencies": lambda: _cached_mcp_text(
            "circular_dependencies:MAIN:max_items=3:brief",
            lambda: mcp_server.get_circular_dependencies(module="MAIN", max_items=3),
        ),
        "blast_radius": lambda: _cached_mcp_text(
            "blast_radius:symbol=main:max_items=3:brief",
            lambda: mcp_server.get_blast_radius(symbol="main", max_items=3),
        ),
        "state_flow": lambda: _cached_mcp_text(
            "state_flow:MAIN:max_items=3:brief",
            lambda: mcp_server.get_state_flow(project="MAIN", max_items=3),
        ),
        "hexagonal_bindings": lambda: _cached_mcp_text(
            "hexagonal_bindings:max_items=3:brief",
            lambda: mcp_server.get_hexagonal_bindings(max_items=3),
        ),
        "surgical_context": lambda: _cached_mcp_text(
            "surgical_context:symbol=Project:brief",
            lambda: mcp_server.get_surgical_context(symbol="Project"),
        ),
        "ui_architecture": lambda: _cached_mcp_text(
            "ui_architecture:component=main:max_items=3:brief",
            lambda: mcp_server.get_ui_architecture(component="main", max_items=3),
        ),
    }
    issues: list[dict[str, Any]] = []
    checked = 0
    required_markers = [str(value) for value in semantic_contract.get("required_markers", [])]
    forbidden_markers = [str(value) for value in semantic_contract.get("forbidden_markers", [])]
    expected_surfaces = {str(value) for value in semantic_contract.get("surfaces", [])}
    surface_markers = semantic_contract.get("surface_specific_required_markers", {})
    surface_markers = surface_markers if isinstance(surface_markers, dict) else {}
    max_chars = int(semantic_contract.get("max_chars") or 0)
    if set(calls) != expected_surfaces:
        issues.append({"issue": "supporting_context_surface_contract_drift", "expected": sorted(expected_surfaces), "actual": sorted(calls)})
    for name, call in calls.items():
        try:
            brief = call()
        except Exception as exc:
            issues.append({"surface": name, "issue": "call_failed", "error": str(exc)})
            continue
        checked += 1
        missing = [marker for marker in required_markers if marker not in brief]
        leaked = [marker for marker in forbidden_markers if marker in brief]
        if missing:
            issues.append({"surface": name, "issue": "brief_missing_supporting_context_contract", "missing": missing})
        if leaked:
            issues.append({"surface": name, "issue": "brief_leaks_operator_or_internal_markers", "markers": leaked})
        if max_chars <= 0 or len(brief) > max_chars:
            issues.append({"surface": name, "issue": "brief_too_large_for_supporting_context", "chars": len(brief)})
        missing_surface_markers = [str(marker) for marker in surface_markers.get(name, []) if str(marker) not in brief]
        if missing_surface_markers:
            issues.append({"surface": name, "issue": "surface_specific_markers_missing", "missing": missing_surface_markers})
        if name == "health_metrics":
            unknown_health = _cached_mcp_text(
                "health_metrics:path=NO_SUCH_MODULE:brief",
                lambda: mcp_server.get_health_metrics(path="NO_SUCH_MODULE"),
            )
            if (
                "resolution_status\": \"overall_fallback\"" not in unknown_health
                or "Requested path was not found; health is the overall fallback, not module-specific." not in unknown_health
            ):
                issues.append({"surface": name, "issue": "health_metrics_unknown_path_must_disclose_overall_fallback"})
        if name == "blast_radius" and (
            "get_impact_radius(target_node=target_ref, depth=2)" not in brief
            or "Do not treat direct/transitive dependent counts as a substitute for listed file paths." not in brief
        ):
            issues.append({"surface": name, "issue": "blast_radius_summary_must_route_to_impact_radius_for_file_paths"})
        if name == "blast_radius":
            empty_blast = _cached_mcp_text(
                "blast_radius:symbol=NO_SUCH_SYMBOL:max_items=3:brief",
                lambda: mcp_server.get_blast_radius(symbol="NO_SUCH_SYMBOL", max_items=3),
            )
            if (
                'status: "no_actionable_items"' not in empty_blast
                or "No concrete blast-radius target was returned for this filter." not in empty_blast
                or "Do not infer downstream impact from an empty summary result." not in empty_blast
            ):
                issues.append({"surface": name, "issue": "blast_radius_empty_result_must_be_no_actionable"})
        if name == "hexagonal_bindings" and (
            "port_target" not in brief
            or "adapter_targets" not in brief
            or "target_file" not in brief
            or "\"port_file\": \"MAIN::" in brief
        ):
            issues.append({"surface": name, "issue": "hexagonal_bindings_must_expose_openable_targets"})
        if name == "module_integrity" and (
            not ('status: "healthy"' in brief and "returned: 0" in brief and "items:\n  []" in brief)
            and (
                "target_file:" not in brief
                or "target_ref:" not in brief
                or "target_status:" not in brief
                or "evidence:" not in brief
                or "inspect_first: [\"EXAMPLE_VARIANT_" in brief
            )
        ):
            issues.append({"surface": name, "issue": "module_integrity_must_expose_openable_targets"})
        if name == "surgical_context" and (
            "target_file" not in brief
            or "target_ref" not in brief
            or "target_status" not in brief
            or "\"file\": \"src/" in brief
        ):
            issues.append({"surface": name, "issue": "surgical_context_must_expose_openable_target"})
        if name == "clones" and (
            "instance_targets" not in brief
            or "target_file" not in brief
            or "target_ref" not in brief
            or "Compare the listed instance_targets before proposing deduplication or extraction." not in brief
            or "public type contracts" not in brief
            or "\"instances\": [\"" in brief
        ):
            issues.append({"surface": name, "issue": "clone_context_must_expose_openable_instance_targets"})
    return {"checked": checked, "issues": issues}


def validate_agent_semantic_contract_smoke() -> dict[str, Any]:
    telemetry: list[dict[str, Any]] = []
    engine_contract_validation = _timed_step(
        "load_engine_signal_contract_validation",
        lambda: load_json_file(RAW_DIR / "engine_signal_contract_validation.json", {}),
        telemetry,
    )
    validation_contracts = _contract_map(engine_contract_validation if isinstance(engine_contract_validation, dict) else {})
    config_contracts = _timed_step("load_config_contracts", _config_contract_map, telemetry)
    confidence_vocabulary = _timed_step("load_confidence_vocabulary", _vocabulary_confidence, telemetry)
    operator_packet = _timed_step("build_operator_packet", build_operator_packet, telemetry)
    signals = _timed_step("load_signals", lambda: load_json_file(RAW_DIR / "signals.json", {}), telemetry)
    circular = _timed_step("load_circular_deps", lambda: load_json_file(RAW_DIR / "circular_deps.json", {}), telemetry)
    surgical_packet = _timed_step(
        "build_surgical_operation_packet",
        lambda: build_surgical_operation_packet(
            signals if isinstance(signals, dict) else {},
            circular_deps_data=circular if isinstance(circular, dict) else {},
            max_signals=2,
        ),
        telemetry,
    )
    react_runtime_summary = _timed_step("load_react_runtime_summary", lambda: _summary("react_runtime_intelligence.json"), telemetry)
    quality_gate_summary = _timed_step("load_quality_gate", lambda: load_json_file(RAW_DIR / "quality_gate.json", {}), telemetry)
    release_readiness = _timed_step("load_release_readiness", lambda: load_json_file(RAW_DIR / "release_readiness.json", {}), telemetry)
    claim_guard = _timed_step("load_claim_guard", lambda: load_json_file(RAW_DIR / "claim_guard_validation.json", {}), telemetry)
    release_bundle_summary = _timed_step("load_release_bundle_summary", lambda: _summary("release_proof_bundle.json"), telemetry)
    oracle_validation = _timed_step("load_oracle_validation", lambda: load_json_file(RAW_DIR / "architecture_oracle_validation.json", {}), telemetry)
    cage_contract = config_contracts.get("performance_n_plus_one_cage", {})
    work_queue_projection = _timed_step("build_work_queue_projection", _work_queue_projection, telemetry)
    approval_consistency = _timed_step("approval_consistency_report", lambda: _approval_consistency_report(operator_packet, work_queue_projection), telemetry)
    merge_grounding = _timed_step("merge_queue_grounding_report", _merge_queue_grounding_report, telemetry)
    no_op_patch = _timed_step("no_op_patch_report", _no_op_patch_report, telemetry)
    patch_grounding = _timed_step("patch_grounding_report", _patch_grounding_report, telemetry)
    patch_target_root_regression = _timed_step(
        "patch_target_root_regression_report",
        _patch_target_root_regression_report,
        telemetry,
    )
    confidence_grounding = _timed_step("confidence_grounding_report", _confidence_grounding_report, telemetry)
    search_inspection_grounding = _timed_step("search_and_inspection_grounding_report", _search_and_inspection_grounding_report, telemetry)
    test_impact_grounding = _timed_step("test_impact_grounding_report", _test_impact_grounding_report, telemetry)
    upstream_trace_grounding = _timed_step(
        "upstream_trace_grounding_report",
        lambda: _upstream_trace_grounding_report(circular if isinstance(circular, dict) else {}),
        telemetry,
    )
    work_queue_grounding = _timed_step("work_queue_grounding_report", lambda: _work_queue_grounding_report(work_queue_projection), telemetry)
    agent_task_scenario = _timed_step("agent_task_scenario_report", lambda: _agent_task_scenario_report(work_queue_projection), telemetry)
    representative_task_families = _timed_step(
        "representative_agent_task_family_report",
        lambda: _representative_agent_task_family_report(work_queue_projection),
        telemetry,
    )
    surgical_brief_grounding = _timed_step("surgical_brief_grounding_report", _surgical_brief_grounding_report, telemetry)
    supporting_context_grounding = _timed_step("supporting_context_grounding_report", _supporting_context_grounding_report, telemetry)

    runtime_confidence = set((react_runtime_summary.get("confidence_counts") or {}).keys())
    unknown_runtime_confidence = sorted(runtime_confidence - confidence_vocabulary)
    react_contract = config_contracts.get("react_surgical_intelligence", {})
    contextos_contract = config_contracts.get("contextos", {})
    architecture_contract = config_contracts.get("architecture_governance", {})
    release_contract = config_contracts.get("release_proof", {})
    release_identity = load_json_file(ROOT / "config" / "release_identity.json", {})
    allowed_release_claim = str(
        ((release_identity.get("release_claim") or {}).get("allowed") or "")
        if isinstance(release_identity, dict)
        else ""
    )

    approval_gates = operator_packet.get("active_human_approval_gates", [])
    architecture_gate = [
        gate for gate in approval_gates if isinstance(gate, dict) and gate.get("id") == "architecture_doctrine_seal"
    ]
    oracle_summary = {}
    for check in oracle_validation.get("checks", []) if isinstance(oracle_validation, dict) else []:
        if isinstance(check, dict) and check.get("name") == "architecture_oracle_does_not_enforce_hard_gate":
            oracle_summary = check.get("details", {}).get("summary", {})
            break

    checks = [
        _check(
            "operator_packet_exposes_engine_signal_contract",
            operator_packet.get("mission_control", {}).get("engine_signal_contract") == "PASS"
            and operator_packet.get("engine_signal_contract", {}).get("mcp_tool") == "get_engine_signal_contracts",
            operator_packet.get("engine_signal_contract", {}).get("summary"),
        ),
        _check(
            "surgical_packet_marks_contextos_as_focus_not_verdict",
            surgical_packet.get("engine_signal_contract", {}).get("mcp_tool") == "get_engine_signal_contracts"
            and "not release proof" in str(surgical_packet.get("engine_signal_contract", {}).get("agent_rule", "")).lower()
            and contextos_contract.get("emits") == "context_packet"
            and contextos_contract.get("quality_gate_effect") == "none",
            {
                "packet_rule": surgical_packet.get("engine_signal_contract", {}).get("agent_rule"),
                "contract": contextos_contract,
            },
        ),
        _check(
            "react_runtime_confidence_terms_are_in_shared_vocabulary",
            not unknown_runtime_confidence and runtime_confidence.issubset(set(react_contract.get("allowed_confidence", []))),
            {
                "runtime_confidence": sorted(runtime_confidence),
                "unknown": unknown_runtime_confidence,
                "react_allowed": react_contract.get("allowed_confidence", []),
            },
        ),
        _check(
            "react_surgical_findings_are_evidence_not_autonomous_blockers",
            react_contract.get("emits") == "evidence"
            and react_contract.get("decision_authority") == "evidence_calibrated"
            and react_contract.get("quality_gate_effect") == "warn",
            react_contract,
        ),
        _check(
            "architecture_oracle_requires_human_seal_despite_governance_verdict_class",
            architecture_contract.get("emits") == "verdict"
            and architecture_contract.get("decision_authority") == "governance_decides"
            and oracle_summary.get("hard_gate_enforced") is False
            and bool(architecture_gate),
            {"contract": architecture_contract, "oracle_summary": oracle_summary, "approval_gate": architecture_gate[:1]},
        ),
        _check(
            "cage_candidates_do_not_enter_v1_release_claims",
            cage_contract.get("emits") == "signal"
            and cage_contract.get("decision_authority") == "engine_signal_only"
            and cage_contract.get("quality_gate_effect") == "advisory"
            and "not_v1_scope" in (cage_contract.get("allowed_confidence") or []),
            cage_contract,
        ),
        _check(
            "release_proof_is_the_release_claim_verdict",
            isinstance(release_readiness, dict)
            and release_readiness.get("readiness") == "PRODUCTION_READY"
            and isinstance(claim_guard, dict)
            and (claim_guard.get("summary") or {}).get("status") == "PASS"
            and release_contract.get("emits") == "verdict"
            and release_contract.get("quality_gate_effect") == "block"
            and (claim_guard.get("summary") or {}).get("allowed_claim")
            == allowed_release_claim
            and bool(allowed_release_claim),
            {
                "release_readiness": {
                    "readiness": release_readiness.get("readiness") if isinstance(release_readiness, dict) else None,
                    "summary": release_readiness.get("summary") if isinstance(release_readiness, dict) else None,
                },
                "claim_guard": claim_guard.get("summary") if isinstance(claim_guard, dict) else {},
                "current_release_verdict": {
                    "readiness": release_readiness.get("readiness") if isinstance(release_readiness, dict) else None,
                    "claim_guard_status": (claim_guard.get("summary") or {}).get("status") if isinstance(claim_guard, dict) else None,
                },
                "historical_bundle_snapshot": {
                    "summary": release_bundle_summary,
                    "freshness": "historical_not_used_for_current_semantic_verdict",
                },
                "release_identity_allowed_claim": allowed_release_claim,
                "contract": release_contract,
            },
        ),
        _check(
            "engine_signal_validation_contracts_are_visible",
            engine_contract_validation.get("summary", {}).get("status") == "PASS"
            and "react_surgical_intelligence" in validation_contracts
            and "contextos" in validation_contracts
            and "release_proof" in validation_contracts,
            engine_contract_validation.get("summary", {}),
        ),
        _check(
            "agent_surfaces_use_consistent_human_approval_decisions",
            approval_consistency.get("checked", 0) > 0
            and not approval_consistency.get("mismatches")
            and not approval_consistency.get("projection_issues")
            and not approval_consistency.get("brief_issues"),
            approval_consistency,
        ),
        _check(
            "merge_review_queue_is_source_grounded_and_review_only",
            merge_grounding.get("checked", 0) > 0 and not merge_grounding.get("issues"),
            merge_grounding,
        ),
        _check(
            "validate_patch_empty_patch_is_noop_not_apply_permission",
            no_op_patch.get("ok") is True,
            no_op_patch,
        ),
        _check(
            "validate_patch_blocks_missing_targets_and_bad_imports",
            patch_grounding.get("ok") is True,
            patch_grounding,
        ),
        _check(
            "validate_patch_uses_target_repo_root_and_preserves_existing_debt_context",
            patch_target_root_regression.get("ok") is True,
            patch_target_root_regression,
        ),
        _check(
            "confidence_score_is_source_grounded_and_not_merge_approval",
            confidence_grounding.get("ok") is True,
            confidence_grounding,
        ),
        _check(
            "search_and_inspection_surfaces_prioritize_openable_exact_targets",
            search_inspection_grounding.get("ok") is True,
            search_inspection_grounding,
        ),
        _check(
            "test_impact_commands_are_source_grounded",
            test_impact_grounding.get("checked", 0) > 0 and not test_impact_grounding.get("issues"),
            test_impact_grounding,
        ),
        _check(
            "upstream_trace_brief_exposes_source_grounded_context",
            upstream_trace_grounding.get("checked", 0) > 0 and not upstream_trace_grounding.get("issues"),
            upstream_trace_grounding,
        ),
        _check(
            "violation_work_queue_is_source_grounded_and_agent_operational",
            (
                work_queue_grounding.get("checked", 0) > 0
                or work_queue_grounding.get("mode") == "clean_within_sage_audit"
            )
            and not work_queue_grounding.get("issues"),
            work_queue_grounding,
        ),
        _check(
            "agent_task_scenario_preserves_target_across_mcp_chain",
            (
                agent_task_scenario.get("checked", 0) == 6
                or agent_task_scenario.get("mode") == "clean_within_sage_audit"
            )
            and not agent_task_scenario.get("issues"),
            agent_task_scenario,
        ),
        _check(
            "representative_agent_task_families_are_source_grounded",
            representative_task_families.get("checked", 0) >= _expected_representative_family_count()
            and not representative_task_families.get("issues"),
            representative_task_families,
        ),
        _check(
            "surgical_brief_is_agent_operational_without_internal_noise",
            surgical_brief_grounding.get("checked", 0) > 0 and not surgical_brief_grounding.get("issues"),
            surgical_brief_grounding,
        ),
        _check(
            "supporting_context_briefs_are_target_agent_bounded",
            supporting_context_grounding.get("checked", 0)
            == len(load_agent_surface_seal_contract().get("supporting_context_semantic_contract", {}).get("surfaces", []))
            and not supporting_context_grounding.get("issues"),
            supporting_context_grounding,
        ),
    ]
    status = "PASS" if all(check["passed"] for check in checks) else "FAIL"
    payload = {
        "meta": {
            "kind": "agent_semantic_contract_smoke",
            "version": "v1",
            "generated_at": _utc_now(),
            "generator": "tools.validate_agent_semantic_contract_smoke",
        },
        "summary": {
            "status": status,
            "checks": len(checks),
            "passed": sum(1 for check in checks if check["passed"]),
        },
        "telemetry": {
            "steps": telemetry,
            "slowest_steps": sorted(telemetry, key=lambda row: float(row.get("duration_ms") or 0), reverse=True)[:5],
        },
        "checks": checks,
        "sampled_artifacts": [
            "output/.raw/nexora_operator_packet.json",
            "output/.raw/signals.json",
            "output/.raw/react_runtime_intelligence.json",
            "output/.raw/architecture_oracle_validation.json",
            "output/.raw/release_proof_bundle.json",
        ],
    }
    save_json_atomic(RAW_DIR / "agent_semantic_contract_smoke.json", payload)
    save_text_atomic(REPORTS_DIR / "agent_semantic_contract_smoke.md", render_report(payload))
    return payload


def render_report(payload: dict[str, Any]) -> str:
    slowest_steps = ((payload.get("telemetry") or {}).get("slowest_steps") or []) if isinstance(payload, dict) else []
    lines = [
        "# Agent Semantic Contract Smoke",
        "",
        "Checks whether real agent-facing packets and findings align with the engine signal/evidence contract.",
        "",
        f"- status: `{payload.get('summary', {}).get('status')}`",
        f"- passed: `{payload.get('summary', {}).get('passed')}/{payload.get('summary', {}).get('checks')}`",
        "",
        "## Telemetry",
        "",
        "| Step | Duration ms | Status | Reason | Shape |",
        "|---|---:|---|---|---|",
    ]
    for row in slowest_steps:
        lines.append(
            f"| `{row.get('name')}` | `{row.get('duration_ms')}` | `{row.get('status')}` | "
            f"`{row.get('reason')}` | `{row.get('result_shape')}` |"
        )
    lines.extend([
        "",
        "| Check | Passed |",
        "|---|---|",
    ])
    for check in payload.get("checks", []):
        lines.append(f"| `{check.get('name')}` | `{check.get('passed')}` |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    payload = validate_agent_semantic_contract_smoke()
    print(json.dumps(payload.get("summary", {}), ensure_ascii=False))
    return 0 if payload.get("summary", {}).get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
