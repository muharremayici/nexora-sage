from __future__ import annotations

from typing import Any, Dict

from tools.core.config import ARCH_CONFIG, DYNAMIC_CONFIG, ENVIRONMENT, PLUGINS
from tools.core.architecture_blueprints import effective_profile_ids
from tools.core.doctrine_contract import require_doctrine_mapping
from tools.core.import_classifier import is_target_relative_import, should_enforce_alias_for_local_import


RULE_DEFINITIONS: Dict[str, Dict[str, Any]] = require_doctrine_mapping("audit_rule_profiles")
RULE_PROFILE_CONTRACT: Dict[str, Any] = require_doctrine_mapping("audit_rule_profile_contract")


def _relative_parent_escape_depth(import_path: str) -> int:
    depth = 0
    minimum_depth = 0
    normalized = str(import_path or "").split("?", 1)[0].split("#", 1)[0].replace("\\", "/")
    for part in normalized.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            depth -= 1
            minimum_depth = min(minimum_depth, depth)
        else:
            depth += 1
    return -minimum_depth


def path_alias_applies_to_file(file_path: str) -> bool:
    target_override = DYNAMIC_CONFIG.get("_target_root_override", {})
    if not isinstance(target_override, dict) or not target_override.get("enabled"):
        return bool((ENVIRONMENT.get("path_aliases") or {}).keys())
    normalized = str(file_path or "").replace("\\", "/").strip("/")
    for scope in ENVIRONMENT.get("scoped_path_aliases", []) or []:
        if not isinstance(scope, dict) or not (scope.get("path_aliases") or {}):
            continue
        scope_root = str(scope.get("scope_root") or ".").replace("\\", "/").strip("/") or "."
        if scope_root == "." or normalized == scope_root or normalized.startswith(f"{scope_root}/"):
            return True
    return False


def canonical_alias_boundary_decision(
    file_path: str,
    import_path: str,
    *,
    language: str,
    primary_alias: str,
    module_root_name: str,
    alias_contract_applies: bool,
) -> dict[str, Any]:
    """Explain whether a local import escapes its nearest architectural surface."""
    profile = RULE_DEFINITIONS.get("relative_imports_no_alias", {})
    scope = profile.get("scope_contract")
    if not isinstance(scope, dict):
        raise ValueError("Missing relative_imports_no_alias.scope_contract")
    languages = scope.get("languages")
    exempt_extensions = scope.get("exempt_import_extensions")
    if not isinstance(languages, list) or not languages:
        raise ValueError("Invalid relative_imports_no_alias.scope_contract.languages")
    if not isinstance(exempt_extensions, list):
        raise ValueError("Invalid relative_imports_no_alias.scope_contract.exempt_import_extensions")
    if scope.get("requires_workspace_path_alias") is not True:
        raise ValueError("relative_imports_no_alias must require a workspace path alias")
    if str(language or "").lower() not in {str(item).lower() for item in languages}:
        return {"violated": False, "applicable": False, "reason": "language_not_in_scope"}
    if not alias_contract_applies or not primary_alias:
        return {"violated": False, "applicable": False, "reason": "workspace_alias_not_applicable"}
    raw_import = str(import_path or "").strip()
    if not should_enforce_alias_for_local_import(raw_import, primary_alias):
        return {"violated": False, "applicable": False, "reason": "import_not_alias_governed"}
    normalized_import = raw_import.split("?", 1)[0].split("#", 1)[0].lower()
    if any(normalized_import.endswith(str(ext).lower()) for ext in exempt_extensions):
        return {"violated": False, "applicable": False, "reason": "extension_exempt"}
    if not is_target_relative_import(raw_import):
        return {"violated": True, "applicable": True, "reason": "local_non_relative_alias_bypass"}
    if scope.get("allow_within_module_relative_imports") is not True:
        return {"violated": True, "applicable": True, "reason": "relative_imports_forbidden"}
    parent_escape_depth = _relative_parent_escape_depth(raw_import)
    if parent_escape_depth == 0:
        return {
            "violated": False,
            "applicable": True,
            "reason": "same_directory_relative_import",
            "parent_escape_depth": 0,
        }

    parts = str(file_path or "").replace("\\", "/").strip("/").split("/")
    owner_index = None
    owner_kind = "top_level_surface"
    if str(module_root_name) in parts:
        module_root_index = parts.index(str(module_root_name))
        if module_root_index + 1 < len(parts) - 1:
            owner_index = module_root_index + 1
            owner_kind = "module"
    if owner_index is None:
        source_root_index = 0 if parts and parts[0].lower() in {"src", "app", "lib"} else -1
        candidate_index = source_root_index + 1
        if candidate_index < len(parts) - 1:
            owner_index = candidate_index
    if owner_index is None:
        return {
            "violated": True,
            "applicable": True,
            "reason": "ownership_surface_unresolved",
            "parent_escape_depth": parent_escape_depth,
        }

    allowed_parent_depth = max(0, len(parts) - owner_index - 2)
    violated = parent_escape_depth > allowed_parent_depth
    return {
        "violated": violated,
        "applicable": True,
        "reason": "relative_import_escapes_owner_surface" if violated else "within_owner_surface",
        "ownership_root": "/".join(parts[: owner_index + 1]),
        "ownership_kind": owner_kind,
        "parent_escape_depth": parent_escape_depth,
        "allowed_parent_depth": allowed_parent_depth,
    }


