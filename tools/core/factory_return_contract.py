"""Pure imported-root factory return syntax, never schema or runtime authority."""

FACTORY_RETURN_VERSION = "v5-sync-factory-return-callback-guard-source"
FACTORY_RETURN_ROLES = frozenset({"factory", "factory_binding", "body", "returned", "expression", "root",
    "root_import", "root_binding", "root_imported", "module"})


def factory_return_roles(record):
    methods = record.get("methods") if isinstance(record, dict) else None
    if not isinstance(methods, list) or not 1 <= len(methods) <= 8:
        return frozenset()
    roles = FACTORY_RETURN_ROLES | {f"{kind}_{index}" for index in range(len(methods))
        for kind in ("call", "method", "open", "close")}
    if "parameter_name" in record:
        roles |= {"parameter", "parameter_binding"}
        if record.get("parameter_form") == "parenthesized": roles |= {"parameters_open", "parameters_close"}
        if isinstance(record.get("spans"),dict) and "parameter_type" in record["spans"]: roles |= {"parameter_type"}
        uses = record.get("parameter_uses")
        if not isinstance(uses,list) or len(uses) > len(methods) * 4: return frozenset()
        for index,use in enumerate(uses):
            count = use.get("argument_count") if isinstance(use,dict) else None
            if type(count) is not int or not 1 <= count <= 4: return frozenset()
            roles |= {f"parameter_use_{index}"} | {f"use_{index}_argument_{i}" for i in range(count)}
        guards = record.get("callback_guard_reads")
        if not isinstance(guards,list) or len(guards) > len(methods) * 4: return frozenset()
        for index,guard in enumerate(guards):
            count = guard.get("argument_count") if isinstance(guard,dict) else None
            if type(count) is not int or not 1 <= count <= 4: return frozenset()
            roles |= {f"guard_{index}_{kind}" for kind in ("callback","body","branch","return","reference","close")}
            roles |= {f"guard_{index}_argument_{i}" for i in range(count)}
    return roles


def factory_parameter_coverage_valid(records):
    """One existing 64-row bound across all retained factories in this file."""
    kept = sum(len(row.get("parameter_uses",[])) for row in records)
    omitted = sum(row.get("parameter_uses_omitted",0) for row in records)
    guard_kept = sum(len(row.get("callback_guard_reads",[])) for row in records)
    guard_omitted = sum(row.get("callback_guard_reads_omitted",0) for row in records)
    return (kept <= 64 and (not omitted or kept == 64)
        and guard_kept <= 64 and (not guard_omitted or guard_kept == 64))


def _factory_callback_guards_valid(record,valid_span):
    guards,omitted,spans = record["callback_guard_reads"],record["callback_guard_reads_omitted"],record["spans"]
    if type(omitted) is not int or omitted < 0 or len(guards)+omitted > len(record["methods"])*4: return False
    trivia,seen = valid_span.trivia_between,set()
    simple = lambda s: (isinstance(s,str) and 0 < len(s) <= 128 and s.isascii() and s != "this"
        and (s[0].isalpha() or s[0] in "_$") and all(c.isalnum() or c in "_$" for c in s))
    for index,guard in enumerate(guards):
        if not isinstance(guard,dict) or set(guard)!={"call_index","argument_index","argument_count","callback_parameters"}: return False
        call,argument,count,parameters = (guard[k] for k in ("call_index","argument_index","argument_count","callback_parameters"))
        if (type(call) is not int or not 0 <= call < len(record["methods"]) or record["methods"][call]!="superRefine"
                or type(argument) is not int or not 0 <= argument < count or (call,argument) in seen
                or not isinstance(parameters,list) or len(parameters)>4 or not all(simple(p) for p in parameters)
                or len(set(parameters))!=len(parameters) or record["parameter_name"] in parameters): return False
        seen.add((call,argument)); prefix=f"guard_{index}_"
        callback,body,branch,returned,reference = (spans[prefix+k] for k in ("callback","body","branch","return","reference"))
        contains = lambda a,b: a["start"] <= b["start"] < b["end"] <= a["end"]
        if (callback!=spans[prefix+f"argument_{argument}"] or not contains(callback,body) or callback["end"]!=body["end"]
                or not contains(body,branch) or not contains(branch,reference) or not contains(branch,returned) or branch["end"]!=returned["end"]
                or not valid_span(reference,record["parameter_name"])
                or not trivia(callback["start"],body["start"],"("+",".join(parameters)+")=>")
                or not trivia(body["start"],branch["start"],"{")
                or not trivia(branch["start"],reference["start"],"if(") or not trivia(reference["end"],returned["start"],")")
                or not (trivia(returned["start"],returned["end"],"return;") or trivia(returned["start"],returned["end"],"return"))
                or not contains(body,spans[prefix+"close"]) or spans[prefix+"close"]["end"]!=body["end"]
                or returned["end"]>spans[prefix+"close"]["start"] or not valid_span(spans[prefix+"close"],"}")): return False
        previous=spans[f"open_{call}"]["end"]
        for i in range(count):
            arg=spans[prefix+f"argument_{i}"]
            if (not contains(spans[f"call_{call}"],arg) or not trivia(previous,arg["start"],"," if i else "")): return False
            previous=arg["end"]
        if not trivia(previous,spans[f"close_{call}"]["start"],""): return False
    return True


