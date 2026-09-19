from __future__ import annotations

import hashlib
import fnmatch
import json
import re
from pathlib import Path
from typing import Any

from tools.core.jsonc import loads_jsonc
from tools.core.json_syntax import loads_json_strict


CODE_MAPS_DIR = Path(__file__).resolve().parents[2]
CONFIG_DIR = CODE_MAPS_DIR / "config"
CONTRACT_FILE = CONFIG_DIR / "target_policy_profile_contract.json"
LANGUAGE_REGISTRY_FILE = CONFIG_DIR / "language_registry.json"


def _load_source_owned_object(path: Path, *, label: str) -> dict[str, Any]:
    """Read a source-owned JSON contract without importing runtime storage/config."""

    try:
        payload = loads_json_strict(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f"{label} is unavailable or invalid: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} root must be a JSON object: {path}")
    return payload


def _config_file_marker_map() -> dict[str, list[str]]:
    registry = _load_source_owned_object(
        LANGUAGE_REGISTRY_FILE,
        label="Language registry",
    )
    mapping = registry.get("config_file_markers")
    if not isinstance(mapping, dict):
        raise ValueError("Language registry config_file_markers must be an object")
    return {
        str(key): [str(item) for item in value if str(item).strip()]
        for key, value in mapping.items()
        if isinstance(value, list)
    }


def load_target_policy_contract() -> dict[str, Any]:
    return _load_source_owned_object(
        CONTRACT_FILE,
        label="Target policy profile contract",
    )


