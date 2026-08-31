from __future__ import annotations

import re
from typing import Pattern


def balanced_paren_end(content: str, open_idx: int) -> int:
    depth = 0
    quote: str | None = None
    escaped = False
    for pos in range(open_idx, len(content)):
        char = content[pos]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in ("'", '"', "`"):
            quote = char
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return pos + 1
    return open_idx


def balanced_brace_block(content: str, start: int) -> str:
    open_idx = content.find("{", start)
    if open_idx < 0:
        return ""
    depth = 0
    quote: str | None = None
    escaped = False
    for pos in range(open_idx, len(content)):
        char = content[pos]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in ("'", '"', "`"):
            quote = char
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return content[open_idx + 1 : pos]
    return ""


def call_snippets(content: str, pattern: str) -> list[str]:
    snippets: list[str] = []
    for match in re.finditer(pattern, content):
        open_paren = match.end() - 1
        depth = 0
        quote: str | None = None
        escaped = False
        for pos in range(open_paren, len(content)):
            char = content[pos]
            if quote:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote:
                    quote = None
                continue
            if char in ("'", '"', "`"):
                quote = char
                continue
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    end = pos + 1
                    if end < len(content) and content[end] == ";":
                        end += 1
                    snippets.append(content[match.start() : end])
                    break
    return snippets


def component_body_snippets(content: str, component_re: Pattern[str], arrow_component_re: Pattern[str]) -> list[str]:
    bodies: list[str] = []
    for match in component_re.finditer(content):
        body_start = balanced_paren_end(content, match.end() - 1)
        body = balanced_brace_block(content, body_start)
        if body:
            bodies.append(body)
    for match in arrow_component_re.finditer(content):
        body = balanced_brace_block(content, match.end())
        if body:
            bodies.append(body)
    return bodies