def _factory_parameter_uses_valid(record,valid_span):
    uses,omitted,spans = record["parameter_uses"],record["parameter_uses_omitted"],record["spans"]
    if type(omitted) is not int or omitted < 0 or len(uses) + omitted > len(record["methods"]) * 4: return False
    seen = set()
    for index,use in enumerate(uses):
        if not isinstance(use,dict) or set(use) != {"call_index","argument_index","argument_count"}: return False
        call,argument,count = (use[key] for key in ("call_index","argument_index","argument_count"))
        if (type(call) is not int or not 0 <= call < len(record["methods"])
                or type(argument) is not int or not 0 <= argument < count or (call,argument) in seen): return False
        seen.add((call,argument))
        reference = spans[f"parameter_use_{index}"]
        if (reference != spans[f"use_{index}_argument_{argument}"]
                or not valid_span(reference,record["parameter_name"])): return False
        previous = spans[f"open_{call}"]["end"]
        for i in range(count):
            arg = spans[f"use_{index}_argument_{i}"]
            if (not spans[f"call_{call}"]["start"] <= arg["start"] < arg["end"] <= spans[f"call_{call}"]["end"]
                    or not valid_span.trivia_between(previous,arg["start"],"," if i else "")): return False
            previous = arg["end"]
        if not valid_span.trivia_between(previous,spans[f"close_{call}"]["start"],""): return False
    return True


def _factory_binding_order_valid(record, valid_span):
    spans = record["spans"]
    binding, factory = spans["factory_binding"], spans["factory"]
    if record["factory_kind"] == "const_arrow":
        return binding["end"] <= factory["start"]
    return (record["factory_kind"] == "function_declaration"
        and factory["start"] < binding["start"] < binding["end"] <= spans["body"]["start"]
        and valid_span.trivia_between(factory["start"],binding["start"],"exportfunction"))


