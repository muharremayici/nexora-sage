"""Shared exact-text coordinates and LOC normalization; no storage or engine I/O."""
from bisect import bisect_left
import copy
import hashlib
import re
from typing import Dict

from tools.core.atlas_ast_contract import AST_CONTRACT_VERSION, loc_measurement_record_valid


def _utf16_offset_to_utf8_byte_offset(content: str, offset: int) -> int:
    """Translate a JavaScript/TypeScript parser offset into the byte contract."""

    if offset < 0:
        raise ValueError(f"negative UTF-16 offset: {offset}")
    utf16_units = 0
    utf8_bytes = 0
    for char in content:
        if utf16_units == offset:
            return utf8_bytes
        char_units = 2 if ord(char) > 0xFFFF else 1
        if utf16_units + char_units > offset:
            raise ValueError(f"UTF-16 offset splits a surrogate pair: {offset}")
        utf16_units += char_units
        utf8_bytes += len(char.encode("utf-8"))
    if utf16_units == offset:
        return utf8_bytes
    raise ValueError(
        f"UTF-16 offset exceeds source length: offset={offset}, units={utf16_units}"
    )


def _utf16_offsets_to_utf8_byte_offsets(content: str, offsets) -> Dict[int, int]:
    """Resolve many compiler offsets with one bounded pass over source content."""

    requested_set = set()
    for offset in offsets:
        try:
            normalized_offset = int(offset)
        except (TypeError, ValueError):
            continue
        if normalized_offset >= 0:
            requested_set.add(normalized_offset)
    requested = sorted(requested_set)
    if not requested:
        return {}
    if content.isascii():
        return {offset: offset for offset in requested if offset <= len(content)}

    resolved: Dict[int, int] = {}
    requested_index = 0
    utf16_units = 0
    utf8_bytes = 0
    for char in content:
        while requested_index < len(requested) and requested[requested_index] == utf16_units:
            resolved[requested[requested_index]] = utf8_bytes
            requested_index += 1

        char_units = 2 if ord(char) > 0xFFFF else 1
        next_utf16_units = utf16_units + char_units
        while (
            requested_index < len(requested)
            and utf16_units < requested[requested_index] < next_utf16_units
        ):
            # Keep invalid surrogate-splitting offsets unresolved so the canonical
            # single-offset validator raises its existing precise error on use.
            requested_index += 1
        utf16_units = next_utf16_units
        utf8_bytes += len(char.encode("utf-8"))

    while requested_index < len(requested) and requested[requested_index] == utf16_units:
        resolved[requested[requested_index]] = utf8_bytes
        requested_index += 1
    return resolved


def _normalize_loc_measurement_evidence(raw_symbol, content, offset_map=None, context=None, atlas_source_hash=None):
    """Validate actual parsed text and reuse the existing file-level coordinate map."""
    invalid = {"status": "unavailable", "reason": "loc_measurement_source_or_shape_invalid"}
    record = raw_symbol.get("loc_measurement_evidence")
    if context is None:
        context = (hashlib.sha256(content.encode("utf-8")).hexdigest(), _native_utf16_span_validator(content))
    identity, valid_span = context
    if (not isinstance(record, dict) or record.get("source_hash") != identity
            or not loc_measurement_record_valid(record, raw_symbol, offset_unit="utf16_code_units", valid_span=valid_span)):
        return invalid
    offsets = [span[key] for span in record["spans"].values() for key in ("start", "end")]
    if offset_map is None:
        offset_map = _utf16_offsets_to_utf8_byte_offsets(content, offsets)
    if any(offset not in offset_map for offset in offsets):
        return invalid
    normalized = copy.deepcopy(record)
    for span in normalized["spans"].values():
        span.update(start=offset_map[span["start"]], end=offset_map[span["end"]])
    normalized.update(offset_unit="utf8_bytes", ast_contract_version=AST_CONTRACT_VERSION,
        atlas_source_hash=atlas_source_hash or hashlib.md5(content.encode("utf-8")).hexdigest())
    return normalized


