import os
import json
import re
import sys
from pathlib import Path

def sequence_go_file_with_evidence(file_path: str):
    """
    Go Symbol Extractor.
    Extracts Packages, Structs, Interfaces, Functions, and Methods (with receivers).
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as exc:
        return {
            "symbols": [], "status": "unavailable", "parser_kind": "unavailable",
            "semantic_depth": "unavailable", "error_family": "source_read_error",
            "error_type": type(exc).__name__,
        }

    symbols = []
    
    # [Architect Logic] Go Symbol Patterns
    # 1. Package
    pkg_pattern = re.compile(r'package\s+([A-Za-z0-9_]+)', re.MULTILINE)
    
    # 2. Struct/Interface definitions
    # Pattern: type <name> struct/interface
    type_pattern = re.compile(r'type\s+([A-Za-z0-9_]+)\s+(struct|interface)', re.MULTILINE)
    
    # 3. Functions
    # Pattern: func <name>(args) <returnType>
    func_pattern = re.compile(r'func\s+([A-Za-z0-9_]+)\s*\(([^\)]*)\)\s*([^\s\{]*)', re.MULTILINE)
    
    # 4. Methods (with receivers)
    # Pattern: func (r *Receiver) <name>(args) <returnType>
    method_pattern = re.compile(r'func\s*\(([^\)]+)\)\s+([A-Za-z0-9_]+)\s*\(([^\)]*)\)\s*([^\s\{]*)', re.MULTILINE)

    def strip_comments(text):
        text = re.sub(r'//.*', '', text)
        text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
        return text

    clean_content = strip_comments(content)

    # Package
    for match in pkg_pattern.finditer(clean_content):
        name = match.group(1)
        symbols.append({
            "name": name,
            "kind": "Package",
            "detail": f"package {name}",
            "location": {"line": clean_content.count('\n', 0, match.start()) + 1}
        })

    # Types (Structs/Interfaces)
    for match in type_pattern.finditer(clean_content):
        name = match.group(1)
        kind = match.group(2)
        symbols.append({
            "name": name,
            "kind": kind.capitalize(),
            "detail": f"type {name} {kind}",
            "location": {"line": clean_content.count('\n', 0, match.start()) + 1}
        })

    # Methods (Higher priority than functions to avoid double matching)
    seen_methods = set()
    for match in method_pattern.finditer(clean_content):
        receiver = match.group(1).strip()
        name = match.group(2)
        args = match.group(3)
        ret_type = match.group(4)
        
        seen_methods.add(match.start())
        symbols.append({
            "name": name,
            "kind": "Method",
            "detail": f"func ({receiver}) {name}({args}) {ret_type}",
            "location": {"line": clean_content.count('\n', 0, match.start()) + 1}
        })

    # Functions
    for match in func_pattern.finditer(clean_content):
        if match.start() in seen_methods:
            continue
            
        name = match.group(1)
        args = match.group(2)
        ret_type = match.group(3)
        
        symbols.append({
            "name": name,
            "kind": "Function",
            "detail": f"func {name}({args}) {ret_type}",
            "location": {"line": clean_content.count('\n', 0, match.start()) + 1}
        })

    for symbol in symbols:
        symbol.update({
            "semanticSignature": symbol.get("detail", symbol.get("name", "")),
            "normalizationProfile": "go_structural_v1",
            "semanticDepth": "signature_only",
            "logicDnaKind": "structural_signature",
            "normalizationConfidence": "low",
            "parserKind": "regex_structural",
            "parserVersion": "v1",
        })
    return {
        "symbols": symbols, "status": "observed", "parser_kind": "regex_structural",
        "semantic_depth": "signature_only", "error_family": None, "error_type": None,
    }


def sequence_go_file(file_path: str):
    """Compatibility symbol-list view for existing deterministic consumers."""
    return sequence_go_file_with_evidence(file_path)["symbols"]

def main():
    if len(sys.argv) < 3:
        return
    project_name = sys.argv[1]
    project_root = Path(sys.argv[2])
    atlas_data = {project_name: {"files": {}}}
    for root, dirs, files in os.walk(str(project_root)):
        for file in files:
            if file.endswith(".go"):
                abs_path = Path(root) / file
                rel_path = abs_path.relative_to(project_root).as_posix()
                atlas_data[project_name]["files"][rel_path] = sequence_go_file_with_evidence(str(abs_path))
    print(json.dumps(atlas_data, indent=2))

if __name__ == "__main__":
    main()
