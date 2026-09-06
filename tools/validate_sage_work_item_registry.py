from __future__ import annotations

import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.config import CONFIG_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.agent_surface_seal_contract import load_agent_surface_seal_contract
from tools.core.distribution_policy import is_clean_install_root
from tools.core.execution_waves import release_scope_matches_wave
from tools.core.json_io import load_json_object_strict
from tools.core.roadmap_phase_registry import current_product_release, roadmap_phase_rows
from tools.generate_sage_work_item_report import run as generate_work_item_report
from tools.core.work_package_receipts import record_work_package_operation_safely


REGISTRY_PATH = CONFIG_DIR / "sage_work_item_registry.json"
WAVE_REGISTRY_PATH = CONFIG_DIR / "sage_execution_wave_registry.json"
CAPABILITY_REGISTRY_PATH = CONFIG_DIR / "capability_registry.json"
ROADMAP_REGISTRY_PATH = CONFIG_DIR / "roadmap_phase_registry.json"
VALIDATION_RAW = RAW_DIR / "sage_work_item_registry_validation.json"
VALIDATION_REPORT = REPORTS_DIR / "sage_work_item_registry_validation.md"


def _check(name: str, passed: bool, details: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# SAGE Work Item Registry Validation",
        "",
        f"- status: `{summary.get('status')}`",
        f"- checks: `{summary.get('passed_checks')}/{summary.get('total_checks')}`",
        f"- open_work_items: `{summary.get('open_work_items')}`",
        f"- blocking_work_items: `{summary.get('blocking_work_items')}`",
        f"- current_execution_wave: `{summary.get('current_execution_wave')}`",
        f"- next_open_delivery_wave: `{summary.get('next_open_delivery_wave')}`",
        "",
        "| Check | Result | Details |",
        "|---|---|---|",
    ]
    for check in payload.get("checks", []):
        details = json.dumps(check.get("details"), ensure_ascii=False, sort_keys=True)[:900]
        lines.append(f"| `{check.get('name')}` | `{'PASS' if check.get('passed') else 'FAIL'}` | `{details}` |")
    return "\n".join(lines) + "\n"


def _policy_list(policy: dict[str, Any], key: str) -> set[str]:
    raw = policy.get(key)
    return {str(item) for item in raw if str(item).strip()} if isinstance(raw, list) else set()


def duplicate_identity_values(values: list[str]) -> list[str]:
    return sorted(value for value, count in Counter(values).items() if count > 1)


def wave_projection_identity(report: dict[str, Any], first_open_wave: str) -> dict[str, Any]:
    """Keep active execution and next delivery identities explicit and non-conflicting."""
    execution_plan = report.get("execution_plan") if isinstance(report.get("execution_plan"), dict) else {}
    current_wave = str(execution_plan.get("current_wave") or "")
    projected_next = str(execution_plan.get("next_open_delivery_wave") or "")
    return {
        "current_execution_wave": current_wave,
        "next_open_delivery_wave": projected_next,
        "selection_basis": str(execution_plan.get("selection_basis") or ""),
        "projection_matches_independent_first_open": projected_next == first_open_wave,
    }


def capability_delivery_issues(
    wave: dict[str, Any],
    *,
    known_capability_ids: set[str],
    known_work_item_ids: set[str],
) -> list[dict[str, Any]]:
    """Validate that wave capability intent is backed by owned delivery work."""
    wave_id = str(wave.get("id") or "")
    declared_capabilities = [str(item) for item in wave.get("capability_ids", [])]
    capability_delivery = (
        wave.get("capability_delivery_work_items")
        if isinstance(wave.get("capability_delivery_work_items"), dict)
        else {}
    )
    assigned_work_items = {str(item) for item in wave.get("work_item_ids", [])}
    issues: list[dict[str, Any]] = []

    for capability_id in declared_capabilities:
        if capability_id not in known_capability_ids:
            continue
        delivery_items = capability_delivery.get(capability_id)
        if not isinstance(delivery_items, list) or not delivery_items:
            issues.append(
                {"wave": wave_id, "capability_id": capability_id, "issue": "missing_delivery_work_items"}
            )
            continue
        unknown_delivery_items = sorted(
            str(item)
            for item in delivery_items
            if str(item) not in assigned_work_items or str(item) not in known_work_item_ids
        )
        if unknown_delivery_items:
            issues.append(
                {
                    "wave": wave_id,
                    "capability_id": capability_id,
                    "issue": "delivery_items_not_owned_by_wave",
                    "work_item_ids": unknown_delivery_items,
                }
            )

    undeclared_delivery_capabilities = sorted(set(capability_delivery) - set(declared_capabilities))
    if undeclared_delivery_capabilities:
        issues.append(
            {
                "wave": wave_id,
                "issue": "delivery_mapping_references_undeclared_capability",
                "capability_ids": undeclared_delivery_capabilities,
            }
        )
    return issues


