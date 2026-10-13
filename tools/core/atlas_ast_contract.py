"""Pure shared Atlas AST identity and finite-record validation. No producer imports."""

AST_CONTRACT_VERSION = "v18.49-returned-callback-control-context-source-facts"


def _jsx_callback_record_valid(row, outer, valid_span, *, child=False, source_span_validator=None):
    """Two distinct source facts, not a cross-file or runtime join."""
    def contains(parent, inner):
        return (parent["start"] <= inner["start"] < inner["end"] <= parent["end"]
                and parent["line"] <= inner["line"] <= inner["end_line"] <= parent["end_line"])

    if (not isinstance(row, dict) or not isinstance(row.get("spans"), dict)
            or any(type(outer.get(key)) is not int for key in ("start", "end", "line", "end_line"))):
        return False
    spans = row["spans"]
    if child:
        if (set(row) != {"prop", "binding_name", "awaited", "context_attributes", "spans"}
                or not isinstance(row["prop"], str) or not row["prop"] or row["binding_name"] != row["prop"]
                or type(row["awaited"]) is not bool or not isinstance(row["context_attributes"], list)
                or not 1 <= len(row["context_attributes"]) <= 2
                or any(not isinstance(value, str) or not value for value in row["context_attributes"])):
            return False
        count = len(row["context_attributes"])
        roles = {"parameter", "property", "binding", "statement", "call", "callee"}
        roles.update(f"{prefix}_{index}" for index in range(count)
                     for prefix in ("context", "attribute", "attribute_name", "body"))
        pairs = [("parameter", "property"), ("parameter", "binding"), ("statement", "call"), ("call", "callee"),
                 (f"body_{count - 1}", "statement")]
        for index in range(count):
            pairs += [(f"attribute_{index}", f"context_{index}"),
                      (f"attribute_{index}", f"attribute_name_{index}"), (f"context_{index}", f"body_{index}")]
            if index: pairs.append((f"body_{index - 1}", f"attribute_{index}"))
        inside = roles
    else:
        if (set(row) != {"source", "localName", "importedName", "kind", "prop", "callback_name", "spans"}
                or row["kind"] != "named"
                or any(not isinstance(row[key], str) or not row[key] for key in
                       ("source", "localName", "importedName", "prop", "callback_name"))):
            return False
        roles = {"jsx_site", "tag", "attribute", "prop", "value", "import_declaration", "import_binding", "imported_binding", "local_binding", "module_source",
                 "callback", "callback_binding", "callback_body"}
        pairs = [("jsx_site", "tag"), ("jsx_site", "attribute"), ("attribute", "prop"), ("attribute", "value"),
                 ("callback", "callback_binding"), ("callback", "callback_body"),
                 ("import_declaration", "import_binding"), ("import_declaration", "module_source"),
                 ("import_binding", "imported_binding"), ("import_binding", "local_binding")]
        inside = roles - {"import_declaration", "import_binding", "imported_binding", "local_binding", "module_source"}
    if (set(spans) != roles or not all(valid_span(span) for span in spans.values())
            or not all(contains(outer, spans[key]) for key in inside)
            or not all(contains(spans[parent], spans[inner]) for parent, inner in pairs)):
        return False
    if source_span_validator is not None:
        literals = ({"property": row["prop"], "binding": row["binding_name"], "callee": row["binding_name"],
                     **{f"attribute_name_{index}": value for index, value in enumerate(row["context_attributes"])}}
                    if child else {"tag": row["localName"], "local_binding": row["localName"],
                        "imported_binding": row["importedName"], "prop": row["prop"], "value": row["callback_name"],
                        "callback_binding": row["callback_name"]})
        if any(not source_span_validator(spans[role], text) for role, text in literals.items()):
            return False
        if not child and not source_span_validator(spans["module_source"], row["source"], True):
            return False
    if child:
        return (spans["property"] == spans["binding"]
                and spans["parameter"]["end"] < spans["attribute_0"]["start"]
                and spans["callee"]["start"] == spans["call"]["start"]
                and all(spans[f"attribute_name_{index}"]["end"] < spans[f"context_{index}"]["start"]
                        for index in range(count)))
    return (spans["tag"]["end"] < spans["attribute"]["start"]
            and spans["prop"]["end"] < spans["value"]["start"]
            and spans["callback_binding"]["end"] < spans["callback_body"]["start"]
            and spans["import_binding"]["end"] < spans["module_source"]["start"]
            and all(spans[role]["end"] <= outer["start"] or spans[role]["start"] >= outer["end"]
                    for role in ("import_declaration",)))


