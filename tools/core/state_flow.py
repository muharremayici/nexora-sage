from __future__ import annotations

import hashlib
from itertools import chain
from pathlib import PurePosixPath

from tools.core.state_flow_import_index import (
    import_index_file_identity_row, select_resolved_module_importers,
)

TRANSITIVE_HOOK_COVERAGE = "positive_top_level_callable_lexical_import_call_only_absence_unproven"


def summarize_state_flow_features(features) -> dict:
    values = list(features or [])
    query_keys = []
    query_key_refs = []
    query_key_dynamic = []
    mutation_keys = []
    mutation_key_refs = []
    mutation_key_dynamic = []
    has_tanstack_mutation = False
    client_actions = []
    zustand_consumers = []
    zustand_no_selector_calls = []
    zustand_broad_selector_calls = []
    react_external_store_consumers = []
    technologies = set()

    for feature in values:
        text = str(feature or "").strip()
        if not text:
            continue
        if text == "ZustandStore":
            technologies.add("zustand")
        elif text.startswith("Tech:selector:"):
            hook_name = text.split(":", 2)[2].strip()
            if hook_name == "useSyncExternalStore":
                react_external_store_consumers.append(hook_name)
                technologies.add("react-external-store")
                continue
            if hook_name:
                zustand_consumers.append(hook_name)
            technologies.add("zustand")
        elif text == "Tech:useSyncExternalStore":
            react_external_store_consumers.append("useSyncExternalStore")
            technologies.add("react-external-store")
        elif text.startswith("Zustand:NoSelector"):
            parts = text.split(":", 2)
            if len(parts) > 2 and parts[2].strip():
                hook_name = parts[2].strip()
                zustand_consumers.append(hook_name)
                zustand_no_selector_calls.append(hook_name)
            technologies.add("zustand")
        elif text.startswith("Zustand:BroadSelector"):
            parts = text.split(":", 2)
            if len(parts) > 2 and parts[2].strip():
                hook_name = parts[2].strip()
                zustand_consumers.append(hook_name)
                zustand_broad_selector_calls.append(hook_name)
            technologies.add("zustand")
        elif text.startswith("QueryKey:"):
            query_keys.append(text.split(":", 1)[1])
            technologies.add("tanstack-query")
        elif text.startswith("QueryKeyRef:"):
            query_key_refs.append(text.split(":", 1)[1])
            technologies.add("tanstack-query")
        elif text.startswith("QueryKeyDynamic:"):
            query_key_dynamic.append(text.split(":", 1)[1])
            technologies.add("tanstack-query")
        elif text.startswith("MutationKey:"):
            mutation_keys.append(text.split(":", 1)[1])
            technologies.add("tanstack-query")
        elif text.startswith("MutationKeyRef:"):
            mutation_key_refs.append(text.split(":", 1)[1])
            technologies.add("tanstack-query")
        elif text.startswith("MutationKeyDynamic:"):
            mutation_key_dynamic.append(text.split(":", 1)[1])
            technologies.add("tanstack-query")
        elif text == "TanStackMutation":
            has_tanstack_mutation = True
            technologies.add("tanstack-query")
        elif text.startswith("QueryClientAction:"):
            client_actions.append(text.split(":", 1)[1])
            technologies.add("tanstack-query")

    return {
        "has_zustand_store": "ZustandStore" in values,
        "query_keys": sorted(set(query_keys)),
        "query_key_refs": sorted(set(query_key_refs)),
        "query_key_dynamic": sorted(set(query_key_dynamic)),
        "mutation_keys": sorted(set(mutation_keys)),
        "mutation_key_refs": sorted(set(mutation_key_refs)),
        "mutation_key_dynamic": sorted(set(mutation_key_dynamic)),
        "has_tanstack_mutation": has_tanstack_mutation,
        "client_actions": sorted(set(client_actions)),
        "zustand_consumers": sorted(set(zustand_consumers)),
        "zustand_no_selector_calls": sorted(set(zustand_no_selector_calls)),
        "zustand_broad_selector_calls": sorted(set(zustand_broad_selector_calls)),
        "react_external_store_consumers": sorted(set(react_external_store_consumers)),
        "technologies": sorted(technologies),
    }


def _focus_relative_path(value: str) -> str:
    """Accept exact relative identity, never traversal, drives or glob expansion."""
    if not isinstance(value, str):
        return ""
    text = value.replace("\\", "/")
    parts = text.split("/")
    if (not text or text.startswith("/") or ":" in text
            or any(part in {"", ".", ".."} for part in parts)
            or any(ord(char) < 32 for char in text)
            or any(char in text for char in "*?")):
        return ""
    return PurePosixPath(text).as_posix()


def _focus_signal_context(record: dict, limit: int) -> dict:
    features = record.get("features")
    signals = summarize_state_flow_features(features if isinstance(features, list) else [])
    stored = record.get("state_flow")
    if isinstance(stored, dict):
        # Only consume the existing producer's known signal schema.
        for key, default in signals.items():
            value = stored.get(key, default)
            if isinstance(default, list) and isinstance(value, list):
                signals[key] = [item for item in value if isinstance(item, str)]
            elif isinstance(default, bool) and isinstance(value, bool):
                signals[key] = value
    omitted = {}
    for key, value in signals.items():
        if isinstance(value, list):
            omitted[key] = max(0, len(value) - limit)
            signals[key] = value[:limit]
    return {
        "status": "observed" if isinstance(stored, dict) or isinstance(features, list) else "unavailable",
        "evidence_class": "static_index_observation",
        "signals": signals, "omitted_signals": omitted,
    }


def _focus_symbol_context(row: dict, limit: int, attribution: str) -> dict:
    """Project one symbol's stored syntax without promoting references to edges."""
    context = _focus_signal_context(row, limit)
    context["attribution"] = attribution
    for output_key, source_key in (
        ("identifier_references", "dependencies"), ("import_references", "dependency_imports")
    ):
        values = row.get(source_key, row.get("dependencyImports") if source_key == "dependency_imports" else None)
        if isinstance(values, list):
            context[output_key] = {
                "status": "observed", "items": values[:limit],
                "returned": min(limit, len(values)), "omitted": max(0, len(values) - limit),
                "edge_class": "syntactic_reference_not_resolved_call_or_state_flow",
            }
        else:
            context[output_key] = {"status": "unavailable", "items": [], "omitted": None}
    return context


def _focus_symbol_rows(symbols, selector):
    """Use recorded members/candidates without inferring properties of call results."""
    for row in symbols:
        yield row, None, False
        if not isinstance(row, dict) or not isinstance(row.get("name"), str):
            continue
        if not selector.startswith(row["name"] + "."):
            continue
        members = row.get("member_details")
        if isinstance(members, list):
            for member in members:
                yield member, row, False
        evidence = row.get("initializer_member_evidence")
        if isinstance(evidence, dict) and evidence.get("status") == "syntax_observed":
            candidates = evidence.get("members")
            if isinstance(candidates, list):
                for member in candidates:
                    yield member, row, True


def _focus_import_candidates(record, row, file_index, limit, budget):
    """Join existing module evidence, not lexical bindings, calls or data flow."""
    references = row.get("dependency_imports", row.get("dependencyImports"))
    imports = record.get("import_records")
    result = {
        "status": "unavailable", "items": [], "returned": 0, "omitted": None,
        "visited_records": 0,
        "relation": "module_import_reference_candidate",
        "attribution": "selected_symbol_syntax_lexical_binding_unverified",
        "runtime_call": "not_established",
    }
    if not isinstance(references, list) or not isinstance(imports, list):
        result["reason"] = "missing_symbol_or_module_import_evidence"
        return result
    # One bounded pass over the owning file, not a per-edge repository scan.
    module_index = {}
    for entry in imports:
        if result["visited_records"] >= budget:
            result.update(status="incomplete_scan", reason="import_scan_budget_exhausted",
                          omitted=len(references))
            return result
        result["visited_records"] += 1
        if not isinstance(entry, dict):
            result.update(reason="malformed_module_import_evidence", omitted=len(references))
            return result
        key = (entry.get("raw_source"), entry.get("name"))
        if not all(isinstance(part, str) for part in key):
            result.update(reason="missing_module_import_identity", omitted=len(references))
            return result
        module_index.setdefault(key, []).append(entry)
    result["status"] = "observed"
    for reference in references[:limit]:
        if result["visited_records"] >= budget:
            result.update(status="incomplete_scan", reason="import_scan_budget_exhausted")
            break
        result["visited_records"] += 1
        candidate = {"status": "unresolved", "reason": "missing_import_binding_identity"}
        result["items"].append(candidate)
        if not isinstance(reference, dict):
            continue
        for key in ("source", "localName", "importedName", "kind"):
            value = reference.get(key)
            candidate[key] = value if isinstance(value, str) else None
        if not all(candidate[key] for key in ("source", "localName", "importedName", "kind")):
            continue
        if candidate["kind"] not in {"named", "default", "namespace"}:
            candidate["reason"] = "unsupported_import_kind"
            continue
        imported_key = candidate["localName"] if candidate["kind"] == "namespace" else candidate["importedName"]
        entries = module_index.get((candidate["source"], imported_key), [])
        if len(entries) != 1:
            candidate["reason"] = "ambiguous_module_import" if entries else "module_import_not_recorded"
            continue
        entry = entries[0]
        if entry.get("kind") != candidate["kind"] or entry.get("scope") != "top_level":
            candidate["reason"] = "type_only_or_incompatible_import_scope"
            continue
        resolved = entry.get("source")
        targets = file_index.get(resolved, []) if _focus_relative_path(resolved) else []
        if len(targets) != 1:
            candidate["reason"] = "ambiguous_target_file" if targets else "no_exact_same_project_target"
            continue
        candidate.update(status="target_candidate", reason="stored_module_resolution_only",
                         target=targets[0], source_binding="not_checked")
    result["returned"] = len(result["items"])
    result["omitted"] = len(references) - result["returned"]
    return result


def _focus_storage_direct_export(candidate, files, project, budget):
    """Bind a named import to one indexed direct variable export, not an adapter effect."""
    result = {"status": "unavailable", "reason": "module_candidate_unavailable",
              "relation": "named_import_direct_variable_export_candidate",
              "runtime_execution": "not_established", "source_binding": "not_checked",
              "visited_records": 0}
    target = candidate.get("target")
    reference = candidate.get("reference")
    if not isinstance(target, dict) or not isinstance(reference, dict):
        return result
    ref = target.get("atlas_ref")
    prefix, separator, rel = ref.partition("::") if isinstance(ref, str) else ("", "", "")
    if prefix != project or not separator or not _focus_relative_path(rel):
        result["reason"] = "malformed_target_file_identity"
        return result
    record = files.get(rel)
    if (not isinstance(record, dict) or target.get("source_hash") != record.get("hash")
            or not isinstance(target.get("source_hash"), str) or not target["source_hash"]
            or not isinstance(record.get("symbols"), list)):
        result["reason"] = "target_export_evidence_unavailable"
        return result
    name = reference.get("importedName")
    matches = []
    for symbol in record["symbols"]:
        if result["visited_records"] >= budget:
            result.update(status="incomplete_scan", reason="target_export_scan_budget_exhausted")
            return result
        result["visited_records"] += 1
        if not isinstance(symbol, dict):
            result["reason"] = "malformed_target_export_evidence"
            return result
        if symbol.get("name") == name:
            matches.append(symbol)
    if len(matches) != 1:
        result.update(status="ambiguous" if matches else "unresolved",
                      reason="ambiguous_target_export" if matches else "target_export_not_indexed")
        return result
    symbol = matches[0]
    if symbol.get("type") in {"ReExportedSymbol", "ProxyExport", "LocalReExport"}:
        result["reason"] = "reexport_or_alias_unresolved"
        return result
    if (symbol.get("type") != "Variable" or symbol.get("exported") is not True
            or symbol.get("export_kind") != "named"):
        result["reason"] = "export_not_direct_variable"
        return result
    line, end_line = symbol.get("line"), symbol.get("end_line")
    if not (type(line) is int and type(end_line) is int and 0 < line <= end_line):
        result["reason"] = "target_symbol_span_unavailable"
        return result
    result.update(status="target_candidate", reason="direct_declared_variable_export",
                  target={**target, "symbol": name, "start_line": line, "end_line": end_line})
    return result


def _focus_persist_storage_option(record, member, declaring, file_index, files, project, budget):
    """Join one parser-bound persist option to a module file, never to an effect."""
    result = {"status": "unavailable", "reason": "missing_parser_storage_option_evidence",
              "relation": "persist_storage_option_named_import_syntax",
              "runtime_execution": "not_established", "source_binding": "not_checked",
              "visited_records": 0}
    evidence = member.get("persist_storage_option_import_evidence")
    if evidence is None:
        return result
    if not isinstance(evidence, dict) or evidence.get("runtime_execution") != "not_established":
        result.update(status="incomplete_scan", reason="malformed_parser_storage_option_evidence")
        return result
    if evidence.get("status") == "unavailable" and isinstance(evidence.get("reason"), str):
        result["reason"] = evidence["reason"]
        return result
    reference = evidence.get("reference")
    line, end_line = evidence.get("line"), evidence.get("end_line")
    if (evidence.get("status") != "observed"
            or evidence.get("relation") != result["relation"]
            or not isinstance(reference, dict)
            or any(not isinstance(reference.get(key), str) or not reference[key]
                   for key in ("source", "localName", "importedName"))
            or reference.get("kind") != "named"
            or type(line) is not int or type(end_line) is not int
            or not (declaring["line"] <= line <= end_line <= declaring["end_line"])):
        result.update(status="incomplete_scan", reason="malformed_parser_storage_option_evidence")
        return result
    imports = _focus_import_candidates(
        record, {"dependency_imports": [reference]}, file_index, 1, budget)
    result["visited_records"] = imports["visited_records"]
    if imports["status"] == "incomplete_scan":
        result.update(status="incomplete_scan", reason=imports.get("reason", "import_scan_incomplete"))
    elif imports["items"]:
        candidate = imports["items"][0]
        result.update(status=candidate["status"], reason=candidate["reason"],
                      reference=reference, line=line, end_line=end_line)
        if candidate["status"] == "target_candidate":
            result["target"] = candidate["target"]
            result["export_candidate"] = _focus_storage_direct_export(
                result, files, project, budget - result["visited_records"])
            result["visited_records"] += result["export_candidate"]["visited_records"]
    else:
        result.update(status="unresolved", reason=imports.get("reason", "module_import_unavailable"))
    return result


def _focus_persist_hydration_option(member, declaring, budget):
    """Project one explicit persist skipHydration literal, never hydration execution."""
    result = {"status": "unavailable", "reason": "missing_parser_hydration_option_evidence",
              "relation": "persist_skip_hydration_literal_syntax",
              "runtime_execution": "not_established", "source_binding": "not_checked",
              "visited_records": 0}
    evidence = member.get("persist_hydration_option_evidence")
    if evidence is None:
        return result
    if budget < 1:
        result.update(status="incomplete_scan", reason="hydration_option_scan_budget_exhausted")
        return result
    result["visited_records"] = 1
    if not isinstance(evidence, dict) or evidence.get("runtime_execution") != "not_established":
        result.update(status="incomplete_scan", reason="malformed_parser_hydration_option_evidence")
        return result
    if evidence.get("status") == "unavailable" and isinstance(evidence.get("reason"), str) and evidence["reason"]:
        result["reason"] = evidence["reason"]
        return result
    line, end_line = evidence.get("line"), evidence.get("end_line")
    if (evidence.get("status") != "observed"
            or evidence.get("relation") != result["relation"]
            or type(evidence.get("skip_hydration")) is not bool
            or type(line) is not int or type(end_line) is not int
            or not (declaring["line"] <= line <= end_line <= declaring["end_line"])):
        result.update(status="incomplete_scan", reason="malformed_parser_hydration_option_evidence")
        return result
    result.update(status="source_candidate", reason="explicit_literal_option",
                  skip_hydration=evidence["skip_hydration"], line=line, end_line=end_line)
    return result


def _focus_import_calls(row, declaring, limit, budget):
    """Project parser-owned call sites, never execute or reconstruct bindings."""
    result = {"status": "unavailable", "items": [], "returned": 0, "omitted": None,
              "visited_records": 0, "binding_scope": "single_file_lexical_import",
              "runtime_execution": "not_established", "source_binding": "not_checked",
              "relation": "import_bound_call_syntax_not_execution"}
    evidence = row.get("import_call_evidence")
    if not isinstance(evidence, dict):
        result["reason"] = "missing_parser_call_evidence"
        return result
    if budget < 1:
        result.update(status="incomplete_scan", reason="call_scan_budget_exhausted")
        return result
    result["visited_records"] = 1
    if (evidence.get("status") not in {"observed", "unavailable", "not_applicable"}
            or evidence.get("binding_scope") != "single_file_lexical_import"
            or evidence.get("runtime_execution") != "not_established"
            or not isinstance(evidence.get("calls"), list)
            or not isinstance(evidence.get("limitations"), list)):
        result["reason"] = "malformed_parser_call_evidence"
        return result
    result["status"] = evidence["status"]
    result["limitations"] = evidence["limitations"][:limit]
    result["omitted_limitations"] = max(0, len(evidence["limitations"]) - limit)
    if evidence["status"] != "observed":
        return result
    span = row if type(row.get("line")) is int and type(row.get("end_line")) is int else declaring or {}
    calls = evidence["calls"]
    for call in calls[:limit]:
        if result["visited_records"] >= budget:
            result.update(status="incomplete_scan", reason="call_scan_budget_exhausted")
            break
        result["visited_records"] += 1
        if not (isinstance(call, dict)
                and all(isinstance(call.get(key), str) and call[key] for key in ("source", "localName", "importedName"))
                and call.get("kind") in {"named", "default", "namespace"}
                and (call.get("member") is None or (call.get("kind") in {"namespace", "named"}
                     and isinstance(call.get("member"), str) and call["member"]))
                and (call.get("first_literal_argument") is None
                     or isinstance(call.get("first_literal_argument"), str))
                and (call.get("nested_callable_depth") is None
                     or type(call.get("nested_callable_depth")) is int
                     and call["nested_callable_depth"] >= 0)
                and type(call.get("optional")) is bool
                and all(type(value) is int for value in (span.get("line"), span.get("end_line"), call.get("line"), call.get("end_line")))
                and 0 < span["line"] <= call["line"] <= call["end_line"] <= span["end_line"]):
            result.update(status="unavailable", reason="malformed_parser_call_site", items=[])
            break
        result["items"].append({key: call.get(key) for key in (
            "source", "localName", "importedName", "kind", "member",
            "first_literal_argument", "nested_callable_depth", "optional", "line", "end_line")})
    result["returned"] = len(result["items"])
    result["omitted"] = len(calls) - result["returned"]
    return result


def _focus_local_arrow_import_calls(record, row, limit, budget):
    """Preserve immediate local-arrow lexical ownership, never outer execution."""
    result = {"status": "unavailable", "items": [], "returned": 0, "omitted": None,
              "visited_records": 0, "source_binding": "not_checked",
              "relation": "local_arrow_named_import_call_syntax_not_outer_execution",
              "runtime_execution": "not_established", "outer_callable_execution": "not_established"}
    evidence = row.get("local_arrow_import_call_evidence")
    if not isinstance(evidence, dict):
        return {**result, "reason": "local_arrow_metadata_missing"}
    from tools.core.atlas_ast_contract import AST_CONTRACT_VERSION
    records = evidence.get("records")
    identity = evidence.get("source_hash")
    if (evidence.get("version") != "v1-immediate-const-arrow-named-import-call"
            or evidence.get("offset_unit") != "utf8_bytes"
            or evidence.get("ast_contract_version") != record.get("ast_contract_version")
            or evidence.get("ast_contract_version") != AST_CONTRACT_VERSION
            or not isinstance(identity, str) or len(identity) != 64
            or any(char not in "0123456789abcdef" for char in identity)
            or identity != record.get("hash")
            or not isinstance(record.get("parser_evidence"), dict)
            or record["parser_evidence"].get("status") != "observed"
            or evidence.get("runtime_execution") != "not_established"
            or not isinstance(records, list) or len(records) > 64
            or type(evidence.get("omitted")) is not int or evidence["omitted"] < 0):
        return {**result, "reason": "local_arrow_identity_or_contract_invalid"}
    if evidence.get("status") != "observed" or evidence["omitted"]:
        return {**result, "status": "incomplete_scan" if evidence.get("status") == "incomplete_scan" else "unavailable",
                "reason": "local_arrow_producer_incomplete", "omitted": len(records) + evidence["omitted"]}
    if len(records) > budget:
        return {**result, "status": "incomplete_scan", "reason": "local_arrow_scan_budget_exhausted",
                "omitted": len(records)}
    seen = set()
    for call in records:
        result["visited_records"] += 1
        spans = call.get("spans") if isinstance(call, dict) else None
        if (not isinstance(call, dict) or set(call) != {"source", "localName", "importedName", "kind", "member",
                "optional", "lexical_owner", "lexical_owner_kind", "spans"}
                or any(not isinstance(call[key], str) or not call[key] for key in
                       ("source", "localName", "importedName", "lexical_owner"))
                or call["kind"] != "named" or call["member"] is not None or call["optional"] is not False
                or call["lexical_owner_kind"] != "const_local_arrow"
                or not isinstance(spans, dict) or set(spans) != {"owner", "owner_binding", "body", "call", "callee"}
                or any(not isinstance(span, dict) or set(span) != {"start", "end", "line", "end_line"}
                       or any(type(value) is not int for value in span.values())
                       or not 0 <= span["start"] < span["end"] or not 1 <= span["line"] <= span["end_line"]
                       for span in spans.values())
                or type(row.get("start")) is not int or type(row.get("end")) is not int
                or not row["start"] <= spans["owner"]["start"] < spans["owner"]["end"] <= row["end"]
                or any(type(row.get(key)) is not int for key in ("line", "end_line"))
                or not row["line"] <= spans["owner"]["line"] <= spans["owner"]["end_line"] <= row["end_line"]
                or any(not spans[outer]["start"] <= spans[inner]["start"] < spans[inner]["end"] <= spans[outer]["end"]
                       for outer, inner in (("owner", "owner_binding"), ("owner", "body"), ("body", "call"), ("call", "callee")))
                or spans["owner_binding"]["end"] > spans["body"]["start"]
                or (spans["call"]["start"], spans["call"]["end"]) in seen):
            return {**result, "reason": "local_arrow_record_invalid", "items": [], "omitted": len(records)}
        seen.add((spans["call"]["start"], spans["call"]["end"]))
        if len(result["items"]) < limit:
            result["items"].append({**call, "line": spans["call"]["line"], "end_line": spans["call"]["end_line"]})
    return {**result, "status": "observed", "returned": len(result["items"]),
            "omitted": len(records) - len(result["items"]), "source_hash": identity,
            "ast_contract_version": evidence["ast_contract_version"]}


