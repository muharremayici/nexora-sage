from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from tools.core.config import CONFIG_DIR
from tools.core.json_io import load_json_object_strict_cached
from tools.core.language_registry import language_for_extension
from tools.core.source_snapshot_integrity import snapshot_content_status


PROFILE_PATH = CONFIG_DIR / "test_impact_profiles.json"

def load_test_impact_profiles() -> dict[str, Any]:
    payload = load_json_object_strict_cached(PROFILE_PATH, label="Test impact profiles")
    for key, expected_type in (("languages", dict), ("confidence", dict), ("fallback_command", str)):
        value = payload.get(key)
        if not isinstance(value, expected_type) or value in ("", {}, []):
            raise ValueError(f"Test impact profiles missing mandatory non-empty field: {key}")
    return payload


def confidence_value(name: str) -> float:
    raw = (load_test_impact_profiles().get("confidence") or {}).get(name)
    if raw is None:
        raise ValueError(f"Test impact profiles missing confidence value: {name}")
    try:
        return float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid test impact confidence value for {name}: {raw!r}") from exc


def static_test_evidence_policy() -> dict[str, Any]:
    policy = load_test_impact_profiles().get("evidence_policy")
    if not isinstance(policy, dict):
        raise ValueError("Test impact profiles missing evidence_policy")
    mapping = policy.get("relation_by_match_type")
    evidence = policy.get("unexecuted_evidence")
    if (
        not isinstance(mapping, dict) or not mapping
        or any(not isinstance(value, str) or not value for value in mapping.values())
        or not isinstance(evidence, dict)
        or any(not isinstance(evidence.get(key), str) or not evidence[key] for key in
               ("confidence_semantics", "test_execution", "changed_behavior", "mock_binding"))
        or evidence.get("native_diagnostics") != "not_ingested"
        or not isinstance(policy.get("unknown_relation"), str) or not policy["unknown_relation"]
        or not isinstance(policy.get("proof_boundary"), str) or not policy["proof_boundary"]
    ):
        raise ValueError("Invalid test impact static evidence policy")
    _validated_mock_declaration_policy(policy.get("mock_declaration_policy"))
    return policy


def test_candidate_project(row: dict[str, Any]) -> str:
    """Use candidate-owned identity only; never borrow the target's project."""
    project = str(row.get("project") or "").strip()
    node = str(row.get("atlas_node") or "")
    node_project, separator, path = node.partition("::")
    if separator and (not node_project or not path or (project and project != node_project)):
        return ""
    return project or (node_project if separator else "")


def static_candidate_evidence(row: dict[str, Any]) -> dict[str, Any]:
    """Describe static relation only; never borrow execution claims from a candidate."""
    policy = static_test_evidence_policy()
    mapping = policy["relation_by_match_type"]
    relation = row.get("static_relation")
    if not isinstance(relation, str) or relation not in set(mapping.values()):
        relation = mapping.get(str(row.get("type") or ""), policy["unknown_relation"])
    return {"relation": relation, **policy["unexecuted_evidence"]}


def _validated_mock_declaration_policy(policy: Any) -> dict[str, Any]:
    if not isinstance(policy, dict):
        raise ValueError("Test impact profiles missing mock_declaration_policy")
    rules = policy.get("framework_imports")
    limit = policy.get("maximum_declarations")
    version = policy.get("literal_argument_evidence_version")
    if (not isinstance(rules, list) or not rules or type(limit) is not int or not 1 <= limit <= 3
            or type(version) is not int or version < 1
            or not isinstance(policy.get("proof_boundary"), str) or not policy["proof_boundary"]):
        raise ValueError("Invalid test impact mock declaration policy")
    for rule in rules:
        if (not isinstance(rule, dict) or not isinstance(rule.get("source"), str) or not rule["source"]
                or not isinstance(rule.get("imported_name"), str) or not rule["imported_name"]
                or not isinstance(rule.get("methods"), list)
                or not rule["methods"] or any(not isinstance(v, str) or not v for v in rule["methods"])):
            raise ValueError("Invalid test impact mock framework import")
    return policy


