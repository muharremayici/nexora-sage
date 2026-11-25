from __future__ import annotations

import ast
import json
import sys
from functools import cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.sqlite_first_access_policy import (
    pipeline_declared_artifact_modules,
    sqlite_first_allowed_references,
    sqlite_first_artifact_file,
    sqlite_first_central_adapter,
    sqlite_first_central_apis,
    sqlite_first_hot_consumers,
    sqlite_first_source_marker,
)


OUTPUT_JSON = RAW_DIR / "atlas_sqlite_first_access_validation.json"
OUTPUT_MD = REPORTS_DIR / "atlas_sqlite_first_access_validation.md"

ARTIFACT_ID = "atlas"
ARTIFACT_FILE = sqlite_first_artifact_file(ARTIFACT_ID)
HOT_ATLAS_CONSUMERS = sqlite_first_hot_consumers(ARTIFACT_ID)
ALLOWED_DIRECT_ATLAS_PATH_REFERENCES = sqlite_first_allowed_references(ARTIFACT_ID)
CENTRAL_ADAPTER = sqlite_first_central_adapter(ARTIFACT_ID)
CENTRAL_APIS = sqlite_first_central_apis(ARTIFACT_ID)
SQLITE_FIRST_MARKER = sqlite_first_source_marker(ARTIFACT_ID)


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _is_atlas_json_constant(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant) and str(node.value) == ARTIFACT_FILE:
        return True
    if isinstance(node, ast.JoinedStr):
        return any(_is_atlas_json_constant(value) for value in node.values)
    return False