def _focus_local_arrow_sibling_calls(record, row, limit, budget):
    """Exact local lexical endpoints; never fabricate an indexed callee."""
    from tools.core.atlas_ast_contract import AST_CONTRACT_VERSION, _local_arrow_sibling_record_valid
    result = {"status": "unavailable", "items": [], "returned": 0, "omitted": None,
              "visited_records": 0, "source_binding": "not_checked",
              "relation": "same_block_local_sibling_call_syntax",
              "value_transfer": "not_established", "runtime_execution": "not_established"}
    evidence = row.get("local_arrow_sibling_call_evidence")
    if not isinstance(evidence, dict): return {**result, "reason": "local_sibling_metadata_missing"}
    records = evidence.get("records")
    identity = evidence.get("source_hash")
    if (set(evidence) - {"status", "version", "offset_unit", "ast_contract_version", "source_hash", "records", "omitted", "runtime_execution", "reason"}
            or evidence.get("version") != "v1-immediate-const-arrow-sibling-call"
            or evidence.get("offset_unit") != "utf8_bytes"
            or evidence.get("ast_contract_version") != AST_CONTRACT_VERSION
            or record.get("ast_contract_version") != AST_CONTRACT_VERSION
            or not isinstance(identity, str) or len(identity) != 64
            or any(char not in "0123456789abcdef" for char in identity) or identity != record.get("hash")
            or not isinstance(record.get("parser_evidence"), dict) or record["parser_evidence"].get("status") != "observed"
            or evidence.get("runtime_execution") != "not_established"
            or not isinstance(records, list) or len(records) > 64
            or type(evidence.get("omitted")) is not int or evidence["omitted"] < 0):
        return {**result, "reason": "local_sibling_identity_or_contract_invalid"}
    if evidence.get("status") != "observed" or evidence["omitted"] or "reason" in evidence:
        return {**result, "status": "incomplete_scan" if evidence.get("status") == "incomplete_scan" else "unavailable",
                "reason": "local_sibling_producer_incomplete", "omitted": len(records) + evidence["omitted"]}
    if len(records) + 1 > max(0, budget):
        return {**result, "status": "incomplete_scan", "reason": "local_sibling_scan_budget_exhausted", "omitted": len(records)}
    def valid_span(span):
        return (isinstance(span, dict) and set(span) == {"start", "end", "line", "end_line"}
                and all(type(value) is int for value in span.values())
                and type(record.get("size")) is int and type(record.get("loc")) is int
                and 0 <= span["start"] < span["end"] <= record["size"]
                and 1 <= span["line"] <= span["end_line"] <= record["loc"])
    seen = set()
    result["visited_records"] = 1
    for call in records:
        result["visited_records"] += 1
        if not _local_arrow_sibling_record_valid(call, row, valid_span):
            return {**result, "reason": "local_sibling_record_invalid", "items": [], "omitted": len(records)}
        key = (call["spans"]["call"]["start"], call["spans"]["call"]["end"])
        if key in seen:
            return {**result, "reason": "local_sibling_duplicate", "items": [], "omitted": len(records)}
        seen.add(key)
        if len(result["items"]) < max(0, limit): result["items"].append(call)
    return {**result, "status": "observed", "returned": len(result["items"]),
            "omitted": len(records) - len(result["items"]), "source_hash": identity,
            "ast_contract_version": evidence["ast_contract_version"]}


def _focus_call_result_property_bindings(record, row, limit, budget):
    """Selected local binding facts; never an inferred callable/dispatch edge."""
    from tools.core.atlas_ast_contract import AST_CONTRACT_VERSION, _call_result_property_binding_record_valid
    result = {"status": "unavailable", "items": [], "returned": 0, "omitted": None,
              "visited_records": 0, "source_binding": "not_checked",
              "relation": "direct_named_import_call_result_property_to_const_local_source_binding",
              "value_transfer": "not_established", "runtime_execution": "not_established",
              "callability": "not_established", "callback_activation": "not_established", "schema_validity": "not_established"}
    evidence = row.get("call_result_property_binding_evidence")
    if not isinstance(evidence, dict): return {**result, "reason": "call_result_binding_metadata_missing"}
    records, identity = evidence.get("records"), evidence.get("source_hash")
    if (set(evidence) - {"status", "version", "offset_unit", "ast_contract_version", "source_hash", "records", "omitted", "runtime_execution", "reason"}
            or evidence.get("version") != "v1-direct-named-import-const-object-binding"
            or evidence.get("offset_unit") != "utf8_bytes"
            or evidence.get("ast_contract_version") != AST_CONTRACT_VERSION
            or record.get("ast_contract_version") != AST_CONTRACT_VERSION
            or not isinstance(identity, str) or len(identity) != 64
            or any(char not in "0123456789abcdef" for char in identity) or identity != record.get("hash")
            or not isinstance(record.get("parser_evidence"), dict) or record["parser_evidence"].get("status") != "observed"
            or row.get("parser_kind") != "typescript_compiler_api"
            or row.get("type") not in {"Function", "Arrow", "Hook", "Component"}
            or evidence.get("runtime_execution") != "not_established"
            or not isinstance(records, list) or len(records) > 64
            or type(evidence.get("omitted")) is not int or evidence["omitted"] < 0):
        return {**result, "reason": "call_result_binding_identity_or_contract_invalid"}
    if evidence.get("status") != "observed" or evidence["omitted"] or "reason" in evidence:
        return {**result, "status": "incomplete_scan" if evidence.get("status") == "incomplete_scan" else "unavailable",
                "reason": "call_result_binding_producer_incomplete", "omitted": len(records) + evidence["omitted"]}
    if len(records) + 1 > max(0, budget):
        return {**result, "status": "incomplete_scan", "reason": "call_result_binding_scan_budget_exhausted", "omitted": len(records)}
    def valid_span(span):
        return (isinstance(span, dict) and set(span) == {"start", "end", "line", "end_line"}
                and all(type(value) is int for value in span.values())
                and type(record.get("size")) is int and type(record.get("loc")) is int
                and 0 <= span["start"] < span["end"] <= record["size"]
                and 1 <= span["line"] <= span["end_line"] <= record["loc"])
    seen = set()
    result["visited_records"] = 1
    for binding in records:
        result["visited_records"] += 1
        if not _call_result_property_binding_record_valid(binding, row, valid_span):
            return {**result, "reason": "call_result_binding_record_invalid", "items": [], "omitted": len(records)}
        site = binding["spans"]["binding_element"]
        unique = (site["start"], site["end"])
        if unique in seen:
            return {**result, "reason": "call_result_binding_duplicate", "items": [], "omitted": len(records)}
        seen.add(unique)
        if len(result["items"]) < max(0, limit): result["items"].append(binding)
    return {**result, "status": "observed", "returned": len(result["items"]),
            "omitted": len(records) - len(result["items"]), "source_hash": identity,
            "ast_contract_version": evidence["ast_contract_version"]}


def _focus_call_result_alias_invocations(record, row, limit, budget, *, _nested=False, _optional_rest=False, _optional=False):
    """Selected immediate call sites; no runtime callable/dispatch authority."""
    from tools.core.atlas_ast_contract import (AST_CONTRACT_VERSION, _call_result_alias_invocation_record_valid,
                                              _nested_callback_alias_record_valid, _optional_rest_alias_record_valid,
                                              _direct_optional_alias_record_valid)
    version = "v1-literal-object-arrow-const-property-alias-invocation" if _nested else "v1-immediate-const-property-alias-invocation"
    validator = _nested_callback_alias_record_valid if _nested else _call_result_alias_invocation_record_valid
    key = "call_result_nested_callback_alias_invocation_evidence" if _nested else "call_result_alias_invocation_evidence"
    if _optional:
        version = "v1-immediate-const-property-optional-alias-invocation"
        validator = _direct_optional_alias_record_valid
        key = "call_result_optional_alias_invocation_evidence"
    if _optional_rest:
        version = "v1-import-result-object-callback-optional-rest-alias"
        validator = _optional_rest_alias_record_valid
        key = "call_result_optional_rest_alias_invocation_evidence"
    result = {"status": "unavailable", "items": [], "returned": 0, "omitted": None,
              "visited_records": 0, "source_binding": "not_checked",
              "relation": ("const_call_result_property_alias_to_literal_object_arrow_identifier_invocation_source_association"
                           if _nested else "const_call_result_property_alias_to_immediate_identifier_invocation_source_association"),
              "value_transfer": "not_established", "runtime_execution": "not_established",
              "callability": "not_established", "callback_activation": "not_established", "schema_validity": "not_established"}
    if _optional:
        result["relation"] = "const_call_result_property_alias_to_immediate_optional_identifier_invocation_source_association"
    if _optional_rest:
        result["relation"] = "const_call_result_property_alias_to_callback_optional_rest_invocation_source_association"
    if _nested or _optional_rest:
        result.update(callback_registration="not_established", effective_object_property="not_established",
                      container_callee_identity="not_established", context_flags_scope="literal_syntax_not_effective_configuration")
    evidence = row.get(key)
    if not isinstance(evidence, dict): return {**result, "reason": "alias_invocation_metadata_missing"}
    records, identity = evidence.get("records"), evidence.get("source_hash")
    if (set(evidence) - {"status", "version", "offset_unit", "ast_contract_version", "source_hash", "records", "omitted", "runtime_execution", "reason"}
            or evidence.get("version") != version
            or evidence.get("offset_unit") != "utf8_bytes"
            or evidence.get("ast_contract_version") != AST_CONTRACT_VERSION
            or record.get("ast_contract_version") != AST_CONTRACT_VERSION
            or not isinstance(identity, str) or len(identity) != 64
            or any(char not in "0123456789abcdef" for char in identity) or identity != record.get("hash")
            or not isinstance(record.get("parser_evidence"), dict) or record["parser_evidence"].get("status") != "observed"
            or row.get("parser_kind") != "typescript_compiler_api"
            or row.get("type") not in {"Function", "Arrow", "Hook", "Component"}
            or evidence.get("runtime_execution") != "not_established"
            or not isinstance(records, list) or len(records) > 64
            or type(evidence.get("omitted")) is not int or evidence["omitted"] < 0):
        return {**result, "reason": "alias_invocation_identity_or_contract_invalid"}
    if evidence.get("status") != "observed" or evidence["omitted"] or "reason" in evidence:
        return {**result, "status": "incomplete_scan" if evidence.get("status") == "incomplete_scan" else "unavailable",
                "reason": "alias_invocation_producer_incomplete", "omitted": len(records) + evidence["omitted"]}
    if len(records) + 1 > max(0, budget):
        return {**result, "status": "incomplete_scan", "reason": "alias_invocation_scan_budget_exhausted", "omitted": len(records)}
    def valid_span(span):
        return (isinstance(span, dict) and set(span) == {"start", "end", "line", "end_line"}
                and all(type(value) is int for value in span.values())
                and type(record.get("size")) is int and type(record.get("loc")) is int
                and 0 <= span["start"] < span["end"] <= record["size"]
                and 1 <= span["line"] <= span["end_line"] <= record["loc"])
    seen = set()
    result["visited_records"] = 1
    for invocation in records:
        result["visited_records"] += 1
        if not validator(invocation, row, valid_span):
            return {**result, "reason": "alias_invocation_record_invalid", "items": [], "omitted": len(records)}
        site = invocation["spans"]["invocation"]
        unique = (site["start"], site["end"])
        if unique in seen:
            return {**result, "reason": "alias_invocation_duplicate", "items": [], "omitted": len(records)}
        seen.add(unique)
        if len(result["items"]) < max(0, limit): result["items"].append(invocation)
    return {**result, "status": "observed", "returned": len(result["items"]),
            "omitted": len(records) - len(result["items"]), "source_hash": identity,
            "ast_contract_version": evidence["ast_contract_version"]}


def _focus_parameter_config_callbacks(record, row, limit, budget):
    """Parameter provenance and optional/rest syntax, not callback execution."""
    from tools.core.atlas_ast_contract import (AST_CONTRACT_VERSION, PARAMETER_CONFIG_CALLBACK_VERSION,
                                              _parameter_config_callback_record_valid)
    result = {"status": "unavailable", "items": [], "returned": 0, "omitted": None,
              "visited_records": 0, "source_binding": "not_checked",
              "relation": "owner_parameter_property_to_callback_optional_rest_forwarding_source_association",
              "value_transfer": "not_established", "runtime_execution": "not_established",
              "callability": "not_established", "callback_activation": "not_established",
              "schema_validity": "not_established", "callback_registration": "not_established",
              "effective_object_property": "not_established", "container_callee_identity": "not_established",
              "context_flags_scope": "literal_syntax_not_effective_configuration"}
    evidence = row.get("parameter_config_callback_forwarding_evidence")
    if not isinstance(evidence, dict): return {**result, "reason": "parameter_callback_metadata_missing"}
    records, identity = evidence.get("records"), evidence.get("source_hash")
    if (set(evidence) - {"status", "version", "offset_unit", "ast_contract_version", "source_hash", "records", "omitted", "runtime_execution", "reason"}
            or evidence.get("version") != PARAMETER_CONFIG_CALLBACK_VERSION
            or evidence.get("offset_unit") != "utf8_bytes"
            or evidence.get("ast_contract_version") != AST_CONTRACT_VERSION
            or record.get("ast_contract_version") != AST_CONTRACT_VERSION
            or not isinstance(identity, str) or len(identity) != 64
            or any(char not in "0123456789abcdef" for char in identity) or identity != record.get("hash")
            or not isinstance(record.get("parser_evidence"), dict) or record["parser_evidence"].get("status") != "observed"
            or row.get("parser_kind") != "typescript_compiler_api"
            or row.get("type") not in {"Function", "Arrow", "Hook", "Component"}
            or evidence.get("runtime_execution") != "not_established"
            or not isinstance(records, list) or len(records) > 64
            or type(evidence.get("omitted")) is not int or evidence["omitted"] < 0):
        return {**result, "reason": "parameter_callback_identity_or_contract_invalid"}
    if evidence.get("status") != "observed" or evidence["omitted"] or "reason" in evidence:
        return {**result, "status": "incomplete_scan" if evidence.get("status") == "incomplete_scan" else "unavailable",
                "reason": "parameter_callback_producer_incomplete", "omitted": len(records) + evidence["omitted"]}
    if len(records) + 1 > max(0, budget):
        return {**result, "status": "incomplete_scan", "reason": "parameter_callback_scan_budget_exhausted", "omitted": len(records)}
    def valid_span(span):
        return (isinstance(span, dict) and set(span) == {"start", "end", "line", "end_line"}
                and all(type(value) is int for value in span.values())
                and type(record.get("size")) is int and type(record.get("loc")) is int
                and 0 <= span["start"] < span["end"] <= record["size"]
                and 1 <= span["line"] <= span["end_line"] <= record["loc"])
    seen = set()
    result["visited_records"] = 1
    for forwarding in records:
        result["visited_records"] += 1
        if not _parameter_config_callback_record_valid(forwarding, row, valid_span):
            return {**result, "reason": "parameter_callback_record_invalid", "items": [], "omitted": len(records)}
        site = forwarding["spans"]["invocation"]
        unique = (site["start"], site["end"])
        if unique in seen:
            return {**result, "reason": "parameter_callback_duplicate", "items": [], "omitted": len(records)}
        seen.add(unique)
        if len(result["items"]) < max(0, limit): result["items"].append(forwarding)
    return {**result, "status": "observed", "returned": len(result["items"]),
            "omitted": len(records) - len(result["items"]), "source_hash": identity,
            "ast_contract_version": evidence["ast_contract_version"]}


def _focus_returned_local_callbacks(record, row, limit, budget):
    """Selected owner's local source endpoints, not a callback execution graph."""
    from tools.core.atlas_ast_contract import AST_CONTRACT_VERSION, _returned_local_callback_record_valid
    result = {"status": "unavailable", "items": [], "returned": 0, "omitted": None,
              "visited_records": 0, "source_binding": "not_checked",
              "relation": "local_usecallback_to_returned_shorthand_source_association",
              "value_transfer": "not_established", "runtime_execution": "not_established",
              "callback_activation": "not_established", "schema_validity": "not_established"}
    evidence = row.get("returned_local_callback_evidence")
    if not isinstance(evidence, dict): return {**result, "reason": "returned_callback_metadata_missing"}
    records, identity = evidence.get("records"), evidence.get("source_hash")
    if (set(evidence) - {"status", "version", "offset_unit", "ast_contract_version", "source_hash", "records", "omitted", "runtime_execution", "reason"}
            or evidence.get("version") != "v1-local-react-usecallback-returned-shorthand"
            or evidence.get("offset_unit") != "utf8_bytes"
            or evidence.get("ast_contract_version") != AST_CONTRACT_VERSION
            or record.get("ast_contract_version") != AST_CONTRACT_VERSION
            or not isinstance(identity, str) or len(identity) != 64
            or any(char not in "0123456789abcdef" for char in identity) or identity != record.get("hash")
            or not isinstance(record.get("parser_evidence"), dict) or record["parser_evidence"].get("status") != "observed"
            or row.get("parser_kind") != "typescript_compiler_api"
            or row.get("type") not in {"Function", "Arrow", "Hook", "Component"}
            or evidence.get("runtime_execution") != "not_established"
            or not isinstance(records, list) or len(records) > 64
            or type(evidence.get("omitted")) is not int or evidence["omitted"] < 0):
        return {**result, "reason": "returned_callback_identity_or_contract_invalid"}
    if evidence.get("status") != "observed" or evidence["omitted"] or "reason" in evidence:
        return {**result, "status": "incomplete_scan" if evidence.get("status") == "incomplete_scan" else "unavailable",
                "reason": "returned_callback_producer_incomplete", "omitted": len(records) + evidence["omitted"]}
    if len(records) + 1 > max(0, budget):
        return {**result, "status": "incomplete_scan", "reason": "returned_callback_scan_budget_exhausted", "omitted": len(records)}
    def valid_span(span):
        return (isinstance(span, dict) and set(span) == {"start", "end", "line", "end_line"}
                and all(type(value) is int for value in span.values())
                and type(record.get("size")) is int and type(record.get("loc")) is int
                and 0 <= span["start"] < span["end"] <= record["size"]
                and 1 <= span["line"] <= span["end_line"] <= record["loc"])
    seen = set()
    result["visited_records"] = 1
    for callback in records:
        result["visited_records"] += 1
        if not _returned_local_callback_record_valid(callback, row, valid_span):
            return {**result, "reason": "returned_callback_record_invalid", "items": [], "omitted": len(records)}
        site = callback["spans"]["property"]
        unique = (site["start"], site["end"])
        if unique in seen:
            return {**result, "reason": "returned_callback_duplicate", "items": [], "omitted": len(records)}
        seen.add(unique)
        if len(result["items"]) < max(0, limit): result["items"].append(callback)
    return {**result, "status": "observed", "returned": len(result["items"]),
            "omitted": len(records) - len(result["items"]), "source_hash": identity,
            "ast_contract_version": evidence["ast_contract_version"]}


