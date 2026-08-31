from __future__ import annotations

import argparse
import fnmatch
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
CODE_MAPS_DIR = TOOLS_DIR.parent
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))

from tools.core.json_io import load_json_file
from tools.core.projects_registry import resolve_runtime_projects
from tools.core.config import (
    CONFIG_DIR,
    RAW_DIR,
    REPORTS_DIR,
    ROOT,
    DYNAMIC_CONFIG,
    SOURCE_EXTENSIONS,
    save_json_atomic,
    save_text_atomic,
)

GOLDEN_DIR = CONFIG_DIR / "golden"
DEAD_CODE_TRUTH_PATH = GOLDEN_DIR / "dead_code_truth_set.json"
MERGE_TRUTH_PATH = GOLDEN_DIR / "merge_candidates_truth_set.json"
BASELINE_PATH = GOLDEN_DIR / "signal_regression_baseline.json"
PROJECT_TRUTH_DIR = GOLDEN_DIR / "projects"
UNIVERSAL_TRUTH_DIR = GOLDEN_DIR / "universal"
WORKSPACE_TRUTH_DIR = GOLDEN_DIR / "workspace" / "projects"

DEAD_CODE_PATH = RAW_DIR / "dead_code.json"
DECISION_EVIDENCE_PATH = RAW_DIR / "decision_evidence.json"

