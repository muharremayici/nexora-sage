from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
CODE_MAPS_DIR = TOOLS_DIR.parent
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))

from tools.core.config import RAW_DIR, REPORTS_DIR, ROOT, save_json_atomic, save_text_atomic
from tools.core.atlas_io import load_atlas_data
from tools.core.json_io import load_json_file
from tools.core.path_engine import to_posix_path
from tools.core.projects_registry import project_display_name, resolve_runtime_projects


def _select_stratified(items: list[dict[str, Any]], sample_size: int) -> list[dict[str, Any]]:
    if sample_size <= 0:
        return []
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in items:
        project = str(item.get("project") or "")
        reason = str(item.get("reason") or "")
        buckets.setdefault((project, reason), []).append(item)
    for key in buckets:
        buckets[key] = sorted(
            buckets[key],
            key=lambda item: (
                to_posix_path(str(item.get("file") or "")),
                str(item.get("symbol") or ""),
            ),
        )

    selected: list[dict[str, Any]] = []
    bucket_keys = sorted(buckets.keys(), key=lambda k: (k[0], k[1]))
    while len(selected) < sample_size:
        progressed = False
        for key in bucket_keys:
            bucket = buckets.get(key, [])
            if not bucket:
                continue
            selected.append(bucket.pop(0))
            progressed = True
            if len(selected) >= sample_size:
                break
        if not progressed:
            break
    return selected


def _symbol_occurrences(project_root: Path, project_files: list[str], symbol: str, declaration_file: str) -> dict[str, Any]:
    needle = re.compile(rf"\b{re.escape(symbol)}\b")
    hits: list[dict[str, Any]] = []
    for rel in project_files:
        rel_norm = to_posix_path(rel)
        abs_path = project_root / rel_norm
        try:
            text = abs_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            if needle.search(line):
                hits.append(
                    {
                        "file": rel_norm,
                        "line": line_no,
                        "snippet": line.strip()[:240],
                        "is_declaration_file": rel_norm == declaration_file,
                    }
                )
    return {
        "total_hits": len(hits),
        "declaration_hits": sum(1 for h in hits if h["is_declaration_file"]),
        "external_hits": [h for h in hits if not h["is_declaration_file"]],
    }


def _is_strong_usage_signal(symbol: str, snippet: str) -> bool:
    text = snippet.strip()
    if not text:
        return False
    if re.search(rf"\b{re.escape(symbol)}\s*\(", text):
        return True
    if re.search(rf"<\s*{re.escape(symbol)}\b", text):
        return True
    if re.search(rf"\bnew\s+{re.escape(symbol)}\b", text):
        return True
    if re.search(rf"\b{re.escape(symbol)}\s*=", text) and "export" not in text:
        return True
    if re.search(rf"\breturn\b.*\b{re.escape(symbol)}\b", text):
        return True
    return False


def _is_import_only_signal(symbol: str, snippet: str) -> bool:
    text = snippet.strip()
    if not text:
        return False
    if re.search(rf"^\s*import\b.*\b{re.escape(symbol)}\b", text):
        return True
    if re.search(rf"^\s*export\b.*\b{re.escape(symbol)}\b.*\bfrom\b", text):
        return True
    return False


def _is_declaration_only_signal(symbol: str, snippet: str) -> bool:
    text = snippet.strip()
    if not text:
        return False
    if re.search(rf"^\s*export\s+(?:const|let|var|function|class|type|interface|enum)\s+{re.escape(symbol)}\b", text):
        return True
    if re.search(rf"^\s*(?:const|let|var|function|class|type|interface|enum)\s+{re.escape(symbol)}\b", text):
        return True
    return False


def _is_comment_only_signal(snippet: str) -> bool:
    text = snippet.strip()
    return text.startswith("//") or text.startswith("/*") or text.startswith("*")


def _is_string_literal_only_signal(symbol: str, snippet: str) -> bool:
    text = snippet.strip()
    if not text:
        return False
    # If symbol appears only inside string literals in this line, treat as neutral evidence.
    stripped = re.sub(r"'[^']*'|\"[^\"]*\"|`[^`]*`", "", text)
    return (symbol in text) and (re.search(rf"\b{re.escape(symbol)}\b", stripped) is None)


def _classify_candidate(symbol: str, external_hits: list[dict[str, Any]]) -> tuple[str, int, int]:
    strong = 0
    import_only = 0
    declaration_only = 0
    other_non_strong = 0
    effective_hits = 0
    for hit in external_hits:
        snippet = str(hit.get("snippet") or "")
        if _is_comment_only_signal(snippet) or _is_string_literal_only_signal(symbol, snippet):
            continue
        effective_hits += 1
        if _is_strong_usage_signal(symbol, snippet):
            strong += 1
        elif _is_import_only_signal(symbol, snippet):
            import_only += 1
        elif _is_declaration_only_signal(symbol, snippet):
            declaration_only += 1
        else:
            other_non_strong += 1
    if strong >= 1:
        return "likely_false_positive", strong, import_only
    if effective_hits and (import_only + declaration_only) == effective_hits:
        return "likely_true_positive", strong, import_only
    if effective_hits and import_only == effective_hits:
        return "needs_manual_review_import_only", strong, import_only
    if effective_hits == 0:
        return "likely_true_positive", strong, import_only
    if other_non_strong > 0:
        return "needs_manual_review_mixed", strong, import_only
    return "likely_true_positive", strong, import_only