def _focus_returned_callback_parameter_optional(record, row, limit, budget):
    """Bounded source syntax inside a returned callback; never callable/value proof."""
    from tools.core.atlas_ast_contract import (AST_CONTRACT_VERSION, RETURNED_PARAMETER_OPTIONAL_VERSION,
                                            _returned_parameter_optional_record_valid, _returned_parameter_anchor)
    result = {"status": "unavailable", "items": [], "returned": 0, "omitted": None, "visited_records": 0,
              "source_binding": "not_checked",
              "relation": "returned_callback_parameter_property_to_awaited_optional_call_source_association",
              "runtime_execution": "not_established", "value_transfer": "not_established", "callability": "not_established",
              "callback_activation": "not_established", "schema_validity": "not_established"}
    evidence = row.get("returned_callback_parameter_optional_call_evidence")
    if not isinstance(evidence, dict): return {**result, "reason": "returned_parameter_metadata_missing"}
    records, identity = evidence.get("records"), evidence.get("source_hash")
    if (set(evidence) - {"status", "version", "offset_unit", "ast_contract_version", "source_hash", "records", "omitted", "runtime_execution", "reason"}
            or evidence.get("version") != RETURNED_PARAMETER_OPTIONAL_VERSION or evidence.get("offset_unit") != "utf8_bytes"
            or evidence.get("ast_contract_version") != AST_CONTRACT_VERSION or identity != record.get("hash")
            or evidence.get("runtime_execution") != "not_established"
            or not isinstance(records, list) or len(records) > 64
            or type(evidence.get("omitted")) is not int or evidence["omitted"] < 0):
        return {**result, "reason": "returned_parameter_identity_or_contract_invalid"}
    if evidence.get("status") != "observed" or evidence["omitted"] or "reason" in evidence:
        return {**result, "status": "incomplete_scan" if evidence.get("status") == "incomplete_scan" else "unavailable",
                "reason": "returned_parameter_producer_incomplete", "omitted": len(records) + evidence["omitted"]}
    anchor = _focus_returned_local_callbacks(record, row, 64, budget)
    result["visited_records"] = anchor["visited_records"]
    if anchor["status"] != "observed" or anchor["omitted"]:
        return {**result, "status": anchor["status"], "reason": "returned_parameter_complete_anchor_unavailable"}
    if result["visited_records"] + len(records) + 1 > max(0, budget):
        return {**result, "status": "incomplete_scan", "reason": "returned_parameter_scan_budget_exhausted", "omitted": len(records)}
    def valid_span(span):
        return (isinstance(span, dict) and set(span) == {"start", "end", "line", "end_line"}
                and all(type(value) is int for value in span.values())
                and type(record.get("size")) is int and type(record.get("loc")) is int
                and 0 <= span["start"] < span["end"] <= record["size"]
                and 1 <= span["line"] <= span["end_line"] <= record["loc"])
    anchors = {r["spans"]["property"]["start"]: r for r in anchor["items"]}
    seen = set()
    result["visited_records"] += 1
    for item in records:
        result["visited_records"] += 1
        if (not _returned_parameter_optional_record_valid(item, row, valid_span)
                or anchors.get(item["spans"]["property"]["start"]) != _returned_parameter_anchor(item)):
            return {**result, "reason": "returned_parameter_record_or_anchor_invalid", "items": [], "omitted": len(records)}
        unique = tuple(item["spans"][role]["start"] for role in ("declaration", "callback_binding_element", "invocation"))
        if unique in seen:
            return {**result, "reason": "returned_parameter_duplicate", "items": [], "omitted": len(records)}
        seen.add(unique)
        if len(result["items"]) < max(0, limit): result["items"].append(item)
    return {**result, "status": "observed", "returned": len(result["items"]),
            "omitted": len(records) - len(result["items"]), "source_hash": identity,
            "ast_contract_version": evidence["ast_contract_version"]}


def _focus_returned_callback_parameter_context(record, row, limit, budget):
    """Independently versioned contextual source facts with charged anchor work."""
    from tools.core.atlas_ast_contract import (AST_CONTRACT_VERSION, RETURNED_PARAMETER_CONTEXT_VERSION,
        _returned_parameter_context_record_valid, _returned_parameter_anchor)
    result = {"status": "unavailable", "items": [], "returned": 0, "omitted": None, "visited_records": 0,
        "source_binding": "not_checked", "relation": "returned_callback_parameter_property_to_contextual_optional_call_source_association",
        "runtime_execution": "not_established", "value_transfer": "not_established", "callability": "not_established",
        "callback_activation": "not_established", "schema_validity": "not_established"}
    evidence = row.get("returned_callback_parameter_context_call_evidence")
    if not isinstance(evidence, dict): return {**result, "reason": "returned_context_metadata_missing"}
    records, identity = evidence.get("records"), evidence.get("source_hash")
    if (set(evidence) - {"status", "version", "offset_unit", "ast_contract_version", "source_hash", "records", "omitted", "runtime_execution", "reason"}
            or evidence.get("version") != RETURNED_PARAMETER_CONTEXT_VERSION or evidence.get("offset_unit") != "utf8_bytes"
            or evidence.get("ast_contract_version") != AST_CONTRACT_VERSION or identity != record.get("hash")
            or evidence.get("runtime_execution") != "not_established" or not isinstance(records, list) or len(records) > 64
            or type(evidence.get("omitted")) is not int or evidence["omitted"] < 0):
        return {**result, "reason": "returned_context_identity_or_contract_invalid"}
    if evidence.get("status") != "observed" or evidence["omitted"] or "reason" in evidence:
        return {**result, "status": "incomplete_scan" if evidence.get("status") == "incomplete_scan" else "unavailable",
            "reason": "returned_context_producer_incomplete", "omitted": len(records) + evidence["omitted"]}
    anchor = _focus_returned_local_callbacks(record, row, 64, budget)
    result["visited_records"] = anchor["visited_records"]
    if anchor["status"] != "observed" or anchor["omitted"]:
        return {**result, "status": anchor["status"], "reason": "returned_context_complete_anchor_unavailable"}
    if result["visited_records"] + len(records) + 1 > max(0, budget):
        return {**result, "status": "incomplete_scan", "reason": "returned_context_scan_budget_exhausted", "omitted": len(records)}
    def valid_span(span):
        return (isinstance(span, dict) and set(span) == {"start", "end", "line", "end_line"}
            and all(type(value) is int for value in span.values()) and type(record.get("size")) is int and type(record.get("loc")) is int
            and 0 <= span["start"] < span["end"] <= record["size"] and 1 <= span["line"] <= span["end_line"] <= record["loc"])
    anchors = {r["spans"]["property"]["start"]: r for r in anchor["items"]}
    seen = set()
    result["visited_records"] += 1
    for item in records:
        result["visited_records"] += 1
        if (not _returned_parameter_context_record_valid(item, row, valid_span)
                or anchors.get(item["spans"]["property"]["start"]) != _returned_parameter_anchor(item)):
            return {**result, "reason": "returned_context_record_or_anchor_invalid", "items": [], "omitted": len(records)}
        unique = tuple(item["spans"][role]["start"] for role in ("declaration", "callback_binding_element", "invocation"))
        if unique in seen: return {**result, "reason": "returned_context_duplicate", "items": [], "omitted": len(records)}
        seen.add(unique)
        if len(result["items"]) < max(0, limit): result["items"].append(item)
    return {**result, "status": "observed", "returned": len(result["items"]), "omitted": len(records) - len(result["items"]),
        "source_hash": identity, "ast_contract_version": evidence["ast_contract_version"]}


def _focus_returned_callback_parameter_unbraced(record, row, limit, budget):
    """Finite source scope; zero rows/truncation is not whole-function absence."""
    from tools.core.atlas_ast_contract import (AST_CONTRACT_VERSION, RETURNED_PARAMETER_UNBRACED_VERSION,
        _returned_parameter_unbraced_record_valid, _returned_parameter_anchor)
    result = {"status": "unavailable", "items": [], "returned": 0, "omitted": None, "visited_records": 0,
        "source_binding": "not_checked", "relation": "returned_callback_parameter_property_to_unbraced_optional_call_source_association",
        "coverage": "finite_source_grammar_only", "runtime_execution": "not_established", "value_transfer": "not_established",
        "callability": "not_established", "callback_activation": "not_established", "schema_validity": "not_established"}
    evidence = row.get("returned_callback_parameter_unbraced_if_call_evidence")
    if not isinstance(evidence, dict): return {**result, "reason": "returned_unbraced_metadata_missing"}
    records, identity = evidence.get("records"), evidence.get("source_hash")
    if (set(evidence) - {"status", "version", "offset_unit", "ast_contract_version", "source_hash", "records", "omitted", "runtime_execution", "coverage", "reason"}
            or evidence.get("version") != RETURNED_PARAMETER_UNBRACED_VERSION or evidence.get("offset_unit") != "utf8_bytes"
            or evidence.get("ast_contract_version") != AST_CONTRACT_VERSION or identity != record.get("hash")
            or evidence.get("coverage") != result["coverage"] or evidence.get("runtime_execution") != "not_established"
            or not isinstance(records, list) or len(records) > 64 or type(evidence.get("omitted")) is not int or evidence["omitted"] < 0):
        return {**result, "reason": "returned_unbraced_identity_or_contract_invalid"}
    if evidence.get("status") != "observed" or evidence["omitted"] or "reason" in evidence:
        return {**result, "status": "incomplete_scan" if evidence.get("status") == "incomplete_scan" else "unavailable",
            "reason": "returned_unbraced_producer_incomplete", "omitted": len(records) + evidence["omitted"]}
    anchor = _focus_returned_local_callbacks(record, row, 64, budget)
    result["visited_records"] = anchor["visited_records"]
    if anchor["status"] != "observed" or anchor["omitted"]:
        return {**result, "status": anchor["status"], "reason": "returned_unbraced_complete_anchor_unavailable"}
    if result["visited_records"] + len(records) + 1 > max(0, budget):
        return {**result, "status": "incomplete_scan", "reason": "returned_unbraced_scan_budget_exhausted", "omitted": len(records)}
    def valid_span(span):
        return (isinstance(span, dict) and set(span) == {"start", "end", "line", "end_line"}
            and all(type(v) is int for v in span.values()) and type(record.get("size")) is int and type(record.get("loc")) is int
            and 0 <= span["start"] < span["end"] <= record["size"] and 1 <= span["line"] <= span["end_line"] <= record["loc"])
    anchors = {r["spans"]["property"]["start"]: r for r in anchor["items"]}
    seen = set(); result["visited_records"] += 1
    for item in records:
        result["visited_records"] += 1
        if (not _returned_parameter_unbraced_record_valid(item, row, valid_span)
                or anchors.get(item["spans"]["property"]["start"]) != _returned_parameter_anchor(item)):
            return {**result, "reason": "returned_unbraced_record_or_anchor_invalid", "items": [], "omitted": len(records)}
        unique = tuple(item["spans"][key]["start"] for key in ("declaration", "callback_binding_element", "invocation"))
        if unique in seen: return {**result, "reason": "returned_unbraced_duplicate", "items": [], "omitted": len(records)}
        seen.add(unique)
        if len(result["items"]) < max(0, limit): result["items"].append(item)
    return {**result, "status": "observed", "returned": len(result["items"]), "omitted": len(records) - len(result["items"]),
        "source_hash": identity, "ast_contract_version": evidence["ast_contract_version"]}


def _focus_local_arrow_result_uses(record, row, limit, budget, calls, callees):
    """Caller property/argument syntax; reuse settled callee orientation only."""
    from tools.core.atlas_ast_contract import AST_CONTRACT_VERSION, _local_arrow_result_use_record_valid
    result = {"status": "unavailable", "items": [], "returned": 0, "omitted": None,
              "visited_records": 0, "source_binding": "not_checked",
              "relation": "guarded_await_result_property_to_call_argument_syntax",
              "promise_resolution": "not_established", "caller_state_binding": "not_established",
              "schema_authority": "not_established", "runtime_execution": "not_established"}
    evidence = row.get("local_arrow_result_use_evidence")
    if not isinstance(evidence, dict):
        return {**result, "reason": "local_result_use_metadata_missing"}
    records = evidence.get("records")
    identity = evidence.get("source_hash")
    if (set(evidence) - {"status", "version", "offset_unit", "ast_contract_version", "source_hash",
                        "records", "omitted", "runtime_execution", "reason"}
            or evidence.get("version") != "v1-immediate-async-const-arrow-guarded-result-argument"
            or evidence.get("offset_unit") != "utf8_bytes"
            or evidence.get("ast_contract_version") != record.get("ast_contract_version")
            or evidence.get("ast_contract_version") != AST_CONTRACT_VERSION
            or not isinstance(identity, str) or len(identity) != 64
            or any(char not in "0123456789abcdef" for char in identity) or identity != record.get("hash")
            or not isinstance(record.get("parser_evidence"), dict)
            or record["parser_evidence"].get("status") != "observed"
            or evidence.get("runtime_execution") != "not_established"
            or not isinstance(records, list) or len(records) > 64
            or type(evidence.get("omitted")) is not int or evidence["omitted"] < 0):
        return {**result, "reason": "local_result_use_identity_or_contract_invalid"}
    if evidence.get("status") != "observed" or evidence["omitted"] or "reason" in evidence:
        return {**result, "status": "incomplete_scan" if evidence.get("status") == "incomplete_scan" else "unavailable",
                "reason": "local_result_use_producer_incomplete", "omitted": len(records) + evidence["omitted"]}
    call_rows = calls.get("items", []) if calls.get("status") == "observed" else []
    candidate_rows = callees.get("items", [])
    if (not isinstance(call_rows, list) or not isinstance(candidate_rows, list)
            or len(call_rows) > 64 or len(candidate_rows) > 64):
        return {**result, "reason": "local_result_use_orientation_invalid"}
    if len(records) + len(call_rows) + len(candidate_rows) > max(0, budget):
        return {**result, "status": "incomplete_scan", "reason": "local_result_use_scan_budget_exhausted",
                "omitted": len(records)}
    roles = ("owner", "owner_binding", "body", "call", "callee")
    def span_key(spans):
        if not isinstance(spans, dict) or set(spans) != set(roles):
            return None
        if any(not isinstance(spans[k], dict) or any(type(spans[k].get(v)) is not int
               for v in ("start", "end", "line", "end_line")) for k in roles):
            return None
        return tuple(spans[k][v] for k in roles for v in ("start", "end", "line", "end_line"))
    bound_calls, orientations = {}, {}
    for call in call_rows:
        result["visited_records"] += 1
        if not isinstance(call, dict):
            continue
        key = span_key(call.get("spans"))
        if key is not None:
            identity_key = tuple(call.get(k) for k in ("source", "localName", "importedName", "kind", "lexical_owner", "lexical_owner_kind"))
            if all(isinstance(value, str) for value in identity_key):
                bound_calls.setdefault((identity_key, key), []).append(call)
    for candidate in candidate_rows:
        result["visited_records"] += 1
        if not isinstance(candidate, dict):
            continue
        key = span_key(candidate.get("caller_spans"))
        names = tuple(candidate.get(k) for k in ("local_name", "imported_name", "lexical_owner", "lexical_owner_kind"))
        if key is not None and all(isinstance(value, str) for value in names):
            orientations.setdefault((names, key), []).append(candidate)
    def valid_span(span):
        return (isinstance(span, dict) and set(span) == {"start", "end", "line", "end_line"}
                and all(type(value) is int for value in span.values())
                and type(record.get("size")) is int and type(record.get("loc")) is int
                and 0 <= span["start"] < span["end"] <= record["size"]
                and 1 <= span["line"] <= span["end_line"] <= record["loc"])
    seen = set()
    for usage in records:
        result["visited_records"] += 1
        if not _local_arrow_result_use_record_valid(usage, row, valid_span):
            return {**result, "reason": "local_result_use_record_invalid", "items": [], "omitted": len(records)}
        spans = usage["spans"]
        key = (spans["call"]["start"], spans["call"]["end"])
        if (key in seen or any(type(row.get(k)) is not int for k in ("line", "end_line"))
                or not row["line"] <= spans["owner"]["line"] <= spans["owner"]["end_line"] <= row["end_line"]):
            return {**result, "reason": "local_result_use_record_invalid", "items": [], "omitted": len(records)}
        seen.add(key)
        if len(result["items"]) >= limit:
            continue
        call_spans = {k: spans[k] for k in ("owner", "owner_binding", "body", "call", "callee")}
        key = span_key(call_spans)
        identity_key = tuple(usage[k] for k in ("source", "localName", "importedName", "kind", "lexical_owner", "lexical_owner_kind"))
        matches = bound_calls.get((identity_key, key), [])
        names = tuple(usage[k] for k in ("localName", "importedName", "lexical_owner", "lexical_owner_kind"))
        candidates = orientations.get((names, key), []) if len(matches) == 1 else []
        association = {"status": "unresolved", "reason": "exact_call_orientation_unavailable"}
        if len(candidates) == 1 and candidates[0].get("status") == "target_candidate":
            candidate = candidates[0]
            association = {k: candidate[k] for k in ("status", "target", "target_context_index",
                           "source_binding", "endpoint_content_binding") if k in candidate}
        result["items"].append({**usage, "callee_orientation": association})
    return {**result, "status": "observed", "returned": len(result["items"]),
            "omitted": len(records) - len(result["items"]), "source_hash": identity,
            "ast_contract_version": evidence["ast_contract_version"]}


def _focus_zustand_setter_calls(row, limit, budget):
    """Project only parser-owned calls to a supported factory setter parameter."""
    result = {"status": "unavailable", "items": [], "returned": 0, "omitted": None,
              "visited_records": 0, "binding_scope": "single_file_lexical_store_factory_parameter",
              "runtime_execution": "not_established", "source_binding": "not_checked",
              "factory_form": "not_recorded", "middleware_form": "not_recorded",
              "factory_api": "not_recorded",
              "relation": "lexically_bound_zustand_setter_call_syntax_not_state_transition"}
    evidence = row.get("zustand_setter_call_evidence")
    if not isinstance(evidence, dict):
        result["reason"] = "missing_action_setter_evidence"
        return result
    if budget < 1:
        result.update(status="incomplete_scan", reason="setter_scan_budget_exhausted")
        return result
    result["visited_records"] = 1
    if (evidence.get("status") not in {"observed", "unavailable", "not_applicable"}
            or evidence.get("binding_scope") != "single_file_lexical_store_factory_parameter"
            or evidence.get("runtime_execution") != "not_established"
            or not isinstance(evidence.get("calls"), list)
            or not isinstance(evidence.get("limitations"), list)
            or any(not isinstance(item, str) for item in evidence["limitations"])
            or (("factory_form" in evidence) != ("middleware_form" in evidence))
            or ("factory_form" in evidence and evidence["factory_form"] not in {"direct", "curried"})
            or ("factory_api" in evidence and evidence["factory_api"] not in {"react_bound_hook", "vanilla_store"})
            or ("middleware_form" in evidence and evidence["middleware_form"] not in {"none", "persist"})):
        result["reason"] = "malformed_action_setter_evidence"
        return result
    result["status"] = evidence["status"]
    result["factory_form"] = evidence.get("factory_form", "not_recorded")
    result["middleware_form"] = evidence.get("middleware_form", "not_recorded")
    result["factory_api"] = evidence.get("factory_api", "not_recorded")
    result["limitations"] = evidence["limitations"][:limit]
    result["omitted_limitations"] = max(0, len(evidence["limitations"]) - limit)
    if evidence["status"] != "observed":
        return result
    remaining_keys = limit
    for call in evidence["calls"][:limit]:
        if result["visited_records"] >= budget:
            result.update(status="incomplete_scan", reason="setter_scan_budget_exhausted")
            break
        result["visited_records"] += 1
        if not (isinstance(call, dict)
                and isinstance(call.get("parameter"), str) and call["parameter"]
                and type(call.get("optional")) is bool
                and all(type(value) is int for value in (
                    row.get("line"), row.get("end_line"), call.get("line"), call.get("end_line")))
                and 0 < row["line"] <= call["line"] <= call["end_line"] <= row["end_line"]):
            result.update(status="unavailable", reason="malformed_action_setter_call_site", items=[])
            break
        item = {key: call[key] for key in ("parameter", "line", "end_line", "optional")}
        key_result = {"status": "unavailable", "items": [], "returned": 0, "omitted": None,
                      "relation": "literal_object_key_in_setter_argument_not_state_write",
                      "runtime_execution": "not_established", "source_binding": "not_checked"}
        key_evidence = call.get("literal_key_evidence")
        if not isinstance(key_evidence, dict):
            key_result["reason"] = "missing_literal_key_evidence"
        elif (key_evidence.get("status") not in {"observed", "unavailable"}
              or not isinstance(key_evidence.get("keys"), list)
              or (key_evidence["status"] == "observed" and "reason" in key_evidence)
              or (key_evidence["status"] == "unavailable" and
                  (key_evidence["keys"] or not isinstance(key_evidence.get("reason"), str)
                   or not key_evidence["reason"]))):
            key_result["reason"] = "malformed_literal_key_evidence"
        elif key_evidence["status"] == "unavailable":
            key_result["reason"] = key_evidence["reason"]
        else:
            key_result["status"] = "observed"
            keys = key_evidence["keys"]
            for key in keys[:remaining_keys]:
                if result["visited_records"] >= budget:
                    break
                result["visited_records"] += 1
                if not (isinstance(key, dict) and isinstance(key.get("name"), str) and key["name"]
                        and type(key.get("line")) is int and type(key.get("end_line")) is int
                        and call["line"] <= key["line"] <= key["end_line"] <= call["end_line"]):
                    key_result.update(status="unavailable", reason="malformed_literal_key_site", items=[])
                    break
                key_result["items"].append({field: key[field] for field in ("name", "line", "end_line")})
            key_result["returned"] = len(key_result["items"])
            key_result["omitted"] = len(keys) - key_result["returned"]
            remaining_keys -= key_result["returned"]
            if key_result["status"] == "observed" and key_result["omitted"]:
                key_result["status"] = "incomplete_scan"
                key_result["reason"] = "literal_key_output_or_scan_budget_exhausted"
        item["literal_state_keys"] = key_result
        result["items"].append(item)
    result["returned"] = len(result["items"])
    result["omitted"] = len(evidence["calls"]) - result["returned"]
    return result