def violates_canonical_alias_boundary(
    file_path: str,
    import_path: str,
    *,
    language: str,
    primary_alias: str,
    module_root_name: str,
    alias_contract_applies: bool,
) -> bool:
    """Evaluate the centrally declared scope of local import alias hygiene."""
    return bool(
        canonical_alias_boundary_decision(
            file_path,
            import_path,
            language=language,
            primary_alias=primary_alias,
            module_root_name=module_root_name,
            alias_contract_applies=alias_contract_applies,
        ).get("violated")
    )


def get_audit_runtime_context(project_count: int | None = None) -> Dict[str, Any]:
    variations = DYNAMIC_CONFIG.get("variations", {})
    inferred_project_count = project_count if project_count is not None else len(variations or {})
    target_override = DYNAMIC_CONFIG.get("_target_root_override", {})
    if isinstance(target_override, dict) and target_override.get("enabled"):
        path_aliases = list(target_override.get("observed_path_aliases") or [])
    else:
        path_aliases = list((ENVIRONMENT.get("path_aliases") or {}).keys())
    return {
        "architecture_type": str(ARCH_CONFIG.get("type", "") or "").lower(),
        "module_root": str(ARCH_CONFIG.get("module_root", "") or ""),
        "plugins": sorted(str(plugin) for plugin in PLUGINS),
        "path_aliases": path_aliases,
        "project_count": int(inferred_project_count or 0),
        "rules_config": DYNAMIC_CONFIG.get("audit", {}).get("rules", {}),
    }


def _requirement_satisfied(requirement: Dict[str, Any], context: Dict[str, Any]) -> bool:
    kind = str(requirement.get("kind", ""))
    value = requirement.get("value")
    if kind not in set(RULE_PROFILE_CONTRACT.get("allowed_requirement_kinds")):
        raise ValueError(f"Unsupported audit rule requirement kind: {kind or '<empty>'}")
    if kind == "architecture_type":
        return str(context.get("architecture_type", "")).lower() == str(value or "").lower()
    if kind == "profile":
        detected_profile = ARCH_CONFIG.get("detected_profile", "MODULAR_FLAT")
        effective_profiles = effective_profile_ids(str(detected_profile))
        if isinstance(value, list):
            return bool(effective_profiles.intersection(set(value)))
        return str(value) in effective_profiles
    if kind == "plugin":
        return str(value or "") in set(context.get("plugins", []))
    if kind == "path_alias":
        aliases = context.get("path_aliases", []) or []
        return bool(aliases)
    if kind == "rule_enabled":
        rules_cfg = context.get("rules_config", {})
        return bool(rules_cfg.get(str(value), {}).get("enabled"))
    if kind == "path_pattern":
        module_root = str(context.get("module_root", "") or "")
        return bool(module_root) or bool(value)
    if kind == "multi_project":
        return int(context.get("project_count", 0) or 0) > 1
    raise ValueError(f"Audit rule requirement kind has no runtime implementation: {kind}")


def get_rule_profile(rule_key: str, context: Dict[str, Any]) -> Dict[str, Any]:
    from tools.core.config import DOCTRINE
    metadata = RULE_DEFINITIONS.get(rule_key, {})
    rules_cfg = context.get("rules_config", {})
    
    # Doctrine Overrides
    gov_policy = DOCTRINE.get("governance_policy", {})
    doctrine_blocking = set(gov_policy.get("blocking_rules", []))
    doctrine_healing = set(gov_policy.get("healing_rules", []))
    doctrine_advisory = set(gov_policy.get("advisory_rules", []))
    
    enabled = bool(rules_cfg.get(rule_key, {}).get("enabled", metadata.get("default_mode") != "disabled"))
    requires = list(metadata.get("requires", []))
    satisfied = all(_requirement_satisfied(req, context) for req in requires)

    if not enabled:
        mode = "disabled"
    elif not satisfied:
        mode = "advisory" if metadata.get("layer") in {"architectural", "doctrine"} else "disabled"
    elif rule_key in doctrine_blocking:
        mode = "enforced"
    elif rule_key in doctrine_healing:
        mode = "heal"
    elif rule_key in doctrine_advisory:
        mode = "advisory"
    elif satisfied:
        mode = "enforced" if metadata.get("default_mode") == "enforced" else "advisory"
    else:
        mode = "advisory" if metadata.get("layer") in {"architectural", "doctrine"} else "disabled"

    return {
        "rule": rule_key,
        "label": metadata.get("label", rule_key),
        "layer": metadata.get("layer", "unknown"),
        "mode": mode,
        "requires": requires,
        "rationale": metadata.get("rationale", ""),
    }


def build_rule_taxonomy(project_count: int | None = None) -> Dict[str, Any]:
    context = get_audit_runtime_context(project_count=project_count)
    profiles = {
        rule_key: get_rule_profile(rule_key, context)
        for rule_key in sorted(RULE_DEFINITIONS.keys())
    }
    by_layer: Dict[str, int] = {}
    by_mode: Dict[str, int] = {}
    for profile in profiles.values():
        by_layer[profile["layer"]] = by_layer.get(profile["layer"], 0) + 1
        by_mode[profile["mode"]] = by_mode.get(profile["mode"], 0) + 1

    return {
        "context": {
            "architecture_type": context.get("architecture_type"),
            "module_root": context.get("module_root"),
            "plugins": context.get("plugins"),
            "project_count": context.get("project_count"),
        },
        "summary": {
            "by_layer": dict(sorted(by_layer.items())),
            "by_mode": dict(sorted(by_mode.items())),
        },
        "profiles": profiles,
    }
