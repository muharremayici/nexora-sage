from __future__ import annotations

import ast
import json
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CODE_MAPS_DIR = Path(__file__).resolve().parent.parent
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))

from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file
from tools.engines.dead_code_detector import DEAD_CODE_ENGINE_SIGNATURE, DeadCodeDetector
from tools.core.package_contracts import build_package_public_contracts


RAW_OUTPUT_PATH = RAW_DIR / "dead_code_public_contract_validation.json"
REPORT_OUTPUT_PATH = REPORTS_DIR / "dead_code_public_contract_validation.md"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def _check(name: str, passed: bool, details: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _scope_nodes(root: ast.AST):
    """Inspect one Python scope; nested functions/classes are not cache evidence."""
    yield root
    for child in ast.iter_child_nodes(root):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            yield child
        else:
            yield from _scope_nodes(child)


def _binds_name(node: ast.AST, name: str) -> bool:
    return (
        isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)) and node.id == name
        or isinstance(node, ast.arg) and node.arg == name
        or isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == name
        or isinstance(node, ast.alias) and (node.asname or node.name.split(".")[0]) == name
        or isinstance(node, (ast.Global, ast.Nonlocal)) and name in node.names
        or isinstance(node, ast.ExceptHandler) and node.name == name
    )


def _engine_signature_cache_binding(source: str, signature: Any) -> dict[str, Any]:
    """Check the supported cache wiring, not a historical feature-name suffix.

    This is bounded source/identity evidence, not a Python interpreter proof.
    Public-export behavior remains covered by the six independent checks below.
    """
    def result(passed: bool, reason: str) -> dict[str, Any]:
        return {"passed": passed, "reason": reason, "engine_signature": signature}

    if not isinstance(signature, str) or not re.fullmatch(
        r"dead_code_logic_v[1-9][0-9]*_[A-Za-z0-9]+(?:_[A-Za-z0-9]+)*", signature
    ):
        return result(False, "invalid_versioned_engine_signature")
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, TypeError) as error:
        return {**result(False, "engine_source_not_parseable"), "error_type": type(error).__name__}
    declarations = [
        node for node in tree.body
        if isinstance(node, ast.Assign) and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "DEAD_CODE_ENGINE_SIGNATURE"
    ]
    if (len(declarations) != 1
            or not isinstance(declarations[0].value, ast.Constant)
            or declarations[0].value.value != signature
            or sum(_binds_name(node, "DEAD_CODE_ENGINE_SIGNATURE") for node in _scope_nodes(tree)) != 1):
        return result(False, "signature_declaration_not_unique_or_current")
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "DeadCodeDetector"]
    runs = [node for cls in classes for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "run"]
    if len(classes) != 1 or len(runs) != 1:
        return result(False, "engine_run_owner_not_unique")
    run = runs[0]
    nodes = list(_scope_nodes(run))
    if any(_binds_name(node, "DEAD_CODE_ENGINE_SIGNATURE") for node in nodes):
        return result(False, "signature_is_shadowed_in_run")
    assignments = [
        node for node in nodes
        if isinstance(node, ast.Assign) and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "project_cache_key"
    ]
    if len(assignments) != 1 or sum(_binds_name(node, "project_cache_key") for node in nodes) != 1:
        return result(False, "project_cache_key_not_uniquely_bound")
    assignment = assignments[0]
    call = assignment.value
    if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name) and call.func.value.id == "self"
            and call.func.attr == "_stable_fingerprint" and len(call.args) == 1
            and not call.keywords and isinstance(call.args[0], ast.Dict)):
        return result(False, "cache_key_not_built_by_owned_fingerprint")
    mapping = call.args[0]
    keys = [key.value if isinstance(key, ast.Constant) and isinstance(key.value, str) else None for key in mapping.keys]
    if None in keys or len(keys) != len(set(keys)):
        return result(False, "cache_inputs_not_explicit_and_unique")
    required = {"engine_signature", "public_export_chain_files", "public_export_chain_symbols"}
    if not required.issubset(keys):
        return result(False, "signature_or_public_contract_cache_inputs_missing")
    value = mapping.values[keys.index("engine_signature")]
    if not isinstance(value, ast.Name) or value.id != "DEAD_CODE_ENGINE_SIGNATURE":
        return result(False, "cache_key_uses_another_signature")

    def cache_entry_key(node: ast.AST) -> bool:
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "str" and len(node.args) == 1 and not node.keywords):
            return False
        get = node.args[0]
        return (isinstance(get, ast.Call) and isinstance(get.func, ast.Attribute)
                and isinstance(get.func.value, ast.Name) and get.func.value.id == "cached_entry"
                and get.func.attr == "get" and len(get.args) in (1, 2) and not get.keywords
                and isinstance(get.args[0], ast.Constant) and get.args[0].value == "cache_key"
                and (len(get.args) == 1 or isinstance(get.args[1], ast.Constant) and get.args[1].value == ""))

    def mandatory_key_match(node: ast.AST) -> bool:
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.And):
            return any(mandatory_key_match(value) for value in node.values)
        if not (isinstance(node, ast.Compare) and len(node.ops) == len(node.comparators) == 1
                and isinstance(node.ops[0], ast.Eq)):
            return False
        left, right = node.left, node.comparators[0]
        return ((isinstance(left, ast.Name) and left.id == "project_cache_key" and cache_entry_key(right))
                or (isinstance(right, ast.Name) and right.id == "project_cache_key" and cache_entry_key(left)))

    consumers = [node for node in nodes if isinstance(node, ast.If) and mandatory_key_match(node.test)
                 and any(isinstance(statement, ast.Assign) and len(statement.targets) == 1
                         and isinstance(statement.targets[0], ast.Name) and statement.targets[0].id == "analysis"
                         and isinstance(statement.value, ast.Name) and statement.value.id == "cached_result"
                         for statement in node.body)]
    wired = False
    for owner in nodes:
        if owner is not run and isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        for _, suite in ast.iter_fields(owner):
            if isinstance(suite, list) and assignment in suite:
                wired = wired or any(consumer in suite and suite.index(assignment) < suite.index(consumer)
                                     for consumer in consumers)
    if not wired:
        return result(False, "cache_hit_not_guarded_by_the_actual_key")
    probe = {key: "cache_binding_probe" for key in keys}
    probe["engine_signature"] = signature
    try:
        current = DeadCodeDetector._stable_fingerprint(probe)
        changed = DeadCodeDetector._stable_fingerprint({**probe, "engine_signature": signature + "_probe"})
    except (TypeError, ValueError, RuntimeError) as error:
        return {**result(False, "fingerprint_probe_failed"), "error_type": type(error).__name__}
    if not isinstance(current, str) or not current or not isinstance(changed, str) or not changed or current == changed:
        return result(False, "fingerprint_ignores_signature_changes")
    return result(True, "versioned_signature_bound_to_public_contract_cache_key_and_hit")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _public_export_chain_fixture() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="nexora_dead_code_public_") as tmp:
        root = Path(tmp)
        _write(
            root / "package.json",
            json.dumps(
                {
                    "name": "@fixture/public-contract",
                    "exports": {"." : "./src/index.ts"},
                    "types": "./src/index.ts",
                },
                indent=2,
            ),
        )
        _write(root / "src" / "index.ts", "export * from './components';\n")
        _write(root / "src" / "components" / "index.ts", "export * from './Button';\n")
        _write(root / "src" / "components" / "Button.ts", "export function Button() { return null; }\n")

        detector = DeadCodeDetector()
        detector.projects = {"FIXTURE": root}
        files = {
            "src/index.ts": {
                "exports": [
                    {"type": "ProxyExport", "name": "proxy:src/components/index.ts", "moduleSpecifier": "./components"}
                ]
            },
            "src/components/index.ts": {
                "exports": [
                    {"type": "ProxyExport", "name": "proxy:src/components/Button.ts", "moduleSpecifier": "./Button"}
                ]
            },
            "src/components/Button.ts": {
                "exports": [{"type": "Function", "name": "Button"}],
                "symbols": [{"name": "Button", "type": "Function", "exported": True}],
            },
        }
        public_contracts = build_package_public_contracts(root, [root / "package.json"])
        public_files, public_symbols = detector._public_export_chain_surfaces("FIXTURE", files, public_contracts)
        return {
            "public_files": sorted(public_files),
            "public_symbols": sorted(f"{path}::{symbol}" for path, symbol in public_symbols),
            "deep_symbol_protected": "src/components/Button.ts" in public_files
            or ("src/components/Button.ts", "Button") in public_symbols,
        }


