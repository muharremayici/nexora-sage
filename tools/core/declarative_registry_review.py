"""Explicit literal registry review; never infer a domain contract or execute a target."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any

from tools.core.json_io import load_json_object_strict_cached
from tools.core.json_syntax import loads_json_strict
from tools.core.patch_input import PatchInputError, _read_confined, _relative_file
from tools.core.unmanaged_atomic_io import native_filesystem_path


POLICY_PATH = Path(__file__).resolve().parents[2] / "config/declarative_registry_review_contract.json"


def _policy() -> dict:
    policy = load_json_object_strict_cached(POLICY_PATH, label="Declarative registry review")
    limits = policy.get("limits")
    if policy.get("version") != "v1-literal-json-key-id" or not isinstance(limits, dict):
        raise ValueError("review_policy_invalid")
    required = ("contract_bytes", "source_bytes", "json_nodes", "json_depth",
                "registry_rows", "display_rows", "output_bytes", "name_characters", "reason_characters")
    if set(limits) != set(required) or any(type(limits[k]) is not int or limits[k] < 1 for k in required):
        raise ValueError("review_policy_invalid")
    return policy


def _json(raw: bytes, limits: dict) -> Any:
    try:
        value = loads_json_strict(raw.decode("utf-8-sig"))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ValueError("literal_json_invalid_or_too_deep") from exc
    pending, visited = [(value, 0)], 0
    while pending:
        node, depth = pending.pop()
        visited += 1
        if visited > limits["json_nodes"] or depth > limits["json_depth"]:
            raise ValueError("json_structure_over_budget")
        if isinstance(node, float) and not math.isfinite(node):
            raise ValueError("non_json_numeric_value")
        if isinstance(node, dict):
            pending.extend((v, depth + 1) for v in node.values())
        elif isinstance(node, list):
            pending.extend((v, depth + 1) for v in node)
        if isinstance(node, str):
            node.encode("utf-8")
        if isinstance(node, dict):
            for key in node:
                key.encode("utf-8")
    return value


def _name(value: Any, limit: int) -> bool:
    return isinstance(value, str) and len(value) <= limit and not any(ord(c) < 32 for c in value)


def _pointer_parts(pointer: Any, limit: int) -> list[str]:
    if not isinstance(pointer, str) or len(pointer) > limit:
        raise ValueError("registry_pointer_invalid")
    if pointer == "":
        return []
    if not pointer.startswith("/") or re.search(r"~(?![01])", pointer):
        raise ValueError("registry_pointer_invalid")
    return [part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")]


def _escape(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _contract(value: Any, policy: dict) -> dict:
    limits = policy["limits"]
    ts_policy = policy.get("typescript")
    ts_version = ts_policy.get("version") if isinstance(ts_policy, dict) else None
    path_policy = ts_policy.get("member_path") if isinstance(ts_policy, dict) else None
    path_version = path_policy.get("version") if isinstance(path_policy, dict) else None
    if (not isinstance(value, dict)
            or set(value) != {"version", "source", "id_field", "exceptions"}
            or not isinstance(value.get("version"), str)
            or value["version"] not in (policy["version"], ts_version, path_version)):
        raise ValueError("target_contract_invalid")
    nested = value["version"] == path_version
    source = value["source"]
    typescript = value["version"] != policy["version"]
    if (not isinstance(source, dict) or set(source) != (({"path", "sha256", "project", "atlas_path", "symbol"} | ({"member_path"} if nested else set())) if typescript else {"path", "sha256", "pointer"})
            or not isinstance(source["path"], str) or len(source["path"]) > limits["name_characters"]
            or not isinstance(source["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", source["sha256"])
            or not _name(value["id_field"], limits["name_characters"]) or not value["id_field"]):
        raise ValueError("target_contract_invalid")
    if typescript:
        if (value["version"] not in ("v1-typescript-literal-key-id", "v2-typescript-literal-path-key-id")
                or any(not _name(source[k], limits["name_characters"]) or not source[k] for k in ("project", "atlas_path", "symbol"))):
            raise ValueError("typescript_target_contract_invalid")
        if nested:
            from tools.core.typescript_registry_contract import registry_member_policy, registry_member_path_valid
            path_rules, _, width = registry_member_policy()
            if not registry_member_path_valid(source["member_path"], path_rules, width):
                raise ValueError("typescript_member_path_invalid")
        _relative_file(source["atlas_path"])
    else:
        _pointer_parts(source["pointer"], limits["name_characters"])
    _relative_file(source["path"])
    if Path(source["path"]).suffix.lower() not in ((".ts", ".tsx") if typescript else (".json",)):
        raise ValueError("source_format_not_supported")
    exceptions = value["exceptions"]
    if not isinstance(exceptions, list) or len(exceptions) > limits["registry_rows"]:
        raise ValueError("target_exceptions_invalid")
    seen = set()
    for row in exceptions:
        if (not isinstance(row, dict) or set(row) != {"key", "expected_id", "reason"}
                or not _name(row["key"], limits["name_characters"])
                or not _name(row["expected_id"], limits["name_characters"])
                or not _name(row["reason"], limits["reason_characters"]) or not row["reason"].strip()
                or row["key"] in seen):
            raise ValueError("target_exceptions_invalid")
        seen.add(row["key"])
    return value


def _compare(registry: Any, contract: dict, limits: dict, *, locations=None) -> dict:
    if not isinstance(registry, dict):
        raise ValueError("registry_not_object_map")
    if len(registry) > limits["registry_rows"]:
        raise ValueError("registry_over_budget")
    exceptions = {row["key"]: row for row in contract["exceptions"]}
    field, pointer = contract["id_field"], contract["source"].get("pointer", "")
    for key, exception in exceptions.items():
        row = registry.get(key)
        if (not isinstance(row, dict) or type(row.get(field)) is not str
                or row[field] != exception["expected_id"] or key == row[field]):
            raise ValueError("exception_missing_changed_or_redundant")
    rows, counts = [], {"matched": 0, "mismatched": 0, "excepted": 0, "unresolved": 0}
    for key, entity in registry.items():
        if not _name(key, limits["name_characters"]):
            raise ValueError("registry_name_over_budget_or_unsupported")
        value = entity.get(field) if isinstance(entity, dict) else None
        location = pointer + "/" + _escape(key)
        if not isinstance(entity, dict) or field not in entity or type(value) is not str:
            kind = "unresolved"
            row = {"kind": kind, "key": key, "pointer": location, "reason": "literal_string_id_unavailable"}
        else:
            if not _name(value, limits["name_characters"]):
                raise ValueError("registry_id_over_budget_or_unsupported")
            kind = "matched" if key == value else "excepted" if key in exceptions else "mismatched"
            row = {"kind": kind, "key": key, "observed_id": value,
                   "pointer": location + "/" + _escape(field)}
            if kind == "excepted":
                row["reason"] = exceptions[key]["reason"]
        counts[kind] += 1
        if locations is not None:
            row.pop("pointer")
            row.update(locations[key])
        if kind != "matched" and len(rows) < limits["display_rows"]:
            rows.append(row)
    omitted = sum(counts[k] for k in ("mismatched", "excepted", "unresolved")) - len(rows)
    status = ("incomplete_evidence" if counts["unresolved"] else
              "review_required" if counts["mismatched"] else
              "checked_within_declared_scope" if registry else "empty_declared_registry")
    return {"status": status, "registry_row_count": len(registry), "counts": counts,
            "rows": rows, "shown": len(rows), "display_omitted": omitted,
            "coverage": "all_rows_of_selected_literal_object_only"}


def review_declarative_registry(target_root: Path | str, contract_path: str) -> dict:
    """Read a named contract and pinned source evidence; write no target or artifact."""
    result = {"status": "unavailable", "rows": [], "shown": 0,
              "domain_authority": "explicit_target_contract_only", "mutation_proposed": False,
              "runtime_proof": "not_performed", "atlas_binding": "not_claimed",
              "claim_boundary": "Only declared literal key-ID equality; not repository health, ordering, locale, type or runtime correctness."}
    try:
        policy = _policy()
        root = Path(target_root)
        if not str(target_root) or not root.is_absolute() or not Path(native_filesystem_path(root)).is_dir():
            raise ValueError("explicit_absolute_target_root_required")
        if (not _name(contract_path, policy["limits"]["name_characters"])
                or Path(contract_path).suffix.lower() != ".json"):
            raise ValueError("target_contract_path_invalid")
        raw_contract = _read_confined(root, contract_path, policy["limits"]["contract_bytes"])
        contract = _contract(_json(raw_contract, policy["limits"]), policy)
        raw_source = _read_confined(root, contract["source"]["path"], policy["limits"]["source_bytes"])
        digest = hashlib.sha256(raw_source).hexdigest()
        result["input_identity"] = {
            "contract_path": contract_path, "contract_sha256": hashlib.sha256(raw_contract).hexdigest(),
            "source_path": contract["source"]["path"], "source_sha256": digest,
            "review_policy_sha256": hashlib.sha256(json.dumps(policy, sort_keys=True, ensure_ascii=False,
                separators=(",", ":")).encode("utf-8")).hexdigest()}
        if digest != contract["source"]["sha256"]:
            raise ValueError("source_identity_mismatch")
        if contract["version"] == policy["version"]:
            source = _json(raw_source, policy["limits"])
            for part in _pointer_parts(contract["source"]["pointer"], policy["limits"]["name_characters"]):
                if not isinstance(source, dict) or part not in source:
                    raise ValueError("registry_pointer_not_found_or_unsupported")
                source = source[part]
            compared = {**_compare(source, contract, policy["limits"]), "source_pointer":contract["source"]["pointer"]}
        else:
            from tools.core.typescript_registry_review import compare_typescript_registry

            compared = compare_typescript_registry(root, raw_source, contract, policy["limits"], compare=_compare)
        # Recheck independently before publishing an observation. Not hostile-FS atomicity.
        if (_policy() != policy
                or _read_confined(root, contract_path, policy["limits"]["contract_bytes"]) != raw_contract
                or _read_confined(root, contract["source"]["path"], policy["limits"]["source_bytes"]) != raw_source):
            raise ValueError("input_changed_during_review")
        observed = {**result, **compared,
                    "id_field": contract["id_field"], "source_binding": "pinned_bytes_rechecked"}
        while len(json.dumps(observed, ensure_ascii=False).encode("utf-8")) > policy["limits"]["output_bytes"]:
            if not observed["rows"]:
                raise ValueError("output_envelope_over_budget")
            observed["rows"].pop()
            observed["shown"] -= 1
            observed["display_omitted"] += 1
        return observed
    except (OSError, ValueError, UnicodeError, RecursionError, RuntimeError) as exc:
        reason = exc.reason if isinstance(exc, PatchInputError) else str(exc)
        return {**result, "reason": reason, "source_binding": "unavailable"}
