from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Acquisition:
    file: str
    function: str
    method: str
    source_expression: str
    line: int

    @property
    def key(self) -> str:
        return "|".join((self.file, self.function, self.method, self.source_expression))


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        prefix = _expression(func.value)
        return f"{prefix}.{func.attr}" if prefix else func.attr
    return ""


def _expression(node: ast.AST) -> str:
    try:
        return ast.unparse(node).strip()
    except (AttributeError, ValueError):
        return "<unavailable>"


def _source_expression(node: ast.Call, method: str) -> str:
    if method in {"read_text", "read_bytes"} and isinstance(node.func, ast.Attribute):
        return _expression(node.func.value)
    if node.args:
        return _expression(node.args[0])
    return "<missing>"


class _AcquisitionVisitor(ast.NodeVisitor):
    def __init__(self, file: str, methods: set[str]) -> None:
        self.file = file
        self.methods = methods
        self.scope_stack: list[str] = []
        self.items: list[Acquisition] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scope_stack.append(node.name)
        self.generic_visit(node)
        self.scope_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.scope_stack.append(node.name)
        self.generic_visit(node)
        self.scope_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.scope_stack.append(node.name)
        self.generic_visit(node)
        self.scope_stack.pop()

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self.scope_stack.append(f"<lambda@{int(getattr(node, 'lineno', 0) or 0)}>")
        self.generic_visit(node)
        self.scope_stack.pop()

    def visit_Call(self, node: ast.Call) -> None:
        full_name = _call_name(node)
        leaf_name = full_name.rsplit(".", 1)[-1]
        matched = full_name if full_name in self.methods else leaf_name if leaf_name in self.methods else ""
        if matched:
            self.items.append(
                Acquisition(
                    file=self.file,
                    function=".".join(self.scope_stack) or "<module>",
                    method=matched,
                    source_expression=_source_expression(node, matched),
                    line=int(getattr(node, "lineno", 0) or 0),
                )
            )
        self.generic_visit(node)


def acquisitions_from_source(source: str, *, file: str, methods: set[str]) -> list[Acquisition]:
    tree = ast.parse(source, filename=file)
    visitor = _AcquisitionVisitor(file, methods)
    visitor.visit(tree)
    return visitor.items


def repeated_acquisitions(
    root: Path,
    *,
    scan_roots: list[str],
    exclude_path_prefixes: list[str],
    methods: set[str],
    minimum_occurrences: int,
) -> tuple[list[dict], list[dict]]:
    groups: dict[str, list[Acquisition]] = {}
    parse_errors: list[dict] = []
    excluded = tuple(prefix.rstrip("/") + "/" for prefix in exclude_path_prefixes)
    seen: set[Path] = set()
    for scan_root in scan_roots:
        for path in sorted((root / scan_root).rglob("*.py")):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            rel = path.relative_to(root).as_posix()
            if any(rel.startswith(prefix) for prefix in excluded):
                continue
            try:
                source = path.read_text(encoding="utf-8", errors="replace")
                items = acquisitions_from_source(source, file=rel, methods=methods)
            except SyntaxError as exc:
                parse_errors.append({"file": rel, "line": exc.lineno or 0, "error": str(exc)})
                continue
            for item in items:
                groups.setdefault(item.key, []).append(item)
    candidates = []
    for key, items in sorted(groups.items()):
        if len(items) < minimum_occurrences:
            continue
        first = items[0]
        candidates.append(
            {
                "key": key,
                "file": first.file,
                "function": first.function,
                "method": first.method,
                "source_expression": first.source_expression,
                "occurrences": len(items),
                "lines": [item.line for item in items],
            }
        )
    return candidates, parse_errors