def _call_result_property_binding_record_valid(row, outer, valid_span, *, source_span_validator=None):
    """Literal binding endpoints, not property existence, callability or value transfer."""
    imports = {"import_declaration", "import_binding", "imported_binding", "producer_local_binding", "module_source"}
    roles = imports | {"owner", "body", "declaration", "pattern", "binding_element", "property_name",
                       "local_binding", "initializer", "callee"}
    if (not isinstance(row, dict)
            or set(row) != {"property_name", "local_name", "source", "localName", "importedName", "kind", "spans"}
            or any(not isinstance(row.get(key), str) or not row[key]
                   for key in ("property_name", "local_name", "source", "localName", "importedName"))
            or row["kind"] != "named" or not isinstance(row["spans"], dict) or set(row["spans"]) != roles
            or not all(valid_span(span) for span in row["spans"].values())
            or any(type(outer.get(key)) is not int for key in ("start", "end", "line", "end_line"))):
        return False
    spans = row["spans"]
    contains = lambda a, b: (a["start"] <= b["start"] < b["end"] <= a["end"]
                            and a["line"] <= b["line"] <= b["end_line"] <= a["end_line"])
    pairs = [("owner", "body"), ("body", "declaration"), ("declaration", "pattern"),
             ("pattern", "binding_element"), ("binding_element", "property_name"), ("binding_element", "local_binding"),
             ("declaration", "initializer"), ("initializer", "callee"), ("import_declaration", "import_binding"),
             ("import_declaration", "module_source"), ("import_binding", "imported_binding"),
             ("import_binding", "producer_local_binding")]
    shorthand = spans["property_name"] == spans["local_binding"]
    if (spans["owner"] != {key: outer[key] for key in ("start", "end", "line", "end_line")}
            or not all(contains(outer, spans[key]) for key in roles - imports)
            or not all(contains(spans[a], spans[b]) for a, b in pairs)
            or spans["pattern"]["end"] >= spans["initializer"]["start"]
            or spans["initializer"]["start"] != spans["callee"]["start"]
            or spans["import_binding"]["end"] >= spans["module_source"]["start"]
            or not (spans["import_declaration"]["end"] <= outer["start"]
                    or spans["import_declaration"]["start"] >= outer["end"])
            or (row["property_name"] != row["local_name"] if shorthand else
                spans["property_name"]["end"] >= spans["local_binding"]["start"])
            or spans["binding_element"]["start"] != spans["property_name"]["start"]
            or spans["binding_element"]["end"] != spans["local_binding"]["end"]):
        return False
    if source_span_validator is not None:
        literals = {"property_name": row["property_name"], "local_binding": row["local_name"],
                    "callee": row["localName"], "producer_local_binding": row["localName"],
                    "imported_binding": row["importedName"]}
        if (any(not source_span_validator(spans[role], text) for role, text in literals.items())
                or not source_span_validator(spans["module_source"], row["source"], True)):
            return False
    return True


def _returned_local_callback_record_valid(row, outer, valid_span, *, source_span_validator=None):
    """Finite local binding/property coordinates, not callback value or execution."""
    imports = {"import_declaration", "import_binding", "imported_binding", "local_binding", "module_source"}
    roles = imports | {"owner", "body", "declaration", "binding", "initializer", "callee", "callback",
                       "callback_body", "dependencies", "return_statement", "returned_object", "property", "value"}
    if (not isinstance(row, dict) or set(row) != {"callback_name", "property_name", "source", "localName", "importedName", "kind", "spans"}
            or any(not isinstance(row.get(key), str) or not row[key] for key in ("callback_name", "property_name", "localName"))
            or row["callback_name"] != row["property_name"] or row["source"] != "react"
            or row["importedName"] != "useCallback" or row["kind"] != "named"
            or not isinstance(row["spans"], dict) or set(row["spans"]) != roles
            or not all(valid_span(span) for span in row["spans"].values())
            or any(type(outer.get(key)) is not int for key in ("start", "end", "line", "end_line"))):
        return False
    spans = row["spans"]
    contains = lambda a, b: (a["start"] <= b["start"] < b["end"] <= a["end"]
                            and a["line"] <= b["line"] <= b["end_line"] <= a["end_line"])
    pairs = [("owner", "body"), ("body", "declaration"), ("declaration", "binding"),
             ("declaration", "initializer"), ("initializer", "callee"), ("initializer", "callback"),
             ("callback", "callback_body"), ("initializer", "dependencies"), ("body", "return_statement"),
             ("return_statement", "returned_object"), ("returned_object", "property"), ("property", "value"),
             ("import_declaration", "import_binding"), ("import_declaration", "module_source"),
             ("import_binding", "imported_binding"), ("import_binding", "local_binding")]
    if (spans["owner"] != {key: outer[key] for key in ("start", "end", "line", "end_line")}
            or not all(contains(outer, spans[key]) for key in roles - imports)
            or not all(contains(spans[a], spans[b]) for a, b in pairs)
            or spans["property"] != spans["value"]
            or spans["binding"]["end"] >= spans["initializer"]["start"]
            or spans["initializer"]["start"] != spans["callee"]["start"]
            or spans["callee"]["end"] >= spans["callback"]["start"]
            or spans["callback"]["end"] >= spans["dependencies"]["start"]
            or spans["dependencies"]["end"] >= spans["initializer"]["end"]
            or spans["declaration"]["end"] >= spans["return_statement"]["start"]
            or spans["return_statement"]["start"] >= spans["returned_object"]["start"]
            or spans["import_binding"]["end"] >= spans["module_source"]["start"]
            or not (spans["import_declaration"]["end"] <= outer["start"]
                    or spans["import_declaration"]["start"] >= outer["end"])):
        return False
    if source_span_validator is not None:
        literals = {"binding": row["callback_name"], "value": row["callback_name"],
                    "callee": row["localName"], "local_binding": row["localName"], "imported_binding": "useCallback"}
        if (any(not source_span_validator(spans[role], text) for role, text in literals.items())
                or not source_span_validator(spans["module_source"], "react", True)):
            return False
    return True


