import os
import fnmatch
from pathlib import Path
from tools.core.config import DOCTRINE


def _path_fragment_matches(rel_path: str, fragment: str) -> bool:
    path_parts = [part for part in str(rel_path or "").replace("\\", "/").lower().split("/") if part]
    fragment_parts = [part for part in str(fragment or "").replace("\\", "/").lower().split("/") if part]
    if not fragment_parts or len(fragment_parts) > len(path_parts):
        return False
    return any(
        path_parts[index:index + len(fragment_parts)] == fragment_parts
        for index in range(len(path_parts) - len(fragment_parts) + 1)
    )


def resolve_layer_evidence(rel_path: str) -> dict:
    """
    Identifies the hexagonal layer of a file based on its relative path 
    and the definitions in architecture_doctrine.json.
    """
    norm_path = rel_path.replace("\\", "/").lower()
    
    # Priority 1: Layer Rules from Doctrine
    layer_rules = DOCTRINE.get("layer_rules", [])
    for index, rule in enumerate(layer_rules):
        layer_name = rule.get("layer")
        conditions = rule.get("conditions", {})
        path_fragments = conditions.get("path_fragments", [])
        
        if any(_path_fragment_matches(norm_path, frag) for frag in path_fragments):
            return {"layer": layer_name, "pointer": f"/layer_rules/{index}", "row": rule}
            
    # Priority 2: Doctrine-driven Generic Path Hints
    hints = DOCTRINE.get("architectural_integrity_rules", {}).get("layer_identification_hints", [])
    for index, hint in enumerate(hints):
        if _path_fragment_matches(norm_path, hint.get("fragment", "")):
            return {"layer": hint.get("layer"), "pointer": f"/architectural_integrity_rules/layer_identification_hints/{index}", "row": hint}
    
    return {"layer": "unknown", "pointer": None, "row": None}


def resolve_layer(rel_path: str) -> str:
    """Compatibility projection of the same ordered path-label evaluation."""
    return resolve_layer_evidence(rel_path)["layer"]


def layer_violation_evidence(source_layer: str, target_layer: str, language: str = "typescript") -> dict:
    """
    Checks if an import from source_layer to target_layer violates 
    architectural principles. Supports global and language-specific scoping.
    """
    integrity_rules = DOCTRINE.get("architectural_integrity_rules", {})
    
    # 1. Global Rules Check
    global_rules = integrity_rules.get("global_rules", [])
    for index, rule in enumerate(global_rules):
        if rule.get("source") == source_layer:
            if target_layer in rule.get("targets", []):
                scope = rule.get("scope", [])
                if not scope or language in scope:
                    return {"violated": True, "pointer": f"/architectural_integrity_rules/global_rules/{index}", "row": rule}

    # 2. Language-Specific Rules Check
    lang_rules = integrity_rules.get("language_specific_rules", {}).get(language, [])
    for index, rule in enumerate(lang_rules):
        if rule.get("source") == source_layer:
            if target_layer in rule.get("targets", []):
                escaped = language.replace("~", "~0").replace("/", "~1")
                return {"violated": True, "pointer": f"/architectural_integrity_rules/language_specific_rules/{escaped}/{index}", "row": rule}

    # 3. Legacy Forbidden Mappings (Fallback)
    forbidden = integrity_rules.get("forbidden_mappings", [])
    for index, rule in enumerate(forbidden):
        if rule.get("source") == source_layer:
            if target_layer in rule.get("targets", []):
                return {"violated": True, "pointer": f"/architectural_integrity_rules/forbidden_mappings/{index}", "row": rule}

    return {"violated": False, "pointer": None, "row": None}


def is_violation(source_layer: str, target_layer: str, language: str = "typescript") -> bool:
    """Retain existing decisions; the trace does not activate a policy."""
    return layer_violation_evidence(source_layer, target_layer, language)["violated"]


def _import_fragment_candidate(import_path: str, raw_import_path: str, fragment: str) -> str | None:
    """Trace the existing segment matcher, preserving candidate and branch order."""
    needle = str(fragment or "").strip().lower().strip("/")
    if not needle:
        return None
    for field, candidate in [("source", import_path), ("raw_source", raw_import_path)]:
        normalized = str(candidate or "").replace("\\", "/").lower().strip()
        if not normalized:
            continue
        normalized = normalized.removeprefix("@/")
        segments = [part for part in normalized.split("/") if part and part not in {".", ".."}]
        if needle.endswith("/"):
            prefix = needle.rstrip("/")
            if segments and segments[0] == prefix:
                return field
            if f"/{prefix}/" in f"/{'/'.join(segments)}/":
                return field
            continue
        if "/" in needle:
            if f"/{needle}/" in f"/{'/'.join(segments)}/":
                return field
            continue
        if needle in segments:
            return field
        if normalized == needle or normalized.startswith(f"{needle}/"):
            return field
    return None


def import_matches_forbidden_fragment(import_path: str, raw_import_path: str, fragment: str) -> bool:
    return _import_fragment_candidate(import_path, raw_import_path, fragment) is not None


def _restriction_allowed_glob(restriction: dict, rel_path: str) -> str | None:
    normalized = str(rel_path or "").replace("\\", "/")
    for pattern in restriction.get("allowed_source_globs", []) or []:
        glob = str(pattern or "").replace("\\", "/")
        if glob and fnmatch.fnmatch(normalized, glob):
            return glob
    return None


def restriction_allows_source(restriction: dict, rel_path: str) -> bool:
    return _restriction_allowed_glob(restriction, rel_path) is not None


def layer_import_restriction_evidence(restriction: dict, index: int, *, rel_path: str,
        source_layer: str, import_path: str, raw_import_path: str, language: str, primary_alias: str) -> dict:
    """Evaluate one existing declaration; this trace never activates a policy."""
    result = {"pointer": f"/architectural_integrity_rules/layer_import_restrictions/{index}",
        "row": restriction, "actual_language": language, "matched_source_glob": None,
        "fragment_index": None, "fragment": None, "match_form": None, "candidate_field": None,
        "primary_alias": primary_alias, "violated": False}
    required_language = restriction.get("language")
    if source_layer != restriction.get("layer") or (required_language and required_language != language):
        return result
    result["matched_source_glob"] = _restriction_allowed_glob(restriction, rel_path)
    if result["matched_source_glob"] is not None:
        return result
    for fragment_index, fragment in enumerate(restriction.get("forbidden_substrings", [])):
        for form, value in [("declared_fragment", fragment), ("primary_alias_fragment", f"{primary_alias}{fragment}")]:
            field = _import_fragment_candidate(import_path, raw_import_path, value)
            if field is not None:
                return {**result, "fragment_index": fragment_index, "fragment": fragment,
                        "match_form": form, "candidate_field": field, "violated": True}
    return result
