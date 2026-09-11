from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
CODE_MAPS_DIR = TOOLS_DIR.parent
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))

from tools.core.atlas_io import load_atlas_data
from tools.core.json_io import load_json_file
from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.workload_profile import build_workload_profile

PERF_VALIDATION_PATH = RAW_DIR / "performance_budget_validation.json"
WORKLOAD_PROFILE_PATH = RAW_DIR / "workload_profile.json"
LEDGER_JSON_PATH = RAW_DIR / "performance_ledger.json"
LEDGER_REPORT_PATH = REPORTS_DIR / "performance_ledger.md"


def _float_values(rows: list[dict[str, Any]], key: str) -> list[float]:
    values: list[float] = []
    for row in rows:
        try:
            value = row.get(key)
            if value is None:
                continue
            values.append(float(value))
        except (TypeError, ValueError):
            continue
    return values


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2.0


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pct = max(0.0, min(100.0, percentile)) / 100.0
    index = round((len(ordered) - 1) * pct)
    return ordered[int(index)]


def _metric_stats(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    values = _float_values(rows, key)
    latest = values[0] if values else None
    previous = values[1] if len(values) > 1 else None
    delta = None if latest is None or previous is None else round(latest - previous, 3)
    return {
        "samples": len(values),
        "latest": latest,
        "min": min(values) if values else None,
        "max": max(values) if values else None,
        "median": _median(values),
        "p95": _percentile(values, 95),
        "delta_from_previous": delta,
    }


def build_trend_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_band: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        band = str(row.get("repo_band") or "UNKNOWN").upper()
        by_band.setdefault(band, []).append(row)
    return {
        "total_runs": len(rows),
        "passed_runs": sum(1 for row in rows if str(row.get("budget_status") or "PASS") == "PASS"),
        "failed_runs": sum(1 for row in rows if str(row.get("budget_status") or "PASS") == "FAIL"),
        "by_repo_band": {
            band: {
                "runs": len(band_rows),
                "pipeline_total_seconds": _metric_stats(band_rows, "pipeline_total_seconds"),
                "atlas_total_seconds": _metric_stats(band_rows, "atlas_total_seconds"),
                "fractal_total_seconds": _metric_stats(band_rows, "fractal_total_seconds"),
                "dead_code_step_seconds": _metric_stats(band_rows, "dead_code_step_seconds"),
            }
            for band, band_rows in sorted(by_band.items())
        },
    }


def _infer_repo_band() -> str:
    workload = load_json_file(WORKLOAD_PROFILE_PATH, {})
    if isinstance(workload, dict) and str(workload.get("band") or "") in {"S", "M", "L"}:
        return str(workload["band"])
    atlas = load_atlas_data(RAW_DIR)
    profile = build_workload_profile(atlas if isinstance(atlas, dict) else {})
    return str(profile.get("band") or "L")


def _normalize_seconds(value: Any) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "-"


def _build_report(rows: list[dict[str, Any]], trends: dict[str, Any] | None = None) -> str:
    trends = trends or build_trend_summary(rows)
    lines = [
        "# Performance Ledger",
        "",
        "Release and validation performance history (latest first).",
        "",
        "## Trend Summary",
        "",
        "| Repo Band | Runs | Pipeline Latest | Pipeline Median | Pipeline p95 | Delta |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for band, stats in (trends.get("by_repo_band", {}) or {}).items():
        pipeline = stats.get("pipeline_total_seconds", {}) if isinstance(stats, dict) else {}
        lines.append(
            f"| `{band}` | {stats.get('runs', 0)} | {_normalize_seconds(pipeline.get('latest'))} | "
            f"{_normalize_seconds(pipeline.get('median'))} | {_normalize_seconds(pipeline.get('p95'))} | "
            f"{_normalize_seconds(pipeline.get('delta_from_previous'))} |"
        )

    lines.extend([
        "",
        "## Runs",
        "",
        "| Run ID | Status | Repo Band | Latest Run (s) | Heavy Run (s) | Cached Run (s) | Validate (s) | Atlas (s) | Fractal (s) | Dead Code (s) | Notes |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ])
    for row in rows:
        lines.append(
            "| {run_id} | {budget_status} | {repo_band} | {latest_run} | {heavy_run} | {cached_run} | {validate} | {atlas} | {fractal} | {dead_code} | {notes} |".format(
                run_id=row.get("run_id", "-"),
                budget_status=row.get("budget_status", "PASS"),
                repo_band=row.get("repo_band", "-"),
                latest_run=_normalize_seconds(row.get("latest_pipeline_total_seconds", row.get("pipeline_total_seconds"))),
                heavy_run=_normalize_seconds(row.get("pipeline_total_seconds")),
                cached_run=_normalize_seconds(row.get("cached_pipeline_total_seconds")),
                validate=_normalize_seconds(row.get("validate_total_seconds")),
                atlas=_normalize_seconds(row.get("atlas_total_seconds")),
                fractal=_normalize_seconds(row.get("fractal_total_seconds")),
                dead_code=_normalize_seconds(row.get("dead_code_step_seconds")),
                notes=str(row.get("notes", "")).replace("\n", " ").strip(),
            )
        )
    return "\n".join(lines) + "\n"


def run_update(repo_band: str | None, notes: str, run_id: str | None) -> dict[str, Any]:
    perf = load_json_file(PERF_VALIDATION_PATH, {})
    metrics = perf.get("metrics", {}) if isinstance(perf, dict) else {}
    summary = perf.get("summary", {}) if isinstance(perf, dict) else {}
    budgets = perf.get("budgets", {}) if isinstance(perf, dict) else {}

    failed_checks_raw = summary.get("failed_checks", 1) if isinstance(summary, dict) else 1
    failed_checks = int(failed_checks_raw if failed_checks_raw is not None else 1)
    if not isinstance(metrics, dict) or not metrics:
        payload = {
            "ok": False,
            "reason": "performance_budget_validation_missing",
            "details": f"path={PERF_VALIDATION_PATH}",
        }
        return payload

    failed_check_names = [
        str(check.get("name"))
        for check in (perf.get("checks", []) if isinstance(perf, dict) else [])
        if isinstance(check, dict) and not check.get("passed")
    ]

    resolved_run_id = run_id or datetime.now().strftime("%Y%m%d-%H%M%S")
    selected_session_mode = str(
        (budgets.get("selected_session_mode") if isinstance(budgets, dict) else "")
        or metrics.get("pipeline_profile")
        or ""
    )
    physical_session_mode = str(metrics.get("physical_latest_session_mode") or "")
    entry = {
        "run_id": resolved_run_id,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "repo_band": (repo_band or _infer_repo_band()).upper(),
        "budget_status": "FAIL" if failed_checks > 0 else "PASS",
        "failed_checks": failed_checks,
        "failed_check_names": failed_check_names,
        "pipeline_total_seconds": metrics.get("pipeline_total_seconds"),
        "latest_pipeline_total_seconds": metrics.get("latest_pipeline_total_seconds", metrics.get("pipeline_total_seconds")),
        "cached_pipeline_total_seconds": metrics.get("cached_pipeline_total_seconds"),
        "validate_total_seconds": None,
        "atlas_total_seconds": metrics.get("atlas_total_seconds"),
        "budget_authority_session_mode": selected_session_mode,
        "physical_latest_session_mode": physical_session_mode,
        "physical_profile_is_budget_authority": bool(
            selected_session_mode and physical_session_mode and selected_session_mode == physical_session_mode
        ),
        "performance_evidence_status": str(metrics.get("performance_evidence_status") or ""),
        "physical_atlas_phase_timings": metrics.get("physical_atlas_phase_timings", {}),
        "physical_atlas_state_payload_profile": metrics.get("physical_atlas_state_payload_profile", {}),
        "physical_atlas_ast_lifecycle_profile": metrics.get("physical_atlas_ast_lifecycle_profile", {}),
        "fractal_total_seconds": metrics.get("fractal_total_seconds"),
        "dead_code_step_seconds": metrics.get("dead_code_step_seconds"),
        "notes": notes.strip(),
    }

    ledger = load_json_file(LEDGER_JSON_PATH, {})
    rows = ledger.get("runs", []) if isinstance(ledger, dict) else []
    if not isinstance(rows, list):
        rows = []
    rows = [r for r in rows if isinstance(r, dict) and str(r.get("run_id") or "") != resolved_run_id]
    rows.insert(0, entry)

    rows = rows[:200]
    trends = build_trend_summary(rows)
    payload = {
        "meta": {"kind": "performance_ledger", "version": "v1"},
        "summary": trends,
        "runs": rows,
    }
    save_json_atomic(LEDGER_JSON_PATH, payload)
    save_text_atomic(LEDGER_REPORT_PATH, _build_report(payload["runs"], trends))
    return {
        "ok": True,
        "entry": entry,
        "total_runs": len(payload["runs"]),
        "recorded_with_budget_failure": failed_checks > 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Update performance ledger from performance budget validation artifacts.")
    parser.add_argument("--repo-band", choices=["S", "M", "L"], help="Override inferred repository size band.")
    parser.add_argument("--notes", default="manual-ledger-update", help="Ledger note for this run.")
    parser.add_argument("--run-id", help="Optional run identifier. Default uses current timestamp.")
    args = parser.parse_args()

    result = run_update(args.repo_band, args.notes, args.run_id)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

