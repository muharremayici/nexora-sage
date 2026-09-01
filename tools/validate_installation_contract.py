from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
CODE_MAPS_DIR = TOOLS_DIR.parent
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))

from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.installation_authority import (
    PRIVATE_MAINTAINER_PROFILE,
    PUBLIC_TARGET_REPOSITORY_PROFILE,
    resolve_installation_authority_profile,
)
from tools.validate_python_runtime_compatibility import compile_python_sources, distribution_python_files


RAW_OUTPUT_PATH = RAW_DIR / "installation_contract_validation.json"
REPORT_OUTPUT_PATH = REPORTS_DIR / "installation_contract_validation.md"
PUBLIC_DISTRIBUTION_MANIFEST_PATH = CODE_MAPS_DIR / "PUBLIC_DISTRIBUTION_MANIFEST.json"


def _normalize_dependency_name(value: str) -> str:
    token = re.split(r"[<>=!~;\[]", value.strip(), maxsplit=1)[0].strip()
    return token.lower().replace("_", "-")


def _normalize_dependency_spec(value: str) -> str:
    stripped = value.strip()
    match = re.match(r"^([A-Za-z0-9_.-]+)(.*)$", stripped)
    if not match:
        return re.sub(r"\s+", "", stripped).lower()
    name = match.group(1).lower().replace("_", "-")
    suffix = re.sub(r"\s+", "", match.group(2)).lower()
    return f"{name}{suffix}"


def _dependency_specs(values: list[str]) -> dict[str, str]:
    return {
        _normalize_dependency_name(value): _normalize_dependency_spec(value)
        for value in values
        if str(value).strip()
    }


def _has_exclusive_major_upper_bound(spec: str, major: int) -> bool:
    normalized = _normalize_dependency_spec(spec)
    return bool(re.search(rf"(?:^|,)<{major}(?:\.0+)?(?:,|$)", normalized))


def _load_pyproject_dependency_specs() -> dict[str, str]:
    payload = tomllib.loads((CODE_MAPS_DIR / "pyproject.toml").read_text(encoding="utf-8"))
    deps = payload.get("project", {}).get("dependencies", []) or []
    return _dependency_specs([str(dep) for dep in deps])


def _load_pyproject_dependencies() -> set[str]:
    return set(_load_pyproject_dependency_specs())


def _load_distribution_contract() -> dict[str, Any]:
    payload = tomllib.loads((CODE_MAPS_DIR / "pyproject.toml").read_text(encoding="utf-8"))
    contract = payload.get("tool", {}).get("nexora_sage", {}).get("distribution", {})
    return contract if isinstance(contract, dict) else {}


def _load_requirements_dependency_specs() -> dict[str, str]:
    deps: list[str] = []
    for line in (CODE_MAPS_DIR / "requirements.txt").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        deps.append(stripped)
    return _dependency_specs(deps)


def _load_requirements_dependencies() -> set[str]:
    return set(_load_requirements_dependency_specs())


def _file_contains(path: Path, *needles: str) -> bool:
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8", errors="replace").lower()
    return all(needle.lower() in text for needle in needles)


def _file_contains_any(path: Path, *needles: str) -> bool:
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8", errors="replace").lower()
    return any(needle.lower() in text for needle in needles)


def _surface_contains(paths: list[Path], *needles: str) -> bool:
    text = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in paths
        if path.exists()
    ).lower()
    return all(needle.lower() in text for needle in needles)


def _ci_node_install_command(node_contract: dict[str, Any]) -> str:
    command = [str(part).strip() for part in node_contract.get("package_install_command", [])]
    manifest = str(node_contract.get("package_manifest", "")).strip().replace("\\", "/")
    if not command or not manifest:
        return ""

    command[0] = "npm" if command[0] == "{npm}" else command[0]
    package_root = Path(manifest).parent.as_posix()
    if package_root not in {"", "."}:
        command[2:2] = ["--prefix", package_root]
    return " ".join(command)


def _ci_node_install_surfaces_match(
    node_contract: dict[str, Any], paths: list[Path]
) -> tuple[bool, str]:
    command = _ci_node_install_command(node_contract)
    return bool(command) and all(_file_contains(path, command) for path in paths), command


