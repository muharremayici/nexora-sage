from __future__ import annotations

import json
from pathlib import Path

import pytest

import tools.validate_installation_contract as installation_contract
from tools.validate_installation_contract import (
    CI_TARGET_DOCTOR_COMMAND,
    CI_TARGET_INIT_COMMAND,
    CI_TARGET_PREPARE_COMMAND,
    PRIVATE_MAINTAINER_PROFILE,
    PUBLIC_TARGET_REPOSITORY_PROFILE,
    _ci_node_install_command,
    _ci_node_install_surfaces_match,
    _ci_target_lifecycle_surfaces_match,
    _has_exclusive_major_upper_bound,
    _installation_authority_profile,
    _installation_profile_omissions,
    _normalize_dependency_spec,
    render_report,
    run_validation,
)
from tools.validate_python_runtime_compatibility import compile_python_sources


EXPECTED_MAINTAINER_ONLY_CHECKS = frozenset(
    {
        "clean_mirror_sync_is_declared",
        "clean_mirror_closeout_is_declared",
        "clean_mirror_excludes_workspace_truth_and_secrets",
        "release_proof_runs_installation_smoke",
    }
)


def test_dependency_spec_normalization_preserves_version_contract() -> None:
    assert _normalize_dependency_spec("MCP >= 1.26.0, < 2") == "mcp>=1.26.0,<2"


def test_major_specific_api_requires_exclusive_upper_bound() -> None:
    assert _has_exclusive_major_upper_bound("mcp>=1.26.0,<2", 2) is True
    assert _has_exclusive_major_upper_bound("mcp>=1.26.0", 2) is False
    assert _has_exclusive_major_upper_bound("mcp>=1.26.0,<=2", 2) is False


def test_ci_node_install_command_is_derived_from_package_manifest() -> None:
    command = _ci_node_install_command(
        {
            "package_manifest": "tools/engines/package.json",
            "package_install_command": ["{npm}", "ci", "--ignore-scripts"],
        }
    )

    assert command == "npm ci --prefix tools/engines --ignore-scripts"


def test_ci_node_install_surface_parity_fails_when_operator_doc_omits_command(
    tmp_path: Path,
) -> None:
    contract = {
        "package_manifest": "tools/engines/package.json",
        "package_install_command": ["{npm}", "ci", "--ignore-scripts"],
    }
    workflow = tmp_path / "quality-gate.yml"
    operator_doc = tmp_path / "CI_GITHUB_ACTIONS.md"
    workflow.write_text("npm ci --prefix tools/engines --ignore-scripts", encoding="utf-8")
    operator_doc.write_text("python -m pip install -e .", encoding="utf-8")

    matched, command = _ci_node_install_surfaces_match(contract, [workflow, operator_doc])

    assert command == "npm ci --prefix tools/engines --ignore-scripts"
    assert matched is False


def test_ci_initializes_explicit_target_before_doctor_validation() -> None:
    paths = [
        installation_contract.CODE_MAPS_DIR / ".github" / "workflows" / "quality-gate.yml",
        installation_contract.CODE_MAPS_DIR / "docs" / "CI_GITHUB_ACTIONS.md",
    ]

    matched, details = _ci_target_lifecycle_surfaces_match(paths)

    assert matched is True
    assert all(detail["passed"] is True for detail in details.values())


def test_ci_target_lifecycle_rejects_doctor_before_init(tmp_path: Path) -> None:
    invalid_surface = tmp_path / "quality-gate.yml"
    invalid_surface.write_text(
        "\n".join(
            [
                CI_TARGET_PREPARE_COMMAND,
                CI_TARGET_DOCTOR_COMMAND,
                CI_TARGET_INIT_COMMAND,
            ]
        ),
        encoding="utf-8",
    )

    matched, details = _ci_target_lifecycle_surfaces_match([invalid_surface])

    assert matched is False
    assert next(iter(details.values()))["passed"] is False


def test_current_installation_manifests_preserve_mcp_v1_compatibility() -> None:
    payload = run_validation()
    checks = {check["name"]: check for check in payload["checks"]}

    assert checks["dependency_specifier_parity"]["passed"] is True
    assert checks["mcp_fastmcp_runtime_is_major_bounded"]["passed"] is True


def test_python_source_compiler_reports_invalid_source(tmp_path: Path) -> None:
    valid = tmp_path / "valid.py"
    invalid = tmp_path / "invalid.py"
    valid.write_text("value = 1\n", encoding="utf-8")
    invalid.write_text("def broken(:\n", encoding="utf-8")

    failures = compile_python_sources([valid, invalid])

    assert len(failures) == 1
    assert Path(failures[0]["path"]).name == "invalid.py"
    assert failures[0]["error_type"] == "SyntaxError"


