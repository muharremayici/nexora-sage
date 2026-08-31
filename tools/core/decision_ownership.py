from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from tools.core.config import CODE_MAPS_DIR, CONFIG_DIR
from tools.core.json_io import load_json_object_strict, load_json_object_strict_cached


REGISTRY_PATH = CONFIG_DIR / "decision_ownership_registry.json"


def load_decision_ownership_registry() -> dict[str, Any]:
    return load_json_object_strict_cached(REGISTRY_PATH, label="Decision ownership registry")


def _pointer_value(payload: Any, pointer: str) -> Any:
    current = payload
    for part in str(pointer or "").split("."):
        if not part or not isinstance(current, dict) or part not in current:
            raise KeyError(pointer)
        current = current[part]
    return current


def _canonical_values(owner_file: str, pointer: str, value_mode: str) -> set[Any]:
    payload = load_json_object_strict(CODE_MAPS_DIR / owner_file, label=f"Decision owner {owner_file}")
    value = _pointer_value(payload, pointer)
    if value_mode == "list_values" and isinstance(value, list):
        return {item for item in value if isinstance(item, (str, int, float, bool))}
    if value_mode == "object_keys" and isinstance(value, dict):
        return {str(item) for item in value}
    raise ValueError(f"Unsupported decision owner value at {owner_file}:{pointer} ({value_mode})")


def _literal_collections(source_text: str) -> list[dict[str, Any]]:
    try:
        tree = ast.parse(source_text)
    except SyntaxError:
        return []
    rows: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.List, ast.Tuple, ast.Set, ast.Dict)):
            continue
        try:
            value = ast.literal_eval(node)
        except (ValueError, TypeError, SyntaxError):
            continue
        raw_values = value.keys() if isinstance(value, dict) else value
        values = {item for item in raw_values if isinstance(item, (str, int, float, bool))}
        if values:
            rows.append({"line": int(getattr(node, "lineno", 0) or 0), "values": values})
    return rows


def find_owned_decision_copies(relative_path: str, source_text: str) -> list[dict[str, Any]]:
    normalized_path = str(relative_path or "").replace("\\", "/").strip("/")
    registry = load_decision_ownership_registry()
    policy = registry.get("guard_policy", {}) if isinstance(registry, dict) else {}
    suffixes = {str(item) for item in policy.get("supported_source_suffixes", [])}
    if Path(normalized_path).suffix.lower() not in suffixes:
        return []
    path_parts = set(Path(normalized_path).parts)
    if path_parts & {str(item) for item in policy.get("excluded_path_parts", [])}:
        return []
    minimum_size = int(policy.get("minimum_local_collection_size", 2) or 2)
    minimum_overlap = int(policy.get("minimum_overlap_values", 2) or 2)
    collections = [row for row in _literal_collections(source_text) if len(row["values"]) >= minimum_size]
    findings: list[dict[str, Any]] = []
    for domain in registry.get("domains", []):
        if not isinstance(domain, dict):
            continue
        owner_file = str(domain.get("owner_file") or "").replace("\\", "/")
        if policy.get("owner_files_are_exempt") and normalized_path == owner_file:
            continue
        best_by_line: dict[int, tuple[tuple[int, int], dict[str, Any]]] = {}
        for pointer in domain.get("owner_pointers", []):
            canonical = _canonical_values(owner_file, str(pointer), str(domain.get("value_mode") or ""))
            for row in collections:
                local_values = row["values"]
                overlap = local_values & canonical
                if len(overlap) < minimum_overlap or not local_values <= canonical:
                    continue
                relationship = "exact_copy" if local_values == canonical else "owned_subset"
                finding = {
                    "rule": "central_decision_reconstructed_locally",
                    "domain": str(domain.get("id") or ""),
                    "file": normalized_path,
                    "line": row["line"],
                    "relationship": relationship,
                    "local_values": sorted(local_values, key=str),
                    "owner_file": owner_file,
                    "owner_pointer": str(pointer),
                    "consumer_rule": str(domain.get("consumer_rule") or ""),
                }
                rank = (1 if relationship == "exact_copy" else 0, len(overlap))
                current = best_by_line.get(row["line"])
                if current is None or rank > current[0]:
                    best_by_line[row["line"]] = (rank, finding)
        findings.extend(item[1] for item in best_by_line.values())
    return findings


def validate_decision_ownership_registry() -> dict[str, Any]:
    registry = load_decision_ownership_registry()
    contract = registry.get("validation_contract", {}) if isinstance(registry, dict) else {}
    domains = [row for row in registry.get("domains", []) if isinstance(row, dict)]
    required_fields = {str(item) for item in contract.get("required_domain_fields", [])}
    required_ids = {str(item) for item in contract.get("required_domain_ids", [])}
    allowed_modes = {str(item) for item in contract.get("allowed_value_modes", [])}
    issues: list[dict[str, Any]] = []
    present_ids = {str(row.get("id") or "") for row in domains}
    if missing := sorted(required_ids - present_ids):
        issues.append({"missing_domain_ids": missing})
    for row in domains:
        missing_fields = sorted(field for field in required_fields if row.get(field) in (None, "", []))
        if missing_fields:
            issues.append({"domain": row.get("id"), "missing_fields": missing_fields})
            continue
        if str(row.get("value_mode")) not in allowed_modes:
            issues.append({"domain": row.get("id"), "invalid_value_mode": row.get("value_mode")})
        for pointer in row.get("owner_pointers", []):
            try:
                _canonical_values(str(row["owner_file"]), str(pointer), str(row["value_mode"]))
            except (FileNotFoundError, KeyError, ValueError) as exc:
                issues.append({"domain": row.get("id"), "pointer": pointer, "error": str(exc)})
    return {"status": "PASS" if not issues else "FAIL", "domains": sorted(present_ids), "issues": issues}
