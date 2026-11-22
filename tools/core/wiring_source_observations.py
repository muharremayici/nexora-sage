"""Admit exact native source observations, never an import-to-action intent join."""
from copy import deepcopy
import hashlib
import re

from tools.core.atlas_ast_contract import AST_CONTRACT_VERSION
from tools.core.source_coordinates import _native_utf16_span_validator, _utf16_offsets_to_utf8_byte_offsets, _utf8_span_validator

VERSION = "v1-named-import-inline-empty-jsx"
COVERAGE = "finite_named_import_and_inline_jsx_syntax"
BOUNDARIES = {"correlation_status": "not_evaluated", "integration_status": "unknown",
              "resolution_status": "not_evaluated", "runtime_execution": "not_established",
              "mutation_proposed": False}
IMPORT = "named_import_without_identifier_occurrence"
CALLBACK = "inline_empty_jsx_callback"
ROLES = {IMPORT: {"import_declaration", "import_binding", "imported_binding", "local_binding", "module_source"},
         CALLBACK: {"jsx_site", "tag", "attribute", "prop", "callback", "callback_body"}}


def normalize_wiring_source_observations(raw_results, content: str) -> dict:
    """Native admission only; no JS reparser, resolver, target execution or intent guess.

    Hash/span checks bind facts to the parsed text, not producer authenticity or a
    live target snapshot. Later consumers must still check primary/snapshot identity.
    A no-occurrence record does not establish that a module has no side effects,
    that a symbol is obsolete, or that dynamic/string-based consumption is absent.
    """
    base = {"version": VERSION, "coverage": COVERAGE, "status": "unavailable", "reason": "metadata_missing",
            "offset_unit": "utf8_bytes", "ast_contract_version": AST_CONTRACT_VERSION,
            "records": [], "record_count": 0, "omitted": 0, **BOUNDARIES}
    def reject(reason):
        return {**base, "reason": reason}
    metas = [row for row in raw_results if isinstance(row, dict) and row.get("name") == "__file_meta__"] if isinstance(raw_results, list) else []
    if len(metas) != 1 or metas[0].get("parserStatus") != "observed" or metas[0].get("parserKind") != "typescript_compiler_api":
        return reject("parser_not_uniquely_observed")
    meta = metas[0]
    evidence = meta.get("wiringSourceObservations")
    if not isinstance(evidence, dict):
        return base
    expected = {"version", "coverage", "status", "reason", "offset_unit", "source_hash", "parser_kind",
                "parser_version", "records", "record_count", "omitted", *BOUNDARIES}
    if (set(evidence) != expected or evidence.get("version") != VERSION or evidence.get("coverage") != COVERAGE
            or evidence.get("offset_unit") != "utf16_code_units" or evidence.get("parser_kind") != "typescript_compiler_api"
            or not isinstance(evidence.get("parser_version"), str) or not evidence["parser_version"]
            or any(evidence.get(key) != value for key, value in BOUNDARIES.items())
            or evidence.get("mutation_proposed") is not False):
        return reject("observation_contract_invalid")
    if not isinstance(content, str) or len(content.encode("utf-8")) > 1048576:
        return reject("source_unavailable_or_byte_budget_exceeded")
    identity = hashlib.sha256(content.encode("utf-8")).hexdigest()
    features = meta.get("features")
    if (evidence.get("source_hash") != identity or not isinstance(features, list)
            or [v for v in features if isinstance(v, str) and v.startswith("Hash:")] != [f"Hash:{identity}"]):
        return reject("source_identity_invalid")
    records, status, omitted = evidence.get("records"), evidence.get("status"), evidence.get("omitted")
    if (not isinstance(records, list) or len(records) > 64 or type(evidence.get("record_count")) is not int
            or evidence["record_count"] != len(records) or type(omitted) is not int or omitted < 0):
        return reject("observation_count_invalid")
    if status == "unavailable":
        return reject(evidence["reason"] if not records and not omitted and isinstance(evidence.get("reason"), str)
                      and evidence["reason"] else "unavailable_record_invalid")
    if ((status == "observed" and (omitted or evidence.get("reason") != ""))
            or (status == "partial" and (omitted < 1 or evidence.get("reason") != "record_budget_exceeded"))
            or status not in ("observed", "partial")):
        return reject("observation_status_invalid")
    problem = _observation_record_problem(records, _native_utf16_span_validator(content))
    if problem:
        return reject(problem)
    offsets = [s[k] for row in records for s in row["spans"].values() for k in ("start", "end")]
    offset_map = _utf16_offsets_to_utf8_byte_offsets(content, offsets)
    if any(offset not in offset_map for offset in offsets):
        return reject("observation_coordinates_invalid")
    normalized = deepcopy(records)
    for row in normalized:
        for span in row["spans"].values():
            span.update(start=offset_map[span["start"]], end=offset_map[span["end"]])
    return {**base, "status": status, "reason": evidence["reason"], "source_hash": identity,
            "parser_kind": evidence["parser_kind"], "parser_version": evidence["parser_version"],
            "records": normalized, "record_count": len(normalized), "omitted": omitted}


