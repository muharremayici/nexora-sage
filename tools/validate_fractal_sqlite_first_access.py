from __future__ import annotations

import ast
import json
import sys
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


OUTPUT_JSON = RAW_DIR / "fractal_sqlite_first_access_validation.json"
OUTPUT_MD = REPORTS_DIR / "fractal_sqlite_first_access_validation.md"

ARTIFACT_ID = "fractal"
PIPELINE_ARTIFACT_ID = "fractal_map"
ARTIFACT_FILE = sqlite_first_artifact_file(ARTIFACT_ID)
HOT_FRACTAL_CONSUMERS = sqlite_first_hot_consumers(ARTIFACT_ID)
ALLOWED_DIRECT_FRACTAL_REFERENCES = sqlite_first_allowed_references(ARTIFACT_ID)
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
        prefix = f"{func.value.id}." if isinstance(func.value, ast.Name) else ""
        return f"{prefix}{func.attr}"
    return ""


def _is_fractal_constant(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant) and str(node.value) == ARTIFACT_FILE:
        return True
    if isinstance(node, ast.JoinedStr):
        return any(_is_fractal_constant(value) for value in node.values)
    return False


def _contains_fractal(node: ast.AST) -> bool:
    return any(_is_fractal_constant(child) for child in ast.walk(node))


