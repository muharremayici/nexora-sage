import argparse
import json
import re
import sys
import os
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

# Ensure the Nexora SAGE root is in sys.path
_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_DIR, ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


from tools.core.config import (
    CODE_MAPS_DIR,
    CONFIG_DIR,
    DISCOVERY_FILE as DISCOVERY_PATH,
    OVERRIDES_FILE as OVERRIDES_PATH,
    CONFIG_FILE as CONFIG_PATH,
    DOCTRINE_FILE as DOCTRINE_PATH,
    save_text_atomic,
)
from tools.core.overrides_validator import overrides_match_discovery
from tools.core.quality_gate_policy import quality_gate_contract_defaults
from tools.core.runtime_config_identity import runtime_config_content_identity


def load_json(path: Path):
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def _deep_merge(base, override):
    if not isinstance(base, dict) or not isinstance(override, dict):
        return deepcopy(override)

    merged = deepcopy(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _filter_workspace_scoped_mapping(mapping: dict, discovery_variations: dict):
    if not isinstance(mapping, dict):
        return {}
    if not discovery_variations:
        return {}

    allowed_paths = {str(value).replace("\\", "/").strip() for value in discovery_variations.values() if isinstance(value, str)}
    filtered = {}
    for key, value in mapping.items():
        if not isinstance(value, str):
            continue
        normalized = value.replace("\\", "/").strip()
        if normalized in allowed_paths:
            filtered[key] = value
    return filtered


def _overrides_match_workspace(discovery_variations: dict, overrides: dict):
    if not discovery_variations or not isinstance(overrides, dict):
        return False

    discovery = load_json(DISCOVERY_PATH)
    return overrides_match_discovery(discovery, overrides)


def _safe_override_block(overrides: dict, key: str):
    value = overrides.get(key, {})
    return value if isinstance(value, dict) else {}


def _normalize_display_label(value: str) -> str:
    label = str(value or "").replace("\\", "/").strip().rstrip("/")
    if not label:
        return ""
    leaf = Path(label).name or label
    if leaf.lower() == "src":
        return "MAIN"
    leaf = leaf.replace("-", " ").replace("_", " ")
    leaf = re.sub(r"\s+", " ", leaf).strip()
    return leaf or str(value)


def _norm_rel_path(value: str) -> str:
    rel = str(value or "").replace("\\", "/").strip().strip("/")
    return rel or "."


def _is_ancestor_path(ancestor: str, descendant: str) -> bool:
    a = _norm_rel_path(ancestor)
    d = _norm_rel_path(descendant)
    if a == d:
        return False
    if a == ".":
        return d != "."
    return d.startswith(f"{a}/")


def _normalize_discovery_payload(discovery: dict):
    if not isinstance(discovery, dict):
        return {}

    normalized = deepcopy(discovery)
    meta = normalized.setdefault("_meta", {})
    if meta.get("kind") != "codemaps.discovery":
        meta["kind"] = "codemaps.discovery"
    meta.setdefault("generated_by", "tools/orchestrators/discovery.py")
    meta.setdefault("purpose", "Machine-generated architectural proposal layer")
    meta.setdefault("version", str(discovery.get("_meta", {}).get("version") or discovery.get("_meta", {}).get("purpose") or "draft"))

    if "quality_gates" in normalized and "quality_gate_seed" not in normalized:
        normalized["quality_gate_seed"] = deepcopy(normalized.get("quality_gates", {}))
    if "audit" in normalized and "audit_seed" not in normalized:
        normalized["audit_seed"] = deepcopy(normalized.get("audit", {}))
    return normalized


def _compile_variations(discovery: dict, overrides: dict, baseline: dict):
    compiled = {}

    discovery_variations = discovery.get("variations", {}) or {}
    baseline_variations = _filter_workspace_scoped_mapping(baseline.get("variations", {}) or {}, discovery_variations)
    override_variations = _filter_workspace_scoped_mapping(overrides.get("variations", {}) or {}, discovery_variations)
    alias_map = overrides.get("variation_aliases", {}) if _overrides_match_workspace(discovery_variations, overrides) else {}
    consumed_discovery_keys = set()

    compiled.update(discovery_variations)
    compiled.update(baseline_variations)
    compiled.update(override_variations)

    remapped = {}
    for alias, payload in alias_map.items():
        if not isinstance(payload, dict):
            continue
        if payload.get("enabled", True) is False:
            continue
        path = payload.get("path")
        discovery_key = payload.get("discovery_key")
        if not path and discovery_key:
            path = discovery_variations.get(discovery_key)
        if discovery_key:
            consumed_discovery_keys.add(discovery_key)
        if path:
            remapped[alias] = path

    compiled.update(remapped)
    for discovery_key in consumed_discovery_keys:
        if discovery_key in compiled and discovery_key not in baseline_variations and discovery_key not in override_variations:
            compiled.pop(discovery_key, None)
    return dict(sorted(compiled.items()))


def _compile_project_display_names(discovery: dict, overrides: dict, baseline: dict, compiled_variations: dict):
    discovery_variations = discovery.get("variations", {}) or {}
    baseline_display = baseline.get("project_display_names", {}) if isinstance(baseline.get("project_display_names", {}), dict) else {}
    override_display = overrides.get("project_display_names", {}) if isinstance(overrides.get("project_display_names", {}), dict) else {}
    alias_map = overrides.get("variation_aliases", {}) if _overrides_match_workspace(discovery_variations, overrides) else {}

    compiled: dict[str, str] = {}
    for project_key, rel_path in (compiled_variations or {}).items():
        display = ""
        alias_payload = alias_map.get(project_key)
        if isinstance(alias_payload, dict):
            display = str(alias_payload.get("display_name") or alias_payload.get("label") or "").strip()
        if not display:
            display = str(override_display.get(project_key) or "").strip()
        if not display:
            display = str(baseline_display.get(project_key) or "").strip()
        if not display:
            display = _normalize_display_label(rel_path)
        if not display:
            display = _normalize_display_label(project_key)
        compiled[str(project_key)] = display
    return dict(sorted(compiled.items()))


def compile_runtime_config():
    discovery_source = load_json(DISCOVERY_PATH)
    discovery = _normalize_discovery_payload(discovery_source)
    overrides = load_json(OVERRIDES_PATH)
    baseline = load_json(CONFIG_PATH)
    discovery_variations = discovery.get("variations", {}) or {}
    overrides_match_workspace = _overrides_match_workspace(discovery_variations, overrides)

    compiled = {}
    if isinstance(baseline, dict):
        preserved = {
            key: deepcopy(value)
            for key, value in baseline.items()
            if key in {"audit", "quality_gates", "host_intelligence"}
        }
        compiled.update(preserved)

    compiled.setdefault("_meta", {})
    compiled["_meta"]["kind"] = "codemaps.config"
    compiled["_meta"]["purpose"] = "Compiled runtime truth for Nexora SAGE"
    compiled["_meta"]["owner"] = "Nexora SAGE governance"
    compiled["_meta"]["source"] = "codemaps.discovery.json + codemaps.overrides.json + prior codemaps.config.json baseline"
    compiled["_meta"]["generated_by"] = "tools/config_compiler.py"
    compiled["_meta"]["contract_version"] = "config-provenance-v1"
    compiled["_meta"]["last_validated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    workspace_scoped_keys = ("workspace_root", "source_extensions", "project_roles", "skip_dirs")
    for key in workspace_scoped_keys:
        if key in discovery:
            compiled[key] = deepcopy(discovery[key])
        if key in overrides and overrides_match_workspace:
            compiled[key] = deepcopy(overrides[key])
    if isinstance(discovery.get("_repository_topology"), dict):
        compiled["_repository_topology"] = deepcopy(discovery["_repository_topology"])
    if "use_sqlite" in overrides:
        compiled["use_sqlite"] = bool(overrides.get("use_sqlite"))
    elif "use_sqlite" in baseline:
        compiled["use_sqlite"] = bool(baseline.get("use_sqlite"))
    elif "use_sqlite" in discovery:
        compiled["use_sqlite"] = bool(discovery.get("use_sqlite"))

    compiled["variations"] = _compile_variations(discovery, overrides, baseline)
    compiled["project_display_names"] = _compile_project_display_names(discovery, overrides, baseline, compiled["variations"])
    variation_keys = set(compiled.get("variations", {}).keys())
    if isinstance(compiled.get("project_roles"), dict):
        compiled["project_roles"] = {
            str(key): value
            for key, value in compiled["project_roles"].items()
            if str(key) in variation_keys
        }

    discovery_arch = discovery.get("architecture", {}) or {}
    baseline_arch = baseline.get("architecture", {}) if isinstance(baseline.get("architecture", {}), dict) else {}
    if compiled["variations"].get("MAIN") == baseline.get("variations", {}).get("MAIN"):
        architecture_seed = _deep_merge(discovery_arch, baseline_arch)
    else:
        architecture_seed = deepcopy(discovery_arch)
    override_arch = _safe_override_block(overrides, "architecture") if overrides_match_workspace else {}
    compiled["architecture"] = _deep_merge(architecture_seed, override_arch)

    discovery_env = discovery.get("environment", {}) or {}
    baseline_env = baseline.get("environment", {}) if isinstance(baseline.get("environment", {}), dict) else {}
    if compiled["variations"].get("MAIN") == baseline.get("variations", {}).get("MAIN"):
        environment_seed = _deep_merge(discovery_env, baseline_env)
    else:
        environment_seed = deepcopy(discovery_env)
    override_env = _safe_override_block(overrides, "environment") if overrides_match_workspace else {}
    compiled["environment"] = _deep_merge(environment_seed, override_env)

    discovery_plugins = set(str(item) for item in (discovery.get("plugins", []) or []) if str(item).strip())
    plugin_overrides = overrides.get("plugin_overrides", {}) if overrides_match_workspace and isinstance(overrides.get("plugin_overrides", {}), dict) else {}
    plugin_additions = {str(item) for item in (plugin_overrides.get("add", []) or []) if str(item).strip()}
    plugin_removals = {str(item) for item in (plugin_overrides.get("remove", []) or []) if str(item).strip()}
    compiled["plugins"] = sorted((discovery_plugins | plugin_additions) - plugin_removals)

    compiled["host_intelligence"] = _deep_merge(
        _deep_merge(discovery.get("host_intelligence", {}) or {}, compiled.get("host_intelligence", {}) or {}),
        _safe_override_block(overrides, "host_intelligence"),
    )
    compiled["audit"] = _deep_merge(
        _deep_merge(discovery.get("audit_seed", {}) or {}, compiled.get("audit", {}) or {}),
        _safe_override_block(overrides, "audit"),
    )
    if compiled.get("source_extensions"):
        compiled["audit"]["file_extensions"] = sorted(compiled["source_extensions"])
    compiled["quality_gates"] = _deep_merge(
        _deep_merge(discovery.get("quality_gate_seed", {}) or {}, compiled.get("quality_gates", {}) or {}),
        _safe_override_block(overrides, "quality_gates"),
    )
    compiled["quality_gates"] = _deep_merge(quality_gate_contract_defaults(), compiled["quality_gates"])
    project_count_for_gate_defaults = max(len(compiled.get("variations", {}) or {}), 1)
    discovered_gates = discovery.get("quality_gate_seed", {}) if isinstance(discovery.get("quality_gate_seed", {}), dict) else {}
    override_gates = _safe_override_block(overrides, "quality_gates")
    if "max_high_risk_candidates" not in discovered_gates and "max_high_risk_candidates" not in override_gates:
        compiled["quality_gates"]["max_high_risk_candidates"] = 5 + (project_count_for_gate_defaults * 3)
    else:
        compiled["quality_gates"].setdefault("max_high_risk_candidates", 5 + (project_count_for_gate_defaults * 3))
    compiled["quality_gates"].pop("max_manual_review", None)

    # --- [Compiler Collision Shield] --- (Sovereign 19.8)
    if "MAIN" in compiled.get("variations", {}):
        main_path = str(compiled["variations"]["MAIN"]).replace("\\", "/").strip("/")
        redundant_keys = []
        for v_key, v_path in compiled["variations"].items():
            if v_key == "MAIN":
                continue
            if str(v_path).replace("\\", "/").strip("/") == main_path:
                redundant_keys.append(v_key)
        
        for r_key in redundant_keys:
            print(f"     [COMPILER] Stripping redundant variation collision: {r_key} -> {main_path}")
            compiled["variations"].pop(r_key, None)
            if "project_roles" in compiled:
                compiled["project_roles"].pop(r_key, None)
            if "project_display_names" in compiled:
                compiled["project_display_names"].pop(r_key, None)

    # --- [Compiler Nested Overlap Shield] ---
    # If MAIN points to workspace root and specific nested projects exist, MAIN can shadow
    # those projects and inflate metrics by duplicate scanning. In that case, drop MAIN.
    variations = compiled.get("variations", {}) or {}
    if "MAIN" in variations and len(variations) > 1:
        main_path = _norm_rel_path(variations.get("MAIN"))
        descendants = [
            key
            for key, value in variations.items()
            if key != "MAIN" and _is_ancestor_path(main_path, str(value))
        ]
        if descendants and main_path == ".":
            print(
                f"     [COMPILER] MAIN root '{main_path}' overlaps nested projects "
                f"{descendants}. Dropping MAIN to prevent double-scan inflation."
            )
            compiled["variations"].pop("MAIN", None)
            if isinstance(compiled.get("project_roles"), dict):
                compiled["project_roles"].pop("MAIN", None)
            if isinstance(compiled.get("project_display_names"), dict):
                compiled["project_display_names"].pop("MAIN", None)

            # Ensure at least one host remains for downstream host-aware engines.
            roles = compiled.get("project_roles", {}) or {}
            remaining = list((compiled.get("variations", {}) or {}).keys())
            if remaining and all(roles.get(k) != "host" for k in remaining):
                roles[remaining[0]] = "host"
                compiled["project_roles"] = roles

    compiled["_compiled_from"] = {
        "discovery": DISCOVERY_PATH.name if DISCOVERY_PATH.exists() else None,
        "overrides": OVERRIDES_PATH.name if OVERRIDES_PATH.exists() else None,
        "baseline": CONFIG_PATH.name if CONFIG_PATH.exists() else None,
        "governance_sync": "tools/governance_sync.py",
        "compiler": "tools/config_compiler.py",
    }
    compiled["_provenance"] = {
        "owner": compiled["_meta"]["owner"],
        "source": compiled["_meta"]["source"],
        "generated_by": compiled["_meta"]["generated_by"],
        "contract_version": compiled["_meta"]["contract_version"],
        "last_validated": compiled["_meta"]["last_validated"],
        "declared_truth": DISCOVERY_PATH.name,
        "governed_truth": OVERRIDES_PATH.name,
        "runtime_truth": CONFIG_PATH.name,
        "workspace_scoped_overrides": overrides_match_workspace,
    }
    compiled["_provenance"].update(
        runtime_config_content_identity(discovery_source, overrides, compiled)
    )
    return compiled


def main():
    parser = argparse.ArgumentParser(description="Compile Nexora SAGE runtime config from discovery + overrides.")
    parser.add_argument("--apply", action="store_true", help="Write the compiled output to codemaps.config.json")
    parser.add_argument("--output", help="Write compiled config to a custom path instead of the runtime config")
    args = parser.parse_args()

    compiled = compile_runtime_config()
    rendered = json.dumps(compiled, indent=4, ensure_ascii=False)

    if args.output:
        out_path = Path(args.output)
        save_text_atomic(out_path, rendered)
        print(f"[OK] Compiled config written to {out_path}")
        return

    if args.apply:
        save_text_atomic(CONFIG_PATH, rendered)
        print(f"[OK] Runtime config updated: {CONFIG_PATH}")
        return

    print(rendered)


if __name__ == "__main__":
    main()
