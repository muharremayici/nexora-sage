"""Bounded, non-executing source wiring checks shared by Audit validators.

These checks admit unique direct named-import calls in one module-level
function. They do not prove reachability, runtime identity or policy legality.
"""
from __future__ import annotations

import ast


def _scope_nodes(body):
    pending = list(reversed(body))
    while pending:
        node = pending.pop()
        yield node
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            pending.extend(reversed(list(ast.iter_child_nodes(node))))


def _bindings(nodes):
    names = []
    for node in nodes:
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.append(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.extend(alias.asname or (alias.name.split('.')[0] if isinstance(node, ast.Import) else alias.name)
                         for alias in node.names)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.append(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            names.extend(node.names)
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
            names.append(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            names.append(node.rest)
    return names


class _Source:
    def __init__(self, text):
        try:
            self.tree = ast.parse(text)
        except (SyntaxError, ValueError, TypeError):
            self.tree = ast.Module(body=[], type_ignores=[])

    def function(self, name):
        found = [node for node in self.tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name]
        return found[0] if len(found) == 1 else None

    def unshadowed(self, owner, name):
        args = owner.args
        parameters = [a.arg for a in args.posonlyargs + args.args + args.kwonlyargs]
        parameters += [a.arg for a in (args.vararg, args.kwarg) if a is not None]
        return (_bindings(_scope_nodes(self.tree.body)).count(name) == 1
                and name not in parameters and name not in _bindings(_scope_nodes(owner.body)))

    def imported_call(self, function, module, symbol):
        owner = self.function(function)
        if owner is None:
            return False
        imports = [alias for node in self.tree.body if isinstance(node, ast.ImportFrom) and node.level == 0
                   and node.module == module for alias in node.names if alias.name == symbol]
        if len(imports) != 1 or any(isinstance(node, ast.ImportFrom) and any(a.name == '*' for a in node.names)
                                    for node in _scope_nodes(self.tree.body)):
            return False
        local = imports[0].asname or symbol
        if not self.unshadowed(owner, local):
            return False
        nodes = list(_scope_nodes(owner.body))
        return any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == local for node in nodes)

    def projection(self, function, expression):
        owner = self.function(function)
        if owner is None:
            return False
        body = list(owner.body)
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            body = body[1:]
        expected = ast.parse(expression, mode='eval').body
        callees = [n.func.id for n in ast.walk(expected) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
        return (len(body) == 1 and isinstance(body[0], ast.Return) and ast.dump(body[0].value) == ast.dump(expected)
                and all(self.function(name) is not None and self.unshadowed(owner, name) for name in callees))


def api_entry_source_contract(pipeline_policy: str, audit: str, mcp_governance: str) -> bool:
    """Current traced Audit calls and legacy MCP projection share one owner."""
    policy, audit_source, mcp = map(_Source, (pipeline_policy, audit, mcp_governance))
    capture = policy.function('capture_api_entry_policy')
    if capture is None:
        return False
    capture_nodes = list(_scope_nodes(capture.body))
    policy_key_read = any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == 'get'
                          and n.args and isinstance(n.args[0], ast.Constant) and n.args[0].value == 'api_entry_filenames'
                          for n in capture_nodes)
    fallback_read = any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'get_audit_policy'
                        for n in capture_nodes)
    return (policy_key_read and fallback_read and policy.function('get_audit_policy') is not None
            and policy.unshadowed(capture, 'get_audit_policy')
            and policy.projection('get_api_entry_filenames', "capture_api_entry_policy()['names']")
            and audit_source.imported_call('analyze_project', 'tools.core.pipeline_policy', 'capture_api_entry_policy')
            and audit_source.imported_call('analyze_project', 'tools.core.import_classifier', 'module_api_import_evidence')
            and mcp.imported_call('_collect_import_violations', 'tools.core.pipeline_policy', 'get_api_entry_filenames'))


def audit_layer_source_contract(audit: str, layer_resolver: str) -> bool:
    """Require traced layer calls and unchanged compatibility projections."""
    source, resolver = map(_Source, (audit, layer_resolver))
    return (source.imported_call('analyze_project', 'tools.core.layer_resolver', 'resolve_layer_evidence')
            and source.imported_call('analyze_project', 'tools.core.layer_resolver', 'layer_violation_evidence')
            and resolver.function('resolve_layer_evidence') is not None
            and resolver.function('layer_violation_evidence') is not None
            and resolver.projection('resolve_layer', "resolve_layer_evidence(rel_path)['layer']")
            and resolver.projection('is_violation', "layer_violation_evidence(source_layer, target_layer, language)['violated']"))
