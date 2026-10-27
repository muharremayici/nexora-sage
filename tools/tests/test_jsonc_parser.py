from __future__ import annotations

import json

import pytest

from tools.core.jsonc import loads_jsonc


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
