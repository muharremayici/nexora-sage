import json
from pathlib import Path

from tools.core.validate_execution_profiles import (
    doctor_validator_profile,
    validator_commands_for_set_id,
)
from tools.validate_entrypoints_and_failures import discovery_entrypoint_environment
from codemaps import _doctor_capability_release_blocks
from tools import validate_react_transitive_propagation as transitive_validator


def _validator_outputs(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(transitive_validator, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(transitive_validator, "REPORTS_DIR", tmp_path / "reports")


def test_repository_doctor_selected_transitive_validator_runs_current_contract(
    monkeypatch, tmp_path: Path,
) -> None:
    selected = _paths_for_scope("SAGE_ON_REPOSITORY")
    assert str(Path(transitive_validator.__file__).resolve()).replace("\\", "/") in selected
    _validator_outputs(monkeypatch, tmp_path)

    assert transitive_validator.main() == 0
    payload = json.loads((tmp_path / "raw/react_transitive_propagation_validation.json").read_text())
    assert payload["summary"]["status"] == "PASS"
    checks = {row["name"]: row for row in payload["checks"]}
    assert checks["positive_call_records_exact_provider_file_query_hint"]["passed"]
    assert checks["positive_call_records_exact_provider_file_store_hint"]["passed"]
    assert checks["provider_query_is_not_consumer_owned"]["passed"]
    assert checks["provider_store_use_is_not_consumer_subscription"]["passed"]
    for variant in ("import_only", "legacy_evidence", "wrong_call_source",
                    "non_top_level", "unresolved_provider", "ambiguous_provider"):
        assert checks[f"{variant}_does_not_create_hook_hint"]["passed"]


def test_transitive_validator_fails_when_positive_hints_disappear(
    monkeypatch, tmp_path: Path,
) -> None:
    _validator_outputs(monkeypatch, tmp_path)
    real_build = transitive_validator.build_state_flow_results

    def missing_hints(*args, **kwargs):
        result = real_build(*args, **kwargs)
        result["transitive_hook_consumers"] = {}
        return result

    monkeypatch.setattr(transitive_validator, "build_state_flow_results", missing_hints)
    assert transitive_validator.main() == 1
    payload = json.loads((tmp_path / "raw/react_transitive_propagation_validation.json").read_text())
    failed = {row["name"] for row in payload["checks"] if not row["passed"]}
    assert failed == {"positive_call_records_exact_provider_file_query_hint",
                      "positive_call_records_exact_provider_file_store_hint"}


def test_transitive_validator_rejects_old_provider_ownership_promotion(
    monkeypatch, tmp_path: Path,
) -> None:
    _validator_outputs(monkeypatch, tmp_path)
    real_build = transitive_validator.build_state_flow_results

    def promoted_signals(*args, **kwargs):
        result = real_build(*args, **kwargs)
        result["tanstack_queries"]["APP::pages/AuthorPage.tsx"] = ["queryKeys.author.details(id)"]
        result["zustand_consumers"]["APP::pages/AuthorShell.tsx"] = ["useAuthorStore"]
        result["zustand_stores"]["APP::pages/AuthorShell.tsx"] = ["zustand_store"]
        return result

    monkeypatch.setattr(transitive_validator, "build_state_flow_results", promoted_signals)
    payload = transitive_validator.run_validation()
    assert payload["summary"]["status"] == "FAIL"
    failed = {row["name"] for row in payload["checks"] if not row["passed"]}
    assert {"provider_query_is_not_consumer_owned", "provider_store_use_is_not_consumer_subscription",
            "consumer_does_not_inherit_store_ownership"} <= failed


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
