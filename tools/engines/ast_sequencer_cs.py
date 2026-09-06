import os
import json
import re
import sys
from pathlib import Path

def sequence_cs_file_with_evidence(file_path: str):
    """
    C# Symbol Extractor.
    Extracts Namespaces, Classes, Interfaces, Structs, Methods, and Properties.
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
    
    # [Architect Logic] C# Symbol Patterns
    # 1. Namespace
    ns_pattern = re.compile(r'namespace\s+([A-Za-z0-9_\.]+)', re.MULTILINE)
    
    # 2. Class/Interface/Struct/Enum
    type_pattern = re.compile(r'(public|protected|private|internal)?\s*(static)?\s*(partial)?\s*(abstract|sealed)?\s*(class|interface|struct|enum)\s+([A-Za-z0-9_]+)', re.MULTILINE)
    
    # 3. Method declarations
    # Pattern: [visibility] [modifiers] <returnType> <name>(args)
    method_pattern = re.compile(r'(public|protected|private|internal)?\s*(static)?\s*(async)?\s*(virtual|override|abstract|sealed)?\s*([\w\<\>\[\]]+)\s+([A-Za-z0-9_]+)\s*\(([^\)]*)\)', re.MULTILINE)
    
    # 4. Properties (Simplified)
    prop_pattern = re.compile(r'(public|protected|private|internal)?\s*(static)?\s*([\w\<\>\[\]]+)\s+([A-Za-z0-9_]+)\s*\{\s*(get|set)', re.MULTILINE)

    def strip_comments(text):
        text = re.sub(r'//.*', '', text)
        text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
        return text

    clean_content = strip_comments(content)

    # Namespaces
    for match in ns_pattern.finditer(clean_content):
        name = match.group(1)
        symbols.append({
            "name": name,
            "kind": "Namespace",
            "detail": f"namespace {name}",
            "location": {"line": clean_content.count('\n', 0, match.start()) + 1}
        })

    # Types
    for match in type_pattern.finditer(clean_content):
        visibility = match.group(1) or "private"
        kind = match.group(5)
        name = match.group(6)
        symbols.append({
            "name": name,
            "kind": kind.capitalize(),
            "detail": f"{visibility} {kind} {name}",
            "location": {"line": clean_content.count('\n', 0, match.start()) + 1}
        })

    # Methods
    for match in method_pattern.finditer(clean_content):
        visibility = match.group(1) or "private"
        ret_type = match.group(5)
        name = match.group(6)
        args = match.group(7)
        
        if name in {"if", "for", "while", "switch", "return", "new", "class", "interface", "struct", "enum", "using"}:
            continue
            
        symbols.append({
            "name": name,
            "kind": "Method",
            "detail": f"{visibility} {ret_type} {name}({args})",
            "location": {"line": clean_content.count('\n', 0, match.start()) + 1}
        })

    # Properties
    for match in prop_pattern.finditer(clean_content):
        visibility = match.group(1) or "private"
        type_name = match.group(3)
        name = match.group(4)
        
        symbols.append({
            "name": name,
            "kind": "Property",
            "detail": f"{visibility} {type_name} {name} {{ get; set; }}",
            "location": {"line": clean_content.count('\n', 0, match.start()) + 1}
        })

    for symbol in symbols:
        symbol.update({
            "semanticSignature": symbol.get("detail", symbol.get("name", "")),
            "normalizationProfile": "csharp_structural_v1",
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


def sequence_cs_file(file_path: str):
    """Compatibility symbol-list view for existing deterministic consumers."""
    return sequence_cs_file_with_evidence(file_path)["symbols"]

def main():
    if len(sys.argv) < 3:
        return
    project_name = sys.argv[1]
    project_root = Path(sys.argv[2])
    atlas_data = {project_name: {"files": {}}}
    for root, dirs, files in os.walk(str(project_root)):
        for file in files:
            if file.endswith(".cs"):
                abs_path = Path(root) / file
                rel_path = abs_path.relative_to(project_root).as_posix()
                atlas_data[project_name]["files"][rel_path] = sequence_cs_file_with_evidence(str(abs_path))
    print(json.dumps(atlas_data, indent=2))

if __name__ == "__main__":
    main()
