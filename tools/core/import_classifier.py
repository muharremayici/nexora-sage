from __future__ import annotations

import re


def import_specifier_from_audit_detail(detail: str) -> str:
    """Read legacy import evidence without interpreting diagnostic fields as paths."""
    _source, separator, tail = str(detail or "").partition(" imports ")
    if not separator or "\n" in tail or "\r" in tail:
        return ""
    # Audit appends semicolon-delimited key=value diagnostics after the import.
    raw = re.split(r";\s+[A-Za-z_]\w*=", tail, maxsplit=1)[0].strip()
    if raw[:1] in {"'", '"', "`"}:
        return raw[1:-1] if len(raw) > 1 and raw[-1] == raw[0] else ""
    return raw


def is_alias_import(import_path: str, primary_alias: str = "@/") -> bool:
    raw = str(import_path or "").strip()
    alias = str(primary_alias or "").strip()
    return bool(alias and raw.startswith(alias))


def is_target_relative_import(import_path: str) -> bool:
    raw = str(import_path or "").strip()
    return raw.startswith("./") or raw.startswith("../") or raw in {".", ".."}


def is_target_absolute_import(import_path: str) -> bool:
    raw = str(import_path or "").strip()
    return raw.startswith("/")


def should_enforce_alias_for_local_import(import_path: str, primary_alias: str = "@/") -> bool:
    """Return true only for target-repo local path imports, not package subpaths."""
    raw = str(import_path or "").strip()
    if not raw or is_alias_import(raw, primary_alias):
        return False
    return is_target_relative_import(raw) or is_target_absolute_import(raw)