def factory_return_record_valid(record, valid_span):
    fields = {"factory_name", "factory_kind", "body_kind", "methods", "import_binding", "spans"}
    if not isinstance(record, dict) or set(record) not in (fields, fields | {"parameter_name", "parameter_form", "parameter_uses", "parameter_uses_omitted", "callback_guard_reads", "callback_guard_reads_omitted"}):
        return False
    simple = lambda value: (isinstance(value, str) and 0 < len(value) <= 128 and value.isascii()
        and (value[0].isalpha() or value[0] in "_$") and all(c.isalnum() or c in "_$" for c in value))
    spans, binding, roles = record["spans"], record["import_binding"], factory_return_roles(record)
    if (not roles or not isinstance(spans, dict) or set(spans) != roles
            or not all(valid_span(span) for span in spans.values()) or not simple(record["factory_name"])
            or record["body_kind"] not in ("expression", "block_final_return")
            or record["factory_kind"] not in ("const_arrow", "function_declaration")
            or record["factory_kind"] == "function_declaration" and record["body_kind"] != "block_final_return"
            or not isinstance(binding, dict) or set(binding) != {"source", "local_name", "imported_name", "kind", "type_only"}
            or binding["kind"] != "named" or binding["type_only"] is not False
            or not isinstance(binding["source"], str) or not 0 < len(binding["source"]) <= 180
            or not all(simple(binding[key]) for key in ("local_name", "imported_name"))
            or not all(simple(method) for method in record["methods"])):
        return False
    contains = lambda a,b: spans[a]["start"] <= spans[b]["start"] < spans[b]["end"] <= spans[a]["end"]
    if (not all(contains(a,b) for a,b in (("factory", "body"), ("body", "returned"), ("returned", "expression"),
                ("expression", "root"), ("root_import", "root_binding"), ("root_import", "root_imported"), ("root_import", "module")))
            or not spans["root_import"]["end"] <= min(spans["factory_binding"]["start"],spans["factory"]["start"])
            or not _factory_binding_order_valid(record,valid_span)
            or any(not valid_span(spans[role], text) for role,text in (("factory_binding", record["factory_name"]),
                ("root", binding["local_name"]), ("root_binding", binding["local_name"]), ("root_imported", binding["imported_name"])))
            or not valid_span(spans["module"], binding["source"], quoted=True)):
        return False
    trivia = valid_span.trivia_between
    if "parameter_name" in record:
        if (not simple(record["parameter_name"]) or record["parameter_name"] == "this" or not valid_span(spans["parameter_binding"],record["parameter_name"])
                or not contains("factory","parameter") or not contains("parameter","parameter_binding")
                or spans["parameter"]["start"] != spans["parameter_binding"]["start"]
                or spans["parameter"]["end"] > spans["body"]["start"]): return False
        if "parameter_type" in spans:
            if (not contains("parameter","parameter_type") or spans["parameter_type"]["end"] != spans["parameter"]["end"]
                    or not trivia(spans["parameter_binding"]["end"],spans["parameter_type"]["start"],":")): return False
        elif spans["parameter"] != spans["parameter_binding"]: return False
        if record["parameter_form"] == "bare_arrow":
            if (record["factory_kind"] != "const_arrow" or "parameter_type" in spans
                    or spans["parameter"]["start"] != spans["factory"]["start"]
                    or not trivia(spans["parameter"]["end"],spans["body"]["start"],"=>")): return False
        elif record["parameter_form"] == "parenthesized":
            if (not all(contains("factory",key) for key in ("parameters_open","parameters_close"))
                    or not valid_span(spans["parameters_open"],"(") or not valid_span(spans["parameters_close"],")")
                    or not trivia(spans["factory_binding"]["end"] if record["factory_kind"] == "function_declaration" else spans["factory"]["start"],spans["parameters_open"]["start"],"")
                    or not trivia(spans["parameters_open"]["end"],spans["parameter"]["start"],"")
                    or not trivia(spans["parameter"]["end"],spans["parameters_close"]["start"],"")
                    or spans["parameters_close"]["end"] > spans["body"]["start"]): return False
        else: return False
        if not _factory_parameter_uses_valid(record,valid_span) or not _factory_callback_guards_valid(record,valid_span): return False
    if record["body_kind"] == "expression":
        if not spans["body"] == spans["returned"] == spans["expression"]: return False
    elif (not trivia(spans["returned"]["start"], spans["expression"]["start"], "return")
            or not (trivia(spans["expression"]["end"], spans["returned"]["end"], ";")
                or trivia(spans["expression"]["end"], spans["returned"]["end"], ""))
            or not trivia(spans["returned"]["end"], spans["body"]["end"], "}")):
        return False
    previous = "root"
    for index, method in enumerate(record["methods"]):
        call, name, opening, closing = (f"{key}_{index}" for key in ("call", "method", "open", "close"))
        if (not contains("expression", call) or not all(contains(call, role) for role in (previous, name, opening, closing))
                or spans[call]["start"] != spans["root"]["start"] or spans[call]["end"] != spans[closing]["end"]
                or not spans[previous]["end"] <= spans[name]["start"] < spans[name]["end"] <= spans[opening]["start"] < spans[opening]["end"] <= spans[closing]["start"]
                or not valid_span(spans[name], method) or not valid_span(spans[opening], "(") or not valid_span(spans[closing], ")")
                or not trivia(spans[previous]["end"], spans[name]["start"], ".")
                or not trivia(spans[name]["end"], spans[opening]["start"], "")):
            return False
        previous = call
    return spans[previous] == spans["expression"]


