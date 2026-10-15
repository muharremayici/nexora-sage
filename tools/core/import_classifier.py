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


def module_source_context(rel_path: str, module_container: str) -> dict:
    """Locate the first exact container sequence in an indexed slash path.

    The runtime owner supplies the normalized container. Do not re-normalize
    source paths, infer dot-root modules or reinterpret alias/API criteria here.
    Single-component containers retain the original first-match decision.
    """
    parts = rel_path.split("/")
    container_parts = module_container.split("/")
    width = len(container_parts)
    if width == 1:
        index = parts.index(module_container) if module_container in parts else None
    else:
        index = next((i for i in range(len(parts) - width + 1)
                      if parts[i:i + width] == container_parts), None)
    module = parts[index + width] if index is not None and index + width < len(parts) else None
    return {"source_container_component_index": index, "source_module": module, "rel_path": rel_path}


def module_api_import_evidence(source_context: dict, raw_import: str, *, module_container: str,
                               primary_alias: str, api_entry_policy: dict | None = None) -> dict:
    """Trace current literal path criteria, not target export or import legality."""
    prefix = f"{primary_alias}{module_container}/"
    trace = {"module_container": module_container, "primary_alias": primary_alias,
        "module_import_prefix": prefix,
        "source_container_component_index": source_context["source_container_component_index"],
        "source_module": source_context["source_module"], "raw_prefix_offset": None,
        "imported_module": None, "decision_branch": None,
        "raw_api_terminal": None, "raw_contains_api": None, "source_api_entry_exemption": None,
        "api_entry_policy": None}
    module = source_context["source_module"]
    if not module or prefix not in raw_import:
        return {"violated": False, "trace": trace}
    trace["raw_api_terminal"] = raw_import.endswith("/api") or raw_import.endswith("/api/")
    trace["raw_contains_api"] = "/api" in raw_import
    trace["raw_prefix_offset"] = raw_import.index(prefix)
    trace["imported_module"] = raw_import.split(prefix)[1].split("/")[0]
    if trace["imported_module"] != module:
        trace["decision_branch"] = "cross_module_literal_api_path"
        return {"violated": not trace["raw_api_terminal"], "trace": trace}
    trace["decision_branch"] = "same_module_literal_api_path"
    if api_entry_policy is None:
        return {"violated": False, "api_entry_policy_required": True, "trace": trace}
    trace["api_entry_policy"] = api_entry_policy
    # Indexed source path is retained by the source-context owner, not guessed
    # from an import label. The caller supplies that exact path for exemptions.
    trace["source_api_entry_exemption"] = any(
        source_context["rel_path"].endswith(f"/api/{name}") for name in api_entry_policy["names"])
    return {"violated": trace["raw_contains_api"] and not trace["source_api_entry_exemption"], "trace": trace}
