from __future__ import annotations

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

_COMMENT_RE = re.compile(r"//.*?$|/\*.*?\*/", flags=re.MULTILINE | re.DOTALL)
_SQUOTE_RE = re.compile(r"'(?:\\.|[^'\\])*'")
_DQUOTE_RE = re.compile(r'"(?:\\.|[^"\\])*"')
_TEMPLATE_RE = re.compile(r"`(?:\\.|[^`\\])*`", flags=re.DOTALL)


def _strip_noise(content: str) -> str:
    no_comments = _COMMENT_RE.sub("", content)
    no_squote = _SQUOTE_RE.sub("''", no_comments)
    no_dquote = _DQUOTE_RE.sub('""', no_squote)
    return _TEMPLATE_RE.sub("``", no_dquote)


def _symbol_pattern(symbol: str) -> re.Pattern[str]:
    return re.compile(rf"\b{re.escape(symbol)}\b")


def _is_declaration_line(symbol: str, line: str) -> bool:
    compact = line.strip()
    if not compact:
        return False

    declaration_patterns = (
        rf"\b(?:export\s+)?(?:const|let|var)\s+{re.escape(symbol)}\b",
        rf"\b(?:export\s+)?function\s+{re.escape(symbol)}\b",
        rf"\b(?:export\s+)?class\s+{re.escape(symbol)}\b",
        rf"\b(?:export\s+)?interface\s+{re.escape(symbol)}\b",
        rf"\b(?:export\s+)?type\s+{re.escape(symbol)}\b",
        rf"\b(?:export\s+)?enum\s+{re.escape(symbol)}\b",
        rf"\bimport\b[^;]*\b{re.escape(symbol)}\b",
        rf"\bexport\s*\{{[^}}]*\b{re.escape(symbol)}\b[^}}]*\}}",
    )
    for pattern in declaration_patterns:
        if re.search(pattern, compact):
            return True
    return False


def _strong_usage_signal(symbol: str, line: str) -> bool:
    compact = line.strip()
    if not compact:
        return False
    usage_patterns = (
        rf"\b{re.escape(symbol)}\s*\(",
        rf"<\s*{re.escape(symbol)}\b",
        rf"\.\s*{re.escape(symbol)}\b",
        rf"\[\s*{re.escape(symbol)}\s*\]",
        rf"\bnew\s+{re.escape(symbol)}\b",
        rf"\btypeof\s+{re.escape(symbol)}\b",
    )
    for pattern in usage_patterns:
        if re.search(pattern, compact):
            return True
    return False


def _is_test_file(path: str) -> bool:
    p = to_posix_path(path).lower()
    return (
        "/__tests__/" in p
        or "/tests/" in p
        or "/test/" in p
        or "/e2e/" in p
        or p.endswith(".test.ts")
        or p.endswith(".test.tsx")
        or p.endswith(".test.js")
        or p.endswith(".test.jsx")
        or p.endswith(".spec.ts")
        or p.endswith(".spec.tsx")
        or p.endswith(".spec.js")
        or p.endswith(".spec.jsx")
    )


def _scan_project_symbol_usage(
    *,
    project_root: Path,
    project_files: list[str],
    declaration_file: str,
    symbol: str,
) -> dict[str, Any]:
    declaration_file_norm = to_posix_path(declaration_file)
    needle = _symbol_pattern(symbol)
    external_non_declaration_hits: list[dict[str, Any]] = []
    declaration_only_collisions: list[dict[str, Any]] = []
    declaration_hit_count = 0

    for rel in project_files:
        rel_norm = to_posix_path(rel)
        abs_path = project_root / rel_norm
        if not abs_path.exists():
            continue
        try:
            content = abs_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        cleaned = _strip_noise(content)
        for line_no, line in enumerate(cleaned.splitlines(), start=1):
            if not needle.search(line):
                continue
            if rel_norm == declaration_file_norm:
                declaration_hit_count += 1
                continue

            original_line = content.splitlines()[line_no - 1] if line_no - 1 < len(content.splitlines()) else line
            row = {
                "file": rel_norm,
                "line": line_no,
                "snippet": original_line.strip()[:220],
            }
            if _is_declaration_line(symbol, line):
                declaration_only_collisions.append(row)
                continue

            strong = _strong_usage_signal(symbol, line)
            row["strong_signal"] = strong
            external_non_declaration_hits.append(row)

    strong_signal_count = sum(1 for row in external_non_declaration_hits if row.get("strong_signal"))
    return {
        "declaration_hits": declaration_hit_count,
        "external_non_declaration_count": len(external_non_declaration_hits),
        "external_non_declaration_samples": external_non_declaration_hits[:8],
        "declaration_only_collision_count": len(declaration_only_collisions),
        "declaration_only_collision_samples": declaration_only_collisions[:8],
        "strong_signal_count": strong_signal_count,
    }