def snapshot_mock_declaration_evidence(
    file_info: dict[str, Any], content: str, *, test_ref: str, target_ref: str,
    target_module_path: str = "",
) -> dict[str, Any]:
    """Positive lexical declarations only, never executed mock/coverage evidence."""
    policy = static_test_evidence_policy()["mock_declaration_policy"]
    rules = policy["framework_imports"]
    limit = policy["maximum_declarations"]
    version = policy["literal_argument_evidence_version"]
    result = {
        "status": "unknown", "reason": "no_supported_target_mock_observed",
        "runtime_binding": "not_established", "proof_boundary": policy["proof_boundary"],
        "test_ref": test_ref, "target_ref": target_ref, "declarations": [],
    }
    digest = str(file_info.get("hash") or "")
    identity = snapshot_content_status(content, digest, digest) if content else "unavailable"
    if identity != "ok":
        result["reason"] = "snapshot_" + identity
        return result
    result["source_hash"] = digest
    evidence = file_info.get("module_root_import_call_evidence")
    if (not isinstance(evidence, dict) or evidence.get("status") != "observed"
            or evidence.get("binding_scope") != "single_file_lexical_import"
            or evidence.get("callsite_scope") != "module_root"
            or evidence.get("runtime_execution") != "not_established"
            or type(evidence.get("literal_argument_evidence_version")) is not int
            or evidence["literal_argument_evidence_version"] != version
            or not isinstance(evidence.get("calls"), list)):
        result["reason"] = "parser_evidence_unavailable"
        return result
    project, separator, _target_path = target_ref.partition("::")
    if (not separator or not _target_path or not project
            or test_ref.partition("::")[0] != project or not target_module_path):
        result["reason"] = "target_module_resolution_unavailable"
        return result
    records = file_info.get("import_records")
    if not isinstance(records, list):
        result["reason"] = "target_module_resolution_unavailable"
        return result
    # Consume exact Atlas resolutions, not a second alias/path resolver or text grep.
    resolved: dict[str, set[str]] = {}
    for record in records:
        if isinstance(record, dict) and record.get("kind") != "type":
            raw, path = record.get("raw_source"), record.get("source")
            if isinstance(raw, str) and isinstance(path, str):
                resolved.setdefault(raw, set()).add(path.replace("\\", "/"))
    declarations = []
    for call in evidence["calls"]:
        if (not isinstance(call, dict) or call.get("kind") != "named"
                or call.get("optional") is not False
                or not isinstance(call.get("first_literal_argument"), str)
                or type(call.get("line")) is not int or call["line"] < 1
                or not any(call.get("source") == rule["source"]
                           and call.get("importedName") == rule["imported_name"]
                           and call.get("member") in rule["methods"] for rule in rules)):
            continue
        specifier = call["first_literal_argument"]
        if resolved.get(specifier) != {target_module_path}:
            continue
        declarations.append({
            "module_specifier": specifier, "framework_source": call["source"],
            "imported_name": call["importedName"], "local_name": call.get("localName", ""),
            "method": call["member"], "line": call["line"],
        })
    if declarations:
        result.update(status="target_mock_declaration_observed",
                      reason="lexical_import_and_exact_atlas_module_resolution",
                      declarations=declarations[:limit])
        result["declarations_omitted"] = max(0, len(declarations) - limit)
    elif not any(paths == {target_module_path} for paths in resolved.values()):
        result["reason"] = "target_module_resolution_unavailable"
    return result


def _language_entry_for_path(path_str: str) -> tuple[str, dict[str, Any]]:
    ext = Path(path_str).suffix.lower()
    language = language_for_extension(ext)
    languages = load_test_impact_profiles().get("languages") or {}
    entry = languages.get(language) if isinstance(languages, dict) else None
    if not isinstance(entry, dict):
        for candidate, payload in languages.items():
            if isinstance(payload, dict) and ext in payload.get("extensions", []):
                return str(candidate), payload
        return language, {}
    return language, entry


def is_test_path(path_str: str) -> bool:
    path_lower = str(path_str or "").lower().replace("\\", "/")
    profile = load_test_impact_profiles()
    for fragment in profile.get("ignored_path_fragments", []):
        if str(fragment).lower() in path_lower:
            return False

    filename = Path(path_lower).name
    _, entry = _language_entry_for_path(path_lower)
    if not entry:
        return False
    path_with_edges = f"/{path_lower}"
    if any(str(fragment).lower() in path_with_edges for fragment in entry.get("path_fragments", [])):
        return True
    if any(token.lower() in filename for token in entry.get("filename_contains", [])):
        return True
    if any(filename.startswith(str(prefix).lower()) for prefix in entry.get("filename_prefixes", [])):
        return True
    if any(filename.endswith(str(suffix).lower()) for suffix in entry.get("filename_suffixes", [])):
        return True
    return False


def extract_logical_base_name(path_str: str) -> str:
    filename = Path(path_str).name
    base = Path(filename).stem
    _, entry = _language_entry_for_path(path_str)
    for cleanup in entry.get("base_cleanup", []) if isinstance(entry, dict) else []:
        if not isinstance(cleanup, dict):
            continue
        pattern = str(cleanup.get("pattern", ""))
        replacement = str(cleanup.get("replacement", ""))
        if pattern:
            base = re.sub(pattern, replacement, base, flags=re.IGNORECASE)
    return base.strip("_.-")


def nearby_test_candidate_paths(source_path: str) -> Iterator[Path]:
    """Bounded adjacent inventory candidates, not imports, coverage or execution.

    Extensions and literal filename/directory conventions belong to the shared
    profile. No recursive repository scan or framework-specific naming table.
    The consumer must check file presence, logical identity and project scope.
    """
    source = Path(source_path)
    _, entry = _language_entry_for_path(source_path)
    if not entry or is_test_path(source_path):
        return
    extensions = list(dict.fromkeys(entry.get("extensions", [])))
    if source.suffix in extensions:
        extensions.remove(source.suffix)
        extensions.insert(0, source.suffix)
    directories = [source.parent]
    for fragment in entry.get("path_fragments", []):
        parts = str(fragment).replace("\\", "/").strip("/").split("/")
        if parts and all(part and part not in {".", ".."} and ":" not in part for part in parts):
            directories.append(source.parent.joinpath(*parts))
    markers = [str(token) for token in entry.get("filename_contains", [])
               if token and not any(char in str(token) for char in "/\\:")]
    for directory in dict.fromkeys(directories):
        stems = [source.stem + marker.rstrip(".") for marker in markers]
        if directory != source.parent:
            stems.append(source.stem)
        for stem in dict.fromkeys(stems):
            for extension in extensions:
                yield directory / (stem + extension)


def command_for_test(test_path: str) -> str:
    path = str(test_path or "").replace("\\", "/")
    _, entry = _language_entry_for_path(path)
    template = str(entry.get("command") or load_test_impact_profiles()["fallback_command"])
    test_dir = str(Path(path).parent).replace("\\", "/")
    if test_dir == ".":
        test_dir = "."
    class_name = Path(path).stem
    return template.format(test_path=path, test_dir=test_dir, class_name=class_name)
