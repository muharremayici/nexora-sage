from __future__ import annotations

import pytest

from tools.core import config
from tools.engines import self_healing_generator, validation_oracle
from tools.orchestrators import discovery


def test_discovery_tsconfig_parser_preserves_clean_machine_alias(tmp_path) -> None:
    (tmp_path / "tsconfig.json").write_text(
        '{"compilerOptions":{"baseUrl":".","paths":{"@/*":["./src/*"]}}}\n',
        encoding="utf-8",
    )

    assert discovery.parse_tsconfig(tmp_path) == {"@/*": ["./src/*"]}


def test_config_alias_consumers_share_jsonc_authority(tmp_path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "tsconfig.json").write_text(
        r'''
        {
          "compilerOptions": {
            "baseUrl": ".",
            "paths": {"@/*": ["./src/*"],},
          },
        }
        ''',
        encoding="utf-8",
    )

    assert config._infer_target_path_aliases(tmp_path) == {"@/*": ["./src/*"]}
    observed = config._observe_target_path_aliases(tmp_path)
    assert observed["aliases"] == ["@/*"]
    assert observed["scoped_alias_maps"][0]["path_aliases"] == {"@/*": ["./src/*"]}


def test_oracle_sanctuary_config_preserves_jsonc_alias_glob(tmp_path) -> None:
    (tmp_path / "tsconfig.json").write_text(
        '{"compilerOptions":{"paths":{"@/*":["./src/*"]}, /* keep */ "strict":true}}',
        encoding="utf-8",
    )

    oracle = object.__new__(validation_oracle.ValidationOracle)
    generated = oracle._build_sanctuary_tsconfig(tmp_path)

    assert generated["compilerOptions"]["paths"]["@/*"] == ["./src/*"]
    assert generated["compilerOptions"]["strict"] is True


def test_self_healer_uses_jsonc_alias_glob(monkeypatch, tmp_path) -> None:
    (tmp_path / "tsconfig.json").write_text(
        '{"compilerOptions":{"paths":{"@/*":["./src/*"]},},}',
        encoding="utf-8",
    )
    monkeypatch.setattr(self_healing_generator, "ROOT", tmp_path)
    monkeypatch.setattr(
        self_healing_generator,
        "DYNAMIC_CONFIG",
        {"variations": {"MAIN": "."}},
    )
    healer = object.__new__(self_healing_generator.CodeHealer)
    healer.project_name = "MAIN"

    assert healer._project_alias_points_to_src_root() is True


def test_self_healer_refuses_alias_rewrite_when_project_config_is_invalid(monkeypatch, tmp_path) -> None:
    (tmp_path / "tsconfig.json").write_text('{"compilerOptions": ', encoding="utf-8")
    monkeypatch.setattr(self_healing_generator, "ROOT", tmp_path)
    monkeypatch.setattr(
        self_healing_generator,
        "DYNAMIC_CONFIG",
        {"variations": {"MAIN": "."}},
    )
    healer = object.__new__(self_healing_generator.CodeHealer)
    healer.project_name = "MAIN"

    with pytest.raises(ValueError):
        healer._project_alias_points_to_src_root()