def _local_arrow_sibling_record_valid(row, outer, valid_span):
    """Local declarations are bounded lexical endpoints, not indexed callees."""
    roles = {"owner", "owner_binding", "body", "target", "target_binding", "target_body", "statement", "call", "callee"}
    if (not isinstance(row, dict) or set(row) != {"caller_name", "target_name", "lexical_owner_kind", "target_kind", "awaited", "spans"}
            or any(not isinstance(row[k], str) or not row[k] for k in ("caller_name", "target_name"))
            or row["caller_name"] == row["target_name"] or type(row["awaited"]) is not bool
            or row["lexical_owner_kind"] != "const_local_arrow" or row["target_kind"] != "const_local_arrow"
            or not isinstance(row["spans"], dict) or set(row["spans"]) != roles
            or not all(valid_span(span) for span in row["spans"].values())
            or any(type(outer.get(k)) is not int for k in ("start", "end", "line", "end_line"))):
        return False
    spans = row["spans"]
    pairs = [("owner", "owner_binding"), ("owner", "body"), ("body", "statement"),
             ("statement", "call"), ("call", "callee"), ("target", "target_binding"), ("target", "target_body")]
    return (all(outer["start"] <= spans[k]["start"] < spans[k]["end"] <= outer["end"]
                and outer["line"] <= spans[k]["line"] <= spans[k]["end_line"] <= outer["end_line"] for k in ("owner", "target"))
            and all(spans[a]["start"] <= spans[b]["start"] < spans[b]["end"] <= spans[a]["end"]
                    and spans[a]["line"] <= spans[b]["line"] <= spans[b]["end_line"] <= spans[a]["end_line"] for a, b in pairs)
            and spans["owner_binding"]["end"] < spans["body"]["start"]
            and spans["target_binding"]["end"] < spans["target_body"]["start"]
            and (spans["target"]["end"] < spans["owner"]["start"] or spans["owner"]["end"] < spans["target"]["start"])
            and spans["call"]["start"] == spans["callee"]["start"]
            and (spans["statement"]["start"] < spans["call"]["start"] if row["awaited"]
                 else spans["statement"]["start"] == spans["call"]["start"]))


def _local_arrow_result_use_record_valid(row, outer, valid_span):
    """Shared finite record/containment guard, in caller coordinates only."""
    fields = {"source", "localName", "importedName", "kind", "member", "optional", "lexical_owner",
              "lexical_owner_kind", "result_name", "selected_property", "sink_name", "spans"}
    roles = {"owner", "owner_binding", "body", "call", "callee", "result_binding", "initializer",
             "condition", "argument", "sink_call", "sink_callee", "sink_binding"}
    if (not isinstance(row, dict) or set(row) != fields
            or any(not isinstance(row[key], str) or not row[key] for key in
                   ("source", "localName", "importedName", "lexical_owner", "result_name", "selected_property", "sink_name"))
            or row["kind"] != "named" or row["member"] is not None or row["optional"] is not False
            or row["lexical_owner_kind"] != "const_local_arrow"
            or not isinstance(row["spans"], dict) or set(row["spans"]) != roles
            or not all(valid_span(span) for span in row["spans"].values())):
        return False
    spans = row["spans"]
    if (type(outer.get("start")) is not int or type(outer.get("end")) is not int
            or not outer["start"] <= spans["owner"]["start"] < spans["owner"]["end"] <= outer["end"]):
        return False
    pairs = [("owner", "owner_binding"), ("owner", "body"), ("body", "result_binding"),
             ("body", "initializer"), ("initializer", "call"), ("call", "callee"),
             ("body", "condition"), ("body", "sink_call"), ("sink_call", "sink_callee"), ("sink_call", "argument")]
    return (all(spans[a]["start"] <= spans[b]["start"] < spans[b]["end"] <= spans[a]["end"] for a, b in pairs)
            and spans["owner_binding"]["end"] <= spans["body"]["start"]
            and spans["result_binding"]["end"] <= spans["initializer"]["start"]
            and spans["initializer"]["end"] < spans["condition"]["start"]
            and spans["condition"]["end"] < spans["sink_call"]["start"]
            and spans["sink_callee"]["end"] < spans["argument"]["start"]
            and spans["callee"]["start"] == spans["call"]["start"]
            and spans["sink_callee"]["start"] == spans["sink_call"]["start"])



