from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import codemaps
from tools.core.validate_execution_profiles import (
    resolve_validate_execution_profile,
    validate_execution_contract,
    validator_commands_for_profile,
)


def _args(**overrides: object) -> SimpleNamespace:
    fields = {
        "validate_profile_live_refresh": False,
        "validate_profile_snapshot_only": False,
        "validate_profile_source_clean": False,
        "validate_auto_remediate_stale": False,
        "validate_update_signal_baseline": False,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def test_default_profile_is_contract_owned() -> None:
    contract = validate_execution_contract()
    profile = resolve_validate_execution_profile(_args())

    assert profile["id"] == contract["default_profile_id"]


def test_multiple_validation_profiles_fail_closed() -> None:
    with pytest.raises(ValueError, match="Only one validation profile"):
        resolve_validate_execution_profile(
            _args(
                validate_profile_live_refresh=True,
                validate_profile_source_clean=True,
            )
        )


def test_source_clean_validator_paths_are_absolute_and_engine_free() -> None:
    profile = resolve_validate_execution_profile(_args(validate_profile_source_clean=True))
    commands = validator_commands_for_profile(profile)
    configured_forbidden = set(
        validate_execution_contract()["source_clean_forbidden_validator_paths"]
    )

    assert commands
    assert all(command[1].startswith(str(codemaps.CODE_MAPS_DIR)) for command in commands)
    assert not configured_forbidden.intersection(
        str(Path(command[1]).relative_to(codemaps.CODE_MAPS_DIR)).replace("\\", "/")
        for command in commands
    )


def test_source_clean_skips_runtime_truth_and_uses_only_declared_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def unexpected_runtime_truth(*_args: object, **_kwargs: object) -> int:
        raise AssertionError("source-clean must not request runtime truth")

    monkeypatch.setattr(codemaps, "ensure_runtime_truth", unexpected_runtime_truth)
    monkeypatch.setattr(codemaps, "run_command", lambda command, **_kwargs: calls.append(command) or 0)

    assert codemaps.cmd_validate(_args(validate_profile_source_clean=True)) == 0
    assert calls == validator_commands_for_profile(
        resolve_validate_execution_profile(_args(validate_profile_source_clean=True))
    )


def test_snapshot_only_requires_existing_truth_without_refreshing_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_calls: list[tuple[object, object]] = []
    validator_calls: list[list[str]] = []

    def record_runtime_truth(scope: object, *, allow_self_heal: object = True) -> int:
        runtime_calls.append((scope, allow_self_heal))
        return 0

    def unexpected_refresh() -> int:
        raise AssertionError("snapshot-only must not refresh validation artifacts")

    monkeypatch.setattr(codemaps, "ensure_runtime_truth", record_runtime_truth)
    monkeypatch.setattr(codemaps, "_refresh_stale_validation_artifacts", unexpected_refresh)
    monkeypatch.setattr(
        codemaps,
        "run_command",
        lambda command, **_kwargs: validator_calls.append(command) or 0,
    )

    assert codemaps.cmd_validate(_args(validate_profile_snapshot_only=True)) == 0
    assert runtime_calls == [("validate snapshot", False)]
    assert validator_calls == validator_commands_for_profile(
        resolve_validate_execution_profile(_args(validate_profile_snapshot_only=True))
    )
