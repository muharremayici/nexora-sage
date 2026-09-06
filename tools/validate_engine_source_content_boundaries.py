from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.core.config import CODE_MAPS_DIR, CONFIG_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic


REPORT_NAME = "engine_source_content_boundary_inventory"
POLICY_PATH = CONFIG_DIR / "engine_source_content_boundary_policy.json"


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        prefix = ""
        if isinstance(func.value, ast.Name):
            prefix = f"{func.value.id}."
        return f"{prefix}{func.attr}"
    return ""


def _literal_arg(node: ast.Call, index: int = 0) -> str:
    if len(node.args) <= index:
        return ""
    value = node.args[index]
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return value.value
    return ""


def _line_at(text: str, line_no: int) -> str:
    lines = text.splitlines()
    if 1 <= line_no <= len(lines):
        return lines[line_no - 1].strip()
    return ""


def _looks_like_artifact_load(node: ast.Call, line: str) -> bool:
    name = _call_name(node)
    return name in {"load_json_file", "load_json_strict"} and ("RAW_DIR" in line or ".raw" in line)


def _looks_like_config_read(line: str) -> bool:
    config_markers = (
        "CONFIG_DIR",
        "POLICY_PATH",
        "DOCTRINE_FILE",
        "package.json",
        "tsconfig",
        "codemaps.config",
        "codemaps.discovery",
        "allowlist",
        "cache_path",
        "_project_cache_path",
        "CACHE_FILE",
        "dead_code_intent_allowlist",
        ".md",
        "report_path",
        "oracle_report_path",
        "patterns_file",
        "source_tsconfig",
        "tsconfig_path",
    )
    return any(marker in line for marker in config_markers)


def _looks_like_snapshot_or_report_read(line: str) -> bool:
    markers = (
        "SNAPSHOTS_DIR",
        "existing[",
        "report_path",
        "oracle_report_path",
        "closure_path",
        "manifest_path",
    )
    return any(marker in line for marker in markers)


def _is_write_call(node: ast.Call, line: str) -> bool:
    name = _call_name(node)
    if name.endswith("write_text") or name.endswith("write_bytes"):
        return True
    if name == "open" and any(mode in line for mode in ("'w'", '"w"', "'a'", '"a"', "'wb'", '"wb"', "'ab'", '"ab"')):
        return True
    return False


def _is_source_read_call(node: ast.Call, line: str) -> bool:
    if _is_write_call(node, line):
        return False
    if _looks_like_snapshot_or_report_read(line):
        return False
    name = _call_name(node)
    if name == "read_atlas_bound_source":
        return True
    if name.endswith("read_text") or name.endswith("read_bytes"):
        return not _looks_like_config_read(line)
    if name == "open":
        literal = _literal_arg(node)
        if literal and (literal.endswith(".json") or literal.endswith(".md")):
            return False
        return not _looks_like_config_read(line)
    return False


def _is_traversal_call(node: ast.Call) -> bool:
    name = _call_name(node)
    return name in {"rglob", "glob", "os.walk", "walk"}


def _source_snapshot_table_status(validation_contract: dict[str, Any]) -> dict[str, Any]:
    db_text = (CODE_MAPS_DIR / "tools" / "core" / "db.py").read_text(encoding="utf-8", errors="replace")
    required_markers = _string_list(validation_contract.get("source_snapshot_table_markers"))
    required_columns = _string_list(validation_contract.get("required_source_snapshot_columns"))
    markers = [marker for marker in required_markers if marker in db_text]
    columns_present = [column for column in required_columns if column in db_text]
    missing_markers = [marker for marker in required_markers if marker not in db_text]
    missing_columns = [column for column in required_columns if column not in db_text]
    return {
        "available": bool(required_markers) and not missing_markers and bool(required_columns) and not missing_columns,
        "markers": markers,
        "missing_markers": missing_markers,
        "columns_present": columns_present,
        "missing_columns": missing_columns,
        "required_for_sqlite_first_source_content": required_columns,
    }


