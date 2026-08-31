import os
from pathlib import Path
from unittest.mock import patch

from tools.core.execution_identity import (
    DEFAULT_WORKSPACE,
    EXPLICIT_TARGET,
    SAGE_OPERATOR_ACTOR_PROFILE,
    SAGE_SELF_REALITY_PROFILE,
    SAGE_ON_REPOSITORY,
    SAGE_ON_SAGE,
    resolve_execution_identity,
)
from tools.orchestrators.orchestrator import _resolve_pipeline_execution_identity


ROOT = Path(__file__).resolve().parents[2]
PUBLIC_DISTRIBUTION = (ROOT / "PUBLIC_DISTRIBUTION_MANIFEST.json").is_file()


def test_default_target_repository_is_not_sage_self(tmp_path: Path) -> None:
    repository = tmp_path / "target-repository"
    identity = resolve_execution_identity(
        installation_root=tmp_path / "sage",
        default_repository_root=repository,
    )
    assert identity["system_scope"] == SAGE_ON_REPOSITORY
    assert identity["acquisition_mode"] == DEFAULT_WORKSPACE
    assert identity["subject_root"] == str(repository.resolve())


def test_explicit_customer_target_remains_repository_scope(tmp_path: Path) -> None:
    target = tmp_path / "customer"
    identity = resolve_execution_identity(
        target_root=str(target),
        actor_profile="target_repository_default",
        installation_root=tmp_path / "sage",
    )
    assert identity["system_scope"] == SAGE_ON_REPOSITORY
    assert identity["acquisition_mode"] == EXPLICIT_TARGET


def test_authorized_explicit_self_target_remains_sage_scope(tmp_path: Path) -> None:
    if PUBLIC_DISTRIBUTION:
        try:
            resolve_execution_identity(
                target_root=str(tmp_path),
                actor_profile=SAGE_OPERATOR_ACTOR_PROFILE,
                reality_profile=SAGE_SELF_REALITY_PROFILE,
                public_distribution=True,
                installation_root=tmp_path,
            )
        except ValueError as exc:
            assert "cannot authorize SAGE_ON_SAGE" in str(exc)
            return
        raise AssertionError("public distribution must reject explicit SAGE self authority")
    identity = resolve_execution_identity(
        target_root=str(tmp_path),
        actor_profile=SAGE_OPERATOR_ACTOR_PROFILE,
        reality_profile=SAGE_SELF_REALITY_PROFILE,
        installation_root=tmp_path,
    )
    assert identity["system_scope"] == SAGE_ON_SAGE
    assert identity["acquisition_mode"] == EXPLICIT_TARGET
    assert identity["explicit_self_target"] is True
    assert identity["authority_mode"] == "EXPLICIT_SAGE_SELF_PROFILE_PAIR"


def test_default_operator_scope_is_sage_governance(tmp_path: Path) -> None:
    if PUBLIC_DISTRIBUTION:
        try:
            resolve_execution_identity(
                actor_profile=SAGE_OPERATOR_ACTOR_PROFILE,
                reality_profile=SAGE_SELF_REALITY_PROFILE,
                public_distribution=True,
                installation_root=tmp_path,
            )
        except ValueError as exc:
            assert "cannot authorize SAGE_ON_SAGE" in str(exc)
            return
        raise AssertionError("public distribution must reject default SAGE self authority")
    identity = resolve_execution_identity(
        actor_profile=SAGE_OPERATOR_ACTOR_PROFILE,
        reality_profile=SAGE_SELF_REALITY_PROFILE,
        installation_root=tmp_path,
    )
    assert identity["system_scope"] == SAGE_ON_SAGE
    assert identity["acquisition_mode"] == DEFAULT_WORKSPACE


def test_self_actor_without_reality_profile_fails_closed(tmp_path: Path) -> None:
    try:
        resolve_execution_identity(
            actor_profile=SAGE_OPERATOR_ACTOR_PROFILE,
            installation_root=tmp_path,
        )
    except ValueError as exc:
        assert "explicit sage_operator_debug + sage_self profile pair" in str(exc)
    else:
        raise AssertionError("actor-only SAGE self authority must fail closed")


def test_self_reality_without_actor_profile_fails_closed(tmp_path: Path) -> None:
    try:
        resolve_execution_identity(
            reality_profile=SAGE_SELF_REALITY_PROFILE,
            installation_root=tmp_path,
        )
    except ValueError as exc:
        assert "explicit sage_operator_debug + sage_self profile pair" in str(exc)
    else:
        raise AssertionError("reality-only SAGE self authority must fail closed")


def test_self_profile_pair_cannot_target_customer_repository(tmp_path: Path) -> None:
    try:
        resolve_execution_identity(
            target_root=str(tmp_path / "customer"),
            actor_profile=SAGE_OPERATOR_ACTOR_PROFILE,
            reality_profile=SAGE_SELF_REALITY_PROFILE,
            installation_root=tmp_path / "sage",
        )
    except ValueError as exc:
        assert "canonical SAGE installation root" in str(exc)
    else:
        raise AssertionError("SAGE self authority must not transfer to a customer repository")


def test_public_distribution_rejects_self_profile_pair(tmp_path: Path) -> None:
    try:
        resolve_execution_identity(
            actor_profile=SAGE_OPERATOR_ACTOR_PROFILE,
            reality_profile=SAGE_SELF_REALITY_PROFILE,
            public_distribution=True,
            installation_root=tmp_path,
        )
    except ValueError as exc:
        assert "cannot authorize SAGE_ON_SAGE" in str(exc)
    else:
        raise AssertionError("public distribution must reject SAGE self authority")


def test_default_pipeline_run_analyzes_repository_not_sage_governance() -> None:
    identity = _resolve_pipeline_execution_identity({})
    assert identity["system_scope"] == SAGE_ON_REPOSITORY
    assert identity["acquisition_mode"] == DEFAULT_WORKSPACE


def test_external_pipeline_run_changes_acquisition_not_subject_scope(tmp_path: Path) -> None:
    target = tmp_path / "customer"
    identity = _resolve_pipeline_execution_identity(
        {
            "_target_root_override": {
                "enabled": True,
                "target_root": str(target),
            }
        }
    )
    assert identity["system_scope"] == SAGE_ON_REPOSITORY
    assert identity["acquisition_mode"] == EXPLICIT_TARGET
    assert identity["subject_root"] == str(target.resolve())


def test_pipeline_self_scope_requires_the_complete_profile_pair() -> None:
    with patch.dict(
        os.environ,
        {
            "SAGE_ACTOR_PROFILE": SAGE_OPERATOR_ACTOR_PROFILE,
            "SAGE_REALITY_TARGET_PROFILE": SAGE_SELF_REALITY_PROFILE,
        },
        clear=False,
    ):
        if PUBLIC_DISTRIBUTION:
            try:
                _resolve_pipeline_execution_identity({})
            except ValueError as exc:
                assert "cannot authorize SAGE_ON_SAGE" in str(exc)
                return
            raise AssertionError("public pipeline must reject the private self profile pair")
        identity = _resolve_pipeline_execution_identity({})

    assert identity["system_scope"] == SAGE_ON_SAGE
    assert identity["authority_mode"] == "EXPLICIT_SAGE_SELF_PROFILE_PAIR"