def _contains_atlas_json(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if _is_atlas_json_constant(child):
            return True
    return False


def _direct_atlas_consumption_calls(path: Path, text: str) -> list[dict[str, Any]]:
    try:
        tree = ast.parse(text, filename=_rel(path))
    except SyntaxError as exc:
        return [{"file": _rel(path), "line": exc.lineno or 0, "kind": "syntax_error"}]

    findings: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if name not in {"load_json_file", "load_json_strict", "_load_json", "json_load", "load_json"}:
            continue
        if not _contains_atlas_json(node):
            continue
        findings.append(
            {
                "file": _rel(path),
                "line": int(getattr(node, "lineno", 0) or 0),
                "kind": name,
            }
        )
    return findings


def _path_reference_lines(path: Path, text: str) -> list[dict[str, Any]]:
    if ARTIFACT_FILE not in text:
        return []
    findings: list[dict[str, Any]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if ARTIFACT_FILE not in line:
            continue
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        findings.append({"file": _rel(path), "line": line_no, "text": stripped[:180]})
    return findings


def affected_by_change(relative: str) -> bool:
    """Select the Atlas guard for local development diagnostics."""
    if relative in {"config/sqlite_first_access_policy.json", "tools/core/sqlite_first_access_policy.py",
                    "tools/validate_atlas_sqlite_first_access.py"} or relative in HOT_ATLAS_CONSUMERS:
        return True
    part = Path(relative)
    if part.anchor or ".." in part.parts or part.parts[:1] != ("tools",) or part.suffix != ".py":
        return False
    path = ROOT / part
    if not path.is_file() or not path.resolve().is_relative_to(ROOT.resolve()):
        return False
    return bool(_path_reference_lines(path, path.read_text(encoding="utf-8", errors="replace")))


def preparation_observation(root: Path) -> dict[str, Any]:
    if root.resolve() != ROOT.resolve():
        raise ValueError("atlas_preparation_source_root_mismatch")
    return validate(preparation_only=True)


def validate(*, preparation_only: bool = False) -> dict[str, Any]:
    pipeline_declared_modules = pipeline_declared_artifact_modules(ARTIFACT_ID)
    missing_pipeline_hot_consumers = [
        {"file": file, "step": step}
        for file, step in sorted(pipeline_declared_modules.items())
        if file not in HOT_ATLAS_CONSUMERS
        and file not in ALLOWED_DIRECT_ATLAS_PATH_REFERENCES
    ]
    python_files = sorted((ROOT / "tools").rglob("*.py"))
    direct_calls: list[dict[str, Any]] = []
    references: list[dict[str, Any]] = []
    policy_sources = HOT_ATLAS_CONSUMERS | {CENTRAL_ADAPTER}

    @cache
    def policy_source_text(relative: str) -> str:
        # Share text across checks within this invocation only.
        return (ROOT / relative).read_text(encoding="utf-8", errors="replace")

    for path in python_files:
        if "__pycache__" in path.parts:
            continue
        rel = _rel(path)
        text = policy_source_text(rel) if rel in policy_sources else path.read_text(encoding="utf-8", errors="replace")
        # Only hot-consumer direct calls affect the verdict. The full report
        # also inventories direct calls in every other module.
        if not preparation_only or rel in HOT_ATLAS_CONSUMERS:
            direct_calls.extend(_direct_atlas_consumption_calls(path, text))
        references.extend(_path_reference_lines(path, text))

    hot_direct = [item for item in direct_calls if item.get("file") in HOT_ATLAS_CONSUMERS and item.get("file") != CENTRAL_ADAPTER]
    undeclared_references = [
        item
        for item in references
        if item.get("file") not in ALLOWED_DIRECT_ATLAS_PATH_REFERENCES
        and item.get("file") not in HOT_ATLAS_CONSUMERS
    ]
    hot_imports = {}
    for rel_path in sorted(HOT_ATLAS_CONSUMERS):
        path = ROOT / rel_path
        if not path.exists():
            hot_imports[rel_path] = {"passed": False, "reason": "missing hot consumer file"}
            continue
        text = policy_source_text(rel_path)
        touches_atlas = (
            rel_path == CENTRAL_ADAPTER
            or rel_path in pipeline_declared_modules
            or any(item.get("file") == rel_path for item in references)
            or any(item.get("file") == rel_path for item in direct_calls)
        )
        matched_apis = sorted(api for api in CENTRAL_APIS if api in text)
        has_central_api = bool(matched_apis) or rel_path == CENTRAL_ADAPTER
        hot_imports[rel_path] = {
            "passed": has_central_api if touches_atlas else True,
            "touches_atlas": touches_atlas,
            "uses_central_api": has_central_api,
            "matched_central_apis": matched_apis,
            "reason": "central API required" if touches_atlas else "not applicable",
        }

    checks = [
        {
            "name": "central_atlas_io_is_sqlite_first",
            "passed": bool(SQLITE_FIRST_MARKER) and SQLITE_FIRST_MARKER in policy_source_text(CENTRAL_ADAPTER),
            "details": "Default Atlas reads must go through ArtifactStore SQLite-first path.",
        },
        {
            "name": "hot_agent_context_consumers_use_central_atlas_api",
            "passed": all(item.get("passed") for item in hot_imports.values()),
            "details": hot_imports,
        },
        {
            "name": "pipeline_declared_atlas_modules_are_hot_consumers_or_allowed_exceptions",
            "passed": not missing_pipeline_hot_consumers,
            "details": missing_pipeline_hot_consumers
            or "all pipeline-declared atlas modules are policy hot consumers or allowed direct-reference exceptions",
        },
        {
            "name": "atlas_path_references_are_policy_declared",
            "passed": not undeclared_references,
            "details": undeclared_references or "all atlas.json path references are policy-declared",
        },
        {
            "name": "hot_agent_context_consumers_do_not_direct_load_atlas_json",
            "passed": not hot_direct,
            "details": hot_direct or "no hot direct atlas.json load calls",
        },
    ]
    status = "PASS" if all(check["passed"] for check in checks) else "FAIL"
    if preparation_only:
        return {"status": status, "checks": {row["name"]: row["passed"] for row in checks},
                "undeclared_references": [{"file": row["file"], "line": row["line"]}
                                          for row in undeclared_references[:100]]}
    payload = {
        "meta": {"kind": "atlas_sqlite_first_access_validation", "version": "v1"},
        "summary": {
            "status": status,
            "checks": len(checks),
            "passed": sum(1 for check in checks if check["passed"]),
            "direct_atlas_load_calls": len(direct_calls),
            "hot_direct_atlas_load_calls": len(hot_direct),
            "atlas_path_references": len(references),
            "undeclared_reference_candidates": len(undeclared_references),
            "pipeline_declared_atlas_modules": len(pipeline_declared_modules),
            "missing_pipeline_hot_consumers": len(missing_pipeline_hot_consumers),
        },
        "checks": checks,
        "direct_atlas_load_calls": direct_calls,
        "undeclared_reference_candidates": undeclared_references[:100],
        "pipeline_declared_atlas_modules": pipeline_declared_modules,
        "missing_pipeline_hot_consumers": missing_pipeline_hot_consumers,
        "allowed_reference_policy": ALLOWED_DIRECT_ATLAS_PATH_REFERENCES,
    }
    save_json_atomic(OUTPUT_JSON, payload)
    save_text_atomic(OUTPUT_MD, render_report(payload))
    return payload


def render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# Atlas SQLite-First Access Validation",
        "",
        f"- status: `{summary.get('status')}`",
        f"- direct atlas load calls: `{summary.get('direct_atlas_load_calls')}`",
        f"- hot direct atlas load calls: `{summary.get('hot_direct_atlas_load_calls')}`",
        f"- atlas path references: `{summary.get('atlas_path_references')}`",
        f"- undeclared reference candidates: `{summary.get('undeclared_reference_candidates')}`",
        f"- pipeline-declared atlas modules: `{summary.get('pipeline_declared_atlas_modules')}`",
        f"- missing pipeline hot consumers: `{summary.get('missing_pipeline_hot_consumers')}`",
        "",
        "## Checks",
        "",
        "| Check | Status |",
        "|---|---|",
    ]
    for check in payload.get("checks", []):
        lines.append(f"| `{check.get('name')}` | `{'PASS' if check.get('passed') else 'FAIL'}` |")
    lines.extend(["", "## Undeclared Reference Candidates", "", "| File | Line | Text |", "|---|---:|---|"])
    for item in payload.get("undeclared_reference_candidates", [])[:50]:
        lines.append(f"| `{item.get('file')}` | `{item.get('line')}` | `{item.get('text')}` |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    payload = validate()
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