def _observation_record_problem(records, valid_span):
    """One finite grammar for producer and consumer; no source parser or intent join."""
    seen = set()
    for row in records:
        if not isinstance(row, dict) or not isinstance(row.get("kind"), str) or row["kind"] not in ROLES:
            return "observation_record_invalid"
        kind, spans = row["kind"], row.get("spans")
        names = {"source", "local_name", "imported_name"} if kind == IMPORT else {"tag", "prop"}
        if (set(row) != {"kind", "spans", *names} or not isinstance(spans, dict) or set(spans) != ROLES[kind]
                or any(not isinstance(row[k], str) or not row[k] or len(row[k]) > (180 if k == "source" else 128) for k in names)
                or not all(valid_span(span) for span in spans.values())):
            return "observation_record_invalid"
        pairs = ([("import_declaration", "import_binding"), ("import_declaration", "module_source"),
                  ("import_binding", "imported_binding"), ("import_binding", "local_binding")]
                 if kind == IMPORT else [("jsx_site", "tag"), ("jsx_site", "attribute"),
                  ("attribute", "prop"), ("attribute", "callback"), ("callback", "callback_body")])
        if any(not spans[a]["start"] <= spans[b]["start"] < spans[b]["end"] <= spans[a]["end"] for a,b in pairs):
            return "observation_relations_invalid"
        role = "import_binding" if kind == IMPORT else "callback"
        key = kind, spans[role]["start"], spans[role]["end"]
        if key in seen:
            return "observation_duplicate"
        seen.add(key)
        if kind == IMPORT:
            if (not valid_span(spans["module_source"], row["source"], quoted=True)
                    or not valid_span(spans["local_binding"], row["local_name"])
                    or not valid_span(spans["imported_binding"], row["imported_name"])
                    or spans["import_binding"]["end"] >= spans["module_source"]["start"]):
                return "observation_tokens_invalid"
        else:
            if (not re.fullmatch(r"[a-z][A-Za-z0-9]*", row["tag"])
                    or not re.fullmatch(r"on[A-Z][A-Za-z0-9]*", row["prop"])
                    or not valid_span(spans["tag"], row["tag"]) or not valid_span(spans["prop"], row["prop"])
                    or spans["tag"]["end"] >= spans["attribute"]["start"]
                    or spans["prop"]["end"] >= spans["callback"]["start"]
                    or not valid_span.trivia_between(spans["callback_body"]["start"], spans["callback_body"]["end"], "{}")
                    or not valid_span.trivia_between(spans["callback"]["start"], spans["callback_body"]["start"], "()=>")):
                return "observation_tokens_invalid"
    return ""


def stored_wiring_observation_problem(evidence, content):
    """Verify a stored normalized lane against exact snapshot text, not live intent.

    Caller owns verified primary reads, snapshot identity and live drift checks.
    No legacy metadata is synthesized and no unavailable lane becomes clean.
    """
    fields = {"version", "coverage", "status", "reason", "offset_unit", "source_hash", "parser_kind",
              "parser_version", "records", "record_count", "omitted", "ast_contract_version", *BOUNDARIES}
    if (not isinstance(evidence, dict) or set(evidence) != fields
            or evidence.get("version") != VERSION or evidence.get("coverage") != COVERAGE
            or evidence.get("offset_unit") != "utf8_bytes" or evidence.get("ast_contract_version") != AST_CONTRACT_VERSION
            or evidence.get("parser_kind") != "typescript_compiler_api" or not isinstance(evidence.get("parser_version"), str)
            or not evidence["parser_version"] or evidence.get("mutation_proposed") is not False
            or any(evidence.get(key) != value for key,value in BOUNDARIES.items())):
        return "stored_contract_invalid"
    if (not isinstance(content, str) or len(content.encode("utf-8")) > 1048576
            or evidence["source_hash"] != hashlib.sha256(content.encode("utf-8")).hexdigest()):
        return "stored_source_identity_invalid"
    records, status, omitted = evidence["records"], evidence["status"], evidence["omitted"]
    if (not isinstance(records, list) or len(records) > 64 or type(evidence["record_count"]) is not int
            or evidence["record_count"] != len(records) or type(omitted) is not int or omitted < 0
            or status not in ("observed", "partial")
            or (status == "observed" and (omitted or evidence["reason"] != ""))
            or (status == "partial" and (omitted < 1 or evidence["reason"] != "record_budget_exceeded"))):
        return "stored_status_or_count_invalid"
    # Bound corrupt stored role maps before building the coordinate context.
    if any(not isinstance(row, dict) or not isinstance(row.get("spans"), dict)
           or len(row["spans"]) > max(len(roles) for roles in ROLES.values()) for row in records):
        return "observation_record_invalid"
    spans = [s for row in records for s in row["spans"].values()]
    return _observation_record_problem(records, _utf8_span_validator(content, spans))