def _build_project_files_index(atlas: dict[str, Any]) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    for project_key, payload in atlas.items():
        if not isinstance(payload, dict):
            continue
        files = payload.get("files", {})
        if isinstance(files, dict):
            index[project_key] = [to_posix_path(path) for path in files.keys()]
    return index


def run() -> dict[str, Any]:
    dead_code = load_json_file(RAW_DIR / "dead_code.json", {})
    atlas = load_atlas_data()
    runtime_projects = resolve_runtime_projects(ROOT)
    project_files_index = _build_project_files_index(atlas if isinstance(atlas, dict) else {})

    candidates = dead_code.get("items", []) if isinstance(dead_code, dict) else []
    checked_candidates = 0
    suspicious_with_external_usage: list[dict[str, Any]] = []
    test_only_external_usage: list[dict[str, Any]] = []
    weak_text_collisions: list[dict[str, Any]] = []
    declaration_only_collisions: list[dict[str, Any]] = []
    not_found_project_or_file = 0

    for item in candidates:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "").strip()
        project = str(item.get("project") or "").strip()
        declaration_file = to_posix_path(str(item.get("file") or ""))
        if not symbol or not project or not declaration_file:
            continue

        project_root = runtime_projects.get(project)
        project_files = project_files_index.get(project, [])
        if not project_root or not project_files:
            not_found_project_or_file += 1
            continue

        checked_candidates += 1
        usage = _scan_project_symbol_usage(
            project_root=project_root,
            project_files=project_files,
            declaration_file=declaration_file,
            symbol=symbol,
        )
        if usage["external_non_declaration_count"] > 0:
            entry = {
                "project": project,
                "file": declaration_file,
                "symbol": symbol,
                "reason": str(item.get("reason") or ""),
                "confidence": str(item.get("confidence") or ""),
                "external_non_declaration_count": usage["external_non_declaration_count"],
                "strong_signal_count": usage["strong_signal_count"],
                "samples": usage["external_non_declaration_samples"],
            }
            samples = usage["external_non_declaration_samples"]
            all_test = bool(samples) and all(_is_test_file(str(s.get("file") or "")) for s in samples)
            if usage["strong_signal_count"] == 0:
                weak_text_collisions.append(entry)
            elif all_test:
                test_only_external_usage.append(entry)
            else:
                suspicious_with_external_usage.append(entry)
            continue

        if usage["declaration_only_collision_count"] > 0:
            declaration_only_collisions.append(
                {
                    "project": project,
                    "file": declaration_file,
                    "symbol": symbol,
                    "reason": str(item.get("reason") or ""),
                    "confidence": str(item.get("confidence") or ""),
                    "declaration_only_collision_count": usage["declaration_only_collision_count"],
                    "samples": usage["declaration_only_collision_samples"],
                }
            )

    summary = {
        "checked_candidates": checked_candidates,
        "suspicious_with_external_usage": len(suspicious_with_external_usage),
        "test_only_external_usage": len(test_only_external_usage),
        "weak_text_collisions": len(weak_text_collisions),
        "declaration_only_collisions": len(declaration_only_collisions),
        "not_found_project_or_file": not_found_project_or_file,
    }

    payload = {
        "meta": {
            "kind": "dead_code_groundtruth_validation",
            "version": "v2",
            "source": str(RAW_DIR / "dead_code.json"),
        },
        "summary": summary,
        "suspicious_with_external_usage": suspicious_with_external_usage,
        "test_only_external_usage": test_only_external_usage,
        "weak_text_collisions": weak_text_collisions,
        "declaration_only_collisions": declaration_only_collisions,
    }

    save_json_atomic(RAW_DIR / "dead_code_groundtruth.json", payload)

    lines: list[str] = []
    lines.append("# Dead Code Groundtruth Report")
    lines.append("")
    lines.append(f"- Checked candidates: `{summary['checked_candidates']}`")
    lines.append(f"- Suspicious with external usage: `{summary['suspicious_with_external_usage']}`")
    lines.append(f"- Test-only external usage: `{summary['test_only_external_usage']}`")
    lines.append(f"- Weak text collisions: `{summary['weak_text_collisions']}`")
    lines.append(f"- Declaration-only collisions: `{summary['declaration_only_collisions']}`")
    lines.append(f"- Missing project/file context: `{summary['not_found_project_or_file']}`")
    lines.append("")
    lines.append("## Suspicious With External Usage")
    lines.append("")
    lines.append("| Project | File | Symbol | Reason | External Hits | Strong Signals |")
    lines.append("|---|---|---|---|---:|---:|")
    for row in suspicious_with_external_usage[:120]:
        lines.append(
            f"| `{project_display_name(str(row['project']))}` | `{row['file']}` | `{row['symbol']}` | `{row['reason']}` | {row['external_non_declaration_count']} | {row['strong_signal_count']} |"
        )
    if not suspicious_with_external_usage:
        lines.append("| `-` | `-` | `-` | `-` | 0 | 0 |")
    lines.append("")
    lines.append("## Test-Only External Usage")
    lines.append("")
    lines.append("| Project | File | Symbol | Reason | External Hits | Strong Signals |")
    lines.append("|---|---|---|---|---:|---:|")
    for row in test_only_external_usage[:120]:
        lines.append(
            f"| `{project_display_name(str(row['project']))}` | `{row['file']}` | `{row['symbol']}` | `{row['reason']}` | {row['external_non_declaration_count']} | {row['strong_signal_count']} |"
        )
    if not test_only_external_usage:
        lines.append("| `-` | `-` | `-` | `-` | 0 | 0 |")
    lines.append("")
    lines.append("## Weak Text Collisions")
    lines.append("")
    lines.append("| Project | File | Symbol | Reason | External Hits | Strong Signals |")
    lines.append("|---|---|---|---|---:|---:|")
    for row in weak_text_collisions[:120]:
        lines.append(
            f"| `{project_display_name(str(row['project']))}` | `{row['file']}` | `{row['symbol']}` | `{row['reason']}` | {row['external_non_declaration_count']} | {row['strong_signal_count']} |"
        )
    if not weak_text_collisions:
        lines.append("| `-` | `-` | `-` | `-` | 0 | 0 |")
    lines.append("")
    lines.append("## Declaration-Only Collisions")
    lines.append("")
    lines.append("| Project | File | Symbol | Reason | Collision Lines |")
    lines.append("|---|---|---|---|---:|")
    for row in declaration_only_collisions[:120]:
        lines.append(
            f"| `{project_display_name(str(row['project']))}` | `{row['file']}` | `{row['symbol']}` | `{row['reason']}` | {row['declaration_only_collision_count']} |"
        )
    if not declaration_only_collisions:
        lines.append("| `-` | `-` | `-` | `-` | 0 |")
    lines.append("")

    save_text_atomic(REPORTS_DIR / "dead_code_groundtruth_report.md", "\n".join(lines))
    return payload


def main() -> int:
    payload = run()
    print(json.dumps(payload.get("summary", {}), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