def _factory_stored_header_problem(evidence, source_hash, *, ast_contract_version, arguments=False, resolver=False):
    """Shared transport envelope; record and parent semantics stay with owners."""
    fields = {"status", "reason", "version", "scope", "offset_unit", "ast_contract_version", "source_hash",
        "records", "record_count", "omitted", "schema_authority", "type_compatibility", "runtime_proof",
        "mutation_proposed", "factory_semantics", "argument_semantics"}
    if (not isinstance(evidence, dict) or set(evidence) != fields
            or not isinstance(ast_contract_version, str) or not ast_contract_version
            or evidence.get("ast_contract_version") != ast_contract_version
            or evidence.get("version") != (RESOLVER_FACTORY_VERSION if resolver else FACTORY_ARGUMENT_VERSION if arguments else FACTORY_RETURN_VERSION)
            or evidence.get("scope") != (RESOLVER_FACTORY_SCOPE if resolver else "finite_factory_call_argument_preceding_const_source_binding" if arguments
                else "finite_exported_sync_factory_imported_root_return_construction")
            or evidence.get("offset_unit") != "utf8_bytes"
            or any(evidence.get(key) != "not_established" for key in
                ("schema_authority", "type_compatibility", "factory_semantics", "argument_semantics"))
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
            or not ((status == "observed" and omitted == 0 and evidence["reason"] == "")
                or (status == "partial" and omitted > 0 and len(records) == 64 and evidence["reason"] == "record_budget_exceeded"))):
        return "stored_identity_or_coverage_invalid"
    return ""


RESOLVER_FACTORY_VERSION = "v1-zod-resolver-named-import-factory-source"
RESOLVER_FACTORY_SCOPE = "finite_named_zod_resolver_wrapped_imported_factory_call"
RESOLVER_FACTORY_ROLES = frozenset({"resolver_call", "resolver_ref", "resolver_import", "resolver_import_binding",
    "resolver_imported", "resolver_module", "factory_call", "factory_ref", "factory_import", "factory_import_binding"})


def resolver_factory_roles(record):
    count = record.get("argument_count") if isinstance(record, dict) else None
    return RESOLVER_FACTORY_ROLES | {f"argument_{i}" for i in range(count)} if type(count) is int and 0 <= count <= 4 else frozenset()


def resolver_factory_record_valid(record, valid_span):
    """Exact adapter/factory call source; argument expressions remain opaque."""
    if not isinstance(record, dict) or set(record) != {"argument_count", "resolver_local_name", "factory_local_name", "spans"}:
        return False
    simple = lambda value: (isinstance(value, str) and 0 < len(value) <= 128 and value.isascii()
        and (value[0].isalpha() or value[0] in "_$") and all(c.isalnum() or c in "_$" for c in value))
    spans, roles = record["spans"], resolver_factory_roles(record)
    if (not roles or not isinstance(spans, dict) or set(spans) != roles or not all(valid_span(s) for s in spans.values())
            or not all(simple(record[k]) for k in ("resolver_local_name", "factory_local_name"))):
        return False
    contains = lambda a,b: spans[a]["start"] <= spans[b]["start"] < spans[b]["end"] <= spans[a]["end"]
    if (not all(contains(a,b) for a,b in (("resolver_call","resolver_ref"),("resolver_call","factory_call"),
            ("factory_call","factory_ref"),("resolver_import","resolver_import_binding"),
            ("resolver_import","resolver_imported"),("resolver_import","resolver_module"),("factory_import","factory_import_binding")))
            or any(spans[k]["end"] > spans["resolver_call"]["start"] for k in ("resolver_import","factory_import"))
            or spans["resolver_call"]["start"] != spans["resolver_ref"]["start"]
            or spans["factory_call"]["start"] != spans["factory_ref"]["start"]
            or not all(valid_span(spans[k],text) for k,text in (("resolver_ref",record["resolver_local_name"]),
                ("resolver_import_binding",record["resolver_local_name"]),("resolver_imported","zodResolver"),
                ("factory_ref",record["factory_local_name"]),("factory_import_binding",record["factory_local_name"])))
            or not valid_span(spans["resolver_module"],"@hookform/resolvers/zod",quoted=True)):
        return False
    trivia = valid_span.trivia_between
    if (not trivia(spans["resolver_ref"]["end"],spans["factory_call"]["start"],"(")
            or not trivia(spans["factory_call"]["end"],spans["resolver_call"]["end"],")")):
        return False
    previous = spans["factory_ref"]["end"]
    for i in range(record["argument_count"]):
        argument = spans[f"argument_{i}"]
        if not contains("factory_call",f"argument_{i}") or not trivia(previous,argument["start"],"(" if not i else ","):
            return False
        previous = argument["end"]
    return trivia(previous,spans["factory_call"]["end"],")" if record["argument_count"] else "()")


