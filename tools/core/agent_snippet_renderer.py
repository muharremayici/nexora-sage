from __future__ import annotations

import json
from typing import Any


def render_target_source_snippets(
    snippets: list[dict[str, Any]],
    *,
    indent: str = "  ",
    item_indent: str = "    ",
    max_items: int = 3,
) -> list[str]:
    """Render target source snippets for agent-facing YAML packets."""
    lines: list[str] = []
    lines.append(f"{indent}target_source_snippets:")
    if not snippets:
        lines.append(f"{item_indent}[]")
        return lines
    for snippet in snippets[:max_items]:
        if not isinstance(snippet, dict):
            continue
        lines.extend(_render_snippet(snippet, item_indent=item_indent))
    return lines


def _render_snippet(snippet: dict[str, Any], *, item_indent: str) -> list[str]:
    field_indent = item_indent + "  "
    lines = [
        f"{item_indent}- symbol: " + json.dumps(snippet.get("symbol") or "", ensure_ascii=False),
        f"{field_indent}source_lines: " + json.dumps(snippet.get("source_lines") or "", ensure_ascii=False),
        f"{field_indent}snippet_status: "
        + json.dumps(snippet.get("snippet_status") or "not_available", ensure_ascii=False),
    ]
    _append_int(lines, field_indent, "line_count", snippet.get("line_count"))
    _append_int(lines, field_indent, "max_lines", snippet.get("max_lines"))
    _append_int(lines, field_indent, "shown_lines", snippet.get("shown_lines"))
    _append_int(lines, field_indent, "omitted_lines", snippet.get("omitted_lines"))
    _append_str(lines, field_indent, "omitted_range", snippet.get("omitted_range"))
    _append_str(lines, field_indent, "next_chunk_lines", snippet.get("next_chunk_lines"))
    if snippet.get("one_shot_edit_ready") is not None:
        lines.append(f"{field_indent}one_shot_edit_ready: " + str(bool(snippet.get("one_shot_edit_ready"))).lower())
    _append_str(lines, field_indent, "snippet_role", snippet.get("snippet_role"))
    _append_str(lines, field_indent, "snippet_strategy", snippet.get("snippet_strategy"))
    _append_str(lines, field_indent, "snippet_purpose", snippet.get("snippet_purpose"))
    _append_str(lines, field_indent, "omitted_context_policy", snippet.get("omitted_context_policy"))
    _append_str(lines, field_indent, "edit_scope", snippet.get("edit_scope"))
    if snippet.get("follow_up_if_needed"):
        _append_str(lines, field_indent, "follow_up_if_needed", snippet.get("follow_up_if_needed"))
    elif snippet.get("next_action"):
        _append_str(lines, field_indent, "next_action", snippet.get("next_action"))
    code = snippet.get("code")
    if code:
        lines.append(f"{field_indent}code: |-")
        for code_line in str(code).splitlines():
            lines.append(field_indent + "  " + code_line)
    return lines


def _append_int(lines: list[str], indent: str, key: str, value: Any) -> None:
    if value is not None:
        lines.append(f"{indent}{key}: {int(value or 0)}")


def _append_str(lines: list[str], indent: str, key: str, value: Any) -> None:
    if value:
        lines.append(f"{indent}{key}: " + json.dumps(value, ensure_ascii=False))
