from __future__ import annotations

import ast
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.core.config import CODE_MAPS_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic


POLICY_PATH = CODE_MAPS_DIR / "config" / "exception_handling_policy.json"
LEGACY_POLICY_FIELDS = {
    "allowed_quiet_handlers",
    "allowed_quiet_handler_notes",
    "allowed_quiet_functions",
    "allowed_quiet_pass_only_functions",
    "allowed_quiet_pass_only_function_notes",
}


_AST_FINGERPRINT_IGNORED_FIELDS = frozenset({"type_params"})


def _canonical_ast_value(value: object) -> object:
    if isinstance(value, ast.AST):
        return {
            "node": type(value).__name__,
            "fields": {
                name: _canonical_ast_value(child)
                for name, child in ast.iter_fields(value)
                if name not in _AST_FINGERPRINT_IGNORED_FIELDS
            },
        }
    if isinstance(value, list):
        return [_canonical_ast_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return {"literal_type": type(value).__name__, "repr": repr(value)}


def _ast_fingerprint(value: ast.AST) -> str:
    normalized = json.dumps(
        _canonical_ast_value(value),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _string_set(value: object) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {str(item).strip() for item in value if str(item).strip()}


def _has_call_named(node: ast.AST, names: set[str]) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Name) and func.id in names:
                return True
            if isinstance(func, ast.Attribute) and func.attr in names:
                return True
    return False


def _has_observable_action(handler: ast.ExceptHandler, call_names: set[str], return_keys: set[str]) -> bool:
    if _has_call_named(handler, call_names):
        return True
    for child in ast.walk(handler):
        if isinstance(child, ast.Raise):
            return True
        if isinstance(child, ast.Return) and isinstance(child.value, ast.Dict):
            keys = {
                str(key.value)
                for key in child.value.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            }
            if keys & return_keys:
                return True
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
            if isinstance(child.func.value, ast.Name) and child.func.value.id == "logger":
                return True
    return False


def _is_generic_exception(handler: ast.ExceptHandler) -> bool:
    t = handler.type
    if t is None:
        return True
    if isinstance(t, ast.Name):
        return t.id in {"Exception", "BaseException"}
    if isinstance(t, ast.Tuple):
        return any(isinstance(item, ast.Name) and item.id in {"Exception", "BaseException"} for item in t.elts)
    return False


def _is_pass_only(handler: ast.ExceptHandler) -> bool:
    return len(handler.body) == 1 and isinstance(handler.body[0], ast.Pass)


def _exception_type_name(handler: ast.ExceptHandler) -> str:
    if handler.type is None:
        return "<bare>"
    return ast.unparse(handler.type)


def _handler_body_fingerprint(handler: ast.ExceptHandler) -> str:
    return _ast_fingerprint(ast.Module(body=handler.body, type_ignores=[]))


def _try_context_fingerprint(node: ast.Try) -> str:
    return _ast_fingerprint(node)


def _semantic_handler_identity(
    rel: str,
    function_name: str,
    handler: ast.ExceptHandler,
    try_context_fingerprint: str,
) -> dict[str, object]:
    return {
        "file": rel,
        "function": function_name,
        "exception_type": _exception_type_name(handler),
        "pass_only": _is_pass_only(handler),
        "body_fingerprint": _handler_body_fingerprint(handler),
        "try_context_fingerprint": try_context_fingerprint,
    }


def _semantic_handler_key(identity: dict[str, object]) -> str:
    return json.dumps(identity, sort_keys=True, separators=(",", ":"))


def _semantic_policy_records(value: object) -> tuple[list[dict[str, object]], list[str]]:
    required_identity_fields = {
        "file",
        "function",
        "exception_type",
        "pass_only",
        "body_fingerprint",
        "try_context_fingerprint",
    }
    records = value if isinstance(value, list) else []
    parsed: list[dict[str, object]] = []
    errors: list[str] = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            errors.append(f"semantic_handler_{index}_not_object")
            continue
        missing = sorted(field for field in required_identity_fields if field not in record)
        if missing:
            errors.append(f"semantic_handler_{index}_missing_{'_'.join(missing)}")
            continue
        if not str(record.get("purpose") or "").strip():
            errors.append(f"semantic_handler_{index}_missing_purpose")
            continue
        identity = {field: record[field] for field in required_identity_fields}
        parsed.append({"identity": identity, "purpose": str(record["purpose"])})
    keys = [_semantic_handler_key(record["identity"]) for record in parsed]
    if len(keys) != len(set(keys)):
        errors.append("semantic_handler_duplicate_identity")
    return parsed, errors


def _iter_exception_handlers(tree: ast.AST):
    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.function_stack: list[str] = []
            self.try_context_stack: list[str] = []
            self.handlers: list[tuple[ast.ExceptHandler, str, str]] = []

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self.function_stack.append(node.name)
            self.generic_visit(node)
            self.function_stack.pop()

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self.function_stack.append(node.name)
            self.generic_visit(node)
            self.function_stack.pop()

        def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
            function_name = self.function_stack[-1] if self.function_stack else "<module>"
            context_fingerprint = self.try_context_stack[-1] if self.try_context_stack else "<unknown_try_context>"
            self.handlers.append((node, function_name, context_fingerprint))
            self.generic_visit(node)

        def visit_Try(self, node: ast.Try) -> None:
            self.try_context_stack.append(_try_context_fingerprint(node))
            self.generic_visit(node)
            self.try_context_stack.pop()

    visitor = Visitor()
    visitor.visit(tree)
    return visitor.handlers


def _render_report(payload: dict) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# Exception Honesty Validation",
        "",
        f"- status: `{summary.get('status')}`",
        f"- contract_errors: `{summary.get('contract_errors')}`",
        f"- scan_roots: `{summary.get('scan_roots')}`",
        f"- handlers: `{summary.get('handlers')}`",
        f"- observed: `{summary.get('observed')}`",
        f"- allowed_quiet: `{summary.get('allowed_quiet')}`",
        f"- unobserved: `{summary.get('unobserved')}`",
        f"- blocking_unobserved_generic: `{summary.get('blocking_unobserved_generic')}`",
        f"- pass_only_handlers: `{summary.get('pass_only_handlers')}`",
        f"- blocking_unclassified_pass_only: `{summary.get('blocking_unclassified_pass_only')}`",
        "",
        "## Top Unobserved Handlers By File",
        "",
        "| File | Count |",
        "|---|---:|",
    ]
    for item in payload.get("top_unobserved_by_file", [])[:40]:
        lines.append(f"| `{item.get('file')}` | {item.get('count')} |")
    lines.extend(
        [
            "",
            "## Blocking Findings",
            "",
            "| File | Line | Generic |",
            "|---|---:|---|",
        ]
    )
    for item in payload.get("blocking", [])[:80]:
        lines.append(f"| `{item.get('file')}` | {item.get('line')} | `{item.get('generic_exception')}` |")
    lines.append("")
    return "\n".join(lines)