def _focus_direct_export(module, name, files, project, index, wanted_names, budget):
    """Existing stored module/direct callable export owner; no call is invented."""
    result = {"status": "unresolved", "reason": "module_target_unavailable", "visited_records": 0}
    if module.get("status") != "target_candidate":
        result["reason"] = module.get("reason", result["reason"])
        return result, None, None
    target = module.get("target")
    ref = target.get("atlas_ref") if isinstance(target, dict) else None
    prefix, separator, rel = ref.partition("::") if isinstance(ref, str) else ("", "", "")
    if prefix != project or not separator or not _focus_relative_path(rel):
        result["reason"] = "malformed_target_file_identity"
        return result, None, None
    record = files.get(rel)
    if (not isinstance(record, dict) or target.get("source_hash") != record.get("hash")
            or not isinstance(target.get("source_hash"), str) or not target["source_hash"]
            or not isinstance(record.get("symbols"), list)):
        result["reason"] = "target_export_evidence_unavailable"
        return result, None, None
    if ref not in index:
        found, complete = {}, True
        for symbol in record["symbols"]:
            if result["visited_records"] >= max(0, budget):
                complete = False
                break
            result["visited_records"] += 1
            if isinstance(symbol, dict):
                export_key = "default" if symbol.get("export_kind") == "default" else symbol.get("name")
                if isinstance(export_key, str) and export_key in wanted_names:
                    found.setdefault(export_key, []).append(symbol)
        index[ref] = (complete, found)
    complete, found = index[ref]
    if not complete:
        result.update(status="incomplete_scan", reason="target_symbol_scan_incomplete")
        return result, None, None
    if not isinstance(name, str) or not name:
        result["reason"] = "dynamic_or_missing_export_name"
        return result, None, None
    matches = found.get(name, [])
    if len(matches) != 1:
        result.update(status="ambiguous" if matches else "unresolved",
                      reason="ambiguous_target_export" if matches else "target_export_not_indexed")
        return result, None, None
    symbol = matches[0]
    if symbol.get("type") in {"ReExportedSymbol", "ProxyExport", "LocalReExport"}:
        result["reason"] = "reexport_or_alias_unresolved"
        return result, None, None
    expected_kind = "default" if name == "default" else "named"
    if (symbol.get("exported") is not True or symbol.get("export_kind") != expected_kind
            or not isinstance(symbol.get("name"), str) or not symbol["name"]
            or symbol.get("type") not in {"Function", "Arrow", "Hook", "Component"}):
        result["reason"] = "export_not_direct_callable"
        return result, None, None
    line, end_line = symbol.get("line"), symbol.get("end_line")
    if not (type(line) is int and type(end_line) is int and 0 < line <= end_line):
        result["reason"] = "target_symbol_span_unavailable"
        return result, None, None
    result.update(status="target_candidate", reason="direct_declared_export", source_binding="not_checked",
                  target={**target, "symbol": symbol["name"], "start_line": line, "end_line": end_line,
                          "ast_contract_version": record.get("ast_contract_version"),
                          "callable_kind": symbol.get("response_json_callable_kind"),
                          "callable_span": {"start": symbol.get("start"), "end": symbol.get("end"),
                                            "line": line, "end_line": end_line}})
    return result, record, symbol


def _focus_jsx_source_facts(record, symbol, budget, *, child=False):
    """Validate normalized finite facts against their exact stored source owner."""
    from tools.core.atlas_ast_contract import AST_CONTRACT_VERSION, _jsx_callback_record_valid
    key = "jsx_parameter_callback_call_evidence" if child else "jsx_prop_callback_evidence"
    version = "v1-jsx-object-parameter-inline-call" if child else "v1-jsx-named-import-const-arrow-prop"
    result = {"status": "unavailable", "records": [], "omitted": None, "visited_records": 0,
              "reason": "jsx_callback_metadata_missing"}
    evidence = symbol.get(key)
    if not isinstance(evidence, dict):
        return result
    rows, identity = evidence.get("records"), evidence.get("source_hash")
    if (set(evidence) - {"status", "version", "offset_unit", "ast_contract_version", "source_hash",
                        "records", "omitted", "runtime_execution", "reason"}
            or evidence.get("version") != version or evidence.get("offset_unit") != "utf8_bytes"
            or evidence.get("ast_contract_version") != AST_CONTRACT_VERSION
            or record.get("ast_contract_version") != AST_CONTRACT_VERSION
            or not isinstance(identity, str) or len(identity) != 64
            or any(char not in "0123456789abcdef" for char in identity) or identity != record.get("hash")
            or not isinstance(record.get("parser_evidence"), dict)
            or record["parser_evidence"].get("status") != "observed"
            or evidence.get("runtime_execution") != "not_established"
            or not isinstance(rows, list) or len(rows) > 64
            or symbol.get("parser_kind") != "typescript_compiler_api"
            or symbol.get("type") not in {"Function", "Arrow", "Hook", "Component"}
            or type(evidence.get("omitted")) is not int or evidence["omitted"] < 0):
        return {**result, "reason": "jsx_callback_identity_or_contract_invalid"}
    if evidence.get("status") != "observed" or evidence["omitted"] or "reason" in evidence:
        return {**result, "status": "incomplete_scan" if evidence.get("status") == "incomplete_scan" else "unavailable",
                "reason": "jsx_callback_producer_incomplete", "omitted": len(rows) + evidence["omitted"]}
    if len(rows) + 1 > max(0, budget):
        return {**result, "status": "incomplete_scan", "reason": "jsx_callback_scan_budget_exhausted", "omitted": len(rows)}
    def valid_span(span):
        return (isinstance(span, dict) and set(span) == {"start", "end", "line", "end_line"}
                and all(type(value) is int for value in span.values())
                and type(record.get("size")) is int and type(record.get("loc")) is int
                and 0 <= span["start"] < span["end"] <= record["size"]
                and 1 <= span["line"] <= span["end_line"] <= record["loc"])
    if not valid_span({key: symbol.get(key) for key in ("start", "end", "line", "end_line")}):
        return {**result, "reason": "jsx_callback_outer_span_invalid", "omitted": len(rows)}
    seen = set()
    result["visited_records"] = 1
    for row in rows:
        result["visited_records"] += 1
        if not _jsx_callback_record_valid(row, symbol, valid_span, child=child):
            return {**result, "reason": "jsx_callback_record_invalid", "omitted": len(rows)}
        site = row["spans"]["call" if child else "attribute"]
        identity_key = (site["start"], site["end"])
        if identity_key in seen:
            return {**result, "reason": "jsx_callback_duplicate", "omitted": len(rows)}
        seen.add(identity_key)
    return {**result, "status": "observed", "reason": "finite_normalized_source_facts",
            "records": rows, "omitted": 0, "source_hash": identity, "ast_contract_version": AST_CONTRACT_VERSION}


def _focus_jsx_prop_callbacks(record, symbol, imports, files, project, limit, budget):
    """Join one lexical JSX site to direct exported child parameter syntax only."""
    result = {"status": "unavailable", "items": [], "returned": 0, "omitted": None,
              "visited_records": 0, "source_binding": "not_checked",
              "relation": "site_specific_jsx_prop_to_child_parameter_call_source_candidate",
              "runtime_execution": "not_established", "value_transfer": "not_established"}
    facts = _focus_jsx_source_facts(record, symbol, budget)
    result["visited_records"] += facts["visited_records"]
    if facts["status"] != "observed":
        return {**result, "status": facts["status"], "reason": facts["reason"], "omitted": facts["omitted"]}
    rows = facts["records"]
    result.update(status="observed", source_hash=facts["source_hash"], ast_contract_version=facts["ast_contract_version"])
    if not rows:
        return {**result, "omitted": 0}
    modules = imports.get("items") if isinstance(imports, dict) else None
    if not isinstance(modules, list) or len(modules) > 64:
        return {**result, "status": "unavailable", "reason": "jsx_module_evidence_unavailable", "omitted": len(rows)}
    module_index = {}
    for module in modules:
        if result["visited_records"] >= max(0, budget):
            return {**result, "status": "incomplete_scan", "reason": "jsx_module_scan_budget_exhausted", "omitted": len(rows)}
        result["visited_records"] += 1
        if isinstance(module, dict):
            identity = tuple(module.get(key) for key in ("source", "localName", "importedName", "kind"))
            if all(isinstance(value, str) and value for value in identity):
                module_index.setdefault(identity, []).append(module)
    exports, child_indexes = {}, {}
    wanted = {row["importedName"] for row in rows}
    remaining_children = max(0, limit)
    for row in rows[:max(0, limit)]:
        if result["visited_records"] >= max(0, budget):
            result.update(status="incomplete_scan", reason="jsx_callback_join_budget_exhausted")
            break
        result["visited_records"] += 1
        child = {"status": "unresolved", "reason": "module_reference_unavailable",
                 "items": [], "returned": 0, "omitted": None, "source_binding": "not_checked"}
        item = {"parent": row, "child": child}
        result["items"].append(item)
        identity = tuple(row[key] for key in ("source", "localName", "importedName", "kind"))
        matching = module_index.get(identity, [])
        if len(matching) != 1:
            child["reason"] = ("ambiguous_module_reference" if matching else "module_reference_omitted"
                               if imports.get("omitted") else "module_reference_unavailable")
            continue
        orientation, child_record, child_symbol = _focus_direct_export(
            matching[0], row["importedName"], files, project, exports, wanted, budget - result["visited_records"])
        result["visited_records"] += orientation.pop("visited_records")
        child.update(orientation)
        if orientation["status"] == "incomplete_scan":
            result.update(status="incomplete_scan", reason="jsx_export_scan_budget_exhausted")
            break
        if orientation["status"] != "target_candidate":
            continue
        target = orientation["target"]
        cache_key = (target["atlas_ref"], target["symbol"], target["start_line"], target["end_line"])
        if cache_key not in child_indexes:
            projected = _focus_jsx_source_facts(child_record, child_symbol, budget - result["visited_records"], child=True)
            result["visited_records"] += projected["visited_records"]
            by_prop = {}
            if projected["status"] == "observed":
                for fact in projected["records"]:
                    if result["visited_records"] >= max(0, budget):
                        projected.update(status="incomplete_scan", reason="jsx_child_index_budget_exhausted",
                                         omitted=len(projected["records"]))
                        break
                    result["visited_records"] += 1
                    by_prop.setdefault(fact["prop"], []).append(fact)
            child_indexes[cache_key] = (projected, by_prop)
        projected, by_prop = child_indexes[cache_key]
        if projected["status"] != "observed":
            child.update(status=projected["status"], reason=projected["reason"], omitted=projected["omitted"])
            if projected["status"] == "incomplete_scan":
                result.update(status="incomplete_scan", reason="jsx_child_evidence_incomplete")
            continue
        selected = by_prop.get(row["prop"], [])
        if not selected:
            child.update(status="unresolved", reason="no_admitted_same_prop_call",
                         omitted=0, absence_claim="not_established")
            continue
        shown = selected[:remaining_children]
        remaining_children -= len(shown)
        child.update(status="source_candidate", reason="same_prop_direct_child_parameter_syntax",
                     source_hash=projected["source_hash"], ast_contract_version=projected["ast_contract_version"],
                     items=shown, returned=len(shown), omitted=len(selected) - len(shown),
                     runtime_execution="not_established", value_transfer="not_established",
                     absence_claim="not_established")
    return {**result, "returned": len(result["items"]), "omitted": len(rows) - len(result["items"])}

def _focus_direct_callees(calls, imports, files, project, limit, budget,
                          *, file_index=None, remaining_hops=1):
    """Resolve at most two syntax hops to unique same-project direct exports."""
    result = {"status": "unavailable", "items": [], "returned": 0, "omitted": None,
              "visited_records": 0, "relation": "static_import_export_candidate",
              "runtime_execution": "not_established", "source_binding": "not_checked"}
    if remaining_hops:
        result["target_symbol_contexts"] = []
    if calls.get("status") != "observed":
        result["status"] = "incomplete_scan" if calls.get("status") == "incomplete_scan" else calls.get("status", "unavailable")
        result["reason"] = "import_call_evidence_incomplete"
        return result
    call_rows = [call for call in calls.get("items", [])
                 if not (call.get("kind") == "named" and call.get("member") is not None)]
    if not call_rows:
        result.update(status="observed", omitted=calls.get("omitted", 0))
        return result
    import_rows = imports.get("items", []) if isinstance(imports, dict) else []
    if not isinstance(import_rows, list):
        import_rows = []
    wanted_names = {call.get("member") if call.get("kind") == "namespace" else call.get("importedName")
                    for call in call_rows}
    indexed_targets = {}
    context_index = {}
    result["status"] = "observed"
    for call in call_rows:
        if result["visited_records"] >= budget:
            result.update(status="incomplete_scan", reason="callee_scan_budget_exhausted")
            break
        result["visited_records"] += 1
        item = {"status": "unresolved", "call_line": call["line"],
                "imported_name": call["importedName"], "local_name": call["localName"],
                "reason": "module_reference_unavailable", "runtime_execution": "not_established"}
        if "lexical_owner" in call:
            item.update(lexical_owner=call["lexical_owner"], lexical_owner_kind=call["lexical_owner_kind"],
                        caller_spans=call["spans"], outer_callable_execution="not_established")
        result["items"].append(item)
        matching = [entry for entry in import_rows if isinstance(entry, dict) and all(
            entry.get(key) == call.get(key) for key in ("source", "localName", "importedName", "kind"))]
        if len(matching) != 1:
            item["reason"] = ("ambiguous_module_reference" if matching else
                              "module_reference_omitted" if imports.get("omitted") else
                              "module_import_evidence_incomplete" if imports.get("status") != "observed" else
                              "module_reference_unavailable")
            continue
        module = matching[0]
        if module.get("status") != "target_candidate":
            item["reason"] = module.get("reason", "module_target_unavailable")
            continue
        name = call.get("member") if call.get("kind") == "namespace" else call.get("importedName")
        orientation, record, symbol = _focus_direct_export(
            module, name, files, project, indexed_targets, wanted_names, budget - result["visited_records"])
        result["visited_records"] += orientation.pop("visited_records")
        item.update(orientation)
        if item["status"] == "incomplete_scan":
            item["status"] = "unresolved"
            result.update(status="incomplete_scan", reason="callee_scan_budget_exhausted")
            break
        if item["status"] != "target_candidate":
            continue
        ref = item["target"]["atlas_ref"]
        if not remaining_hops:
            continue
        line, end_line = item["target"]["start_line"], item["target"]["end_line"]
        context_key = (ref, symbol["name"], line, end_line)
        if context_key not in context_index:
            context = _focus_symbol_context(symbol, limit, "direct_target_symbol_syntax_not_execution")
            context.update(target=item["target"], source_binding="not_checked",
                           traversal="one_target_context_plus_one_bounded_outbound_candidate_hop")
            context["import_calls"] = _focus_import_calls(
                symbol, None, limit, budget - result["visited_records"])
            result["visited_records"] += context["import_calls"]["visited_records"]
            next_imports = (_focus_import_candidates(
                record, symbol, file_index, limit, budget - result["visited_records"])
                if context["import_calls"].get("items") and isinstance(file_index, dict)
                else {"status": "unavailable", "items": [], "omitted": None})
            result["visited_records"] += next_imports.get("visited_records", 0)
            context["outbound_calls"] = _focus_direct_callees(
                context["import_calls"], next_imports, files, project, limit,
                budget - result["visited_records"], file_index=None, remaining_hops=0)
            context["outbound_calls"]["relation"] = "second_hop_static_import_export_candidate"
            result["visited_records"] += context["outbound_calls"]["visited_records"]
            context_index[context_key] = len(result["target_symbol_contexts"])
            result["target_symbol_contexts"].append(context)
        item["target_context_index"] = context_index[context_key]
    result["returned"] = len(result["items"])
    result["omitted"] = (calls.get("omitted") or 0) + len(call_rows) - result["returned"]
    return result


def _focus_same_file_store_action_calls(record, declaring, action, target, limit, budget):
    """Project parser-bound direct/destructured store action syntax, not a runtime caller graph."""
    result = {"status": "not_applicable", "items": [], "returned": 0, "omitted": None,
              "visited_records": 0, "relation": "same_file_lexical_store_action_call_candidate",
              "runtime_execution": "not_established", "source_binding": "not_checked",
              "scope": "selected_file_direct_or_const_destructured_getstate_only"}
    setter = action.get("zustand_setter_call_evidence") if isinstance(action, dict) else None
    if (not isinstance(declaring, dict) or declaring.get("exported") is not True
            or not isinstance(setter, dict) or setter.get("status") != "observed"
            or setter.get("binding_scope") != "single_file_lexical_store_factory_parameter"):
        result["reason"] = "selected_action_not_supported_lexical_store_candidate"
        return result
    symbols = record.get("symbols") if isinstance(record, dict) else None
    if not isinstance(symbols, list):
        result.update(status="unavailable", reason="caller_symbols_unavailable")
        return result
    result["status"] = "observed"
    seen_evidence = False
    matched = 0
    for caller in symbols:
        if result["visited_records"] >= budget:
            result.update(status="incomplete_scan", reason="caller_scan_budget_exhausted")
            break
        result["visited_records"] += 1
        if not isinstance(caller, dict):
            result.update(status="unavailable", reason="malformed_caller_symbol")
            break
        if caller.get("name") == "__file_meta__":
            continue
        if caller.get("type") not in {"Function", "Arrow", "Hook", "Component"}:
            continue
        evidence = caller.get("same_file_store_action_call_evidence")
        if not isinstance(evidence, dict):
            result.update(status="unavailable", reason="parser_call_evidence_not_indexed")
            break
        seen_evidence = True
        if evidence.get("binding_scope") not in {
                "single_file_lexical_exported_store", "single_file_lexical_store_or_named_import"}:
            result.update(status="unavailable", reason="caller_binding_scope_unavailable")
            break
        if evidence.get("status") == "unavailable":
            result.update(status="unavailable", reason="caller_binding_unavailable")
            break
        if evidence.get("status") != "observed":
            continue
        calls = evidence.get("calls")
        if not isinstance(calls, list):
            result.update(status="unavailable", reason="caller_call_evidence_malformed")
            break
        for call in calls:
            if result["visited_records"] >= budget:
                result.update(status="incomplete_scan", reason="caller_scan_budget_exhausted")
                break
            result["visited_records"] += 1
            if not isinstance(call, dict):
                result.update(status="unavailable", reason="caller_call_evidence_malformed")
                break
            if (call.get("binding_kind", "same_file_export") != "same_file_export"
                    or call.get("store") != declaring.get("name")
                    or call.get("action") != action.get("name")):
                continue
            call_form = call.get("call_form", "direct_getstate_action")
            if call_form not in {"direct_getstate_action", "const_destructured_getstate_action"}:
                result.update(status="unavailable", reason="caller_call_form_unavailable")
                break
            line, end_line = call.get("line"), call.get("end_line")
            start, end = caller.get("line"), caller.get("end_line")
            if not (isinstance(caller.get("name"), str) and caller["name"]
                    and all(type(value) is int for value in (line, end_line, start, end))
                    and 0 < start <= line <= end_line <= end):
                result.update(status="unavailable", reason="caller_span_unavailable")
                break
            matched += 1
            if len(result["items"]) < limit:
                result["items"].append({
                    "status": "target_candidate", "call_line": line, "call_end_line": end_line,
                    "reason": "lexical_same_file_store_getstate_action_call",
                    "call_form": call_form,
                    "caller": {key: target[key] for key in
                               ("atlas_ref", "target_ref", "target_file", "source_hash")},
                    "caller_symbol": caller["name"],
                    "caller_span": {"start_line": start, "end_line": end},
                })
        if result["status"] != "observed":
            break
    if result["status"] == "observed" and not seen_evidence:
        result.update(status="unavailable", reason="parser_call_evidence_not_indexed")
    if result["status"] == "unavailable":
        result["items"] = []
    result["returned"] = len(result["items"])
    result["omitted"] = max(0, matched - result["returned"]) if result["status"] == "observed" else None
    return result