def _call_result_alias_invocation_record_valid(row, outer, valid_span, *, source_span_validator=None):
    """An invocation site associated with a const binding, not an invoked runtime value."""
    extra = {"invocation_statement", "invocation", "invoked_identifier"}
    if not isinstance(row, dict) or not isinstance(row.get("spans"), dict) or not extra <= row["spans"].keys():
        return False
    spans = row["spans"]
    binding = {**row, "spans": {key: value for key, value in spans.items() if key not in extra}}
    if not _call_result_property_binding_record_valid(binding, outer, valid_span,
                                                     source_span_validator=source_span_validator):
        return False
    contains = lambda a, b: (a["start"] <= b["start"] < b["end"] <= a["end"]
                            and a["line"] <= b["line"] <= b["end_line"] <= a["end_line"])
    if (not all(valid_span(spans[key]) for key in extra)
            or not contains(spans["body"], spans["invocation_statement"])
            or not contains(spans["invocation_statement"], spans["invocation"])
            or not contains(spans["invocation"], spans["invoked_identifier"])
            or spans["declaration"]["end"] >= spans["invocation_statement"]["start"]
            or spans["invocation_statement"]["start"] != spans["invocation"]["start"]
            or spans["invocation"]["start"] != spans["invoked_identifier"]["start"]
            or spans["invoked_identifier"]["end"] >= spans["invocation"]["end"]):
        return False
    return source_span_validator is None or source_span_validator(spans["invoked_identifier"], row["local_name"])


def _direct_optional_alias_record_valid(row, outer, valid_span, *, source_span_validator=None):
    """Exact optional marker and admitted local source binding, never a runtime outcome."""
    if (not isinstance(row, dict) or row.get("optional_call") is not True
            or not isinstance(row.get("spans"), dict) or "optional_operator" not in row["spans"]):
        return False
    plain = {key: value for key, value in row.items() if key != "optional_call"}
    plain["spans"] = {key: value for key, value in row["spans"].items() if key != "optional_operator"}
    if not _call_result_alias_invocation_record_valid(plain, outer, valid_span,
                                                     source_span_validator=source_span_validator):
        return False
    spans, marker = row["spans"], row["spans"]["optional_operator"]
    if (not valid_span(marker) or marker["end"] - marker["start"] != 2
            or not spans["invoked_identifier"]["end"] <= marker["start"] < marker["end"] < spans["invocation"]["end"]
            or not spans["invoked_identifier"]["end_line"] <= marker["line"] <= marker["end_line"] <= spans["invocation"]["end_line"]):
        return False
    return source_span_validator is None or source_span_validator(marker, "?.")


def _nested_callback_alias_record_valid(row, outer, valid_span, *, source_span_validator=None):
    """Finite source nesting and endpoints, never effective registration or execution."""
    extra_roles = {"container_statement", "container_call", "container_callee", "container_object",
                   "callback_property", "callback_property_name", "callback", "callback_body",
                   "invocation_statement", "invocation", "invoked_identifier"}
    extra_fields = {"callback_property_name", "container_callee_spelling", "object_spread_present",
                    "duplicate_callback_property_spelling"}
    if (not isinstance(row, dict) or not isinstance(row.get("spans"), dict)
            or not extra_roles <= row["spans"].keys() or not extra_fields <= row.keys()
            or any(not isinstance(row.get(key), str) or not row[key]
                   for key in ("callback_property_name", "container_callee_spelling"))
            or any(type(row.get(key)) is not bool for key in
                   ("object_spread_present", "duplicate_callback_property_spelling"))):
        return False
    spans = row["spans"]
    binding = {key: value for key, value in row.items() if key not in extra_fields}
    binding["spans"] = {key: value for key, value in spans.items() if key not in extra_roles}
    if not _call_result_property_binding_record_valid(binding, outer, valid_span,
                                                     source_span_validator=source_span_validator):
        return False
    contains = lambda a, b: (a["start"] <= b["start"] < b["end"] <= a["end"]
                            and a["line"] <= b["line"] <= b["end_line"] <= a["end_line"])
    pairs = [("body", "container_statement"), ("container_statement", "container_call"),
             ("container_call", "container_callee"), ("container_call", "container_object"),
             ("container_object", "callback_property"), ("callback_property", "callback_property_name"),
             ("callback_property", "callback"), ("callback", "callback_body"),
             ("callback_body", "invocation_statement"), ("invocation_statement", "invocation"),
             ("invocation", "invoked_identifier")]
    if (set(spans) != set(binding["spans"]) | extra_roles
            or not all(valid_span(spans[key]) for key in extra_roles)
            or not all(contains(spans[a], spans[b]) for a, b in pairs)
            or spans["declaration"]["end"] >= spans["container_statement"]["start"]
            or spans["container_call"]["start"] != spans["container_callee"]["start"]
            or spans["container_callee"]["end"] >= spans["container_object"]["start"]
            or spans["callback_property"]["start"] != spans["callback_property_name"]["start"]
            or spans["callback_property_name"]["end"] >= spans["callback"]["start"]
            or spans["callback"]["end"] != spans["callback_body"]["end"]
            or spans["callback_property"]["end"] != spans["callback"]["end"]
            or spans["invocation_statement"]["start"] != spans["invocation"]["start"]
            or spans["invocation"]["start"] != spans["invoked_identifier"]["start"]
            or spans["invoked_identifier"]["end"] >= spans["invocation"]["end"]):
        return False
    return source_span_validator is None or all(source_span_validator(spans[role], text) for role, text in
        (("invoked_identifier", row["local_name"]), ("callback_property_name", row["callback_property_name"]),
         ("container_callee", row["container_callee_spelling"])))


