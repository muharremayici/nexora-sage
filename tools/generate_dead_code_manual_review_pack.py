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

from tools.core.atlas_io import load_atlas_data
from tools.core.json_io import load_json_file
from tools.core.config import CONFIG_DIR, RAW_DIR, REPORTS_DIR, ROOT, save_json_atomic, save_text_atomic
from tools.core.dead_code_allowlist_policy import get_allowlist_scope, scope_allows_global, scope_allows_project
from tools.core.path_engine import to_posix_path
from tools.core.projects_registry import project_display_name, resolve_runtime_projects
def _load_enabled_allowlist_rules() -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    scope = get_allowlist_scope(CONFIG_DIR)

    payloads: list[tuple[str, Path, dict[str, Any], str | None]] = []
    if scope_allows_global(scope):
        global_path = CONFIG_DIR / "golden" / "dead_code_intent_allowlist.json"
        payloads.append(("global", global_path, load_json_file(global_path, {}), None))

    if scope_allows_project(scope):
        projects_dir = CONFIG_DIR / "golden" / "projects"
        if projects_dir.exists():
            for project_dir in sorted(projects_dir.iterdir(), key=lambda p: p.name):
                if not project_dir.is_dir():
                    continue
                path = project_dir / "dead_code_intent_allowlist.json"
                payloads.append((f"project:{project_dir.name}", path, load_json_file(path, {}), project_dir.name))

    for rule_scope, path, payload, fallback_project in payloads:
        for rule in payload.get("rules", []) if isinstance(payload, dict) else []:
            if not isinstance(rule, dict):
                continue
            if not rule.get("enabled", False):
                continue
            rule_id = str(rule.get("id") or "")
            match = rule.get("match", {}) if isinstance(rule.get("match"), dict) else {}
            rules.append(
                {
                    "scope": rule_scope,
                    "project": str(match.get("project") or fallback_project or ""),
                    "file": to_posix_path(str(match.get("file") or "")),
                    "reason": str(match.get("reason") or ""),
                    "confidence": str(match.get("confidence") or ""),
                    "id": rule_id,
                    "label": str(rule.get("label") or ""),
                    "allowlist_path": str(path),
                }
            )
    return rules


def _symbol_occurrences(project_root: Path, project_files: list[str], symbol: str, declaration_file: str) -> dict[str, Any]:
    needle = re.compile(rf"\b{re.escape(symbol)}\b")
    external_hits: list[dict[str, Any]] = []
    declaration_hits = 0
    for rel in project_files:
        rel_norm = to_posix_path(rel)
        abs_path = project_root / rel_norm
        try:
            text = abs_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            if not needle.search(line):
                continue
            entry = {
                "file": rel_norm,
                "line": line_no,
                "snippet": line.strip()[:220],
            }
            if rel_norm == declaration_file:
                declaration_hits += 1
            else:
                external_hits.append(entry)
    return {
        "declaration_hits": declaration_hits,
        "external_hit_count": len(external_hits),
        "external_samples": external_hits[:8],
    }


def _path_contract_like(path: str) -> bool:
    p = to_posix_path(path).lower()
    return any(token in p for token in ("/prompts/", "/types/", "/schemas/", "/constants/"))


def _strong_usage_signal_count(symbol: str, samples: list[dict[str, Any]]) -> int:
    count = 0
    needle_call = re.compile(rf"\b{re.escape(symbol)}\s*\(")
    needle_import = re.compile(rf"\bimport\b.*\b{re.escape(symbol)}\b")
    for sample in samples:
        text = str(sample.get("snippet") or "")
        if needle_call.search(text) or needle_import.search(text):
            count += 1
            continue
        if f"{symbol}," in text or f"{symbol} }}" in text:
            count += 1
    return count


