import os
import json
import re
import sys
import ast
import hashlib
import tokenize
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.core.language_registry import extensions_for_language

PARSER_VERSION = f"{sys.version_info.major}.{sys.version_info.minor}"


def _annotation_text(node):
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:
        return ast.dump(node, include_attributes=False)


def _function_signature(node, symbol_type):
    positional = [*getattr(node.args, "posonlyargs", []), *node.args.args]
    params = [
        f"{arg.arg}:{_annotation_text(arg.annotation) or '?'}"
        for arg in positional
    ]
    if node.args.vararg:
        params.append(f"*{node.args.vararg.arg}:{_annotation_text(node.args.vararg.annotation) or '?'}")
    params.extend(f"{arg.arg}:{_annotation_text(arg.annotation) or '?'}" for arg in node.args.kwonlyargs)
    if node.args.kwarg:
        params.append(f"**{node.args.kwarg.arg}:{_annotation_text(node.args.kwarg.annotation) or '?'}")
    async_tag = "async:" if isinstance(node, ast.AsyncFunctionDef) else ""
    return f"{symbol_type.lower()}:{async_tag}{node.name}({','.join(params)})->{_annotation_text(node.returns) or '?'}"


def _normalized_body_dna(body):
    normalized = ast.Module(body=list(body), type_ignores=[])
    payload = ast.dump(normalized, annotate_fields=True, include_attributes=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _python_identity(symbol, *, logic_dna, semantic_signature):
    symbol.update({
        "logicDna": logic_dna,
        "semanticSignature": semantic_signature,
        "normalizationProfile": "python_ast_v1",
        "semanticDepth": "ast_normalized",
        "logicDnaKind": "normalized_ast_body",
        "normalizationConfidence": "high",
        "parserKind": "python_ast",
        "parserVersion": PARSER_VERSION,
    })
    return symbol

def _import_scope(node, parents):
    return "top_level" if isinstance(parents.get(node), ast.Module) else "local"

def sequence_python_file_with_evidence(file_path: str):
    """
    Python symbol extraction plus run-scoped parser evidence.

    Declared Python AST capability is not proof that this particular file was
    parsed successfully. Completeness claims must consume this evidence rather
    than infer success from an empty symbol list.
    """
    try:
        with tokenize.open(file_path) as f:
            content = f.read()
    except Exception as exc:
        return {
            "symbols": [],
            "status": "unavailable",
            "parser_kind": "unavailable",
            "semantic_depth": "unavailable",
            "error_family": "source_read_error",
            "error_type": type(exc).__name__,
        }

    symbols = []
    
    try:
        tree = ast.parse(content)
        parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # [Surgical] Reference extraction
                refs = set()
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call):
                        if isinstance(sub.func, ast.Name): refs.add(sub.func.id)
                        elif isinstance(sub.func, ast.Attribute): refs.add(sub.func.attr)
                
                # [Surgical] API Route extraction
                features = []
                for deco in node.decorator_list:
                    d_name = ""
                    if isinstance(deco, ast.Call) and isinstance(deco.func, ast.Attribute): d_name = deco.func.attr
                    elif isinstance(deco, ast.Call) and isinstance(deco.func, ast.Name): d_name = deco.func.id
                    elif isinstance(deco, ast.Name): d_name = deco.id
                    
                    if d_name.lower() in ["get", "post", "put", "patch", "delete", "route", "api_view"]:
                        features.append(f"api_endpoint:{d_name.upper()}")
                        if isinstance(deco, ast.Call) and deco.args:
                            arg = deco.args[0]
                            if isinstance(arg, ast.Constant): features.append(f"route_path:{arg.value}")
                            elif isinstance(arg, ast.Str): features.append(f"route_path:{arg.s}")

                parent_node = parents.get(node)
                if isinstance(parent_node, ast.ClassDef):
                    symbol_type = "Method"
                elif isinstance(parent_node, ast.Module):
                    symbol_type = "Function"
                else:
                    symbol_type = "NestedFunction"
                symbols.append(_python_identity({
                    "name": node.name,
                    "type": symbol_type,
                    "signature": _function_signature(node, symbol_type),
                    "dna": hashlib.sha1(ast.dump(node).encode()).hexdigest()[:16],
                    "start": node.lineno,
                    "end": getattr(node, "end_lineno", node.lineno),
                    "features": features,
                    "symbols_referenced": sorted(list(refs)),
                    "exported": symbol_type == "Function" and not node.name.startswith("_")
                }, logic_dna=_normalized_body_dna(node.body), semantic_signature=_function_signature(node, symbol_type)))
            elif isinstance(node, ast.ClassDef):
                bases = []
                for base in node.bases:
                    if isinstance(base, ast.Name):
                        bases.append(base.id)
                    elif isinstance(base, ast.Attribute):
                        parts = []
                        curr = base
                        while isinstance(curr, ast.Attribute):
                            parts.append(curr.attr)
                            curr = curr.value
                        if isinstance(curr, ast.Name):
                            parts.append(curr.id)
                        bases.append(".".join(reversed(parts)))
                
                members = []
                member_details = []
                for sub_node in node.body:
                    if isinstance(sub_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        method_name = sub_node.name
                        members.append(method_name)
                        
                        # Extract references
                        sub_refs = set()
                        for inner in ast.walk(sub_node):
                            if isinstance(inner, ast.Call):
                                if isinstance(inner.func, ast.Name): sub_refs.add(inner.func.id)
                                elif isinstance(inner.func, ast.Attribute): sub_refs.add(inner.func.attr)
                        
                        is_static = False
                        is_class = False
                        for deco in sub_node.decorator_list:
                            if isinstance(deco, ast.Name):
                                if deco.id == "staticmethod":
                                    is_static = True
                                elif deco.id == "classmethod":
                                    is_class = True
                        
                        kind = "method"
                        if is_static:
                            kind = "staticmethod"
                        elif is_class:
                            kind = "classmethod"
                            
                        member_details.append({
                            "name": method_name,
                            "kind": kind,
                            "signature": f"def {method_name}",
                            "static": is_static or is_class,
                            "dependencies": sorted(list(sub_refs))
                        })

                class_signature = f"class:{node.name}({','.join(bases)})"
                symbols.append(_python_identity({
                    "name": node.name,
                    "type": "Class",
                    "signature": class_signature,
                    "dna": hashlib.sha1(ast.dump(node).encode()).hexdigest()[:16],
                    "start": node.lineno,
                    "end": getattr(node, "end_lineno", node.lineno),
                    "features": [],
                    "symbols_referenced": [],
                    "exported": not node.name.startswith("_"),
                    "extends": bases,
                    "implements": [],
                    "members": members,
                    "memberDetails": member_details
                }, logic_dna=_normalized_body_dna(node.body), semantic_signature=class_signature))
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id.isupper():
                        signature = f"constant:{target.id}:{type(node.value).__name__}"
                        symbols.append(_python_identity({
                            "name": target.id,
                            "type": "Variable",
                            "signature": signature,
                            "dna": hashlib.sha1(ast.dump(node).encode()).hexdigest()[:16],
                            "start": node.lineno,
                            "end": getattr(node, "end_lineno", node.lineno),
                            "features": ["CONSTANT"],
                            "symbols_referenced": [],
                            "exported": not target.id.startswith("_")
                        }, logic_dna=hashlib.sha256(ast.dump(node.value, include_attributes=False).encode()).hexdigest(), semantic_signature=signature))
            elif isinstance(node, ast.Import):
                import_scope = _import_scope(node, parents)
                for alias in node.names:
                    name = alias.asname if alias.asname else alias.name
                    signature = f"import:{alias.name}"
                    symbols.append(_python_identity({
                        "name": name,
                        "type": "Import",
                        "signature": signature,
                        "dna": hashlib.sha1(f"Import:{alias.name}:{node.lineno}".encode()).hexdigest()[:16],
                        "start": node.lineno,
                        "end": getattr(node, "end_lineno", node.lineno),
                        "features": [f"source:{alias.name}", f"import_scope:{import_scope}"],
                        "symbols_referenced": [alias.name],
                        "importScope": import_scope,
                        "exported": False
                    }, logic_dna=hashlib.sha256(signature.encode()).hexdigest(), semantic_signature=signature))
            elif isinstance(node, ast.ImportFrom):
                module = node.module if node.module else ""
                dots = "." * (node.level or 0)
                full_module = dots + module
                import_scope = _import_scope(node, parents)
                for alias in node.names:
                    name = alias.asname if alias.asname else alias.name
                    orig_name = alias.name
                    signature = f"import:{full_module}:{orig_name}"
                    symbols.append(_python_identity({
                        "name": name,
                        "type": "Import",
                        "signature": signature,
                        "dna": hashlib.sha1(f"ImportFrom:{full_module}:{orig_name}:{node.lineno}".encode()).hexdigest()[:16],
                        "start": node.lineno,
                        "end": getattr(node, "end_lineno", node.lineno),
                        "features": [f"source:{full_module}", f"import_scope:{import_scope}"],
                        "symbols_referenced": [f"{full_module}.{orig_name}" if full_module else orig_name],
                        "importScope": import_scope,
                        "exported": False
                    }, logic_dna=hashlib.sha256(signature.encode()).hexdigest(), semantic_signature=signature))
    except Exception as exc:
        return {
            "symbols": _sequence_python_regex(content),
            "status": "degraded",
            "parser_kind": "regex_fallback",
            "semantic_depth": "unavailable",
            "error_family": "python_ast_processing_error",
            "error_type": type(exc).__name__,
        }

    return {
        "symbols": symbols,
        "status": "observed",
        "parser_kind": "python_ast",
        "semantic_depth": "ast_normalized",
        "error_family": None,
        "error_type": None,
    }


def sequence_python_file(file_path: str):
    """Compatibility symbol-list view for existing deterministic consumers."""
    return sequence_python_file_with_evidence(file_path)["symbols"]

def _sequence_python_regex(content):
    symbols = []
    class_pattern = re.compile(r'class\s+([A-Za-z0-9_]+)', re.MULTILINE)
    func_pattern = re.compile(r'(async\s+)?def\s+([A-Za-z0-9_]+)\s*\(', re.MULTILINE)
    
    clean_content = re.sub(r'#.*', '', content)
    
    for match in class_pattern.finditer(clean_content):
        name = match.group(1)
        line = clean_content.count('\n', 0, match.start()) + 1
        symbols.append({
            "name": name,
            "type": "Class",
            "signature": f"class {name}",
            "dna": hashlib.sha1(f"Class:{name}:{line}".encode()).hexdigest()[:16],
            "start": line,
            "end": line,
            "features": [],
            "symbols_referenced": [],
            "exported": not name.startswith("_"),
            "logicDna": "",
            "semanticSignature": f"class {name}",
            "normalizationProfile": "python_regex_fallback_v1",
            "semanticDepth": "unavailable",
            "logicDnaKind": "unavailable",
            "normalizationConfidence": "none",
            "parserKind": "regex_fallback",
            "parserVersion": "v1"
        })
    for match in func_pattern.finditer(clean_content):
        name = match.group(2)
        line = clean_content.count('\n', 0, match.start()) + 1
        symbols.append({
            "name": name,
            "type": "Function",
            "signature": f"def {name}",
            "dna": hashlib.sha1(f"Function:{name}:{line}".encode()).hexdigest()[:16],
            "start": line,
            "end": line,
            "features": [],
            "symbols_referenced": [],
            "exported": not name.startswith("_"),
            "logicDna": "",
            "semanticSignature": f"def {name}",
            "normalizationProfile": "python_regex_fallback_v1",
            "semanticDepth": "unavailable",
            "logicDnaKind": "unavailable",
            "normalizationConfidence": "none",
            "parserKind": "regex_fallback",
            "parserVersion": "v1"
        })
    return symbols

def main():
    if len(sys.argv) < 3:
        return
    project_name = sys.argv[1]
    project_root = Path(sys.argv[2])
    atlas_data = {project_name: {"files": {}}}
    python_extensions = extensions_for_language("python")
    for root, dirs, files in os.walk(str(project_root)):
        for file in files:
            if file.endswith(python_extensions):
                abs_path = Path(root) / file
                rel_path = abs_path.relative_to(project_root).as_posix()
                atlas_data[project_name]["files"][rel_path] = sequence_python_file_with_evidence(str(abs_path))
    print(json.dumps(atlas_data, indent=2))

if __name__ == "__main__":
    main()
