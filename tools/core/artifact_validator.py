import json
import re
from pathlib import Path
from typing import Any, Dict, List
from tools.core.logger import logger

from tools.core.artifact_registry import ARTIFACT_METADATA
from tools.core.artifact_registry import ARTIFACT_PATHS, ARTIFACT_SCHEMAS, mandatory_artifact_ids
class ArtifactValidationError(Exception):
    pass


def load_json(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def _json_type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return "integer"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _matches_type(value: Any, expected: Any) -> bool:
    actual = _json_type_name(value)
    if isinstance(expected, list):
        return any(_matches_type(value, item) for item in expected)
    if expected == "number":
        return actual in {"integer", "number"}
    return actual == expected


def _resolve_ref(schema_root: Dict[str, Any], ref: str) -> Dict[str, Any]:
    if not ref.startswith("#/"):
        raise ArtifactValidationError(f"Unsupported $ref: {ref}")
    node: Any = schema_root
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def _render_validation_path(path: list[str | int]) -> str:
    rendered = str(path[0]) if path else "$"
    for segment in path[1:]:
        if isinstance(segment, int):
            rendered += f"[{segment}]"
        else:
            rendered += f".{segment}"
    return rendered


def _validation_error(errors: List[str], path: list[str | int], message: str) -> None:
    errors.append(f"{_render_validation_path(path)}: {message}")


def _validate(
    value: Any,
    schema: Dict[str, Any],
    schema_root: Dict[str, Any],
    path: list[str | int],
    errors: List[str],
) -> None:
    if "$ref" in schema:
        _validate(value, _resolve_ref(schema_root, schema["$ref"]), schema_root, path, errors)
        return

    if "allOf" in schema:
        for part in schema["allOf"]:
            _validate(value, part, schema_root, path, errors)

    if "if" in schema:
        condition_errors: List[str] = []
        _validate(value, schema["if"], schema_root, path, condition_errors)
        branch = schema.get("then") if not condition_errors else schema.get("else")
        if isinstance(branch, dict):
            _validate(value, branch, schema_root, path, errors)

    expected_type = schema.get("type")
    if expected_type and not _matches_type(value, expected_type):
        _validation_error(errors, path, f"expected {expected_type}, got {_json_type_name(value)}")
        return

    enum_values = schema.get("enum")
    if enum_values is not None and value not in enum_values:
        _validation_error(errors, path, f"expected one of {enum_values}, got {value!r}")
        return

    if "const" in schema and value != schema["const"]:
        _validation_error(errors, path, f"expected constant {schema['const']!r}, got {value!r}")

    pattern = schema.get("pattern")
    if pattern is not None and isinstance(value, str) and re.search(str(pattern), value) is None:
        _validation_error(errors, path, f"value does not match pattern {pattern!r}")

    minimum = schema.get("minimum")
    if minimum is not None and isinstance(value, (int, float)) and not isinstance(value, bool) and value < minimum:
        _validation_error(errors, path, f"expected value >= {minimum}, got {value}")

    min_length = schema.get("minLength")
    if min_length is not None and isinstance(value, str) and len(value) < int(min_length):
        _validation_error(errors, path, f"expected length >= {min_length}, got {len(value)}")

    if expected_type == "object":
        required = schema.get("required", [])
        for key in required:
            if not isinstance(value, dict) or key not in value:
                _validation_error(errors, path, f"missing required key '{key}'")
        if not isinstance(value, dict):
            return

        min_properties = schema.get("minProperties")
        if min_properties is not None and len(value) < int(min_properties):
            _validation_error(errors, path, f"expected at least {min_properties} properties, got {len(value)}")

        properties = schema.get("properties", {})
        for key, prop_schema in properties.items():
            if key in value:
                path.append(key)
                try:
                    _validate(value[key], prop_schema, schema_root, path, errors)
                finally:
                    path.pop()

        additional = schema.get("additionalProperties")
        if additional is not None:
            for key, child in value.items():
                if key not in properties:
                    if additional is False:
                        _validation_error(errors, path, f"unexpected key '{key}'")
                    elif isinstance(additional, dict):
                        path.append(key)
                        try:
                            _validate(child, additional, schema_root, path, errors)
                        finally:
                            path.pop()
        return

    if expected_type == "array":
        if not isinstance(value, list):
            return
        min_items = schema.get("minItems")
        if min_items is not None and len(value) < int(min_items):
            _validation_error(errors, path, f"expected at least {min_items} items, got {len(value)}")
        if schema.get("uniqueItems") is True:
            for idx, item in enumerate(value):
                if any(item == previous for previous in value[:idx]):
                    path.append(idx)
                    try:
                        _validation_error(errors, path, "duplicate item violates uniqueItems")
                    finally:
                        path.pop()
                    break
        item_schema = schema.get("items")
        if item_schema:
            for idx, item in enumerate(value):
                path.append(idx)
                try:
                    _validate(item, item_schema, schema_root, path, errors)
                finally:
                    path.pop()
        return


def validate_against_schema(schema_path: Path, contract_name: str, payload: Any) -> List[str]:
    schema = load_json(schema_path)
    errors: List[str] = []
    _validate(payload, schema, schema, [contract_name], errors)
    return errors


def ensure_against_schema(schema_path: Path, contract_name: str, payload: Any) -> None:
    errors = validate_against_schema(schema_path, contract_name, payload)
    if errors:
        raise ArtifactValidationError(
            f"{contract_name} failed schema validation:\n" + "\n".join(errors[:25])
        )


def validate_payload(artifact_name: str, payload: Any) -> List[str]:
    return validate_against_schema(ARTIFACT_SCHEMAS[artifact_name], artifact_name, payload)


def ensure_valid_payload(artifact_name: str, payload: Any) -> None:
    ensure_against_schema(ARTIFACT_SCHEMAS[artifact_name], artifact_name, payload)


def validate_artifact_file(artifact_name: str) -> List[str]:
    artifact_path = ARTIFACT_PATHS[artifact_name]
    metadata = ARTIFACT_METADATA[artifact_name]
    if metadata.get("storage_class") == "managed_runtime_artifact":
        from tools.core.json_io import load_raw_artifact_path_strict

        payload = load_raw_artifact_path_strict(artifact_path)
    else:
        payload = load_json(artifact_path)
    if artifact_name == "telemetry_traces":
        payload.setdefault("traces", [])
        payload.setdefault("hot_paths", {})
        payload.setdefault("meta", {"kind": "local_telemetry_traces", "trace_origin_policy": "legacy-compatible validation"})
        for trace in payload.get("traces", []):
            if isinstance(trace, dict):
                trace.setdefault("trace_origin", "legacy")
    elif artifact_name == "local_telemetry_analysis":
        payload.setdefault("trace_origin_summary", {"legacy": int(payload.get("total_captured_traces", 0) or 0)})
        payload.setdefault("coverage_gap_label", "local_session_coverage_gap")
    return validate_payload(artifact_name, payload)


def validate_all_artifacts() -> Dict[str, List[str]]:
    results = {}
    mandatory = mandatory_artifact_ids()

    for artifact_name, artifact_path in ARTIFACT_PATHS.items():
        if not artifact_path.exists():
            if artifact_name in mandatory:
                results[artifact_name] = [f"missing mandatory artifact file: {artifact_path}"]
            else:
                # Optional/Derived artifact missing - skip validation without error
                pass
            continue
        
        results[artifact_name] = validate_artifact_file(artifact_name)
    return results
