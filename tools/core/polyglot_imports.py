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


def extract_typescript_import_evidence(parser_entries: list[dict] | None) -> dict:
    """Normalize syntax-grounded JS/TS module edges emitted by the Node parser."""
    meta = next(
        (
            entry
            for entry in parser_entries or []
            if isinstance(entry, dict) and entry.get("name") == "__file_meta__"
        ),
        None,
    )
    if not isinstance(meta, dict) or not isinstance(meta.get("moduleImports"), list):
        return {"status": "unavailable", "records": [], "eager_sources": [], "lazy_sources": []}

    records: list[dict[str, str]] = []
    eager_sources: list[str] = []
    lazy_sources: list[str] = []
    seen_records: set[tuple[str, str, str, str]] = set()
    for raw_record in meta["moduleImports"]:
        if not isinstance(raw_record, dict):
            continue
        source = str(raw_record.get("source") or "").strip()
        name = str(raw_record.get("name") or "*").strip() or "*"
        kind = str(raw_record.get("kind") or "module").strip().lower()
        scope = str(raw_record.get("scope") or "top_level").strip().lower()
        if not source or scope not in {"top_level", "local"}:
            continue
        key = (source, name, kind, scope)
        if key in seen_records:
            continue
        seen_records.add(key)
        records.append({"source": source, "name": name, "kind": kind, "scope": scope})
        if kind == "type":
            continue
        bucket = lazy_sources if kind == "dynamic" or scope == "local" else eager_sources
        if source not in bucket:
            bucket.append(source)

    return {
        "status": str(meta.get("parserStatus") or "unavailable").strip().lower(),
        "records": records,
        "eager_sources": eager_sources,
        "lazy_sources": lazy_sources,
    }


def extract_imports(content: str, language: str, *, parser_entries: list[dict] | None = None) -> list[str]:
    """Extract module specifiers without treating JS/TS source text as syntax evidence."""
    imports: list[str] = []
    clean_content = strip_comments_for_import_scan(content)
    language = (language or "typescript").lower()

    def add(value: str):
        normalized = str(value or "").strip()
        if normalized and normalized not in imports:
            imports.append(normalized)

    if language in {"typescript", "javascript"}:
        evidence = extract_typescript_import_evidence(parser_entries)
        for source in evidence["eager_sources"] + evidence["lazy_sources"]:
            add(source)
    elif language == "vue":
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


def extract_go_qualified_imports(content: str) -> list[dict[str, str]]:
    """Return source-grounded Go package member uses.

    A Go import addresses a package, not a file. Qualified calls such as
    config.LoadConfig are the symbol-level evidence needed by downstream
    reachability consumers.
    """
    clean_content = strip_comments_for_import_scan(content)
    aliases: dict[str, str] = {}

    for alias, source in re.findall(
        r"\bimport\s+(?:(\w+)\s+)?\"([^\"]+)\"",
        clean_content,
    ):
        package_alias = alias or source.rsplit("/", 1)[-1]
        if package_alias not in {"_", "."}:
            aliases[package_alias] = source

    for block in re.findall(r"\bimport\s*\((.*?)\)", clean_content, re.DOTALL):
        for alias, source in re.findall(r"(?m)^\s*(?:(\w+)\s+)?\"([^\"]+)\"", block):
            package_alias = alias or source.rsplit("/", 1)[-1]
            if package_alias not in {"_", "."}:
                aliases[package_alias] = source

    records: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for package_alias, source in sorted(aliases.items()):
        for member in re.findall(rf"\b{re.escape(package_alias)}\.([A-Za-z_]\w*)", clean_content):
            key = (source, member)
            if key in seen:
                continue
            seen.add(key)
            records.append(
                {
                    "source": source,
                    "name": member,
                    "kind": "qualified-member",
                    "alias": package_alias,
                }
            )
    return records
