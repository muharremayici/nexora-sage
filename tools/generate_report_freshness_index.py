from __future__ import annotations

import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.core.config import CODE_MAPS_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.artifact_store import STORE


RAW_SUFFIXES = (".json", ".txt")
REPORT_SUFFIXES = (".md", ".txt")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rel(path: Path) -> str:
    try:
        return path.relative_to(CODE_MAPS_DIR).as_posix()
    except ValueError:
        return path.as_posix()


def _sha256_file(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iso_mtime(path: Path) -> str | None:
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()


def _candidate_raw_paths(report: Path) -> list[Path]:
    stem = report.stem
    candidates = [RAW_DIR / f"{stem}{suffix}" for suffix in RAW_SUFFIXES]
    if stem.endswith("_report"):
        candidates.extend(RAW_DIR / f"{stem[:-7]}{suffix}" for suffix in RAW_SUFFIXES)
    return candidates


def _matching_raw(report: Path) -> Path | None:
    for candidate in _candidate_raw_paths(report):
        if candidate.exists():
            return candidate
    return None


def _freshness_status(report: Path, raw: Path | None, raw_source_mtime: float | None = None) -> str:
    if raw is None:
        return "no_matching_raw_artifact"
    authoritative_raw_mtime = raw_source_mtime if raw_source_mtime is not None else raw.stat().st_mtime
    delta = report.stat().st_mtime - authoritative_raw_mtime
    if delta >= -1.0:
        return "fresh_or_report_newer"
    return "raw_newer_than_report"


def _report_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for report in sorted(REPORTS_DIR.rglob("*")):
        if not report.is_file() or report.suffix.lower() not in REPORT_SUFFIXES:
            continue
        raw = _matching_raw(report)
        raw_metadata = STORE.raw_metadata(raw.stem) if raw and raw.suffix.lower() == ".json" else {}
        raw_source_mtime = (
            float(raw_metadata.get("source_mtime", 0.0) or 0.0)
            if raw_metadata.get("truth_source") == "sqlite_state_payloads"
            else None
        )
        rows.append(
            {
                "report": _rel(report),
                "report_sha256": _sha256_file(report),
                "report_modified_at": _iso_mtime(report),
                "raw_artifact": _rel(raw) if raw else None,
                "raw_sha256": _sha256_file(raw) if raw else None,
                "canonical_payload_sha": raw_metadata.get("payload_sha") or None,
                "raw_modified_at": _iso_mtime(raw) if raw else None,
                "raw_truth_source": raw_metadata.get("truth_source") if raw_metadata else "filesystem",
                "raw_source_modified_at": (
                    datetime.fromtimestamp(raw_source_mtime, timezone.utc).isoformat()
                    if raw_source_mtime is not None
                    else _iso_mtime(raw) if raw else None
                ),
                "status": _freshness_status(report, raw, raw_source_mtime),
            }
        )
    return rows


def build_report_freshness_index() -> dict[str, Any]:
    rows = _report_rows()
    by_status: dict[str, int] = {}
    for row in rows:
        status = str(row["status"])
        by_status[status] = by_status.get(status, 0) + 1
    by_truth_source: dict[str, int] = {}
    for row in rows:
        truth_source = str(row.get("raw_truth_source") or "unknown")
        by_truth_source[truth_source] = by_truth_source.get(truth_source, 0) + 1
    stale = [row["report"] for row in rows if row["status"] == "raw_newer_than_report"]
    metadata_fallbacks = [
        row["report"]
        for row in rows
        if STORE.use_sqlite and row.get("raw_truth_source") == "shadow_json_fallback"
    ]
    return {
        "meta": {
            "kind": "report_freshness_index",
            "version": "v2",
            "generated_at": _utc_now(),
            "generator": "tools.generate_report_freshness_index",
            "workspace_root": str(CODE_MAPS_DIR),
        },
        "summary": {
            "reports": len(rows),
            "statuses": by_status,
            "truth_sources": by_truth_source,
            "stale_reports": len(stale),
            "metadata_fallbacks": len(metadata_fallbacks),
            "freshness_gate": "PASS" if not stale and not metadata_fallbacks else "ATTENTION",
        },
        "stale_reports": stale,
        "metadata_fallback_reports": metadata_fallbacks,
        "reports": rows,
    }


def render_report(index: dict[str, Any]) -> str:
    meta = index.get("meta", {})
    summary = index.get("summary", {})
    lines = [
        "# Report Freshness Index",
        "",
        f"- generated_at: `{meta.get('generated_at')}`",
        f"- freshness_gate: `{summary.get('freshness_gate')}`",
        f"- reports: `{summary.get('reports')}`",
        f"- stale_reports: `{summary.get('stale_reports')}`",
        f"- metadata_fallbacks: `{summary.get('metadata_fallbacks')}`",
        f"- statuses: `{summary.get('statuses')}`",
        f"- truth_sources: `{summary.get('truth_sources')}`",
        "",
        "## Reports Needing Attention",
        "",
    ]
    stale = index.get("stale_reports") or []
    if stale:
        for path in stale:
            lines.append(f"- `{path}`")
    else:
        lines.append("- None.")
    lines.extend(["", "## Metadata Fallbacks", ""])
    metadata_fallbacks = index.get("metadata_fallback_reports") or []
    if metadata_fallbacks:
        for path in metadata_fallbacks:
            lines.append(f"- `{path}`")
    else:
        lines.append("- None.")
    lines.extend(["", "## Report Map", "", "| Report | Status | Raw Artifact | Report SHA256 | Raw SHA256 |", "|---|---|---|---|---|"])
    for row in index.get("reports", []):
        report_hash = row.get("report_sha256")
        raw_hash = row.get("raw_sha256")
        lines.append(
            f"| `{row.get('report')}` | `{row.get('status')}` | `{row.get('raw_artifact')}` | `{str(report_hash)[:16] + '...' if report_hash else ''}` | `{str(raw_hash)[:16] + '...' if raw_hash else ''}` |"
        )
    return "\n".join(lines) + "\n"


def run() -> dict[str, Any]:
    index = build_report_freshness_index()
    save_json_atomic(RAW_DIR / "report_freshness_index.json", index)
    save_text_atomic(REPORTS_DIR / "report_freshness_index.md", render_report(index))
    return index


def main() -> int:
    index = run()
    print(index["summary"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