def validate_exception_honesty() -> dict:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    validation_contract = policy.get("validation_contract", {}) if isinstance(policy.get("validation_contract"), dict) else {}
    scan_roots = [CODE_MAPS_DIR / item for item in _string_set(validation_contract.get("scan_roots"))]
    observable_call_names = _string_set(validation_contract.get("observable_call_names"))
    observable_return_keys = _string_set(validation_contract.get("observable_return_keys"))
    generic_blocking_scope = str(validation_contract.get("generic_blocking_scope") or "")
    contract_errors = []
    if not scan_roots:
        contract_errors.append("missing_scan_roots")
    if not observable_call_names:
        contract_errors.append("missing_observable_call_names")
    if not observable_return_keys:
        contract_errors.append("missing_observable_return_keys")
    if generic_blocking_scope != "all_scan_roots":
        contract_errors.append("generic_blocking_scope_must_cover_all_scan_roots")
    legacy_policy_fields_present = sorted(LEGACY_POLICY_FIELDS & set(policy))
    if legacy_policy_fields_present:
        contract_errors.append("legacy_quiet_handler_policy_fields_present")
    semantic_policy_records, semantic_policy_errors = _semantic_policy_records(
        policy.get("allowed_quiet_semantic_handlers", [])
    )
    semantic_policy_keys = {
        _semantic_handler_key(record["identity"])
        for record in semantic_policy_records
    }
    semantic_policy_matches = Counter()
    contract_errors.extend(semantic_policy_errors)
    findings = []
    for root in scan_roots:
        for path in root.rglob("*.py"):
            rel = path.relative_to(CODE_MAPS_DIR).as_posix()
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
                tree = ast.parse(text, filename=rel)
            except SyntaxError as exc:
                line = int(exc.lineno or 0)
                contract_errors.append(f"syntax_error:{rel}:{line}")
                findings.append(
                    {
                        "file": rel,
                        "line": line,
                        "function": "<module>",
                        "status": "syntax_error",
                        "generic_exception": False,
                        "pass_only": False,
                        "semantic_identity": None,
                    }
                )
                continue
            for handler, function_name, try_context_fingerprint in _iter_exception_handlers(tree):
                semantic_identity = _semantic_handler_identity(rel, function_name, handler, try_context_fingerprint)
                semantic_key = _semantic_handler_key(semantic_identity)
                if _has_observable_action(handler, observable_call_names, observable_return_keys):
                    status = "observed"
                else:
                    status = "allowed_quiet" if semantic_key in semantic_policy_keys else "unobserved"
                if semantic_key in semantic_policy_keys:
                    semantic_policy_matches[semantic_key] += 1
                findings.append(
                    {
                        "file": rel,
                        "line": int(handler.lineno or 0),
                        "function": function_name,
                        "status": status,
                        "generic_exception": _is_generic_exception(handler),
                        "pass_only": _is_pass_only(handler),
                        "semantic_identity": semantic_identity,
                    }
                )
    blocking = [
        item
        for item in findings
        if item["status"] == "unobserved" and item["generic_exception"]
    ]
    blocking_pass_only = [item for item in findings if item["pass_only"] and item["status"] == "unobserved"]
    unmatched_semantic_policy = sorted(
        key for key in semantic_policy_keys if semantic_policy_matches.get(key, 0) != 1
    )
    if unmatched_semantic_policy:
        contract_errors.append("semantic_handler_policy_not_one_to_one")
    unobserved_by_file = Counter(
        str(item.get("file") or "")
        for item in findings
        if item.get("status") == "unobserved"
    )
    payload = {
        "meta": {"kind": "exception_honesty_validation", "version": "v1"},
        "summary": {
            "status": "PASS" if not blocking and not blocking_pass_only and not contract_errors else "FAIL",
            "contract_errors": contract_errors,
            "scan_roots": len(scan_roots),
            "generic_blocking_scope": generic_blocking_scope,
            "handlers": len(findings),
            "observed": sum(1 for item in findings if item["status"] == "observed"),
            "allowed_quiet": sum(1 for item in findings if item["status"] == "allowed_quiet"),
            "unobserved": sum(1 for item in findings if item["status"] == "unobserved"),
            "blocking_unobserved_generic": len(blocking),
            "pass_only_handlers": sum(1 for item in findings if item["pass_only"]),
            "blocking_unclassified_pass_only": len(blocking_pass_only),
            "legacy_quiet_handler_policy_fields_present": legacy_policy_fields_present,
            "semantic_handler_policy_records": len(semantic_policy_records),
            "semantic_handler_policy_unmatched_or_ambiguous": len(unmatched_semantic_policy),
        },
        "validation_contract": validation_contract,
        "principles": policy.get("principles", {}),
        "top_unobserved_by_file": [
            {"file": file, "count": count}
            for file, count in unobserved_by_file.most_common()
        ],
        "blocking": blocking + blocking_pass_only,
        "semantic_policy_unmatched_or_ambiguous": unmatched_semantic_policy,
        "findings": findings,
    }
    save_json_atomic(RAW_DIR / "exception_honesty_validation.json", payload)
    save_text_atomic(REPORTS_DIR / "exception_honesty_validation.md", _render_report(payload))
    return payload


if __name__ == "__main__":
    result = validate_exception_honesty()
    print(json.dumps(result["summary"], indent=2))
    if result.get("blocking"):
        print(json.dumps(result["blocking"][:20], indent=2))
    raise SystemExit(0 if result["summary"]["status"] == "PASS" else 1)
