from __future__ import annotations

import argparse
import json
from pathlib import Path

import codemaps


def _args(target: Path, *, refresh_policy: str = "current") -> argparse.Namespace:
    return argparse.Namespace(
        target_root=str(target),
        projects="MAIN",
        mode="baseline",
        refresh_policy=refresh_policy,
    )


def _proof_fixture(current_dir: Path, verdict: str = "PASS") -> None:
    raw_dir = current_dir / ".raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "target_repository_proof_bundle.json").write_text(
        json.dumps({"summary": {"verdict": verdict}}),
        encoding="utf-8",
    )


def test_target_proof_current_delegates_to_canonical_generator(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    target = tmp_path / "repo"
    target.mkdir()
    sage_root = tmp_path / "sage"
    monkeypatch.setattr(codemaps, "CODE_MAPS_DIR", sage_root)
    monkeypatch.setattr(
        codemaps,
        "TARGET_REPOSITORY_PROOF_GENERATOR",
        sage_root / "tools" / "generate_target_repository_proof_bundle.py",
    )
    monkeypatch.setattr(
        codemaps,
        "_target_root_env",
        lambda _args: {"CODEMAPS_TARGET_ROOT": str(target.resolve())},
    )
    target_dir = (
        sage_root
        / "output"
        / "external_targets"
        / codemaps._target_output_slug(str(target.resolve()))
    )
    current_dir = target_dir / "generations" / "sage-run-current"
    monkeypatch.setattr(
        "tools.core.external_target_generation.resolve_current_external_target_generation",
        lambda _target_dir: (
            current_dir,
            {"run_id": "sage-run-current"},
            "validated_current",
        ),
    )
    calls = []
    def run_generator(command, **kwargs):
        calls.append((command, kwargs))
        _proof_fixture(current_dir)
        return 0

    monkeypatch.setattr(codemaps, "run_command", run_generator)

    result = codemaps.cmd_target_proof(_args(target))

    assert result == 0
    command, kwargs = calls[0]
    assert command[1] == str(codemaps.TARGET_REPOSITORY_PROOF_GENERATOR)
    assert command[-2:] == ["--projects", "MAIN"]
    assert kwargs["env"]["CODEMAPS_EXTERNAL_RUN_ID"] == "sage-run-current"
    terminal = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert terminal["status"] == "PASS"
    assert terminal["refresh_performed"] is False


def test_target_proof_if_missing_runs_one_claim_owned_quality_refresh(
    tmp_path: Path,
    monkeypatch,
) -> None:
    target = tmp_path / "repo"
    target.mkdir()
    sage_root = tmp_path / "sage"
    monkeypatch.setattr(codemaps, "CODE_MAPS_DIR", sage_root)
    monkeypatch.setattr(
        codemaps,
        "TARGET_REPOSITORY_PROOF_GENERATOR",
        sage_root / "tools" / "generate_target_repository_proof_bundle.py",
    )
    monkeypatch.setattr(
        codemaps,
        "_target_root_env",
        lambda _args: {"CODEMAPS_TARGET_ROOT": str(target.resolve())},
    )
    current_dir = sage_root / "current"
    resolutions = iter([
        (None, {}, "current_pointer_unavailable:FileNotFoundError"),
        (current_dir, {"run_id": "sage-run-refreshed"}, "validated_current"),
    ])
    monkeypatch.setattr(
        "tools.core.external_target_generation.resolve_current_external_target_generation",
        lambda _target_dir: next(resolutions),
    )
    refreshes = []
    monkeypatch.setattr(
        codemaps,
        "cmd_run",
        lambda args: refreshes.append(args) or 0,
    )
    monkeypatch.setattr(
        codemaps,
        "run_command",
        lambda *_args, **_kwargs: _proof_fixture(current_dir) or 0,
    )

    result = codemaps.cmd_target_proof(_args(target, refresh_policy="if-missing"))

    assert result == 0
    assert len(refreshes) == 1
    assert refreshes[0].projects == "MAIN"
    assert refreshes[0].profile == "target-quality"
    assert refreshes[0].full is False
    assert refreshes[0].refresh is True


def test_target_proof_current_blocks_without_validated_generation(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    target = tmp_path / "repo"
    target.mkdir()
    monkeypatch.setattr(
        codemaps,
        "_target_root_env",
        lambda _args: {"CODEMAPS_TARGET_ROOT": str(target.resolve())},
    )
    monkeypatch.setattr(
        "tools.core.external_target_generation.resolve_current_external_target_generation",
        lambda _target_dir: (None, {}, "current_pointer_kind_invalid"),
    )
    monkeypatch.setattr(
        codemaps,
        "run_command",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("proof generator must not run")
        ),
    )

    result = codemaps.cmd_target_proof(_args(target))

    assert result == 2
    terminal = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert terminal["status"] == "BLOCKED"
    assert terminal["current_reason"] == "current_pointer_kind_invalid"


def test_target_proof_fails_closed_when_builder_writes_no_valid_summary(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    target = tmp_path / "repo"
    target.mkdir()
    sage_root = tmp_path / "sage"
    monkeypatch.setattr(codemaps, "CODE_MAPS_DIR", sage_root)
    monkeypatch.setattr(
        codemaps,
        "TARGET_REPOSITORY_PROOF_GENERATOR",
        sage_root / "tools" / "generate_target_repository_proof_bundle.py",
    )
    monkeypatch.setattr(
        codemaps,
        "_target_root_env",
        lambda _args: {"CODEMAPS_TARGET_ROOT": str(target.resolve())},
    )
    current_dir = sage_root / "current"
    current_dir.mkdir(parents=True)
    monkeypatch.setattr(
        "tools.core.external_target_generation.resolve_current_external_target_generation",
        lambda _target_dir: (
            current_dir,
            {"run_id": "sage-run-current"},
            "validated_current",
        ),
    )
    monkeypatch.setattr(codemaps, "run_command", lambda *_args, **_kwargs: 0)

    result = codemaps.cmd_target_proof(_args(target))

    assert result == 3
    terminal = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert terminal["status"] == "BLOCKED"
    assert terminal["reason"] == "proof_artifact_not_refreshed"
