"""Disabled selection over supplied immutable bytes; no filesystem or execution API.

Receipt bytes are canonical metadata, not source-origin attestation or read authority.
The projector only builds the existing text-window request; it never invokes it.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
from tools.core.artifact_validator import ArtifactValidationError, _validate
from tools.core.json_syntax import loads_json_strict

PROFILE = "private_compiler_text_selection"
MAX_SAFE = 2**53 - 1
_DENIED_FLAGS = ("filesystem_capture_attested", "source_freshness_verified", "read_authority_granted", "enclosing_source_syntax_verified",
                 "actual_callsite_context_verified", "global_byte_line_mapping_verified", "actual_export_set_complete", "compiler_loaded",
                 "native_execution_attested", "input_code_evaluated", "production_enabled", "semantic_lineage", "cache_eligible", "publication_authority")


class TextSelectionRefusal(ValueError):
    def __init__(self, code: str, stage: str):
        super().__init__(code)
        self.code, self.stage = code, stage


def _need(condition, code, stage):
    if not condition:
        raise TextSelectionRefusal(code, stage)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _hash(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _hex(value):
    return type(value) is str and len(value) == 64 and re.fullmatch("[a-f0-9]{64}", value) is not None


def _finite_time(value):
    return type(value) in (int, float) and -MAX_SAFE <= value <= MAX_SAFE and math.isfinite(value)


def _plain(value, limit, stage, seen=None, depth=0, budget=None):
    seen = set() if seen is None else seen
    budget = [0, 0] if budget is None else budget
    budget[0] += 1
    _need(depth <= 32 and budget[0] <= limit, "selection_metadata_invalid", stage)
    kind = type(value)
    if value is None or kind in (bool, int, float, str):
        _need(kind is not int or abs(value) <= MAX_SAFE, "selection_metadata_invalid", stage)
        _need(kind is not float or math.isfinite(value), "selection_metadata_invalid", stage)
        try:
            budget[1] += len(value.encode("utf-8")) if kind is str else 8
        except UnicodeEncodeError as error:
            raise TextSelectionRefusal("selection_metadata_invalid", stage) from error
        _need(budget[1] <= limit, "selection_metadata_invalid", stage)
        return value
    _need(kind in (dict, list) and id(value) not in seen, "selection_metadata_invalid", stage)
    _need(len(value) <= limit - budget[0], "selection_metadata_invalid", stage)
    seen.add(id(value))
    try:
        if kind is list:
            return [_plain(item, limit, stage, seen, depth + 1, budget) for item in value]
        _need(all(type(key) is str for key in value), "selection_metadata_invalid", stage)
        result = {}
        for key, item in value.items():
            _plain(key, limit, stage, seen, depth + 1, budget)
            result[key] = _plain(item, limit, stage, seen, depth + 1, budget)
        return result
    finally:
        seen.remove(id(value))


def _checked(value, definition, schema, code, stage):
    errors = []
    try:
        _validate(value, schema["$defs"][definition], schema, ["$"], errors)
    except (KeyError, TypeError, RecursionError, ArtifactValidationError) as error:
        raise TextSelectionRefusal("selection_policy_invalid", stage) from error
    _need(not errors, code, stage)


def _schema_supported(schema, stage):
    """Reject ignored vocabulary locally; do not alter the shared validator."""
    allowed = {"$schema", "$id", "$defs", "$ref", "title", "description", "type", "const", "enum", "minimum",
               "pattern", "minLength", "properties", "required", "additionalProperties", "items", "minItems", "maxItems", "uniqueItems"}
    _need(type(schema) is dict and set(schema) <= allowed, "selection_policy_invalid", stage)
    for key in ("$defs", "properties"):
        if key in schema:
            _need(type(schema[key]) is dict, "selection_policy_invalid", stage)
            for child in schema[key].values():
                _schema_supported(child, stage)
    for key in ("items", "additionalProperties"):
        if type(schema.get(key)) is dict:
            _schema_supported(schema[key], stage)


class _Clock:
    def __init__(self, now, deadline):
        self.now = now
        self.started = self.last = now()
        _need(_finite_time(self.started), "selection_deadline", "start")
        self.end = deadline
        if deadline is not None:
            _need(_finite_time(deadline) and self.started < deadline, "selection_deadline", "start")

    def bind(self, milliseconds):
        ceiling = self.started + milliseconds / 1000
        if self.end is None:
            self.end = ceiling
        _need(self.end <= ceiling, "selection_deadline", "policy")
        self.check("policy")

    def check(self, stage):
        current = self.now()
        _need(_finite_time(current) and self.last <= current < self.end, "selection_deadline", stage)
        self.last = current


def _context(profiles, schema, clock):
    _need(type(profiles) is dict and type(profiles.get(PROFILE)) is dict, "selection_policy_invalid", "policy")
    _need(type(profiles.get("private_input_image_experiment")) is dict, "selection_policy_invalid", "policy")
    image_input = profiles["private_input_image_experiment"]
    _need(type(image_input.get("limits")) is dict, "selection_policy_invalid", "policy")
    limit = image_input["limits"].get("metadata_bytes")
    _need(type(limit) is int and 0 < limit <= MAX_SAFE, "selection_policy_invalid", "policy")
    context = _plain({"descriptor": profiles[PROFILE], "capture": profiles.get("compiler_library_inputs"), "image": image_input, "schema": schema}, limit, "policy")
    d, capture, image, schema = (context[key] for key in ("descriptor", "capture", "image", "schema"))
    _need(all(type(value) is dict for value in (capture, image, schema)), "selection_policy_invalid", "policy")
    _schema_supported(schema, "policy")
    _checked(d, "policy", schema, "selection_policy_invalid", "policy")
    _need(d["version"] == "v1-private-supplied-text-selection" and d["model"] == "immutable_supplied_utf8_line_interval_v1"
          and d["text_policy"] == "strict_utf8_preserve_bom_shebang_crlf_v1" and d["line_scheme"] == "crlf_lf_cr_full_terminators_v1"
          and d["method"] == "reviewed_exact_text_interval_v1" and d["input_kind"] == "supplied_immutable_bytes_v1"
          and d["receipt_status"] == "EXACT_CONTENT_SELECTION_RECORDED_NOT_FILE_ATTESTATION", "selection_policy_invalid", "policy")
    _need(all(d[key] is False for key in ("default_enabled", "native_execution_enabled", "production_enabled", "semantic_lineage", "cache_eligible")), "selection_policy_invalid", "policy")
    _need(d["schema_sha256"] == _hash(schema) and d["capture_policy_sha256"] == _hash(capture) and d["image_policy_sha256"] == _hash(image), "selection_policy_invalid", "policy")
    limits = d["limits"]
    _need(all(type(n) is int and 0 < n <= MAX_SAFE for n in limits.values()), "selection_policy_invalid", "policy")
    _need(limits["source_utf8_bytes"] <= capture["max_module_bytes"] and limits["source_utf16_units"] <= capture["max_module_bytes"]
          and limits["scan_work_units"] <= 2 * capture["max_module_bytes"] and limits["metadata_bytes"] <= limit
          and limits["receipt_bytes"] <= limit and limits["projection_bytes"] <= limit and limits["image_bytes"] <= image["limits"]["image_bytes"]
          and limits["operation_ms"] <= image["limits"]["capture_ms"] and limits["check_interval"] <= limits["source_utf16_units"], "selection_policy_invalid", "policy")
    _need(len(_canonical(context)) <= limits["metadata_bytes"], "selection_budget", "policy")
    clock.bind(limits["operation_ms"])
    return context


def _walk(text, start_line, end_line, limits, clock):
    line, units, byte_at, index, work = 1, 0, 0, 0, 0
    start = (0, 0) if start_line == 1 else None
    end, next_check = None, limits["check_interval"]
    while index < len(text):
        cp = ord(text[index]); step = 2 if cp > 0xFFFF else 1
        units += step; byte_at += 1 if cp < 128 else 2 if cp < 2048 else 3 if cp < 65536 else 4
        work += step; index += 1
        if cp == 13:
            work += 1
            if index < len(text) and text[index] == "\n":
                index += 1; units += 1; byte_at += 1; work += 1
        if cp in (10, 13):
            line += 1
            if line == start_line:
                start = units, byte_at
            if line == end_line + 1:
                end = units, byte_at
        _need(units <= limits["source_utf16_units"] and work <= limits["scan_work_units"], "selection_budget", "walk")
        if units >= next_check:
            clock.check("walk"); next_check = units + limits["check_interval"]
    if end_line == line:
        end = units, byte_at
    _need(start is not None and end is not None and end_line <= line and start[0] < end[0], "selection_interval_invalid", "walk")
    clock.check("walk")
    return start, end, units, byte_at, work


def produce_selection_receipt(raw, expected, profiles, schema, *, now=time.monotonic, deadline=None):
    """Return immutable canonical receipt bytes for supplied bytes, never a file claim."""
    clock = _Clock(now, deadline)
    context = _context(profiles, schema, clock); d = context["descriptor"]; limits = d["limits"]; schema = context["schema"]
    expected = _plain(expected, limits["metadata_bytes"], "expected")
    _checked(expected, "producer_expected", schema, "selection_expected_invalid", "expected")
    _need(type(raw) is bytes, "selection_input_invalid", "input")
    _need(len(raw) == expected["source_utf8_bytes"] and _hex(expected["source_raw_sha256"]) and _hex(expected["origin_ref_sha256"]), "selection_identity", "input")
    _need(type(expected["start_line"]) is int and type(expected["end_line"]) is int and 1 <= expected["start_line"] <= expected["end_line"], "selection_interval_invalid", "expected")
    metadata = 2 * len(_canonical(context)) + len(_canonical(expected))
    reserved = 3 * len(raw) + metadata + limits["view_utf8_bytes"] + limits["cursor_bytes"] + limits["receipt_bytes"]
    _need(len(raw) <= limits["source_utf8_bytes"] and metadata <= limits["metadata_bytes"] and reserved <= limits["image_bytes"], "selection_budget", "input_budget")
    text = None
    try:
        clock.check("identity"); digest = hashlib.sha256(raw).hexdigest()
        _need(digest == expected["source_raw_sha256"], "selection_identity", "identity")
        try:
            text = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise TextSelectionRefusal("selection_encoding_refused", "decode") from error
        clock.check("decode")
        start, end, units, byte_count, work = _walk(text, expected["start_line"], expected["end_line"], limits, clock)
        _need(byte_count == len(raw), "selection_identity", "walk")
        view_units, view_bytes = end[0] - start[0], end[1] - start[1]
        _need(view_units <= limits["view_utf16_units"] and view_bytes <= limits["view_utf8_bytes"], "selection_budget", "view_budget")
        with memoryview(raw) as view:
            view_digest = hashlib.sha256(view[start[1]:end[1]]).hexdigest()
        clock.check("view")
        content = {"source_raw_sha256": digest, "source_text_sha256": digest, "source_utf8_bytes": len(raw), "source_code_units": units,
                   "text_policy": d["text_policy"], "line_scheme": d["line_scheme"], "method": d["method"], "start_line": expected["start_line"], "end_line": expected["end_line"],
                   "start_utf16": start[0], "end_utf16": end[0], "view_code_units": view_units, "view_utf8_bytes": view_bytes, "view_text_sha256": view_digest}
        receipt = {"version": d["version"], "status": d["receipt_status"], "producer_policy_sha256": _hash(d), "producer_schema_sha256": _hash(schema),
                   "content": content, "content_sha256": _hash(content), "input_kind": d["input_kind"], "origin_ref_sha256": expected["origin_ref_sha256"],
                   "cost": {"input_bytes": len(raw), "decoded_text_reserved_bytes": 2 * len(raw), "measured_source_code_units": units,
                            "metadata_reserved_bytes": metadata, "view_reserved_bytes": limits["view_utf8_bytes"], "cursor_reserved_bytes": limits["cursor_bytes"],
                            "receipt_reserved_bytes": limits["receipt_bytes"], "known_inputs_reserved_bytes": reserved, "scan_work_units": work, "view_bytes_hashed": view_bytes},
                   "flags": {key: False for key in _DENIED_FLAGS}}
        _checked(receipt, "receipt", schema, "selection_receipt_invalid", "result")
        result = _canonical(receipt)
        _need(len(result) <= limits["receipt_bytes"], "selection_budget", "result")
        clock.check("result")
        return result
    finally:
        text = raw = None


def project_text_window_request(receipt_raw, receipt_sha256, entry_expected, authority, profiles, schemas, *, now=time.monotonic, deadline=None):
    """Validate pinned receipt bytes and build data for an independently authorized reader."""
    clock = _Clock(now, deadline)
    _need(type(schemas) is dict and "selection" in schemas, "selection_policy_invalid", "policy")
    context = _context(profiles, schemas["selection"], clock); d = context["descriptor"]; limits = d["limits"]; schema = context["schema"]
    _need(type(receipt_raw) is bytes and len(receipt_raw) <= limits["receipt_bytes"] and _hex(receipt_sha256), "selection_receipt_invalid", "receipt")
    _need(hashlib.sha256(receipt_raw).hexdigest() == receipt_sha256, "selection_receipt_identity", "receipt")
    try:
        receipt = loads_json_strict(receipt_raw.decode("utf-8"))
    except (ValueError, UnicodeError) as error:
        raise TextSelectionRefusal("selection_receipt_invalid", "receipt") from error
    receipt = _plain(receipt, limits["metadata_bytes"], "receipt")
    _need(_canonical(receipt) == receipt_raw, "selection_receipt_invalid", "receipt")
    _checked(receipt, "receipt", schema, "selection_receipt_invalid", "receipt")
    _need(receipt["producer_policy_sha256"] == _hash(d) and receipt["producer_schema_sha256"] == _hash(schema)
          and receipt["content_sha256"] == _hash(receipt["content"]) and set(receipt["flags"]) == set(_DENIED_FLAGS)
          and all(value is False for value in receipt["flags"].values()), "selection_receipt_identity", "receipt")
    c = receipt["content"]
    _need(all(_hex(c[key]) for key in ("source_raw_sha256", "source_text_sha256", "view_text_sha256")), "selection_receipt_invalid", "receipt")
    _need(all(type(c[key]) is int for key in ("source_utf8_bytes", "source_code_units", "start_line", "end_line", "start_utf16", "end_utf16", "view_code_units", "view_utf8_bytes")), "selection_receipt_invalid", "receipt")
    _need(c["source_raw_sha256"] == c["source_text_sha256"] and 0 <= c["start_utf16"] < c["end_utf16"] <= c["source_code_units"] <= c["source_utf8_bytes"]
          and c["view_code_units"] == c["end_utf16"] - c["start_utf16"] and 0 < c["view_utf8_bytes"] <= min(c["source_utf8_bytes"], 4 * c["view_code_units"])
          and 1 <= c["start_line"] <= c["end_line"], "selection_receipt_invalid", "receipt")
    data = _plain({"entry_expected": entry_expected, "authority": authority, "entry_profile": profiles.get("private_compiler_text_owner"),
                   "window_profile": profiles.get("private_compiler_text_window"), "schemas": schemas}, limits["metadata_bytes"], "projection")
    _need(len(_canonical(data)) + 2 * len(receipt_raw) <= limits["metadata_bytes"], "selection_budget", "projection")
    entry_expected, authority = data["entry_expected"], data["authority"]
    owner, window, schemas = data["entry_profile"], data["window_profile"], data["schemas"]
    _need(set(schemas) == {"selection", "entry", "text_window"}, "selection_policy_invalid", "projection")
    _schema_supported(schemas["entry"], "projection"); _schema_supported(schemas["text_window"], "projection")
    _checked(owner, "policy", schemas["entry"], "selection_policy_invalid", "projection")
    _checked(window, "policy", schemas["text_window"], "selection_policy_invalid", "projection")
    _checked(entry_expected, "expected", schemas["entry"], "selection_expected_invalid", "projection")
    _need(type(authority) is dict and all(_hex(authority.get(key)) for key in ("principal_sha256", "authority_ref_sha256", "root_sha256", "entry_context_sha256"))
          and all(_hex(value) for key, value in entry_expected.items() if key.endswith("_sha256")), "selection_expected_invalid", "projection")
    _need(owner["model"] == "single_declared_compiler_entry_text_only_v1" and window["model"] == "restricted_declared_entry_text_candidate_window_v1"
          and all(profile[key] is False for profile in (owner, window) for key in ("default_enabled", "native_execution_enabled", "production_enabled", "semantic_lineage", "cache_eligible")), "selection_policy_invalid", "projection")
    _need(owner["schema_sha256"] == _hash(schemas["entry"]) and window["schema_sha256"] == _hash(schemas["text_window"])
          and window["entry_policy_sha256"] == _hash(owner) and window["entry_schema_sha256"] == owner["schema_sha256"]
          and entry_expected["policy_sha256"] == _hash(owner) and entry_expected["schema_sha256"] == owner["schema_sha256"]
          and entry_expected["capture_policy_sha256"] == window["capture_policy_sha256"] == _hash(context["capture"])
          and entry_expected["image_policy_sha256"] == window["image_policy_sha256"] == _hash(context["image"]), "selection_identity", "projection")
    _need(entry_expected["entry_sha256"] == entry_expected["text_sha256"] == c["source_raw_sha256"] and entry_expected["entry_bytes"] == c["source_utf8_bytes"], "selection_identity", "projection")
    entry = {key: entry_expected[key] for key in ("entry_sha256", "text_sha256", "policy_sha256", "schema_sha256")}
    entry["context_sha256"] = _hash(entry_expected)
    _need(authority.get("entry_context_sha256") == entry["context_sha256"], "selection_authority_mismatch", "projection")
    selection = {key: c[key] for key in ("source_code_units", "start_utf16", "end_utf16", "view_code_units", "view_utf8_bytes", "view_text_sha256")}
    provenance = {**selection, "entry_context_sha256": entry["context_sha256"], "method": d["method"], "receipt_sha256": receipt_sha256}
    selection["provenance_sha256"] = _hash(provenance)
    result = {"entry_expected": entry_expected, "entry": entry, "authority": authority, "selection": selection, "selection_provenance": provenance,
              "observer_policy_sha256": _hash(window), "observer_schema_sha256": _hash(schemas["text_window"]),
              "selection_schema_sha256": _hash(schemas["text_window"]["$defs"]["selection"]), "selection_sha256": _hash(selection),
              "authority_sha256": _hash(authority), "selection_provenance_sha256": _hash(provenance),
              **{key + "_sha256": _hash(window[key]) for key in ("grammar", "span_scheme", "privacy", "limits", "consumer", "claim_scope", "callee", "whole_limits", "selection_policy")}}
    _checked(result, "expected", schemas["text_window"], "selection_expected_invalid", "result")
    w, capture = window["whole_limits"], context["capture"]
    _need(c["source_code_units"] <= w["source_code_units"] and c["source_utf8_bytes"] <= w["verification_utf8_bytes"]
          and c["source_code_units"] + c["source_utf8_bytes"] <= w["verification_work_units"] and c["view_code_units"] <= window["limits"]["scan_code_units"]
          and 4 + 6 * c["view_code_units"] <= w["selection_work_units"], "selection_budget", "result")
    _need(authority["max_open_attempts"] == 5 and c["source_utf8_bytes"] <= authority["max_entry_bytes"] <= capture["max_module_bytes"]
          and c["source_utf8_bytes"] <= authority["max_total_bytes"] <= capture["max_total_bytes"], "selection_authority_mismatch", "result")
    _need(len(_canonical(result)) <= limits["projection_bytes"], "selection_budget", "result")
    clock.check("result")
    return result