def run(sample_size: int = 20) -> dict[str, Any]:
    dead_code = load_json_file(RAW_DIR / "dead_code.json", {})
    atlas = load_atlas_data()
    projects = resolve_runtime_projects(ROOT)

    atlas_files_by_project: dict[str, list[str]] = {}
    if isinstance(atlas, dict):
        for project, payload in atlas.items():
            if not isinstance(payload, dict):
                continue
            files = payload.get("files", {})
            if isinstance(files, dict):
                atlas_files_by_project[project] = [to_posix_path(k) for k in files.keys()]

    items = dead_code.get("items", []) if isinstance(dead_code, dict) else []
    medium_items = [item for item in items if isinstance(item, dict) and str(item.get("confidence") or "").upper() == "MEDIUM"]
    medium_items = sorted(
        medium_items,
        key=lambda item: (
            str(item.get("project") or ""),
            to_posix_path(str(item.get("file") or "")),
            str(item.get("symbol") or ""),
        ),
    )
    sampled = _select_stratified(medium_items, max(1, int(sample_size)))

    rows: list[dict[str, Any]] = []
    summary = {
        "medium_total": len(medium_items),
        "sampled": len(sampled),
        "likely_true_positive": 0,
        "likely_false_positive": 0,
        "needs_manual_review_import_only": 0,
        "needs_manual_review_mixed": 0,
    }

    for item in sampled:
        project = str(item.get("project") or "")
        symbol = str(item.get("symbol") or "")
        declaration_file = to_posix_path(str(item.get("file") or ""))
        project_root = projects.get(project)
        project_files = atlas_files_by_project.get(project, [])

        if project_root is None or not project_files:
            verdict = "needs_manual_review_mixed"
            strong = 0
            import_only = 0
            evidence = {"total_hits": 0, "declaration_hits": 0, "external_hits": []}
        else:
            evidence = _symbol_occurrences(project_root, project_files, symbol, declaration_file)
            verdict, strong, import_only = _classify_candidate(symbol, list(evidence.get("external_hits", [])))

        summary[verdict] += 1
        rows.append(
            {
                "project": project,
                "project_display_name": project_display_name(project),
                "file": declaration_file,
                "symbol": symbol,
                "reason": str(item.get("reason") or ""),
                "verdict": verdict,
                "strong_usage_signals": strong,
                "import_only_signals": import_only,
                "external_hits": int(len(evidence.get("external_hits", []))),
                "declaration_hits": int(evidence.get("declaration_hits", 0) or 0),
                "external_samples": list(evidence.get("external_hits", []))[:6],
            }
        )

    payload = {
        "meta": {"kind": "dead_code_medium_precision_pack", "version": "v1"},
        "summary": summary,
        "items": rows,
    }

    save_json_atomic(RAW_DIR / "dead_code_medium_precision_pack.json", payload)

    lines = [
        "# Dead Code MEDIUM Precision Pack",
        "",
        f"- Medium total in report: `{summary['medium_total']}`",
        f"- Sampled for manual-style audit: `{summary['sampled']}`",
        f"- likely_true_positive: `{summary['likely_true_positive']}`",
        f"- likely_false_positive: `{summary['likely_false_positive']}`",
        f"- needs_manual_review_import_only: `{summary['needs_manual_review_import_only']}`",
        f"- needs_manual_review_mixed: `{summary['needs_manual_review_mixed']}`",
        "",
        "## Sample Table",
        "",
        "| # | Project | Symbol | File | Verdict | Ext Hits | Strong | Import-only |",
        "|---:|---|---|---|---|---:|---:|---:|",
    ]
    for idx, row in enumerate(rows, start=1):
        lines.append(
            f"| {idx} | `{row['project']}` | `{row['symbol']}` | `{row['file']}` | `{row['verdict']}` | {row['external_hits']} | {row['strong_usage_signals']} | {row['import_only_signals']} |"
        )

    lines.append("")
    lines.append("## Evidence Samples")
    for row in rows:
        lines.append("")
        lines.append(f"### {row['project_display_name']} :: {row['symbol']}")
        lines.append(f"- File: `{row['file']}`")
        lines.append(f"- Verdict: `{row['verdict']}`")
        lines.append(f"- Reason: `{row['reason']}`")
        for sample in row["external_samples"][:3]:
            lines.append(f"- `{sample['file']}:{sample['line']}` -> `{sample['snippet']}`")

    save_text_atomic(REPORTS_DIR / "dead_code_medium_precision_pack.md", "\n".join(lines) + "\n")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a manual-style precision pack for dead_code MEDIUM candidates.")
    parser.add_argument("--sample-size", type=int, default=20, help="Number of MEDIUM candidates to audit (default: 20)")
    args = parser.parse_args()
    payload = run(sample_size=args.sample_size)
    print(json.dumps(payload.get("summary", {}), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