def _optional_rest_alias_record_valid(row, outer, valid_span, *, source_span_validator=None):
    """Reuse exact imported-binding/nesting guards; add rest endpoints, not value proof."""
    roles = {"rest_parameter", "rest_binding", "spread_argument", "spread_identifier"}
    if (not isinstance(row, dict) or not isinstance(row.get("spans"), dict)
            or not roles <= row["spans"].keys() or row.get("optional_call") is not True
            or not isinstance(row.get("rest_name"), str) or not row["rest_name"]):
        return False
    nested = {key: value for key, value in row.items() if key not in {"rest_name", "optional_call"}}
    nested["spans"] = {key: value for key, value in row["spans"].items() if key not in roles}
    if not _nested_callback_alias_record_valid(nested, outer, valid_span,
                                               source_span_validator=source_span_validator):
        return False
    spans = row["spans"]
    contains = lambda a, b: (a["start"] <= b["start"] < b["end"] <= a["end"]
                            and a["line"] <= b["line"] <= b["end_line"] <= a["end_line"])
    if (not all(valid_span(spans[key]) for key in roles)
            or not all(contains(spans[a], spans[b]) for a, b in (
                ("callback", "rest_parameter"), ("rest_parameter", "rest_binding"),
                ("invocation", "spread_argument"), ("spread_argument", "spread_identifier")))
            or spans["rest_parameter"]["end"] >= spans["callback_body"]["start"]
            or spans["rest_parameter"]["start"] >= spans["rest_binding"]["start"]
            or spans["invoked_identifier"]["end"] >= spans["spread_argument"]["start"]
            or spans["spread_argument"]["start"] >= spans["spread_identifier"]["start"]
            or spans["spread_argument"]["end"] != spans["spread_identifier"]["end"]
            or spans["spread_argument"]["end"] >= spans["invocation"]["end"]):
        return False
    return source_span_validator is None or all(source_span_validator(spans[role], row["rest_name"])
        for role in ("rest_binding", "spread_identifier"))


PARAMETER_CONFIG_CALLBACK_VERSION = "v1-parameter-config-optional-rest-forwarding"


PARAMETER_CONFIG_CALLBACK_ROLES = frozenset({
    "owner", "body", "parameter", "parameter_binding", "parameter_name", "parameter_input",
    "declaration", "pattern", "binding_element", "property_name", "local_binding", "initializer",
    "container_statement", "container_call", "container_callee", "container_object",
    "callback_property", "callback_property_name", "callback", "callback_body",
    "rest_parameter", "rest_binding", "invocation_statement", "invocation", "invoked_identifier",
    "spread_argument", "spread_identifier",
})


