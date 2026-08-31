import json
from pathlib import Path

from tools.core.validate_execution_profiles import (
    doctor_validator_profile,
    validator_commands_for_set_id,
)
from tools.validate_entrypoints_and_failures import discovery_entrypoint_environment
from codemaps import _doctor_capability_release_blocks


def _paths_for_scope(scope: str) -> set[str]:
    profile = doctor_validator_profile(scope)
    commands = validator_commands_for_set_id(str(profile["validator_set_id"]))
    return {str(command[-1]).replace("\\", "/") for command in commands}


def test_repository_doctor_does_not_inherit_sage_self_evidence_authority() -> None:
    paths = _paths_for_scope("SAGE_ON_REPOSITORY")

    assert not any(path.endswith("tools/validate_contextos_contracts.py") for path in paths)
    assert not any(path.endswith("tools/validate_product_reports.py") for path in paths)
    assert any(path.endswith("tools/validate_entrypoints_and_failures.py") for path in paths)
    assert not any(path.endswith("tools/validate_source_contracts.py") for path in paths)


def test_sage_self_doctor_retains_product_evidence_validators() -> None:
    paths = _paths_for_scope("SAGE_ON_SAGE")

    assert any(path.endswith("tools/validate_contextos_contracts.py") for path in paths)
    assert any(path.endswith("tools/validate_product_reports.py") for path in paths)


def test_repository_doctor_cannot_be_blocked_by_sage_self_release_status() -> None:
    assert not _doctor_capability_release_blocks("SAGE_ON_REPOSITORY", "FAIL")
    assert not _doctor_capability_release_blocks("SAGE_ON_REPOSITORY", "ATTENTION")
    assert _doctor_capability_release_blocks("SAGE_ON_SAGE", "FAIL")
    assert not _doctor_capability_release_blocks("SAGE_ON_SAGE", "PASS")


def test_public_entrypoint_validation_recovers_explicit_target_from_compiled_config(
    tmp_path: Path,
) -> None:
    installation_root = tmp_path / "sage"
    target_root = tmp_path / "target"
    config_dir = installation_root / "config"
    config_dir.mkdir(parents=True)
    target_root.mkdir()
    manifest = installation_root / "PUBLIC_DISTRIBUTION_MANIFEST.json"
    manifest.write_text("{}\n", encoding="utf-8")
    config_file = config_dir / "codemaps.config.json"
    config_file.write_text(
        json.dumps({"workspace_root": "../target"}) + "\n",
        encoding="utf-8",
    )

    assert discovery_entrypoint_environment(
        code_maps_dir=installation_root,
        config_file=config_file,
        public_manifest=manifest,
    ) == {"CODEMAPS_TARGET_ROOT": str(target_root.resolve())}


def test_public_entrypoint_validation_does_not_guess_without_compiled_target(
    tmp_path: Path,
) -> None:
    installation_root = tmp_path / "sage"
    installation_root.mkdir()
    manifest = installation_root / "PUBLIC_DISTRIBUTION_MANIFEST.json"
    manifest.write_text("{}\n", encoding="utf-8")

    assert discovery_entrypoint_environment(
        code_maps_dir=installation_root,
        config_file=installation_root / "config" / "codemaps.config.json",
        public_manifest=manifest,
    ) == {}