def _engine_inventory(path: Path) -> dict[str, Any]:
    rel = path.relative_to(CODE_MAPS_DIR).as_posix()
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(text, filename=rel)
    except SyntaxError as exc:
        return {
            "file": rel,
            "syntax_error": {"line": exc.lineno or 0, "message": str(exc)},
            "status": "syntax_error",
        }

    artifact_loads: list[dict[str, Any]] = []
    source_reads: list[dict[str, Any]] = []
    traversals: list[dict[str, Any]] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        line_no = int(getattr(node, "lineno", 0) or 0)
        line = _line_at(text, line_no)
        if _looks_like_artifact_load(node, line):
            artifact_loads.append({"line": line_no, "call": _call_name(node), "text": line})
        if _is_source_read_call(node, line):
            source_reads.append({"line": line_no, "call": _call_name(node), "text": line})
        if _is_traversal_call(node):
            traversals.append({"line": line_no, "call": _call_name(node), "text": line})

    source_evidence_text = (CODE_MAPS_DIR / "tools" / "core" / "source_evidence.py").read_text(
        encoding="utf-8", errors="replace"
    )
    atlas_bound_helper_is_sqlite_first = (
        "load_source_text(" in source_evidence_text
        and "allow_live_fallback=False" in source_evidence_text
    )
    uses_source_snapshot_reader = (
        "load_source_text(" in text
        or "source_snapshot_reader" in text
        or ("read_atlas_bound_source(" in text and atlas_bound_helper_is_sqlite_first)
    )
    has_source_contract = all(
        marker in text
        for marker in (
            "source_contract",
            "content_source",
        )
    )
    if source_reads and uses_source_snapshot_reader:
        status = "sqlite_source_snapshot_consumer"
    elif source_reads and has_source_contract:
        status = "declared_live_source_boundary"
    elif uses_source_snapshot_reader:
        status = "sqlite_source_snapshot_consumer"
    elif source_reads:
        status = "needs_source_boundary_review"
    elif traversals:
        status = "filesystem_scope_only_or_adapter"
    else:
        status = "no_source_content_reads_detected"

    return {
        "file": rel,
        "status": status,
        "artifact_loads": artifact_loads,
        "source_reads": source_reads,
        "filesystem_traversals": traversals,
        "has_source_contract": has_source_contract,
        "uses_source_snapshot_reader": uses_source_snapshot_reader,
    }


