import os
import json
import re
import sys
from pathlib import Path

def sequence_java_file_with_evidence(file_path: str):
    """
    Java Symbol Extractor (Hardened).
    Extracts Classes, Interfaces, Methods, and Internal References.
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
    
    # [Architect Logic] Java Symbol Patterns
    # 1. Class/Interface/Enum
    type_pattern = re.compile(r'(public|protected|private)?\s*(static|abstract|final)?\s*(class|interface|enum)\s+([A-Za-z0-9_]+)', re.MULTILINE)
    
    # 2. Methods
    method_pattern = re.compile(r'(public|protected|private|static|\s) +[\w\<\>\[\]]+\s+([A-Za-z0-9_]+)\s*\(([^\)]*)\)\s*\{', re.MULTILINE)

    def strip_comments(text):
        text = re.sub(r'//.*', '', text)
        text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
        return text

    clean_content = strip_comments(content)

    # Types
    for match in type_pattern.finditer(clean_content):
        kind = match.group(3)
        name = match.group(4)
        symbols.append({
            "name": name,
            "kind": kind.capitalize(),
            "detail": f"{kind} {name}",
            "location": {"line": clean_content.count('\n', 0, match.start()) + 1},
            "symbols_referenced": []
        })

    # Methods with Internal Reference Detection
    for match in method_pattern.finditer(clean_content):
        name = match.group(2)
        args = match.group(3)
        start_pos = match.end()
        
        # Simple brace matching to find method body
        brace_count = 1
        end_pos = start_pos
        for i in range(start_pos, len(clean_content)):
            if clean_content[i] == '{': brace_count += 1
            elif clean_content[i] == '}': brace_count -= 1
            if brace_count == 0:
                end_pos = i
                break
        
        method_body = clean_content[start_pos:end_pos]
        refs = re.findall(r'([A-Za-z0-9_]+)\(', method_body)
        
        symbols.append({
            "name": name,
            "kind": "Method",
            "detail": f"method {name}({args})",
            "location": {"line": clean_content.count('\n', 0, match.start()) + 1},
            "symbols_referenced": sorted(list(set(refs)))
        })

    for symbol in symbols:
        symbol.update({
            "semanticSignature": symbol.get("detail", symbol.get("name", "")),
            "normalizationProfile": "java_structural_v1",
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


def sequence_java_file(file_path: str):
    """Compatibility symbol-list view for existing deterministic consumers."""
    return sequence_java_file_with_evidence(file_path)["symbols"]

def main():
    if len(sys.argv) < 3:
        return
    project_name = sys.argv[1]
    project_root = Path(sys.argv[2])
    atlas_data = {project_name: {"files": {}}}
    for root, dirs, files in os.walk(str(project_root)):
        for file in files:
            if file.endswith(".java"):
                abs_path = Path(root) / file
                rel_path = abs_path.relative_to(project_root).as_posix()
                atlas_data[project_name]["files"][rel_path] = sequence_java_file_with_evidence(str(abs_path))
    print(json.dumps(atlas_data, indent=2))

if __name__ == "__main__":
    main()
