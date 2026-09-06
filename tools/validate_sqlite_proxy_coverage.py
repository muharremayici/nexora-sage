from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path
from typing import Any

CODE_MAPS_DIR = Path(__file__).resolve().parents[1]
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))

from tools.core.config import RAW_DIR, DYNAMIC_CONFIG, save_json_atomic
from tools.core.validator_policy_registry import sqlite_proxy_coverage_policy


REPORT_PATH = RAW_DIR / "sqlite_proxy_coverage_validation.json"


def _check(name: str, passed: bool, details: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _read(path: str) -> str:
    return (CODE_MAPS_DIR / path).read_text(encoding="utf-8", errors="replace")


def _contains(path: str, needle: str) -> bool:
    return needle in _read(path)


def _definition_source(path: str, definition_name: str) -> str:
    """Return one complete function/method body for structural source checks."""

    text = _read(path)
    tree = ast.parse(text, filename=path)
    lines = text.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == definition_name:
            return "\n".join(lines[node.lineno - 1 : node.end_lineno])
    return ""


def _forbidden_matches() -> list[dict[str, str]]:
    """Detect direct disk reads for active output/.raw artifacts.

    This is intentionally scoped to hot integration files. Source-code reads,
    config reads, external-target smoke fixtures, report markdown and explicit
    bypass/recovery paths remain valid.
    """

    targets = sqlite_proxy_coverage_policy()["hot_integration_targets"]
    patterns = [
        re.compile(r"json\.loads\([^\\n]*(RAW_DIR|SIGNALS_PATH|HOST_INTELLIGENCE_PATH)[^\\n]*read_text"),
        re.compile(r"json\.load\([^\\n]*(RAW_DIR|atlas_path|genome_path|fractal_path)"),
        re.compile(r"(atlas_path|genome_path|fractal_path|signals_path)\.read_text"),
    ]
    matches: list[dict[str, str]] = []
    for rel_path in targets:
        text = _read(rel_path)
        for line_no, line in enumerate(text.splitlines(), start=1):
            if "bypass_proxy=True" in line:
                continue
            if any(pattern.search(line) for pattern in patterns):
                matches.append({"file": rel_path, "line": str(line_no), "text": line.strip()})
    return matches


def _active_runtime_raw_disk_read_matches() -> list[dict[str, str]]:
    """Conservatively scan active runtime code for direct managed raw reads.

    Validators, tests, explicit parity/recovery code and source/provenance
    indexers may inspect physical files. Runtime engines and agent surfaces
    should consume active RAW_DIR artifacts through load_json_file/STORE so
    SQLite remains the primary source of truth.
    """

    policy = sqlite_proxy_coverage_policy()
    roots = [CODE_MAPS_DIR / path for path in policy["active_runtime_roots"]]
    allowed_files = set(policy["raw_disk_read_allowed_files"])
    patterns = [
        re.compile(r"json\.loads\([^\\n]*(RAW_DIR|\.raw|output/\.raw|output\\\\\.raw)[^\\n]*read_text"),
        re.compile(r"json\.load\([^\\n]*(RAW_DIR|\.raw|output/\.raw|output\\\\\.raw)"),
        re.compile(r"(RAW_DIR|\.raw|output/\.raw|output\\\\\.raw)[^\\n]*(read_text|read_bytes)\("),
    ]
    matches: list[dict[str, str]] = []
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            rel_path = path.relative_to(CODE_MAPS_DIR).as_posix()
            if rel_path in allowed_files:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for line_no, line in enumerate(text.splitlines(), start=1):
                if "bypass_proxy=True" in line or "load_json_file(" in line or "load_json_strict(" in line:
                    continue
                if any(pattern.search(line) for pattern in patterns):
                    matches.append({"file": rel_path, "line": str(line_no), "text": line.strip()})
    return matches


def _active_runtime_raw_exists_gate_matches() -> list[dict[str, str]]:
    """Detect shadow-file exists gates for managed RAW_DIR JSON artifacts.

    A managed artifact may exist in SQLite while its compatibility JSON shadow
    is missing or still being exported. Runtime code should ask the proxy loader
    for the payload and branch on the returned value, not on Path.exists().
    """

    policy = sqlite_proxy_coverage_policy()
    roots = [CODE_MAPS_DIR / path for path in policy["active_runtime_exists_gate_roots"]]
    allowed_files = set(policy["raw_exists_gate_allowed_files"])
    matches: list[dict[str, str]] = []
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            rel_path = path.relative_to(CODE_MAPS_DIR).as_posix()
            if rel_path in allowed_files or "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            try:
                tree = ast.parse(text, filename=rel_path)
            except SyntaxError:
                continue

            parent: dict[ast.AST, ast.AST] = {}
            for node in ast.walk(tree):
                for child in ast.iter_child_nodes(node):
                    parent[child] = node

            def owning_scope(node: ast.AST) -> ast.AST:
                current = node
                while current in parent:
                    current = parent[current]
                    if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
                        return current
                return tree

            raw_json_vars_by_scope: dict[ast.AST, set[str]] = {}
            for node in ast.walk(tree):
                if not isinstance(node, ast.Assign):
                    continue
                if not _ast_contains_raw_json_path(node.value):
                    continue
                scope = owning_scope(node)
                raw_json_vars = raw_json_vars_by_scope.setdefault(scope, set())
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        raw_json_vars.add(target.id)
            if not raw_json_vars_by_scope:
                continue
            lines = text.splitlines()
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if not isinstance(func, ast.Attribute) or func.attr != "exists":
                    continue
                scope = owning_scope(node)
                raw_json_vars = raw_json_vars_by_scope.get(scope, set())
                if isinstance(func.value, ast.Name) and func.value.id in raw_json_vars:
                    line_no = int(getattr(node, "lineno", 0) or 0)
                    source = lines[line_no - 1].strip() if 1 <= line_no <= len(lines) else ""
                    matches.append({"file": rel_path, "line": str(line_no), "text": source})
    return matches


def _ast_contains_raw_json_path(node: ast.AST) -> bool:
    text_parts: list[str] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            text_parts.append(child.id)
        elif isinstance(child, ast.Constant):
            text_parts.append(str(child.value))
    joined = " ".join(text_parts)
    return "RAW_DIR" in joined and ".json" in joined


def run_validation() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    load_raw_source = _definition_source("tools/core/artifact_store.py", "load_raw")
    load_state_payload_source = _definition_source(
        "tools/core/artifact_store.py",
        "_load_state_payload_row",
    )

    checks.append(
        _check(
            "sqlite_feature_flag_enabled",
            bool(DYNAMIC_CONFIG.get("use_sqlite", False)),
            f"use_sqlite={bool(DYNAMIC_CONFIG.get('use_sqlite', False))}",
        )
    )
    checks.append(
        _check(
            "json_io_generic_raw_proxy",
            _contains("tools/core/json_io.py", "resolved.parent == raw_dir")
            and _contains("tools/core/json_io.py", "STORE.load_raw(path.stem"),
            "load_json_file/load_json_strict proxy active RAW_DIR artifacts by stem",
        )
    )
    checks.append(
        _check(
            "config_generic_raw_proxy",
            _contains("tools/core/config.py", "path.resolve().parent == Path(raw_dir).resolve()")
            and _contains("tools/core/config.py", "STORE.save_raw(path.stem"),
            "save_json_atomic proxies active RAW_DIR artifacts by stem",
        )
    )
    checks.append(
        _check(
            "state_payload_schema_exists",
            _contains("tools/core/db.py", "CREATE TABLE IF NOT EXISTS state_payloads"),
            "SQLite schema includes state_payloads",
        )
    )
    checks.append(
        _check(
            "artifact_store_sqlite_first_read",
            "SELECT * FROM state_payloads WHERE name = ?" in load_raw_source
            and "self._load_state_payload_row(conn, row)" in load_raw_source
            and "Falling back to JSON" in load_raw_source,
            "ArtifactStore reads SQLite first and falls back to JSON only for recovery",
        )
    )
    checks.append(
        _check(
            "artifact_store_existing_row_does_not_read_shadow_json",
            "if sqlite_row_found:" in load_raw_source
            and "return sqlite_payload" in load_raw_source
            and "load_json_file(json_path" not in load_raw_source.split("if sqlite_row_found:", 1)[1].split("return sqlite_payload", 1)[0]
            and _contains("tools/core/db.py", "source_mtime REAL"),
            "When SQLite has a state_payload row, runtime reads preserve SQLite primary truth and do not read shadow JSON",
        )
    )
    checks.append(
        _check(
            "artifact_store_hot_read_avoids_per_read_payload_digest",
            'if storage_mode == "inline_json":' in load_state_payload_source
            and 'return json.loads(row["payload"])' in load_state_payload_source
            and "digest.update(part_bytes)" in load_state_payload_source
            and "json.dumps" not in load_state_payload_source
            and "_payload_digest(sqlite_payload)" not in load_raw_source,
            "Inline runtime reads do not canonical-rehash JSON; partitioned reads verify bounded parts and the manifest digest while reassembling.",
        )
    )
    checks.append(
        _check(
            "artifact_store_missing_row_self_heal_is_telemetered",
            _contains("tools/core/artifact_store.py", "load_raw_missing_sqlite_row")
            and _contains("tools/core/artifact_store.py", "_record_store_missing_row_event")
            and _contains("tools/core/artifact_store.py", "shadow_json_read_and_sqlite_self_heal"),
            "Shadow JSON is read only for missing-row recovery and that self-heal path records honesty telemetry",
        )
    )
    checks.append(
        _check(
            "config_save_proxy_fallback_is_telemetered",
            _contains("tools/core/config.py", "managed raw artifact SQLite proxy write failed")
            and _contains("tools/core/config.py", "record_honesty_event")
            and _contains("tools/core/config.py", "direct_json_atomic_write")
            and _contains("tools/core/config.py", "artifact_freshness_requires_validation"),
            "Managed RAW_DIR write proxy fallback must be visible in honesty telemetry, not only printed",
        )
    )
    checks.append(
        _check(
            "artifact_store_dual_write_shadow_export",
            _contains("tools/core/artifact_store.py", "_save_payload_to_state_table")
            and _contains("tools/core/artifact_store.py", "bypass_proxy=True"),
            "ArtifactStore writes state_payloads and full shadow JSON exports",
        )
    )
    checks.append(
        _check(
            "artifact_store_shadow_flush_available",
            _contains("tools/core/artifact_store.py", "def flush_shadow_writes")
            and _contains("tools/core/artifact_store.py", "thread.daemon = False")
            and _contains("tools/core/artifact_store.py", "_write_threads.setdefault"),
            "Async JSON shadow exports have an explicit deterministic flush point",
        )
    )
    checks.append(
        _check(
            "raw_producers_do_not_require_immediate_shadow_file_visibility",
            "ctx_path.stat()" not in _read("tools/engines/ai_context_generator.py"),
            "SQLite-first producers must not stat an asynchronous JSON shadow immediately after save_json_atomic",
        )
    )
    checks.append(
        _check(
            "sqlite_parity_validator_uses_store_and_shadow_bypass",
            _contains("tools/validate_sqlite_artifact_parity.py", 'STORE.load_raw("atlas"')
            and _contains("tools/validate_sqlite_artifact_parity.py", "bypass_proxy=True"),
            "Parity validation compares SQLite primary payload against explicit shadow JSON",
        )
    )

    forbidden = _forbidden_matches()
    checks.append(
        _check(
            "hot_paths_do_not_bypass_raw_proxy",
            not forbidden,
            forbidden or "no direct active .raw JSON disk reads in hot integration paths",
        )
    )
    active_runtime_forbidden = _active_runtime_raw_disk_read_matches()
    checks.append(
        _check(
            "active_runtime_code_does_not_direct_read_managed_raw_json",
            not active_runtime_forbidden,
            active_runtime_forbidden
            or "no direct managed RAW_DIR/.raw JSON disk reads in active runtime code outside explicit storage/recovery boundaries",
        )
    )
    active_runtime_exists_gates = _active_runtime_raw_exists_gate_matches()
    checks.append(
        _check(
            "active_runtime_code_does_not_gate_managed_raw_truth_on_shadow_exists",
            not active_runtime_exists_gates,
            active_runtime_exists_gates
            or "no managed RAW_DIR JSON Path.exists gates in active runtime code outside explicit storage/recovery boundaries",
        )
    )
    checks.append(
        _check(
            "docs_describe_sqlite_primary_storage",
            _contains("README.md", "SQLite-backed artifact store and primary runtime SSoT")
            and _contains("docs/ARCHITECTURE.md", "state_payloads")
            and _contains("docs/TRUTH_MODEL.md", "output/.raw/codemaps.db"),
            "README, ARCHITECTURE and TRUTH_MODEL describe SQLite-first storage",
        )
    )

    failed = [check for check in checks if not check["passed"]]
    report = {
        "summary": {
            "total_checks": len(checks),
            "passed_checks": len(checks) - len(failed),
            "failed_checks": len(failed),
            "status": "PASS" if not failed else "FAIL",
        },
        "checks": checks,
    }
    save_json_atomic(REPORT_PATH, report, indent=2)
    return report


def main() -> int:
    report = run_validation()
    print(json.dumps(report["summary"], ensure_ascii=False))
    return 0 if report["summary"]["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
