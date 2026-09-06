from __future__ import annotations

import json

import pytest

from tools.core import config
from tools.core.jsonc import loads_jsonc
from tools.engines import self_healing_generator, validation_oracle
from tools.orchestrators import discovery


def test_jsonc_parser_preserves_alias_globs_and_comment_like_strings() -> None:
    payload = loads_jsonc(
        r'''
        {
          // JSONC comment
          "compilerOptions": {
            "paths": {
              "@/*": ["./src/*"],
              "https://example.test/*": ["./remote/*"]
            },
          },
        }
        '''
    )

    assert payload["compilerOptions"]["paths"] == {
        "@/*": ["./src/*"],
        "https://example.test/*": ["./remote/*"],
    }


def test_jsonc_parser_supports_block_comments_without_hiding_strings() -> None:
    payload = loads_jsonc(
        r'''
        {
          "pattern": "/* literal */",
          /* actual comment */
          "escaped": "quote: \" // literal",
          "values": [1, 2,],
        }
        '''
    )

    assert payload == {
        "pattern": "/* literal */",
        "escaped": 'quote: " // literal',
        "values": [1, 2],
    }


def test_jsonc_parser_rejects_unterminated_block_comment() -> None:
    with pytest.raises(json.JSONDecodeError, match="Unterminated block comment"):
        loads_jsonc('{"value": 1, /* missing close')


def test_jsonc_parser_accepts_one_utf8_bom() -> None:
    assert loads_jsonc('\ufeff{"compilerOptions": {}}') == {"compilerOptions": {}}


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
