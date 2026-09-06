from __future__ import annotations

import re


def _split_ts_named_members(blob: str) -> list[str]:
    return [part.strip() for part in str(blob or "").split(",") if part.strip()]


def _ts_named_members_have_runtime(blob: str) -> bool:
    for member in _split_ts_named_members(blob):
        if member.startswith("type "):
            continue
        return True
    return False


def _ts_import_clause_has_runtime(clause: str) -> bool:
    text = str(clause or "").strip()
    if not text or text.startswith("type "):
        return False
    if "{" in text and "}" in text:
        before_named = text.split("{", 1)[0].strip().rstrip(",").strip()
        if before_named:
            return True
        named_blob = text.split("{", 1)[1].rsplit("}", 1)[0]
        return _ts_named_members_have_runtime(named_blob)
    return True


def _ts_export_clause_has_runtime(clause: str) -> bool:
    text = str(clause or "").strip()
    if not text or text.startswith("type "):
        return False
    if text.startswith("*"):
        return True
    if "{" in text and "}" in text:
        named_blob = text.split("{", 1)[1].rsplit("}", 1)[0]
        return _ts_named_members_have_runtime(named_blob)
    return True


def strip_comments_for_import_scan(content: str) -> str:
    without_blocks = re.sub(r"/\*.*?\*/", "", content or "", flags=re.DOTALL)
    without_lines = re.sub(r"//.*?$", "", without_blocks, flags=re.MULTILINE)
    without_python_comments = re.sub(r"#.*?$", "", without_lines, flags=re.MULTILINE)
    return without_python_comments


def extract_imports(content: str, language: str) -> list[str]:
    """Extract language-aware import specifiers and normalize dot packages to slash paths."""
    imports: list[str] = []
    clean_content = strip_comments_for_import_scan(content)
    language = (language or "typescript").lower()

    def add(value: str):
        normalized = str(value or "").strip()
        if normalized and normalized not in imports:
            imports.append(normalized)

    if language in {"typescript", "javascript", "vue"}:
        matches = []
        for match in re.finditer(r"\bimport\s+([\s\S]*?)\s+from\s+['\"](.+?)['\"]", clean_content):
            if _ts_import_clause_has_runtime(match.group(1)):
                matches.append(match.group(2))
        for match in re.finditer(r"\bexport\s+([\s\S]*?)\s+from\s+['\"](.+?)['\"]", clean_content):
            if _ts_export_clause_has_runtime(match.group(1)):
                matches.append(match.group(2))
        matches.extend(re.findall(r"import\s+['\"](.+?)['\"]", clean_content))
        matches.extend(re.findall(r"require\(\s*['\"](.+?)['\"]\s*\)", clean_content))
        matches.extend(re.findall(r"import\(\s*['\"](.+?)['\"]\s*\)", clean_content))
        for match in matches:
            add(match)
    elif language == "python":
        for match in re.finditer(r"from\s+([A-Za-z0-9_\.]+)\s+import", clean_content):
            add(match.group(1).replace(".", "/"))
        for match in re.finditer(r"^import\s+([A-Za-z0-9_\.]+)", clean_content, re.MULTILINE):
            add(match.group(1).replace(".", "/"))
    elif language == "go":
        for match in re.finditer(r"import\s+[\(]?\s*\"([^\"]+)\"", clean_content):
            add(match.group(1))
        for block in re.findall(r"import\s*\((.*?)\)", clean_content, re.DOTALL):
            for item in re.findall(r"\"([^\"]+)\"", block):
                add(item)
    elif language == "java":
        for match in re.finditer(r"import\s+(?:static\s+)?([A-Za-z0-9_\.]+(?:\.\*)?)\s*;", clean_content):
            add(match.group(1).replace(".", "/").removesuffix("/*"))
    elif language == "csharp":
        for match in re.finditer(r"using\s+(?:static\s+)?([A-Za-z0-9_\.]+)\s*;", clean_content):
            add(match.group(1).replace(".", "/"))

    return imports