def _load_policy() -> dict[str, Any]:
    try:
        payload = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def validate_engine_source_content_boundaries() -> dict[str, Any]:
    policy = _load_policy()
    validation_contract = (
        policy.get("validation_contract", {})
        if isinstance(policy.get("validation_contract"), dict)
        else {}
    )
    scan_roots = [
        CODE_MAPS_DIR / item
        for item in _string_list(validation_contract.get("scan_roots"))
    ]
    contract_errors: list[str] = []
    if not scan_roots:
        contract_errors.append("missing_scan_roots")
    if not _string_list(validation_contract.get("source_snapshot_table_markers")):
        contract_errors.append("missing_source_snapshot_table_markers")
    if not _string_list(validation_contract.get("required_source_snapshot_columns")):
        contract_errors.append("missing_required_source_snapshot_columns")
    boundaries = policy.get("engine_boundaries", {}) if isinstance(policy.get("engine_boundaries"), dict) else {}
    accepted_live_read_classes = (
        policy.get("accepted_live_read_classes", {})
        if isinstance(policy.get("accepted_live_read_classes"), dict)
        else {}
    )
    engines = [
        _engine_inventory(path)
        for root in scan_roots
        for path in sorted(root.glob("*.py"))
    ]
    source_snapshot_table = _source_snapshot_table_status(validation_contract)
    for item in engines:
        declaration = boundaries.get(str(item.get("file") or ""))
        if isinstance(declaration, dict):
            item["declared_boundary"] = {
                "class": declaration.get("class"),
                "status": declaration.get("status"),
                "reason": declaration.get("reason"),
            }
        else:
            item["declared_boundary"] = None
        if item.get("source_reads"):
            if item.get("uses_source_snapshot_reader"):
                item["status"] = "sqlite_source_snapshot_consumer"
                item["boundary_class"] = str((declaration or {}).get("class") or "source_snapshot_consumer")
            elif isinstance(declaration, dict):
                item["status"] = str(declaration.get("status") or item.get("status"))
                item["boundary_class"] = str(declaration.get("class") or "")
            else:
                item["boundary_class"] = ""

    status_counts: dict[str, int] = {}
    for item in engines:
        status = str(item.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
    review_candidates = [
        item
        for item in engines
        if item.get("source_reads") and not item.get("declared_boundary")
    ]
    declared_snapshot_consumers_without_reader = [
        item
        for item in engines
        if item.get("declared_boundary")
        and item.get("declared_boundary", {}).get("status") == "sqlite_source_snapshot_consumer"
        and not item.get("uses_source_snapshot_reader")
    ]
    sqlite_ready_candidates = [
        item
        for item in engines
        if item.get("source_reads")
        and item.get("status") != "sqlite_source_snapshot_consumer"
        and item.get("declared_boundary")
        and item.get("declared_boundary", {}).get("status") == "sqlite_snapshot_candidate"
    ]
    sqlite_source_snapshot_consumers = [
        item
        for item in engines
        if item.get("status") == "sqlite_source_snapshot_consumer"
    ]
    source_snapshot_table_missing = bool(sqlite_source_snapshot_consumers) and not bool(source_snapshot_table["available"])
    accepted_live_reads = [
        item
        for item in engines
        if item.get("source_reads")
        and item.get("declared_boundary")
        and item.get("declared_boundary", {}).get("status") == "accepted_live_source_read"
    ]
    accepted_live_reads_missing_class_policy = [
        item
        for item in accepted_live_reads
        if str(item.get("declared_boundary", {}).get("class") or "") not in accepted_live_read_classes
    ]
    payload = {
        "meta": {
            "kind": REPORT_NAME,
            "version": "v1",
        },
        "summary": {
            "status": "PASS"
            if not contract_errors
            and not review_candidates
            and not declared_snapshot_consumers_without_reader
            and not source_snapshot_table_missing
            and not accepted_live_reads_missing_class_policy
            else "FAIL",
            "engines_scanned": len(engines),
            "scan_roots": len(scan_roots),
            "contract_errors": contract_errors,
            "source_snapshot_table_available": source_snapshot_table["available"],
            "source_snapshot_table_missing": source_snapshot_table_missing,
            "source_reading_engines": sum(1 for item in engines if item.get("source_reads")),
            "undeclared_source_boundaries": len(review_candidates),
            "declared_snapshot_consumers_without_reader": len(declared_snapshot_consumers_without_reader),
            "accepted_live_source_reads": len(accepted_live_reads),
            "accepted_live_reads_missing_class_policy": len(accepted_live_reads_missing_class_policy),
            "accepted_live_read_classes": len(accepted_live_read_classes),
            "sqlite_ready_candidates": len(sqlite_ready_candidates),
            "sqlite_source_snapshot_consumers": len(sqlite_source_snapshot_consumers),
            "status_counts": dict(sorted(status_counts.items())),
        },
        "validation_contract": validation_contract,
        "principles": policy.get("principles", {}),
        "accepted_live_read_classes": accepted_live_read_classes,
        "source_snapshot_table": source_snapshot_table,
        "undeclared_source_boundaries": review_candidates,
        "declared_snapshot_consumers_without_reader": declared_snapshot_consumers_without_reader,
        "accepted_live_reads_missing_class_policy": accepted_live_reads_missing_class_policy,
        "accepted_live_source_reads": accepted_live_reads,
        "sqlite_ready_candidates": sqlite_ready_candidates,
        "sqlite_source_snapshot_consumers": sqlite_source_snapshot_consumers,
        "engines": engines,
    }
    save_json_atomic(RAW_DIR / f"{REPORT_NAME}.json", payload)
    save_text_atomic(REPORTS_DIR / f"{REPORT_NAME}.md", render_report(payload))
    return payload


def render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# Engine Source Content Boundary Inventory",
        "",
        f"- status: `{summary.get('status')}`",
        f"- engines_scanned: `{summary.get('engines_scanned')}`",
        f"- scan_roots: `{summary.get('scan_roots')}`",
        f"- contract_errors: `{summary.get('contract_errors')}`",
        f"- source_snapshot_table_available: `{summary.get('source_snapshot_table_available')}`",
        f"- source_snapshot_table_missing: `{summary.get('source_snapshot_table_missing')}`",
        f"- source_reading_engines: `{summary.get('source_reading_engines')}`",
        f"- undeclared_source_boundaries: `{summary.get('undeclared_source_boundaries')}`",
        f"- declared_snapshot_consumers_without_reader: `{summary.get('declared_snapshot_consumers_without_reader')}`",
        f"- accepted_live_source_reads: `{summary.get('accepted_live_source_reads')}`",
        f"- accepted_live_reads_missing_class_policy: `{summary.get('accepted_live_reads_missing_class_policy')}`",
        f"- accepted_live_read_classes: `{summary.get('accepted_live_read_classes')}`",
        f"- sqlite_ready_candidates: `{summary.get('sqlite_ready_candidates')}`",
        f"- sqlite_source_snapshot_consumers: `{summary.get('sqlite_source_snapshot_consumers')}`",
        "",
        "## Status Counts",
        "",
        "| Status | Count |",
        "|---|---:|",
    ]
    for key, value in (summary.get("status_counts") or {}).items():
        lines.append(f"| `{key}` | {value} |")
    lines.extend(
        [
            "",
            "## Undeclared Source Boundaries",
            "",
            "| Engine | Source Reads | Artifact Loads | First Source Read |",
            "|---|---:|---:|---|",
        ]
    )
    for item in payload.get("undeclared_source_boundaries", [])[:80]:
        first = (item.get("source_reads") or [{}])[0]
        lines.append(
            f"| `{item.get('file')}` | {len(item.get('source_reads') or [])} | "
            f"{len(item.get('artifact_loads') or [])} | line {first.get('line')}: `{first.get('text', '')}` |"
        )
    lines.extend(
        [
            "",
            "## Accepted Live Source Reads",
            "",
            "| Engine | Class | Reason | Class policy |",
            "|---|---|---|---|",
        ]
    )
    for item in payload.get("accepted_live_source_reads", [])[:80]:
        declared = item.get("declared_boundary") or {}
        class_name = str(declared.get("class") or "")
        class_policy = (payload.get("accepted_live_read_classes") or {}).get(class_name) or {}
        lines.append(
            f"| `{item.get('file')}` | `{class_name}` | {declared.get('reason') or ''} | "
            f"{class_policy.get('allowed_scope') or 'MISSING CLASS POLICY'} |"
        )
    lines.extend(
        [
            "",
            "## SQLite-Ready Candidates",
            "",
            "These engines read live target source text and are declared as candidates for a future hash-validated SQLite source snapshot layer.",
            "",
            "| Engine | Source Reads | Artifact Loads |",
            "|---|---:|---:|",
        ]
    )
    for item in payload.get("sqlite_ready_candidates", [])[:80]:
        lines.append(
            f"| `{item.get('file')}` | {len(item.get('source_reads') or [])} | "
            f"{len(item.get('artifact_loads') or [])} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    payload = validate_engine_source_content_boundaries()
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