def _parameter_config_callback_record_valid(row, outer, valid_span, *, source_span_validator=None):
    """Independent parameter provenance; no imported producer or runtime registration."""
    strings = {"property_name", "local_name", "parameter_name", "callback_property_name",
               "container_callee_spelling", "rest_name"}
    flags = {"parameter_default_empty_object", "object_rest_present", "object_spread_present",
             "duplicate_callback_property_spelling", "optional_call"}
    if (not isinstance(row, dict)
            or set(row) != strings | flags | {"parameter_binding_kind", "fallback_operator", "spans"}
            or any(not isinstance(row[key], str) or not row[key] for key in strings)
            or any(type(row[key]) is not bool for key in flags)
            or row["optional_call"] is not True
            or row["parameter_binding_kind"] not in ("simple_parameter", "object_parameter_property")
            or row["fallback_operator"] not in ("none", "||", "??")
            or not isinstance(row["spans"], dict) or set(row["spans"]) != PARAMETER_CONFIG_CALLBACK_ROLES
            or not all(valid_span(span) for span in row["spans"].values())
            or any(type(outer.get(key)) is not int for key in ("start", "end", "line", "end_line"))):
        return False
    spans = row["spans"]
    contains = lambda a, b: (a["start"] <= b["start"] < b["end"] <= a["end"]
                            and a["line"] <= b["line"] <= b["end_line"] <= a["end_line"])
    pairs = [("owner", "body"), ("owner", "parameter"), ("parameter", "parameter_binding"),
             ("parameter_binding", "parameter_name"), ("body", "declaration"), ("declaration", "pattern"),
             ("pattern", "binding_element"), ("binding_element", "property_name"),
             ("binding_element", "local_binding"), ("declaration", "initializer"),
             ("initializer", "parameter_input"), ("body", "container_statement"),
             ("container_statement", "container_call"), ("container_call", "container_callee"),
             ("container_call", "container_object"), ("container_object", "callback_property"),
             ("callback_property", "callback_property_name"), ("callback_property", "callback"),
             ("callback", "callback_body"), ("callback", "rest_parameter"), ("rest_parameter", "rest_binding"),
             ("callback_body", "invocation_statement"), ("invocation_statement", "invocation"),
             ("invocation", "invoked_identifier"), ("invocation", "spread_argument"),
             ("spread_argument", "spread_identifier")]
    shorthand = spans["property_name"] == spans["local_binding"]
    if (spans["owner"] != {key: outer[key] for key in ("start", "end", "line", "end_line")}
            or not all(contains(outer, span) for span in spans.values())
            or not all(contains(spans[a], spans[b]) for a, b in pairs)
            or spans["parameter"]["end"] > spans["body"]["start"]
            or (spans["parameter"] == spans["parameter_binding"]) != (row["parameter_binding_kind"] == "simple_parameter")
            or spans["pattern"]["end"] >= spans["initializer"]["start"]
            or spans["initializer"]["start"] != spans["parameter_input"]["start"]
            or (spans["initializer"] == spans["parameter_input"]) != (row["fallback_operator"] == "none")
            or spans["binding_element"]["start"] != spans["property_name"]["start"]
            or spans["binding_element"]["end"] != spans["local_binding"]["end"]
            or (row["property_name"] != row["local_name"] if shorthand else
                spans["property_name"]["end"] >= spans["local_binding"]["start"])
            or spans["declaration"]["end"] >= spans["container_statement"]["start"]
            or spans["container_call"]["start"] != spans["container_callee"]["start"]
            or spans["container_callee"]["end"] >= spans["container_object"]["start"]
            or spans["callback_property"]["start"] != spans["callback_property_name"]["start"]
            or spans["callback_property_name"]["end"] >= spans["callback"]["start"]
            or spans["callback_property"]["end"] != spans["callback"]["end"]
            or spans["callback"]["end"] != spans["callback_body"]["end"]
            or spans["rest_parameter"]["end"] >= spans["callback_body"]["start"]
            or spans["invocation_statement"]["start"] != spans["invocation"]["start"]
            or spans["invocation"]["start"] != spans["invoked_identifier"]["start"]
            or spans["invoked_identifier"]["end"] >= spans["spread_argument"]["start"]
            or spans["spread_argument"]["end"] != spans["spread_identifier"]["end"]
            or spans["spread_argument"]["start"] >= spans["spread_identifier"]["start"]
            or spans["rest_parameter"]["start"] >= spans["rest_binding"]["start"]):
        return False
    if source_span_validator is not None:
        tokens = {"property_name": row["property_name"], "local_binding": row["local_name"],
                  "invoked_identifier": row["local_name"], "parameter_name": row["parameter_name"],
                  "parameter_input": row["parameter_name"], "callback_property_name": row["callback_property_name"],
                  "container_callee": row["container_callee_spelling"], "rest_binding": row["rest_name"],
                  "spread_identifier": row["rest_name"]}
        if not all(source_span_validator(spans[role], text) for role, text in tokens.items()): return False
    return True


RETURNED_PARAMETER_OPTIONAL_VERSION = "v1-returned-react-async-parameter-try-awaited-optional-call"


RETURNED_CALLBACK_ANCHOR_FIELDS = {"callback_name", "property_name", "source", "localName", "importedName", "kind", "spans"}


RETURNED_CALLBACK_ANCHOR_ROLES = {"owner", "body", "declaration", "binding", "initializer", "callee", "callback",
    "callback_body", "dependencies", "return_statement", "returned_object", "property", "value",
    "import_declaration", "import_binding", "imported_binding", "local_binding", "module_source"}


def _returned_parameter_anchor(row):
    """Project only the original anchor vocabulary; never overwrite its property_name."""
    return {**{key: row[key] for key in RETURNED_CALLBACK_ANCHOR_FIELDS - {"spans"}},
            "spans": {key: row["spans"][key] for key in RETURNED_CALLBACK_ANCHOR_ROLES}}