def _named_public_reexport_fixture() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="nexora_dead_code_named_public_") as tmp:
        root = Path(tmp)
        _write(root / "package.json", json.dumps({"exports": "./src/index.ts"}, indent=2))
        _write(root / "src" / "index.ts", "export { Button } from './Button';\n")
        _write(root / "src" / "Button.ts", "export function Button() { return null; }\n")

        detector = DeadCodeDetector()
        detector.projects = {"FIXTURE": root}
        files = {
            "src/index.ts": {
                "exports": [
                    {
                        "type": "ReExportedSymbol",
                        "name": "Button",
                        "moduleSpecifier": "./Button",
                        "dependencies": ["Button"],
                        "exportedNames": ["Button"],
                    }
                ]
            },
            "src/Button.ts": {
                "exports": [{"type": "Function", "name": "Button"}],
                "symbols": [{"name": "Button", "type": "Function", "exported": True}],
            },
        }
        public_contracts = build_package_public_contracts(root, [root / "package.json"])
        public_files, public_symbols = detector._public_export_chain_surfaces("FIXTURE", files, public_contracts)
        return {
            "public_files": sorted(public_files),
            "public_symbols": sorted(f"{path}::{symbol}" for path, symbol in public_symbols),
            "named_symbol_protected": ("src/Button.ts", "Button") in public_symbols,
        }