def run() -> dict[str, Any]:
    dead_code = load_json_file(RAW_DIR / "dead_code.json", {})
    atlas = load_atlas_data()
    projects = resolve_runtime_projects(ROOT)
    enabled_rules = _load_enabled_allowlist_rules()
    exclusions = dead_code.get("intent_allowlist_exclusions", []) if isinstance(dead_code, dict) else []

    atlas_files_by_project: dict[str, list[str]] = {}
    if isinstance(atlas, dict):
        for project, payload in atlas.items():
            if not isinstance(payload, dict):
                continue
            files = payload.get("files", {})
            if isinstance(files, dict):
                atlas_files_by_project[project] = [to_posix_path(k) for k in files.keys()]

    rule_rows = []
    totals = {"rules": 0, "symbols": 0, "needs_review": 0, "safe": 0}
    for rule in enabled_rules:
        project = str(rule.get("project") or "")
        rule_id = rule["id"]
        file_path = to_posix_path(rule["file"])
        candidates = [
            item
            for item in exclusions
            if isinstance(item, dict)
            and isinstance(item.get("allowlist"), dict)
            and str((item.get("allowlist") or {}).get("rule_id") or "") == rule_id
            and (not project or str(item.get("project") or "") == project)
        ]
        file_matched_candidates = [
            item
            for item in candidates
            if (not file_path) or to_posix_path(str(item.get("file") or "")) == file_path
        ]
        symbol_rows = []
        sample_project = project
        if not sample_project and file_matched_candidates:
            sample_project = str(file_matched_candidates[0].get("project") or "")
        if not sample_project and candidates:
            sample_project = str(candidates[0].get("project") or "")
        project_root = projects.get(sample_project)
        project_files = atlas_files_by_project.get(sample_project, [])
        declaration_fallback = file_path
        for item in file_matched_candidates[:8]:
            symbol = str(item.get("symbol") or "")
            declaration_file = to_posix_path(str(item.get("file") or declaration_fallback))
            occ = (
                _symbol_occurrences(project_root, project_files, symbol, declaration_file)
                if project_root and project_files and symbol
                else {"declaration_hits": 0, "external_hit_count": 0, "external_samples": []}
            )
            ext = int(occ.get("external_hit_count", 0) or 0)
            strong_signals = _strong_usage_signal_count(symbol, occ.get("external_samples", []))
            if ext == 0:
                verdict = "likely_false_positive_allowlist_safe"
            elif strong_signals >= 1:
                verdict = "strong_false_positive_signal"
            elif ext <= 2 and _path_contract_like(file_path):
                verdict = "probably_safe_contract_surface"
            else:
                verdict = "needs_manual_review"
            symbol_rows.append(
                {
                    "symbol": symbol,
                    "declaration_file": declaration_file,
                    "reason": str(item.get("reason") or ""),
                    "external_hit_count": ext,
                    "strong_usage_signals": strong_signals,
                    "declaration_hits": int(occ.get("declaration_hits", 0) or 0),
                    "verdict": verdict,
                    "external_samples": occ.get("external_samples", []),
                }
            )

        rule_verdict = "safe"
        if any(row["verdict"] == "needs_manual_review" for row in symbol_rows):
            rule_verdict = "needs_manual_review"
        totals["rules"] += 1
        totals["symbols"] += len(symbol_rows)
        if rule_verdict == "needs_manual_review":
            totals["needs_review"] += 1
        else:
            totals["safe"] += 1

        rule_rows.append(
            {
                "rule_id": rule_id,
                "scope": str(rule.get("scope") or ""),
                "project": sample_project or project,
                "display_name": project_display_name(sample_project or project) if (sample_project or project) else "GLOBAL",
                "file": file_path,
                "rule_label": rule.get("label", ""),
                "allowlist_path": rule.get("allowlist_path", ""),
                "excluded_symbol_count": len(candidates),
                "file_matched_symbol_count": len(file_matched_candidates),
                "sampled_symbols": len(symbol_rows),
                "rule_verdict": rule_verdict,
                "symbols": symbol_rows,
            }
        )

    payload = {
        "meta": {"kind": "dead_code_manual_review_pack", "version": "v1", "allowlist_scope": get_allowlist_scope(CONFIG_DIR)},
        "summary": totals,
        "rules": rule_rows,
    }
    save_json_atomic(RAW_DIR / "dead_code_manual_review_pack.json", payload)

    lines = [
        "# Dead Code Manual Review Pack",
        "",
        f"- Rules scanned: `{totals['rules']}`",
        f"- Sampled symbols: `{totals['symbols']}`",
        f"- Rule verdicts: safe=`{totals['safe']}`, needs_manual_review=`{totals['needs_review']}`",
        "",
        "## Rule Checklist",
        "",
        "| Rule | Scope | Project | File | Excluded Symbols | Sampled | Verdict |",
        "|---|---|---|---|---:|---:|---|",
    ]
    for row in rule_rows:
        lines.append(
            f"| `{row['rule_id']}` | `{row.get('scope', '')}` | `{row['project']}` | `{row['file']}` | {row['excluded_symbol_count']} | {row['sampled_symbols']} | `{row['rule_verdict']}` |"
        )
    lines.append("")
    lines.append("## Symbol Evidence Samples")
    for row in rule_rows:
        lines.append("")
        lines.append(f"### {row['display_name']} - {row['rule_id']}")
        lines.append("")
        lines.append("| Symbol | External Hits | Verdict |")
        lines.append("|---|---:|---|")
        for sym in row["symbols"]:
            lines.append(f"| `{sym['symbol']}` | {sym['external_hit_count']} | `{sym['verdict']}` |")
            for sample in sym["external_samples"][:3]:
                lines.append(f"- `{sample['file']}:{sample['line']}` -> `{sample['snippet']}`")
        if not row["symbols"]:
            lines.append("- No sampled symbols found for this rule.")

    save_text_atomic(REPORTS_DIR / "dead_code_manual_review_pack.md", "\n".join(lines) + "\n")
    return payload


def main() -> int:
    payload = run()
    summary = payload.get("summary", {})
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

