from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.capability_registry import load_capability_registry
from tools.core.config import CONFIG_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_object_strict
from tools.engines.project_dna_profiler import build_project_dna_profile


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check(name: str, passed: bool, details: Any = None) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _project_ids(payload: dict[str, Any]) -> list[str]:
    return [
        str(project.get("project"))
        for project in payload.get("projects", [])
        if isinstance(project, dict) and project.get("project")
    ]


def validate_project_dna_profile() -> dict[str, Any]:
    policy = load_json_object_strict(CONFIG_DIR / "project_dna_profile_policy.json", label="Project DNA profile policy")
    capability_registry = load_capability_registry()
    payload = build_project_dna_profile()
    capability_ids = {
        str(item.get("id"))
        for item in capability_registry.get("capabilities", [])
        if isinstance(item, dict) and item.get("id")
    }
    activation_capabilities = {
        str(item.get("capability"))
        for project in payload.get("projects", [])
        if isinstance(project, dict)
        for item in project.get("activation_intents", [])
        if isinstance(item, dict)
    }
    project_rows = [project for project in payload.get("projects", []) if isinstance(project, dict)]
    project_ids = _project_ids(payload)
    source = (ROOT / "tools" / "engines" / "project_dna_profiler.py").read_text(encoding="utf-8")
    forbidden_repo_tokens = ["privatehost", "legacyproduct", "example-product", "example-suite"]

    checks = [
        _check(
            "policy_exists_and_is_machine_readable",
            policy.get("meta", {}).get("kind") == "nexora.project_dna_profile_policy"
            and isinstance(policy.get("language_extensions"), dict)
            and isinstance(policy.get("framework_package_signals"), dict),
            {"policy": "config/project_dna_profile_policy.json"},
        ),
        _check(
            "profile_has_stable_contract",
            payload.get("meta", {}).get("kind") == "project_dna_profile"
            and payload.get("summary", {}).get("status") == "PASS"
            and bool(project_rows)
            and "MAIN" in project_ids,
            {"project_count": len(project_rows), "projects": project_ids},
        ),
        _check(
            "profile_separates_description_from_scheduling",
            payload.get("summary", {}).get("capability_activation_ready") is True
            and payload.get("summary", {}).get("does_not_schedule_pipeline") is True
            and payload.get("meta", {}).get("scope") == "descriptive_profile_only",
            payload.get("summary", {}),
        ),
        _check(
            "all_projects_have_shape_and_roots",
            bool(project_rows)
            and all(
                isinstance(project.get("repo_shape"), dict) and project.get("roots")
                for project in project_rows
            ),
            {"projects": project_ids},
        ),
        _check(
            "activation_intents_reference_known_capabilities",
            activation_capabilities <= capability_ids,
            {"unknown_capabilities": sorted(activation_capabilities - capability_ids)},
        ),
        _check(
            "engine_is_policy_backed_not_repo_specific",
            "project_dna_profile_policy.json" in source.lower()
            and not any(token in source.lower() for token in forbidden_repo_tokens),
            {"forbidden_repo_tokens": forbidden_repo_tokens},
        ),
        _check(
            "package_manifest_parse_failures_are_telemetry_recorded",
            "record_honesty_event" in source
            and "read_package_json" in source
            and "project_dna_dependency_and_framework_detection_degraded" in source,
            {"telemetry_contract": "package.json parse fallback must be visible"},
        ),
    ]

    status = "PASS" if all(check["passed"] for check in checks) else "FAIL"
    result = {
        "meta": {
            "kind": "project_dna_profile_validation",
            "version": "v1",
            "generated_at": _utc_now(),
            "validator": "tools.validate_project_dna_profile",
        },
        "summary": {
            "status": status,
            "checks": len(checks),
            "passed": sum(1 for check in checks if check["passed"]),
        },
        "checks": checks,
    }
    save_json_atomic(RAW_DIR / "project_dna_profile.json", payload)
    save_json_atomic(RAW_DIR / "project_dna_profile_validation.json", result)
    save_text_atomic(REPORTS_DIR / "project_dna_profile.md", _render_profile(payload))
    save_text_atomic(REPORTS_DIR / "project_dna_profile_validation.md", _render_validation(result))
    return result


def _render_profile(payload: dict[str, Any]) -> str:
    lines = [
        "# Project DNA Profile",
        "",
        "Policy-backed repository DNA summary for future capability activation planning.",
        "",
        "| Project | Shape | Languages | Frameworks | Activation Candidates |",
        "|---|---|---|---|---|",
    ]
    for project in payload.get("projects", []):
        if not isinstance(project, dict):
            continue
        shape = (project.get("repo_shape") or {}).get("id") if isinstance(project.get("repo_shape"), dict) else ""
        languages = ", ".join(str(item.get("id")) for item in project.get("languages", []) if isinstance(item, dict))
        frameworks = ", ".join(str(item.get("id")) for item in project.get("frameworks", []) if isinstance(item, dict))
        candidates = ", ".join(str(item.get("capability")) for item in project.get("activation_intents", []) if isinstance(item, dict))
        lines.append(f"| `{project.get('project')}` | `{shape}` | `{languages}` | `{frameworks}` | `{candidates}` |")
    lines.append("")
    return "\n".join(lines)


def _render_validation(result: dict[str, Any]) -> str:
    lines = [
        "# Project DNA Profile Validation",
        "",
        f"- status: `{result.get('summary', {}).get('status')}`",
        f"- passed: `{result.get('summary', {}).get('passed')}/{result.get('summary', {}).get('checks')}`",
        "",
        "| Check | Passed |",
        "|---|---|",
    ]
    for check in result.get("checks", []):
        lines.append(f"| `{check.get('name')}` | `{check.get('passed')}` |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    result = validate_project_dna_profile()
    print(json.dumps(result["summary"], ensure_ascii=False))
    return 0 if result["summary"]["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
