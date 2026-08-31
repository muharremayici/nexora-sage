#!/usr/bin/env python3
"""Validate line-range consistency in agent-facing source snippets."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file


RAW_PATH = RAW_DIR / "agent_surface_quality_review.json"
VALIDATION_RAW = RAW_DIR / "agent_surface_snippet_range_validation.json"
VALIDATION_REPORT = REPORTS_DIR / "agent_surface_snippet_range_validation.md"
LINE_RANGE_RE = re.compile(r"L(\d+)-L(\d+)")
CODE_LINE_RE = re.compile(r"^\s*(\d+):")
OMITTED_RE = re.compile(r"\.\.\.\s+(\d+)\s+omitted lines inside large symbol")


def _range_tuple(value: str) -> tuple[int, int] | None:
    match = LINE_RANGE_RE.search(str(value or ""))
    if not match:
        return None
    start = int(match.group(1))
    end = int(match.group(2))
    if start <= 0 or end < start:
        return None
    return start, end


def _field(line: str, name: str) -> str:
    prefix = f"{name}:"
    stripped = line.strip()
    if not stripped.startswith(prefix):
        return ""
    return stripped[len(prefix) :].strip().strip('"')


def _snippet_blocks(body: str) -> list[list[str]]:
    lines = str(body or "").splitlines()
    blocks: list[list[str]] = []
    in_target_snippets = False
    base_indent = 0
    item_indent: int | None = None
    current: list[str] = []
    for line in lines:
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if stripped == "target_source_snippets:":
            in_target_snippets = True
            base_indent = indent
            item_indent = None
            current = []
            continue
        if not in_target_snippets:
            continue
        if stripped and indent <= base_indent:
            if current:
                blocks.append(current)
                current = []
            in_target_snippets = False
            item_indent = None
            continue
        if stripped == "[]":
            continue
        is_item = stripped == "-" or stripped.startswith("- ")
        if is_item and item_indent is None:
            item_indent = indent
        if is_item and indent == item_indent and current:
            blocks.append(current)
            current = [line]
        elif is_item and indent == item_indent:
            current = [line]
        elif current:
            current.append(line)
    if in_target_snippets and current:
        blocks.append(current)
    return blocks


def _declared_target_span_ranges(body: str) -> set[tuple[int, int]]:
    lines = str(body or "").splitlines()
    ranges: set[tuple[int, int]] = set()
    in_spans = False
    base_indent = 0
    for line in lines:
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if stripped == "target_spans:":
            in_spans = True
            base_indent = indent
            continue
        if not in_spans:
            continue
        if stripped and indent <= base_indent:
            in_spans = False
            continue
        if stripped.startswith("source_lines:"):
            parsed = _range_tuple(_field(line, "source_lines"))
            if parsed:
                ranges.add(parsed)
    return ranges


def _shown_ranges(block: list[str]) -> list[tuple[int, int]]:
    numbers = [int(match.group(1)) for line in block for match in [CODE_LINE_RE.match(line)] if match]
    if not numbers:
        return []
    ranges: list[tuple[int, int]] = []
    start = prev = numbers[0]
    for number in numbers[1:]:
        if number == prev + 1:
            prev = number
            continue
        ranges.append((start, prev))
        start = prev = number
    ranges.append((start, prev))
    return ranges


def _int_field(block: list[str], name: str) -> int | None:
    for line in block:
        value = _field(line, name)
        if not value:
            continue
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _str_field(block: list[str], name: str) -> str:
    for line in block:
        value = _field(line, name)
        if value:
            return value
    return ""


def _validate_block(sample_name: str, block: list[str]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    status = _str_field(block, "snippet_status")
    source_range = _range_tuple(_str_field(block, "source_lines"))
    omitted_range = _range_tuple(_str_field(block, "omitted_range"))
    next_chunk_range = _range_tuple(_str_field(block, "next_chunk_lines"))
    line_count = _int_field(block, "line_count")
    shown_lines = _int_field(block, "shown_lines")
    omitted_lines = _int_field(block, "omitted_lines")
    shown_ranges = _shown_ranges(block)
    shown_count = sum(end - start + 1 for start, end in shown_ranges)

    if source_range and not shown_ranges:
        findings.append(
            {
                "sample": sample_name,
                "rule": "source_lines_requires_numbered_code_lines",
                "source_lines": _str_field(block, "source_lines"),
                "snippet_status": status,
            }
        )
    if source_range and shown_ranges:
        for shown_start, shown_end in shown_ranges:
            if shown_start < source_range[0] or shown_end > source_range[1]:
                findings.append(
                    {
                        "sample": sample_name,
                        "rule": "shown_code_lines_must_stay_inside_source_lines",
                        "source_lines": _str_field(block, "source_lines"),
                        "shown_range": (shown_start, shown_end),
                        "snippet_status": status,
                    }
                )
    if source_range and shown_count and "partial" not in status:
        expected = source_range[1] - source_range[0] + 1
        if shown_count != expected:
            findings.append(
                {
                    "sample": sample_name,
                    "rule": "complete_snippet_lines_match_source_range",
                    "source_lines": _str_field(block, "source_lines"),
                    "shown_code_lines": shown_count,
                    "expected": expected,
                    "snippet_status": status,
                    "shown_ranges": shown_ranges,
                }
            )
    if "partial" not in status:
        return findings

    if source_range and line_count is not None:
        expected = source_range[1] - source_range[0] + 1
        if line_count != expected:
            findings.append(
                {
                    "sample": sample_name,
                    "rule": "line_count_matches_source_lines",
                    "source_lines": _str_field(block, "source_lines"),
                    "line_count": line_count,
                    "expected": expected,
                }
            )
    if shown_lines is not None and shown_count and shown_lines != shown_count:
        findings.append(
            {
                "sample": sample_name,
                "rule": "shown_lines_matches_code_lines",
                "shown_lines": shown_lines,
                "actual_code_lines": shown_count,
                "shown_ranges": shown_ranges,
            }
        )
    if omitted_lines is not None and line_count is not None and shown_count:
        expected_omitted = line_count - shown_count
        if omitted_lines != expected_omitted:
            findings.append(
                {
                    "sample": sample_name,
                    "rule": "omitted_lines_matches_line_count_minus_shown",
                    "omitted_lines": omitted_lines,
                    "expected": expected_omitted,
                    "shown_ranges": shown_ranges,
                }
            )
    if omitted_range:
        for shown_start, shown_end in shown_ranges:
            overlap = not (omitted_range[1] < shown_start or omitted_range[0] > shown_end)
            if overlap:
                findings.append(
                    {
                        "sample": sample_name,
                        "rule": "omitted_range_must_not_overlap_shown_code",
                        "omitted_range": omitted_range,
                        "shown_range": (shown_start, shown_end),
                    }
                )
    if omitted_range and next_chunk_range:
        if next_chunk_range[0] < omitted_range[0] or next_chunk_range[1] > omitted_range[1]:
            findings.append(
                {
                    "sample": sample_name,
                    "rule": "next_chunk_must_be_inside_omitted_range",
                    "next_chunk_lines": next_chunk_range,
                    "omitted_range": omitted_range,
                }
            )
    omitted_marker = next((OMITTED_RE.search(line) for line in block if OMITTED_RE.search(line)), None)
    if omitted_marker and omitted_lines is not None and int(omitted_marker.group(1)) != omitted_lines:
        findings.append(
            {
                "sample": sample_name,
                "rule": "inline_omitted_marker_matches_omitted_lines",
                "marker": int(omitted_marker.group(1)),
                "omitted_lines": omitted_lines,
            }
        )
    return findings


def run_validation() -> dict[str, Any]:
    payload = load_json_file(RAW_PATH, {})
    samples = payload.get("samples", []) if isinstance(payload, dict) else []
    findings: list[dict[str, Any]] = []
    checked_blocks = 0
    partial_blocks = 0
    complete_blocks = 0
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        sample_name = str(sample.get("name") or "")
        body = str(sample.get("body") or "")
        declared_span_ranges = _declared_target_span_ranges(body)
        for block in _snippet_blocks(body):
            checked_blocks += 1
            status = _str_field(block, "snippet_status")
            if "partial" in status:
                partial_blocks += 1
            else:
                complete_blocks += 1
            findings.extend(_validate_block(sample_name, block))
            snippet_range = _range_tuple(_str_field(block, "source_lines"))
            if (
                declared_span_ranges
                and snippet_range
                and status != "included_no_symbol_span"
                and snippet_range not in declared_span_ranges
            ):
                findings.append(
                    {
                        "sample": sample_name,
                        "rule": "snippet_source_range_must_match_declared_target_span",
                        "source_lines": _str_field(block, "source_lines"),
                        "declared_target_span_ranges": sorted(declared_span_ranges),
                        "snippet_status": status,
                    }
                )
    result = {
        "meta": {"kind": "agent_surface_snippet_range_validation", "version": "1.0.0"},
        "summary": {
            "status": "PASS" if not findings else "FAIL",
            "samples": len(samples),
            "snippet_blocks_checked": checked_blocks,
            "complete_snippet_blocks_checked": complete_blocks,
            "partial_snippet_blocks_checked": partial_blocks,
            "findings": len(findings),
        },
        "findings": findings,
    }
    save_json_atomic(VALIDATION_RAW, result)
    save_text_atomic(VALIDATION_REPORT, _render_report(result))
    return result


def _render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# Agent Surface Snippet Range Validation",
        "",
        f"- status: `{summary.get('status')}`",
        f"- samples: `{summary.get('samples')}`",
        f"- snippet_blocks_checked: `{summary.get('snippet_blocks_checked')}`",
        f"- complete_snippet_blocks_checked: `{summary.get('complete_snippet_blocks_checked')}`",
        f"- partial_snippet_blocks_checked: `{summary.get('partial_snippet_blocks_checked')}`",
        f"- findings: `{summary.get('findings')}`",
        "",
    ]
    findings = payload.get("findings", [])
    if not findings:
        lines.append("No snippet range inconsistencies found.")
    else:
        lines.append("| Sample | Rule | Details |")
        lines.append("|---|---|---|")
        for finding in findings:
            details = json.dumps(finding, ensure_ascii=False, sort_keys=True)[:800]
            lines.append(f"| `{finding.get('sample')}` | `{finding.get('rule')}` | `{details}` |")
    return "\n".join(lines) + "\n"


def main() -> int:
    payload = run_validation()
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
