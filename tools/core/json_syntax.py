"""Strict JSON syntax without configuration, telemetry or storage dependencies."""
from __future__ import annotations

import json
import io
import re
from typing import Any


class DuplicateJSONKeyError(ValueError):
    """Raised when JSON contains two members with the same object key."""


def _unique_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJSONKeyError(f"Duplicate JSON object key: {key}")
        result[key] = value
    return result


def loads_json_strict(content: str) -> Any:
    """Parse JSON without allowing last-key-wins semantic ambiguity."""
    return json.loads(content, object_pairs_hook=_unique_object_pairs)


class JSONSelectionLimitError(ValueError):
    """Selected value or an identity token exceeded the caller-owned budget."""


class _JSONStreamPathSelector:
    """Validate a JSON stream while retaining only one exact object-member path.

    Unselected strings are scanned in blocks, not collected. Duplicate keys on
    the selected lineage are ambiguous; the selected value uses the strict
    object decoder. No complete object or unselected array is materialized.
    """

    _string_special = re.compile(r'["\\\x00-\x1f]')
    _scalar_end = re.compile(r'[,\]} \t\r\n]')
    _number = re.compile(r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\Z')

    def __init__(self, stream, chunk_size: int, max_value_bytes: int):
        self.stream, self.chunk_size, self.limit = stream, chunk_size, max_value_bytes
        self.buffer, self.offset = "", 0
        self.capture = None
        self.capture_bytes = 0
        self.selected = None

    def _peek(self):
        if self.offset == len(self.buffer):
            self.buffer = self.stream.read(self.chunk_size)
            self.offset = 0
        return self.buffer[self.offset:self.offset + 1]

    def _take(self, size):
        piece = self.buffer[self.offset:self.offset + size]
        self.offset += len(piece)
        if self.capture is not None:
            self.capture_bytes += len(piece.encode("utf-8"))
            if self.capture_bytes > self.limit:
                raise JSONSelectionLimitError("Selected JSON value exceeds the storage record budget.")
            self.capture.write(piece)
        return piece

    def _exact(self, size):
        parts = []
        while size:
            if not self._peek():
                raise ValueError("JSON ended inside a token.")
            piece = self._take(min(size, len(self.buffer) - self.offset))
            parts.append(piece)
            size -= len(piece)
        return "".join(parts)

    def _space(self):
        while self._peek() and self._peek() in " \t\r\n":
            match = re.search(r'[^ \t\r\n]', self.buffer[self.offset:])
            self._take(match.start() if match else len(self.buffer) - self.offset)

    def _expect(self, token):
        if self._exact(len(token)) != token:
            raise ValueError("Invalid JSON punctuation or literal.")

    def _string(self, keep=False):
        parts, size = [], 0
        def accept(piece):
            nonlocal size
            if keep:
                size += len(piece.encode("utf-8"))
                if size > self.limit:
                    raise JSONSelectionLimitError("JSON identity token exceeds the storage record budget.")
                parts.append(piece)
        self._expect('"')
        accept('"')
        while True:
            if not self._peek():
                raise ValueError("JSON ended inside a string.")
            match = self._string_special.search(self.buffer, self.offset)
            accept(self._take((match.start() if match else len(self.buffer)) - self.offset))
            if match is None:
                continue
            marker = self._exact(1)
            accept(marker)
            if marker == '"':
                return json.loads("".join(parts)) if keep else None
            if marker != "\\":
                raise ValueError("Unescaped JSON control character.")
            escape = self._exact(1)
            accept(escape)
            if escape == "u":
                digits = self._exact(4)
                if any(char not in "0123456789abcdefABCDEF" for char in digits):
                    raise ValueError("Invalid JSON Unicode escape.")
                accept(digits)
            elif escape not in '"\\/bfnrt':
                raise ValueError("Invalid JSON string escape.")

    def _value(self, path):
        self._space()
        if path == ():
            self.capture, self.capture_bytes = io.StringIO(), 0
            try:
                self._value(None)
                self.selected = self.capture.getvalue()
            finally:
                self.capture.close()
                self.capture = None
            return
        marker = self._peek()
        if marker == "{":
            self._take(1)
            self._space()
            if self._peek() == "}":
                self._take(1)
                return
            matched = False
            while True:
                self._space()
                key = self._string(keep=bool(path))
                self._space()
                self._expect(":")
                selected = bool(path) and key == path[0]
                if selected and matched:
                    raise DuplicateJSONKeyError("Duplicate member on the selected JSON path.")
                matched = matched or selected
                self._value(path[1:] if selected else None)
                self._space()
                separator = self._exact(1)
                if separator == "}":
                    return
                if separator != ",":
                    raise ValueError("Invalid JSON object separator.")
        elif marker == "[":
            self._take(1)
            self._space()
            if self._peek() == "]":
                self._take(1)
                return
            while True:
                self._value(None)
                self._space()
                separator = self._exact(1)
                if separator == "]":
                    return
                if separator != ",":
                    raise ValueError("Invalid JSON array separator.")
        elif marker == '"':
            self._string()
        elif marker in ("t", "f", "n"):
            self._expect({"t": "true", "f": "false", "n": "null"}[marker])
        elif marker and marker in "-0123456789":
            parts, size = [], 0
            while self._peek():
                match = self._scalar_end.search(self.buffer, self.offset)
                piece = self._take((match.start() if match else len(self.buffer)) - self.offset)
                size += len(piece)
                if size > self.limit:
                    raise JSONSelectionLimitError("JSON numeric token exceeds the storage record budget.")
                parts.append(piece)
                if match:
                    break
            if not self._number.fullmatch("".join(parts)):
                raise ValueError("Invalid JSON number.")
        else:
            raise ValueError("Invalid JSON value.")


def select_json_object_path(stream, path: tuple[str, ...], *, chunk_size: int, max_value_bytes: int) -> Any:
    """Return one member value after validating the entire stream, or None.

    The caller owns chunk/record budgets and stream identity. A matching value
    is never returned early: invalid tails and duplicate selected members fail.
    Unselected duplicate keys are not assessed by this path-specific reader.
    """
    if not path or any(not isinstance(key, str) for key in path):
        raise ValueError("An exact non-empty JSON object-member path is required.")
    if chunk_size < 1 or max_value_bytes < 1:
        raise ValueError("JSON selection budgets must be positive.")
    selector = _JSONStreamPathSelector(stream, chunk_size, max_value_bytes)
    selector._value(path)
    selector._space()
    if selector._peek():
        raise ValueError("Trailing data after the JSON document.")
    return loads_json_strict(selector.selected) if selector.selected is not None else None
