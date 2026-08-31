from __future__ import annotations

import json
from typing import Any


def _strip_comments(content: str) -> str:
    """Remove JSONC comments without interpreting comment markers inside strings."""

    result: list[str] = []
    index = 0
    in_string = False
    escaped = False

    while index < len(content):
        char = content[index]
        next_char = content[index + 1] if index + 1 < len(content) else ""

        if in_string:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue

        if char == '"':
            in_string = True
            result.append(char)
            index += 1
            continue

        if char == "/" and next_char == "/":
            result.extend((" ", " "))
            index += 2
            while index < len(content) and content[index] not in "\r\n":
                result.append(" ")
                index += 1
            continue

        if char == "/" and next_char == "*":
            result.extend((" ", " "))
            index += 2
            while index < len(content):
                char = content[index]
                next_char = content[index + 1] if index + 1 < len(content) else ""
                if char == "*" and next_char == "/":
                    result.extend((" ", " "))
                    index += 2
                    break
                result.append(char if char in "\r\n" else " ")
                index += 1
            else:
                raise json.JSONDecodeError("Unterminated block comment", content, max(len(content) - 1, 0))
            continue

        result.append(char)
        index += 1

    return "".join(result)


def _strip_trailing_commas(content: str) -> str:
    result = list(content)
    index = 0
    in_string = False
    escaped = False

    while index < len(result):
        char = result[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue

        if char == '"':
            in_string = True
            index += 1
            continue

        if char == ",":
            lookahead = index + 1
            while lookahead < len(result) and result[lookahead].isspace():
                lookahead += 1
            if lookahead < len(result) and result[lookahead] in "}]":
                result[index] = " "
        index += 1

    return "".join(result)


def loads_jsonc(content: str) -> Any:
    """Parse JSON or JSONC while preserving string content exactly."""

    if content.startswith("\ufeff"):
        content = content[1:]
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return json.loads(_strip_trailing_commas(_strip_comments(content)))