def test_current_installation_contract_guards_python_runtime_matrix() -> None:
    payload = run_validation()
    checks = {check["name"]: check for check in payload["checks"]}

    assert checks["distributed_python_sources_compile_on_active_runtime"]["passed"] is True
    assert checks["declared_python_runtime_matrix_is_ci_guarded"]["passed"] is True
    assert checks["ci_node_dependency_install_is_contract_derived_and_documented"]["passed"] is True


def test_public_installation_profile_keeps_product_checks_and_omits_maintainer_checks() -> None:
    payload = run_validation(public_distribution=True)
    names = {str(check["name"]) for check in payload["checks"]}

    assert payload["summary"]["authority_profile"] == PUBLIC_TARGET_REPOSITORY_PROFILE
    assert payload["summary"]["failed_checks"] == 0
    assert payload["summary"]["total_checks"] == 22
    assert set(payload["summary"]["omitted_maintainer_checks"]) == EXPECTED_MAINTAINER_ONLY_CHECKS
    assert not (names & EXPECTED_MAINTAINER_ONLY_CHECKS)
    assert {
        "distributed_python_sources_compile_on_active_runtime",
        "dependency_manifest_parity",
        "init_command_is_publicly_documented",
        "doctor_command_is_publicly_documented",
        "ci_fresh_target_lifecycle_is_explicit_and_ordered",
        "target_aware_installation_contract_is_explicit",
        "default_profile_keeps_mcp_and_watchdog_first_class",
        "setup_only_init_is_public_and_install_proof_avoids_duplicate_analysis",
    }.issubset(names)


def test_private_installation_profile_retains_maintainer_checks() -> None:
    payload = run_validation(public_distribution=False)
    names = {str(check["name"]) for check in payload["checks"]}

    assert payload["summary"]["authority_profile"] == PRIVATE_MAINTAINER_PROFILE
    assert payload["summary"]["total_checks"] == 26
    assert payload["summary"]["omitted_maintainer_checks"] == []
    assert EXPECTED_MAINTAINER_ONLY_CHECKS <= names


def test_installation_profile_auto_detects_public_manifest(monkeypatch, tmp_path: Path) -> None:
    manifest = tmp_path / "PUBLIC_DISTRIBUTION_MANIFEST.json"
    manifest.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(
        "tools.validate_installation_contract.PUBLIC_DISTRIBUTION_MANIFEST_PATH",
        manifest,
    )

    assert _installation_authority_profile() == PUBLIC_TARGET_REPOSITORY_PROFILE


def test_installation_profile_omissions_are_contract_driven() -> None:
    contract = json.loads(
        (installation_contract.CODE_MAPS_DIR / "config" / "installation_preflight_contract.json").read_text(
            encoding="utf-8"
        )
    )

    assert _installation_profile_omissions(
        contract, PUBLIC_TARGET_REPOSITORY_PROFILE
    ) == EXPECTED_MAINTAINER_ONLY_CHECKS
    assert not _installation_profile_omissions(contract, PRIVATE_MAINTAINER_PROFILE)


def test_public_profile_does_not_require_private_packaging_document(
    monkeypatch,
) -> None:
    packaging = (
        installation_contract.CODE_MAPS_DIR / "docs" / "RELEASE_PACKAGING_CHECKLIST.md"
    ).resolve()
    original_file_contains = installation_contract._file_contains
    original_file_contains_any = installation_contract._file_contains_any

    def without_private_packaging(path: Path, *needles: str) -> bool:
        if path.resolve() == packaging:
            return False
        return original_file_contains(path, *needles)

    def without_private_packaging_any(path: Path, *needles: str) -> bool:
        if path.resolve() == packaging:
            return False
        return original_file_contains_any(path, *needles)

    monkeypatch.setattr(installation_contract, "_file_contains", without_private_packaging)
    monkeypatch.setattr(
        installation_contract, "_file_contains_any", without_private_packaging_any
    )

    payload = run_validation(public_distribution=True)

    assert payload["summary"]["failed_checks"] == 0
    assert {
        check["name"]: check["passed"] for check in payload["checks"]
    }["clean_install_docs_are_present"] is True


def test_public_installation_report_names_authority_and_omissions() -> None:
    report = render_report(run_validation(public_distribution=True))
    private_report = render_report(run_validation(public_distribution=False))

    assert "Authority profile: `public_target_repository`" in report
    assert "release_proof_runs_installation_smoke" in report
    assert "release packaging docs share" not in report
    assert "release packaging docs share" in private_report


def test_unknown_profile_omission_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(
        installation_contract,
        "_installation_profile_omissions",
        lambda *_args: frozenset({"unknown_installation_check"}),
    )

    with pytest.raises(ValueError, match="unknown_installation_check"):
        run_validation(public_distribution=True)
