"""Pure bounded admission for literal LOC policy, not a native config evaluator."""
from __future__ import annotations

import math
import re
from typing import Any
from tools.core.atlas_ast_contract import loc_measurement_record_valid


def loc_measurement_admission(match: dict, context: dict | None, policy: dict) -> dict:
    """Apply one central measurement contract, never a caller's compatibility flag."""
    spec = policy["static_projection"]["literal_applicability"]["measurement_contract"]
    result = {"contract": spec["id"], "sage_basis": spec["sage_basis"], "status": "unresolved",
        "reasons": [], "native_execution": "not_run"}
    reason = None
    versions = match.get("declared_package_versions")
    if (match.get("tool"), match.get("rule")) != (spec["tool"], spec["rule"]):
        reason = "native_measurement_basis_not_supported"
    elif not isinstance(versions, list) or not versions or any(v not in spec["reviewed_declared_versions"] for v in versions):
        reason = "declared_measurement_semantics_unresolved"
    elif (match.get("option_admission") != "bounded_explicit_options" or not isinstance(match.get("counting_options"), dict)
            or any(type(value) is not bool for value in match["counting_options"].values())
            or not set(match["counting_options"]) <= set(policy["static_projection"]["literal_applicability"]["tools"][spec["tool"]]["loc_options"]["boolean_options"])):
        reason = "measurement_options_unavailable"
    elif any(match.get("counting_options", {}).get(flag, False) is not False for flag in spec["false_counting_flags"]):
        reason = "counting_options_not_declaration_lines"
    if reason:
        result["reasons"] = [reason]
        return result
    context = context if isinstance(context, dict) else {}
    symbol = context.get("symbol")
    record = symbol.get("loc_measurement_evidence") if isinstance(symbol, dict) else None
    if (not loc_measurement_record_valid(record, symbol, offset_unit="utf8_bytes")
            or not isinstance(context.get("source_hash"), str) or not context["source_hash"]
            or record.get("atlas_source_hash") != context["source_hash"]):
        result["reasons"] = ["source_measurement_unavailable_or_unbound"]
        return result
    decl, call = record["spans"]["declaration"], record["spans"]["callable"]
    if (type(context.get("observed_loc")) is not int
            or context["observed_loc"] != decl["end_line"] - decl["line"] + 1
            or type(context.get("start_line")) is not int or type(context.get("end_line")) is not int
            or context["start_line"] != decl["line"] or context["end_line"] != decl["end_line"]):
        result["reasons"] = ["observed_measurement_not_source_bound"]
    elif (decl["line"], decl["end_line"]) != (call["line"], call["end_line"]):
        result["reasons"] = ["callable_and_declaration_line_bounds_differ"]
    else:
        result.update(status="bounded_static_compatible", source_hash=record["source_hash"],
            ast_contract_version=record["ast_contract_version"], callable_kind=record["callable_kind"],
            spans={key: dict(span) for key, span in record["spans"].items()}, declared_semantics=list(versions))
    return result


def _present(payload: dict[str, Any], path: str) -> bool:
    current: Any = payload
    for component in path.split("."):
        if not isinstance(current, dict) or component not in current:
            return False
        current = current[component]
    return True


def _exact_version(raw: Any, max_chars: int) -> tuple[int, int, int] | None:
    if not isinstance(raw, str) or len(raw) > max_chars:
        return None
    match = re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", raw)
    return tuple(int(part) for part in match.groups()) if match else None