def _returned_parameter_optional_record_valid(row, outer, valid_span, *, source_span_validator=None):
    extra = {"parameter_name", "parameter_default_empty_object", "parameter_property_name", "parameter_local_name",
             "argument_name", "unselected_literal_default_count", "context_kind", "awaited", "optional_call"}
    roles = {"parameter", "parameter_name", "parameter_input", "binding_statement", "binding_declaration", "binding_pattern",
             "callback_binding_element", "callback_property_name", "callback_local_binding", "try_statement", "try_body",
             "invocation_statement", "await_expression", "invocation", "invoked_identifier", "optional_operator", "argument"}
    if (not isinstance(row, dict) or set(row) != RETURNED_CALLBACK_ANCHOR_FIELDS | extra
            or not isinstance(row.get("spans"), dict) or set(row["spans"]) != RETURNED_CALLBACK_ANCHOR_ROLES | roles
            or any(not isinstance(row.get(k), str) or not row[k] for k in
                   ("parameter_name", "parameter_property_name", "parameter_local_name", "argument_name"))
            or type(row.get("parameter_default_empty_object")) is not bool
            or type(row.get("unselected_literal_default_count")) is not int
            or not 0 <= row["unselected_literal_default_count"] <= 10000
            or row.get("context_kind") != "immediate_own_try_block"
            or row.get("awaited") is not True or row.get("optional_call") is not True
            or not all(valid_span(s) for s in row["spans"].values())
            or not _returned_local_callback_record_valid(_returned_parameter_anchor(row), outer, valid_span,
                                                        source_span_validator=source_span_validator)):
        return False
    s = row["spans"]
    pairs = [("callback", "parameter"), ("parameter", "parameter_name"), ("callback_body", "binding_statement"),
        ("binding_statement", "binding_declaration"), ("binding_declaration", "binding_pattern"),
        ("binding_declaration", "parameter_input"), ("binding_pattern", "callback_binding_element"),
        ("callback_binding_element", "callback_property_name"), ("callback_binding_element", "callback_local_binding"),
        ("callback_body", "try_statement"), ("try_statement", "try_body"), ("try_body", "invocation_statement"),
        ("invocation_statement", "await_expression"), ("await_expression", "invocation"),
        ("invocation", "invoked_identifier"), ("invocation", "optional_operator"), ("invocation", "argument")]
    if (not all(s[a]["start"] <= s[b]["start"] < s[b]["end"] <= s[a]["end"]
                and s[a]["line"] <= s[b]["line"] <= s[b]["end_line"] <= s[a]["end_line"] for a, b in pairs)
            or s["parameter"]["end"] >= s["callback_body"]["start"]
            or s["parameter"]["start"] != s["parameter_name"]["start"]
            or s["binding_pattern"]["end"] >= s["parameter_input"]["start"]
            or s["binding_statement"]["end"] >= s["try_statement"]["start"]
            or s["await_expression"]["start"] != s["invocation_statement"]["start"]
            or s["await_expression"]["start"] + 5 > s["invocation"]["start"]
            or s["await_expression"]["end"] != s["invocation"]["end"]
            or s["invocation"]["start"] != s["invoked_identifier"]["start"]
            or s["invoked_identifier"]["end"] > s["optional_operator"]["start"]
            or s["optional_operator"]["end"] >= s["argument"]["start"]
            or s["argument"]["end"] >= s["invocation"]["end"]
            or (s["callback_property_name"] != s["callback_local_binding"]
                and s["callback_property_name"]["end"] >= s["callback_local_binding"]["start"])):
        return False
    if source_span_validator is not None:
        tokens = {"parameter_name": row["parameter_name"], "parameter_input": row["parameter_name"],
            "callback_property_name": row["parameter_property_name"], "callback_local_binding": row["parameter_local_name"],
            "invoked_identifier": row["parameter_local_name"], "argument": row["argument_name"], "optional_operator": "?."}
        await_token = {**s["await_expression"], "end": s["await_expression"]["start"] + 5,
                       "end_line": s["await_expression"]["line"]}
        if (any(not source_span_validator(s[role], token) for role, token in tokens.items())
                or not source_span_validator(await_token, "await")):
            return False
    return True


RETURNED_PARAMETER_CONTEXT_VERSION = "v1-returned-react-async-parameter-own-control-optional-call"


RETURNED_PARAMETER_CONTEXT_VARIANTS = {
    "immediate_own_try_block": ("try_statement", "try_body"),
    "immediate_own_catch_block": ("try_statement", "catch_clause", "catch_body"),
    "immediate_own_finally_block": ("try_statement", "finally_body"),
    "immediate_callback_if_block": (),
    "immediate_own_try_if_block": ("try_statement", "try_body"),
    "immediate_own_catch_if_block": ("try_statement", "catch_clause", "catch_body"),
    "immediate_own_finally_if_block": ("try_statement", "finally_body"),
}


RETURNED_PARAMETER_CONTEXT_COMMON_ROLES = RETURNED_CALLBACK_ANCHOR_ROLES | {
    "parameter", "parameter_name", "parameter_input", "binding_statement", "binding_declaration", "binding_pattern",
    "callback_binding_element", "callback_property_name", "callback_local_binding", "invocation_statement",
    "invocation", "invoked_identifier", "optional_operator", "argument"}


def _returned_parameter_context_roles(row):
    if not isinstance(row, dict): return None
    kind, branch, awaited = (row.get(key) for key in ("context_kind", "branch_kind", "awaited"))
    if (not isinstance(kind, str) or kind not in RETURNED_PARAMETER_CONTEXT_VARIANTS
            or type(awaited) is not bool or not isinstance(branch, str)):
        return None
    conditional = kind.endswith("_if_block")
    if branch not in ({"then", "else"} if conditional else {"none"}): return None
    if kind == "immediate_own_try_block" and awaited: return None
    return (RETURNED_PARAMETER_CONTEXT_COMMON_ROLES | set(RETURNED_PARAMETER_CONTEXT_VARIANTS[kind])
            | ({"if_statement", "if_condition", "branch_selector", "branch_body"} if conditional else set())
            | ({"await_expression"} if awaited else set()))


