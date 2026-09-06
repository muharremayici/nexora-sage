from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

CODE_MAPS_DIR_FOR_IMPORT = Path(__file__).resolve().parent.parent
if str(CODE_MAPS_DIR_FOR_IMPORT) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR_FOR_IMPORT))

from tools.core.artifact_validator import ensure_against_schema
from tools.core.config import CODE_MAPS_DIR, CONFIG_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic


PROFILE_PATH = CONFIG_DIR / "test_impact_profiles.json"
SCHEMA_PATH = CONFIG_DIR / "schemas" / "test_impact_profiles.schema.json"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def _check(name: str, passed: bool, details: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def run_validation() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    try:
        ensure_against_schema(SCHEMA_PATH, "test_impact_profiles", profile)
        schema_errors: list[str] = []
    except Exception as exc:
        schema_errors = [str(exc)]
    checks.append(_check("test_impact_profile_schema_valid", not schema_errors, schema_errors or "schema ok"))

    languages = profile.get("languages", {}) if isinstance(profile.get("languages"), dict) else {}
    required = {"typescript", "python", "go", "java", "csharp"}
    missing = sorted(required - set(languages))
    checks.append(_check("polyglot_test_profiles_declared", not missing, missing or "required test profiles declared"))

    matcher_text = _read(CODE_MAPS_DIR / "tools" / "engines" / "test_impact_matcher.py")
    hardcoded_tokens = ['".test."', '"pytest {test_path}"', '"go test -v']
    checks.append(
        _check(
            "test_impact_matcher_uses_profile_config",
            "tools.core.test_impact_profiles" in matcher_text and not any(token in matcher_text for token in hardcoded_tokens),
            "Matcher should delegate naming, confidence and command policy to config/test_impact_profiles.json.",
        )
    )
    checks.append(
        _check(
            "test_impact_semantic_match_stays_target_project_scoped",
            "semantic_projects = [(project_key, atlas.get(project_key, {}))]" in matcher_text
            and "# matches require a real static graph edge rather than name similarity." in matcher_text,
            "Semantic naming matches must stay inside the target project; cross-project matches require a static graph edge.",
        )
    )
    checks.append(
        _check(
            "test_impact_runtime_log_is_english_text",
            "Running Test-Impact Matcher (value flow)" in matcher_text
            and "Değer Akışı" not in matcher_text,
            "Runtime logs should use stable English text for global agent/operator readability.",
        )
    )

    core_text = _read(CODE_MAPS_DIR / "tools" / "core" / "test_impact_profiles.py")
    checks.append(
        _check(
            "test_impact_profile_core_uses_language_registry",
            "language_for_extension" in core_text,
            "Test impact profile resolver should inherit language extension ownership from language_registry.",
        )
    )

    payload = {
        "meta": {"kind": "test_impact_profile_validation", "version": "v1"},
        "summary": {
            "total_checks": len(checks),
            "passed_checks": sum(1 for check in checks if check["passed"]),
            "failed_checks": sum(1 for check in checks if not check["passed"]),
        },
        "checks": checks,
    }
    save_json_atomic(RAW_DIR / "test_impact_profile_validation.json", payload)
    lines = [
        "# Test Impact Profile Validation",
        "",
        f"- Total checks: `{payload['summary']['total_checks']}`",
        f"- Passed: `{payload['summary']['passed_checks']}`",
        f"- Failed: `{payload['summary']['failed_checks']}`",
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
    save_text_atomic(REPORTS_DIR / "test_impact_profile_validation.md", "\n".join(lines) + "\n")
    return payload


def main() -> int:
    payload = run_validation()
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["failed_checks"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