CI_TARGET_PREPARE_COMMAND = 'mkdir -p "$RUNNER_TEMP/sage-ci-target/src"'
CI_TARGET_INIT_COMMAND = (
    'python sage.py init --skip-deps --target-root "$RUNNER_TEMP/sage-ci-target"'
)
CI_TARGET_DOCTOR_COMMAND = (
    'CODEMAPS_TARGET_ROOT="$RUNNER_TEMP/sage-ci-target" '
    "python sage.py doctor --include-validate --quick --max-seconds 45"
)


def _ci_target_lifecycle_surfaces_match(
    paths: list[Path],
) -> tuple[bool, dict[str, dict[str, Any]]]:
    details: dict[str, dict[str, Any]] = {}
    all_match = True
    for path in paths:
        try:
            path_identity = path.relative_to(CODE_MAPS_DIR).as_posix()
        except ValueError:
            path_identity = path.as_posix()
        if not path.exists():
            details[path_identity] = {"passed": False, "reason": "missing"}
            all_match = False
            continue

        text = path.read_text(encoding="utf-8", errors="replace")
        positions = {
            "prepare": text.find(CI_TARGET_PREPARE_COMMAND),
            "init": text.find(CI_TARGET_INIT_COMMAND),
            "doctor": text.find(CI_TARGET_DOCTOR_COMMAND),
        }
        passed = (
            min(positions.values()) >= 0
            and positions["prepare"] < positions["init"] < positions["doctor"]
        )
        details[path_identity] = {"passed": passed, "positions": positions}
        all_match = all_match and passed
    return all_match and bool(paths), details


def _files_without_legacy_cli_examples(paths: list[Path]) -> tuple[bool, list[str]]:
    legacy_patterns = ("python codemaps.py", "python .\\codemaps.py", "python nexora.py", "python .\\nexora.py")
    offenders: list[str] = []
    for path in paths:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace").lower()
        if any(pattern in text for pattern in legacy_patterns):
            offenders.append(path.relative_to(CODE_MAPS_DIR).as_posix())
    return not offenders, offenders


