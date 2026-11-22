"""Pure finite destructured-success guard records; no producer or runtime imports."""

EARLY_RETURN_VERSION = "v2-safeparse-negative-return-single-const-actual"
EARLY_RETURN_ROLES = frozenset({
    "callback", "callback_binding", "callback_body", "block", "declaration", "success_property", "success_binding",
    "parser_call", "factory_call", "factory_ref", "factory_import", "factory_import_binding", "parser_input",
    "input_declaration", "input_binding", "guard", "condition", "guard_ref", "then_body", "return",
})
EARLY_RETURN_ARGUMENT_ROLES = frozenset({"factory_argument", "argument_declaration_list", "argument_declaration",
    "argument_const_keyword", "argument_binding", "argument_initializer"})


def early_return_roles(record):
    return EARLY_RETURN_ROLES | EARLY_RETURN_ARGUMENT_ROLES if isinstance(record, dict) and "factory_argument_name" in record else EARLY_RETURN_ROLES


def early_return_record_valid(record, valid_span):
    """Binding/guard source correspondence, never a rejecting-schema or safe-action claim."""
    if not isinstance(record, dict) or set(record) not in ({"spans"}, {"spans", "factory_argument_name"}):
        return False
    spans = record["spans"]
    if (not isinstance(spans, dict) or set(spans) != early_return_roles(record)
            or not all(valid_span(span) for span in spans.values())):
        return False
    containment = [("callback", "callback_body"), ("callback_body", "block"),
        ("block", "declaration"), ("block", "guard"), ("declaration", "success_property"),
        ("declaration", "success_binding"), ("declaration", "parser_call"), ("parser_call", "factory_call"),
        ("factory_call", "factory_ref"), ("factory_import", "factory_import_binding"),
        ("parser_call", "parser_input"), ("input_declaration", "input_binding"),
        ("guard", "condition"), ("condition", "guard_ref"), ("guard", "then_body"), ("then_body", "return")]
    order = [("factory_import", "callback_binding"), ("input_declaration", "callback_binding"),
        ("callback_binding", "callback"), ("success_binding", "parser_call"), ("declaration", "guard"),
        ("factory_call", "parser_input"), ("condition", "then_body")]
    if (not all(spans[a]["start"] <= spans[b]["start"] < spans[b]["end"] <= spans[a]["end"] for a,b in containment)
            or not all(spans[a]["end"] <= spans[b]["start"] for a,b in order)):
        return False
    trivia = valid_span.trivia_between
    if "factory_argument_name" in record:
        name = record["factory_argument_name"]
        if (not isinstance(name, str) or not name.isascii() or not 0 < len(name) <= 128
                or not (name[0].isalpha() or name[0] in "_$") or not all(c.isalnum() or c in "_$" for c in name)
                or not all(valid_span(spans[key],text) for key,text in (("factory_argument",name),("argument_binding",name),("argument_const_keyword","const")))
                or not all(spans[a]["start"] <= spans[b]["start"] < spans[b]["end"] <= spans[a]["end"] for a,b in
                    (("factory_call","factory_argument"),("argument_declaration_list","argument_const_keyword"),
                     ("argument_declaration_list","argument_declaration"),("argument_declaration","argument_binding"),
                     ("argument_declaration","argument_initializer")))
                or spans["argument_declaration"]["start"] != spans["argument_binding"]["start"]
                or spans["argument_declaration"]["end"] != spans["argument_initializer"]["end"]
                or spans["argument_declaration_list"]["start"] != spans["argument_const_keyword"]["start"]
                or spans["argument_declaration_list"]["end"] != spans["argument_declaration"]["end"]
                or spans["argument_declaration_list"]["end"] > spans["callback_binding"]["start"]
                or not trivia(spans["argument_const_keyword"]["end"],spans["argument_binding"]["start"],"")
                or not trivia(spans["argument_binding"]["end"],spans["argument_initializer"]["start"],"=")
                or not trivia(spans["factory_ref"]["end"],spans["factory_argument"]["start"],"(")
                or not trivia(spans["factory_argument"]["end"],spans["factory_call"]["end"],")")):
            return False
    elif not trivia(spans["factory_ref"]["end"], spans["factory_call"]["end"], "()"):
        return False
    return (valid_span(spans["success_property"], "success")
        and trivia(spans["factory_call"]["end"], spans["parser_input"]["start"], ".safeParse(")
        and trivia(spans["parser_input"]["end"], spans["parser_call"]["end"], ")")
        and trivia(spans["guard"]["start"], spans["condition"]["start"], "if(")
        and trivia(spans["condition"]["start"], spans["guard_ref"]["start"], "!")
        and trivia(spans["guard_ref"]["end"], spans["condition"]["end"], "")
        and trivia(spans["condition"]["end"], spans["then_body"]["start"], ")")
        and (trivia(spans["return"]["start"], spans["return"]["end"], "return;")
            or trivia(spans["return"]["start"], spans["return"]["end"], "return"))
        and trivia(spans["return"]["end"], spans["then_body"]["end"], "}"))


def early_return_stored_problem(evidence, source_hash, valid_span=None, *, ast_contract_version):
    """Validate every stored row; caller owns current primary/snapshot/live binding."""
    fields = {"status", "reason", "version", "scope", "offset_unit", "ast_contract_version", "source_hash",
        "records", "record_count", "omitted", "schema_authority", "type_compatibility", "runtime_proof",
        "mutation_proposed", "factory_semantics", "continuation_safety"}
    if (not isinstance(evidence, dict) or set(evidence) != fields
            or not isinstance(ast_contract_version, str) or not ast_contract_version
            or evidence.get("ast_contract_version") != ast_contract_version
            or evidence.get("version") != EARLY_RETURN_VERSION
            or evidence.get("scope") != "finite_named_import_factory_safeparse_success_binding_negative_return"
            or evidence.get("offset_unit") != "utf8_bytes"
            or any(evidence.get(key) != "not_established" for key in
                ("schema_authority", "type_compatibility", "factory_semantics", "continuation_safety"))
            or evidence.get("runtime_proof") != "not_performed" or evidence.get("mutation_proposed") is not False):
        return "stored_contract_invalid"
    records, status, omitted = evidence["records"], evidence["status"], evidence["omitted"]
    if (not isinstance(records, list) or len(records) > 64 or type(evidence["record_count"]) is not int
            or evidence["record_count"] != len(records) or type(omitted) is not int or omitted < 0
            or not isinstance(evidence["reason"], str)):
        return "stored_count_or_status_invalid"
    if status == "unavailable":
        return "producer_evidence_unavailable" if not records and not omitted and evidence["reason"] else "stored_unavailable_contract_invalid"
    if (evidence["source_hash"] != source_hash
            or not ((status == "observed" and not omitted and evidence["reason"] == "")
                or (status == "partial" and omitted > 0 and len(records) == 64 and evidence["reason"] == "record_budget_exceeded"))):
        return "stored_identity_or_coverage_invalid"
    seen = set()
    for record in records:
        if valid_span is None or not early_return_record_valid(record, valid_span):
            return "stored_record_or_coordinates_invalid"
        guard = record["spans"]["guard"]
        key = guard["start"], guard["end"]
        if key in seen:
            return "stored_duplicate_guard"
        seen.add(key)
    return ""
