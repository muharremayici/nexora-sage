from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

CODE_MAPS_DIR = Path(__file__).resolve().parents[1]
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))

from tools.core.config import LOGS_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file


LOG_PATH = LOGS_DIR / "pipeline.log"
RAW_PATH = RAW_DIR / "performance_bottlenecks.json"
REPORT_PATH = REPORTS_DIR / "performance_bottlenecks.md"
PIPELINE_METRICS_PATH = REPORTS_DIR / "pipeline_metrics.json"
PERFORMANCE_BUDGET_PATH = CODE_MAPS_DIR / "config" / "performance_budget.json"


def _latest_session(log_text: str) -> str:
    marker = "PIPELINE ANALYSIS SESSION STARTED"
    idx = log_text.rfind(marker)
    if idx < 0:
        return log_text
    return log_text[idx:]


def _session_chunks(log_text: str) -> list[str]:
    marker = "PIPELINE ANALYSIS SESSION STARTED"
    chunks = log_text.split(marker)
    return [marker + chunk for chunk in chunks[1:]]


def _session_mode(chunk: str) -> str:
    match = re.search(r"\[MODE\] ([a-zA-Z0-9_-]+) active", chunk)
    if match:
        return match.group(1)
    if "[FAST] [SURGERY]" in chunk or "[WATCHDOG]" in chunk:
        return "watchdog_save_pulse"
    return "normal_full"


def _latest_budget_session(log_text: str) -> tuple[str, str, str | None]:
    """Return the latest full/release-like session for bottleneck reporting.

    Explicit single-step and watchdog sessions are useful telemetry, but they
    can trigger unusual dependency closures. They should not mask the latest
    full/release bottleneck evidence.
    """
    chunks = _session_chunks(log_text)
    if not chunks:
        return log_text, "unknown", None
    ignored_modes = {"explicit_step", "watchdog_save_pulse"}
    latest = chunks[-1]
    latest_mode = _session_mode(latest)
    for chunk in reversed(chunks):
        mode = _session_mode(chunk)
        if mode not in ignored_modes and _last_float(r"PIPELINE ANALYSIS COMPLETED in ([0-9.]+)s", chunk) is not None:
            ignored = latest_mode if chunk is not latest and latest_mode in ignored_modes else None
            return chunk, mode, ignored
    return latest, latest_mode, latest_mode