def _direct_fractal_calls(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(text, filename=_rel(path))
    except SyntaxError as exc:
        return [{"file": _rel(path), "line": exc.lineno or 0, "kind": "syntax_error"}]

    findings: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if name in {"load_json_file", "load_json_strict", "_load_json", "json_load", "load_json"} and _contains_fractal(node):
            findings.append({"file": _rel(path), "line": int(getattr(node, "lineno", 0) or 0), "kind": name})
        if name.endswith("exists"):
            line = _line_at(text, int(getattr(node, "lineno", 0) or 0))
            if "fractal" in line.lower():
                findings.append({"file": _rel(path), "line": int(getattr(node, "lineno", 0) or 0), "kind": "exists_gate", "text": line})
    return findings


def _line_at(text: str, line_no: int) -> str:
    lines = text.splitlines()
    if 1 <= line_no <= len(lines):
        return lines[line_no - 1].strip()
    return ""


def _reference_lines(path: Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
        if ARTIFACT_FILE not in line:
            continue
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        findings.append({"file": _rel(path), "line": line_no, "text": stripped[:180]})
    return findings


def validate() -> dict[str, Any]:
    pipeline_declared_modules = pipeline_declared_artifact_modules(PIPELINE_ARTIFACT_ID)
    missing_pipeline_hot_consumers = [
        {"file": file, "step": step}
        for file, step in sorted(pipeline_declared_modules.items())
        if file not in HOT_FRACTAL_CONSUMERS
    ]
    python_files = sorted((ROOT / "tools").rglob("*.py"))
    calls: list[dict[str, Any]] = []
    references: list[dict[str, Any]] = []
    for path in python_files:
        if "__pycache__" in path.parts:
            continue
        calls.extend(_direct_fractal_calls(path))
        references.extend(_reference_lines(path))

    hot_exists_gates = [
        item for item in calls
        if item.get("file") in HOT_FRACTAL_CONSUMERS and item.get("kind") == "exists_gate"
    ]
    hot_direct = [
        item for item in calls
        if item.get("file") in HOT_FRACTAL_CONSUMERS
        and item.get("kind") != "exists_gate"
        and item.get("kind") not in {"load_json_file", "load_json_strict", "_load_json"}
    ]
    undeclared_references = [
        item for item in references
        if item.get("file") not in ALLOWED_DIRECT_FRACTAL_REFERENCES
        and item.get("file") not in HOT_FRACTAL_CONSUMERS
    ]
    hot_imports = {}
    for rel_path in sorted(HOT_FRACTAL_CONSUMERS):
        path = ROOT / rel_path
        if not path.exists():
            hot_imports[rel_path] = {"passed": False, "reason": "missing hot consumer file"}
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        touches_fractal = (
            rel_path == CENTRAL_ADAPTER
            or rel_path in pipeline_declared_modules
            or any(item.get("file") == rel_path for item in references)
            or any(item.get("file") == rel_path for item in calls)
        )
        matched_apis = sorted(api for api in CENTRAL_APIS if api in text)
        has_central_api = bool(matched_apis) or rel_path == CENTRAL_ADAPTER
        hot_imports[rel_path] = {
            "passed": has_central_api if touches_fractal else True,
            "touches_fractal": touches_fractal,
            "uses_central_api": has_central_api,
            "matched_central_apis": matched_apis,
        }
    mcp_text = (ROOT / "tools/mcp/server.py").read_text(encoding="utf-8", errors="replace")
    json_io_text = (ROOT / "tools/core/json_io.py").read_text(encoding="utf-8", errors="replace")

    checks = [
        {
            "name": "central_fractal_io_is_sqlite_first",
            "passed": bool(CENTRAL_ADAPTER)
            and bool(SQLITE_FIRST_MARKER)
            and SQLITE_FIRST_MARKER in (ROOT / CENTRAL_ADAPTER).read_text(encoding="utf-8", errors="replace"),
            "details": "Managed Fractal Map reads must use ArtifactStore before JSON shadow fallback.",
        },
        {
            "name": "hot_fractal_consumers_use_central_api",
            "passed": all(item.get("passed") for item in hot_imports.values()),
            "details": hot_imports,
        },
        {
            "name": "managed_raw_loader_handles_missing_shadow_with_sqlite_proxy",
            "passed": "if is_raw_artifact_path(path):" in mcp_text
            and "load_raw_artifact_path(path, None)" in mcp_text
            and "if _is_managed_raw_artifact(json_path):" in json_io_text
            and "return load_json_file(json_path, default)" in json_io_text,
            "details": "MCP _load_json must not treat a missing JSON shadow as missing managed truth before consulting SQLite.",
        },
        {
            "name": "hot_fractal_consumers_do_not_gate_on_shadow_exists",
            "passed": not hot_exists_gates,
            "details": hot_exists_gates or "no hot fractal_map exists() gates",
        },
        {
            "name": "hot_fractal_consumers_use_proxy_loaders",
            "passed": not hot_direct,
            "details": hot_direct or "no hot direct fractal_map loads outside proxy helpers",
        },
        {
            "name": "pipeline_declared_fractal_modules_are_hot_consumers",
            "passed": not missing_pipeline_hot_consumers,
            "details": missing_pipeline_hot_consumers or "all pipeline-declared fractal modules are policy hot consumers",
        },
        {
            "name": "fractal_path_references_are_policy_declared",
            "passed": not undeclared_references,
            "details": undeclared_references or "all fractal_map.json path references are policy-declared",
        },
    ]
    status = "PASS" if all(check["passed"] for check in checks) else "FAIL"
    payload = {
        "meta": {"kind": "fractal_sqlite_first_access_validation", "version": "v1"},
        "summary": {
            "status": status,
            "checks": len(checks),
            "passed": sum(1 for check in checks if check["passed"]),
            "fractal_references": len(references),
            "hot_exists_gates": len(hot_exists_gates),
            "hot_direct_loads": len(hot_direct),
            "undeclared_reference_candidates": len(undeclared_references),
            "pipeline_declared_fractal_modules": len(pipeline_declared_modules),
            "missing_pipeline_hot_consumers": len(missing_pipeline_hot_consumers),
        },
        "checks": checks,
        "direct_fractal_calls": calls,
        "undeclared_reference_candidates": undeclared_references[:100],
        "pipeline_declared_fractal_modules": pipeline_declared_modules,
        "missing_pipeline_hot_consumers": missing_pipeline_hot_consumers,
        "allowed_reference_policy": ALLOWED_DIRECT_FRACTAL_REFERENCES,
    }
    save_json_atomic(OUTPUT_JSON, payload)
    save_text_atomic(OUTPUT_MD, render_report(payload))
    return payload


def render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# Fractal Map SQLite-First Access Validation",
        "",
        f"- status: `{summary.get('status')}`",
        f"- fractal references: `{summary.get('fractal_references')}`",
        f"- hot exists gates: `{summary.get('hot_exists_gates')}`",
        f"- hot direct loads: `{summary.get('hot_direct_loads')}`",
        f"- pipeline-declared fractal modules: `{summary.get('pipeline_declared_fractal_modules')}`",
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
