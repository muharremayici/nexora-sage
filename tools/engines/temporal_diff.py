"""
Temporal Diff Engine - compares current pipeline run against the previous snapshot.
Stores a summary snapshot after each run and diffs against the last one.
"""

import json
import time

from tools.core.audit_report import get_total_violations
from tools.core.config import RAW_DIR, REPORTS_DIR, SNAPSHOTS_DIR, SNAPSHOT_RETENTION
from tools.core.genome_io import load_genome_data
from tools.core.json_io import load_json_file
from tools.core.logger import logger
from tools.core.config import save_json_atomic, save_text_atomic

def _prune_old_snapshots() -> int:
    existing = sorted(SNAPSHOTS_DIR.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    removed = 0
    for stale_path in existing[SNAPSHOT_RETENTION:]:
        stale_path.unlink(missing_ok=True)
        removed += 1
    return removed


def save_snapshot_and_diff():
    logger.info("Running temporal diff analysis...")

    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)

    current = _build_summary()

    existing = sorted(SNAPSHOTS_DIR.glob("*.json"), reverse=True)
    previous = None
    previous_error = None
    if existing:
        try:
            previous = json.loads(existing[0].read_text("utf-8"))
            logger.info(f"Comparing against snapshot: {existing[0].name}")
        except Exception as exc:
            previous_error = {
                "error_type": type(exc).__name__,
                "snapshot": existing[0].name,
            }
            logger.warning(
                "Previous temporal snapshot is unavailable: %s (%s)",
                existing[0],
                type(exc).__name__,
            )

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    snapshot_path = SNAPSHOTS_DIR / f"{timestamp}_summary.json"
    save_json_atomic(snapshot_path, current)
    logger.info(f"Snapshot saved: {snapshot_path.name}")
    removed = _prune_old_snapshots()
    if removed:
        logger.info(f"Pruned {removed} stale snapshots (retention={SNAPSHOT_RETENTION})")

    if previous is not None:
        diff = _compute_diff(previous, current)
    elif previous_error:
        diff = {
            "status": "previous_snapshot_unavailable",
            "message": "Previous snapshot could not be parsed; no temporal comparison claim was made.",
            **previous_error,
        }
    else:
        diff = {"status": "first_run", "message": "No previous snapshot to compare against."}

    diff_json_path = RAW_DIR / "temporal_diff.json"
    save_json_atomic(diff_json_path, diff)

    md_lines = _render_diff_md(diff)
    md_path = REPORTS_DIR / "temporal_diff.md"
    save_text_atomic(md_path, "\n".join(md_lines))

    logger.info(f"[OK] Temporal diff report saved to {md_path}")


def _build_summary():
    summary = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S")}

    hs = load_json_file(RAW_DIR / "health_score.json")
    if hs:
        summary["health_score"] = hs.get("overall", 0)
        summary["health_breakdown"] = hs.get("breakdown", {})

    dead_payload = load_json_file(RAW_DIR / "dead_code.json")
    dead = dead_payload.get("items", []) if isinstance(dead_payload, dict) else dead_payload
    summary["dead_exports"] = len(dead) if isinstance(dead, list) else 0

    circ = load_json_file(RAW_DIR / "circular_deps.json")
    summary["circular_deps"] = circ.get("cycles_found", 0) if circ else 0
    summary["total_import_edges"] = circ.get("total_edges", 0) if circ else 0

    summary["audit_violations"] = get_total_violations()

    risk = load_json_file(RAW_DIR / "module_risk_matrix.json")
    risk_rows = risk.get("rows", []) if isinstance(risk, dict) else risk
    if isinstance(risk_rows, list):
        summary["high_risk_modules"] = len([r for r in risk_rows if "HIGH" in r.get("risk_level", "")])
        summary["total_modules_analyzed"] = len(risk_rows)

    genome = load_genome_data()
    summary["total_atoms"] = len(genome) if isinstance(genome, dict) else 0

    return summary


def _compute_diff(prev, curr):
    changes = []
    metrics = [
        ("health_score", "Health Score", True),
        ("dead_exports", "Dead Exports", False),
        ("circular_deps", "Circular Deps", False),
        ("audit_violations", "Audit Violations", False),
        ("total_atoms", "Total Atoms", True),
        ("high_risk_modules", "High Risk Modules", False),
    ]

    for key, label, higher_is_better in metrics:
        old_val = prev.get(key, 0)
        new_val = curr.get(key, 0)
        delta = new_val - old_val

        if delta == 0:
            status = "unchanged"
            marker = "[-]"
        elif (delta > 0 and higher_is_better) or (delta < 0 and not higher_is_better):
            status = "improved"
            marker = "[OK]"
        else:
            status = "degraded"
            marker = "[FAIL]"

        changes.append(
            {
                "metric": label,
                "key": key,
                "old": old_val,
                "new": new_val,
                "delta": delta,
                "status": status,
                "emoji": marker,
            }
        )

    breakdown_diff = {}
    prev_bd = prev.get("health_breakdown", {})
    curr_bd = curr.get("health_breakdown", {})
    for key in set(list(prev_bd.keys()) + list(curr_bd.keys())):
        old_v = prev_bd.get(key, 0)
        new_v = curr_bd.get(key, 0)
        breakdown_diff[key] = {"old": old_v, "new": new_v, "delta": new_v - old_v}

    return {
        "status": "compared",
        "previous_date": prev.get("generated_at", "unknown"),
        "current_date": curr.get("generated_at", "now"),
        "changes": changes,
        "health_breakdown_diff": breakdown_diff,
    }


def _render_diff_md(diff):
    lines = ["# Temporal Diff Report", ""]

    if diff.get("status") == "first_run":
        lines.append("**First run - no previous snapshot to compare.**")
        lines.append("")
        lines.append("Future runs will show what changed between executions.")
        return lines
    if diff.get("status") == "previous_snapshot_unavailable":
        lines.append("**Previous snapshot unavailable - no temporal comparison claim was made.**")
        lines.append("")
        lines.append(
            f"Snapshot: `{diff.get('snapshot', '?')}`; error type: "
            f"`{diff.get('error_type', 'unknown')}`."
        )
        return lines

    lines.append(f"**Comparing:** `{diff.get('previous_date', '?')}` -> `{diff.get('current_date', '?')}`")
    lines.append("")
    lines.append("## Changes")
    lines.append("| Metric | Previous | Current | Delta | Status |")
    lines.append("|---|---:|---:|---:|---|")

    for change in diff.get("changes", []):
        delta_str = f"+{change['delta']}" if change["delta"] > 0 else str(change["delta"])
        lines.append(
            f"| {change['emoji']} {change['metric']} | {change['old']} | {change['new']} | {delta_str} | {change['status']} |"
        )

    breakdown = diff.get("health_breakdown_diff", {})
    if breakdown:
        lines.extend(["", "## Health Score Breakdown"])
        lines.append("| Component | Previous | Current | Delta |")
        lines.append("|---|---:|---:|---:|")
        for key, value in sorted(breakdown.items()):
            delta_str = f"+{value['delta']}" if value["delta"] > 0 else str(value["delta"])
            marker = "[OK]" if value["delta"] > 0 else "[FAIL]" if value["delta"] < 0 else "[-]"
            lines.append(
                f"| {marker} {key.replace('_', ' ').title()} | {value['old']} | {value['new']} | {delta_str} |"
            )

    return lines


if __name__ == "__main__":
    save_snapshot_and_diff()