def _parse_steps(session: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    pattern = re.compile(r"STEP COMPLETED: (.+?) \(Time: ([0-9.]+)s\)")
    for name, seconds in pattern.findall(session):
        rows.append({"step": name.strip(), "seconds": float(seconds)})
    return sorted(rows, key=lambda item: item["seconds"], reverse=True)


def _parse_atlas_projects(session: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    pattern = re.compile(
        r"Atlas (.+?) \| files=(\d+) ast=(\d+) lift_mtime=(\d+) lift_fingerprint=(\d+) "
        r"lift_hash=(\d+) ast_workers=(\d+) ast_chunk=(\d+) ast_jobs=(\d+) ast_adaptive=(yes|no) "
        r"walk=([0-9.]+)s content=([0-9.]+)s ast_batch=([0-9.]+)s enrich=([0-9.]+)s "
        r"index=([0-9.]+)s cluster=([0-9.]+)s total=([0-9.]+)s"
    )
    for match in pattern.findall(session):
        (
            project,
            files,
            ast,
            lift_mtime,
            lift_fingerprint,
            lift_hash,
            ast_workers,
            ast_chunk,
            ast_jobs,
            ast_adaptive,
            walk,
            content,
            ast_batch,
            enrich,
            index,
            cluster,
            total,
        ) = match
        rows.append(
            {
                "project": project.strip(),
                "files": int(files),
                "ast_files": int(ast),
                "lift_mtime": int(lift_mtime),
                "lift_fingerprint": int(lift_fingerprint),
                "lift_hash": int(lift_hash),
                "ast_workers": int(ast_workers),
                "ast_chunk": int(ast_chunk),
                "ast_jobs": int(ast_jobs),
                "ast_adaptive": ast_adaptive == "yes",
                "walk_seconds": float(walk),
                "content_seconds": float(content),
                "ast_batch_seconds": float(ast_batch),
                "enrich_seconds": float(enrich),
                "index_seconds": float(index),
                "cluster_seconds": float(cluster),
                "total_seconds": float(total),
            }
        )
    return sorted(rows, key=lambda item: item["total_seconds"], reverse=True)


def _last_float(pattern: str, text: str) -> float | None:
    matches = re.findall(pattern, text)
    if not matches:
        return None
    try:
        return float(matches[-1])
    except (TypeError, ValueError):
        return None


def _latest_pipeline_metrics() -> dict[str, Any]:
    metrics = load_json_file(PIPELINE_METRICS_PATH, {})
    if not isinstance(metrics, dict):
        return {}

    steps = metrics.get("steps", [])
    if not isinstance(steps, list):
        steps = []

    normalized_steps: list[dict[str, Any]] = []
    for row in steps:
        if not isinstance(row, dict):
            continue
        try:
            seconds = float(row.get("seconds") or 0)
        except (TypeError, ValueError):
            seconds = 0.0
        normalized_steps.append(
            {
                "step": str(row.get("name") or row.get("step") or ""),
                "status": str(row.get("status") or ""),
                "seconds": seconds,
            }
        )
    normalized_steps.sort(key=lambda item: item["seconds"], reverse=True)

    try:
        total_seconds = float(metrics.get("total_seconds") or 0)
    except (TypeError, ValueError):
        total_seconds = 0.0

    return {
        "source": str(PIPELINE_METRICS_PATH.relative_to(CODE_MAPS_DIR)),
        "total_seconds": total_seconds,
        "top_steps": normalized_steps[:20],
    }


def _performance_profile_integrity_passed() -> bool:
    payload = load_json_file(RAW_DIR / "performance_profile_integrity_validation.json", {})
    summary = payload.get("summary", {}) if isinstance(payload, dict) else {}
    try:
        return int(summary.get("failed_checks", 1) or 0) == 0 and int(summary.get("passed_checks", 0) or 0) > 0
    except (TypeError, ValueError):
        return False


def _performance_bottleneck_policy() -> dict[str, Any]:
    payload = load_json_file(PERFORMANCE_BUDGET_PATH, {})
    if not isinstance(payload, dict):
        return {}
    policy = payload.get("bottleneck_report", {})
    return policy if isinstance(policy, dict) else {}


def _recommendation_record(rule: dict[str, Any]) -> dict[str, str]:
    return {
        "priority": str(rule.get("priority") or ""),
        "area": str(rule.get("area") or ""),
        "recommendation": str(rule.get("recommendation") or ""),
        "why": str(rule.get("why") or ""),
    }


def _recommendations(top_steps: list[dict[str, Any]]) -> list[dict[str, str]]:
    names = {row["step"]: row["seconds"] for row in top_steps}
    recs: list[dict[str, str]] = []
    policy = _performance_bottleneck_policy()
    for rule in policy.get("step_recommendations", []):
        if not isinstance(rule, dict):
            continue
        step = str(rule.get("step") or "")
        threshold = float(rule.get("threshold_seconds") or 0)
        if step and names.get(step, 0) > threshold:
            recs.append(_recommendation_record(rule))
    for rule in policy.get("aggregate_recommendations", []):
        if not isinstance(rule, dict):
            continue
        steps = [str(item) for item in rule.get("steps", []) if str(item)]
        threshold = float(rule.get("threshold_seconds") or 0)
        if sum(names.get(step, 0) for step in steps) > threshold:
            recs.append(_recommendation_record(rule))
    if not _performance_profile_integrity_passed():
        rule = policy.get("profile_integrity_recommendation", {})
        if isinstance(rule, dict):
            recs.append(_recommendation_record(rule))
    return recs


def _benchmark_advisories(atlas_projects: list[dict[str, Any]]) -> list[dict[str, str]]:
    if not atlas_projects:
        return []
    policy = _performance_bottleneck_policy()
    rows: list[dict[str, str]] = []
    for rule in policy.get("benchmark_advisories", []):
        if not isinstance(rule, dict) or rule.get("trigger") != "atlas_projects_present":
            continue
        item = _recommendation_record(rule)
        item["classification"] = str(rule.get("classification") or "benchmark_advisory")
        rows.append(item)
    return rows


def _report_status(total_seconds: float | None, steps: list[dict[str, Any]], recommendations: list[dict[str, str]]) -> tuple[str, str]:
    if total_seconds is None and not steps:
        return "UNKNOWN", "No full/release-like pipeline timing session was available for bottleneck classification."
    if recommendations:
        return "ATTENTION", f"{len(recommendations)} performance recommendations are open for review."
    return "PASS", "No performance bottleneck recommendations were produced for the selected session."


def build_report() -> dict[str, Any]:
    text = LOG_PATH.read_text(encoding="utf-8", errors="replace") if LOG_PATH.exists() else ""
    physical_latest = _latest_session(text)
    session, selected_session_mode, ignored_latest_session_mode = _latest_budget_session(text)
    steps = _parse_steps(session)
    atlas_projects = _parse_atlas_projects(session)
    total = _last_float(r"PIPELINE ANALYSIS COMPLETED in ([0-9.]+)s", session)
    atlas_total = _last_float(r"Atlas total \| projects=\d+ files=\d+ total=([0-9.]+)s", session)
    sqlite_parity = load_json_file(RAW_DIR / "sqlite_artifact_parity_validation.json", {})
    sqlite_proxy = load_json_file(RAW_DIR / "sqlite_proxy_coverage_validation.json", {})
    benchmark_summary = {
        "sqlite_parity": (sqlite_parity.get("summary") or {}).get("status"),
        "sqlite_proxy_coverage": (sqlite_proxy.get("summary") or {}).get("status"),
    }
    recommendations = _recommendations(steps)
    benchmark_advisories = _benchmark_advisories(atlas_projects)
    status, status_reason = _report_status(total, steps, recommendations)
    payload = {
        "meta": {"kind": "performance_bottlenecks", "version": "v1"},
        "summary": {
            "status": status,
            "status_reason": status_reason,
            "pipeline_total_seconds": total,
            "selected_session_mode": selected_session_mode,
            "physical_latest_session_mode": _session_mode(physical_latest),
            "ignored_latest_session_mode": ignored_latest_session_mode,
            "atlas_total_seconds": atlas_total,
            "completed_steps": len(steps),
            "top_step": steps[0] if steps else None,
            "sqlite_validation": benchmark_summary,
        },
        "top_steps": steps[:20],
        "latest_pipeline_metrics": _latest_pipeline_metrics(),
        "atlas_projects": atlas_projects,
        "recommendations": recommendations,
        "benchmark_advisories": benchmark_advisories,
    }
    return payload


def render_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# Performance Bottleneck Report",
        "",
        f"- status: `{summary.get('status')}`",
        f"- status_reason: `{summary.get('status_reason')}`",
        f"- pipeline_total_seconds: `{summary.get('pipeline_total_seconds')}`",
        f"- selected_session_mode: `{summary.get('selected_session_mode')}`",
        f"- physical_latest_session_mode: `{summary.get('physical_latest_session_mode')}`",
        f"- ignored_latest_session_mode: `{summary.get('ignored_latest_session_mode')}`",
        f"- atlas_total_seconds: `{summary.get('atlas_total_seconds')}`",
        f"- completed_steps: `{summary.get('completed_steps')}`",
        f"- sqlite_parity: `{(summary.get('sqlite_validation') or {}).get('sqlite_parity')}`",
        f"- sqlite_proxy_coverage: `{(summary.get('sqlite_validation') or {}).get('sqlite_proxy_coverage')}`",
        "",
    ]
    latest_metrics = payload.get("latest_pipeline_metrics") or {}
    if latest_metrics:
        lines.extend(
            [
            "## Latest Pipeline Metrics Artifact",
            "",
            "This section reflects the latest `output/reports/pipeline_metrics.json` artifact. Bottleneck recommendations still use the latest full/release-like log session so single-step probes do not erase release evidence.",
            "",
            f"- source: `{latest_metrics.get('source')}`",
            f"- total_seconds: `{latest_metrics.get('total_seconds')}`",
            "",
            "| Rank | Step | Seconds |",
            "|---:|---|---:|",
            ]
        )
        for idx, row in enumerate((latest_metrics.get("top_steps") or [])[:10], start=1):
            lines.append(f"| {idx} | `{row.get('step')}` | {float(row.get('seconds') or 0):.2f} |")
        lines.append("")
    lines.extend(
        [
            "## Top Steps",
            "",
            "| Rank | Step | Seconds |",
            "|---:|---|---:|",
        ]
    )
    for idx, row in enumerate(payload.get("top_steps", [])[:15], start=1):
        lines.append(f"| {idx} | `{row.get('step')}` | {float(row.get('seconds') or 0):.2f} |")
    lines.extend(["", "## Atlas Project Profile", "", "| Project | Files | AST Files | AST Workers | AST Batch | Enrich | Total |", "|---|---:|---:|---:|---:|---:|---:|"])
    for row in payload.get("atlas_projects", []):
        lines.append(
            f"| `{row.get('project')}` | {row.get('files')} | {row.get('ast_files')} | {row.get('ast_workers')} | "
            f"{float(row.get('ast_batch_seconds') or 0):.2f} | {float(row.get('enrich_seconds') or 0):.2f} | {float(row.get('total_seconds') or 0):.2f} |"
        )
    lines.extend(["", "## Recommendations", ""])
    for rec in payload.get("recommendations", []):
        lines.append(f"- **{rec.get('priority')} {rec.get('area')}**: {rec.get('recommendation')} {rec.get('why')}")
    if not payload.get("recommendations", []):
        lines.append("- None.")
    lines.extend(["", "## Benchmark Advisories", ""])
    for rec in payload.get("benchmark_advisories", []):
        lines.append(f"- **{rec.get('priority')} {rec.get('area')}**: {rec.get('recommendation')} {rec.get('why')}")
    if not payload.get("benchmark_advisories", []):
        lines.append("- None.")
    return "\n".join(lines) + "\n"


def main() -> int:
    payload = build_report()
    save_json_atomic(RAW_PATH, payload)
    save_text_atomic(REPORT_PATH, render_markdown(payload))
    print(json.dumps(payload.get("summary", {}), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