def _wildcard_public_subpath_fixture() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="nexora_dead_code_wildcard_public_") as tmp:
        root = Path(tmp)
        _write(
            root / "package.json",
            json.dumps(
                {
                    "name": "@fixture/wildcard",
                    "exports": {"./*": {"types": "./*.d.ts", "default": "./*.js"}},
                },
                indent=2,
            ),
        )
        _write(root / "src" / "traditional.ts", "export function createWithEqualityFn() {}\n")
        public_contracts = build_package_public_contracts(root, [root / "package.json"])
        detector = DeadCodeDetector()
        detector.projects = {"FIXTURE": root}
        return {
            "entry_patterns": public_contracts.get("entry_patterns", []),
            "protected": detector._is_package_public_export_surface(
                "FIXTURE",
                "src/traditional.ts",
                {"public_contracts": public_contracts},
            ),
        }


def _typescript_declaration_fixture() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="nexora_dead_code_aug_") as tmp:
        root = Path(tmp)
        _write(
            root / "src" / "theme.d.ts",
            "declare module '@mui/material/styles' { interface Theme { sidebarWidth: number } }\n",
        )
        detector = DeadCodeDetector()
        detector.projects = {"FIXTURE": root}
        detector._file_content_cache[("FIXTURE", "src/theme.d.ts")] = (
            root / "src" / "theme.d.ts"
        ).read_text(encoding="utf-8")
        match = detector._match_contract_registry("FIXTURE", "src/theme.d.ts", "Theme")
        return {
            "matched": bool(match),
            "match": match or {},
            "reason": (match or {}).get("reason"),
        }