def resolver_factory_stored_problem(evidence, source_hash, valid_span=None, *, ast_contract_version):
    """Check all retained calls before sampling, including undisplayed records."""
    problem = _factory_stored_header_problem(evidence,source_hash,ast_contract_version=ast_contract_version,resolver=True)
    if problem: return problem
    seen = set()
    for record in evidence["records"]:
        if valid_span is None or not resolver_factory_record_valid(record,valid_span): return "stored_record_or_coordinates_invalid"
        span = record["spans"]["resolver_call"]
        key = span["start"],span["end"]
        if key in seen: return "stored_duplicate_resolver_call"
        seen.add(key)
    return ""


def factory_return_stored_problem(evidence, source_hash, valid_span=None, *, ast_contract_version):
    """Validate the entire retained lane before selecting an indexed factory."""
    problem = _factory_stored_header_problem(evidence,source_hash,ast_contract_version=ast_contract_version)
    if problem: return problem
    seen, names = set(), set()
    for record in evidence["records"]:
        if valid_span is None or not factory_return_record_valid(record, valid_span):
            return "stored_record_or_coordinates_invalid"
        span = record["spans"]["factory"]
        key, name = (span["start"], span["end"]), record["factory_name"]
        if key in seen or name in names:
            return "stored_duplicate_factory"
        seen.add(key)
        names.add(name)
    if not factory_parameter_coverage_valid(evidence["records"]): return "stored_parameter_use_coverage_invalid"
    return ""


FACTORY_ARGUMENT_VERSION = "v2-sync-factory-call-argument-const-binding"
FACTORY_ARGUMENT_ROLES = frozenset({"factory", "factory_binding", "body", "returned", "expression", "call", "method", "open", "close",
    "argument", "reference", "declaration_list", "declaration", "const_keyword", "binding", "initializer"})


def factory_argument_roles(record):
    count = record.get("argument_count") if isinstance(record, dict) else None
    return FACTORY_ARGUMENT_ROLES | {f"argument_{i}" for i in range(count)} if type(count) is int and 1 <= count <= 4 else frozenset()


def factory_argument_parent_valid(record, parent):
    """The caller validates the complete parent lane before this exact join."""
    index = record.get("call_index")
    if (type(index) is not int or not 0 <= index < len(parent["methods"])
            or record["factory_name"] != parent["factory_name"] or record["factory_kind"] != parent["factory_kind"]
            or record["method_name"] != parent["methods"][index]):
        return False
    return (all(record["spans"][key] == parent["spans"][key] for key in ("factory", "factory_binding", "body", "returned", "expression"))
        and all(record["spans"][key] == parent["spans"][f"{key}_{index}"] for key in ("call", "method", "open", "close")))


