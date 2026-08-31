from __future__ import annotations

import argparse
import json
import sys
import re
import time
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
CODE_MAPS_DIR = TOOLS_DIR.parent
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))

from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.installation_identity import is_sage_installation_root
from tools.core.json_io import load_json_file
from tools.core.operational_limits import (
    distribution_cli_help_timeout_seconds,
    distribution_git_visibility_retry_attempts,
    distribution_git_visibility_retry_delay_ms,
    quant_git_status_timeout_seconds,
)
from tools.core.subprocess_telemetry import run_observed_subprocess

README_PATH = CODE_MAPS_DIR / "README.md"
CHECKLIST_PATH = CODE_MAPS_DIR / "docs" / "UNIVERSAL_RELEASE_CHECKLIST.md"
WALKTHROUGH_PATH = CODE_MAPS_DIR / "docs" / "WHOLE_PRODUCT_WALKTHROUGH.md"
GITIGNORE_PATH = CODE_MAPS_DIR / ".gitignore"
CLI_COMMAND_CONTRACT_PATH = CODE_MAPS_DIR / "config" / "cli_command_contract.json"


def _check(name: str, passed: bool, details: str) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _load_text_file(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _git_check_ignore(path: Path) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    max_attempts = distribution_git_visibility_retry_attempts()
    delay_seconds = distribution_git_visibility_retry_delay_ms() / 1000
    for attempt in range(1, max_attempts + 1):
        try:
            result, duration = run_observed_subprocess(
                ["git", "check-ignore", "-q", "--", str(path)],
                cwd=str(CODE_MAPS_DIR.parent),
                label="distribution git check-ignore",
                timeout=quant_git_status_timeout_seconds(),
                log=None,
            )
            row = {
                "attempt": attempt,
                "returncode": result.returncode,
                "stderr": (result.stderr or "").strip(),
                "duration_seconds": round(duration, 3),
            }
            attempts.append(row)
            if result.returncode == 0:
                return {"state": "ignored", "attempts": attempts}
            if result.returncode == 1:
                return {"state": "visible", "attempts": attempts}
        except Exception as exc:
            attempts.append({"attempt": attempt, "returncode": None, "stderr": str(exc), "duration_seconds": None})
        if attempt < max_attempts:
            time.sleep(delay_seconds)
    return {"state": "unknown", "attempts": attempts}


def _sage_development_folders() -> list[Path]:
    candidates: list[Path] = []
    try:
        for path in CODE_MAPS_DIR.parent.iterdir():
            if path.is_dir() and is_sage_installation_root(path):
                candidates.append(path)
    except Exception:
        return [CODE_MAPS_DIR]
    if CODE_MAPS_DIR not in candidates:
        candidates.append(CODE_MAPS_DIR)
    return sorted(candidates, key=lambda item: item.name.lower())


def _generated_sage_archives() -> list[Path]:
    candidates: list[Path] = []
    try:
        for path in CODE_MAPS_DIR.parent.iterdir():
            name = path.name.lower()
            if path.is_file() and name.startswith(CODE_MAPS_DIR.name.lower()) and name.endswith((".rar", ".zip", ".7z")):
                candidates.append(path)
    except Exception:
        return []
    return sorted(candidates, key=lambda item: item.name.lower())


VALIDATION_SCOPES = {"source-checkout", "installed-package"}


def _extract_help_commands(help_text: str) -> set[str]:
    match = re.search(r"usage:.*?\{([^}]+)\}", help_text, flags=re.DOTALL)
    if not match:
        return set()
    return {item.strip() for item in match.group(1).split(",") if item.strip()}


def _authority_surface_checks(
    scope: str,
    help_text: str,
    *,
    walkthrough_present: bool,
    walkthrough_text: str,
    checklist_present: bool,
    checklist_text: str,
) -> list[dict[str, Any]]:
    if scope not in VALIDATION_SCOPES:
        raise ValueError(f"Unsupported distribution validation scope: {scope}")
    contract = load_json_file(CLI_COMMAND_CONTRACT_PATH, {})
    validation = contract.get("validation", {}) if isinstance(contract, dict) else {}
    validation = validation if isinstance(validation, dict) else {}
    visibility = validation.get("top_level_command_visibility", {})
    visibility = visibility if isinstance(visibility, dict) else {}
    classes = visibility.get("classes", {})
    classes = classes if isinstance(classes, dict) else {}
    surface_id = "public" if scope == "installed-package" else "development"
    help_surfaces = validation.get("help_surfaces", {})
    help_surfaces = help_surfaces if isinstance(help_surfaces, dict) else {}
    surface = help_surfaces.get(surface_id, {})
    surface = surface if isinstance(surface, dict) else {}

    expected_commands: set[str] = set()
    for row in classes.values():
        if not isinstance(row, dict):
            continue
        if scope == "installed-package" and not bool(row.get("public_distribution")):
            continue
        commands = row.get("commands", [])
        if isinstance(commands, list):
            expected_commands.update(str(item) for item in commands if str(item))
    actual_commands = _extract_help_commands(help_text)
    normalized_help = " ".join(help_text.split())
    required_phrases = {
        " ".join(str(item).split())
        for item in surface.get("required_phrases", [])
        if str(item)
    }
    forbidden_phrases = {
        " ".join(str(item).split())
        for item in surface.get("forbidden_phrases", [])
        if str(item)
    }
    missing_phrases = sorted(phrase for phrase in required_phrases if phrase not in normalized_help)
    exposed_phrases = sorted(phrase for phrase in forbidden_phrases if phrase in normalized_help)
    checks = [
        _check(
            "cli_command_surface_matches_authority_profile",
            bool(expected_commands)
            and actual_commands == expected_commands
            and not missing_phrases
            and not exposed_phrases,
            json.dumps(
                {
                    "surface": surface_id,
                    "missing_commands": sorted(expected_commands - actual_commands),
                    "unexpected_commands": sorted(actual_commands - expected_commands),
                    "missing_help_phrases": missing_phrases,
                    "forbidden_help_phrases_exposed": exposed_phrases,
                },
                ensure_ascii=False,
            ),
        )
    ]
    if scope == "installed-package":
        checks.extend(
            [
                _check(
                    "private_release_walkthrough_omitted",
                    not walkthrough_present,
                    "installed public package omits the private whole-product maintainer walkthrough",
                ),
                _check(
                    "private_release_checklist_omitted",
                    not checklist_present,
                    "installed public package omits the private universal release checklist",
                ),
            ]
        )
    else:
        checks.extend(
            [
                _check(
                    "walkthrough_has_release_check",
                    walkthrough_present and "python sage.py release-check" in walkthrough_text,
                    "source checkout walkthrough uses the private sage.py release-check command",
                ),
                _check(
                    "checklist_has_phase6_and_evidence",
                    checklist_present
                    and "| 6 | Distribution hardening |" in checklist_text
                    and "performance_ledger" in checklist_text
                    and "release-check" in checklist_text,
                    "source checkout keeps phase6 and private distribution evidence contract",
                ),
            ]
        )
    return checks


def _source_checkout_visibility_checks() -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    development_folders = _sage_development_folders()
    development_visibility = {path: _git_check_ignore(path) for path in development_folders}
    ignored_development = [str(path.relative_to(CODE_MAPS_DIR.parent)) for path, probe in development_visibility.items() if probe["state"] == "ignored"]
    unknown_development = [str(path.relative_to(CODE_MAPS_DIR.parent)) for path, probe in development_visibility.items() if probe["state"] == "unknown"]
    checks.append(
        _check(
            "sage_source_and_clean_mirror_folders_are_git_visible",
            bool(development_folders) and not ignored_development and not unknown_development,
            json.dumps(
                {
                    "development_folders": [
                        str(path.relative_to(CODE_MAPS_DIR.parent)) for path in development_folders
                    ],
                    "ignored": ignored_development,
                    "unknown": unknown_development,
                    "visibility_probes": {str(path.relative_to(CODE_MAPS_DIR.parent)): probe for path, probe in development_visibility.items()},
                },
                ensure_ascii=False,
            ),
        )
    )
    generated_archives = _generated_sage_archives()
    archive_visibility = {path: _git_check_ignore(path) for path in generated_archives}
    unignored_archives = [str(path.relative_to(CODE_MAPS_DIR.parent)) for path, probe in archive_visibility.items() if probe["state"] == "visible"]
    unknown_archives = [str(path.relative_to(CODE_MAPS_DIR.parent)) for path, probe in archive_visibility.items() if probe["state"] == "unknown"]
    checks.append(
        _check(
            "generated_sage_archives_are_git_ignored",
            not unignored_archives and not unknown_archives,
            json.dumps(
                {
                    "generated_archives": [
                        str(path.relative_to(CODE_MAPS_DIR.parent)) for path in generated_archives
                    ],
                    "unignored": unignored_archives,
                    "unknown": unknown_archives,
                    "visibility_probes": {str(path.relative_to(CODE_MAPS_DIR.parent)): probe for path, probe in archive_visibility.items()},
                },
                ensure_ascii=False,
            ),
        )
    )
    return checks


def _scope_specific_checks(scope: str) -> list[dict[str, Any]]:
    if scope not in VALIDATION_SCOPES:
        raise ValueError(f"Unsupported distribution validation scope: {scope}")
    if scope == "installed-package":
        return []
    return _source_checkout_visibility_checks()


def _artifact_stem(scope: str) -> str:
    if scope not in VALIDATION_SCOPES:
        raise ValueError(f"Unsupported distribution validation scope: {scope}")
    return (
        "distribution_hardening_validation"
        if scope == "source-checkout"
        else "installed_distribution_hardening_validation"
    )


def run_validation(*, scope: str = "source-checkout") -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    try:
        sage_help_run, _sage_seconds = run_observed_subprocess(
            ["python", str(CODE_MAPS_DIR / "sage.py"), "--help"],
            cwd=CODE_MAPS_DIR,
            label="sage_cli_help",
            timeout=distribution_cli_help_timeout_seconds(),
            log=lambda message: print(f"[distribution-hardening] {message}", flush=True),
        )
        help_text = (
            (sage_help_run.stdout or "")
            + "\n"
            + (sage_help_run.stderr or "")
        )
    except Exception as exc:
        sage_help_run = None
        help_text = str(exc)
    normalized_help_text = re.sub(r"\s+", " ", help_text)

    checks.append(
        _check(
            "cli_help_available",
            bool(
                sage_help_run
                and sage_help_run.returncode == 0
                and "Nexora SAGE" in help_text
                and "python sage.py" in normalized_help_text
                and "python codemaps.py" not in normalized_help_text
            ),
            json.dumps(
                {
                    "sage_returncode": getattr(sage_help_run, "returncode", None),
                    "has_nexora_brand": "Nexora SAGE" in help_text,
                    "has_sage_flow": "python sage.py" in normalized_help_text,
                    "has_legacy_alias": "python codemaps.py" in normalized_help_text,
                },
                ensure_ascii=False,
            ),
        )
    )

    readme = _load_text_file(README_PATH)
    checklist = _load_text_file(CHECKLIST_PATH)
    walkthrough = _load_text_file(WALKTHROUGH_PATH)

    checks.extend(
        _authority_surface_checks(
            scope,
            help_text,
            walkthrough_present=WALKTHROUGH_PATH.exists(),
            walkthrough_text=walkthrough,
            checklist_present=CHECKLIST_PATH.exists(),
            checklist_text=checklist,
        )
    )

    checks.append(
        _check(
            "readme_has_install_path",
            "python sage.py init --plan-only" in readme
            and "python sage.py init --target-root" in readme
            and "python sage.py run --target-root" in readme
            and "python sage.py mcp --print-config --profile target_repository_default" in readme
            and "python codemaps.py" not in readme
            and "Nexora SAGE" in readme,
            "README Nexora SAGE preflight/init/target-run/MCP path without private commands or public codemaps.py aliases",
        )
    )
    checks.append(
        _check(
            "dx_docs_present",
            (CODE_MAPS_DIR / "docs" / "QUICKSTART.md").exists()
            and (CODE_MAPS_DIR / "docs" / "TROUBLESHOOTING.md").exists()
            and (CODE_MAPS_DIR / "docs" / "CLAIMS_EVIDENCE_MATRIX.md").exists(),
            "quickstart + troubleshooting + claims/evidence docs",
        )
    )
    gitignore = _load_text_file(GITIGNORE_PATH)
    checks.append(
        _check(
            "release_gitignore_excludes_generated_and_private_artifacts",
            "output/" in gitignore
            and "scratch/" in gitignore
            and "__pycache__/" in gitignore
            and "*.rar" in gitignore
            and "*.zip" in gitignore
            and ".env" in gitignore,
            "internal .gitignore excludes live outputs, caches, archives and env files",
        )
    )
    checks.extend(_scope_specific_checks(scope))

    payload = {
        "meta": {
            "kind": "distribution_hardening_validation",
            "version": "v1",
            "scope": scope,
        },
        "summary": {
            "total_checks": len(checks),
            "passed_checks": sum(1 for c in checks if c.get("passed")),
            "failed_checks": sum(1 for c in checks if not c.get("passed")),
        },
        "checks": checks,
    }

    artifact_stem = _artifact_stem(scope)
    save_json_atomic(RAW_DIR / f"{artifact_stem}.json", payload)
    lines = [
        "# Distribution Hardening Validation",
        "",
        f"- Scope: `{scope}`",
        f"- Total checks: `{payload['summary']['total_checks']}`",
        f"- Passed: `{payload['summary']['passed_checks']}`",
        f"- Failed: `{payload['summary']['failed_checks']}`",
        "",
        "| Check | Result | Details |",
        "|---|---|---|",
    ]
    for check in checks:
        lines.append(
            f"| `{check['name']}` | {'PASS' if check['passed'] else 'FAIL'} | {check['details']} |"
        )
    save_text_atomic(REPORTS_DIR / f"{artifact_stem}.md", "\n".join(lines) + "\n")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate source-checkout or installed-package distribution boundaries.")
    parser.add_argument("--scope", choices=sorted(VALIDATION_SCOPES), default="source-checkout")
    args = parser.parse_args()
    payload = run_validation(scope=args.scope)
    print(json.dumps(payload.get("summary", {}), ensure_ascii=False))
    return 0 if payload.get("summary", {}).get("failed_checks", 1) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