def literal_loc_limit(value: Any, tool_id: str, contract: dict[str, Any], *,
    declared_versions: list[str] | None = None, declared_schema: str | None = None,
) -> int | None:
    """Admit exact finite integers in the source-owned supported subset only."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and (not math.isfinite(value) or not value.is_integer()):
        return None
    policy = contract["static_projection"]["literal_applicability"]
    tool = policy["tools"][tool_id]
    options = tool["loc_options"]
    if options["format"] not in {"eslint_array_object", "biome_object_options"}:
        return None
    maximum = options["supported_maximum"]
    if declared_versions is not None and not isinstance(declared_versions, list):
        return None
    versions = [_exact_version(raw, policy["max_identity_chars"]) for raw in (declared_versions or [])]
    if declared_schema is not None:
        match = (re.fullmatch(tool["schema_version_pattern"], declared_schema)
            if isinstance(declared_schema, str) and len(declared_schema) <= policy["max_identity_chars"]
            and tool.get("schema_version_pattern") else None)
        versions.append(_exact_version(match.group(1), policy["max_identity_chars"]) if match else None)
    if any(version is None or version[0] not in tool["supported_declared_majors"] for version in versions):
        return None
    for bound in options["version_maximums"]:
        # Unknown identity cannot select the newer, wider option range.
        if not versions or any(version is None or version < tuple(bound["before"]) for version in versions):
            maximum = min(maximum, bound["maximum"])
    return int(value) if options["supported_minimum"] <= value <= maximum else None


def _loc_option_evidence(value: Any, tool_id: str, contract: dict[str, Any], *,
    declared_versions: list[str] | None, declared_schema: str | None,
) -> dict[str, Any]:
    spec = contract["static_projection"]["literal_applicability"]["tools"][tool_id]["loc_options"]
    evidence = {"rule": spec["rule_id"], "format": spec["format"], "status": "not_provided",
        "counting_options": {}, "numeric_limit": None, "reasons": [],
        "measurement_compatibility": "not_evaluated", "native_execution": "not_run"}
    if spec["format"] == "eslint_array_object":
        if not isinstance(value, list) or len(value) < 2:
            return evidence
        options = value[1]
        if len(value) != 2:
            evidence["reasons"].append("excess_rule_option_arguments")
    elif spec["format"] == "biome_object_options":
        if not isinstance(value, dict) or "options" not in value:
            return evidence
        options = value["options"]
    else:
        evidence.update(status="unresolved", reasons=["unsupported_loc_option_format"])
        return evidence
    if not isinstance(options, dict):
        evidence["reasons"].append("unsupported_explicit_option_shape")
    else:
        numeric = spec["numeric_option"]
        unknown = set(options) - {numeric, *spec["boolean_options"]}
        if unknown:
            evidence["reasons"].append("unknown_option_keys")
            evidence["unknown_option_count"] = len(unknown)
        for flag in spec["boolean_options"]:
            if flag in options:
                if not isinstance(options[flag], bool):
                    evidence["reasons"].append("counting_option_type_unresolved")
                else:
                    evidence["counting_options"][flag] = options[flag]
        if numeric not in options:
            evidence["reasons"].append("explicit_numeric_limit_unavailable")
        else:
            limit = literal_loc_limit(options[numeric], tool_id, contract,
                declared_versions=declared_versions, declared_schema=declared_schema)
            evidence["numeric_input_type"] = type(options[numeric]).__name__
            if limit is None:
                evidence["reasons"].append("numeric_limit_outside_supported_subset")
            else:
                evidence["numeric_limit"] = limit
    evidence["status"] = "unresolved" if evidence["reasons"] else "bounded_explicit_options"
    evidence["reasons"] = sorted(set(evidence["reasons"]))
    return evidence


def project_literal_applicability(
    tool_id: str, payload: dict[str, Any], contract: dict[str, Any], *, declared_versions: list[str] | None = None,
) -> dict[str, Any]:
    """Refuse relevant context the current static adapter cannot compose.

    Identity is declared config/package evidence only; nothing is fetched,
    installed, imported or executed, and SAGE native analysis stays independent.
    """
    policy = contract["static_projection"]["literal_applicability"]
    tool = policy["tools"][tool_id]
    versions = declared_versions or []
    reasons: list[str] = []
    loc_option_evidence: list[dict[str, Any]] = []
    loc_option_evidence_omitted = 0
    max_chars = policy["max_identity_chars"]
    schema = payload.get("$schema")
    schema_version = None
    if "$schema" in payload:
        if isinstance(schema, str) and len(schema) <= max_chars and tool.get("schema_version_pattern"):
            match = re.fullmatch(tool["schema_version_pattern"], schema)
            schema_version = _exact_version(match.group(1), max_chars) if match else None
        if schema_version is None:
            reasons.append("schema_declaration_unresolved")
        elif schema_version[0] not in tool["supported_declared_majors"]:
            reasons.append("unsupported_schema_major")
    parsed = [_exact_version(version, max_chars) for version in versions]
    if any(version is None for version in parsed):
        reasons.append("package_version_declaration_unresolved")
    exact = {version for version in parsed if version is not None}
    if len(exact) > 1:
        reasons.append("conflicting_package_version_declarations")
    if any(version[0] not in tool["supported_declared_majors"] for version in exact):
        reasons.append("unsupported_package_major")
    minimum = tuple(tool.get("minimum_declared_version", []))
    if minimum and any(version < minimum for version in [*exact, *([schema_version] if schema_version else [])]):
        reasons.append("declared_version_before_mapped_rule_group")
    if schema_version and any(version != schema_version for version in exact):
        reasons.append("schema_package_version_conflict")
    if tool["requires_declared_version"] and not schema_version and not exact:
        reasons.append("declared_config_dialect_unresolved")
    for path in tool["unsupported_root_paths"]:
        if _present(payload, path):
            reasons.append(f"unsupported_root_context:{path}")
    overrides = payload.get("overrides", [])
    if not isinstance(overrides, list) or any(not isinstance(item, dict) for item in overrides):
        reasons.append("override_shape_unresolved")
        overrides = []
    if tool_id == "biome" and overrides and _present(payload, "files.includes"):
        reasons.append("global_filter_override_composition_unresolved")
    for index, container in enumerate([payload, *overrides]):
        label = "root" if index == 0 else f"override[{index - 1}]"
        raw_rule: Any = container
        for part in tool["loc_options"]["rule_path"]:
            if not isinstance(raw_rule, dict) or part not in raw_rule:
                break
            raw_rule = raw_rule[part]
        else:
            if len(loc_option_evidence) < contract["static_projection"]["max_rules_per_config"]:
                option_record = _loc_option_evidence(raw_rule, tool_id, contract,
                    declared_versions=declared_versions, declared_schema=schema)
                loc_option_evidence.append({"container": label, **option_record})
                reasons.extend(f"{label}:loc_options:{reason}" for reason in option_record["reasons"])
            else:
                loc_option_evidence_omitted += 1
        if tool_id == "biome":
            linter = container.get("linter", {})
            if not isinstance(linter, dict):
                reasons.append(f"{label}:linter_shape_unresolved")
            elif "enabled" in linter and linter["enabled"] is not True:
                reasons.append(f"{label}:linter_enablement_not_composed")
            if index == 0 and "files" in container and not isinstance(container["files"], dict):
                reasons.append("root:files_shape_unresolved")
            scope = container if index else container.get("files", {})
            if index and "includes" not in container:
                reasons.append(f"{label}:scope_shape_unresolved")
            if isinstance(scope, dict) and "includes" in scope:
                includes = scope["includes"]
                if not isinstance(includes, list) or any(not isinstance(item, str) for item in includes):
                    reasons.append(f"{label}:scope_shape_unresolved")
        elif index:
            if "files" not in container:
                reasons.append(f"{label}:files_shape_unresolved")
            for key in ("files", "excludedFiles"):
                if key in container:
                    value = container[key]
                    if not isinstance(value, str) and not (isinstance(value, list) and all(isinstance(item, str) for item in value)):
                        reasons.append(f"{label}:{key}_shape_unresolved")
        if index:
            for path in tool["unsupported_override_paths"]:
                if _present(container, path):
                    reasons.append(f"{label}:unsupported_context:{path}")
    if loc_option_evidence_omitted:
        reasons.append("loc_option_evidence_truncated")
    return {
        "contract": policy["id"],
        "status": "unresolved" if reasons else "bounded_declared_subset",
        "reasons": sorted(set(reasons)),
        "loc_option_evidence": loc_option_evidence,
        "loc_option_evidence_omitted": loc_option_evidence_omitted,
        "declared_schema": schema if isinstance(schema, str) and len(schema) <= max_chars else None,
        "declared_package_versions": [version if isinstance(version, str) and len(version) <= max_chars else None
            for version in versions],
        "identity_authority": "declared_config_and_package_only_not_installed_or_executed_identity",
        "native_execution": "not_run",
    }