RAW_OUTPUT_PATH = RAW_DIR / "signal_regression_validation.json"
REPORT_OUTPUT_PATH = REPORTS_DIR / "signal_regression_validation.md"
def _norm_path(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.replace("\\", "/").strip()


def _match_field(actual: Any, expected: Any, *, path_mode: bool = False) -> bool:
    if expected is None:
        return True
    if isinstance(expected, str) and expected.strip() == "*":
        return True

    actual_val = _norm_path(actual) if path_mode else str(actual or "")
    if isinstance(expected, str):
        expected_val = _norm_path(expected) if path_mode else expected
        if expected_val.startswith("re:"):
            try:
                return re.search(expected_val[3:], actual_val) is not None
            except re.error:
                return False
        if any(ch in expected_val for ch in "*?[]"):
            return fnmatch.fnmatchcase(actual_val, expected_val)
        return actual_val == expected_val
    return actual == expected


def _match_record(record: dict[str, Any], matcher: dict[str, Any], *, path_fields: set[str]) -> bool:
    for key, expected in matcher.items():
        if not _match_field(record.get(key), expected, path_mode=key in path_fields):
            return False
    return True


def _collect_dead_code_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items = payload.get("items", []) if isinstance(payload, dict) else []
    return items if isinstance(items, list) else []


def _collect_merge_candidates(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    deep = payload.get("focus_deep_dive", {})
    if not isinstance(deep, dict):
        return []

    candidates: list[dict[str, Any]] = []
    for bucket_name in ("trusted_top_candidates", "review_queue_examples"):
        bucket = deep.get(bucket_name, [])
        if not isinstance(bucket, list):
            continue
        for row in bucket:
            if not isinstance(row, dict):
                continue
            enriched = dict(row)
            enriched["bucket"] = bucket_name
            candidates.append(enriched)
    return candidates


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _active_project_keys(dead_code_payload: dict[str, Any]) -> set[str]:
    meta = dead_code_payload.get("meta", {}) if isinstance(dead_code_payload, dict) else {}
    execution_scope = meta.get("execution_scope", {}) if isinstance(meta, dict) else {}
    analyzed = execution_scope.get("analyzed_projects", []) if isinstance(execution_scope, dict) else []
    artifact_projects = {
        str(project).strip()
        for project in analyzed
        if str(project).strip()
    }
    if artifact_projects:
        return artifact_projects
    return set(resolve_runtime_projects(ROOT))


def _matcher_project_scope(matcher: dict[str, Any]) -> str:
    if not isinstance(matcher, dict):
        return ""
    project = str(matcher.get("project") or "").strip()
    if project in {"", "*"}:
        return ""
    return project


def _case_in_active_scope(case: dict[str, Any], active_projects: set[str]) -> bool:
    matcher = case.get("match", {}) if isinstance(case.get("match"), dict) else {}
    scoped_project = _matcher_project_scope(matcher)
    if scoped_project and scoped_project not in active_projects:
        return False

    source_project = str(matcher.get("source") or "").strip()
    if source_project and source_project != "*" and source_project not in active_projects:
        return False
    return True


def _merge_case_is_workspace_anchored(case: dict[str, Any]) -> bool:
    matcher = case.get("match", {}) if isinstance(case.get("match"), dict) else {}
    for key in ("project", "source", "target", "proposed_target_path", "file"):
        value = str(matcher.get(key) or "").strip()
        if value and value != "*":
            return True
    return False


def _project_root(project_key: str) -> Path:
    variations = (DYNAMIC_CONFIG.get("variations", {}) or {})
    rel_path = variations.get(project_key, ".")
    return (ROOT / rel_path).resolve()


def _count_symbol_occurrences(project_key: str, symbol: str) -> tuple[int, int]:
    root = _project_root(project_key)
    if not root.exists() or not root.is_dir() or not symbol:
        return (0, 0)
    allowed_exts = {ext.lower() for ext in (SOURCE_EXTENSIONS or {".ts", ".tsx", ".js", ".jsx"})}
    total_occ = 0
    hit_files = 0
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in allowed_exts:
            continue
        parts = set(path.parts)
        if "node_modules" in parts or ".git" in parts:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if symbol not in text:
            continue
        count = text.count(symbol)
        if count > 0:
            hit_files += 1
            total_occ += count
    return (total_occ, hit_files)


def _ratio_delta(current: int, baseline: int) -> float:
    if baseline <= 0:
        return 0.0 if current <= 0 else 1.0
    return abs(current - baseline) / float(baseline)


def _evaluate_truth_set(
    truth_payload: dict[str, Any],
    records: list[dict[str, Any]],
    *,
    kind: str,
    path_fields: set[str],
    active_projects: set[str],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    cases = truth_payload.get("cases", []) if isinstance(truth_payload, dict) else []
    checks: list[dict[str, Any]] = []
    totals = {"total": 0, "passed": 0, "failed": 0, "skipped_scope": 0}

    for case in cases if isinstance(cases, list) else []:
        if not isinstance(case, dict):
            continue
        if case.get("enabled", True) is False:
            continue
        if kind.startswith("merge_truth:") and ":universal" not in kind:
            if not _merge_case_is_workspace_anchored(case):
                totals["skipped_scope"] += 1
                continue
        if not _case_in_active_scope(case, active_projects):
            totals["skipped_scope"] += 1
            continue

        totals["total"] += 1
        case_id = str(case.get("id") or f"{kind}_case_{totals['total']}")
        matcher = case.get("match", {}) if isinstance(case.get("match"), dict) else {}
        expected = str(case.get("expected") or "present").strip().lower()
        matched = [row for row in records if _match_record(row, matcher, path_fields=path_fields)]
        matched_count = len(matched)

        if expected == "absent":
            passed = matched_count == 0
        else:
            passed = matched_count > 0

        if passed:
            totals["passed"] += 1
        else:
            totals["failed"] += 1

        checks.append(
            {
                "name": f"{kind}:{case_id}",
                "passed": passed,
                "details": f"expected={expected} matched={matched_count}",
            }
        )

    return checks, totals


def _enabled_case_count(truth_payload: dict[str, Any]) -> int:
    if not isinstance(truth_payload, dict):
        return 0
    cases = truth_payload.get("cases", [])
    if not isinstance(cases, list):
        return 0
    return sum(1 for case in cases if isinstance(case, dict) and case.get("enabled", True) is not False)


def _enabled_case_count_in_scope(truth_payload: dict[str, Any], active_projects: set[str]) -> int:
    if not isinstance(truth_payload, dict):
        return 0
    cases = truth_payload.get("cases", [])
    if not isinstance(cases, list):
        return 0
    return sum(
        1
        for case in cases
        if isinstance(case, dict)
        and case.get("enabled", True) is not False
        and _case_in_active_scope(case, active_projects)
    )


def _iter_truth_payloads(base_path: Path, filename: str, kind: str) -> list[tuple[str, dict[str, Any]]]:
    payloads: list[tuple[str, dict[str, Any]]] = []
    base_payload = load_json_file(base_path, {})
    if isinstance(base_payload, dict):
        payloads.append((f"{kind}:global", base_payload))

    universal_path = UNIVERSAL_TRUTH_DIR / filename
    universal_payload = load_json_file(universal_path, {})
    if isinstance(universal_payload, dict) and universal_payload:
        payloads.append((f"{kind}:universal", universal_payload))

    for project_truth_root, root_label in (
        (WORKSPACE_TRUTH_DIR, "workspace"),
        (PROJECT_TRUTH_DIR, "legacy_workspace"),
    ):
        if not project_truth_root.exists() or not project_truth_root.is_dir():
            continue
        for project_dir in sorted(project_truth_root.iterdir(), key=lambda p: p.name.lower()):
            if not project_dir.is_dir():
                continue
            candidate = project_dir / filename
            project_payload = load_json_file(candidate, {})
            if isinstance(project_payload, dict) and project_payload:
                payloads.append((f"{kind}:{root_label}:project:{project_dir.name}", project_payload))
    return payloads


def _build_current_metrics(dead_code_payload: dict[str, Any], decision_payload: dict[str, Any]) -> dict[str, Any]:
    dead_summary = dead_code_payload.get("summary", {}) if isinstance(dead_code_payload, dict) else {}
    studio_rows = decision_payload.get("studio_summary", []) if isinstance(decision_payload, dict) else []
    if not isinstance(studio_rows, list):
        studio_rows = []

    total_decisions = sum(_safe_int(row.get("total_decisions")) for row in studio_rows if isinstance(row, dict))
    auto_merge_ready = sum(_safe_int(row.get("auto_merge_ready")) for row in studio_rows if isinstance(row, dict))
    manual_review = sum(_safe_int(row.get("manual_review")) for row in studio_rows if isinstance(row, dict))

    return {
        "dead_code": {
            "total": _safe_int(dead_summary.get("total")),
            "high": _safe_int(dead_summary.get("high")),
            "medium": _safe_int(dead_summary.get("medium")),
            "low": _safe_int(dead_summary.get("low")),
        },
        "merge_decisions": {
            "total_decisions": total_decisions,
            "auto_merge_ready": auto_merge_ready,
            "manual_review": manual_review,
        },
    }


def _baseline_project_scope(baseline: dict[str, Any]) -> set[str]:
    meta = baseline.get("meta", {}) if isinstance(baseline, dict) else {}
    declared = meta.get("project_scope", []) if isinstance(meta, dict) else []
    if isinstance(declared, list) and declared:
        return {str(project).strip() for project in declared if str(project).strip()}
    variations = DYNAMIC_CONFIG.get("variations", {}) or {}
    return {str(project).strip() for project in variations if str(project).strip()}


def _evaluate_baseline(
    current: dict[str, Any],
    baseline: dict[str, Any],
    active_projects: set[str],
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    sections = ("dead_code", "merge_decisions")
    baseline_scope = _baseline_project_scope(baseline)
    if baseline_scope != active_projects:
        return [
            {
                "name": "baseline:scope_compatible",
                "passed": True,
                "details": {
                    "status": "not_applicable",
                    "active_projects": sorted(active_projects),
                    "baseline_projects": sorted(baseline_scope),
                    "reason": "fixed signal totals cannot govern a different repository project scope",
                },
            }
        ]

    checks.append(
        {
            "name": "baseline:scope_compatible",
            "passed": True,
            "details": {"status": "applicable", "projects": sorted(active_projects)},
        }
    )

    for section in sections:
        current_section = current.get(section, {})
        baseline_section = baseline.get(section, {})
        baseline_summary = baseline_section.get("summary", {}) if isinstance(baseline_section, dict) else {}
        max_drift = baseline_section.get("max_drift", {}) if isinstance(baseline_section, dict) else {}

        if not isinstance(baseline_summary, dict) or not baseline_summary:
            checks.append(
                {
                    "name": f"baseline:{section}:configured",
                    "passed": False,
                    "details": "missing baseline summary",
                }
            )
            continue

        checks.append(
            {
                "name": f"baseline:{section}:configured",
                "passed": True,
                "details": "baseline summary present",
            }
        )

        for metric_name, baseline_value in baseline_summary.items():
            current_value = _safe_int(current_section.get(metric_name))
            baseline_int = _safe_int(baseline_value)
            raw_tolerance = max_drift.get(metric_name, max_drift.get("*", 0.15))
            try:
                tolerance = float(raw_tolerance)
            except (TypeError, ValueError):
                tolerance = 0.15
            drift = _ratio_delta(current_value, baseline_int)
            passed = drift <= tolerance
            checks.append(
                {
                    "name": f"baseline:{section}:{metric_name}",
                    "passed": passed,
                    "details": (
                        f"current={current_value} baseline={baseline_int} "
                        f"drift={drift:.3f} tolerance={tolerance:.3f}"
                    ),
                }
            )

    return checks


def _baseline_provenance_checks(baseline: dict[str, Any]) -> list[dict[str, Any]]:
    meta = baseline.get("meta", {}) if isinstance(baseline, dict) else {}
    required = ("owner", "source", "generated_by", "contract_version", "last_validated", "intentional_change")
    missing = [field for field in required if not str((meta or {}).get(field) or "").strip()]
    return [
        {
            "name": "baseline:provenance:intentional_change_required",
            "passed": not missing,
            "details": {"missing": missing, "contract_version": (meta or {}).get("contract_version")},
        }
    ]


def _write_default_baseline(
    current: dict[str, Any],
    intentional_change: str,
    active_projects: set[str],
) -> dict[str, Any]:
    payload = {
        "meta": {
            "kind": "signal_regression_baseline",
            "version": "v1",
            "owner": "Nexora SAGE governance",
            "source": "current signal artifacts",
            "generated_by": "tools/validate_signal_regression.py --update-baseline",
            "contract_version": "signal-baseline-provenance-v1",
            "scope_mode": "exact_project_set",
            "project_scope": sorted(active_projects),
            "evidence_role": "mutable_workspace_calibration_not_release_universality",
            "last_validated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "intentional_change": intentional_change,
        },
        "dead_code": {
            "summary": current.get("dead_code", {}),
            "max_drift": {"*": 0.2, "high": 0.25, "medium": 0.25, "low": 0.25},
        },
        "merge_decisions": {
            "summary": current.get("merge_decisions", {}),
            "max_drift": {"*": 0.2, "manual_review": 0.35},
        },
    }
    save_json_atomic(BASELINE_PATH, payload)
    return payload


def run_validation(update_baseline: bool = False, intentional_change: str = "") -> dict[str, Any]:
    dead_code_payload = load_json_file(DEAD_CODE_PATH, {})
    decision_payload = load_json_file(DECISION_EVIDENCE_PATH, {})
    dead_truth_layers = _iter_truth_payloads(DEAD_CODE_TRUTH_PATH, "dead_code_truth_set.json", "dead_code_truth")
    merge_truth_layers = _iter_truth_payloads(MERGE_TRUTH_PATH, "merge_candidates_truth_set.json", "merge_truth")
    baseline_payload = load_json_file(BASELINE_PATH, {})

    dead_code_items = _collect_dead_code_items(dead_code_payload)
    merge_candidates = _collect_merge_candidates(decision_payload)
    current_metrics = _build_current_metrics(dead_code_payload, decision_payload)
    active_projects = _active_project_keys(dead_code_payload)

    if update_baseline:
        if not intentional_change.strip():
            baseline_payload = {
                "meta": {"kind": "signal_regression_baseline", "version": "v1"},
                "dead_code": {"summary": {}},
                "merge_decisions": {"summary": {}},
            }
        else:
            baseline_payload = _write_default_baseline(
                current_metrics,
                intentional_change.strip(),
                active_projects,
            )
    elif not isinstance(baseline_payload, dict) or not baseline_payload:
        baseline_payload = {}

    dead_checks: list[dict[str, Any]] = []
    merge_checks: list[dict[str, Any]] = []
    dead_totals = {"total": 0, "passed": 0, "failed": 0, "skipped_scope": 0}
    merge_totals = {"total": 0, "passed": 0, "failed": 0, "skipped_scope": 0}
    truth_gate_checks: list[dict[str, Any]] = []

    for layer_name, dead_truth in dead_truth_layers:
        layer_checks, layer_totals = _evaluate_truth_set(
            dead_truth,
            dead_code_items,
            kind=layer_name,
            path_fields={"file"},
            active_projects=active_projects,
        )
        dead_checks.extend(layer_checks)
        dead_totals["total"] += layer_totals["total"]
        dead_totals["passed"] += layer_totals["passed"]
        dead_totals["failed"] += layer_totals["failed"]
        dead_totals["skipped_scope"] += layer_totals.get("skipped_scope", 0)

        dead_policy = dead_truth.get("policy", {}) if isinstance(dead_truth, dict) else {}
        dead_min_enabled = _safe_int((dead_policy or {}).get("min_enabled_cases", 1))
        dead_enabled_total = _enabled_case_count(dead_truth)
        dead_enabled = _enabled_case_count_in_scope(dead_truth, active_projects)
        out_of_scope_only = dead_enabled == 0 and dead_enabled_total > 0
        partial_scope_layer = (
            layer_name.startswith("dead_code_truth:global") or layer_name.startswith("dead_code_truth:legacy_workspace")
        ) and dead_enabled_total > dead_enabled and dead_enabled < dead_min_enabled
        truth_gate_checks.append(
            {
                "name": f"truth_set:{layer_name}:minimum_enabled_cases",
                "passed": out_of_scope_only or partial_scope_layer or dead_enabled >= dead_min_enabled,
                "details": (
                    f"enabled_in_scope={dead_enabled} required>={dead_min_enabled}"
                    if not (out_of_scope_only or partial_scope_layer)
                    else f"enabled_in_scope={dead_enabled} (out_of_scope_enabled={dead_enabled_total}) -> N/A"
                ),
            }
        )

    for layer_name, merge_truth in merge_truth_layers:
        layer_checks, layer_totals = _evaluate_truth_set(
            merge_truth,
            merge_candidates,
            kind=layer_name,
            path_fields={"target", "proposed_target_path"},
            active_projects=active_projects,
        )
        merge_checks.extend(layer_checks)
        merge_totals["total"] += layer_totals["total"]
        merge_totals["passed"] += layer_totals["passed"]
        merge_totals["failed"] += layer_totals["failed"]
        merge_totals["skipped_scope"] += layer_totals.get("skipped_scope", 0)

        merge_policy = merge_truth.get("policy", {}) if isinstance(merge_truth, dict) else {}
        merge_min_enabled = _safe_int((merge_policy or {}).get("min_enabled_cases", 1))
        merge_enabled_total = _enabled_case_count(merge_truth)
        merge_enabled = _enabled_case_count_in_scope(merge_truth, active_projects)
        out_of_scope_only = merge_enabled == 0 and merge_enabled_total > 0
        partial_scope_layer = (
            layer_name.startswith("merge_truth:global") or layer_name.startswith("merge_truth:legacy_workspace")
        ) and merge_enabled_total > merge_enabled and merge_enabled < merge_min_enabled
        truth_gate_checks.append(
            {
                "name": f"truth_set:{layer_name}:minimum_enabled_cases",
                "passed": out_of_scope_only or partial_scope_layer or merge_enabled >= merge_min_enabled,
                "details": (
                    f"enabled_in_scope={merge_enabled} required>={merge_min_enabled}"
                    if not (out_of_scope_only or partial_scope_layer)
                    else f"enabled_in_scope={merge_enabled} (out_of_scope_enabled={merge_enabled_total}) -> N/A"
                ),
            }
        )

    baseline_checks = _evaluate_baseline(current_metrics, baseline_payload, active_projects)
    evidence_checks: list[dict[str, Any]] = []
    for layer_name, dead_truth in dead_truth_layers:
        dead_policy = dead_truth.get("policy", {}) if isinstance(dead_truth, dict) else {}
        evidence_cfg = dead_policy.get("manual_evidence", {}) if isinstance(dead_policy, dict) else {}
        evidence_enabled = bool(evidence_cfg.get("enabled", True))
        if not evidence_enabled:
            continue
        max_occ_present = _safe_int(evidence_cfg.get("max_text_occurrences_for_present_dead", 2))
        min_occ_absent = _safe_int(evidence_cfg.get("min_text_occurrences_for_absent_guard", 2))
        for case in dead_truth.get("cases", []) if isinstance(dead_truth, dict) else []:
            if not isinstance(case, dict) or case.get("enabled", True) is False:
                continue
            matcher = case.get("match", {}) if isinstance(case.get("match"), dict) else {}
            if not _case_in_active_scope(case, active_projects):
                continue
            project_key = str(matcher.get("project") or "").strip()
            symbol = str(matcher.get("symbol") or "").strip()
            expected = str(case.get("expected") or "present").strip().lower()
            case_id = str(case.get("id") or "case")
            if not project_key or project_key == "*" or not symbol:
                continue
            total_occ, hit_files = _count_symbol_occurrences(project_key, symbol)
            if expected == "present":
                passed = total_occ <= max_occ_present
                details = (
                    f"symbol={symbol} occurrences={total_occ} files={hit_files} "
                    f"threshold<={max_occ_present}"
                )
            else:
                passed = total_occ >= min_occ_absent
                details = (
                    f"symbol={symbol} occurrences={total_occ} files={hit_files} "
                    f"threshold>={min_occ_absent}"
                )
            evidence_checks.append(
                {
                    "name": f"evidence:{layer_name}:{case_id}",
                    "passed": passed,
                    "details": details,
                }
            )

    provenance_checks = _baseline_provenance_checks(baseline_payload)
    checks = [*truth_gate_checks, *dead_checks, *merge_checks, *evidence_checks, *baseline_checks, *provenance_checks]
    summary = {
        "total_checks": len(checks),
        "passed_checks": sum(1 for row in checks if row.get("passed")),
        "failed_checks": sum(1 for row in checks if not row.get("passed")),
    }

    payload = {
        "meta": {"kind": "signal_regression_validation", "version": "v1"},
        "truth_summary": {
            "dead_code_truth_cases": dead_totals,
            "merge_truth_cases": merge_totals,
            "dead_truth_layers": [name for name, _ in dead_truth_layers],
            "merge_truth_layers": [name for name, _ in merge_truth_layers],
            "active_projects": sorted(active_projects),
        },
        "artifact_counts": {
            "dead_code_items": len(dead_code_items),
            "merge_candidates": len(merge_candidates),
        },
        "current_metrics": current_metrics,
        "summary": summary,
        "checks": checks,
    }
    save_json_atomic(RAW_OUTPUT_PATH, payload)

    lines = [
        "# Signal Regression Validation",
        "",
        f"- Total checks: `{summary['total_checks']}`",
        f"- Passed: `{summary['passed_checks']}`",
        f"- Failed: `{summary['failed_checks']}`",
        "",
        "## Current Metrics",
        "",
        f"- Dead code total/high/medium/low: `{current_metrics['dead_code']['total']}` / `{current_metrics['dead_code']['high']}` / `{current_metrics['dead_code']['medium']}` / `{current_metrics['dead_code']['low']}`",
        f"- Merge decisions total/auto/manual: `{current_metrics['merge_decisions']['total_decisions']}` / `{current_metrics['merge_decisions']['auto_merge_ready']}` / `{current_metrics['merge_decisions']['manual_review']}`",
        "",
        "| Check | Result | Details |",
        "|---|---|---|",
    ]
    for check in checks:
        lines.append(
            f"| `{check['name']}` | {'PASS' if check['passed'] else 'FAIL'} | {check['details']} |"
        )
    save_text_atomic(REPORT_OUTPUT_PATH, "\n".join(lines) + "\n")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate dead-code and merge-signal regression gates.")
    parser.add_argument("--update-baseline", action="store_true", help="Write baseline from current artifacts before validating.")
    parser.add_argument("--intentional-change", default="", help="Required explanation when updating the signal baseline.")
    args = parser.parse_args()

    payload = run_validation(update_baseline=args.update_baseline, intentional_change=args.intentional_change)
    print(json.dumps(payload.get("summary", {}), ensure_ascii=False))
    return 0 if payload.get("summary", {}).get("failed_checks", 1) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

