"""Bounded, local XML 1.0 checks for an already produced output document.

This module never executes target code. Its PASS is limited to the supplied
document and explicit root/text/attribute expectations, not a source or release claim.
Actual DTD declarations are rejected; their spelling in literal data is allowed.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any
from xml.etree import ElementTree


MAX_XML_CHARACTERS = 2_000_000
MAX_VALUE_EXPECTATIONS = 32
CLAIM_BOUNDARY = (
    "Checks only the supplied XML document and explicit root/leaf-text/attribute expectations. "
    "It does not bind the document to a target source snapshot, prove all input cases, "
    "certify the full XML tree, or authorize an automatic source finding."
)
_DECLARATION_VERSION = re.compile(r'^<\?xml\s+version\s*=\s*["\']([^"\']+)["\']')
_DECLARATION_ENCODING = re.compile(r'\bencoding\s*=\s*["\']([^"\']+)["\']')


class _UnsupportedDtd(ValueError):
    """Abort an actual DTD declaration before processing its subset."""


class _DtdRejectingTreeBuilder(ElementTree.TreeBuilder):
    def doctype(self, name: str, pubid: str | None, system: str | None) -> None:
        # Parser events distinguish DTD syntax from comment/CDATA/PI data.
        raise _UnsupportedDtd


def _xml10_character_allowed(codepoint: int) -> bool:
    return (
        codepoint in {0x9, 0xA, 0xD}
        or 0x20 <= codepoint <= 0xD7FF
        or 0xE000 <= codepoint <= 0xFFFD
        or 0x10000 <= codepoint <= 0x10FFFF
    )


def _valid_expectations(expectations: Any) -> bool:
    if (not isinstance(expectations, dict)
            or not {"root_tag", "texts"} <= set(expectations)
            or not set(expectations) <= {"root_tag", "texts", "attributes"}):
        return False
    texts = expectations["texts"]
    attributes = expectations.get("attributes", [])
    if (not isinstance(expectations["root_tag"], str)
            or not expectations["root_tag"]
            or not isinstance(texts, list)
            or not isinstance(attributes, list)
            or not 1 <= len(texts) + len(attributes) <= MAX_VALUE_EXPECTATIONS):
        return False
    if len(expectations["root_tag"]) > MAX_XML_CHARACTERS:
        return False
    valid_texts = all(
        isinstance(row, dict)
        and set(row) == {"tag", "occurrence", "expected_text"}
        and isinstance(row["tag"], str)
        and bool(row["tag"])
        and len(row["tag"]) <= MAX_XML_CHARACTERS
        and type(row["occurrence"]) is int
        and row["occurrence"] >= 0
        and isinstance(row["expected_text"], str)
        and len(row["expected_text"]) <= MAX_XML_CHARACTERS
        for row in texts
    )
    valid_attributes = all(
        isinstance(row, dict)
        and set(row) == {"tag", "occurrence", "attribute", "expected_value"}
        and isinstance(row["tag"], str) and bool(row["tag"])
        and type(row["occurrence"]) is int and row["occurrence"] >= 0
        and isinstance(row["attribute"], str) and bool(row["attribute"])
        and isinstance(row["expected_value"], str)
        for row in attributes
    )
    return (
        valid_texts and valid_attributes
        and len(expectations["root_tag"])
        + sum(len(row["tag"]) + len(row["expected_text"]) for row in texts)
        + sum(
            len(row["tag"]) + len(row["attribute"]) + len(row["expected_value"])
            for row in attributes
        ) <= MAX_XML_CHARACTERS
    )


def evaluate_xml_output(xml_text: str, expectations: dict[str, Any] | None = None) -> dict[str, Any]:
    """Check XML 1.0 syntax and selected value round-trip without echoing content."""
    result: dict[str, Any] = {
        "status": "UNKNOWN",
        "xml10_characters": "unknown",
        "well_formed": None,
        "round_trip": "unverified",
        "checked_text_count": 0,
        "checked_attribute_count": 0,
        "input_sha256": None,
        "claim_boundary": CLAIM_BOUNDARY,
    }
    if not isinstance(xml_text, str) or len(xml_text) > MAX_XML_CHARACTERS:
        result["status"] = "INVALID_INPUT"
        return result
    try:
        result["input_sha256"] = hashlib.sha256(xml_text.encode("utf-8")).hexdigest()
    except UnicodeEncodeError as exc:
        # Lone surrogates have no valid UTF-8 document encoding.
        result.update(
            status="INVALID_XML10_CHARACTER", xml10_characters="invalid",
            invalid_character={"codepoint": f"U+{ord(xml_text[exc.start]):04X}", "offset": exc.start},
        )
        return result
    for offset, char in enumerate(xml_text):
        codepoint = ord(char)
        if not _xml10_character_allowed(codepoint):
            result.update(
                status="INVALID_XML10_CHARACTER",
                xml10_characters="invalid",
                invalid_character={"codepoint": f"U+{codepoint:04X}", "offset": offset},
            )
            return result
    result["xml10_characters"] = "valid"
    document = xml_text.removeprefix("\ufeff")
    version = _DECLARATION_VERSION.match(document)
    if version and version.group(1) != "1.0":
        result["status"] = "UNSUPPORTED_XML_VERSION"
        return result
    if version:
        declaration_end = document.find("?>")
        if declaration_end < 0:
            result.update(status="MALFORMED_XML", well_formed=False)
            return result
        encoding = _DECLARATION_ENCODING.search(document[:declaration_end])
        if encoding and encoding.group(1).lower() not in {"utf-8", "utf8"}:
            result["status"] = "UNSUPPORTED_ENCODING_DECLARATION"
            return result
    if expectations is not None and not _valid_expectations(expectations):
        result["status"] = "INVALID_EXPECTATIONS"
        return result
    try:
        parser = ElementTree.XMLParser(target=_DtdRejectingTreeBuilder())
        root = ElementTree.fromstring(document, parser=parser)
    except _UnsupportedDtd:
        result["status"] = "UNSUPPORTED_DTD"
        return result
    except (ElementTree.ParseError, ValueError):
        result.update(status="MALFORMED_XML", well_formed=False)
        return result
    result["well_formed"] = True
    if expectations is None:
        result["status"] = "NEEDS_EXPECTATIONS"
        return result
    if root.tag != expectations["root_tag"]:
        result.update(status="ROUND_TRIP_MISMATCH", round_trip="mismatch",
                      mismatch_kind="root_tag")
        return result
    for index, expected in enumerate(expectations["texts"]):
        matches = [node for node in root.iter(expected["tag"])]
        occurrence = expected["occurrence"]
        if occurrence >= len(matches):
            result.update(status="ROUND_TRIP_MISMATCH", round_trip="mismatch",
                          mismatch_kind="missing_text_node", expectation_index=index)
            return result
        node = matches[occurrence]
        if len(node) or (node.text or "") != expected["expected_text"]:
            result.update(status="ROUND_TRIP_MISMATCH", round_trip="mismatch",
                          mismatch_kind="text_or_structure", expectation_index=index)
            return result
        result["checked_text_count"] += 1
    for index, expected in enumerate(expectations.get("attributes", [])):
        matches = [node for node in root.iter(expected["tag"])]
        occurrence = expected["occurrence"]
        if occurrence >= len(matches):
            result.update(status="ROUND_TRIP_MISMATCH", round_trip="mismatch",
                          mismatch_kind="missing_attribute_node", expectation_index=index)
            return result
        if matches[occurrence].attrib.get(expected["attribute"]) != expected["expected_value"]:
            result.update(status="ROUND_TRIP_MISMATCH", round_trip="mismatch",
                          mismatch_kind="attribute_value", expectation_index=index)
            return result
        result["checked_attribute_count"] += 1
    result.update(status="PASS", round_trip="verified")
    return result