def _returned_parameter_context_record_valid(row, outer, valid_span, *, source_span_validator=None):
    """Finite role variants; source context is not reachability or value transfer."""
    roles = _returned_parameter_context_roles(row)
    extra = {"parameter_name", "parameter_default_empty_object", "parameter_property_name", "parameter_local_name",
             "argument_name", "unselected_literal_default_count", "context_kind", "branch_kind", "awaited", "optional_call"}
    if (roles is None or set(row) != RETURNED_CALLBACK_ANCHOR_FIELDS | extra
            or not isinstance(row.get("spans"), dict) or set(row["spans"]) != roles
            or any(not isinstance(row.get(k), str) or not row[k] for k in
                ("parameter_name", "parameter_property_name", "parameter_local_name", "argument_name"))
            or type(row.get("parameter_default_empty_object")) is not bool
            or type(row.get("unselected_literal_default_count")) is not int
            or not 0 <= row["unselected_literal_default_count"] <= 10000
            or row.get("optional_call") is not True or not all(valid_span(s) for s in row["spans"].values())
            or not _returned_local_callback_record_valid(_returned_parameter_anchor(row), outer, valid_span,
                                                        source_span_validator=source_span_validator)):
        return False
    s, kind = row["spans"], row["context_kind"]
    root = "if_statement" if kind == "immediate_callback_if_block" else "try_statement"
    own = ("catch_body" if "catch" in kind else "finally_body" if "finally" in kind else "try_body")
    conditional = row["branch_kind"] != "none"
    pairs = [("callback", "parameter"), ("parameter", "parameter_name"), ("callback_body", "binding_statement"),
        ("binding_statement", "binding_declaration"), ("binding_declaration", "binding_pattern"),
        ("binding_declaration", "parameter_input"), ("binding_pattern", "callback_binding_element"),
        ("callback_binding_element", "callback_property_name"), ("callback_binding_element", "callback_local_binding"),
        ("callback_body", root), ("invocation_statement", "invocation"), ("invocation", "invoked_identifier"),
        ("invocation", "optional_operator"), ("invocation", "argument")]
    if root == "try_statement":
        pairs.append((root, "catch_clause" if own == "catch_body" else own))
        if own == "catch_body": pairs.append(("catch_clause", own))
    if conditional:
        if root != "if_statement": pairs.append((own, "if_statement"))
        pairs += [("if_statement", key) for key in ("if_condition", "branch_selector", "branch_body")]
    pairs.append(("branch_body" if conditional else own, "invocation_statement"))
    if row["awaited"]: pairs += [("invocation_statement", "await_expression"), ("await_expression", "invocation")]
    if (not all(s[a]["start"] <= s[b]["start"] < s[b]["end"] <= s[a]["end"]
                and s[a]["line"] <= s[b]["line"] <= s[b]["end_line"] <= s[a]["end_line"] for a,b in pairs)
            or s["parameter"]["end"] >= s["callback_body"]["start"]
            or s["parameter"]["start"] != s["parameter_name"]["start"]
            or s["binding_pattern"]["end"] >= s["parameter_input"]["start"]
            or s["binding_statement"]["end"] >= s[root]["start"]
            or s["invocation"]["start"] != s["invoked_identifier"]["start"]
            or s["invoked_identifier"]["end"] > s["optional_operator"]["start"]
            or s["optional_operator"]["end"] >= s["argument"]["start"]
            or s["argument"]["end"] >= s["invocation"]["end"]
            or (s["callback_property_name"] != s["callback_local_binding"]
                and s["callback_property_name"]["end"] >= s["callback_local_binding"]["start"])):
        return False
    if row["awaited"]:
        if (s["await_expression"]["start"] != s["invocation_statement"]["start"]
                or s["await_expression"]["start"] + 5 > s["invocation"]["start"]
                or s["await_expression"]["end"] != s["invocation"]["end"]): return False
    elif s["invocation_statement"]["start"] != s["invocation"]["start"]: return False
    if conditional:
        selector, condition, body = (s[key] for key in ("branch_selector", "if_condition", "branch_body"))
        if condition["end"] >= body["start"]: return False
        if row["branch_kind"] == "then":
            if selector["start"] != s["if_statement"]["start"] or selector["end"] >= condition["start"]: return False
        elif not condition["end"] < selector["start"] < selector["end"] <= body["start"]: return False
    if source_span_validator is not None:
        tokens = {"parameter_name": row["parameter_name"], "parameter_input": row["parameter_name"],
            "callback_property_name": row["parameter_property_name"], "callback_local_binding": row["parameter_local_name"],
            "invoked_identifier": row["parameter_local_name"], "argument": row["argument_name"], "optional_operator": "?."}
        if conditional: tokens["branch_selector"] = "if" if row["branch_kind"] == "then" else "else"
        if any(not source_span_validator(s[role], token) for role,token in tokens.items()): return False
        if row["awaited"]:
            token = {**s["await_expression"], "end": s["await_expression"]["start"] + 5, "end_line": s["await_expression"]["line"]}
            if not source_span_validator(token, "await"): return False
        for role in ([own] if root == "try_statement" else []) + (["branch_body"] if conditional else []):
            start = {**s[role], "end": s[role]["start"] + 1, "end_line": s[role]["line"]}
            end = {**s[role], "start": s[role]["end"] - 1, "line": s[role]["end_line"]}
            if not source_span_validator(start, "{") or not source_span_validator(end, "}"): return False
    return True