def _focus_same_file_rehydrate_calls(record, declaring, action, target, limit, budget):
    """Project bounded rehydrate syntax, including direct inline callbacks; never infer hydration."""
    result = {"status": "not_applicable", "items": [], "returned": 0, "omitted": None,
              "visited_records": 0, "relation": "same_file_lexical_explicit_rehydrate_call_candidate",
              "runtime_execution": "not_established", "source_binding": "not_checked",
              "scope": "indexed_callable_body_same_file_export_direct_const_alias_or_inline_callback"}
    setter = action.get("zustand_setter_call_evidence") if isinstance(action, dict) else None
    if (not isinstance(declaring, dict) or declaring.get("type") != "Variable"
            or declaring.get("exported") is not True or declaring.get("export_kind") != "named"
            or type(declaring.get("start")) is not int
            or not isinstance(setter, dict) or setter.get("status") != "observed"
            or setter.get("middleware_form") != "persist"
            or setter.get("binding_scope") != "single_file_lexical_store_factory_parameter"):
        result["reason"] = "selected_action_not_supported_persist_store_candidate"
        return result
    symbols = record.get("symbols") if isinstance(record, dict) else None
    if not isinstance(symbols, list):
        result.update(status="unavailable", reason="caller_symbols_unavailable")
        return result
    result["status"] = "observed"
    matched = 0
    seen_evidence = False
    for caller in symbols:
        if result["visited_records"] >= budget:
            result.update(status="incomplete_scan", reason="caller_scan_budget_exhausted")
            break
        result["visited_records"] += 1
        if not isinstance(caller, dict):
            result.update(status="unavailable", reason="malformed_caller_symbol")
            break
        if caller.get("name") == "__file_meta__" or caller.get("type") not in {
                "Function", "Arrow", "Hook", "Component"}:
            continue
        evidence = caller.get("same_file_store_action_call_evidence")
        if not isinstance(evidence, dict) or "rehydrate_calls" not in evidence:
            result.update(status="unavailable", reason="parser_rehydrate_evidence_not_indexed")
            break
        seen_evidence = True
        if (evidence.get("status") != "observed"
                or evidence.get("binding_scope") != "single_file_lexical_store_or_named_import"
                or evidence.get("runtime_execution") != "not_established"
                or not isinstance(evidence.get("rehydrate_calls"), list)
                or type(evidence.get("rehydrate_omitted")) is not int
                or evidence["rehydrate_omitted"] < 0):
            result.update(status="unavailable", reason="malformed_parser_rehydrate_evidence")
            break
        if evidence["rehydrate_omitted"]:
            result.update(status="incomplete_scan", reason="parser_rehydrate_source_cap")
        for call in evidence["rehydrate_calls"]:
            if result["visited_records"] >= budget:
                result.update(status="incomplete_scan", reason="caller_scan_budget_exhausted")
                break
            result["visited_records"] += 1
            if not isinstance(call, dict):
                result.update(status="unavailable", reason="malformed_parser_rehydrate_call")
                break
            line, end_line = call.get("line"), call.get("end_line")
            start, end = caller.get("line"), caller.get("end_line")
            call_form = call.get("call_form")
            call_context = call.get("call_context")
            if (not isinstance(call.get("store"), str) or not call["store"]
                    or any(type(value) is not int for value in
                           (call.get("store_start"), line, end_line, start, end))
                    or not (0 < start <= line <= end_line <= end)
                    or call["store_start"] < 0
                    or (call_form is not None and
                        (call_form != "const_local_alias"
                         or not isinstance(call.get("alias_name"), str)
                         or not call["alias_name"]))
                    or (call_form is None and ("call_form" in call or "alias_name" in call))):
                result.update(status="unavailable", reason="malformed_parser_rehydrate_call")
                break
            if (call_context is not None and
                    (call_context != "inline_callback" or call_form is not None)):
                result.update(status="unavailable", reason="malformed_parser_rehydrate_call")
                break
            if call_context is None and "call_context" in call:
                result.update(status="unavailable", reason="malformed_parser_rehydrate_call")
                break
            if call["store"] != declaring.get("name"):
                continue
            if call["store_start"] != declaring["start"]:
                result.update(status="unavailable", reason="rehydrate_store_identity_mismatch")
                break
            matched += 1
            if len(result["items"]) < limit:
                result["items"].append({
                    "status": "source_candidate", "call_line": line, "call_end_line": end_line,
                    "reason": ("checker_bound_same_file_inline_callback_rehydrate_call"
                               if call_context else
                               "checker_bound_same_file_const_alias_rehydrate_call"
                               if call_form else "checker_bound_same_file_export_rehydrate_call"),
                    **({"call_form": call_form, "alias_name": call["alias_name"]}
                       if call_form else {}),
                    **({"call_context": call_context} if call_context else {}),
                    "caller": {key: target[key] for key in
                               ("atlas_ref", "target_ref", "target_file", "source_hash")},
                    "caller_symbol": caller["name"],
                    "caller_span": {"start_line": start, "end_line": end},
                })
        if result["status"] == "unavailable":
            break
    if result["status"] == "observed" and not seen_evidence:
        result.update(status="unavailable", reason="parser_rehydrate_evidence_not_indexed")
    if result["status"] == "unavailable":
        result["items"] = []
    result["returned"] = len(result["items"])
    result["omitted"] = max(0, matched - result["returned"]) if result["status"] == "observed" else None
    return result


def _focus_cross_file_rehydrate_calls(importer_rows, file_index, declaring, action, target,
                                      project, limit, budget):
    """Join bounded named-import rehydrate syntax, including direct inline callbacks."""
    result = {"status": "not_applicable", "items": [], "returned": 0, "omitted": None,
              "visited_records": 0,
              "relation": "same_project_named_import_explicit_rehydrate_call_candidate",
              "runtime_execution": "not_established", "source_binding": "not_checked",
              "scope": "indexed_callable_body_named_import_direct_const_alias_or_inline_callback"}
    setter = action.get("zustand_setter_call_evidence") if isinstance(action, dict) else None
    if (not isinstance(declaring, dict) or declaring.get("type") != "Variable"
            or declaring.get("exported") is not True or declaring.get("export_kind") != "named"
            or type(declaring.get("start")) is not int
            or not isinstance(setter, dict) or setter.get("status") != "observed"
            or setter.get("middleware_form") != "persist"
            or setter.get("binding_scope") != "single_file_lexical_store_factory_parameter"):
        result["reason"] = "selected_action_not_supported_persist_store_candidate"
        return result
    if not isinstance(importer_rows, list) or not isinstance(file_index, dict):
        result.update(status="unavailable", reason="atlas_file_index_unavailable")
        return result
    result["status"] = "observed"
    matched = 0
    selected_rel = target["atlas_ref"].partition("::")[2]
    for rel, record in importer_rows:
        if result["visited_records"] >= budget:
            result.update(status="incomplete_scan", reason="importer_scan_budget_exhausted")
            break
        result["visited_records"] += 1
        if rel == selected_rel:
            continue
        if not isinstance(record, dict) or not _focus_relative_path(rel):
            result.update(status="unavailable", reason="malformed_importer_file")
            break
        imports = record.get("import_records")
        if not isinstance(imports, list):
            result.update(status="unavailable", reason="importer_module_evidence_unavailable")
            break
        matching_imports = []
        for entry in imports:
            if result["visited_records"] >= budget:
                result.update(status="incomplete_scan", reason="importer_scan_budget_exhausted")
                break
            result["visited_records"] += 1
            if not isinstance(entry, dict):
                result.update(status="unavailable", reason="malformed_importer_module_evidence")
                break
            resolved = entry.get("source")
            if not _focus_relative_path(resolved):
                continue
            targets = file_index.get(resolved, [])
            if not any(item.get("atlas_ref") == target["atlas_ref"] for item in targets):
                continue
            if len(targets) != 1:
                result.update(status="ambiguous", reason="ambiguous_imported_store_file")
                break
            if (entry.get("kind") == "named" and entry.get("scope") == "top_level"
                    and entry.get("name") == declaring.get("name")
                    and isinstance(entry.get("raw_source"), str) and entry["raw_source"]):
                matching_imports.append(entry)
        if result["status"] != "observed":
            break
        if not matching_imports:
            continue
        if len(matching_imports) != 1:
            result.update(status="ambiguous", reason="ambiguous_named_store_import")
            break
        module = matching_imports[0]
        symbols = record.get("symbols")
        workspace_rel = record.get("workspace_rel") or rel
        if (not isinstance(symbols, list) or not isinstance(record.get("hash"), str)
                or not record["hash"] or not _focus_relative_path(workspace_rel)):
            result.update(status="unavailable", reason="importer_source_evidence_unavailable")
            break
        caller_target = {"atlas_ref": f"{project}::{rel}",
                         "target_ref": f"{project}::{workspace_rel}",
                         "target_file": workspace_rel, "source_hash": record["hash"]}
        for caller in symbols:
            if result["visited_records"] >= budget:
                result.update(status="incomplete_scan", reason="caller_scan_budget_exhausted")
                break
            result["visited_records"] += 1
            if not isinstance(caller, dict):
                result.update(status="unavailable", reason="malformed_importer_symbol")
                break
            if caller.get("type") not in {"Function", "Arrow", "Hook", "Component"}:
                continue
            evidence = caller.get("same_file_store_action_call_evidence")
            if not isinstance(evidence, dict) or "imported_rehydrate_calls" not in evidence:
                result.update(status="unavailable", reason="imported_rehydrate_evidence_not_indexed")
                break
            if (evidence.get("status") != "observed"
                    or evidence.get("binding_scope") != "single_file_lexical_store_or_named_import"
                    or evidence.get("runtime_execution") != "not_established"
                    or not isinstance(evidence.get("imported_rehydrate_calls"), list)
                    or type(evidence.get("imported_rehydrate_omitted")) is not int
                    or evidence["imported_rehydrate_omitted"] < 0):
                result.update(status="unavailable", reason="malformed_imported_rehydrate_evidence")
                break
            if evidence["imported_rehydrate_omitted"]:
                result.update(status="incomplete_scan", reason="parser_imported_rehydrate_source_cap")
            for call in evidence["imported_rehydrate_calls"]:
                if result["visited_records"] >= budget:
                    result.update(status="incomplete_scan", reason="caller_scan_budget_exhausted")
                    break
                result["visited_records"] += 1
                if not isinstance(call, dict):
                    result.update(status="unavailable", reason="malformed_imported_rehydrate_call")
                    break
                line, end_line = call.get("line"), call.get("end_line")
                start, end = caller.get("line"), caller.get("end_line")
                call_form = call.get("call_form")
                call_context = call.get("call_context")
                if (not isinstance(call.get("store"), str) or not call["store"]
                        or not isinstance(call.get("module_source"), str) or not call["module_source"]
                        or not isinstance(call.get("imported_store"), str) or not call["imported_store"]
                        or not isinstance(caller.get("name"), str) or not caller["name"]
                        or any(type(value) is not int for value in (line, end_line, start, end))
                        or not (0 < start <= line <= end_line <= end)
                        or (call_form is not None and
                            (call_form != "const_local_alias"
                             or not isinstance(call.get("alias_name"), str)
                             or not call["alias_name"]))
                        or (call_form is None and ("call_form" in call or "alias_name" in call))):
                    result.update(status="unavailable", reason="malformed_imported_rehydrate_call")
                    break
                if (call_context is not None and
                        (call_context != "inline_callback" or call_form is not None)):
                    result.update(status="unavailable", reason="malformed_imported_rehydrate_call")
                    break
                if call_context is None and "call_context" in call:
                    result.update(status="unavailable", reason="malformed_imported_rehydrate_call")
                    break
                if (call["module_source"] != module["raw_source"]
                        or call["imported_store"] != declaring["name"]):
                    continue
                matched += 1
                if len(result["items"]) < limit:
                    result["items"].append({
                        "status": "source_candidate",
                        "reason": ("checker_bound_named_import_inline_callback_rehydrate_call"
                                   if call_context else
                                   "checker_bound_named_import_const_alias_rehydrate_call"
                                   if call_form else
                                   "checker_bound_named_import_and_unique_module_rehydrate_call"),
                        **({"call_form": call_form, "alias_name": call["alias_name"]}
                           if call_form else {}),
                        **({"call_context": call_context} if call_context else {}),
                        "call_line": line, "call_end_line": end_line,
                        "local_store": call["store"], "imported_store": declaring["name"],
                        "module_source": module["raw_source"], "caller": caller_target,
                        "caller_symbol": caller["name"],
                        "caller_span": {"start_line": start, "end_line": end},
                    })
            if result["status"] != "observed":
                break
        if result["status"] != "observed":
            break
    if result["status"] in {"unavailable", "ambiguous"}:
        result["items"] = []
    result["returned"] = len(result["items"])
    result["omitted"] = max(0, matched - result["returned"]) if result["status"] == "observed" else None
    return result


def _focus_cross_file_store_action_calls(importer_rows, file_index, declaring, action, target,
                                         project, limit, budget):
    """Join exact named-import call syntax to one stored same-project module target."""
    result = {"status": "not_applicable", "items": [], "returned": 0, "omitted": None,
              "visited_records": 0, "relation": "same_project_named_import_store_action_call_candidate",
              "runtime_execution": "not_established", "source_binding": "not_checked",
              "scope": "direct_or_const_destructured_named_import_getstate_only_no_hook_or_alias_flow"}
    setter = action.get("zustand_setter_call_evidence") if isinstance(action, dict) else None
    if (not isinstance(declaring, dict) or declaring.get("exported") is not True
            or declaring.get("export_kind") != "named"
            or not isinstance(setter, dict) or setter.get("status") != "observed"
            or setter.get("binding_scope") != "single_file_lexical_store_factory_parameter"):
        result["reason"] = "selected_action_not_supported_named_store_export"
        return result
    if not isinstance(importer_rows, list) or not isinstance(file_index, dict):
        result.update(status="unavailable", reason="atlas_file_index_unavailable")
        return result
    result["status"] = "observed"
    matched = 0
    for rel, record in importer_rows:
        if result["visited_records"] >= budget:
            result.update(status="incomplete_scan", reason="importer_scan_budget_exhausted")
            break
        result["visited_records"] += 1
        if rel == target["atlas_ref"].partition("::")[2]:
            continue
        if not isinstance(record, dict) or not _focus_relative_path(rel):
            result.update(status="unavailable", reason="malformed_importer_file")
            break
        imports = record.get("import_records")
        if not isinstance(imports, list):
            result.update(status="unavailable", reason="importer_module_evidence_unavailable")
            break
        matching_imports = []
        for entry in imports:
            if result["visited_records"] >= budget:
                result.update(status="incomplete_scan", reason="importer_scan_budget_exhausted")
                break
            result["visited_records"] += 1
            if not isinstance(entry, dict):
                result.update(status="unavailable", reason="malformed_importer_module_evidence")
                break
            resolved = entry.get("source")
            if not _focus_relative_path(resolved):
                continue
            targets = file_index.get(resolved, [])
            if not any(item.get("atlas_ref") == target["atlas_ref"] for item in targets):
                continue
            if len(targets) != 1:
                result.update(status="ambiguous", reason="ambiguous_imported_store_file")
                break
            if (entry.get("kind") == "named" and entry.get("scope") == "top_level"
                    and entry.get("name") == declaring.get("name")
                    and isinstance(entry.get("raw_source"), str) and entry["raw_source"]):
                matching_imports.append(entry)
        if result["status"] != "observed":
            break
        if not matching_imports:
            continue
        if len(matching_imports) != 1:
            result.update(status="ambiguous", reason="ambiguous_named_store_import")
            break
        module = matching_imports[0]
        symbols = record.get("symbols")
        if not isinstance(symbols, list) or not isinstance(record.get("hash"), str) or not record["hash"]:
            result.update(status="unavailable", reason="importer_source_evidence_unavailable")
            break
        workspace_rel = record.get("workspace_rel") or rel
        if not _focus_relative_path(workspace_rel):
            result.update(status="unavailable", reason="importer_path_identity_unavailable")
            break
        caller_target = {"atlas_ref": f"{project}::{rel}",
                         "target_ref": f"{project}::{workspace_rel}",
                         "target_file": workspace_rel, "source_hash": record["hash"]}
        for caller in symbols:
            if result["visited_records"] >= budget:
                result.update(status="incomplete_scan", reason="caller_scan_budget_exhausted")
                break
            result["visited_records"] += 1
            if not isinstance(caller, dict):
                result.update(status="unavailable", reason="malformed_importer_symbol")
                break
            if caller.get("type") not in {"Function", "Arrow", "Hook", "Component"}:
                continue
            evidence = caller.get("same_file_store_action_call_evidence")
            if not isinstance(evidence, dict) or evidence.get("binding_scope") != "single_file_lexical_store_or_named_import":
                result.update(status="unavailable", reason="importer_parser_evidence_not_indexed")
                break
            if evidence.get("status") == "unavailable":
                result.update(status="unavailable", reason="importer_parser_binding_unavailable")
                break
            if evidence.get("status") != "observed" or not isinstance(evidence.get("calls"), list):
                continue
            for call in evidence["calls"]:
                if result["visited_records"] >= budget:
                    result.update(status="incomplete_scan", reason="caller_scan_budget_exhausted")
                    break
                result["visited_records"] += 1
                if not isinstance(call, dict):
                    result.update(status="unavailable", reason="malformed_importer_call")
                    break
                if (call.get("binding_kind") != "named_import"
                        or call.get("module_source") != module["raw_source"]
                        or call.get("imported_store") != declaring.get("name")
                        or call.get("action") != action.get("name")):
                    continue
                call_form = call.get("call_form", "direct_getstate_action")
                if call_form not in {"direct_getstate_action", "const_destructured_getstate_action"}:
                    result.update(status="unavailable", reason="importer_call_form_unavailable")
                    break
                line, end_line = call.get("line"), call.get("end_line")
                start, end = caller.get("line"), caller.get("end_line")
                if not (isinstance(call.get("store"), str) and call["store"]
                        and isinstance(caller.get("name"), str) and caller["name"]
                        and all(type(value) is int for value in (line, end_line, start, end))
                        and 0 < start <= line <= end_line <= end):
                    result.update(status="unavailable", reason="importer_call_span_unavailable")
                    break
                matched += 1
                if len(result["items"]) < limit:
                    result["items"].append({
                        "status": "target_candidate", "reason": "lexical_named_import_and_unique_module_target",
                        "call_line": line, "call_end_line": end_line, "call_form": call_form,
                        "local_store": call["store"], "imported_store": declaring["name"],
                        "module_source": module["raw_source"], "caller": caller_target,
                        "caller_symbol": caller["name"],
                        "caller_span": {"start_line": start, "end_line": end},
                    })
            if result["status"] != "observed":
                break
        if result["status"] != "observed":
            break
    if result["status"] in {"unavailable", "ambiguous"}:
        result["items"] = []
    result["returned"] = len(result["items"])
    result["omitted"] = max(0, matched - result["returned"]) if result["status"] == "observed" else None
    return result