def closure_evidence_is_available(*, root: Path, evidence: str) -> bool:
    """Require source evidence locally, but never require runtime output in a clean mirror."""
    if is_clean_install_root(root):
        return True
    return not evidence.startswith(("output/", "config/", "docs/")) or (root / evidence).exists()


def delivery_release_issues(
    work_items: list[dict[str, Any]],
    *,
    roadmap_releases: set[str],
) -> list[dict[str, str]]:
    """Keep planned release intent separate from actual shipped scope."""
    issues: list[dict[str, str]] = []
    for row in work_items:
        status = str(row.get("status") or "")
        delivered_release = str(row.get("delivered_release") or "")
        if status == "closed" and not delivered_release:
            issues.append(
                {"id": str(row.get("id") or ""), "status": status, "issue": "closed_without_delivered_release"}
            )
        elif status != "closed" and delivered_release:
            issues.append(
                {"id": str(row.get("id") or ""), "status": status, "issue": "open_with_delivered_release"}
            )
        elif delivered_release and delivered_release not in roadmap_releases:
            issues.append(
                {"id": str(row.get("id") or ""), "status": status, "issue": "unknown_delivered_release"}
            )
    return issues


def run_validation() -> dict[str, Any]:
    registry = load_json_object_strict(REGISTRY_PATH, label="SAGE work item registry")
    wave_registry = load_json_object_strict(WAVE_REGISTRY_PATH, label="SAGE execution wave registry")
    capability_registry = load_json_object_strict(CAPABILITY_REGISTRY_PATH, label="Capability registry")
    roadmap_registry = load_json_object_strict(ROADMAP_REGISTRY_PATH, label="Roadmap phase registry")
    priority_policy = registry.get("priority_policy") if isinstance(registry.get("priority_policy"), dict) else {}
    report = generate_work_item_report()
    agent_surface_contract = load_agent_surface_seal_contract()
    legacy_followups = (
        agent_surface_contract.get("open_followups")
        if isinstance(agent_surface_contract, dict)
        else []
    )
    registry_path = Path(report.get("registry") or "")
    open_items = report.get("open_items") if isinstance(report.get("open_items"), list) else []
    all_items = registry.get("work_items") if isinstance(registry.get("work_items"), list) else []
    all_item_id_list = [
        str(row.get("id") or "")
        for row in all_items
        if isinstance(row, dict) and row.get("id")
    ]
    all_item_ids = set(all_item_id_list)
    duplicate_all_ids = duplicate_identity_values(all_item_id_list)
    work_items = {
        str(row.get("id") or ""): row
        for row in all_items
        if isinstance(row, dict) and row.get("id")
    }
    item_status_by_id = {
        str(row.get("id") or ""): str(row.get("status") or "")
        for row in all_items
        if isinstance(row, dict) and row.get("id")
    }
    roadmap_releases = {
        str(row.get("release") or "")
        for row in roadmap_phase_rows(roadmap_registry)
        if row.get("release")
    }
    active_release = current_product_release(roadmap_registry)
    unknown_target_releases = sorted(
        {
            str(row.get("target_release") or "")
            for row in all_items
            if isinstance(row, dict)
            and row.get("target_release")
            and str(row.get("target_release")) not in roadmap_releases
        }
    )
    closed_items_with_open_language = [
        str(row.get("id") or "")
        for row in all_items
        if isinstance(row, dict)
        and row.get("status") == "closed"
        and not str(row.get("next_action") or "").lower().startswith("closed")
    ]
    work_item_ids = [str(row.get("id") or "") for row in open_items if isinstance(row, dict)]
    duplicate_open_ids = duplicate_identity_values(work_item_ids)
    allowed_priorities = _policy_list(priority_policy, "blocking_priorities") | _policy_list(priority_policy, "deferred_priorities")
    allowed_statuses = _policy_list(priority_policy, "allowed_statuses")
    allowed_delivery_timings = _policy_list(priority_policy, "allowed_delivery_timings")
    allowed_sources = _policy_list(priority_policy, "allowed_sources")
    required_fields = {
        "id",
        "priority",
        "status",
        "source",
        "family",
        "owner_layer",
        "evidence",
        "problem",
        "next_action",
        "release_blocking",
        "target_release",
    }
    incomplete_items = []
    invalid_priority_items = []
    invalid_status_items = []
    invalid_source_items = []
    invalid_blocking_items = []
    invalid_delivery_timing_items = []
    invalid_delivery_release_items = delivery_release_issues(
        [row for row in all_items if isinstance(row, dict)],
        roadmap_releases=roadmap_releases,
    )
    for row in open_items:
        if not isinstance(row, dict):
            continue
        missing = sorted(field for field in required_fields if field not in row or row.get(field) in {"", None})
        if missing:
            incomplete_items.append({"id": row.get("id"), "missing": missing})
        if str(row.get("priority") or "") not in allowed_priorities:
            invalid_priority_items.append({"id": row.get("id"), "priority": row.get("priority")})
        if str(row.get("status") or "") not in allowed_statuses:
            invalid_status_items.append({"id": row.get("id"), "status": row.get("status")})
        if str(row.get("source") or "") not in allowed_sources:
            invalid_source_items.append({"id": row.get("id"), "source": row.get("source")})
        if str(row.get("priority") or "") in _policy_list(priority_policy, "blocking_priorities") and row.get("release_blocking") is not True:
            invalid_blocking_items.append({"id": row.get("id"), "priority": row.get("priority"), "release_blocking": row.get("release_blocking")})

    for row in all_items:
        if not isinstance(row, dict):
            continue
        if not row.get("delivery_timing"):
            continue
        timing = str(row.get("delivery_timing") or "")
        delivered_at = str(row.get("delivered_at") or "")
        if (
            timing not in allowed_delivery_timings
            or str(row.get("status") or "") != "closed"
            or (timing in {"pulled_forward", "emergency"} and not delivered_at)
        ):
            invalid_delivery_timing_items.append(
                {"id": row.get("id"), "delivery_timing": timing, "delivered_at": delivered_at, "status": row.get("status")}
            )

    wave_policy = wave_registry.get("policy") if isinstance(wave_registry.get("policy"), dict) else {}
    wave_release_scope_policy = wave_policy.get("release_scope_policy") if isinstance(wave_policy.get("release_scope_policy"), dict) else {}
    waves = wave_registry.get("waves") if isinstance(wave_registry.get("waves"), list) else []
    declared_wave_order = [str(item) for item in wave_policy.get("ordered_wave_ids", [])]
    actual_wave_order = [str(row.get("id") or "") for row in waves if isinstance(row, dict)]
    wave_assignments: dict[str, list[str]] = {}
    unknown_wave_work_items: list[dict[str, str]] = []
    unknown_wave_capabilities: list[dict[str, str]] = []
    invalid_wave_capability_delivery: list[dict[str, Any]] = []
    capability_ids = {
        str(row.get("id") or "")
        for row in capability_registry.get("capabilities", [])
        if isinstance(row, dict) and row.get("id")
    }
    for wave in waves:
        if not isinstance(wave, dict):
            continue
        wave_id = str(wave.get("id") or "")
        for item_id in wave.get("work_item_ids", []):
            item_id = str(item_id)
            wave_assignments.setdefault(item_id, []).append(wave_id)
            if item_id not in all_item_ids:
                unknown_wave_work_items.append({"wave": wave_id, "work_item_id": item_id})
        for capability_id in wave.get("capability_ids", []):
            capability_id = str(capability_id)
            if capability_id not in capability_ids:
                unknown_wave_capabilities.append({"wave": wave_id, "capability_id": capability_id})
        invalid_wave_capability_delivery.extend(
            capability_delivery_issues(
                wave,
                known_capability_ids=capability_ids,
                known_work_item_ids=all_item_ids,
            )
        )
    unassigned_open_items = sorted(set(work_item_ids) - set(wave_assignments))
    multiply_assigned_open_items = {
        item_id: owners
        for item_id, owners in sorted(wave_assignments.items())
        if item_id in set(work_item_ids) and len(owners) != 1
    }
    first_open_wave = ""
    wave_dependency_issues: list[dict[str, Any]] = []
    completed_waves_without_evidence: list[str] = []
    completed_waves_missing_local_evidence: list[dict[str, str]] = []
    invalid_wave_release_scopes: list[dict[str, Any]] = []
    evidence_check_scope = "clean_mirror_reference_only" if is_clean_install_root(ROOT) else "source_local_artifact_presence"
    current_output_reference = VALIDATION_RAW.relative_to(ROOT).as_posix()
    self_output_referenced = False
    wave_positions = {wave_id: index for index, wave_id in enumerate(actual_wave_order)}
    for wave in waves:
        if not isinstance(wave, dict):
            continue
        wave_id = str(wave.get("id") or "")
        release_scope_ok, release_scope_details = release_scope_matches_wave(
            wave,
            work_items,
            wave_release_scope_policy,
            roadmap_releases,
            active_release,
        )
        if not release_scope_ok:
            invalid_wave_release_scopes.append(release_scope_details)
        assigned = [str(item) for item in wave.get("work_item_ids", [])]
        open_assigned = [item for item in assigned if item_status_by_id.get(item) != "closed"]
        dependencies = [str(item) for item in wave.get("blocked_by", [])]
        for dependency in dependencies:
            if dependency not in wave_positions or wave_positions[dependency] >= wave_positions.get(wave_id, -1):
                wave_dependency_issues.append({"wave": wave_id, "invalid_dependency": dependency})
        if open_assigned and not first_open_wave:
            first_open_wave = wave_id
        if assigned and not open_assigned and not wave.get("closure_evidence"):
            completed_waves_without_evidence.append(wave_id)
        if assigned and not open_assigned:
            for evidence in wave.get("closure_evidence", []):
                evidence_text = str(evidence)
                if evidence_text == current_output_reference:
                    # This invocation produces that reference; previous bytes cannot prove it.
                    self_output_referenced = True
                    continue
                if not closure_evidence_is_available(root=ROOT, evidence=evidence_text):
                    completed_waves_missing_local_evidence.append({"wave": wave_id, "evidence": evidence_text})

    wave_identity = wave_projection_identity(report, first_open_wave)
    checks = [
        _check(
            "registry_report_was_generated",
            registry_path.as_posix() == "config/sage_work_item_registry.json" and REGISTRY_PATH.exists(),
            {"registry": report.get("registry"), "exists": REGISTRY_PATH.exists()},
        ),
        _check(
            "agent_surface_followups_are_tracked",
            int(report.get("summary", {}).get("untracked_agent_surface_followups") or 0) == 0,
            {"untracked_agent_surface_followups": report.get("untracked_agent_surface_followups")},
        ),
        _check(
            "agent_surface_followups_are_centrally_owned",
            not (isinstance(legacy_followups, list) and legacy_followups),
            {"legacy_source": "config/agent_surface_seal_contract.json:open_followups", "legacy_followups": legacy_followups},
        ),
        _check(
            "open_work_items_have_required_fields",
            not incomplete_items,
            {"incomplete_items": incomplete_items},
        ),
        _check(
            "open_work_item_ids_are_unique",
            not duplicate_open_ids,
            {"duplicate_open_ids": duplicate_open_ids},
        ),
        _check(
            "all_work_item_ids_are_unique",
            not duplicate_all_ids,
            {"duplicate_all_ids": duplicate_all_ids},
        ),
        _check(
            "open_work_items_use_allowed_priority_and_status",
            bool(allowed_priorities)
            and bool(allowed_statuses)
            and bool(allowed_sources)
            and not invalid_priority_items
            and not invalid_status_items
            and not invalid_source_items,
            {
                "invalid_priority_items": invalid_priority_items,
                "invalid_status_items": invalid_status_items,
                "invalid_source_items": invalid_source_items,
            },
        ),
        _check(
            "delivery_timing_exceptions_are_explicit_and_closed",
            bool(allowed_delivery_timings) and not invalid_delivery_timing_items,
            {"invalid_delivery_timing_items": invalid_delivery_timing_items},
        ),
        _check(
            "delivery_release_is_explicit_and_lifecycle_bound",
            not invalid_delivery_release_items,
            {"invalid_delivery_release_items": invalid_delivery_release_items},
        ),
        _check(
            "p0_p0_5_items_are_release_blocking",
            not invalid_blocking_items,
            {"invalid_blocking_items": invalid_blocking_items},
        ),
        _check(
            "delivery_ready_items_have_reviewed_passing_technical_evidence",
            not any(row.get("errors") for row in report.get("delivery_assessments", [])),
            {"assessments": report.get("delivery_assessments", [])},
        ),
        _check(
            "closed_work_items_have_closed_lifecycle_language",
            not closed_items_with_open_language,
            {"closed_items_with_open_language": closed_items_with_open_language},
        ),
        _check(
            "execution_wave_order_matches_contract",
            bool(declared_wave_order) and declared_wave_order == actual_wave_order,
            {"declared": declared_wave_order, "actual": actual_wave_order},
        ),
        _check(
            "execution_waves_reference_known_truth",
            not unknown_wave_work_items
            and not unknown_wave_capabilities
            and not invalid_wave_capability_delivery,
            {
                "unknown_work_items": unknown_wave_work_items,
                "unknown_capabilities": unknown_wave_capabilities,
                "invalid_capability_delivery": invalid_wave_capability_delivery,
            },
        ),
        _check(
            "work_item_target_releases_exist_in_roadmap",
            bool(roadmap_releases) and not unknown_target_releases,
            {"roadmap_releases": sorted(roadmap_releases), "unknown_target_releases": unknown_target_releases},
        ),
        _check(
            "open_work_items_are_assigned_to_exactly_one_wave",
            not unassigned_open_items and not multiply_assigned_open_items,
            {
                "unassigned_open_items": unassigned_open_items,
                "multiply_assigned_open_items": multiply_assigned_open_items,
            },
        ),
        _check(
            "execution_wave_state_is_derived_and_dependencies_are_ordered",
            wave_identity["projection_matches_independent_first_open"]
            and not wave_dependency_issues
            and not completed_waves_without_evidence
            and not completed_waves_missing_local_evidence,
            {
                "first_open_wave": first_open_wave,
                "projected_current_execution_wave": wave_identity["current_execution_wave"],
                "projected_next_open_delivery_wave": wave_identity["next_open_delivery_wave"],
                "selection_basis": wave_identity["selection_basis"],
                "projection_matches_independent_first_open": wave_identity[
                    "projection_matches_independent_first_open"
                ],
                "dependency_issues": wave_dependency_issues,
                "completed_waves_without_evidence": completed_waves_without_evidence,
                "completed_waves_missing_local_evidence": completed_waves_missing_local_evidence,
                "evidence_check_scope": evidence_check_scope,
                "self_output_presence_check": "after_atomic_write" if self_output_referenced else "not_referenced",
                "state_source": "config/sage_work_item_registry.json",
            },
        ),
        _check(
            "execution_wave_release_scopes_are_explicit_and_bounded",
            bool(wave_release_scope_policy) and not invalid_wave_release_scopes,
            {
                "current_product_release": active_release,
                "invalid_wave_release_scopes": invalid_wave_release_scopes,
            },
        ),
    ]
    failed = [check for check in checks if not check["passed"]]
    payload = {
        "meta": {"kind": "sage_work_item_registry_validation", "version": "v1"},
        "summary": {
            "status": "PASS" if not failed else "FAIL",
            "total_checks": len(checks),
            "passed_checks": len(checks) - len(failed),
            "failed_checks": len(failed),
            "open_work_items": report.get("summary", {}).get("open_work_items"),
            "blocking_work_items": report.get("summary", {}).get("blocking_work_items"),
            "technical_blocking_work_items": report.get("summary", {}).get("technical_blocking_work_items"),
            "delivery_pending_work_items": report.get("summary", {}).get("delivery_pending_work_items"),
            "current_execution_wave": wave_identity["current_execution_wave"],
            "next_open_delivery_wave": wave_identity["next_open_delivery_wave"],
            "execution_wave_selection_basis": wave_identity["selection_basis"],
            "execution_waves": len(waves),
        },
        "checks": checks,
    }
    save_json_atomic(VALIDATION_RAW, payload)
    if self_output_referenced and not VALIDATION_RAW.is_file():
        raise OSError(f"Validation output was not materialized: {VALIDATION_RAW}")
    save_text_atomic(VALIDATION_REPORT, _render_report(payload))
    return payload


def main() -> int:
    started = time.perf_counter()
    payload = run_validation()
    record_work_package_operation_safely(
        operation_id="sage_work_item_registry_validation",
        result_status=payload["summary"]["status"],
        started=started,
        evidence_artifact="output/.raw/sage_work_item_registry_validation.json",
    )
    print(json.dumps(payload["summary"], ensure_ascii=False, sort_keys=True))
    return 0 if payload["summary"]["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