def _native_utf16_span_validator(content: str):
    """Validate parser spans before the existing batch byte-offset conversion."""
    encoded = content.encode("utf-16-le")
    length = len(encoded) // 2
    # ECMAScript line terminators; CRLF contributes one completed break.
    breaks = [index for index in range(length)
              if encoded[index * 2:index * 2 + 2] in {b"\n\x00", b"\r\x00", b"\x28\x20", b"\x29\x20"}
              and not (encoded[index * 2:index * 2 + 4] == b"\r\x00\n\x00")]

    def valid_span(span, expected_text=None, quoted=False):
        if (not isinstance(span, dict) or set(span) != {"start", "end", "line", "end_line"}
                or any(type(value) is not int for value in span.values())
                or not 0 <= span["start"] < span["end"] <= length):
            return False
        for offset in (span["start"], span["end"]):
            if offset < length and 0xDC00 <= int.from_bytes(encoded[offset * 2:offset * 2 + 2], "little") <= 0xDFFF:
                return False
        if (span["line"] != bisect_left(breaks, span["start"]) + 1
                or span["end_line"] != bisect_left(breaks, span["end"] - 1) + 1):
            return False
        if expected_text is not None:
            token = encoded[span["start"] * 2:span["end"] * 2].decode("utf-16-le")
            if quoted:
                # Finite literal path grammar: no JS escape interpretation here.
                if len(token) < 2 or token[0] not in {"'", '"'} or token[-1] != token[0] or "\\" in token:
                    return False
                token = token[1:-1]
            if token != expected_text:
                return False
        return True

    def trivia_between(start, end, expected):
        if type(start) is not int or type(end) is not int or not 0 <= start <= end <= length:
            return False
        fragment = encoded[start * 2:end * 2].decode("utf-16-le")
        return re.sub(r"/\*.*?\*/|//[^\r\n\u2028\u2029]*|[\s\ufeff]+", "", fragment, flags=re.S) == expected

    valid_span.trivia_between = trivia_between
    return valid_span


def native_loc_measurement_contexts(symbols, content):
    """Validate one actual buffer once, without importing an Atlas producer."""
    if not isinstance(content, str) or not any(isinstance(s, dict) and "loc_measurement_evidence" in s for s in symbols):
        return [None] * len(symbols)
    context = (hashlib.sha256(content.encode("utf-8")).hexdigest(), _native_utf16_span_validator(content))
    file_hash = hashlib.md5(content.encode("utf-8")).hexdigest()
    offsets = []
    for symbol in symbols:
        if not isinstance(symbol, dict): continue
        offsets.extend(symbol[key] for key in ("start", "end") if type(symbol.get(key)) is int)
        record = symbol.get("loc_measurement_evidence")
        spans = record.get("spans") if isinstance(record, dict) else None
        if isinstance(spans, dict) and set(spans) == {"declaration", "callable"}:
            for span in spans.values():
                if isinstance(span, dict):
                    offsets.extend(span[key] for key in ("start", "end") if type(span.get(key)) is int)
    offset_map = _utf16_offsets_to_utf8_byte_offsets(content, offsets)
    contexts = []
    for symbol in symbols:
        if (not isinstance(symbol, dict) or type(symbol.get("start")) is not int or type(symbol.get("end")) is not int
                or symbol["start"] not in offset_map or symbol["end"] not in offset_map):
            contexts.append(None)
            continue
        normalized = {"start": offset_map[symbol["start"]], "end": offset_map[symbol["end"]],
            "line": symbol.get("line"), "end_line": symbol.get("endLine", symbol.get("end_line")),
            "parser_kind": symbol.get("parserKind", symbol.get("parser_kind")),
            "parser_version": symbol.get("parserVersion", symbol.get("parser_version")),
            "loc_measurement_evidence": _normalize_loc_measurement_evidence(symbol, content, offset_map, context, file_hash)}
        contexts.append({"symbol": normalized, "source_hash": file_hash})
    return contexts