def _focus_store_hook_selectors(record, importer_rows, file_index, declaring, action,
                                target, project, limit, budget):
    """Orient direct store-selector syntax; a subscription or execution is not proven."""
    result = {"status": "not_applicable", "items": [], "returned": 0, "omitted": None,
              "visited_records": 0, "relation": "direct_zustand_hook_selector_syntax_candidate",
              "runtime_execution": "not_established", "subscription": "not_established",
              "runtime_owner_binding": "not_established",
              "source_binding": "not_checked",
              "scope": "direct_arrow_property_selector_same_file_or_named_import_only",
              "result_invocation_scope": "positive_direct_const_call_syntax_only"}
    setter = action.get("zustand_setter_call_evidence") if isinstance(action, dict) else None
    if (not isinstance(declaring, dict) or declaring.get("exported") is not True
            or declaring.get("export_kind") != "named" or not isinstance(setter, dict)
            or setter.get("status") != "observed"
            or setter.get("binding_scope") != "single_file_lexical_store_factory_parameter"):
        result["reason"] = "selected_action_not_supported_named_store_export"
        return result
    if setter.get("factory_api") is None:
        result.update(status="unavailable", reason="store_factory_api_not_indexed")
        return result
    if setter.get("factory_api") == "vanilla_store":
        result["reason"] = "selected_store_not_react_bound_hook"
        return result
    if setter.get("factory_api") != "react_bound_hook":
        result.update(status="unavailable", reason="store_factory_api_malformed")
        return result
    if not isinstance(record, dict) or not isinstance(importer_rows, list):
        result.update(status="unavailable", reason="caller_files_unavailable")
        return result
    result["status"] = "observed"
    selected_rel = target["atlas_ref"].partition("::")[2]
    matched = 0
    seen_selected = False
    for rel, caller_file in chain(((selected_rel, record),), importer_rows):
        if rel == selected_rel:
            if seen_selected:
                continue
            seen_selected = True
        if result["visited_records"] >= budget:
            result.update(status="incomplete_scan", reason="hook_consumer_budget_exhausted")
            break
        result["visited_records"] += 1
        if not isinstance(caller_file, dict) or not _focus_relative_path(rel):
            result.update(status="unavailable", reason="malformed_hook_consumer_file")
            break
        module = None
        if rel != selected_rel:
            imports = caller_file.get("import_records")
            if not isinstance(imports, list):
                result.update(status="unavailable", reason="hook_consumer_module_evidence_unavailable")
                break
            matching = []
            for entry in imports:
                if result["visited_records"] >= budget:
                    result.update(status="incomplete_scan", reason="hook_consumer_budget_exhausted")
                    break
                result["visited_records"] += 1
                if not isinstance(entry, dict):
                    result.update(status="unavailable", reason="malformed_hook_consumer_import")
                    break
                resolved = entry.get("source")
                if not _focus_relative_path(resolved):
                    continue
                targets = file_index.get(resolved, [])
                if not any(item.get("atlas_ref") == target["atlas_ref"] for item in targets):
                    continue
                if len(targets) != 1:
                    result.update(status="ambiguous", reason="ambiguous_hook_store_file")
                    break
                if (entry.get("kind") == "named" and entry.get("scope") == "top_level"
                        and entry.get("name") == declaring.get("name")
                        and isinstance(entry.get("raw_source"), str) and entry["raw_source"]):
                    matching.append(entry)
            if result["status"] != "observed":
                break
            if not matching:
                continue
            if len(matching) != 1:
                result.update(status="ambiguous", reason="ambiguous_hook_store_import")
                break
            module = matching[0]
        symbols = caller_file.get("symbols")
        workspace_rel = caller_file.get("workspace_rel") or rel
        if (not isinstance(symbols, list) or not isinstance(caller_file.get("hash"), str)
                or not caller_file["hash"] or not _focus_relative_path(workspace_rel)):
            result.update(status="unavailable", reason="hook_consumer_source_evidence_unavailable")
            break
        caller_target = {"atlas_ref": f"{project}::{rel}",
                         "target_ref": f"{project}::{workspace_rel}",
                         "target_file": workspace_rel, "source_hash": caller_file["hash"]}
        for caller in symbols:
            if result["visited_records"] >= budget:
                result.update(status="incomplete_scan", reason="hook_consumer_budget_exhausted")
                break
            result["visited_records"] += 1
            if not isinstance(caller, dict):
                result.update(status="unavailable", reason="malformed_hook_consumer_symbol")
                break
            if caller.get("type") not in {"Function", "Arrow", "Hook", "Component"}:
                continue
            evidence = caller.get("store_hook_selector_evidence")
            if (not isinstance(evidence, dict)
                    or evidence.get("binding_scope") != "single_file_lexical_store_or_named_import"):
                result.update(status="unavailable", reason="hook_selector_parser_evidence_not_indexed")
                break
            if evidence.get("status") == "unavailable":
                result.update(status="unavailable", reason="hook_selector_binding_unavailable")
                break
            if evidence.get("status") != "observed" or not isinstance(evidence.get("calls"), list):
                continue
            for call in evidence["calls"]:
                if result["visited_records"] >= budget:
                    result.update(status="incomplete_scan", reason="hook_consumer_budget_exhausted")
                    break
                result["visited_records"] += 1
                if not isinstance(call, dict):
                    result.update(status="unavailable", reason="malformed_hook_selector_evidence")
                    break
                binding_matches = (call.get("binding_kind") == "same_file_export"
                                   and rel == selected_rel and call.get("store") == declaring.get("name"))
                if module is not None:
                    binding_matches = (call.get("binding_kind") == "named_import"
                                       and call.get("module_source") == module["raw_source"]
                                       and call.get("imported_store") == declaring.get("name"))
                if not binding_matches or call.get("selected_member") != action.get("name"):
                    continue
                line, end_line = call.get("line"), call.get("end_line")
                start, end = caller.get("line"), caller.get("end_line")
                if (call.get("selector_form") != "direct_arrow_property"
                        or not isinstance(call.get("store"), str) or not call["store"]
                        or not isinstance(caller.get("name"), str) or not caller["name"]
                        or not all(type(value) is int for value in (line, end_line, start, end))
                        or not 0 < start <= line <= end_line <= end):
                    result.update(status="unavailable", reason="malformed_hook_selector_span")
                    break
                return_form = call.get("direct_return_form")
                if return_form is not None and return_form not in {"return_statement", "arrow_expression"}:
                    result.update(status="unavailable", reason="malformed_hook_return_evidence")
                    break
                result_calls = call.get("selected_result_direct_calls")
                omitted_calls = call.get("selected_result_direct_calls_omitted", 0)
                if ((result_calls is not None and (not isinstance(result_calls, list)
                        or not 1 <= len(result_calls) <= 8))
                        or type(omitted_calls) is not int or omitted_calls < 0
                        or (result_calls is None and omitted_calls)):
                    result.update(status="unavailable", reason="malformed_hook_result_call_evidence")
                    break
                validated_calls = []
                for invocation in result_calls or []:
                    if result["visited_records"] >= budget:
                        result.update(status="incomplete_scan", reason="hook_consumer_budget_exhausted")
                        break
                    result["visited_records"] += 1
                    if not isinstance(invocation, dict):
                        result.update(status="unavailable", reason="malformed_hook_result_call_evidence")
                        break
                    call_start, call_end = invocation.get("line"), invocation.get("end_line")
                    if (type(call_start) is not int or type(call_end) is not int
                            or not 0 < start <= call_start <= call_end <= end
                            or call_start < line):
                        result.update(status="unavailable", reason="malformed_hook_result_call_span")
                        break
                    validated_calls.append({"line": call_start, "end_line": call_end})
                if result["status"] != "observed":
                    break
                matched += 1
                if len(result["items"]) < limit:
                    item = {"status": "target_candidate",
                        "reason": "lexical_direct_store_selector_and_unique_module_target",
                        "selector_form": "direct_arrow_property", "selected_member": call["selected_member"],
                        "local_store": call["store"], "call_line": line, "call_end_line": end_line,
                        "caller": caller_target, "caller_symbol": caller["name"],
                        "caller_span": {"start_line": start, "end_line": end},
                        "subscription": "not_established",
                        "runtime_owner_binding": "not_established"}
                    if validated_calls:
                        item["selected_result_direct_calls"] = validated_calls
                        if omitted_calls:
                            item["selected_result_direct_calls_omitted"] = omitted_calls
                    if (return_form and caller["name"].startswith("use")
                            and caller.get("exported") is True
                            and caller.get("export_kind") == "named"):
                        item["hook_return_owner"] = {
                            "hook": caller["name"], "return_form": return_form,
                            "relation": "exported_hook_directly_returns_selected_store_member_syntax",
                            "runtime_execution": "not_established",
                            "runtime_owner_binding": "not_established",
                        }
                    result["items"].append(item)
            if result["status"] != "observed":
                break
        if result["status"] != "observed":
            break
    if result["status"] in {"unavailable", "ambiguous"}:
        result["items"] = []
    result["returned"] = len(result["items"])
    result["omitted"] = max(0, matched - result["returned"]) if result["status"] == "observed" else None
    return result


def _focus_class_instance_event_calls(caller, caller_target, raw_source, producer_name,
                                      limit, budget):
    """Project only exact readonly-field import syntax, not runtime instance ownership."""
    result = {"status": "unavailable", "items": [], "matched": 0, "visited_records": 0}
    evidence = caller.get("class_instance_event_call_evidence")
    if not isinstance(evidence, dict):
        result["reason"] = "legacy_instance_field_evidence_not_indexed"
        return result
    if budget < 1:
        result.update(status="incomplete_scan", reason="event_bus_scan_budget_exhausted")
        return result
    result["visited_records"] = 1
    if (evidence.get("binding_scope") != "single_file_lexical_readonly_class_field_named_import"
            or evidence.get("runtime_owner_binding") != "not_established"
            or evidence.get("runtime_execution") != "not_established"
            or evidence.get("status") not in {"observed", "incomplete_scan", "unavailable"}
            or not isinstance(evidence.get("calls"), list)
            or type(evidence.get("omitted")) is not int or evidence["omitted"] < 0
            or len(evidence["calls"]) > 64):
        result["reason"] = "malformed_instance_field_evidence"
        return result
    if evidence["status"] == "unavailable":
        result["reason"] = "instance_field_parser_unavailable"
        return result
    result["status"] = "observed"
    members = caller.get("member_details")
    if not isinstance(members, list):
        result.update(status="unavailable", reason="instance_field_member_index_unavailable")
        return result
    for call in evidence["calls"]:
        if result["visited_records"] >= budget:
            result.update(status="incomplete_scan", reason="event_bus_scan_budget_exhausted")
            break
        result["visited_records"] += 1
        if not isinstance(call, dict):
            result.update(status="unavailable", reason="malformed_instance_field_call")
            break
        start, end = caller.get("line"), caller.get("end_line")
        member_line, member_end = call.get("member_line"), call.get("member_end_line")
        line, end_line = call.get("line"), call.get("end_line")
        owner = [member for member in members if isinstance(member, dict)
                 and member.get("name") == call.get("caller_member")
                 and member.get("line") == member_line
                 and member.get("end_line") == member_end]
        if (not all(isinstance(call.get(key), str) and call[key] for key in (
                "source", "localName", "importedName", "owner_field", "caller_member", "member"))
                or call.get("kind") != "named"
                or not isinstance(call.get("first_literal_argument"), str)
                or call["member"] not in {"emit", "on", "once", "off", "addListener", "removeListener"}
                or len(owner) != 1
                or not all(type(value) is int for value in (
                    start, end, member_line, member_end, line, end_line))
                or not 0 < start <= member_line <= line <= end_line <= member_end <= end):
            result.update(status="unavailable", reason="malformed_instance_field_call")
            break
        if (call["source"] != raw_source or call["importedName"] != producer_name
                or not call["first_literal_argument"]):
            continue
        result["matched"] += 1
        if len(result["items"]) < limit:
            result["items"].append({
                "status": "target_candidate",
                "reason": "lexical_readonly_class_field_named_import_literal_event",
                "event": call["first_literal_argument"], "method": call["member"],
                "callable_nesting": "direct_caller_body_syntax",
                "call_line": line, "call_end_line": end_line,
                "caller_symbol": caller["name"], "caller_member": call["caller_member"],
                "caller_owner_kind": "readonly_class_field_initializer_syntax",
                "caller_owner_field": call["owner_field"],
                "runtime_owner_binding": "not_established",
                "caller": caller_target, "runtime_execution": "not_established"})
    if result["status"] == "observed" and (evidence["status"] == "incomplete_scan"
                                           or evidence["omitted"]):
        result.update(status="incomplete_scan", reason="instance_field_call_output_capped")
    if result["status"] == "unavailable":
        result["items"] = []
        result["matched"] = 0
    return result


def _focus_class_field_event_calls(field, declaring, limit, budget):
    """Show direct constructor/this.field syntax without joining external instances."""
    result = {"status": "unavailable", "items": [], "returned": 0, "omitted": None,
              "visited_records": 0, "runtime_owner_binding": "not_established",
              "runtime_execution": "not_established",
              "external_property_callers": "unresolved"}
    evidence = field.get("class_event_emitter_field_evidence")
    if not isinstance(evidence, dict):
        result["reason"] = "legacy_class_field_constructor_evidence_not_indexed"
        return result
    if budget < 1:
        result.update(status="incomplete_scan", reason="class_field_event_scan_budget_exhausted")
        return result
    result["visited_records"] = 1
    if (evidence.get("binding_scope") != "single_file_lexical_class_field_direct_constructor"
            or evidence.get("constructor") != "EventEmitter"
            or evidence.get("module_source") not in {"node:events", "events"}
            or evidence.get("runtime_owner_binding") != "not_established"
            or evidence.get("runtime_execution") != "not_established"
            or evidence.get("status") not in {"observed", "incomplete_scan", "unavailable"}
            or not isinstance(evidence.get("calls"), list)
            or len(evidence["calls"]) > 64
            or type(evidence.get("omitted")) is not int or evidence["omitted"] < 0):
        result["reason"] = "malformed_class_field_constructor_evidence"
        return result
    if evidence["status"] == "unavailable":
        result["reason"] = "class_field_constructor_or_parser_unavailable"
        return result
    members = declaring.get("member_details")
    if not isinstance(members, list):
        result["reason"] = "declaring_class_members_unavailable"
        return result
    class_start, class_end = declaring.get("line"), declaring.get("end_line")
    field_start, field_end = field.get("line"), field.get("end_line")
    if not (isinstance(field.get("name"), str) and field["name"]
            and field.get("kind") == "property"
            and all(type(value) is int for value in
                    (class_start, class_end, field_start, field_end))
            and 0 < class_start <= field_start <= field_end <= class_end):
        result["reason"] = "class_field_span_unavailable"
        return result
    result["status"] = "observed"
    for call in evidence["calls"]:
        if result["visited_records"] >= budget:
            result.update(status="incomplete_scan", reason="class_field_event_scan_budget_exhausted")
            break
        result["visited_records"] += 1
        if not isinstance(call, dict):
            result.update(status="unavailable", reason="malformed_class_field_event_call")
            break
        member_line, member_end = call.get("member_line"), call.get("member_end_line")
        line, end_line = call.get("line"), call.get("end_line")
        owner = [member for member in members if isinstance(member, dict)
                 and member.get("name") == call.get("caller_member")
                 and member.get("line") == member_line
                 and member.get("end_line") == member_end]
        if (call.get("owner_field") != field["name"]
                or not isinstance(call.get("caller_member"), str)
                or not call["caller_member"]
                or call.get("method") not in {"emit", "on", "once", "off",
                                              "addListener", "removeListener"}
                or not isinstance(call.get("first_literal_argument"), str)
                or type(call.get("nested_callable_depth")) is not int
                or not 0 <= call["nested_callable_depth"] <= 8
                or len(owner) != 1
                or not all(type(value) is int for value in
                           (member_line, member_end, line, end_line))
                or not 0 < class_start <= member_line <= line <= end_line <= member_end <= class_end):
            result.update(status="unavailable", reason="malformed_class_field_event_call")
            break
        if len(result["items"]) < limit:
            result["items"].append({
                "status": "syntax_candidate",
                "reason": "direct_class_field_constructor_and_this_field_call",
                "event": call["first_literal_argument"],
                "method": call["method"],
                "call_line": line, "call_end_line": end_line,
                "callable_nesting": (
                    "lexical_arrow_callback_syntax" if call["nested_callable_depth"]
                    else "direct_caller_body_syntax"),
                "nested_callable_depth": call["nested_callable_depth"],
                "caller_member": call["caller_member"],
                "caller_owner_kind": "class_direct_constructor_field_syntax",
                "runtime_owner_binding": "not_established",
                "runtime_execution": "not_established"})
    if result["status"] == "unavailable":
        result["items"] = []
    elif result["status"] == "observed" and (
            evidence["status"] == "incomplete_scan" or evidence["omitted"]):
        result.update(
            status="incomplete_scan",
            reason=("class_field_event_output_capped" if evidence["omitted"]
                    else "class_field_event_parser_incomplete"),
        )
    result["returned"] = len(result["items"])
    if result["status"] == "observed":
        result["omitted"] = max(0, len(evidence["calls"]) - result["returned"])
    return result


def _focus_imported_event_bus_calls(importer_rows, file_index, producer, target,
                                    project, limit, budget):
    """Join direct named-import EventEmitter calls by stored module and literal key."""
    result = {"status": "unavailable", "items": [], "returned": 0, "omitted": None,
              "visited_records": 0, "relation": "imported_event_emitter_literal_call_candidate",
              "runtime_execution": "not_established", "source_binding": "not_checked",
              "coverage": "indexed_callable_class_member_and_direct_object_member_syntax",
              "instance_property_coverage": "no_class_caller_scanned"}
    proof = producer.get("event_emitter_singleton_evidence")
    if not isinstance(proof, dict):
        result["reason"] = "missing_producer_constructor_evidence"
        return result
    if (proof.get("status") != "observed" or proof.get("constructor") != "EventEmitter"
            or proof.get("module_source") not in {"node:events", "events"}
            or proof.get("binding_scope") != "single_file_lexical_constructor_import"
            or proof.get("runtime_execution") != "not_established"
            or producer.get("type") != "Variable" or producer.get("exported") is not True
            or producer.get("export_kind") != "named"
            or not isinstance(producer.get("name"), str) or not producer["name"]):
        result["reason"] = "producer_constructor_or_export_unavailable"
        return result
    result["status"] = "observed"
    matched = 0
    for rel, record in importer_rows:
        if result["visited_records"] >= budget:
            result.update(status="incomplete_scan", reason="event_bus_scan_budget_exhausted")
            break
        result["visited_records"] += 1
        if not isinstance(record, dict) or not _focus_relative_path(rel):
            result.update(status="unavailable", reason="malformed_event_bus_importer")
            break
        imports = record.get("import_records")
        if not isinstance(imports, list):
            result.update(status="unavailable", reason="missing_event_bus_module_evidence")
            break
        modules = []
        for entry in imports:
            if result["visited_records"] >= budget:
                result.update(status="incomplete_scan", reason="event_bus_scan_budget_exhausted")
                break
            result["visited_records"] += 1
            if not isinstance(entry, dict):
                result.update(status="unavailable", reason="malformed_event_bus_module_evidence")
                break
            resolved = entry.get("source")
            if not _focus_relative_path(resolved):
                continue
            targets = file_index.get(resolved, [])
            if not any(item.get("atlas_ref") == target["atlas_ref"] for item in targets):
                continue
            if len(targets) != 1:
                result.update(status="ambiguous", reason="ambiguous_event_bus_module")
                break
            if (entry.get("kind") == "named" and entry.get("scope") == "top_level"
                    and entry.get("name") == producer["name"]
                    and isinstance(entry.get("raw_source"), str) and entry["raw_source"]):
                modules.append(entry)
        if result["status"] != "observed":
            break
        if not modules:
            continue
        if len(modules) != 1:
            result.update(status="ambiguous", reason="ambiguous_event_bus_named_import")
            break
        symbols = record.get("symbols")
        workspace_rel = record.get("workspace_rel") or rel
        if (not isinstance(symbols, list) or not _focus_relative_path(workspace_rel)
                or not isinstance(record.get("hash"), str) or not record["hash"]):
            result.update(status="unavailable", reason="event_bus_caller_source_unavailable")
            break
        caller_target = {"atlas_ref": f"{project}::{rel}",
                         "target_ref": f"{project}::{workspace_rel}",
                         "target_file": workspace_rel, "source_hash": record["hash"]}
        for caller in symbols:
            if result["visited_records"] >= budget:
                result.update(status="incomplete_scan", reason="event_bus_scan_budget_exhausted")
                break
            result["visited_records"] += 1
            if not isinstance(caller, dict):
                result.update(status="unavailable", reason="malformed_event_bus_caller")
                break
            caller_type = caller.get("type")
            if caller_type not in {"Function", "Arrow", "Hook", "Component", "Class", "Variable"}:
                continue
            if not isinstance(caller.get("name"), str) or not caller["name"]:
                result.update(status="unavailable", reason="event_bus_caller_symbol_unavailable")
                break
            sites = [(caller, None, "indexed_callable")]
            if caller_type == "Class":
                members = caller.get("member_details")
                if not isinstance(members, list):
                    result.update(status="unavailable", reason="event_bus_class_members_unavailable")
                    break
                sites = [(member, member.get("name") if isinstance(member, dict) else None,
                          "class_member_syntax") for member in members]
            elif caller_type == "Variable":
                initializer = caller.get("initializer_member_evidence")
                if not isinstance(initializer, dict) or initializer.get("status") != "syntax_observed":
                    continue
                members = initializer.get("members")
                if (not isinstance(members, list)
                        or any(not isinstance(member, dict)
                               or member.get("attribution") not in {
                                   "direct_object_initializer", "returned_object_candidate"}
                               for member in members)):
                    result.update(status="unavailable", reason="event_bus_object_members_unavailable")
                    break
                sites = [(member, member.get("name"),
                          "direct_object_member_syntax") for member in members
                         if member["attribution"] == "direct_object_initializer"]
            for site, member_name, owner_kind in sites:
                if result["visited_records"] >= budget:
                    result.update(status="incomplete_scan", reason="event_bus_scan_budget_exhausted")
                    break
                result["visited_records"] += 1
                if owner_kind != "indexed_callable":
                    if not (isinstance(site, dict) and isinstance(member_name, str) and member_name
                            and all(type(value) is int for value in (
                                caller.get("line"), caller.get("end_line"),
                                site.get("line"), site.get("end_line")))
                            and 0 < caller["line"] <= site["line"] <= site["end_line"] <= caller["end_line"]):
                        result.update(status="unavailable", reason="malformed_event_bus_member_site")
                        break
                calls = _focus_import_calls(site, caller, limit, budget - result["visited_records"])
                result["visited_records"] += calls["visited_records"]
                if calls["status"] == "not_applicable" and owner_kind != "indexed_callable":
                    continue
                if calls["status"] != "observed":
                    result.update(status="incomplete_scan" if calls["status"] == "incomplete_scan" else "unavailable",
                                  reason="event_bus_parser_calls_unavailable")
                    break
                for call in calls["items"]:
                    if result["visited_records"] >= budget:
                        result.update(status="incomplete_scan", reason="event_bus_scan_budget_exhausted")
                        break
                    result["visited_records"] += 1
                    if (call["kind"] != "named" or call["importedName"] != producer["name"]
                            or call["source"] != modules[0]["raw_source"]
                            or call["member"] not in {"emit", "on", "once", "off", "addListener", "removeListener"}
                            or not call["first_literal_argument"] or call["optional"]
                            or type(call.get("nested_callable_depth")) is not int):
                        continue
                    matched += 1
                    if len(result["items"]) < limit:
                        result["items"].append({
                            "status": "target_candidate", "reason": "lexical_named_import_unique_module_literal_event",
                            "event": call["first_literal_argument"], "method": call["member"],
                            "callable_nesting": "nested_anonymous_callback_syntax"
                                if call["nested_callable_depth"] else "direct_caller_body_syntax",
                            "call_line": call["line"], "call_end_line": call["end_line"],
                            "caller_symbol": caller["name"], "caller_member": member_name,
                            "caller_owner_kind": owner_kind, "caller": caller_target,
                            "runtime_execution": "not_established"})
                if calls["omitted"] and result["status"] == "observed":
                    result.update(status="incomplete_scan", reason="event_bus_caller_call_output_capped")
                if result["status"] != "observed":
                    break
            if result["status"] != "observed":
                break
            if caller_type == "Class":
                field_calls = _focus_class_instance_event_calls(
                    caller, caller_target, modules[0]["raw_source"], producer["name"],
                    max(0, limit - len(result["items"])), budget - result["visited_records"])
                result["visited_records"] += field_calls["visited_records"]
                if field_calls["status"] == "unavailable" and field_calls.get("reason") in {
                        "legacy_instance_field_evidence_not_indexed", "instance_field_parser_unavailable"}:
                    result["instance_property_coverage"] = "partial_legacy_or_parser_unavailable"
                elif field_calls["status"] == "unavailable":
                    result.update(status="unavailable", reason=field_calls["reason"])
                    break
                else:
                    if result["instance_property_coverage"] == "no_class_caller_scanned":
                        result["instance_property_coverage"] = "parser_recorded_class_callers"
                    matched += field_calls["matched"]
                    result["items"].extend(field_calls["items"])
                    if field_calls["status"] == "incomplete_scan":
                        result.update(status="incomplete_scan", reason=field_calls["reason"])
                        break
        if result["status"] != "observed":
            break
    if result["status"] in {"unavailable", "ambiguous"}:
        result["items"] = []
    result["returned"] = len(result["items"])
    result["omitted"] = max(0, matched - result["returned"]) if result["status"] == "observed" else None
    return result