def factory_argument_record_valid(record, valid_span):
    if not isinstance(record, dict) or set(record) != {"factory_name", "factory_kind", "method_name", "call_index", "argument_index", "argument_count", "kind", "name", "binding_scope", "spans"}:
        return False
    simple = lambda value: (isinstance(value, str) and 0 < len(value) <= 128 and value.isascii()
        and (value[0].isalpha() or value[0] in "_$") and all(c.isalnum() or c in "_$" for c in value))
    roles, spans = factory_argument_roles(record), record["spans"]
    if (not roles or not isinstance(spans, dict) or set(spans) != roles or not all(valid_span(s) for s in spans.values())
            or not all(simple(record[key]) for key in ("factory_name", "method_name", "name"))
            or record["factory_kind"] not in ("const_arrow","function_declaration")
            or type(record["call_index"]) is not int or not 0 <= record["call_index"] < 8
            or type(record["argument_index"]) is not int or not 0 <= record["argument_index"] < record["argument_count"]
            or record["kind"] not in ("direct_identifier", "object_shorthand") or record["binding_scope"] not in ("module_const", "factory_const")):
        return False
    contains = lambda a,b: spans[a]["start"] <= spans[b]["start"] < spans[b]["end"] <= spans[a]["end"]
    if (not all(contains(a,b) for a,b in (("factory", "body"), ("body", "returned"), ("returned", "expression"),
                ("expression", "call"), ("call", "method"), ("call", "open"), ("call", "close"), ("argument", "reference"),
                ("declaration_list", "const_keyword"), ("declaration_list", "declaration"), ("declaration", "binding"), ("declaration", "initializer")))
            or spans["argument"] != spans[f'argument_{record["argument_index"]}']
            or spans["call"]["end"] != spans["close"]["end"] or spans["method"]["end"] > spans["open"]["start"]
            or spans["declaration"]["start"] != spans["binding"]["start"] or spans["declaration"]["end"] != spans["initializer"]["end"]
            or spans["declaration_list"]["start"] != spans["const_keyword"]["start"] or spans["declaration_list"]["end"] != spans["declaration"]["end"]
            or not all(valid_span(spans[key],text) for key,text in (("factory_binding",record["factory_name"]), ("method",record["method_name"]),
                ("reference",record["name"]), ("binding",record["name"]), ("const_keyword","const"), ("open","("), ("close",")")))):
        return False
    if not _factory_binding_order_valid(record,valid_span): return False
    trivia = valid_span.trivia_between
    if (not trivia(spans["method"]["end"],spans["open"]["start"],"")
            or not trivia(spans["const_keyword"]["end"],spans["binding"]["start"],"")
            or not trivia(spans["binding"]["end"],spans["initializer"]["start"],"=")):
        return False
    previous = spans["open"]["end"]
    for i in range(record["argument_count"]):
        argument = spans[f"argument_{i}"]
        if not contains("call",f"argument_{i}") or not trivia(previous,argument["start"],"," if i else ""):
            return False
        previous = argument["end"]
    if not trivia(previous,spans["close"]["start"],""): return False
    if record["kind"] == "direct_identifier":
        if spans["argument"] != spans["reference"]: return False
    elif not (trivia(spans["argument"]["start"],spans["reference"]["start"],"{")
            and trivia(spans["reference"]["end"],spans["argument"]["end"],"}")):
        return False
    if record["binding_scope"] == "module_const":
        return spans["declaration_list"]["end"] <= min(spans["factory_binding"]["start"],spans["factory"]["start"])
    return contains("body","declaration_list") and spans["declaration_list"]["end"] <= spans["returned"]["start"]


def factory_argument_stored_problem(evidence, parent_evidence, source_hash, valid_span=None, *, ast_contract_version):
    """Check every retained argument and complete parent before display sampling."""
    problem = _factory_stored_header_problem(evidence,source_hash,ast_contract_version=ast_contract_version,arguments=True)
    if problem: return problem
    problem = factory_return_stored_problem(parent_evidence,source_hash,valid_span,ast_contract_version=ast_contract_version)
    if problem or parent_evidence["status"] != "observed": return "stored_argument_parent_unavailable_or_partial"
    parents = {(row["factory_name"],row["spans"]["factory"]["start"],row["spans"]["factory"]["end"]):row for row in parent_evidence["records"]}
    seen = set()
    for record in evidence["records"]:
        if valid_span is None or not factory_argument_record_valid(record,valid_span): return "stored_argument_record_or_coordinates_invalid"
        parent = parents.get((record["factory_name"],record["spans"]["factory"]["start"],record["spans"]["factory"]["end"]))
        if not parent or not factory_argument_parent_valid(record,parent): return "stored_argument_parent_mismatch"
        key = (record["spans"]["reference"]["start"],record["spans"]["reference"]["end"])
        if key in seen: return "stored_duplicate_argument_reference"
        seen.add(key)
    return ""
