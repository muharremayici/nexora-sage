from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.config import (
    CODE_MAPS_DIR,
    CONFIG_DIR,
    DYNAMIC_CONFIG,
    RAW_DIR,
    REPORTS_DIR,
    ROOT as ANALYSIS_ROOT,
    save_json_atomic,
    save_text_atomic,
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def _check(name: str, passed: bool, details: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _listify(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _declared_project_root_details() -> dict[str, Any]:
    variations = DYNAMIC_CONFIG.get("variations", {}) or {}
    project_roles = DYNAMIC_CONFIG.get("project_roles", {}) or {}
    missing: list[dict[str, str]] = []
    escaping: list[dict[str, str]] = []
    inside_sage_workspace: list[dict[str, str]] = []
    resolved: dict[str, list[str]] = {}

    for name, raw_paths in variations.items():
        for raw_path in _listify(raw_paths):
            candidate = (ANALYSIS_ROOT / raw_path).resolve()
            resolved.setdefault(str(name), []).append(str(candidate))
            if not candidate.exists() or not candidate.is_dir():
                missing.append({"project": str(name), "path": str(candidate)})
            if not _is_relative_to(candidate, ANALYSIS_ROOT):
                escaping.append({"project": str(name), "path": str(candidate)})
            if _is_relative_to(candidate, CODE_MAPS_DIR):
                inside_sage_workspace.append({"project": str(name), "path": str(candidate)})

    return {
        "analysis_root": str(ANALYSIS_ROOT),
        "code_maps_dir": str(CODE_MAPS_DIR),
        "workspace_root": str(DYNAMIC_CONFIG.get("workspace_root", "")),
        "main_path": variations.get("MAIN"),
        "main_role": project_roles.get("MAIN"),
        "project_count": len(variations),
        "resolved": resolved,
        "missing": missing,
        "escaping": escaping,
        "inside_sage_workspace": inside_sage_workspace,
    }


def run_validation() -> dict[str, Any]:
    discovery_path = ROOT / "tools" / "orchestrators" / "discovery.py"
    oracle_path = ROOT / "tools" / "engines" / "architecture_oracle.py"
    language_registry_path = CONFIG_DIR / "language_registry.json"
    doctrine_path = CONFIG_DIR / "architecture_doctrine.json"
    contract_doc_path = ROOT / "docs" / "DISCOVERY_UNIVERSALITY_CONTRACT.md"

    discovery_text = _read(discovery_path)
    oracle_text = _read(oracle_path)
    doctrine = _read_json(doctrine_path)
    registry = _read_json(language_registry_path)
    contract_doc = _read(contract_doc_path)

    forbidden_discovery_fragments = [
        '"**/application/**/*.ts"',
        '"**/shared/hooks/**/*.tsx"',
        '"**/components/**/*.tsx"',
        '"**/api/index.ts"',
    ]
    hardcoded_hits = [fragment for fragment in forbidden_discovery_fragments if fragment in discovery_text]
    project_root_details = _declared_project_root_details()

    checks = [
        _check(
            "declared_project_roots_exist_and_stay_outside_sage_workspace",
            bool(project_root_details["project_count"])
            and project_root_details["main_path"] is not None
            and project_root_details["main_role"] == "host"
            and Path(project_root_details["analysis_root"]).exists()
            and not project_root_details["missing"]
            and not project_root_details["escaping"]
            and not project_root_details["inside_sage_workspace"],
            project_root_details,
        ),
        _check(
            "discovery_uses_registry_for_languages_and_markers",
            "load_language_registry()" in discovery_text
            and "language_extensions()" in discovery_text
            and "extension_language_map()" in discovery_text
            and "is_config_or_manifest_file" in discovery_text
            and "plugins_for_dependencies" in discovery_text
            and "plugins_for_bundler" in discovery_text,
            "Discovery must consume language_registry helpers.",
        ),
        _check(
            "discovery_keeps_deterministic_and_heuristic_evidence_separate",
            "_classify_evidence" in discovery_text
            and "signal_quality" in discovery_text
            and "discovery_determinism.md" in discovery_text,
            "Discovery must report deterministic vs heuristic signal ownership.",
        ),
        _check(
            "discovery_host_intelligence_is_doctrine_driven",
            "build_host_intelligence_seed()" in discovery_text
            and "discovery_host_intelligence_defaults" in discovery_text
            and not hardcoded_hits
            and isinstance(doctrine.get("discovery_host_intelligence_defaults"), dict),
            {"hardcoded_hits": hardcoded_hits, "doctrine_has_defaults": isinstance(doctrine.get("discovery_host_intelligence_defaults"), dict)},
        ),
        _check(
            "discovery_project_role_markers_are_doctrine_driven",
            "discovery_project_role_markers" in discovery_text
            and isinstance(doctrine.get("discovery_project_role_markers"), dict)
            and 'variant_containers = {"' not in discovery_text
            and 'companion_containers = {"' not in discovery_text,
            {"doctrine_has_role_markers": isinstance(doctrine.get("discovery_project_role_markers"), dict)},
        ),
        _check(
            "language_registry_covers_polyglot_driver_surface",
            isinstance(registry.get("languages"), dict)
            and {"typescript", "javascript", "python", "java", "go", "csharp"}.issubset(set(registry.get("languages", {}))),
            sorted((registry.get("languages") or {}).keys()),
        ),
        _check(
            "post_atlas_oracle_owns_blueprint_decisions",
            "input\": \"post_atlas\"" in oracle_text
            and "mode\": \"advisory_human_seal\"" in oracle_text
            and "seal_requires_human_approval" in oracle_text
            and "audit_blocking_before_seal" in oracle_text,
            "Architecture Oracle must propose after Atlas and require human seal.",
        ),
        _check(
            "contract_doc_states_discovery_boundaries",
            contract_doc_path.exists()
            and "Discovery is the scout layer" in contract_doc
            and "Folder names are allowed only as weak structural hints" in contract_doc
            and "Blueprint proposal" in contract_doc,
            str(contract_doc_path.relative_to(ROOT)),
        ),
    ]

    summary = {
        "total_checks": len(checks),
        "passed_checks": sum(1 for check in checks if check["passed"]),
        "failed_checks": sum(1 for check in checks if not check["passed"]),
    }
    payload = {
        "meta": {"kind": "discovery_universality_validation", "version": "v1"},
        "summary": summary,
        "checks": checks,
    }
    save_json_atomic(RAW_DIR / "discovery_universality_validation.json", payload)

    lines = [
        "# Discovery Universality Validation",
        "",
        f"- Total checks: `{summary['total_checks']}`",
        f"- Passed: `{summary['passed_checks']}`",
        f"- Failed: `{summary['failed_checks']}`",
        "",
        "| Check | Result | Details |",
        "|---|---|---|",
    ]
    for check in checks:
        details = check.get("details")
        if not isinstance(details, str):
            details = json.dumps(details, ensure_ascii=False)
        escaped_details = details.replace("|", "\\|")
        lines.append(f"| `{check['name']}` | {'PASS' if check['passed'] else 'FAIL'} | {escaped_details} |")
    save_text_atomic(REPORTS_DIR / "discovery_universality_validation.md", "\n".join(lines) + "\n")
    return payload


def main() -> int:
    payload = run_validation()
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["failed_checks"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