def _focus_imported_property_event_calls(importer_rows, file_index, selected, target,
                                         project, limit, budget):
    """Reverse only named-import value.property literal event-call syntax."""
    result = {"status": "not_applicable", "items": [], "returned": 0, "omitted": None,
              "visited_records": 0,
              "relation": "imported_value_property_literal_event_call_candidate",
              "coverage": "indexed_callable_positive_evidence_with_file_parser_marker",
              "selected_export_value_origin": "unverified",
              "runtime_owner_binding": "not_established",
              "runtime_execution": "not_established", "source_binding": "not_checked"}
    if (selected.get("type") != "Variable" or selected.get("exported") is not True
            or selected.get("export_kind") != "named"):
        return result
    if (not isinstance(selected.get("name"), str) or not selected["name"]
            or not isinstance(target.get("source_hash"), str) or not target["source_hash"]):
        result.update(status="unavailable", reason="selected_value_source_unavailable")
        return result
    result["status"] = "observed"
    matched = 0
    legacy_coverage = False
    for rel, record in importer_rows:
        if result["visited_records"] >= budget:
            result.update(status="incomplete_scan", reason="property_event_scan_budget_exhausted")
            break
        result["visited_records"] += 1
        if not isinstance(record, dict) or not _focus_relative_path(rel):
            result.update(status="unavailable", reason="malformed_property_event_importer")
            break
        imports = record.get("import_records")
        if not isinstance(imports, list):
            result.update(status="unavailable", reason="property_event_module_evidence_unavailable")
            break
        matching = []
        for entry in imports:
            if result["visited_records"] >= budget:
                result.update(status="incomplete_scan", reason="property_event_scan_budget_exhausted")
                break
            result["visited_records"] += 1
            if not isinstance(entry, dict):
                result.update(status="unavailable", reason="malformed_property_event_module_evidence")
                break
            resolved = entry.get("source")
            if not _focus_relative_path(resolved):
                continue
            targets = file_index.get(resolved, [])
            if not any(item.get("atlas_ref") == target["atlas_ref"] for item in targets):
                continue
            if len(targets) != 1:
                result.update(status="ambiguous", reason="ambiguous_property_event_module")
                break
            if (entry.get("kind") == "named" and entry.get("scope") == "top_level"
                    and entry.get("name") == selected["name"]
                    and isinstance(entry.get("raw_source"), str) and entry["raw_source"]):
                matching.append(entry)
        if result["status"] != "observed":
            break
        if not matching:
            continue
        if len(matching) != 1:
            result.update(status="ambiguous", reason="ambiguous_property_event_named_import")
            break
        symbols = record.get("symbols")
        workspace_rel = record.get("workspace_rel") or rel
        if (not isinstance(symbols, list) or not _focus_relative_path(workspace_rel)
                or not isinstance(record.get("hash"), str) or not record["hash"]):
            result.update(status="unavailable", reason="property_event_caller_source_unavailable")
            break
        caller_target = {"atlas_ref": f"{project}::{rel}",
                         "target_ref": f"{project}::{workspace_rel}",
                         "target_file": workspace_rel, "source_hash": record["hash"]}
        file_coverage = (isinstance(record.get("features"), list)
                         and "ParserEvidence:ImportedPropertyEventCallsV1" in record["features"])
        if not file_coverage:
            legacy_coverage = True
        for caller in symbols:
            if result["visited_records"] >= budget:
                result.update(status="incomplete_scan", reason="property_event_scan_budget_exhausted")
                break
            result["visited_records"] += 1
            if not isinstance(caller, dict):
                result.update(status="unavailable", reason="malformed_property_event_caller")
                break
            if caller.get("type") not in {"Function", "Arrow", "Hook", "Component"}:
                continue
            if not isinstance(caller.get("name"), str) or not caller["name"]:
                result.update(status="unavailable", reason="property_event_caller_symbol_unavailable")
                break
            evidence = caller.get("import_call_evidence")
            property_evidence = evidence.get("property_event_evidence") if isinstance(evidence, dict) else None
            if not isinstance(property_evidence, dict):
                continue
            if (evidence.get("status") != "observed"
                    or property_evidence.get("binding_scope") != "single_file_lexical_named_import_property"
                    or property_evidence.get("runtime_owner_binding") != "not_established"
                    or property_evidence.get("runtime_execution") != "not_established"
                    or property_evidence.get("status") not in {"observed", "incomplete_scan"}
                    or not isinstance(property_evidence.get("calls"), list)
                    or not isinstance(property_evidence.get("limitations"), list)
                    or any(not isinstance(item, str) for item in property_evidence["limitations"])
                    or len(property_evidence["calls"]) > 64
                    or type(property_evidence.get("omitted")) is not int
                    or property_evidence["omitted"] < 0):
                result.update(status="unavailable", reason="malformed_property_event_parser_evidence")
                break
            for call in property_evidence["calls"]:
                if result["visited_records"] >= budget:
                    result.update(status="incomplete_scan", reason="property_event_scan_budget_exhausted")
                    break
                result["visited_records"] += 1
                if (not isinstance(call, dict)
                        or not all(isinstance(call.get(key), str) and call[key]
                                   for key in ("source", "localName", "importedName", "owner_property"))
                        or call.get("method") not in {"emit", "on", "once", "off",
                                                       "addListener", "removeListener"}
                        or not isinstance(call.get("first_literal_argument"), str)
                        or type(call.get("nested_callable_depth")) is not int
                        or not 0 <= call["nested_callable_depth"] <= 8
                        or not all(type(value) is int for value in (
                            caller.get("line"), caller.get("end_line"),
                            call.get("line"), call.get("end_line")))
                        or not 0 < caller["line"] <= call["line"] <= call["end_line"] <= caller["end_line"]):
                    result.update(status="unavailable", reason="malformed_property_event_call")
                    break
                if (call["source"] != matching[0]["raw_source"]
                        or call["importedName"] != selected["name"]):
                    continue
                matched += 1
                if len(result["items"]) < limit:
                    result["items"].append({
                        "status": "syntax_candidate",
                        "reason": "lexical_named_import_unique_module_property_event",
                        "owner_property": call["owner_property"],
                        "method": call["method"], "event": call["first_literal_argument"],
                        "call_line": call["line"], "call_end_line": call["end_line"],
                        "callable_nesting": ("nested_callable_syntax"
                                             if call["nested_callable_depth"]
                                             else "direct_caller_body_syntax"),
                        "caller_symbol": caller.get("name"), "caller": caller_target,
                        "runtime_owner_binding": "not_established",
                        "runtime_execution": "not_established"})
            if result["status"] != "observed":
                break
            if property_evidence["status"] == "incomplete_scan" or property_evidence["omitted"]:
                result.update(status="incomplete_scan", reason="property_event_parser_incomplete")
                break
        if result["status"] != "observed":
            break
    if result["status"] == "observed" and legacy_coverage:
        result.update(status="incomplete_scan", reason="legacy_property_event_coverage_unavailable")
    if result["status"] in {"unavailable", "ambiguous"}:
        result["items"] = []
    result["returned"] = len(result["items"])
    result["omitted"] = max(0, matched - result["returned"]) if result["status"] == "observed" else None
    return result


def _focus_factory_source_candidate(selected, record, target, budget):
    """Expose one parser-bound factory path without a package or runtime join."""
    result = {"status": "unavailable", "reason": "no_supported_factory_evidence_or_legacy_parser",
              "relation": "local_factory_named_import_constructor_source_candidate",
              "runtime_instance_identity": "not_established",
              "factory_execution": "not_established",
              "package_condition_resolution": "not_evaluated",
              "public_field_mutation": "not_checked",
              "visited_records": 0, "source_binding": "not_checked"}
    if (selected.get("type") != "Variable" or selected.get("exported") is not True
            or selected.get("export_kind") != "named"):
        result.update(status="not_applicable", reason="not_selected_exported_variable")
        return result
    if not isinstance(target.get("source_hash"), str) or not target["source_hash"]:
        result["reason"] = "selected_factory_source_unavailable"
        return result
    if budget <= 0:
        result.update(status="incomplete_scan", reason="factory_source_scan_budget_exhausted")
        return result
    result["visited_records"] = 1
    evidence = selected.get("factory_source_evidence")
    if not isinstance(evidence, dict):
        return result
    if (evidence.get("status") != "syntax_candidate"
            or evidence.get("binding_scope") != "single_file_lexical_factory_and_named_constructor_import"
            or evidence.get("initializer_form") not in {"direct_factory_call", "factory_passed_as_argument"}
            or evidence.get("return_form") not in {"direct_new_return", "direct_const_new_return"}
            or not all(isinstance(evidence.get(key), str) and evidence[key] for key in (
                "factory", "constructed_imported_name", "constructor_module_source"))
            or not all(type(evidence.get(key)) is int and evidence[key] > 0 for key in (
                "factory_line", "return_line"))
            or evidence["factory_line"] > evidence["return_line"]
            or evidence.get("factory_execution") != "not_established"
            or evidence.get("runtime_instance_identity") != "not_established"
            or evidence.get("package_condition_resolution") != "not_evaluated"
            or evidence.get("public_field_mutation") != "not_checked"
            or (evidence["initializer_form"] == "factory_passed_as_argument")
                != (isinstance(evidence.get("wrapper_name"), str)
                    and bool(evidence["wrapper_name"]))):
        result["reason"] = "malformed_factory_source_evidence"
        return result
    imports = record.get("import_records")
    if not isinstance(imports, list):
        result["reason"] = "factory_constructor_import_evidence_unavailable"
        return result
    matched_imports = 0
    for entry in imports:
        if result["visited_records"] >= budget:
            result.update(status="incomplete_scan", reason="factory_import_scan_budget_exhausted")
            return result
        result["visited_records"] += 1
        if not isinstance(entry, dict):
            result["reason"] = "malformed_factory_constructor_import"
            return result
        if (entry.get("scope") == "top_level" and entry.get("kind") == "named"
                and entry.get("raw_source") == evidence["constructor_module_source"]
                and entry.get("name") == evidence["constructed_imported_name"]):
            matched_imports += 1
    if matched_imports != 1:
        result.update(status="ambiguous" if matched_imports > 1 else "unavailable",
                      reason="factory_constructor_import_not_unique_or_missing")
        return result
    result.pop("reason")
    result.update({key: evidence[key] for key in (
        "status", "initializer_form", "wrapper_name", "factory", "factory_line",
        "return_line", "return_form", "constructed_imported_name", "constructor_module_source")})
    return result


def _focus_upstream_named_import_calls(importer_rows, file_index, selected, target,
                                       project, limit, budget):
    """Reverse only direct named-import call syntax to one declared export."""
    result = {"status": "not_applicable", "items": [], "returned": 0, "omitted": None,
              "visited_records": 0, "relation": "direct_named_import_call_candidate",
              "coverage": "cross_file_indexed_callable_body_only",
              "runtime_execution": "not_established", "source_binding": "not_checked"}
    if (selected.get("type") not in {"Function", "Arrow", "Hook", "Component"}
            or selected.get("exported") is not True
            or selected.get("export_kind") != "named"):
        return result
    if not isinstance(selected.get("name"), str) or not selected["name"]:
        result.update(status="unavailable", reason="selected_export_name_unavailable")
        return result
    if (not isinstance(target.get("source_hash"), str) or not target["source_hash"]
            or type(selected.get("line")) is not int or type(selected.get("end_line")) is not int
            or not 0 < selected["line"] <= selected["end_line"]):
        result.update(status="unavailable", reason="selected_export_source_unavailable")
        return result
    result["status"] = "observed"
    matched = 0
    for rel, record in importer_rows:
        if result["visited_records"] >= budget:
            result.update(status="incomplete_scan", reason="upstream_call_scan_budget_exhausted")
            break
        result["visited_records"] += 1
        if not isinstance(record, dict) or not _focus_relative_path(rel):
            result.update(status="unavailable", reason="malformed_upstream_caller_file")
            break
        imports = record.get("import_records")
        if not isinstance(imports, list):
            result.update(status="unavailable", reason="upstream_module_evidence_unavailable")
            break
        matching = []
        for entry in imports:
            if result["visited_records"] >= budget:
                result.update(status="incomplete_scan", reason="upstream_call_scan_budget_exhausted")
                break
            result["visited_records"] += 1
            if not isinstance(entry, dict):
                result.update(status="unavailable", reason="malformed_upstream_module_evidence")
                break
            resolved = entry.get("source")
            if not _focus_relative_path(resolved):
                continue
            targets = file_index.get(resolved, [])
            if not any(item.get("atlas_ref") == target["atlas_ref"] for item in targets):
                continue
            if len(targets) != 1:
                result.update(status="ambiguous", reason="ambiguous_upstream_module")
                break
            if (entry.get("kind") == "named" and entry.get("scope") == "top_level"
                    and entry.get("name") == selected["name"]
                    and isinstance(entry.get("raw_source"), str) and entry["raw_source"]):
                matching.append(entry)
        if result["status"] != "observed":
            break
        if not matching:
            continue
        if len(matching) != 1:
            result.update(status="ambiguous", reason="ambiguous_upstream_named_import")
            break
        symbols = record.get("symbols")
        workspace_rel = record.get("workspace_rel") or rel
        if (not isinstance(symbols, list) or not _focus_relative_path(workspace_rel)
                or not isinstance(record.get("hash"), str) or not record["hash"]):
            result.update(status="unavailable", reason="upstream_caller_source_unavailable")
            break
        caller_target = {"atlas_ref": f"{project}::{rel}",
                         "target_ref": f"{project}::{workspace_rel}",
                         "target_file": workspace_rel, "source_hash": record["hash"]}
        for caller in symbols:
            if result["visited_records"] >= budget:
                result.update(status="incomplete_scan", reason="upstream_call_scan_budget_exhausted")
                break
            result["visited_records"] += 1
            if not isinstance(caller, dict):
                result.update(status="unavailable", reason="malformed_upstream_caller")
                break
            if caller.get("type") not in {"Function", "Arrow", "Hook", "Component"}:
                continue
            calls = _focus_import_calls(caller, None, limit, budget - result["visited_records"])
            result["visited_records"] += calls["visited_records"]
            if calls["status"] != "observed":
                result.update(status="incomplete_scan" if calls["status"] == "incomplete_scan" else "unavailable",
                              reason="upstream_parser_calls_unavailable")
                break
            for call in calls["items"]:
                if result["visited_records"] >= budget:
                    result.update(status="incomplete_scan", reason="upstream_call_scan_budget_exhausted")
                    break
                result["visited_records"] += 1
                if (call["kind"] != "named" or call["member"] is not None
                        or call["importedName"] != selected["name"]
                        or call["source"] != matching[0]["raw_source"] or call["optional"]
                        or call.get("nested_callable_depth") is not None):
                    continue
                if not isinstance(caller.get("name"), str) or not caller["name"]:
                    result.update(status="unavailable", reason="upstream_caller_symbol_unavailable")
                    break
                matched += 1
                if len(result["items"]) < limit:
                    result["items"].append({
                        "status": "target_candidate", "reason": "lexical_direct_named_import_unique_module",
                        "call_line": call["line"], "call_end_line": call["end_line"],
                        "caller_symbol": caller["name"], "caller": caller_target,
                        "runtime_execution": "not_established"})
            if calls["omitted"] and result["status"] == "observed":
                result.update(status="incomplete_scan", reason="upstream_caller_call_output_capped")
            if result["status"] != "observed":
                break
        if result["status"] != "observed":
            break
    if result["status"] in {"unavailable", "ambiguous"}:
        result["items"] = []
    result["returned"] = len(result["items"])
    result["omitted"] = max(0, matched - result["returned"]) if result["status"] == "observed" else None
    return result


def _focus_same_file_direct_calls(record, selected, target, limit, budget):
    """Reverse parser-bound direct calls in the selected file; never infer execution."""
    result = {"status": "not_applicable", "items": [], "returned": 0, "omitted": None,
              "visited_records": 0, "relation": "same_file_direct_call_candidate",
              "coverage": "indexed_callable_body_only", "runtime_execution": "not_established",
              "source_binding": "not_checked"}
    if selected.get("type") not in {"Function", "Arrow", "Hook", "Component"}:
        return result
    if (not isinstance(target.get("source_hash"), str) or not target["source_hash"]
            or not isinstance(selected.get("name"), str) or not selected["name"]
            or type(selected.get("start")) is not int or selected["start"] < 0):
        result.update(status="unavailable", reason="selected_callable_identity_unavailable")
        return result
    symbols = record.get("symbols") if isinstance(record, dict) else None
    if not isinstance(symbols, list):
        result.update(status="unavailable", reason="caller_symbols_unavailable")
        return result
    result["status"] = "observed"
    matched = 0
    partial = False
    for caller in symbols:
        if result["visited_records"] >= budget:
            result.update(status="incomplete_scan", reason="same_file_caller_budget_exhausted")
            break
        result["visited_records"] += 1
        if not isinstance(caller, dict):
            result.update(status="unavailable", reason="malformed_caller_symbol")
            break
        if caller.get("type") not in {"Function", "Arrow", "Hook", "Component"}:
            continue
        evidence = caller.get("same_file_direct_call_evidence")
        if not isinstance(evidence, dict):
            result.update(status="unavailable", reason="parser_call_evidence_not_indexed")
            break
        if evidence.get("binding_scope") != "single_file_lexical_direct_callable":
            result.update(status="unavailable", reason="caller_binding_scope_unavailable")
            break
        if evidence.get("status") not in {"observed", "incomplete_scan"}:
            result.update(status="unavailable", reason="caller_binding_unavailable")
            break
        calls = evidence.get("calls")
        if (not isinstance(calls, list) or type(evidence.get("omitted")) is not int
                or evidence["omitted"] < 0 or len(calls) > 64):
            result.update(status="unavailable", reason="caller_call_evidence_malformed")
            break
        if evidence["status"] == "incomplete_scan" or evidence["omitted"]:
            partial = True
        for call in calls:
            if result["visited_records"] >= budget:
                result.update(status="incomplete_scan", reason="same_file_caller_budget_exhausted")
                break
            result["visited_records"] += 1
            if not isinstance(call, dict):
                result.update(status="unavailable", reason="caller_call_evidence_malformed")
                break
            line, end_line = call.get("line"), call.get("end_line")
            start, end = caller.get("line"), caller.get("end_line")
            if not (isinstance(caller.get("name"), str) and caller["name"]
                    and isinstance(call.get("target_symbol"), str) and call["target_symbol"]
                    and type(call.get("target_start")) is int and call["target_start"] >= 0
                    and all(type(value) is int for value in (line, end_line, start, end))
                    and 0 < start <= line <= end_line <= end):
                result.update(status="unavailable", reason="caller_call_span_unavailable")
                break
            if (call["target_symbol"] != selected["name"]
                    or call["target_start"] != selected["start"]):
                continue
            matched += 1
            if len(result["items"]) < limit:
                result["items"].append({
                    "status": "target_candidate", "reason": "lexical_same_file_direct_call",
                    "call_line": line, "call_end_line": end_line,
                    "caller_symbol": caller["name"],
                    "caller": {key: target[key] for key in
                               ("atlas_ref", "target_ref", "target_file", "source_hash")},
                    "runtime_execution": "not_established"})
        if result["status"] != "observed":
            break
    if result["status"] == "observed" and partial:
        result.update(status="incomplete_scan", reason="parser_call_output_capped")
    if result["status"] == "unavailable":
        result["items"] = []
    result["returned"] = len(result["items"])
    result["omitted"] = max(0, matched - result["returned"]) if result["status"] == "observed" else None
    return result