def run_validation() -> dict[str, Any]:
    dead_code_text = _read(CODE_MAPS_DIR / "tools" / "engines" / "dead_code_detector.py")
    doctrine_text = _read(CODE_MAPS_DIR / "config" / "architecture_doctrine.json")
    artifact_payload = load_json_file(RAW_DIR / "dead_code.json", {})
    meta = artifact_payload.get("meta", {}) if isinstance(artifact_payload, dict) else {}

    signature_binding = _engine_signature_cache_binding(dead_code_text, DEAD_CODE_ENGINE_SIGNATURE)

    star_fixture = _public_export_chain_fixture()
    named_fixture = _named_public_reexport_fixture()
    wildcard_fixture = _wildcard_public_subpath_fixture()
    declaration_fixture = _typescript_declaration_fixture()

    checks = [
        _check(
            "dead_code_engine_signature_tracks_public_contract_logic",
            signature_binding["passed"],
            signature_binding,
        ),
        _check(
            "package_exports_are_scanned_for_public_surfaces",
            "_package_public_export_patterns" in dead_code_text
            and "public_contracts" in dead_code_text
            and ".rglob(" not in dead_code_text,
            "tools/engines/dead_code_detector.py",
        ),
        _check(
            "deep_star_barrel_export_chain_protects_deep_symbol",
            bool(star_fixture["deep_symbol_protected"]),
            star_fixture,
        ),
        _check(
            "named_reexport_chain_protects_named_symbol",
            bool(named_fixture["named_symbol_protected"]),
            named_fixture,
        ),
        _check(
            "wildcard_package_subpath_projects_to_existing_source",
            bool(wildcard_fixture["protected"]),
            wildcard_fixture,
        ),
        _check(
            "typescript_declaration_and_augmentation_contracts_are_doctrine_driven",
            "typescript_declaration_contracts" in doctrine_text
            and "typescript_augmentation_contracts" in doctrine_text
            and bool(declaration_fixture["matched"]),
            declaration_fixture,
        ),
        _check(
            "dead_code_artifact_exposes_contract_exclusion_channels",
            isinstance(artifact_payload, dict)
            and "compatibility_exclusions" in artifact_payload
            and "contract_registry_exclusions" in artifact_payload
            and "type_only_export_exclusions" in artifact_payload
            and "classification_reason_counts" in meta,
            {
                "artifact": "output/.raw/dead_code.json",
                "meta_keys": sorted(meta.keys()),
                "contract_registry_exclusions": len(artifact_payload.get("contract_registry_exclusions", []) if isinstance(artifact_payload, dict) else []),
            },
        ),
    ]

    summary = {
        "status": "PASS" if all(check["passed"] for check in checks) else "FAIL",
        "total_checks": len(checks),
        "passed_checks": sum(1 for check in checks if check.get("passed")),
        "failed_checks": sum(1 for check in checks if not check.get("passed")),
        "generated_at": _utc_now(),
    }
    payload = {
        "meta": {
            "kind": "dead_code_public_contract_validation",
            "version": "v1",
            "generator": "tools.validate_dead_code_public_contracts",
        },
        "summary": summary,
        "checks": checks,
    }
    save_json_atomic(RAW_OUTPUT_PATH, payload)

    lines = [
        "# Dead Code Public Contract Validation",
        "",
        f"- Status: `{summary['status']}`",
        f"- Total checks: `{summary['total_checks']}`",
        f"- Passed: `{summary['passed_checks']}`",
        f"- Failed: `{summary['failed_checks']}`",
        "",
        "| Check | Result | Details |",
        "|---|---|---|",
    ]
    for check in checks:
        details = json.dumps(check.get("details"), ensure_ascii=False)
        details = details.replace("|", "\\|")
        lines.append(f"| `{check['name']}` | {'PASS' if check['passed'] else 'FAIL'} | {details} |")
    save_text_atomic(REPORT_OUTPUT_PATH, "\n".join(lines) + "\n")
    return payload


def main() -> int:
    payload = run_validation()
    print(json.dumps(payload.get("summary", {}), ensure_ascii=False))
    return 0 if payload.get("summary", {}).get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