def _check(name: str, passed: bool, details: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _installation_authority_profile(public_distribution: bool | None = None) -> str:
    if public_distribution is None:
        public_distribution = PUBLIC_DISTRIBUTION_MANIFEST_PATH.is_file()
    return resolve_installation_authority_profile(
        CODE_MAPS_DIR,
        public_distribution=public_distribution,
    )


def _installation_profile_omissions(
    contract: dict[str, Any], authority_profile: str
) -> frozenset[str]:
    profile = contract.get("validation_profiles", {}).get(authority_profile, {})
    return frozenset(
        str(name).strip()
        for name in profile.get("omit_checks", [])
        if str(name).strip()
    )


def run_validation(*, public_distribution: bool | None = None) -> dict[str, Any]:
    authority_profile = _installation_authority_profile(public_distribution)
    is_public_distribution = authority_profile == PUBLIC_TARGET_REPOSITORY_PROFILE
    pyproject_specs = _load_pyproject_dependency_specs()
    requirements_specs = _load_requirements_dependency_specs()
    pyproject_deps = set(pyproject_specs)
    requirements_deps = set(requirements_specs)
    distribution_contract = _load_distribution_contract()
    installation_preflight_contract_path = CODE_MAPS_DIR / "config" / "installation_preflight_contract.json"
    installation_preflight_contract = json.loads(
        installation_preflight_contract_path.read_text(encoding="utf-8")
    )
    installation_profile = installation_preflight_contract.get("profile", {})
    installation_validation_profiles = installation_preflight_contract.get("validation_profiles", {})
    omitted_check_names = _installation_profile_omissions(
        installation_preflight_contract, authority_profile
    )
    installation_python_features = installation_preflight_contract.get("python_features", {})
    installation_node_contract = installation_preflight_contract.get("node", {})
    runtime_source_files = distribution_python_files(CODE_MAPS_DIR)
    runtime_syntax_failures = compile_python_sources(runtime_source_files)
    quality_gate_workflow = CODE_MAPS_DIR / ".github" / "workflows" / "quality-gate.yml"
    quality_gate_doc = CODE_MAPS_DIR / "docs" / "CI_GITHUB_ACTIONS.md"
    ci_node_install_surfaces_match, ci_node_install_command = _ci_node_install_surfaces_match(
        installation_node_contract,
        [quality_gate_workflow, quality_gate_doc],
    )
    ci_target_lifecycle_surfaces_match, ci_target_lifecycle_details = (
        _ci_target_lifecycle_surfaces_match([quality_gate_workflow, quality_gate_doc])
    )
    clean_mirror_forbidden_paths = {
        str(path).replace("\\", "/")
        for path in distribution_contract.get("clean_mirror_forbidden_relative_paths", [])
        if str(path).strip()
    }
    missing_from_requirements = sorted(pyproject_deps - requirements_deps)
    extra_requirements = sorted(requirements_deps - pyproject_deps)
    mismatched_specs = {
        name: {
            "pyproject": pyproject_specs[name],
            "requirements": requirements_specs[name],
        }
        for name in sorted(pyproject_deps & requirements_deps)
        if pyproject_specs[name] != requirements_specs[name]
    }

    clean_notes = CODE_MAPS_DIR / "CLEAN_INSTALL_NOTES.md"
    quickstart = CODE_MAPS_DIR / "docs" / "QUICKSTART.md"
    packaging = CODE_MAPS_DIR / "docs" / "RELEASE_PACKAGING_CHECKLIST.md"
    readme = CODE_MAPS_DIR / "README.md"
    skill = CODE_MAPS_DIR / "SKILL.md"
    sage = CODE_MAPS_DIR / "sage.py"
    codemaps = CODE_MAPS_DIR / "codemaps.py"
    bootstrap = CODE_MAPS_DIR / "tools" / "core" / "bootstrap_env.py"
    installation_proof = CODE_MAPS_DIR / "tools" / "generate_installation_proof.py"
    release_proof_bundle = CODE_MAPS_DIR / "tools" / "run_release_proof_bundle.py"
    release_proof_contract = CODE_MAPS_DIR / "config" / "release_proof_steps_contract.json"
    active_operator_docs = [
        quality_gate_workflow,
        quality_gate_doc,
        CODE_MAPS_DIR / "docs" / "EXTERNAL_TARGET_RUNBOOK.md",
        CODE_MAPS_DIR / "docs" / "L4_PROOF_ENVELOPE_CONTRACT.md",
        CODE_MAPS_DIR / "docs" / "REACT_EDGE_CASE_COVERAGE.md",
        CODE_MAPS_DIR / "docs" / "SIGNAL_REGRESSION_RUNBOOK.md",
        CODE_MAPS_DIR / "docs" / "TROUBLESHOOTING.md",
        CODE_MAPS_DIR / "docs" / "UNIVERSAL_PROOF_RUNBOOK.md",
        CODE_MAPS_DIR / "docs" / "UNIVERSAL_RELEASE_CHECKLIST.md",
        CODE_MAPS_DIR / "docs" / "WHOLE_PRODUCT_WALKTHROUGH.md",
    ]
    active_docs_prefer_nexora, legacy_doc_offenders = _files_without_legacy_cli_examples(active_operator_docs)

    checks = [
        _check(
            "distributed_python_sources_compile_on_active_runtime",
            not runtime_syntax_failures,
            {
                "python": ".".join(str(part) for part in sys.version_info[:3]),
                "source_files": len(runtime_source_files),
                "failures": runtime_syntax_failures,
            },
        ),
        _check(
            "declared_python_runtime_matrix_is_ci_guarded",
            distribution_contract.get("tested_python_versions") == ["3.11", "3.12", "3.13", "3.14"]
            and _file_contains(
                quality_gate_workflow,
                "python-runtime-compatibility",
                'python-version: ["3.11", "3.12", "3.13", "3.14"]',
                "validate_python_runtime_compatibility.py",
                "python -B sage.py --help",
                "python -B tools/run_distribution_tests.py",
            ),
            {
                "tested_python_versions": distribution_contract.get("tested_python_versions", []),
                "workflow": quality_gate_workflow.relative_to(CODE_MAPS_DIR).as_posix(),
            },
        ),
        _check(
            "dependency_manifest_parity",
            not missing_from_requirements and not extra_requirements,
            {
                "pyproject_dependencies": sorted(pyproject_deps),
                "requirements_dependencies": sorted(requirements_deps),
                "missing_from_requirements": missing_from_requirements,
                "extra_requirements": extra_requirements,
            },
        ),
        _check(
            "dependency_specifier_parity",
            not mismatched_specs,
            {
                "pyproject_specs": pyproject_specs,
                "requirements_specs": requirements_specs,
                "mismatched_specs": mismatched_specs,
            },
        ),
        _check(
            "mcp_fastmcp_runtime_is_major_bounded",
            _file_contains(CODE_MAPS_DIR / "tools" / "mcp" / "server.py", "from mcp.server.fastmcp import fastmcp")
            and pyproject_specs.get("mcp") == requirements_specs.get("mcp")
            and _has_exclusive_major_upper_bound(pyproject_specs.get("mcp", ""), 2),
            {
                "api_family": "mcp.server.fastmcp v1",
                "pyproject_spec": pyproject_specs.get("mcp"),
                "requirements_spec": requirements_specs.get("mcp"),
                "required_boundary": "<2",
            },
        ),
        _check(
            "distribution_mode_is_explicit_and_documented",
            distribution_contract.get("mode") == "source_checkout"
            and distribution_contract.get("wheel_supported") is False
            and distribution_contract.get("entrypoint") == "python sage.py"
            and _file_contains(readme, "source checkout", "wheel/pypi")
            and _file_contains(quickstart, "source checkout", "wheel/pypi"),
            distribution_contract,
        ),
        _check(
            "clean_mirror_sync_is_declared",
            distribution_contract.get("clean_mirror_sync") == "tools/sync_clean_distribution.py"
            and (CODE_MAPS_DIR / "tools" / "sync_clean_distribution.py").exists()
            and _file_contains(packaging, "sync_clean_distribution.py"),
            distribution_contract.get("clean_mirror_sync"),
        ),
        _check(
            "clean_mirror_closeout_is_declared",
            distribution_contract.get("clean_mirror_closeout") == "tools/close_clean_distribution.py"
            and (CODE_MAPS_DIR / "tools" / "close_clean_distribution.py").exists()
            and _file_contains(packaging, "close_clean_distribution.py"),
            distribution_contract.get("clean_mirror_closeout"),
        ),
        _check(
            "clean_mirror_excludes_workspace_truth_and_secrets",
            {
                "config/.nexora_hitl_secret",
                "config/codemaps.config.json",
                "config/codemaps.discovery.json",
                "config/codemaps.overrides.json",
                "tools/engines/node_modules",
            }.issubset(clean_mirror_forbidden_paths),
            sorted(clean_mirror_forbidden_paths),
        ),
        _check(
            "public_cli_entrypoint_points_to_internal_implementation",
            _file_contains(sage, "from codemaps import main") and not (CODE_MAPS_DIR / "nexora.py").exists(),
            "sage.py should be the only public root CLI wrapper; codemaps.py is internal implementation, not a documented compatibility command.",
        ),
        _check(
            "init_command_is_publicly_documented",
            _file_contains(readme, "python sage.py init")
            and _file_contains(quickstart, "python sage.py init")
            and (
                is_public_distribution
                or (
                    _file_contains(packaging, "python sage.py init --setup-only")
                    and not _file_contains(packaging, "python sage.py init --setup-only --skip-deps")
                )
            ),
            (
                "README and Quickstart document the public first-truth build."
                if is_public_distribution
                else "README and Quickstart document the first-truth build; clean-mirror packaging uses setup-only without disabling required dependency preparation."
            ),
        ),
        _check(
            "doctor_command_is_publicly_documented",
            _file_contains(readme, "python sage.py doctor")
            and _file_contains(quickstart, "python sage.py doctor")
            and (is_public_distribution or _file_contains(packaging, "python sage.py doctor")),
            (
                "README and Quickstart share the public health-check surface."
                if is_public_distribution
                else "README, Quickstart and release packaging docs share the same health-check surface."
            ),
        ),
        _check(
            "installation_proof_doctor_avoids_release_proof_cycle",
            _file_contains(installation_proof, '"doctor", "--skip-release-proof"'),
            "Installation proof must validate local setup without depending on a pre-existing release proof bundle.",
        ),
        _check(
            "release_proof_runs_installation_smoke",
            _surface_contains(
                [release_proof_bundle, release_proof_contract],
                "installation_proof_smoke",
                "generate_installation_proof.py",
                '"--level"',
                "smoke",
            ),
            "Release proof should include install-proof smoke while install-proof keeps doctor release-proof checks skipped.",
        ),
        _check(
            "target_aware_installation_contract_is_explicit",
            installation_profile.get("id") == "human_and_ai_full"
            and installation_profile.get("default") is True
            and set(installation_validation_profiles) >= {
                "selection",
                PUBLIC_TARGET_REPOSITORY_PROFILE,
                PRIVATE_MAINTAINER_PROFILE,
            }
            and not installation_validation_profiles.get(
                PRIVATE_MAINTAINER_PROFILE, {}
            ).get("omit_checks", [])
            and bool(
                installation_validation_profiles.get(
                    PUBLIC_TARGET_REPOSITORY_PROFILE, {}
                ).get("omit_checks", [])
            )
            and installation_preflight_contract.get("mutation_policy", {}).get("system_runtime_install_requires_explicit_external_action") is True
            and set(installation_node_contract.get("required_for_languages", [])) >= {"javascript", "typescript"}
            and installation_node_contract.get("required_when_react_signal") is True,
            installation_preflight_contract,
        ),
        _check(
            "default_profile_keeps_mcp_and_watchdog_first_class",
            installation_python_features.get("mcp-runtime", {}).get("required_by_default_profile") is True
            and installation_python_features.get("watch-live", {}).get("required_by_default_profile") is True,
            sorted(
                feature
                for feature, spec in installation_python_features.items()
                if isinstance(spec, dict) and spec.get("required_by_default_profile")
            ),
        ),
        _check(
            "plan_only_init_is_public_and_non_installing",
            _file_contains(codemaps, "--plan-only", "Read-only Installation Plan")
            and _file_contains(bootstrap, "--plan-only", "build_installation_plan", "write_installation_plan")
            and _file_contains(
                CODE_MAPS_DIR / "config" / "cli_command_contract.json",
                "init_plan_only",
                "read_only_target_aware_installation_plan",
            ),
            "The plan-only surface must consume canonical target preflight without dependency installation, discovery, or analysis.",
        ),
        _check(
            "bootstrap_supports_skip_deps",
            _file_contains(codemaps, "--skip-deps")
            and _file_contains(bootstrap, "--skip-deps")
            and _file_contains(bootstrap, "install_deps"),
            "Clean-install and offline validation need an explicit no-install path while preserving the dependency bootstrap path.",
        ),
        _check(
            "clean_install_docs_are_present",
            clean_notes.exists()
            and (
                is_public_distribution
                or (
                    _file_contains(packaging, "close_clean_distribution.py")
                    and _file_contains_any(packaging, "friend-machine proof", "friend machine proof")
                )
            ),
            (
                "Clean-install notes are present without requiring private packaging instructions."
                if is_public_distribution
                else "Release packaging names canonical clean-mirror closeout and friend-machine proof steps."
            ),
        ),
        _check(
            "setup_only_init_is_public_and_install_proof_avoids_duplicate_analysis",
            _file_contains(codemaps, "--setup-only")
            and _file_contains(bootstrap, 'if args.mode == "setup":')
            and _file_contains(
                installation_proof,
                "installation_proof_init_mode(level)",
                "public_flag",
                "init_command.append(public_flag)",
            )
            and (
                is_public_distribution
                or (
                    _file_contains(packaging, "python sage.py init --setup-only")
                    and not _file_contains(packaging, "python sage.py init --setup-only --skip-deps")
                )
            ),
            (
                "Public setup and daily/release installation proof initialize workspace truth once."
                if is_public_distribution
                else "Clean setup and daily/release installation proof initialize workspace truth once; packaging setup still prepares required dependencies."
            ),
        ),
        _check(
            "ai_ide_skill_context_is_packaged",
            skill.exists()
            and _file_contains(readme, "SKILL.md")
            and _file_contains(bootstrap, "SKILL.md"),
            "The root SKILL.md must ship with the package and be named in onboarding guidance for AI-agent IDEs.",
        ),
        _check(
            "no_platform_specific_install_script_required",
            not (CODE_MAPS_DIR / "install.sh").exists() and not (CODE_MAPS_DIR / "install.ps1").exists(),
            "The canonical installer is the cross-platform Python CLI: python sage.py init. Shell wrappers are optional sugar, not a required contract.",
        ),
        _check(
            "cross_platform_entrypoint_contract",
            installation_node_contract.get("package_install_command")
            == ["{npm}", "ci", "--ignore-scripts"]
            and _file_contains(
                CODE_MAPS_DIR / "tools" / "core" / "installation_preflight.py",
                "sys.executable",
                "npm_state.get(\"path\")",
            )
            and _file_contains(codemaps, "sys.executable")
            and _file_contains(readme, "python sage.py init"),
            "The setup contract resolves active Python/npm executables instead of requiring Windows-only shell launchers.",
        ),
        _check(
            "ci_node_dependency_install_is_contract_derived_and_documented",
            ci_node_install_surfaces_match,
            {
                "command": ci_node_install_command,
                "workflow": quality_gate_workflow.relative_to(CODE_MAPS_DIR).as_posix(),
                "operator_doc": quality_gate_doc.relative_to(CODE_MAPS_DIR).as_posix(),
            },
        ),
        _check(
            "ci_fresh_target_lifecycle_is_explicit_and_ordered",
            ci_target_lifecycle_surfaces_match,
            ci_target_lifecycle_details,
        ),
        _check(
            "active_operator_docs_prefer_sage_cli",
            active_docs_prefer_nexora,
            {
                "checked_docs": [path.relative_to(CODE_MAPS_DIR).as_posix() for path in active_operator_docs],
                "legacy_cli_examples": legacy_doc_offenders,
            },
        ),
    ]

    known_check_names = {str(check.get("name")) for check in checks}
    unknown_omissions = sorted(omitted_check_names - known_check_names)
    if unknown_omissions:
        raise ValueError(
            "Installation validation profile declares unknown checks: "
            + ", ".join(unknown_omissions)
        )
    selected_checks = [
        check for check in checks if str(check.get("name")) not in omitted_check_names
    ]
    omitted_checks = sorted(omitted_check_names)
    return {
        "meta": {
            "kind": "installation_contract_validation",
            "version": "v1",
            "authority_profile": authority_profile,
        },
        "summary": {
            "authority_profile": authority_profile,
            "total_checks": len(selected_checks),
            "passed_checks": sum(1 for check in selected_checks if check["passed"]),
            "failed_checks": sum(1 for check in selected_checks if not check["passed"]),
            "omitted_maintainer_checks": omitted_checks,
        },
        "checks": selected_checks,
    }


def render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# Installation Contract Validation",
        "",
        f"- Status: {'PASS' if summary.get('failed_checks') == 0 else 'FAIL'}",
        f"- Authority profile: `{summary.get('authority_profile', 'unknown')}`",
        f"- Checks: {summary.get('passed_checks', 0)}/{summary.get('total_checks', 0)}",
        f"- Omitted maintainer-only checks: {', '.join(summary.get('omitted_maintainer_checks', [])) or 'none'}",
        "",
        "| Check | Status | Details |",
        "|---|---:|---|",
    ]
    for check in payload.get("checks", []):
        status = "PASS" if check.get("passed") else "FAIL"
        details = check.get("details")
        if not isinstance(details, str):
            details = json.dumps(details, ensure_ascii=False, sort_keys=True)
        escaped_details = details.replace("|", "\\|")
        lines.append(f"| `{check.get('name')}` | {status} | {escaped_details} |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Nexora SAGE installation/onboarding contract.")
    parser.add_argument("--no-write", action="store_true", help="Do not write validation artifacts.")
    args = parser.parse_args()

    payload = run_validation()
    if not args.no_write:
        save_json_atomic(RAW_OUTPUT_PATH, payload)
        save_text_atomic(REPORT_OUTPUT_PATH, render_report(payload))
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["failed_checks"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