def project_state_flow_focus(atlas: dict, *, project: str, file: str, symbol: str,
                             max_items: int, scan_limit: int, _deferred_result_use: dict | None = None,
                             _deferred_local_sibling: dict | None = None,
                             _deferred_jsx_callbacks: dict | None = None,
                             _deferred_returned_callbacks: dict | None = None,
                             _deferred_call_result_bindings: dict | None = None,
                             _deferred_alias_invocations: dict | None = None,
                             _deferred_nested_alias_invocations: dict | None = None,
                             _deferred_parameter_callbacks: dict | None = None,
                             _deferred_optional_rest_alias_invocations: dict | None = None,
                             _deferred_optional_alias_invocations: dict | None = None,
                             _deferred_returned_parameter_calls: dict | None = None,
                             _deferred_returned_context_calls: dict | None = None,
                             _deferred_returned_unbraced_calls: dict | None = None) -> dict:
    """Project stored evidence only; identifier references are not data-flow edges."""
    limit = max(1, int(max_items))
    budget = max(1, int(scan_limit))
    result = {
        "mode": "focused_orientation", "status": "invalid_selector",
        "selector": {"project": project, "file": file, "symbol": symbol},
        "matches": 0, "returned": 0, "omitted": 0, "candidates": [],
        "matches_semantics": "observed_matches_only_when_search_incomplete",
        "search_complete": False, "visited_records": 0,
        "unknowns": {
            "upstream_downstream_trace": "unavailable",
            "runtime_execution": "not_performed",
            "persistence_correctness": "unavailable",
            "state_flow_artifact_join": "not_source_bound",
            "absence_claim": "not_established",
            "member_index_coverage": "producer_recorded_members_and_initializer_syntax_candidates_only",
        },
        "source_binding": "not_checked",
    }
    if (not project or project in {"*", "all", "ALL"} or "::" in project
            or "/" in project or "\\" in project or not (file or symbol)):
        return result
    if file:
        if "::" in file:
            qualifier, file = file.split("::", 1)
            if qualifier != project:
                return result
        file = _focus_relative_path(file)
        if not file:
            return result
    if not isinstance(atlas, dict) or not atlas:
        result["status"] = "atlas_unavailable"
        return result
    if project not in atlas:
        result["status"] = "project_not_found"
        return result
    project_data = atlas[project]
    files = project_data.get("files") if isinstance(project_data, dict) else None
    if not isinstance(files, dict):
        result["status"] = "evidence_unavailable"
        return result

    matched = []
    invalid = False
    exhausted = False
    selected_records = None
    file_index = {}
    file_order = {}
    importer_rows = []
    file_identities = [] if isinstance(project_data.get("resolved_module_importer_index"), dict) else None
    file_identity_complete = True
    for rel, record in files.items():
        if result["visited_records"] >= budget:
            exhausted = True
            break
        result["visited_records"] += 1
        if file_identities is not None:
            file_order[rel] = len(file_order)
            identity_row = import_index_file_identity_row(rel, record)
            if identity_row is None:
                file_identity_complete = False
            else:
                file_identities.append((rel, identity_row))
        if not isinstance(record, dict):
            invalid = True
            importer_rows.append((rel, record))
            continue
        # The selection pass already visits every file. Revisit only files that
        # might carry module evidence; an explicit empty import list cannot call
        # a selected store through a named import.
        imports = record.get("import_records")
        if not isinstance(imports, list) or imports:
            importer_rows.append((rel, record))
        workspace_rel = record.get("workspace_rel") or rel
        if _focus_relative_path(rel) and _focus_relative_path(workspace_rel):
            file_target = {
                "atlas_ref": f"{project}::{rel}", "target_ref": f"{project}::{workspace_rel}",
                "target_file": workspace_rel, "source_hash": record.get("hash") or "",
            }
            for alias in {rel, workspace_rel}:
                file_index.setdefault(alias, []).append(file_target)
        if file and file not in (rel, workspace_rel):
            continue
        if not _focus_relative_path(rel) or not _focus_relative_path(workspace_rel):
            invalid = True
            continue
        symbols = record.get("symbols")
        if symbol and not isinstance(symbols, list):
            invalid = True
            continue
        rows = _focus_symbol_rows(symbols, symbol) if symbol else [(None, None, False)]
        for row, declaring, initializer_candidate in rows:
            if symbol:
                if result["visited_records"] >= budget:
                    exhausted = True
                    break
                result["visited_records"] += 1
                if not isinstance(row, dict):
                    invalid = True
                    continue
                name = row.get("name")
                if declaring and (not isinstance(name, str) or not name):
                    invalid = True
                    continue
                if initializer_candidate and not (
                    row.get("attribution") in {"direct_object_initializer", "returned_object_candidate"}
                    and type(row.get("line")) is int and type(row.get("end_line")) is int
                    and type(declaring.get("line")) is int and type(declaring.get("end_line")) is int
                    and 0 < declaring["line"] <= row["line"] <= row["end_line"] <= declaring["end_line"]
                ):
                    invalid = True
                    continue
                qualified = f"{declaring['name']}.{name}" if declaring else name
                if qualified != symbol:
                    continue
            result["matches"] += 1
            if len(matched) < limit:
                target = {
                    "atlas_ref": f"{project}::{rel}",
                    "target_ref": f"{project}::{workspace_rel}",
                    "target_file": workspace_rel,
                    "symbol": symbol or None, "source_hash": record.get("hash") or "",
                }
                if row is not None:
                    target["selection_kind"] = "initializer_member_candidate" if initializer_candidate else "member" if declaring else "symbol"
                    for output_key, source_key in (("start_line", "line"), ("end_line", "end_line")):
                        value = row.get(source_key)
                        target[output_key] = value if type(value) is int and value > 0 else None
                    if declaring:
                        target["declaring_symbol"] = declaring["name"]
                        target["span_scope"] = "initializer_member_syntax" if initializer_candidate else "member_span_unavailable_use_declaring_symbol"
                        if initializer_candidate:
                            target["owner_attribution"] = row.get("attribution")
                            target["runtime_owner_binding"] = "not_established"
                matched.append(target)
            if result["matches"] == 1:
                selected_records = (record, row, declaring, initializer_candidate)
        if exhausted:
            break
    result.update(
        candidates=matched, returned=len(matched),
        omitted=max(0, result["matches"] - len(matched)),
        search_complete=not exhausted and not invalid,
    )
    if exhausted:
        result["status"] = "incomplete_search"
    elif invalid:
        result["status"] = "evidence_unavailable"
    elif result["matches"] > 1:
        result["status"] = "ambiguous"
    elif not matched:
        result["status"] = "not_found"
    else:
        result["status"] = "selected"
        result["target"] = matched[0]
        record, row, declaring, initializer_candidate = selected_records
        result["file_context"] = _focus_signal_context(record, limit)
        result["file_context"]["attribution"] = "file_only_not_selected_symbol"
        if row is not None:
            context = _focus_symbol_context(row, limit, "selected_symbol_syntax_not_execution")
            result["symbol_context"] = context
            if declaring and declaring.get("type") == "Class" and row.get("kind") == "property":
                result["class_field_event_calls"] = _focus_class_field_event_calls(
                    row, declaring, limit, budget - result["visited_records"])
                result["visited_records"] += result["class_field_event_calls"]["visited_records"]
            initializer = (declaring or row).get("initializer_member_evidence")
            if isinstance(initializer, dict):
                limitations = initializer.get("limitations")
                limitations = limitations if isinstance(limitations, list) else []
                result["initializer_member_coverage"] = {
                    "status": initializer.get("status", "unavailable"),
                    "runtime_owner_binding": "not_established",
                    "lexical_binding": "unverified",
                    "limitations": limitations[:limit],
                    "omitted_limitations": max(0, len(limitations) - limit),
                }
                if initializer_candidate and isinstance(declaring, dict):
                    result["persist_storage_candidate"] = _focus_persist_storage_option(
                        record, row, declaring, file_index, files, project,
                        budget - result["visited_records"])
                    result["visited_records"] += result["persist_storage_candidate"]["visited_records"]
                    result["persist_hydration_candidate"] = _focus_persist_hydration_option(
                        row, declaring, budget - result["visited_records"])
                    result["visited_records"] += result["persist_hydration_candidate"]["visited_records"]
            result["import_candidates"] = _focus_import_candidates(
                record, row, file_index, limit, budget - result["visited_records"])
            result["visited_records"] += result["import_candidates"]["visited_records"]
            context["import_calls"] = _focus_import_calls(
                row, declaring, limit, budget - result["visited_records"])
            result["visited_records"] += context["import_calls"]["visited_records"]
            if initializer_candidate:
                context["setter_calls"] = _focus_zustand_setter_calls(
                    row, limit, budget - result["visited_records"])
                result["visited_records"] += context["setter_calls"]["visited_records"]
            result["direct_callees"] = _focus_direct_callees(
                context["import_calls"], result["import_candidates"], files, project,
                limit, budget - result["visited_records"], file_index=file_index)
            result["visited_records"] += result["direct_callees"]["visited_records"]
            if "local_arrow_import_call_evidence" in row:
                context["local_arrow_import_calls"] = _focus_local_arrow_import_calls(
                    record, row, limit, budget - result["visited_records"])
                result["visited_records"] += context["local_arrow_import_calls"]["visited_records"]
                result["local_arrow_import_callees"] = _focus_direct_callees(
                    context["local_arrow_import_calls"], result["import_candidates"], files, project,
                    limit, budget - result["visited_records"], file_index=file_index)
                result["local_arrow_import_callees"]["relation"] = "local_arrow_named_import_direct_export_orientation"
                result["visited_records"] += result["local_arrow_import_callees"]["visited_records"]
            if not declaring and row.get("type") in {"Function", "Arrow", "Hook", "Component"}:
                result["same_file_direct_calls"] = _focus_same_file_direct_calls(
                    record, row, result["target"], limit, budget - result["visited_records"])
                result["visited_records"] += result["same_file_direct_calls"]["visited_records"]
                file_identity = hashlib.sha256()
                if file_identities is not None and file_identity_complete:
                    for _, identity_row in sorted(file_identities):
                        file_identity.update(identity_row)
                indexed_importers = select_resolved_module_importers(
                    project_data, files, result["target"], file_order,
                    file_identity.hexdigest() if file_identities is not None and file_identity_complete else None)
                result["upstream_direct_import_calls"] = _focus_upstream_named_import_calls(
                    importer_rows if indexed_importers is None else indexed_importers,
                    file_index, row, result["target"], project, limit,
                    budget - result["visited_records"])
                result["upstream_direct_import_calls"]["importer_lookup"] = (
                    "bounded_file_import_scan" if indexed_importers is None
                    else "same_snapshot_resolved_module_importer_index")
                result["visited_records"] += result["upstream_direct_import_calls"]["visited_records"]
            if not declaring and row.get("type") == "Variable":
                result["factory_source_candidate"] = _focus_factory_source_candidate(
                    row, record, result["target"], budget - result["visited_records"])
                result["visited_records"] += result["factory_source_candidate"]["visited_records"]
                file_identity = hashlib.sha256()
                if file_identities is not None and file_identity_complete:
                    for _, identity_row in sorted(file_identities):
                        file_identity.update(identity_row)
                indexed_importers = select_resolved_module_importers(
                    project_data, files, result["target"], file_order,
                    file_identity.hexdigest() if file_identities is not None and file_identity_complete else None)
                result["event_bus_calls"] = _focus_imported_event_bus_calls(
                    importer_rows if indexed_importers is None else indexed_importers,
                    file_index, row, result["target"], project, limit,
                    budget - result["visited_records"])
                result["event_bus_calls"]["importer_lookup"] = (
                    "bounded_file_import_scan" if indexed_importers is None
                    else "same_snapshot_resolved_module_importer_index")
                result["visited_records"] += result["event_bus_calls"]["visited_records"]
                result["imported_property_event_calls"] = _focus_imported_property_event_calls(
                    importer_rows if indexed_importers is None else indexed_importers,
                    file_index, row, result["target"], project, limit,
                    budget - result["visited_records"])
                result["imported_property_event_calls"]["importer_lookup"] = (
                    "bounded_file_import_scan" if indexed_importers is None
                    else "same_snapshot_resolved_module_importer_index")
                result["visited_records"] += result["imported_property_event_calls"]["visited_records"]
            if initializer_candidate:
                result["upstream_action_calls"] = _focus_same_file_store_action_calls(
                    record, declaring, row, result["target"], limit,
                    budget - result["visited_records"])
                result["visited_records"] += result["upstream_action_calls"]["visited_records"]
                file_identity = hashlib.sha256()
                if file_identities is not None and file_identity_complete:
                    for _, identity_row in sorted(file_identities):
                        file_identity.update(identity_row)
                indexed_importers = select_resolved_module_importers(
                    project_data, files, result["target"], file_order,
                    file_identity.hexdigest() if file_identities is not None and file_identity_complete else None)
                result["cross_file_action_calls"] = _focus_cross_file_store_action_calls(
                    importer_rows if indexed_importers is None else indexed_importers,
                    file_index, declaring, row, result["target"], project, limit,
                    budget - result["visited_records"])
                result["cross_file_action_calls"]["importer_lookup"] = (
                    "bounded_file_import_scan" if indexed_importers is None
                    else "same_snapshot_resolved_module_importer_index")
                result["visited_records"] += result["cross_file_action_calls"]["visited_records"]
                result["hook_selector_candidates"] = _focus_store_hook_selectors(
                    record, importer_rows if indexed_importers is None else indexed_importers,
                    file_index, declaring, row, result["target"], project, limit,
                    budget - result["visited_records"])
                result["hook_selector_candidates"]["importer_lookup"] = (
                    "bounded_file_import_scan" if indexed_importers is None
                    else "same_snapshot_resolved_module_importer_index")
                result["visited_records"] += result["hook_selector_candidates"]["visited_records"]
                result["rehydrate_call_candidates"] = _focus_same_file_rehydrate_calls(
                    record, declaring, row, result["target"], limit,
                    budget - result["visited_records"])
                result["visited_records"] += result["rehydrate_call_candidates"]["visited_records"]
                result["cross_file_rehydrate_call_candidates"] = _focus_cross_file_rehydrate_calls(
                    importer_rows if indexed_importers is None else indexed_importers,
                    file_index, declaring, row, result["target"], project, limit,
                    budget - result["visited_records"])
                result["cross_file_rehydrate_call_candidates"]["importer_lookup"] = (
                    "bounded_file_import_scan" if indexed_importers is None
                    else "same_snapshot_resolved_module_importer_index")
                result["visited_records"] += result["cross_file_rehydrate_call_candidates"]["visited_records"]
            if "local_arrow_result_use_evidence" in row:
                if _deferred_result_use is not None:
                    # MCP settles pre-existing return/packet budgets first. Pass
                    # selected references, not another Atlas load or symbol walk.
                    _deferred_result_use.update(record=record, symbol=row)
                else:
                    context["local_arrow_result_uses"] = _focus_local_arrow_result_uses(
                        record, row, limit, budget - result["visited_records"],
                        context.get("local_arrow_import_calls", {}), result.get("local_arrow_import_callees", {}))
                    result["visited_records"] += context["local_arrow_result_uses"]["visited_records"]
            if "local_arrow_sibling_call_evidence" in row:
                if _deferred_local_sibling is not None:
                    _deferred_local_sibling.update(record=record, symbol=row)
                else:
                    context["local_arrow_sibling_calls"] = _focus_local_arrow_sibling_calls(
                        record, row, limit, budget - result["visited_records"])
                    result["visited_records"] += context["local_arrow_sibling_calls"]["visited_records"]
            if "jsx_prop_callback_evidence" in row:
                if _deferred_jsx_callbacks is not None:
                    _deferred_jsx_callbacks.update(record=record, symbol=row, files=files, project=project,
                                                   imports=result["import_candidates"])
                else:
                    context["jsx_prop_callbacks"] = _focus_jsx_prop_callbacks(
                        record, row, result["import_candidates"], files, project,
                        limit, budget - result["visited_records"])
                    result["visited_records"] += context["jsx_prop_callbacks"]["visited_records"]
            if "returned_local_callback_evidence" in row:
                if _deferred_returned_callbacks is not None:
                    _deferred_returned_callbacks.update(record=record, symbol=row)
                else:
                    context["returned_local_callbacks"] = _focus_returned_local_callbacks(
                        record, row, limit, budget - result["visited_records"])
                    result["visited_records"] += context["returned_local_callbacks"]["visited_records"]
            if "call_result_property_binding_evidence" in row:
                if _deferred_call_result_bindings is not None:
                    _deferred_call_result_bindings.update(record=record, symbol=row)
                else:
                    context["call_result_property_bindings"] = _focus_call_result_property_bindings(
                        record, row, limit, budget - result["visited_records"])
                    result["visited_records"] += context["call_result_property_bindings"]["visited_records"]
            if "call_result_alias_invocation_evidence" in row:
                if _deferred_alias_invocations is not None:
                    _deferred_alias_invocations.update(record=record, symbol=row)
                else:
                    context["call_result_alias_invocations"] = _focus_call_result_alias_invocations(
                        record, row, limit, budget - result["visited_records"])
                    result["visited_records"] += context["call_result_alias_invocations"]["visited_records"]
            if "call_result_nested_callback_alias_invocation_evidence" in row:
                if _deferred_nested_alias_invocations is not None:
                    _deferred_nested_alias_invocations.update(record=record, symbol=row)
                else:
                    context["call_result_nested_callback_alias_invocations"] = _focus_call_result_alias_invocations(
                        record, row, limit, budget - result["visited_records"], _nested=True)
                    result["visited_records"] += context["call_result_nested_callback_alias_invocations"]["visited_records"]
            if "parameter_config_callback_forwarding_evidence" in row:
                if _deferred_parameter_callbacks is not None:
                    _deferred_parameter_callbacks.update(record=record, symbol=row)
                else:
                    context["parameter_config_callback_forwardings"] = _focus_parameter_config_callbacks(
                        record, row, limit, budget - result["visited_records"])
                    result["visited_records"] += context["parameter_config_callback_forwardings"]["visited_records"]
            if "call_result_optional_rest_alias_invocation_evidence" in row:
                if _deferred_optional_rest_alias_invocations is not None:
                    _deferred_optional_rest_alias_invocations.update(record=record, symbol=row)
                else:
                    context["call_result_optional_rest_alias_invocations"] = _focus_call_result_alias_invocations(
                        record, row, limit, budget - result["visited_records"], _optional_rest=True)
                    result["visited_records"] += context["call_result_optional_rest_alias_invocations"]["visited_records"]
            if "call_result_optional_alias_invocation_evidence" in row:
                if _deferred_optional_alias_invocations is not None:
                    _deferred_optional_alias_invocations.update(record=record, symbol=row)
                else:
                    context["call_result_optional_alias_invocations"] = _focus_call_result_alias_invocations(
                        record, row, limit, budget - result["visited_records"], _optional=True)
                    result["visited_records"] += context["call_result_optional_alias_invocations"]["visited_records"]
            if "returned_callback_parameter_optional_call_evidence" in row:
                if _deferred_returned_parameter_calls is not None:
                    _deferred_returned_parameter_calls.update(record=record, symbol=row)
                else:
                    context["returned_callback_parameter_optional_calls"] = _focus_returned_callback_parameter_optional(
                        record, row, limit, budget - result["visited_records"])
                    result["visited_records"] += context["returned_callback_parameter_optional_calls"]["visited_records"]
            if "returned_callback_parameter_context_call_evidence" in row:
                if _deferred_returned_context_calls is not None:
                    _deferred_returned_context_calls.update(record=record, symbol=row)
                else:
                    context["returned_callback_parameter_context_calls"] = _focus_returned_callback_parameter_context(
                        record, row, limit, budget - result["visited_records"])
                    result["visited_records"] += context["returned_callback_parameter_context_calls"]["visited_records"]
            if "returned_callback_parameter_unbraced_if_call_evidence" in row:
                if _deferred_returned_unbraced_calls is not None:
                    _deferred_returned_unbraced_calls.update(record=record, symbol=row)
                else:
                    context["returned_callback_parameter_unbraced_if_calls"] = _focus_returned_callback_parameter_unbraced(
                        record, row, limit, budget - result["visited_records"])
                    result["visited_records"] += context["returned_callback_parameter_unbraced_if_calls"]["visited_records"]
    return result
