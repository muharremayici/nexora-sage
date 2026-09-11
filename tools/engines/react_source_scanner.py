from __future__ import annotations

import re
from typing import Pattern


def mask_js_comments_and_strings(content: str, *, mask_jsx_text: bool = False) -> str:
    """Preserve offsets while masking non-code text without hiding JSX text."""
    masked = list(content)
    state = "code"
    resume_state = "code"
    escaped = False
    jsx_depth = 0
    jsx_tag_closing = False
    jsx_expression_depth = 0
    jsx_expression_return: str | None = None
    index = 0
    while index < len(content):
        char = content[index]
        next_char = content[index + 1] if index + 1 < len(content) else ""
        if state == "code":
            if jsx_expression_return is not None:
                if char == "{":
                    jsx_expression_depth += 1
                elif char == "}":
                    jsx_expression_depth -= 1
                    if jsx_expression_depth == 0:
                        state = jsx_expression_return
                        jsx_expression_return = None
                        index += 1
                        continue
            if char == "/" and next_char == "/":
                masked[index] = masked[index + 1] = " "
                resume_state = state
                state = "line_comment"
                index += 2
                continue
            if char == "/" and next_char == "*":
                masked[index] = masked[index + 1] = " "
                resume_state = state
                state = "block_comment"
                index += 2
                continue
            if char in {"'", '"', "`"}:
                masked[index] = " "
                resume_state = state
                state = {"'": "single_quote", '"': "double_quote", "`": "template"}[char]
                escaped = False
            elif char == "<" and (next_char.isalpha() or next_char in {"/", ">"}):
                jsx_tag_closing = next_char == "/"
                state = "jsx_tag"
        elif state == "jsx_text":
            if char == "<" and (next_char.isalpha() or next_char in {"/", ">"}):
                jsx_tag_closing = next_char == "/"
                state = "jsx_tag"
            elif char == "{":
                jsx_expression_depth = 1
                jsx_expression_return = "jsx_text"
                state = "code"
            elif mask_jsx_text and char != "\n":
                masked[index] = " "
        elif state == "jsx_tag":
            if char in {"'", '"'}:
                masked[index] = " "
                resume_state = state
                state = "single_quote" if char == "'" else "double_quote"
                escaped = False
            elif char == "{":
                jsx_expression_depth = 1
                jsx_expression_return = "jsx_tag"
                state = "code"
            elif char == ">":
                previous = index - 1
                while previous >= 0 and content[previous].isspace():
                    previous -= 1
                self_closing = previous >= 0 and content[previous] == "/"
                if jsx_tag_closing:
                    jsx_depth = max(0, jsx_depth - 1)
                elif not self_closing:
                    jsx_depth += 1
                state = "jsx_text" if jsx_depth else "code"
        elif state == "line_comment":
            if char == "\n":
                state = resume_state
            else:
                masked[index] = " "
        elif state == "block_comment":
            if char == "*" and next_char == "/":
                masked[index] = masked[index + 1] = " "
                state = resume_state
                index += 2
                continue
            if char != "\n":
                masked[index] = " "
        else:
            delimiter = {"single_quote": "'", "double_quote": '"', "template": "`"}[state]
            if char != "\n":
                masked[index] = " "
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == delimiter:
                state = resume_state
        index += 1
    return "".join(masked)


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
    code = mask_js_comments_and_strings(content)
    open_idx = code.find("{", start)
    if open_idx < 0:
        return ""
    depth = 0
    for pos in range(open_idx, len(content)):
        char = code[pos]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return content[open_idx + 1 : pos]
    return ""


def call_snippets(content: str, pattern: str, *, executable_only: bool = False) -> list[str]:
    snippets: list[str] = []
    scan_content = mask_js_comments_and_strings(content, mask_jsx_text=True) if executable_only else content
    for match in re.finditer(pattern, scan_content):
        open_paren = match.end() - 1
        depth = 0
        quote: str | None = None
        escaped = False
        for pos in range(open_paren, len(scan_content)):
            char = scan_content[pos]
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
                    if end < len(scan_content) and scan_content[end] == ";":
                        end += 1
                    snippets.append(content[match.start() : end])
                    break
    return snippets


def jsx_opening_tag_snippets(content: str, tag_names: set[str]) -> list[str]:
    """Return complete JSX opening tags without stopping at arrows in expressions."""
    code = mask_js_comments_and_strings(content, mask_jsx_text=True)
    accepted = {name.lower() for name in tag_names}
    snippets: list[str] = []
    for match in re.finditer(r"<([A-Za-z][A-Za-z0-9_.$:-]*)\b", code):
        if match.group(1).lower() not in accepted:
            continue
        brace_depth = 0
        for pos in range(match.end(), len(code)):
            char = code[pos]
            if char == "{":
                brace_depth += 1
            elif char == "}" and brace_depth:
                brace_depth -= 1
            elif char == ">" and brace_depth == 0:
                snippets.append(content[match.start() : pos + 1])
                break
    return snippets


def jsx_attribute_names(opening_tag: str) -> set[str]:
    """Return top-level JSX attribute names, excluding identifiers in expressions."""
    code = mask_js_comments_and_strings(opening_tag, mask_jsx_text=True)
    tag = re.match(r"<([A-Za-z][A-Za-z0-9_.$:-]*)\b", code)
    if not tag:
        return set()
    names: set[str] = set()
    brace_depth = 0
    pos = tag.end()
    while pos < len(code):
        char = code[pos]
        if char == "{":
            brace_depth += 1
            pos += 1
            continue
        if char == "}" and brace_depth:
            brace_depth -= 1
            pos += 1
            continue
        if brace_depth == 0 and (char.isalpha() or char in {"_", ":"}):
            match = re.match(r"[A-Za-z_:][A-Za-z0-9_.:-]*", code[pos:])
            if match:
                names.add(match.group(0))
                pos += len(match.group(0))
                continue
        pos += 1
    return names


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
