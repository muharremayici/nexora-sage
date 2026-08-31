from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic


DB_PATH = RAW_DIR / "codemaps.db"
REPORT_NAME = "symbol_span_integrity_validation"
MAX_SAMPLES = 25
SOURCE_LINES_RE = re.compile(r"^L(?P<start>\d+)-L(?P<end>\d+)$")


def _log(message: str) -> None:
    print(f"[symbol-span-integrity] {message}", flush=True)


def _check(name: str, passed: bool, details: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _line_count(content: str) -> int:
    return len(content.splitlines())


def validate_symbol_span_integrity() -> dict[str, Any]:
    _log("START validation")
    checks: list[dict[str, Any]] = []
    samples: list[dict[str, Any]] = []
    missing_snapshot_count = 0
    invalid_order_count = 0
    out_of_bounds_count = 0
    source_lines_mismatch_count = 0
    source_lines_missing_count = 0
    total_symbols = 0

    if not DB_PATH.exists():
        payload = {
            "meta": {"kind": REPORT_NAME, "version": "v1"},
            "summary": {
                "status": "FAIL",
                "total_checks": 1,
                "passed_checks": 0,
                "failed_checks": 1,
                "total_symbols": 0,
                "invalid_symbols": 0,
            },
            "checks": [_check("sqlite_database_exists", False, str(DB_PATH))],
            "samples": [],
        }
        save_json_atomic(RAW_DIR / f"{REPORT_NAME}.json", payload)
        save_text_atomic(REPORTS_DIR / f"{REPORT_NAME}.md", render_report(payload))
        print(json.dumps(payload["summary"], ensure_ascii=False))
        return payload

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT
              f.project_key,
              f.rel_path,
              s.name,
              s.type,
              s.line,
              s.end_line,
              s.source_lines,
              ss.content
            FROM symbols AS s
            JOIN files AS f
              ON f.file_id = s.file_id
            LEFT JOIN source_snapshots AS ss
              ON ss.project_key = f.project_key
             AND ss.rel_path = f.rel_path
            WHERE s.line IS NOT NULL
              AND s.line > 0;
            """
        ).fetchall()

    total_symbols = len(rows)
    _log(f"SCAN symbols={total_symbols}")
    for row in rows:
        content = row["content"]
        line = int(row["line"] or 0)
        end_line = int(row["end_line"] or line or 0)
        source_lines = str(row["source_lines"] or "")
        line_count = _line_count(str(content)) if isinstance(content, str) else 0
        issue: str | None = None

        if not isinstance(content, str) or not content:
            missing_snapshot_count += 1
            issue = "missing_source_snapshot"
        elif end_line < line:
            invalid_order_count += 1
            issue = "end_line_before_start_line"
        elif end_line > line_count:
            out_of_bounds_count += 1
            issue = "end_line_out_of_source_bounds"
        else:
            match = SOURCE_LINES_RE.match(source_lines)
            if not match:
                source_lines_missing_count += 1
                issue = "source_lines_missing_or_invalid"
            elif int(match.group("start")) != line or int(match.group("end")) != end_line:
                source_lines_mismatch_count += 1
                issue = "source_lines_span_mismatch"

        if issue and len(samples) < MAX_SAMPLES:
            samples.append(
                {
                    "issue": issue,
                    "project_key": row["project_key"],
                    "rel_path": row["rel_path"],
                    "symbol": row["name"],
                    "type": row["type"],
                    "line": line,
                    "end_line": end_line,
                    "source_lines": source_lines,
                    "source_line_count": line_count,
                }
            )

    invalid_symbols = (
        missing_snapshot_count
        + invalid_order_count
        + out_of_bounds_count
        + source_lines_missing_count
        + source_lines_mismatch_count
    )
    checks.extend(
        [
            _check("sqlite_database_exists", True, str(DB_PATH)),
            _check("symbols_have_source_snapshots", missing_snapshot_count == 0, {"missing_snapshot_count": missing_snapshot_count}),
            _check("symbol_spans_have_valid_order", invalid_order_count == 0, {"invalid_order_count": invalid_order_count}),
            _check("symbol_end_lines_within_source_bounds", out_of_bounds_count == 0, {"out_of_bounds_count": out_of_bounds_count}),
            _check("symbol_source_lines_are_present", source_lines_missing_count == 0, {"source_lines_missing_count": source_lines_missing_count}),
            _check("symbol_source_lines_match_span", source_lines_mismatch_count == 0, {"source_lines_mismatch_count": source_lines_mismatch_count}),
        ]
    )
    status = "PASS" if all(check["passed"] for check in checks) else "FAIL"
    payload = {
        "meta": {"kind": REPORT_NAME, "version": "v1"},
        "summary": {
            "status": status,
            "total_checks": len(checks),
            "passed_checks": sum(1 for check in checks if check["passed"]),
            "failed_checks": sum(1 for check in checks if not check["passed"]),
            "total_symbols": total_symbols,
            "invalid_symbols": invalid_symbols,
            "missing_snapshot_count": missing_snapshot_count,
            "invalid_order_count": invalid_order_count,
            "out_of_bounds_count": out_of_bounds_count,
            "source_lines_missing_count": source_lines_missing_count,
            "source_lines_mismatch_count": source_lines_mismatch_count,
        },
        "checks": checks,
        "samples": samples,
    }
    save_json_atomic(RAW_DIR / f"{REPORT_NAME}.json", payload)
    save_text_atomic(REPORTS_DIR / f"{REPORT_NAME}.md", render_report(payload))
    _log(f"DONE status={status} invalid_symbols={invalid_symbols}")
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return payload


def render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# Symbol Span Integrity Validation",
        "",
        f"- status: `{summary.get('status')}`",
        f"- total_symbols: `{summary.get('total_symbols')}`",
        f"- invalid_symbols: `{summary.get('invalid_symbols')}`",
        f"- out_of_bounds_count: `{summary.get('out_of_bounds_count')}`",
        "",
        "## Checks",
        "",
        "| Check | Passed | Details |",
        "|---|---:|---|",
    ]
    for check in payload.get("checks", []):
        lines.append(f"| `{check.get('name')}` | `{check.get('passed')}` | `{json.dumps(check.get('details'), ensure_ascii=False)}` |")
    lines.extend(["", "## Samples", "", "| Issue | Project | Path | Symbol | Span | Source Lines |", "|---|---|---|---|---|---:|"])
    for sample in payload.get("samples", []):
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{sample.get('issue')}`",
                    f"`{sample.get('project_key')}`",
                    f"`{sample.get('rel_path')}`",
                    f"`{sample.get('symbol')}`",
                    f"`L{sample.get('line')}-L{sample.get('end_line')}`",
                    f"`{sample.get('source_line_count')}`",
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    result = validate_symbol_span_integrity()
    raise SystemExit(0 if result.get("summary", {}).get("status") == "PASS" else 1)