def _payload_sha256(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rule_level(value: Any) -> str | None:
    raw = value[0] if isinstance(value, list) and value else value
    if isinstance(raw, dict):
        raw = raw.get("level")
    if raw in (0, False, "off"):
        return "disabled"
    if raw in (1, "warn", "warning", "info"):
        return "advisory"
    if raw in (2, True, "on", "error"):
        return "enforced"
    return None


def _scope_payload(container: dict[str, Any], *, biome: bool = False) -> dict[str, Any]:
    include_key = "includes" if biome else "files"
    exclude_key = "ignores" if biome else "excludedFiles"
    nested_files = container.get("files") if biome and isinstance(container.get("files"), dict) else {}
    direct_includes = container.get(include_key)
    nested_includes = nested_files.get("includes") if isinstance(nested_files, dict) else None
    unresolved = bool(
        biome
        and direct_includes is not None
        and nested_includes is not None
        and direct_includes != nested_includes
    )
    includes = direct_includes if direct_includes is not None else nested_includes
    if includes is None:
        includes = ["**/*"]
    excludes = container.get(exclude_key, [])
    if not isinstance(includes, list):
        includes = [includes]
    if not isinstance(excludes, list):
        excludes = [excludes]
    scope = {
        "includes": [str(item) for item in includes if str(item).strip()],
        "excludes": [str(item) for item in excludes if str(item).strip()],
    }
    if unresolved:
        scope["resolution"] = "unresolved_multiple_scope_declarations"
    return scope


def _literal_rule_rows(
    tool_id: str,
    payload: dict[str, Any],
    *,
    max_rules: int,
) -> tuple[list[dict[str, Any]], bool]:
    rows: list[dict[str, Any]] = []
    containers: list[tuple[dict[str, Any], dict[str, Any]]] = []
    if tool_id in {"eslint", "stylelint"}:
        containers.append((payload, _scope_payload(payload)))
        for override in payload.get("overrides", []) if isinstance(payload.get("overrides"), list) else []:
            if isinstance(override, dict):
                containers.append((override, _scope_payload(override)))
        for container, scope in containers:
            rules = container.get("rules") if isinstance(container.get("rules"), dict) else {}
            for rule_id, value in sorted(rules.items()):
                level = _rule_level(value)
                if level:
                    row = {"id": str(rule_id), "state": level, "scope": scope}
                    option_payload = (
                        value[1]
                        if isinstance(value, list) and len(value) > 1 and isinstance(value[1], dict)
                        else value.get("options")
                        if isinstance(value, dict) and isinstance(value.get("options"), dict)
                        else None
                    )
                    if isinstance(option_payload, dict):
                        numeric = {
                            str(key): number
                            for key, number in option_payload.items()
                            if isinstance(number, (int, float)) and not isinstance(number, bool)
                        }
                        if numeric:
                            row["numeric_options"] = numeric
                    rows.append(row)
    elif tool_id == "biome":
        containers.append((payload, _scope_payload(payload, biome=True)))
        for override in payload.get("overrides", []) if isinstance(payload.get("overrides"), list) else []:
            if isinstance(override, dict):
                containers.append((override, _scope_payload(override, biome=True)))
        for container, scope in containers:
            linter = container.get("linter") if isinstance(container.get("linter"), dict) else {}
            rules = linter.get("rules") if isinstance(linter.get("rules"), dict) else {}
            for group, group_rules in sorted(rules.items()):
                if group == "recommended" or not isinstance(group_rules, dict):
                    continue
                for rule_id, value in sorted(group_rules.items()):
                    level = _rule_level(value)
                    if level:
                        row = {"id": f"{group}/{rule_id}", "state": level, "scope": scope}
                        option_payload = value.get("options") if isinstance(value, dict) else None
                        if isinstance(option_payload, dict):
                            numeric = {
                                str(key): number
                                for key, number in option_payload.items()
                                if isinstance(number, (int, float)) and not isinstance(number, bool)
                            }
                            if numeric:
                                row["numeric_options"] = numeric
                        rows.append(row)
    truncated = len(rows) > max_rules
    return rows[:max_rules], truncated


def _static_projection(path: Path, tool_id: str, policy: dict[str, Any]) -> dict[str, Any]:
    suffix = path.suffix.lower()
    name = path.name.lower()
    executable_suffixes = {".js", ".cjs", ".mjs", ".ts", ".cts", ".mts"}
    if suffix in executable_suffixes:
        return {"status": "executable_config_not_statically_resolved", "tool_state": "unknown"}
    if tool_id not in {"eslint", "stylelint", "biome", "typescript", "sonar"}:
        return {"status": "inventory_only_config_identity", "tool_state": "unknown"}
    static_policy = policy.get("static_projection") if isinstance(policy.get("static_projection"), dict) else {}
    if tool_id == "sonar" and name == "sonar-project.properties":
        allowed = {str(item) for item in static_policy.get("sonar_property_allowlist", [])}
        properties: dict[str, str] = {}
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            if key.strip() in allowed:
                properties[key.strip()] = value.strip()
        return {"status": "partial_literal_projection", "tool_state": "declared", "properties": properties}
    if suffix not in {".json", ".jsonc"} and name not in {".eslintrc", ".stylelintrc"}:
        return {"status": "unsupported_static_config_format", "tool_state": "unknown"}
    try:
        payload = loads_jsonc(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return {"status": "literal_config_unreadable", "tool_state": "unknown"}
    if not isinstance(payload, dict):
        return {"status": "literal_config_invalid_shape", "tool_state": "unknown"}
    if tool_id == "typescript":
        compiler = payload.get("compilerOptions") if isinstance(payload.get("compilerOptions"), dict) else {}
        allowed = {str(item) for item in static_policy.get("typescript_compiler_options", [])}
        selected = {key: compiler[key] for key in sorted(compiler) if key in allowed and isinstance(compiler[key], (str, int, float, bool))}
        return {
            "status": "partial_literal_projection",
            "tool_state": "declared",
            "compiler_options": selected,
            "extends_unresolved": bool(payload.get("extends")),
        }
    tool_state = "unknown"
    if tool_id == "biome":
        linter = payload.get("linter") if isinstance(payload.get("linter"), dict) else {}
        if isinstance(linter.get("enabled"), bool):
            tool_state = "enabled" if linter["enabled"] else "disabled"
    rules, truncated = _literal_rule_rows(
        tool_id,
        payload,
        max_rules=max(1, int(static_policy.get("max_rules_per_config", 500) or 500)),
    )
    return {
        "status": "partial_literal_projection",
        "tool_state": tool_state,
        "rules": rules,
        "rules_truncated": truncated,
        "extends_unresolved": bool(payload.get("extends")),
    }


def _config_rows(
    project_root: Path,
    workspace_root: Path,
    groups: list[str],
    marker_map: dict[str, list[str]],
    *,
    tool_id: str,
    policy: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for group in groups:
        for pattern in marker_map.get(group, []):
            for candidate in project_root.glob(str(pattern)):
                if not candidate.is_file():
                    continue
                resolved = candidate.resolve()
                if project_root.resolve() not in (resolved, *resolved.parents):
                    continue
                rel = _relative(resolved, workspace_root)
                rows[rel] = {
                    "path": rel,
                    "config_group": group,
                    "sha256": _sha256(resolved),
                    "bytes": resolved.stat().st_size,
                    "interpretation": "source_identity_only",
                    "static_projection": _static_projection(resolved, tool_id, policy),
                }
    return [rows[key] for key in sorted(rows)]


def _declared_packages(package_json: dict[str, Any], sections: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for section in sections:
        values = package_json.get(section)
        if not isinstance(values, dict):
            continue
        for name, version in values.items():
            result[str(name)] = str(version)
    return result


def _package_matches(name: str, tool: dict[str, Any]) -> bool:
    lowered = name.lower()
    exact = {str(item).lower() for item in tool.get("package_exact", [])}
    prefixes = tuple(str(item).lower() for item in tool.get("package_prefixes", []))
    return lowered in exact or bool(prefixes and lowered.startswith(prefixes))


def _command_mentions(command: str, token: str) -> bool:
    return bool(re.search(rf"(?<![A-Za-z0-9_@/-]){re.escape(token)}(?![A-Za-z0-9_-])", command, re.IGNORECASE))


def inventory_project_target_policy(
    project_root: Path,
    *,
    project: str,
    package_json: dict[str, Any] | None = None,
    package_path: Path | None = None,
    workspace_root: Path | None = None,
    contract: dict[str, Any] | None = None,
    marker_map: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Inventory declared policy sources without importing or executing target config."""

    root = project_root.resolve()
    workspace = (workspace_root or root).resolve()
    policy = contract or load_target_policy_contract()
    markers = marker_map or _config_file_marker_map()
    package_payload = package_json if isinstance(package_json, dict) else {}
    packages = _declared_packages(package_payload, [str(item) for item in policy.get("package_sections", [])])
    scripts = package_payload.get("scripts") if isinstance(package_payload.get("scripts"), dict) else {}
    authority = policy.get("authority") if isinstance(policy.get("authority"), dict) else {}
    rows: list[dict[str, Any]] = []

    for tool_id, tool_payload in sorted((policy.get("tools") or {}).items()):
        if not isinstance(tool_payload, dict):
            continue
        configs = _config_rows(
            root,
            workspace,
            [str(item) for item in tool_payload.get("config_groups", [])],
            markers,
            tool_id=str(tool_id),
            policy=policy,
        )
        package_hits = [
            {"name": name, "declared_version": packages[name]}
            for name in sorted(packages)
            if _package_matches(name, tool_payload)
        ]
        script_hits = []
        for name, command in sorted(scripts.items()):
            command_text = str(command)
            tokens = [str(item) for item in tool_payload.get("command_tokens", [])]
            if any(_command_mentions(command_text, token) for token in tokens):
                script_hits.append({"name": str(name), "command": command_text})
        if configs or package_hits or script_hits:
            projection_statuses = {
                str(config.get("static_projection", {}).get("status"))
                for config in configs
                if isinstance(config.get("static_projection"), dict)
            }
            tool_states = [
                str(config.get("static_projection", {}).get("tool_state"))
                for config in configs
                if isinstance(config.get("static_projection"), dict)
            ]
            if "enabled" in tool_states:
                declared_tool_state = "enabled"
            elif tool_states and all(state == "disabled" for state in tool_states):
                declared_tool_state = "disabled"
            elif "declared" in tool_states:
                declared_tool_state = "declared"
            else:
                declared_tool_state = "unknown"
            rows.append(
                {
                    "id": str(tool_id),
                    "status": str(authority.get("declared") or "declared_not_evaluated"),
                    "config_files": configs,
                    "packages": package_hits,
                    "scripts": script_hits,
                    "effective_rule_resolution": (
                        "partial_literal_projection"
                        if "partial_literal_projection" in projection_statuses
                        else "not_evaluated"
                    ),
                    "policy_adapter": (
                        "enabled"
                        if "partial_literal_projection" in projection_statuses
                        else "inventory_only"
                    ),
                    "declared_tool_state": declared_tool_state,
                    "native_execution": "not_run",
                }
            )

    status = (
        str(authority.get("declared") or "declared_not_evaluated")
        if rows
        else str(authority.get("not_observed") or "not_observed_in_configured_taxonomy")
    )
    tool_ids = [row["id"] for row in rows]
    policy_adapters = {row["id"]: row["policy_adapter"] for row in rows}
    target_tool_states = {row["id"]: row["declared_tool_state"] for row in rows}
    manifest = None
    package_boundary = (
        package_path.resolve() == workspace or workspace in package_path.resolve().parents
        if package_path is not None
        else False
    )
    if package_path is not None and package_boundary and package_path.is_file():
        manifest = {
            "path": _relative(package_path.resolve(), workspace),
            "sha256": _sha256(package_path.resolve()),
            "bytes": package_path.stat().st_size,
            "interpretation": "declared_package_and_script_identity",
        }
    return {
        "contract": "target_policy_profile_v1",
        "contract_source": {
            "path": "config/target_policy_profile_contract.json",
            "content_sha256": _payload_sha256(policy),
        },
        "project": str(project),
        "scope_root": _relative(root, workspace) or ".",
        "status": status,
        "declared_tools": tool_ids,
        "manifest": manifest,
        "tools": rows,
        "feature_flags": {
            "sage_native_analysis": "enabled",
            "target_policy_inventory": "enabled" if rows else "no_signal",
            "policy_adapters": policy_adapters,
            "target_declared_tool_states": target_tool_states,
            "target_native_execution": "not_evaluated",
            "combined_governance_verdict": str(authority.get("combined_without_execution") or "not_available"),
        },
        "claim_boundary": str(authority.get("claim_boundary") or ""),
        "absence_semantics": "not_observed_does_not_prove_absence_or_disable_a_rule",
    }


def aggregate_effective_target_policy(
    projects: dict[str, dict[str, Any]],
    *,
    selected_project_count: int | None = None,
    missing_policy_projects: list[str] | None = None,
) -> dict[str, Any]:
    missing_projects = sorted({str(item) for item in (missing_policy_projects or []) if str(item).strip()})
    declared_tools = sorted({
        str(tool)
        for profile in projects.values()
        for tool in profile.get("declared_tools", [])
        if str(tool).strip()
    })
    declared_projects = sorted(
        project
        for project, profile in projects.items()
        if profile.get("status") == "declared_not_evaluated"
    )
    adapter_state_sets: dict[str, set[str]] = {}
    target_tool_state_sets: dict[str, set[str]] = {}
    for profile in projects.values():
        flags = profile.get("feature_flags") if isinstance(profile.get("feature_flags"), dict) else {}
        adapters = flags.get("policy_adapters") if isinstance(flags.get("policy_adapters"), dict) else {}
        for tool_id, state in adapters.items():
            adapter_state_sets.setdefault(str(tool_id), set()).add(str(state))
        declared_states = flags.get("target_declared_tool_states") if isinstance(flags.get("target_declared_tool_states"), dict) else {}
        for tool_id, state in declared_states.items():
            target_tool_state_sets.setdefault(str(tool_id), set()).add(str(state))
    adapter_states = {
        tool_id: next(iter(states)) if len(states) == 1 else "mixed"
        for tool_id, states in adapter_state_sets.items()
    }
    target_tool_states = {
        tool_id: next(iter(states)) if len(states) == 1 else "mixed"
        for tool_id, states in target_tool_state_sets.items()
    }
    authority = load_target_policy_contract().get("authority", {})
    if missing_projects:
        status = str(authority.get("profile_incomplete") or "profile_incomplete_refresh_required")
    elif declared_projects:
        status = str(authority.get("declared") or "declared_not_evaluated")
    else:
        status = str(authority.get("not_observed") or "not_observed_in_configured_taxonomy")
    return {
        "contract": "effective_target_policy_v1",
        "status": status,
        "projects": projects,
        "summary": {
            "selected_projects": int(selected_project_count if selected_project_count is not None else len(projects)),
            "profiled_projects": len(projects),
            "declared_policy_projects": declared_projects,
            "missing_policy_projects": missing_projects,
            "declared_tools": declared_tools,
            "native_execution": "not_run",
            "combined_governance_verdict": "not_available",
        },
        "feature_flags": {
            "sage_native_analysis": "enabled",
            "target_policy_inventory": (
                "refresh_required"
                if missing_projects
                else "enabled" if declared_projects else "no_signal"
            ),
            "target_native_execution": "not_evaluated",
            "policy_adapters": dict(sorted(adapter_states.items())),
            "target_declared_tool_states": dict(sorted(target_tool_states.items())),
            "combined_governance_verdict": "not_available",
        },
        "claim_boundary": (
            "Runtime projection of repository-declared policy sources. Rule-level activation remains UNKNOWN "
            "until a safe static resolver or provenance-bound native adapter establishes effective policy."
        ),
    }


def compile_effective_target_policy(
    discovery: dict[str, Any],
    variations: dict[str, str],
) -> dict[str, Any]:
    """Project the discovery inventory into runtime config without broadening authority."""

    metadata = discovery.get("_discovery_metadata") if isinstance(discovery.get("_discovery_metadata"), dict) else {}
    discovered_projects = metadata.get("projects") if isinstance(metadata.get("projects"), dict) else {}
    projects: dict[str, dict[str, Any]] = {}
    missing_projects: list[str] = []
    for project in sorted(variations):
        project_meta = discovered_projects.get(project) if isinstance(discovered_projects.get(project), dict) else {}
        signals = project_meta.get("workspace_signals") if isinstance(project_meta.get("workspace_signals"), dict) else {}
        profile = signals.get("target_policy") if isinstance(signals.get("target_policy"), dict) else None
        if profile:
            projects[project] = profile
        else:
            missing_projects.append(project)
    return aggregate_effective_target_policy(
        projects,
        selected_project_count=len(variations),
        missing_policy_projects=missing_projects,
    )


def _static_scope_pattern_matches(file_path: str, pattern: str) -> bool | None:
    normalized_path = str(file_path or "").replace("\\", "/").strip("/")
    normalized_pattern = str(pattern or "").replace("\\", "/").strip()
    if not normalized_pattern or normalized_pattern.startswith("!"):
        return None
    if any(token in normalized_pattern for token in ("{", "}", "!(", "@(", "+(", "?(", "*(")):
        return None
    if normalized_pattern.count("**/") > 4:
        return None
    normalized_pattern = normalized_pattern.lstrip("./")
    if normalized_pattern in {"*", "**", "**/*"}:
        return True
    candidates = {normalized_pattern}
    pending = [normalized_pattern]
    while pending:
        candidate = pending.pop()
        marker = candidate.find("**/")
        if marker < 0:
            continue
        collapsed = candidate[:marker] + candidate[marker + 3 :]
        if collapsed not in candidates:
            candidates.add(collapsed)
            pending.append(collapsed)
    return any(fnmatch.fnmatchcase(normalized_path, candidate) for candidate in candidates)


def _static_scope_matches(file_path: str, scope: dict[str, Any]) -> bool | None:
    if str(scope.get("resolution") or "").startswith("unresolved"):
        return None
    includes = scope.get("includes") if isinstance(scope.get("includes"), list) else ["**/*"]
    excludes = scope.get("excludes") if isinstance(scope.get("excludes"), list) else []
    include_results = [_static_scope_pattern_matches(file_path, str(pattern)) for pattern in includes]
    exclude_results = [_static_scope_pattern_matches(file_path, str(pattern)) for pattern in excludes]
    if any(result is None for result in include_results + exclude_results):
        return None
    return any(include_results) and not any(exclude_results)


def resolve_target_symbol_loc_policy(
    effective_target_policy: dict[str, Any] | None,
    *,
    project: str,
    file_path: str,
    symbol_kind: str,
    sage_default_limit: int,
    contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve a bounded literal target LOC rule without claiming native execution."""

    policy = effective_target_policy if isinstance(effective_target_policy, dict) else {}
    projects = policy.get("projects") if isinstance(policy.get("projects"), dict) else {}
    project_policy = projects.get(str(project)) if isinstance(projects.get(str(project)), dict) else {}
    result = {
        "status": "project_policy_unavailable",
        "limit": int(sage_default_limit),
        "limit_authority": "sage_default",
        "project": str(project),
        "file": str(file_path or "").replace("\\", "/"),
        "symbol_kind": str(symbol_kind or "unknown").lower(),
        "matched_rules": [],
        "unresolved_reasons": [],
        "native_execution": "not_run",
    }
    if not project_policy:
        return result
    policy_contract = contract or load_target_policy_contract()
    adapter = (
        policy_contract.get("consumer_adapters", {}).get("symbol_loc_limit", {})
        if isinstance(policy_contract.get("consumer_adapters"), dict)
        else {}
    )
    mappings = adapter.get("supported_rule_mappings") if isinstance(adapter.get("supported_rule_mappings"), list) else []
    mapping_by_key = {
        (str(row.get("tool")), str(row.get("rule"))): row
        for row in mappings
        if isinstance(row, dict)
        and result["symbol_kind"] in {str(kind).lower() for kind in row.get("symbol_kinds", [])}
    }
    matches: list[dict[str, Any]] = []
    unresolved: list[str] = []
    for tool in project_policy.get("tools", []):
        if not isinstance(tool, dict):
            continue
        tool_id = str(tool.get("id") or "")
        relevant_mappings = {
            rule_id: row
            for (mapped_tool, rule_id), row in mapping_by_key.items()
            if mapped_tool == tool_id
        }
        if not relevant_mappings:
            continue
        configs = [row for row in tool.get("config_files", []) if isinstance(row, dict)]
        if len(configs) != 1:
            if configs:
                unresolved.append(f"{tool_id}:multiple_config_precedence_unknown")
            continue
        projection = configs[0].get("static_projection") if isinstance(configs[0].get("static_projection"), dict) else {}
        if projection.get("status") != "partial_literal_projection":
            unresolved.append(f"{tool_id}:config_not_literal")
            continue
        if projection.get("extends_unresolved"):
            unresolved.append(f"{tool_id}:extends_unresolved")
            continue
        if projection.get("rules_truncated"):
            unresolved.append(f"{tool_id}:rules_truncated")
            continue
        tool_matches: dict[str, dict[str, Any]] = {}
        for rule in projection.get("rules", []):
            if not isinstance(rule, dict):
                continue
            rule_id = str(rule.get("id") or "")
            mapping = relevant_mappings.get(rule_id)
            if not mapping:
                continue
            scope = rule.get("scope") if isinstance(rule.get("scope"), dict) else {}
            scope_match = _static_scope_matches(result["file"], scope)
            if scope_match is None:
                unresolved.append(f"{tool_id}:{rule_id}:scope_unresolved")
                continue
            if not scope_match:
                continue
            numeric_options = rule.get("numeric_options") if isinstance(rule.get("numeric_options"), dict) else {}
            option_name = str(mapping.get("numeric_option") or "")
            raw_limit = numeric_options.get(option_name)
            limit = (
                int(raw_limit)
                if isinstance(raw_limit, (int, float))
                and not isinstance(raw_limit, bool)
                and int(raw_limit) > 0
                else None
            )
            tool_matches[rule_id] = {
                "tool": tool_id,
                "rule": rule_id,
                "state": str(rule.get("state") or "unknown"),
                "limit": limit,
                "config_path": configs[0].get("path"),
                "config_sha256": configs[0].get("sha256"),
                "scope": scope,
            }
        matches.extend(tool_matches.values())
    result["matched_rules"] = matches
    result["unresolved_reasons"] = sorted(set(unresolved))
    if unresolved:
        result["status"] = "unresolved_fallback_to_sage_default"
        return result
    if not matches:
        result["status"] = "no_matching_static_rule"
        return result
    enabled = [row for row in matches if row.get("state") in {"advisory", "enforced"} and row.get("limit")]
    disabled = [row for row in matches if row.get("state") == "disabled"]
    if disabled and not enabled:
        result["status"] = "target_rule_disabled_sage_signal_retained"
        result["limit_authority"] = "sage_default_target_rule_disabled"
        return result
    limits = {int(row["limit"]) for row in enabled}
    if disabled or len(limits) != 1 or len(enabled) != len(matches):
        result["status"] = "conflicting_static_rules_fallback_to_sage_default"
        return result
    result["status"] = "resolved_declared_literal_policy"
    result["limit"] = next(iter(limits))
    result["limit_authority"] = str(adapter.get("authority") or "declared_literal_policy")
    return result
