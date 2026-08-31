from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.sqlite_first_access_policy import (
    sqlite_first_large_allowed_references,
    sqlite_first_large_artifact_policies,
)


OUTPUT_JSON = RAW_DIR / "large_artifact_sqlite_first_access_validation.json"
OUTPUT_MD = REPORTS_DIR / "large_artifact_sqlite_first_access_validation.md"

ARTIFACTS = sqlite_first_large_artifact_policies()
ALLOWED_REFERENCE_FILES = sqlite_first_large_allowed_references()


def _log(message: str) -> None:
    print(f"[large-artifact-sqlite-first] {message}", flush=True)


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


def _contains_artifact(node: ast.AST, artifact_name: str) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and str(child.value) == artifact_name:
            return True
        if isinstance(child, ast.JoinedStr):
            for value in child.values:
                if isinstance(value, ast.Constant) and str(value.value) == artifact_name:
                    return True
    return False


def _artifact_path_variables(tree: ast.AST, artifact_name: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not _contains_artifact(node.value, artifact_name):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
    return names


def _call_uses_artifact_path(node: ast.Call, artifact_name: str, artifact_vars: set[str]) -> bool:
    if _contains_artifact(node, artifact_name):
        return True
    for arg in node.args:
        if isinstance(arg, ast.Name) and arg.id in artifact_vars:
            return True
    for keyword in node.keywords:
        value = keyword.value
        if isinstance(value, ast.Name) and value.id in artifact_vars:
            return True
    return False


def _line_at(text: str, line_no: int) -> str:
    lines = text.splitlines()
    if 1 <= line_no <= len(lines):
        return lines[line_no - 1].strip()
    return ""


def _python_files() -> list[Path]:
    roots = [
        ROOT / "codemaps.py",
        ROOT / "sage.py",
        ROOT / "tools",
    ]
    files: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        if root.is_file():
            files.append(root)
        else:
            files.extend(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)
    return sorted(files)


def _empty_scan_result() -> dict[str, list[dict[str, Any]]]:
    return {
        "proxy_loads": [],
        "proxy_writes": [],
        "bad_direct_reads": [],
        "exists_gates": [],
        "references": [],
    }


def _mentions_artifact(text: str, artifact_name: str) -> bool:
    pattern = rf"(?<![A-Za-z0-9_.-]){re.escape(artifact_name)}(?![A-Za-z0-9_.-])"
    return re.search(pattern, text) is not None


def _external_raw_consumer_findings(path: Path, text: str) -> list[dict[str, Any]]:
    """Reject shadow-only reads of registry-resolved external raw artifacts."""
    rel = _rel(path)
    try:
        tree = ast.parse(text, filename=rel)
    except SyntaxError:
        return []
    findings: list[dict[str, Any]] = []
    functions = (node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)))
    for function in functions:
        if "raw_dir" not in {argument.arg for argument in function.args.args}:
            continue
        external_path_names: set[str] = set()
        for node in ast.walk(function):
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
                continue
            if _call_name(node.value) != "artifact_path_for_storage_root":
                continue
            if not any(isinstance(arg, ast.Name) and arg.id == "raw_dir" for arg in node.value.args):
                continue
            external_path_names.update(target.id for target in node.targets if isinstance(target, ast.Name))
        for node in ast.walk(function):
            if not isinstance(node, ast.Call) or _call_name(node) != "load_json_strict":
                continue
            if any(isinstance(arg, ast.Name) and arg.id in external_path_names for arg in node.args):
                line_no = int(getattr(node, "lineno", 0) or 0)
                findings.append(
                    {
                        "file": rel,
                        "line": line_no,
                        "kind": "external_raw_shadow_only_read",
                        "text": _line_at(text, line_no),
                    }
                )
    return findings


