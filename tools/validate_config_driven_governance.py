from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

CODE_MAPS_DIR_FOR_IMPORT = Path(__file__).resolve().parent.parent
if str(CODE_MAPS_DIR_FOR_IMPORT) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR_FOR_IMPORT))

from tools.core.config import CODE_MAPS_DIR, CONFIG_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.artifact_validator import ensure_against_schema


SCHEMA_PATH = CONFIG_DIR / "schemas" / "language_registry.schema.json"
REGISTRY_PATH = CONFIG_DIR / "language_registry.json"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def _check(name: str, passed: bool, details: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _contains_literal(text: str, literal: str) -> bool:
    return literal in text


def run_validation() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))

    try:
        ensure_against_schema(SCHEMA_PATH, "language_registry", registry)
        schema_errors: list[str] = []
    except Exception as exc:
        schema_errors = [str(exc)]
    checks.append(_check("language_registry_schema_valid", not schema_errors, schema_errors or "schema ok"))

    mcp_text = _read(CODE_MAPS_DIR / "tools" / "engines" / "mcp_governance_engine.py")
    audit_text = _read(CODE_MAPS_DIR / "tools" / "engines" / "audit.py")
    pipeline_policy_text = _read(CODE_MAPS_DIR / "tools" / "core" / "pipeline_policy.py")
    audit_policy_text = _read(CODE_MAPS_DIR / "config" / "audit_policy.json")
    checks.append(
        _check(
            "mcp_module_root_uses_runtime_config",
            "get_module_root_name()" in mcp_text and '"lifecycle-modules"' not in re.sub(r"#.*", "", mcp_text),
            "MCP governance must not own a lifecycle-modules fallback.",
        )
    )

    path_engine_text = _read(CODE_MAPS_DIR / "tools" / "core" / "path_engine.py")
    checks.append(
        _check(
            "path_engine_uses_language_registry",
            "language_extensions()" in path_engine_text and "index_files()" in path_engine_text,
            "Path candidate expansion should be registry-driven.",
        )
    )

    atlas_text = _read(CODE_MAPS_DIR / "tools" / "engines" / "generate_atlas.py")
    nuclear_text = _read(CODE_MAPS_DIR / "tools" / "engines" / "nuclear_processor.py")
    checks.append(
        _check(
            "atlas_extensions_use_registry_or_runtime_config",
            "structure_extensions()" in atlas_text
            and "AST_FILE_EXTENSIONS = tuple(sorted(SOURCE_EXTENSIONS))" in atlas_text
            and "language_for_extension" in atlas_text,
            "Atlas extension and language detection should not duplicate per-language chains.",
        )
    )
    checks.append(
        _check(
            "atlas_language_detection_uses_registry",
            "extension_language_map()" in atlas_text
            and "prune_owned_walk_dirs(" in atlas_text
            and "skipped_names=SKIP_DIRS" in atlas_text
            and 'lowered.endswith(".py")' not in atlas_text
            and 'lowered.endswith(".java")' not in atlas_text,
            "Atlas project language detection should follow language_registry and skip ignored directories.",
        )
    )

    source_files_text = _read(CODE_MAPS_DIR / "tools" / "core" / "source_files.py")
    language_registry_text = _read(CODE_MAPS_DIR / "tools" / "core" / "language_registry.py")
    external_preflight_text = _read(CODE_MAPS_DIR / "tools" / "external_target_preflight.py")
    checks.append(
        _check(
            "source_file_classifier_uses_registry_exclusions",
            "non_source_template_extensions()" in source_files_text
            and "non_source_compound_suffixes()" in source_files_text
            and "NON_SOURCE_TEMPLATE_EXTENSIONS" not in source_files_text
            and "non_source_template_extensions" in language_registry_text
            and "non_source_template_extensions" in registry,
            "Source inclusion/exclusion should be controlled by language_registry, not local hardcoded extension lists.",
        )
    )
    checks.append(
        _check(
            "atlas_and_genome_share_source_classifier",
            "is_analysis_source_file" in atlas_text
            and "is_analysis_source_file" in nuclear_text
            and "file.endswith(ext) for ext in SOURCE_EXTENSIONS" not in nuclear_text,
            "Atlas and Nuclear/Genome should walk the same registry-governed source universe.",
        )
    )

    discovery_text = _read(CODE_MAPS_DIR / "tools" / "orchestrators" / "discovery.py")
    repository_topology_text = _read(CODE_MAPS_DIR / "tools" / "core" / "repository_topology.py")
    checks.append(
        _check(
            "discovery_uses_language_registry",
            "load_language_registry()" in discovery_text
            and "language_extensions()" in discovery_text
            and "extension_language_map()" in discovery_text
            and "is_config_or_manifest_file" in discovery_text
            and "plugins_for_dependencies" in discovery_text
            and "plugins_for_bundler" in discovery_text,
            "Discovery source/language/plugin registry should be config-driven.",
        )
    )
    checks.append(
        _check(
            "discovery_config_markers_use_registry",
            "config_or_manifest_predicate=is_config_or_manifest_file" in discovery_text
            and "has_config = any(is_config_or_manifest(name) for name in item_names)"
            in repository_topology_text
            and '"vite.config.ts"' not in discovery_text
            and '"next.config.js"' not in discovery_text,
            "Discovery workspace config markers should come from language_registry.config_file_markers.",
        )
    )

    watchdog_text = _read(CODE_MAPS_DIR / "tools" / "orchestrators" / "watchdog.py")
    checks.append(
        _check(
            "watchdog_uses_language_registry",
            "watch_extensions()" in watchdog_text,
            "Watchdog extension list should follow language registry.",
        )
    )

    mcp_text = _read(CODE_MAPS_DIR / "tools" / "engines" / "mcp_governance_engine.py")
    checks.append(
        _check(
            "mcp_language_detection_uses_registry",
            "language_for_extension(file_ext)" in mcp_text
            and 'file_ext == ".py"' not in mcp_text,
            "MCP patch validation language detection should follow language_registry.",
        )
    )
    checks.append(
        _check(
            "audit_and_mcp_api_entry_policy_is_centralized",
            "def get_api_entry_filenames" in pipeline_policy_text
            and "get_api_entry_filenames" in audit_text
            and "get_api_entry_filenames" in mcp_text
            and '["index.ts", "index.tsx", "__init__.py", "mod.go"]' not in audit_text
            and '["index.ts", "index.tsx", "__init__.py", "mod.go"]' not in mcp_text,
            "Audit and MCP governance should read public API entry filenames from shared policy, not local engine fallbacks.",
        )
    )
    checks.append(
        _check(
            "config_and_manifest_globs_use_one_matcher",
            "fnmatchcase" in language_registry_text
            and "is_config_or_manifest_file(name)" in _read(CODE_MAPS_DIR / "tools" / "engines" / "quant_engine.py")
            and "config_or_manifest_predicate=is_config_or_manifest_file" in discovery_text
            and "is_config_or_manifest(name)" in repository_topology_text,
            "Exact names and glob patterns from the central taxonomy must have identical semantics in Discovery and Quant.",
        )
    )
    checks.append(
        _check(
            "external_preflight_uses_central_polyglot_manifest_taxonomy",
            "manifest_file_markers" in registry
            and "manifest_file_marker_map()" in external_preflight_text
            and "language_registry_provenance()" in external_preflight_text
            and 'policy.get("react_config_files"' not in external_preflight_text
            and 'config_files = {"package.json"' not in external_preflight_text,
            "External preflight must derive config and manifest discovery from the central language registry and fail closed when its provenance is unavailable.",
        )
    )
    checks.append(
        _check(
            "audit_loc_limits_preserve_full_policy_contract",
            'get_audit_policy().get("loc_limits", {})' in pipeline_policy_text
            and '"function": 150' in audit_policy_text
            and '"class": 500' in audit_policy_text
            and '"default": 400' in audit_policy_text,
            "Audit LOC limits should preserve function/class/default thresholds through config/audit_policy.json.",
        )
    )
    checks.append(
        _check(
            "loc_finding_semantics_are_centralized",
            '"loc_finding_contract"' in audit_policy_text
            and '"generic_rule": "loc_limits_symbol"' in audit_policy_text
            and "def resolve_loc_finding_semantics" in pipeline_policy_text
            and "resolve_loc_finding_semantics" in audit_text
            and "resolve_loc_finding_semantics" in mcp_text,
            "Audit and MCP LOC findings should resolve rule, symbol kind, threshold kind and limit through one audit-policy contract.",
        )
    )
    checks.append(
        _check(
            "generic_loc_findings_never_fall_back_to_component",
            "rule_key = 'loc_limits_component'" not in audit_text
            and 'rule_key = "loc_limits_component"' not in mcp_text,
            "Generic LOC findings must use loc_limits_symbol and preserve their actual symbol kind instead of masquerading as components.",
        )
    )

    merge_text = _read(CODE_MAPS_DIR / "tools" / "engines" / "merge_simulation_engine.py")
    ui_runtime_text = _read(CODE_MAPS_DIR / "tools" / "engines" / "ui_runtime_contract_analyzer.py")
    checks.append(
        _check(
            "legacy_path_shims_are_configured",
            "apply_legacy_path_shims" in merge_text
            and "apply_legacy_path_shims" in ui_runtime_text
            and any(not item.get("enabled", True) for item in registry.get("legacy_path_shims", [])),
            "Legacy path rewrites must be registry-controlled and disabled by default.",
        )
    )

    compiler_text = _read(CODE_MAPS_DIR / "tools" / "config_compiler.py")
    checks.append(
        _check(
            "compiled_audit_extensions_follow_source_extensions",
            'compiled["audit"]["file_extensions"] = sorted(compiled["source_extensions"])' in compiler_text,
            "Compiled audit scope should not preserve stale TS-only file_extensions.",
        )
    )

    payload = {
        "meta": {"kind": "config_driven_governance_validation", "version": "v1"},
        "summary": {
            "total_checks": len(checks),
            "passed_checks": sum(1 for check in checks if check["passed"]),
            "failed_checks": sum(1 for check in checks if not check["passed"]),
        },
        "checks": checks,
    }
    save_json_atomic(RAW_DIR / "config_driven_governance_validation.json", payload)
    lines = [
        "# Config-Driven Governance Validation",
        "",
        f"- Total checks: `{payload['summary']['total_checks']}`",
        f"- Passed: `{payload['summary']['passed_checks']}`",
        f"- Failed: `{payload['summary']['failed_checks']}`",
        "",
        "| Check | Result | Details |",
        "|---|---|---|",
    ]
    for check in checks:
        details = str(check.get("details", "")).replace("|", "\\|")
        lines.append(f"| `{check['name']}` | {'PASS' if check['passed'] else 'FAIL'} | {details} |")
    save_text_atomic(REPORTS_DIR / "config_driven_governance_validation.md", "\n".join(lines) + "\n")
    return payload


def main() -> int:
    payload = run_validation()
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["failed_checks"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