def _scan_file(path: Path, text: str | None = None) -> dict[str, dict[str, list[dict[str, Any]]]]:
    if text is None:
        text = path.read_text(encoding="utf-8", errors="replace")
    rel = _rel(path)
    present_artifacts = [artifact_name for artifact_name in ARTIFACTS if _mentions_artifact(text, artifact_name)]
    results = {artifact_name: _empty_scan_result() for artifact_name in present_artifacts}
    if not present_artifacts:
        return results

    for line_no, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for artifact_name in present_artifacts:
            if _mentions_artifact(line, artifact_name):
                results[artifact_name]["references"].append({"file": rel, "line": line_no, "text": stripped[:180]})

    try:
        tree = ast.parse(text, filename=rel)
    except SyntaxError as exc:
        for artifact_name in present_artifacts:
            results[artifact_name]["bad_direct_reads"].append({"file": rel, "line": exc.lineno or 0, "kind": "syntax_error"})
        return results

    artifact_vars_by_name = {
        artifact_name: _artifact_path_variables(tree, artifact_name)
        for artifact_name in present_artifacts
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        line_no = int(getattr(node, "lineno", 0) or 0)
        call = _call_name(node)
        line = _line_at(text, line_no)
        for artifact_name in present_artifacts:
            artifact_vars = artifact_vars_by_name[artifact_name]
            line_mentions_artifact = _mentions_artifact(line, artifact_name)
            call_uses_artifact = _call_uses_artifact_path(node, artifact_name, artifact_vars)
            if (
                call in {
                    "load_json_file",
                    "load_json_strict",
                    "_load_json",
                    "_load",
                    "_read_json_artifact",
                    "_artifact_or_missing",
                    "_failed_checks",
                    "_load_raw",
                    "_summary",
                }
                and call_uses_artifact
            ) or (artifact_name == "audit_report.json" and call == "load_audit_report"):
                results[artifact_name]["proxy_loads"].append({"file": rel, "line": line_no, "kind": call, "text": line})
            elif call == "save_json_atomic" and call_uses_artifact:
                results[artifact_name]["proxy_writes"].append({"file": rel, "line": line_no, "kind": call, "text": line})
            elif call in {"json.load", "json.loads"} and (call_uses_artifact or line_mentions_artifact):
                results[artifact_name]["bad_direct_reads"].append({"file": rel, "line": line_no, "kind": call, "text": line})
            elif call.endswith("read_text") and line_mentions_artifact:
                results[artifact_name]["bad_direct_reads"].append({"file": rel, "line": line_no, "kind": call, "text": line})
            elif call.endswith("exists") and (line_mentions_artifact or call_uses_artifact):
                results[artifact_name]["exists_gates"].append({"file": rel, "line": line_no, "kind": "exists_gate", "text": line})
    return results


def validate() -> dict[str, Any]:
    files = _python_files()
    _log(f"START files={len(files)} artifacts={len(ARTIFACTS)} mode=single_pass")
    artifact_scan = {
        artifact_name: _empty_scan_result()
        for artifact_name in ARTIFACTS
    }
    external_raw_shadow_reads: list[dict[str, Any]] = []
    for index, file_path in enumerate(files, start=1):
        if index == 1 or index % 100 == 0 or index == len(files):
            _log(f"SCAN files={index}/{len(files)}")
        file_text = file_path.read_text(encoding="utf-8", errors="replace")
        external_raw_shadow_reads.extend(_external_raw_consumer_findings(file_path, file_text))
        scanned_by_artifact = _scan_file(file_path, file_text)
        for artifact_name, scanned in scanned_by_artifact.items():
            artifact_scan[artifact_name]["references"].extend(scanned["references"])
            artifact_scan[artifact_name]["proxy_loads"].extend(scanned["proxy_loads"])
            artifact_scan[artifact_name]["proxy_writes"].extend(scanned["proxy_writes"])
            artifact_scan[artifact_name]["bad_direct_reads"].extend(scanned["bad_direct_reads"])
            artifact_scan[artifact_name]["exists_gates"].extend(scanned["exists_gates"])
    artifact_reports: list[dict[str, Any]] = []
    all_bad_direct_reads: list[dict[str, Any]] = []
    all_hot_exists_gates: list[dict[str, Any]] = []
    all_missing_hot_proxy_usage: list[dict[str, Any]] = []
    all_undeclared_refs: list[dict[str, Any]] = []

    for artifact_name, policy in ARTIFACTS.items():
        hot_consumers = set(policy.get("hot_consumers") or set())
        producer = str(policy.get("producer") or "")
        references = artifact_scan[artifact_name]["references"]
        proxy_loads = artifact_scan[artifact_name]["proxy_loads"]
        proxy_writes = artifact_scan[artifact_name]["proxy_writes"]
        bad_direct_reads = artifact_scan[artifact_name]["bad_direct_reads"]
        exists_gates = artifact_scan[artifact_name]["exists_gates"]

        hot_proxy_files = {
            item["file"] for item in proxy_loads + proxy_writes
            if item.get("file") in hot_consumers
        }
        hot_reference_files = {
            item["file"] for item in references
            if item.get("file") in hot_consumers
        }
        missing_hot_proxy_usage = [
            {"artifact": artifact_name, "file": file}
            for file in sorted(hot_reference_files - hot_proxy_files)
            if file != producer
        ]
        hot_exists_gates = [
            dict(item, artifact=artifact_name)
            for item in exists_gates
            if item.get("file") in hot_consumers
        ]
        undeclared_refs = [
            dict(item, artifact=artifact_name)
            for item in references
            if item.get("file") not in hot_consumers
            and item.get("file") != producer
            and item.get("file") not in ALLOWED_REFERENCE_FILES
        ]

        all_bad_direct_reads.extend(dict(item, artifact=artifact_name) for item in bad_direct_reads)
        all_hot_exists_gates.extend(hot_exists_gates)
        all_missing_hot_proxy_usage.extend(missing_hot_proxy_usage)
        all_undeclared_refs.extend(undeclared_refs)
        artifact_reports.append(
            {
                "artifact": artifact_name,
                "hot_consumers": sorted(hot_consumers),
                "references": len(references),
                "proxy_loads": len(proxy_loads),
                "proxy_writes": len(proxy_writes),
                "bad_direct_reads": len(bad_direct_reads),
                "hot_exists_gates": len(hot_exists_gates),
                "missing_hot_proxy_usage": missing_hot_proxy_usage,
                "undeclared_reference_candidates": undeclared_refs[:50],
                "sample_proxy_loads": proxy_loads[:20],
                "sample_proxy_writes": proxy_writes[:20],
            }
        )
        _log(
            f"DONE artifact={artifact_name} refs={len(references)} "
            f"proxy_loads={len(proxy_loads)} bad_reads={len(bad_direct_reads)}"
        )

    checks = [
        {
            "name": "large_artifact_hot_consumers_use_proxy_loaders",
            "passed": not all_missing_hot_proxy_usage,
            "details": all_missing_hot_proxy_usage or "all hot consumers with references use proxy load/write helpers",
        },
        {
            "name": "large_artifact_hot_consumers_do_not_gate_on_shadow_exists",
            "passed": not all_hot_exists_gates,
            "details": all_hot_exists_gates or "no hot exists() gates for large shadow artifacts",
        },
        {
            "name": "large_artifact_no_direct_json_disk_reads",
            "passed": not all_bad_direct_reads,
            "details": all_bad_direct_reads or "no direct json/read_text bypasses for tracked large artifacts",
        },
        {
            "name": "large_artifact_path_references_are_policy_declared",
            "passed": not all_undeclared_refs,
            "details": all_undeclared_refs or "all large artifact path references are policy-declared",
        },
        {
            "name": "external_raw_consumers_use_sqlite_first_loader",
            "passed": not external_raw_shadow_reads,
            "details": external_raw_shadow_reads or "registry-resolved external raw artifacts use the SQLite-first loader",
        },
    ]
    status = "PASS" if all(check["passed"] for check in checks) else "FAIL"
    payload = {
        "meta": {"kind": "large_artifact_sqlite_first_access_validation", "version": "v1"},
        "summary": {
            "status": status,
            "checks": len(checks),
            "passed": sum(1 for check in checks if check["passed"]),
            "tracked_artifacts": len(ARTIFACTS),
            "bad_direct_reads": len(all_bad_direct_reads),
            "hot_exists_gates": len(all_hot_exists_gates),
            "missing_hot_proxy_usage": len(all_missing_hot_proxy_usage),
            "undeclared_reference_candidates": len(all_undeclared_refs),
            "external_raw_shadow_reads": len(external_raw_shadow_reads),
        },
        "checks": checks,
        "artifacts": artifact_reports,
        "undeclared_reference_candidates": all_undeclared_refs[:100],
        "external_raw_shadow_reads": external_raw_shadow_reads,
        "allowed_reference_policy": ALLOWED_REFERENCE_FILES,
    }
    save_json_atomic(OUTPUT_JSON, payload)
    save_text_atomic(OUTPUT_MD, render_report(payload))
    _log(f"DONE status={status}")
    return payload


def render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# Large Artifact SQLite-First Access Validation",
        "",
        f"- status: `{summary.get('status')}`",
        f"- tracked artifacts: `{summary.get('tracked_artifacts')}`",
        f"- bad direct reads: `{summary.get('bad_direct_reads')}`",
        f"- hot exists gates: `{summary.get('hot_exists_gates')}`",
        f"- missing hot proxy usage: `{summary.get('missing_hot_proxy_usage')}`",
        f"- external raw shadow reads: `{summary.get('external_raw_shadow_reads')}`",
        "",
        "## Checks",
        "",
        "| Check | Status |",
        "|---|---|",
    ]
    for check in payload.get("checks", []):
        lines.append(f"| `{check.get('name')}` | `{'PASS' if check.get('passed') else 'FAIL'}` |")
    lines.extend(["", "## Artifacts", "", "| Artifact | References | Proxy loads | Proxy writes | Bad reads | Hot exists gates |", "|---|---:|---:|---:|---:|---:|"])
    for artifact in payload.get("artifacts", []):
        lines.append(
            f"| `{artifact.get('artifact')}` | {artifact.get('references')} | {artifact.get('proxy_loads')} | "
            f"{artifact.get('proxy_writes')} | {artifact.get('bad_direct_reads')} | {artifact.get('hot_exists_gates')} |"
        )
    lines.extend(["", "## Undeclared Reference Candidates", "", "| Artifact | File | Line | Text |", "|---|---|---:|---|"])
    for item in payload.get("undeclared_reference_candidates", [])[:50]:
        text = str(item.get("text") or "").replace("|", "\\|")
        lines.append(f"| `{item.get('artifact')}` | `{item.get('file')}` | {item.get('line')} | {text} |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    payload = validate()
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
