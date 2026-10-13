from __future__ import annotations

import argparse
import json
import posixpath
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.core.audit_rules import build_rule_taxonomy
from tools.core.agent_packet_budget import COMPACT_AGENT_PACKET_TOKENS, context_budget_profile
from tools.core.agent_snippet_renderer import render_target_source_snippets
from tools.core.atlas_io import load_atlas_data
from tools.core.config import DOCTRINE, RAW_DIR, REPORTS_DIR, ROOT as ANALYZED_REPOSITORY_ROOT, save_json_atomic, save_text_atomic
from tools.core.honesty_telemetry import record_honesty_event
from tools.core.json_io import load_json_file
from tools.core.operational_limits import sqlite_read_timeout_seconds


AGENT_INSPECTION_MAX_TARGET_SPANS = 6


def _project_client_local_parser_syntax_evidence(file_info: Any, source_status: dict[str, Any], *, input_sink: bool,
                                               imported: bool = False, source_candidate=None) -> dict[str, Any]:
    """Sample stored syntax, never rescan source or infer validation authority."""
    base = {"status": "unavailable", "reason": "metadata_missing", "records": [],
            "record_count": None, "shown": 0, "omitted": None,
            "projection_scope": "file_wide_record_sample_not_requested_line_range",
            "coverage": "finite_grammar_not_exhaustive", "offset_unit": "utf8_bytes",
            "source_binding": "unavailable", "schema_authority": "not_established",
            "runtime_proof": "not_performed", "edit_authority": "not_granted",
            "empty_result_meaning": "No recorded finite-grammar relation; not proof of clean code or absent validation."}
    if input_sink:
        base.update(selected_value_origin="json_input", call_result_use="not_selected_by_this_sink",
                    parser_effect_on_input="not_established")
    if imported:
        base.update(parser_effect_on_input="not_established")
    if not isinstance(file_info, dict):
        return base
    evidence = file_info.get("client_imported_parser_evidence" if imported else "client_local_parser_input_sink_evidence" if input_sink else "client_local_parser_result_evidence")
    if not isinstance(evidence, dict):
        return base
    if evidence.get("status") != "observed":
        return {**base, "reason": "producer_evidence_unavailable"}
    identity = evidence.get("source_hash")
    records = evidence.get("records")
    version = "v1-const-parse-call-input-to-state" if input_sink else "v1-const-sync-call-result-to-state"
    scope = "finite_same_file_parse_call_json_input_to_react_state" if input_sink else "finite_same_file_sync_call_result_to_react_state"
    if imported:
        version, scope = "v1-named-import-parse-to-state", "finite_same_file_named_import_parse_json_to_react_state"
    if (evidence.get("version") != version
            or evidence.get("scope") != scope
            or evidence.get("offset_unit") != "utf8_bytes"
            or not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f]{64}", identity)
            or identity != file_info.get("hash")
            or not isinstance(evidence.get("ast_contract_version"), str)
            or not evidence["ast_contract_version"]
            or evidence["ast_contract_version"] != file_info.get("ast_contract_version")
            or evidence.get("schema_authority") != "not_established"
            or evidence.get("runtime_proof") != "not_performed"
            or (imported and evidence.get("parser_effect_on_input") != "not_established")
            or not isinstance(records, list)
            or (input_sink and any(evidence.get(key) != base[key] for key in
                ("selected_value_origin", "call_result_use", "parser_effect_on_input")))):
        return {**base, "reason": "metadata_identity_or_contract_invalid"}
    if (source_status.get("source_snapshot_hash") != identity
            or source_status.get("source_snapshot_status") != "ok"
            or source_status.get("drift_check_status") != "match"
            or not all(source_status.get(key) for key in ("inside_root", "exists", "indexed"))):
        return {**base, "reason": "source_binding_unavailable"}
    result = {**base, "status": "observed", "reason": "stored_syntax_only",
              "source_binding": "snapshot_and_live_match", "source_hash": identity,
              "version": evidence["version"], "ast_contract_version": evidence["ast_contract_version"],
              "record_count": len(records), "omitted": len(records),
              "syntax_claim": ("This setter selects the parse call's JSON input, not that call's result. The call can reject or mutate the input; validation bypass is not established."
                               if input_sink else "Recorded local call-result consumption by a typed React setter; a parse-named call may be passthrough.")}
    if imported:
        result["syntax_claim"] = "Named-import parse-call syntax with selected input or result. Direct export orientation is not schema, SDK, HTTP or runtime proof."
    roles = ("fetch", "json", "parser_call", "result", "sink", "argument")
    shown: list[dict[str, Any]] = []
    for record in records[:AGENT_INSPECTION_MAX_TARGET_SPANS]:
        if not isinstance(record, dict) or not isinstance(record.get("spans"), dict):
            return {**base, "reason": "projected_record_invalid"}
        field_path, spans = record.get("field_path"), record["spans"]
        if not isinstance(field_path, list) or any(not isinstance(key, str) for key in field_path):
            return {**base, "reason": "projected_record_invalid"}
        selected = {}
        for role in roles:
            span = spans.get(role)
            if (not isinstance(span, dict) or set(span) != {"start", "end", "line", "end_line"}
                    or any(type(value) is not int for value in span.values())
                    or not 0 <= span["start"] < span["end"]
                    or not 1 <= span["line"] <= span["end_line"]):
                return {**base, "reason": "projected_record_invalid"}
            selected[role] = dict(span)
        item = {"field_path": list(field_path), "spans": selected,
                "source_followup": {"tool": "inspect_file", "file_path": source_status.get("target_ref") or "",
                    "line_start": selected["parser_call"]["line"], "line_end": selected["sink"]["end_line"]}}
        if imported:
            binding, origin = record.get("import_binding"), record.get("selected_value_origin")
            if (not isinstance(origin, str) or origin not in {"parser_call_result", "json_input"} or not isinstance(binding, dict)
                    or set(binding) != {"source", "kind", "imported_name", "local_name", "type_only"}
                    or binding["kind"] != "named" or binding["type_only"] is not False
                    or any(not isinstance(binding[key], str) or not binding[key] for key in
                           ("source", "imported_name", "local_name"))):
                return {**base, "reason": "projected_import_binding_invalid"}
            declared = file_info.get("direct_import_binding_evidence")
            bindings = declared.get("records") if isinstance(declared, dict) and declared.get("status") == "observed" else None
            matches = [row for row in bindings if isinstance(row, dict)
                       and row.get("source") == binding["source"]
                       and row.get("localName") == binding["local_name"]
                       and row.get("importedName") == binding["imported_name"]
                       and row.get("kind") == "named" and row.get("typeOnly") is False
                       and row.get("bindingScope") == "file_top_level_import_declaration"] if isinstance(bindings, list) else []
            if len(matches) != 1:
                return {**base, "reason": "projected_direct_binding_conflict"}
            item.update(import_binding=dict(binding), selected_value_origin=origin,
                        parser_source_candidate=(source_candidate(record) if source_candidate else
                            {"status": "unresolved", "reason": "source_candidate_not_requested", "source_binding": "unavailable"}))
        # Reuse the compact packet allowance for this section, including its
        # explicit limits. Do not truncate a path/span or claim omitted rows.
        candidate = {**result, "records": [*shown, item], "shown": len(shown) + 1,
                     "omitted": len(records) - len(shown) - 1}
        if context_budget_profile(json.dumps(candidate, ensure_ascii=False),
                                  budget_tokens=COMPACT_AGENT_PACKET_TOKENS)["status"] != "pass":
            result["reason"] = "compact_budget_omission"
            break
        shown.append(item)
    return {**result, "records": shown, "shown": len(shown), "omitted": len(records) - len(shown)}


def project_client_local_parser_result_evidence(file_info: Any, source_status: dict[str, Any]) -> dict[str, Any]:
    """Retain the existing result-selected projection contract."""
    return _project_client_local_parser_syntax_evidence(file_info, source_status, input_sink=False)


def _project_response_json_return_evidence(file_info: Any, source_status: dict[str, Any], *,
                                         selected_target=None, scan_limit=None) -> dict[str, Any]:
    """Return stored source-bound expression syntax, never resolved async data."""
    base = {"status": "unavailable", "reason": "metadata_missing", "records": [],
            "record_count": None, "shown": 0, "omitted": None,
            "projection_scope": "file_wide_record_sample_not_requested_line_range",
            "coverage": "finite_grammar_not_exhaustive", "offset_unit": "utf8_bytes",
            "source_binding": "unavailable", "schema_authority": "not_established",
            "promise_resolution": "not_established", "caller_state_binding": "not_established",
            "runtime_proof": "not_performed", "edit_authority": "not_granted"}
    evidence = file_info.get("response_json_return_evidence") if isinstance(file_info, dict) else None
    if selected_target is not None:
        base["projection_scope"] = "exact_selected_callable_return_expression"
    if not isinstance(evidence, dict):
        return base
    if evidence.get("status") != "observed":
        return {**base, "reason": "producer_evidence_unavailable"}
    identity, records = evidence.get("source_hash"), evidence.get("records")
    if (evidence.get("version") != "v1-three-statement-async-json-return"
            or evidence.get("scope") != "finite_top_level_async_json_return_expression"
            or evidence.get("offset_unit") != "utf8_bytes"
            or not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f]{64}", identity)
            or identity != file_info.get("hash")
            or not isinstance(evidence.get("ast_contract_version"), str) or not evidence["ast_contract_version"]
            or evidence["ast_contract_version"] != file_info.get("ast_contract_version")
            or any(evidence.get(key) != base[key] for key in
                   ("schema_authority", "promise_resolution", "caller_state_binding", "runtime_proof"))
            or not isinstance(records, list)):
        return {**base, "reason": "metadata_identity_or_contract_invalid"}
    if selected_target is not None:
        from tools.core.atlas_ast_contract import AST_CONTRACT_VERSION
        if (not isinstance(selected_target, dict)
                or selected_target.get("source_hash") != identity
                or selected_target.get("ast_contract_version") != evidence["ast_contract_version"]
                or evidence["ast_contract_version"] != AST_CONTRACT_VERSION
                or not isinstance(file_info.get("parser_evidence"), dict)
                or file_info["parser_evidence"].get("status") != "observed"
                or selected_target.get("callable_kind") not in ("async_function", "const_async_arrow")
                or not isinstance(selected_target.get("callable_span"), dict)
                or set(selected_target["callable_span"]) != {"start", "end", "line", "end_line"}
                or any(type(value) is not int for value in selected_target["callable_span"].values())
                or not 0 <= selected_target["callable_span"]["start"] < selected_target["callable_span"]["end"]
                or not 1 <= selected_target["callable_span"]["line"] <= selected_target["callable_span"]["end_line"]):
            return {**base, "reason": "selected_callable_identity_unavailable"}
        if type(scan_limit) is not int or len(records) > scan_limit:
            return {**base, "reason": "selected_return_scan_budget_exhausted"}
    if (source_status.get("source_snapshot_hash") != identity or source_status.get("source_snapshot_status") != "ok"
            or source_status.get("drift_check_status") != "match"
            or not all(source_status.get(key) for key in ("inside_root", "exists", "indexed"))):
        return {**base, "reason": "source_binding_unavailable"}
    if not records:
        if selected_target is not None:
            return {**base, "reason": "selected_callable_return_not_recorded", "record_count": 0, "omitted": 0}
        # Do not spend the older lanes' record allowance on a verbose empty
        # fourth header. Empty is still observed only after identity binding.
        return {key: value for key, value in {**base, "status": "observed",
                "reason": "no_finite_grammar_return_record", "source_binding": "snapshot_and_live_match",
                "record_count": 0, "shown": 0, "omitted": 0}.items()
                if key not in {"projection_scope", "offset_unit", "edit_authority"}}
    roles = {"callable", "callable_binding", "body", "fetch", "fetch_binding", "json", "json_binding",
             "response_ref", "return_statement", "return_expression", "selected_expression", "json_ref"}
    shown = []
    matched = 0
    for row in records if selected_target is not None else records[:AGENT_INSPECTION_MAX_TARGET_SPANS]:
        if (not isinstance(row, dict) or set(row) != {"callable_name", "callable_kind", "field_path", "return_type_assertion", "spans"}
                or not isinstance(row["callable_name"], str) or not row["callable_name"]
                or row["callable_kind"] not in ("async_function", "const_async_arrow")
                or type(row["return_type_assertion"]) is not bool
                or not isinstance(row["field_path"], list) or any(not isinstance(key, str) for key in row["field_path"])):
            return {**base, "reason": "projected_record_invalid"}
        spans = row["spans"]
        if (not isinstance(spans, dict) or set(spans) != roles | ({"type_assertion"} if row["return_type_assertion"] else set())
                or any(not isinstance(value, dict) or set(value) != {"start", "end", "line", "end_line"}
                       or any(type(number) is not int for number in value.values())
                       or not 0 <= value["start"] < value["end"] or not 1 <= value["line"] <= value["end_line"]
                       for value in spans.values())):
            return {**base, "reason": "projected_record_invalid"}
        # Compact orientation keeps exact selected expression and producer spans;
        # complete native roles remain in existing Atlas metadata.
        containment = [("callable", "callable_binding"), ("callable", "body"), ("body", "fetch"), ("body", "json"),
                       ("body", "return_statement"), ("fetch", "fetch_binding"), ("json", "json_binding"),
                       ("json", "response_ref"), ("return_statement", "return_expression"),
                       ("return_expression", "selected_expression"), ("selected_expression", "json_ref")]
        if row["return_type_assertion"]:
            containment += [("return_expression", "type_assertion"), ("type_assertion", "selected_expression")]
        if (any(not spans[outer]["start"] <= spans[inner]["start"] < spans[inner]["end"] <= spans[outer]["end"]
                for outer, inner in containment)
                or any(spans[earlier]["end"] > spans[later]["start"] for earlier, later in
                       [("callable_binding", "body"), ("fetch", "json"), ("json", "return_statement")])):
            return {**base, "reason": "projected_relation_invalid"}
        if selected_target is not None:
            if (row["callable_name"] != selected_target.get("symbol")
                    or row["callable_kind"] != selected_target["callable_kind"]
                    or spans["callable"] != selected_target["callable_span"]):
                continue
            matched += 1
        selected = {role: dict(spans[role]) for role in ("fetch", "json", "return_statement", "selected_expression", "json_ref")}
        if row["return_type_assertion"]:
            selected["type_assertion"] = dict(spans["type_assertion"])
        shown.append({**{key: row[key] for key in ("callable_name", "callable_kind", "return_type_assertion")},
            "field_path": list(row["field_path"]), "spans": selected,
            "source_followup": {"tool": "inspect_file", "file_path": source_status.get("target_ref") or "",
                "line_start": spans["fetch"]["line"], "line_end": spans["return_statement"]["end_line"]}})
    if selected_target is not None and matched != 1:
        return {**base, "reason": "selected_callable_return_ambiguous" if matched else "selected_callable_return_not_recorded",
                "record_count": len(records), "omitted": len(records)}
    return {**base, "status": "observed", "reason": "stored_return_expression_syntax_only",
            "source_hash": identity, "source_binding": "snapshot_and_live_match",
            "version": evidence["version"], "ast_contract_version": evidence["ast_contract_version"],
            "records": shown, "record_count": len(records), "shown": len(shown), "omitted": len(records) - len(shown)}


def project_client_local_parser_evidence(file_info: Any, source_status: dict[str, Any], *, source_candidate=None) -> dict[str, Any]:
    """All syntax lanes share the existing compact allowance and record cap."""
    projections = {
        "client_local_parser_result_evidence": project_client_local_parser_result_evidence(file_info, source_status),
        "client_local_parser_input_sink_evidence": _project_client_local_parser_syntax_evidence(file_info, source_status, input_sink=True),
    }
    # Legacy files retain their old two-section packet; absent new metadata is
    # not an empty observed lane and must not be synthesized as such.
    if isinstance(file_info, dict) and "client_imported_parser_evidence" in file_info:
        projections["client_imported_parser_evidence"] = _project_client_local_parser_syntax_evidence(
            file_info, source_status, input_sink=False, imported=True, source_candidate=source_candidate)
    if isinstance(file_info, dict) and "response_json_return_evidence" in file_info:
        projections["response_json_return_evidence"] = _project_response_json_return_evidence(file_info, source_status)
    sections = {key: {**value, "records": [], "shown": 0, "omitted": value["record_count"]}
                for key, value in projections.items()}
    if context_budget_profile(json.dumps(sections, ensure_ascii=False),
                              budget_tokens=COMPACT_AGENT_PACKET_TOKENS)["status"] != "pass":
        return {key: {**(_project_response_json_return_evidence(None, source_status) if key == "response_json_return_evidence" else
                        _project_client_local_parser_syntax_evidence(None, source_status, input_sink=index == 1, imported=index == 2)),
                      "reason": "compact_projection_header_over_budget"}
                for index, key in enumerate(sections)}
    shown = 0
    for key, projected in projections.items():
        for row in projected["records"]:
            if shown >= AGENT_INSPECTION_MAX_TARGET_SPANS:
                sections[key]["reason"] = "shared_record_budget_omission"
                break
            section = sections[key]
            candidate = {**section, "records": [*section["records"], row],
                         "shown": section["shown"] + 1, "omitted": section["omitted"] - 1}
            budgeted = {name: {**value, "reason": "shared_record_budget_omission"}
                        if value["status"] == "observed" and value["omitted"] else value
                        for name, value in {**sections, key: candidate}.items()}
            if context_budget_profile(json.dumps(budgeted, ensure_ascii=False),
                                      budget_tokens=COMPACT_AGENT_PACKET_TOKENS)["status"] != "pass":
                sections[key]["reason"] = "compact_budget_omission"
                break
            sections[key] = candidate
            shown += 1
    return sections


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _with_context_budget(yaml_lines: list[str]) -> list[str]:
    profile = context_budget_profile("\n".join(yaml_lines))
    return [
        *yaml_lines,
        "context_budget:",
        f"  estimator: {json.dumps(profile.get('estimator') or '', ensure_ascii=False)}",
        f"  estimated_tokens: {int(profile.get('estimated_tokens') or 0)}",
        f"  budget_tokens: {int(profile.get('budget_tokens') or 0)}",
        f"  status: {json.dumps(profile.get('status') or 'unknown', ensure_ascii=False)}",
        f"  exact_tokenizer: {str(bool(profile.get('exact_tokenizer'))).lower()}",
    ]


def _norm(value: str) -> str:
    normalized = str(value or "").replace("\\", "/").strip().strip("/")
    if not normalized:
        return ""
    if "::" in normalized:
        project, rel = normalized.split("::", 1)
        rel_norm = posixpath.normpath(rel.strip("/")) if rel else ""
        return f"{project}::{'' if rel_norm == '.' else rel_norm}"
    path_norm = posixpath.normpath(normalized)
    return "" if path_norm == "." else path_norm


def _path_variants(value: str) -> set[str]:
    normalized = _norm(value).lower()
    variants = {normalized} if normalized else set()
    if normalized.startswith("src/"):
        variants.add(normalized[4:])
    else:
        variants.add(f"src/{normalized}")
    return {item for item in variants if item}


def _split_scoped(value: str) -> tuple[str | None, str]:
    normalized = _norm(value)
    if "::" in normalized:
        project, rel_path = normalized.split("::", 1)
        return project or None, rel_path
    return None, normalized


def _blast_matches_for_context(
    blast: dict[str, Any],
    file_contexts: list[dict[str, Any]],
    target: str,
) -> list[dict[str, Any]]:
    rows = blast.get("blast_radius", []) if isinstance(blast, dict) else []
    if not isinstance(rows, list):
        return []
    context_nodes = {
        _norm(str(row.get("atlas_node") or "")).lower()
        for row in file_contexts
        if isinstance(row, dict) and str(row.get("atlas_node") or "").strip()
    }
    target_text = _norm(target).lower()
    matches: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_file = _norm(str(row.get("file") or "")).lower()
        if (context_nodes and row_file in context_nodes) or (
            not context_nodes and target_text and target_text in row_file
        ):
            matches.append({"key": row.get("file"), "value": row})
    return matches


def _iter_atlas_files(atlas: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for project, payload in (atlas or {}).items():
        if not isinstance(payload, dict):
            continue
        files = payload.get("files") or {}
        if not isinstance(files, dict):
            continue
        for file_key, info in files.items():
            if not isinstance(info, dict):
                info = {}
            workspace_rel = _norm(str(info.get("workspace_rel") or ""))
            atlas_rel = _norm(str(file_key))
            rows.append(
                {
                    "project": project,
                    "file": atlas_rel,
                    "workspace_rel": workspace_rel or atlas_rel,
                    "atlas_node": f"{project}::{atlas_rel}",
                    "type": info.get("type"),
                    "loc": info.get("loc"),
                }
            )
    return rows


def _file_contexts(atlas: dict[str, Any], target: str) -> list[dict[str, Any]]:
    project_filter, rel_target = _split_scoped(target)
    target_variants = _path_variants(rel_target)
    matches: list[dict[str, Any]] = []
    for row in _iter_atlas_files(atlas):
        if project_filter and row.get("project") != project_filter:
            continue
        row_variants = _path_variants(str(row.get("file") or "")) | _path_variants(str(row.get("workspace_rel") or ""))
        if target_variants.intersection(row_variants):
            matches.append(row)
    exact_workspace = [
        row for row in matches
        if _norm(str(row.get("workspace_rel") or "")).lower() == _norm(rel_target).lower()
    ]
    if exact_workspace:
        return exact_workspace
    # Prefer exact workspace-relative matches over source-root-relative fallbacks.
    matches.sort(key=lambda row: 0 if _norm(str(row.get("workspace_rel"))).lower() == _norm(rel_target).lower() else 1)
    return matches


def _file_contexts_from_symbol_rows(atlas: dict[str, Any], rows: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    contexts: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        project = str(row.get("project") or "")
        candidates = [
            str(row.get("workspace_rel") or ""),
            str(row.get("file") or ""),
        ]
        for candidate in candidates:
            if not candidate:
                continue
            scoped_candidate = f"{project}::{candidate}" if project else candidate
            for context in _file_contexts(atlas, scoped_candidate):
                key = (
                    str(context.get("project") or ""),
                    _norm(str(context.get("file") or "")),
                    _norm(str(context.get("workspace_rel") or context.get("file") or "")),
                )
                if key in seen:
                    continue
                seen.add(key)
                contexts.append(context)
                if len(contexts) >= limit:
                    return contexts
            if contexts and _norm(str(contexts[-1].get("workspace_rel") or contexts[-1].get("file") or "")) == _norm(candidate):
                break
    return contexts


def _matches_file_context(item: dict[str, Any], contexts: list[dict[str, Any]]) -> bool:
    if not contexts:
        return False
    fields = [
        item.get("file"),
        item.get("scoped_file"),
        item.get("target_path"),
        item.get("target_path_suggestion"),
        item.get("source_path"),
        ((item.get("evidence") or {}) if isinstance(item.get("evidence"), dict) else {}).get("source_contract_file"),
    ]
    item_project = str(item.get("project") or "")
    normalized_fields = {_norm(str(field)).lower() for field in fields if field}
    for context in contexts:
        project = str(context.get("project") or "")
        if item_project and project and item_project != project:
            continue
        variants = _path_variants(str(context.get("file") or "")) | _path_variants(str(context.get("workspace_rel") or ""))
        atlas_node = _norm(str(context.get("atlas_node") or "")).lower()
        if atlas_node:
            variants.add(atlas_node)
        if normalized_fields.intersection(variants):
            return True
    return False


def _contains_path(item: dict[str, Any], target: str) -> bool:
    needles = _path_variants(target)
    if not needles:
        return False
    fields = [
        item.get("file"),
        item.get("scoped_file"),
        item.get("target_path"),
        item.get("target_path_suggestion"),
        item.get("source_path"),
        ((item.get("evidence") or {}) if isinstance(item.get("evidence"), dict) else {}).get("source_contract_file"),
    ]
    return any(any(needle in _norm(str(field)).lower() for needle in needles) for field in fields if field)


def _under_folder(item: dict[str, Any], folder: str) -> bool:
    prefixes = {item.rstrip("/") for item in _path_variants(folder)}
    if not prefixes:
        return False
    fields = [item.get("file"), item.get("scoped_file"), item.get("target_path"), item.get("target_path_suggestion")]
    for field in fields:
        normalized = _norm(str(field)).lower()
        for prefix in prefixes:
            if normalized.startswith(prefix) or f"::{prefix}" in normalized or f"/{prefix}/" in normalized:
                return True
    return False


def _iter_atlas_symbols(atlas: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for project, payload in (atlas or {}).items():
        if not isinstance(payload, dict):
            continue
        files = payload.get("files") or {}
        workspace_by_file: dict[str, str] = {}
        if isinstance(files, dict):
            for file_key, file_info in files.items():
                file_rel = _norm(str(file_key))
                workspace_rel = file_rel
                if isinstance(file_info, dict):
                    workspace_rel = _norm(str(file_info.get("workspace_rel") or file_rel))
                if file_rel:
                    workspace_by_file[file_rel] = workspace_rel
        for info in (payload.get("symbols") or []):
            if not isinstance(info, dict):
                continue
            symbol = str(info.get("name") or "")
            if not symbol:
                continue
            file_rel = _norm(str(info.get("file") or ""))
            workspace_rel = workspace_by_file.get(file_rel, file_rel)
            rows.append(
                {
                    "project": project,
                    "symbol": symbol,
                    "file": file_rel,
                    "workspace_rel": workspace_rel,
                    "target_ref": f"{project}::{workspace_rel}" if project and workspace_rel else "",
                    "type": info.get("type"),
                    "dependencies": info.get("dependencies", []),
                }
            )
    return rows


def _iter_atlas_file_rows(atlas: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for project, payload in (atlas or {}).items():
        if not isinstance(payload, dict):
            continue
        files = payload.get("files") or {}
        if not isinstance(files, dict):
            continue
        for file_key, file_info in files.items():
            file_rel = _norm(str(file_key))
            workspace_rel = file_rel
            imports = []
            if isinstance(file_info, dict):
                workspace_rel = _norm(str(file_info.get("workspace_rel") or file_rel))
                imports = file_info.get("imports") if isinstance(file_info.get("imports"), list) else []
            rows.append(
                {
                    "project": project,
                    "symbol": Path(workspace_rel).name,
                    "file": file_rel,
                    "workspace_rel": workspace_rel,
                    "target_ref": f"{project}::{workspace_rel}" if project and workspace_rel else "",
                    "type": "File",
                    "dependencies": imports,
                }
            )
    return rows


def _risk_summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        tier = str(row.get("risk_tier") or row.get("confidence") or "unknown")
        counts[tier] = counts.get(tier, 0) + 1
    return counts


def _list_sample(value: Any, limit: int = 8) -> list[Any]:
    return list(value[:limit]) if isinstance(value, list) else []


def _workspace_rel_index(atlas: dict[str, Any]) -> dict[str, str]:
    index: dict[str, str] = {}
    for row in _iter_atlas_files(atlas if isinstance(atlas, dict) else {}):
        project = str(row.get("project") or "")
        atlas_rel = _norm(str(row.get("file") or ""))
        workspace_rel = _norm(str(row.get("workspace_rel") or atlas_rel))
        if project and atlas_rel and workspace_rel:
            index[f"{project}::{atlas_rel}"] = workspace_rel
    return index


def _target_file_for_row(row: dict[str, Any], workspace_index: dict[str, str] | None = None) -> str:
    workspace_index = workspace_index or {}
    scoped = _norm(str(row.get("scoped_file") or ""))
    project = str(row.get("project") or row.get("project_key") or "")
    rel = _norm(str(row.get("workspace_rel") or row.get("repo_relative_path") or row.get("file") or ""))
    candidates = [scoped]
    if project and rel:
        candidates.append(f"{project}::{rel}")
    for candidate in candidates:
        if candidate in workspace_index:
            return workspace_index[candidate]
    if "::" in scoped:
        scoped_project, scoped_rel = scoped.split("::", 1)
        if f"{scoped_project}::{scoped_rel}" in workspace_index:
            return workspace_index[f"{scoped_project}::{scoped_rel}"]
    return rel


def _target_ref_for_row(row: dict[str, Any], target_file: str) -> str:
    project = str(row.get("project") or row.get("project_key") or "")
    return f"{project}::{target_file}" if project and target_file else target_file


def _compact_symbol_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "project": row.get("project"),
        "symbol": row.get("symbol"),
        "file": row.get("file"),
        "workspace_rel": row.get("workspace_rel") or row.get("file"),
        "target_ref": row.get("target_ref"),
        "type": row.get("type"),
        "line": row.get("line"),
        "char": row.get("char"),
        "end_line": row.get("end_line"),
        "source_lines": row.get("source_lines"),
        "dependencies_sample": _list_sample(row.get("dependencies"), 8),
    }


def _sqlite_symbol_span_index(raw_dir: Path) -> dict[tuple[str, str, str], dict[str, Any]]:
    db_path = raw_dir / "codemaps.db"
    if not db_path.exists():
        return {}
    try:
        with sqlite3.connect(db_path, timeout=float(sqlite_read_timeout_seconds())) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT files.project_key, files.rel_path, symbols.name,
                       symbols.line, symbols.char, symbols.end_line,
                       symbols.source_lines
                FROM symbols
                JOIN files ON files.file_id = symbols.file_id;
                """
            ).fetchall()
    except Exception as exc:
        record_honesty_event(
            component="inspect_target",
            category="storage_fallback",
            operation="sqlite_symbol_span_index",
            subject=str(db_path),
            reason="SQLite symbol span lookup failed while enriching target inspection.",
            fallback="atlas_projection_without_sqlite_spans",
            claim_impact="target_inspection_symbol_spans_may_be_incomplete",
            evidence_source="tools.inspect_target",
            exception=exc,
        )
        return {}
    index: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        key = (
            str(row["project_key"] or ""),
            _norm(str(row["rel_path"] or "")),
            str(row["name"] or ""),
        )
        if all(key):
            index[key] = {
                "line": row["line"],
                "char": row["char"],
                "end_line": row["end_line"],
                "source_lines": row["source_lines"],
                "source": "sqlite_symbols",
            }
    return index


def _enrich_symbol_rows_from_sqlite(rows: list[dict[str, Any]], raw_dir: Path) -> list[dict[str, Any]]:
    span_index = _sqlite_symbol_span_index(raw_dir)
    if not span_index:
        return rows
    enriched: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            enriched.append(row)
            continue
        key = (
            str(row.get("project") or ""),
            _norm(str(row.get("file") or "")),
            str(row.get("symbol") or ""),
        )
        span = span_index.get(key)
        if not span:
            enriched.append(row)
            continue
        clone = dict(row)
        for field in ("line", "char", "end_line", "source_lines", "source"):
            if span.get(field) not in (None, ""):
                clone[field] = span.get(field)
        enriched.append(clone)
    return enriched


def _symbol_match_score(row: dict[str, Any], query: str) -> tuple[int, str]:
    q = _norm(query).lower()
    symbol = str(row.get("symbol") or "").lower()
    workspace_rel = _norm(str(row.get("workspace_rel") or row.get("file") or "")).lower()
    filename = Path(workspace_rel).name
    stem = Path(filename).stem
    tokens = [token for token in re.split(r"[^a-z0-9]+", f"{symbol}/{workspace_rel}") if token]
    segments = [segment for segment in workspace_rel.split("/") if segment]
    if q in {symbol, filename, stem}:
        return (0, workspace_rel)
    if q in tokens:
        return (1, workspace_rel)
    if q in {Path(segment).stem for segment in segments}:
        return (2, workspace_rel)
    if symbol.startswith(q) or filename.startswith(q) or stem.startswith(q):
        return (3, workspace_rel)
    if q in symbol or q in workspace_rel:
        return (4, workspace_rel)
    return (99, workspace_rel)


def _compact_ui_row(row: dict[str, Any], workspace_index: dict[str, str] | None = None) -> dict[str, Any]:
    target_file = _target_file_for_row(row, workspace_index)
    return {
        "project": row.get("project"),
        "file": row.get("file"),
        "scoped_file": row.get("scoped_file"),
        "target_file": target_file,
        "target_ref": _target_ref_for_row(row, target_file),
        "loc": row.get("loc"),
        "risk_tier": row.get("risk_tier"),
        "risk_points": row.get("risk_points"),
        "risk_reasons": _list_sample(row.get("risk_reasons"), 8),
        "store_hooks": _list_sample(row.get("store_hooks"), 8),
        "context_hooks": _list_sample(row.get("context_hooks"), 8),
        "router_hooks": _list_sample(row.get("router_hooks"), 8),
        "missing_i18n_keys_sample": _list_sample(row.get("missing_i18n_keys"), 8),
        "relative_imports_sample": _list_sample(row.get("relative_imports"), 8),
    }


def _compact_dead_row(row: dict[str, Any], workspace_index: dict[str, str] | None = None) -> dict[str, Any]:
    actionability = row.get("actionability") if isinstance(row.get("actionability"), dict) else {}
    target_file = _target_file_for_row(row, workspace_index)
    return {
        "project": row.get("project"),
        "file": row.get("file"),
        "scoped_file": row.get("scoped_file"),
        "target_file": target_file,
        "target_ref": _target_ref_for_row(row, target_file),
        "symbol": row.get("symbol"),
        "confidence": row.get("confidence"),
        "reason": row.get("reason"),
        "actionability": {
            "level": actionability.get("level"),
            "score": actionability.get("score"),
            "unusedness_confidence": actionability.get("unusedness_confidence"),
            "remediation_confidence": actionability.get("remediation_confidence"),
            "intent_decision_required": actionability.get("intent_decision_required"),
            "mutation_proposed": actionability.get("mutation_proposed"),
            "allowed_outcomes": _list_sample(actionability.get("allowed_outcomes"), 8),
            "why": actionability.get("why"),
        },
    }


def project_dead_code_matches_for_file(
    dead_payload: dict[str, Any],
    file_contexts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Project one canonical dead-code generation onto an already resolved file context."""
    if not isinstance(dead_payload, dict) or not file_contexts:
        return []
    items = dead_payload.get("items") or []
    if not isinstance(items, list):
        return []
    workspace_index: dict[str, str] = {}
    for context in file_contexts:
        if not isinstance(context, dict):
            continue
        project = str(context.get("project") or context.get("project_key") or "")
        atlas_rel = _norm(str(context.get("file") or ""))
        workspace_rel = _norm(str(context.get("workspace_rel") or context.get("repo_relative_path") or atlas_rel))
        if project and atlas_rel and workspace_rel:
            workspace_index[f"{project}::{atlas_rel}"] = workspace_rel
    return [
        _compact_dead_row(row, workspace_index)
        for row in items
        if isinstance(row, dict) and _matches_file_context(row, file_contexts)
    ]


def _compact_decision_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate": row.get("candidate"),
        "action": row.get("action"),
        "target_path": row.get("target_path"),
        "source_path": row.get("source_path"),
        "reasons": _list_sample(row.get("reasons"), 8),
        "risk": row.get("risk"),
    }


def _compact_blast_row(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("value") if isinstance(row.get("value"), dict) else {}
    return {
        "key": row.get("key"),
        "direct_dependents": value.get("direct_dependents"),
        "transitive_dependents": value.get("transitive_dependents"),
        "direct_dependents_sample": _list_sample(value.get("direct_dependent_files") or value.get("dependents"), 8),
    }


def _rule_guidance(rule_id: str) -> dict[str, Any]:
    profiles = (build_rule_taxonomy().get("profiles", {}) or {})
    profile = profiles.get(rule_id, {}) if isinstance(profiles, dict) else {}
    labels = DOCTRINE.get("violation_labels", {}) if isinstance(DOCTRINE, dict) else {}
    remediation = (
        DOCTRINE.get("audit_remediation_policy", {}).get("waves", {})
        if isinstance(DOCTRINE.get("audit_remediation_policy"), dict)
        else {}
    )
    policy = remediation.get(rule_id, {}) if isinstance(remediation, dict) else {}
    if not policy and isinstance(remediation, dict):
        policy = remediation.get("default", {})
    label = labels.get(rule_id, profile.get("label") or rule_id) if isinstance(labels, dict) else profile.get("label") or rule_id
    action = policy.get("action") or "Make the smallest code change that satisfies the rule rationale and preserves public behavior."
    return {
        "label": label,
        "why_it_matters": profile.get("rationale") or "SAGE matched this rule against the target file.",
        "fix_strategy": action,
        "priority": policy.get("priority") or "normal",
    }


def build_inspection(kind: str, target: str, raw_dir: Path | None = None) -> dict[str, Any]:
    target = _norm(target)
    source_raw_dir = Path(raw_dir) if raw_dir is not None else RAW_DIR
    atlas = load_atlas_data(source_raw_dir)
    ui = load_json_file(source_raw_dir / "ui_runtime_contracts.json", {})
    dead = load_json_file(source_raw_dir / "dead_code.json", {})
    audit = load_json_file(source_raw_dir / "audit_report.json", {})
    cockpit = load_json_file(source_raw_dir / "merge_decision_cockpit.json", {})
    blast = load_json_file(source_raw_dir / "blast_radius.json", {})
    atlas_payload = atlas if isinstance(atlas, dict) else {}
    workspace_index = _workspace_rel_index(atlas_payload)

    ui_files = (ui.get("files") or []) if isinstance(ui, dict) else []
    dead_items = (dead.get("items") or []) if isinstance(dead, dict) else []
    audit_items = (audit.get("violations") or []) if isinstance(audit, dict) else []
    decisions = (cockpit.get("decisions") or []) if isinstance(cockpit, dict) else []
    atlas_symbols = _iter_atlas_symbols(atlas_payload)
    atlas_file_rows = _iter_atlas_file_rows(atlas_payload)
    file_contexts = _file_contexts(atlas_payload, target) if kind == "file" else []

    if kind == "symbol":
        project_filter, symbol_target = _split_scoped(target)
        needle = symbol_target.lower()
        symbol_rows = [
            row for row in [*atlas_symbols, *atlas_file_rows]
            if (not project_filter or str(row.get("project") or "") == project_filter)
            and (
                needle in str(row.get("symbol", "")).lower()
                or needle in _norm(str(row.get("file", ""))).lower()
                or needle in _norm(str(row.get("workspace_rel", ""))).lower()
            )
        ]
        symbol_rows.sort(
            key=lambda row: (
                _symbol_match_score(row, symbol_target)[0],
                0 if str(row.get("project") or "").upper() == "MAIN" else 1,
                _symbol_match_score(row, symbol_target)[1],
                str(row.get("symbol") or ""),
            )
        )
        file_contexts = _file_contexts_from_symbol_rows(atlas_payload, symbol_rows)
        if file_contexts:
            ui_matches = [row for row in ui_files if _matches_file_context(row, file_contexts)]
            dead_matches = [
                row
                for row in dead_items
                if str(row.get("symbol", "")).lower() == needle and _matches_file_context(row, file_contexts)
            ]
        else:
            ui_matches = [row for row in ui_files if _contains_path(row, symbol_target)]
            dead_matches = [row for row in dead_items if str(row.get("symbol", "")).lower() == needle or _contains_path(row, symbol_target)]
        decision_matches = [
            row for row in decisions
            if (not file_contexts and (needle in str(row.get("candidate", "")).lower() or _contains_path(row, symbol_target)))
            or (file_contexts and _matches_file_context(row, file_contexts))
        ]
    elif kind == "folder":
        symbol_rows = [row for row in atlas_symbols if _norm(str(row.get("file", ""))).lower().startswith(target.lower())]
        file_contexts = _file_contexts_from_symbol_rows(atlas_payload, symbol_rows)
        ui_matches = [row for row in ui_files if _under_folder(row, target)]
        dead_matches = [row for row in dead_items if _under_folder(row, target)]
        decision_matches = [row for row in decisions if _under_folder(row, target)]
    else:
        context_projects = {row.get("project") for row in file_contexts}
        context_files = {row.get("file") for row in file_contexts}
        symbol_rows = [
            row for row in atlas_symbols
            if (
                _matches_file_context(row, file_contexts)
                or (
                    row.get("project") in context_projects
                    and row.get("file") in context_files
                )
            )
        ]
        ui_matches = [row for row in ui_files if _matches_file_context(row, file_contexts)] if file_contexts else [row for row in ui_files if _contains_path(row, target)]
        dead_matches = [row for row in dead_items if _matches_file_context(row, file_contexts)] if file_contexts else [row for row in dead_items if _contains_path(row, target)]
        decision_matches = [row for row in decisions if _matches_file_context(row, file_contexts)] if file_contexts else [row for row in decisions if _contains_path(row, target)]

    audit_matches = [row for row in audit_items if _matches_file_context(row, file_contexts)] if file_contexts else [row for row in audit_items if _contains_path(row, target)]
    blast_matches = _blast_matches_for_context(blast, file_contexts, target)

    symbol_rows = _enrich_symbol_rows_from_sqlite(symbol_rows, source_raw_dir)
    payload = {
        "meta": {
            "kind": "target_inspection",
            "version": "v1",
            "generated_at": _utc_now(),
            "generator": "tools.inspect_target",
            "artifact_root": str(source_raw_dir),
        },
        "analysis_root": str(ANALYZED_REPOSITORY_ROOT),
        "target": {"kind": kind, "value": target},
        "target_file_context": file_contexts[:10],
        "summary": {
            "atlas_files": len(file_contexts),
            "atlas_symbols": len(symbol_rows),
            "ui_runtime_matches": len(ui_matches),
            "ui_risk_tiers": _risk_summary(ui_matches),
            "dead_code_matches": len(dead_matches),
            "audit_violations": len(audit_matches),
            "merge_decisions": len(decision_matches),
            "blast_radius_matches": len(blast_matches),
        },
        "atlas_symbols": [_compact_symbol_row(row) for row in symbol_rows[:12]],
        "ui_runtime_matches": [_compact_ui_row(row, workspace_index) for row in ui_matches[:12]],
        "dead_code_matches": [_compact_dead_row(row, workspace_index) for row in dead_matches[:12]],
        "audit_violations": audit_matches[:12],
        "merge_decisions": [_compact_decision_row(row) for row in decision_matches[:12]],
        "blast_radius_matches": [_compact_blast_row(row) for row in blast_matches[:12]],
    }
    return payload


def _target_repo_files_from_inspection(payload: dict[str, Any]) -> list[str]:
    files: list[str] = []
    target = payload.get("target") if isinstance(payload.get("target"), dict) else {}
    if target.get("kind") == "symbol":
        for row in payload.get("atlas_symbols", []):
            if not isinstance(row, dict):
                continue
            rel = _norm(str(row.get("workspace_rel") or row.get("file") or ""))
            if rel and rel not in files:
                files.append(rel)
        if files:
            return files[:12]
    context_atlas_rels: set[str] = set()
    for context in payload.get("target_file_context", []):
        if not isinstance(context, dict):
            continue
        rel = _norm(str(context.get("workspace_rel") or context.get("file") or ""))
        atlas_rel = _norm(str(context.get("file") or ""))
        if atlas_rel:
            context_atlas_rels.add(atlas_rel)
        if rel and rel not in files:
            files.append(rel)
    for section in ("ui_runtime_matches", "dead_code_matches", "audit_violations", "merge_decisions"):
        for row in payload.get(section, []):
            if not isinstance(row, dict):
                continue
            for key in ("repo_relative_path", "workspace_rel", "scoped_file", "file", "target_path", "source_path"):
                value = str(row.get(key) or "")
                if "::" in value:
                    value = value.split("::", 1)[1]
                rel = _norm(value)
                if rel in context_atlas_rels and rel not in files:
                    continue
                if rel and rel not in files:
                    files.append(rel)
    for row in payload.get("atlas_symbols", []):
        if not isinstance(row, dict):
            continue
        rel = _norm(str(row.get("workspace_rel") or row.get("file") or ""))
        if rel and rel not in files:
            files.append(rel)
    return files[:12]


def _target_refs_from_inspection(payload: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for context in payload.get("target_file_context", []):
        if not isinstance(context, dict):
            continue
        project = str(context.get("project") or "").strip()
        rel = _norm(str(context.get("workspace_rel") or context.get("file") or ""))
        if project and rel:
            ref = f"{project}::{rel}"
            if ref not in refs:
                refs.append(ref)
    for row in payload.get("atlas_symbols", []):
        if not isinstance(row, dict):
            continue
        ref = str(row.get("target_ref") or "").strip()
        if not ref:
            project = str(row.get("project") or "").strip()
            rel = _norm(str(row.get("workspace_rel") or row.get("file") or ""))
            ref = f"{project}::{rel}" if project and rel else ""
        if ref and ref not in refs:
            refs.append(ref)
    return refs[:12]


def _target_spans_from_inspection(payload: dict[str, Any]) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    for row in payload.get("atlas_symbols", []):
        if not isinstance(row, dict):
            continue
        target_file = _norm(str(row.get("workspace_rel") or row.get("file") or ""))
        if not target_file:
            continue
        line = row.get("line")
        try:
            start_line = int(line or 0)
        except (TypeError, ValueError):
            start_line = 0
        source_lines = str(row.get("source_lines") or "").strip()
        end_line = row.get("end_line")
        try:
            parsed_end_line = int(end_line or 0)
        except (TypeError, ValueError):
            parsed_end_line = 0
        span: dict[str, Any] = {
            "symbol": row.get("symbol") or "",
            "type": row.get("type") or "",
            "target_file": target_file,
            "target_ref": row.get("target_ref") or "",
            "source": "sqlite_symbols",
        }
        if start_line > 0:
            span["start_line"] = start_line
            span["line_status"] = "available"
        elif source_lines:
            span["line_status"] = "source_lines_available"
        else:
            span["line_status"] = "not_available"
        if parsed_end_line > 0:
            span["end_line"] = parsed_end_line
        if source_lines:
            span["source_lines"] = source_lines
        if row.get("char") not in (None, ""):
            try:
                span["char"] = int(row.get("char") or 0)
            except (TypeError, ValueError):
                pass
        if span not in spans:
            spans.append(span)
    return spans


def _openable_under_analysis_root(analysis_root: Any, rel_path: str) -> bool:
    root_text = str(analysis_root or "").strip()
    rel = _norm(rel_path)
    if not root_text or not rel:
        return False
    try:
        root = Path(root_text).resolve()
        target_abs = (root / rel).resolve()
    except Exception:
        return False
    return root in [target_abs, *target_abs.parents] and target_abs.exists() and target_abs.is_file()


def _finding_key(finding: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        str(finding.get("type") or ""),
        str(finding.get("target_file") or finding.get("file") or finding.get("target_path") or ""),
        str(finding.get("target_ref") or ""),
        str(finding.get("rule") or finding.get("symbol") or finding.get("action") or ""),
        str(finding.get("priority") or finding.get("confidence") or finding.get("risk_tier") or ""),
    )


def _dedupe_agent_findings(findings: list[dict[str, Any]], *, limit: int = 6) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for finding in findings:
        if not any(value for value in finding.values()):
            continue
        key = _finding_key(finding)
        existing = grouped.get(key)
        evidence = finding.get("evidence")
        if existing is None:
            clone = dict(finding)
            if evidence not in (None, "", [], {}):
                clone["evidence_samples"] = [evidence]
            clone.pop("evidence", None)
            grouped[key] = clone
            continue
        if evidence not in (None, "", [], {}):
            samples = existing.setdefault("evidence_samples", [])
            if evidence not in samples and len(samples) < 3:
                samples.append(evidence)
    return list(grouped.values())[:limit]


def _agent_findings(payload: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    context_file_map: dict[tuple[str, str], str] = {}
    context_file_fallback: dict[str, str] = {}
    for row in payload.get("target_file_context", []):
        if not isinstance(row, dict):
            continue
        project = str(row.get("project") or "")
        atlas_rel = _norm(str(row.get("file") or ""))
        workspace_rel = _norm(str(row.get("workspace_rel") or row.get("file") or ""))
        if project and atlas_rel:
            context_file_map[(project, atlas_rel)] = workspace_rel
        if atlas_rel and atlas_rel not in context_file_fallback:
            context_file_fallback[atlas_rel] = workspace_rel

    def display_file(value: Any, *, project: str = "") -> str:
        rel = _norm(str(value or ""))
        if "::" in rel:
            scoped_project, rel = rel.split("::", 1)
            project = project or scoped_project
        if project and (project, rel) in context_file_map:
            return context_file_map[(project, rel)]
        return context_file_fallback.get(rel, rel)

    def target_ref(project: str, file_path: str) -> str:
        return f"{project}::{file_path}" if project and file_path else file_path

    for row in payload.get("audit_violations", [])[:6]:
        if not isinstance(row, dict):
            continue
        rule = str(row.get("rule") or row.get("label") or "architecture_rule")
        guidance = _rule_guidance(rule)
        project = str(row.get("project") or row.get("project_key") or "")
        file_path = display_file(row.get("repo_relative_path") or row.get("workspace_rel") or row.get("file"), project=project)
        findings.append(
            {
                "type": "architecture_violation",
                "file": file_path,
                "target_file": file_path,
                "target_ref": target_ref(project, file_path),
                "rule": rule,
                "label": guidance.get("label"),
                "priority": guidance.get("priority"),
                "why_it_matters": guidance.get("why_it_matters"),
                "fix_strategy": guidance.get("fix_strategy"),
                "evidence": row.get("detail") or row.get("reason") or row.get("message") or "Audit violation is linked to this target.",
            }
        )
    for row in payload.get("ui_runtime_matches", [])[:6]:
        if not isinstance(row, dict):
            continue
        project = str(row.get("project") or row.get("project_key") or "")
        file_path = display_file(row.get("target_file") or row.get("scoped_file") or row.get("file"), project=project)
        findings.append(
            {
                "type": "ui_runtime_risk",
                "file": file_path,
                "target_file": file_path,
                "target_ref": row.get("target_ref") or target_ref(project, file_path),
                "risk_tier": row.get("risk_tier"),
                "risk_reasons": _list_sample(row.get("risk_reasons"), 6),
                "evidence": "UI/runtime contracts indicate this file deserves focused review before edits.",
            }
        )
    for row in payload.get("dead_code_matches", [])[:6]:
        if not isinstance(row, dict):
            continue
        project = str(row.get("project") or row.get("project_key") or "")
        file_path = display_file(row.get("target_file") or row.get("scoped_file") or row.get("file"), project=project)
        findings.append(
            {
                "type": "dead_code_candidate",
                "file": file_path,
                "target_file": file_path,
                "target_ref": row.get("target_ref") or target_ref(project, file_path),
                "symbol": row.get("symbol"),
                "confidence": row.get("confidence"),
                "evidence": row.get("reason") or "Dead-code engine linked this candidate to the target.",
            }
        )
    for row in payload.get("merge_decisions", [])[:6]:
        if not isinstance(row, dict):
            continue
        findings.append(
            {
                "type": "merge_decision",
                "target_path": _norm(str(row.get("target_path") or "")),
                "source_path": _norm(str(row.get("source_path") or "")),
                "action": row.get("action"),
                "evidence": "; ".join(str(item) for item in _list_sample(row.get("reasons"), 4)),
            }
        )
    return _dedupe_agent_findings(findings, limit=6)


def render_agent_inspection_brief(payload: dict[str, Any], *, debug: bool = False) -> str:
    target = payload.get("target", {}) if isinstance(payload.get("target"), dict) else {}
    summary = payload.get("summary", {}) if isinstance(payload.get("summary"), dict) else {}
    analysis_root = payload.get("analysis_root") or ""
    target_kind = str(target.get("kind") or "").strip()
    target_value = str(target.get("value") or "").strip()
    target_query_ref = ""
    project_scope = str(payload.get("requested_project_scope") or "").strip()
    if target_kind == "symbol":
        target_query_ref = target_value
        if "::" in target_value:
            scoped_project, scoped_symbol = target_value.split("::", 1)
            target_value = scoped_symbol
            if not project_scope:
                project_scope = scoped_project
        if not project_scope:
            project_scope = "all"
    all_target_files = _target_repo_files_from_inspection(payload)
    all_target_refs = _target_refs_from_inspection(payload)
    target_spans = _target_spans_from_inspection(payload)
    findings = _agent_findings(payload)
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        finding_file = _norm(str(finding.get("target_file") or finding.get("file") or finding.get("target_path") or ""))
        if finding_file and finding_file not in all_target_files:
            all_target_files.append(finding_file)
        finding_ref = str(finding.get("target_ref") or "").strip()
        if finding_ref and finding_ref not in all_target_refs:
            all_target_refs.append(finding_ref)
    openable_target_files: list[str] = []
    missing_target_files: list[str] = []
    for file_path in all_target_files:
        if _openable_under_analysis_root(analysis_root, file_path):
            openable_target_files.append(file_path)
        else:
            missing_target_files.append(file_path)
    if openable_target_files and missing_target_files:
        openable_variants: set[str] = set()
        for file_path in openable_target_files:
            openable_variants.update(_path_variants(file_path))
        missing_target_files = [
            file_path
            for file_path in missing_target_files
            if not _path_variants(file_path).intersection(openable_variants)
        ]
    if not openable_target_files and not missing_target_files:
        openable_target_files = all_target_files
    for finding in findings:
        finding_file = _norm(str(finding.get("target_file") or finding.get("file") or finding.get("target_path") or ""))
        if not finding_file:
            continue
        if _openable_under_analysis_root(analysis_root, finding_file):
            finding["source_status"] = "openable_in_analysis_root"
        else:
            finding["source_status"] = "missing_in_analysis_root"
            finding["next_step"] = "Refresh analysis for this exact target root before editing this finding."
    target_files = openable_target_files[:6]
    prioritized_refs: list[str] = []
    for file_path in target_files:
        for ref in all_target_refs:
            if ref.endswith(file_path) and ref not in prioritized_refs:
                prioritized_refs.append(ref)
                break
    for ref in all_target_refs:
        if ref not in prioritized_refs:
            prioritized_refs.append(ref)
    target_refs = prioritized_refs[:6]
    yaml_lines = [
        "mission:",
        "  - Inspect only the listed repository targets before changing code.",
        "task:",
        f"  analysis_root: {json.dumps(analysis_root, ensure_ascii=False)}",
        f"  target_kind: {json.dumps(target_kind, ensure_ascii=False)}",
        f"  target: {json.dumps(target_value, ensure_ascii=False)}",
    ]
    if target_kind == "symbol":
        yaml_lines.extend(
            [
                f"  symbol_query: {json.dumps(target_value, ensure_ascii=False)}",
                f"  project_scope: {json.dumps(project_scope, ensure_ascii=False)}",
                f"  target_query_ref: {json.dumps(target_query_ref or target_value, ensure_ascii=False)}",
                "  target_query_ref_usage: \"SAGE/MCP symbol follow-up reference only; not a filesystem path\"",
            ]
        )
    yaml_lines.extend(
        [
        f"  returned_target_files: {len(target_files)}",
        f"  omitted_target_files: {max(0, len(openable_target_files) - len(target_files))}",
        "  target_files:",
        ]
    )
    if target_files:
        yaml_lines.extend(f"    - {json.dumps(path, ensure_ascii=False)}" for path in target_files)
    else:
        yaml_lines.append("    []")
    yaml_lines.append("  target_refs:")
    if target_refs:
        yaml_lines.extend(f"    - {json.dumps(ref, ensure_ascii=False)}" for ref in target_refs)
    else:
        yaml_lines.append("    []")
    yaml_lines.append(f"  returned_target_refs: {len(target_refs)}")
    yaml_lines.append(f"  omitted_target_refs: {max(0, len(all_target_refs) - len(target_refs))}")
    shown_target_spans = target_spans[:AGENT_INSPECTION_MAX_TARGET_SPANS]
    yaml_lines.append(f"  target_spans_shown: {len(shown_target_spans)}")
    yaml_lines.append(f"  target_spans_omitted: {max(0, len(target_spans) - len(shown_target_spans))}")
    yaml_lines.append("  target_spans:")
    if shown_target_spans:
        for span in shown_target_spans:
            yaml_lines.append("    - symbol: " + json.dumps(span.get("symbol") or "", ensure_ascii=False))
            yaml_lines.append("      type: " + json.dumps(span.get("type") or "", ensure_ascii=False))
            yaml_lines.append("      target_file: " + json.dumps(span.get("target_file") or "", ensure_ascii=False))
            yaml_lines.append("      target_ref: " + json.dumps(span.get("target_ref") or "", ensure_ascii=False))
            yaml_lines.append("      line_status: " + json.dumps(span.get("line_status") or "not_available", ensure_ascii=False))
            if span.get("start_line"):
                yaml_lines.append(f"      start_line: {int(span.get('start_line') or 0)}")
            if span.get("end_line"):
                yaml_lines.append(f"      end_line: {int(span.get('end_line') or 0)}")
            if span.get("source_lines"):
                yaml_lines.append("      source_lines: " + json.dumps(span.get("source_lines") or "", ensure_ascii=False))
            if span.get("char") is not None:
                yaml_lines.append(f"      char: {int(span.get('char') or 0)}")
    else:
        yaml_lines.append("    []")
    yaml_lines.extend(
        [
            "source_grounding:",
            f"  openable_target_files: {len(openable_target_files)}",
            f"  missing_target_files: {len(missing_target_files)}",
            "  source_grounded_for_inspection: "
            + str(bool(payload.get("source_grounded_for_inspection"))).lower(),
            "  one_shot_edit_ready: " + str(bool(payload.get("one_shot_edit_ready"))).lower(),
            "  inspection_authority: "
            + json.dumps(payload.get("inspection_authority") or "orientation_only", ensure_ascii=False),
            "  edit_readiness_reason: "
            + json.dumps(payload.get("edit_readiness_reason") or "not_available", ensure_ascii=False),
        ]
    )
    target_path_status = payload.get("target_path_status") if isinstance(payload.get("target_path_status"), dict) else {}
    if target_path_status:
        snapshot_hash = str(target_path_status.get("source_snapshot_hash") or "")
        yaml_lines.extend(
            [
                "  snapshot:",
                "    source_snapshot_status: "
                + json.dumps(target_path_status.get("source_snapshot_status") or "missing", ensure_ascii=False),
                "    source_snapshot_hash_prefix: " + json.dumps(snapshot_hash[:12], ensure_ascii=False),
                "    drift_check_status: "
                + json.dumps(target_path_status.get("drift_check_status") or "not_available", ensure_ascii=False),
                f"    target_span_count: {int(target_path_status.get('target_span_count') or 0)}",
            ]
        )
        requested_line_range = (
            target_path_status.get("requested_line_range")
            if isinstance(target_path_status.get("requested_line_range"), dict)
            else {}
        )
        if requested_line_range:
            yaml_lines.extend(
                [
                    "    requested_line_range:",
                    f"      line_start: {int(requested_line_range.get('line_start') or 0)}",
                    f"      line_end: {int(requested_line_range.get('line_end') or 0)}",
                    f"      bounded_line_start: {int(requested_line_range.get('bounded_line_start') or 0)}",
                    f"      bounded_line_end: {int(requested_line_range.get('bounded_line_end') or 0)}",
                    "      line_status: "
                    + json.dumps(requested_line_range.get("line_status") or "not_available", ensure_ascii=False),
                ]
            )
        snippets = (
            target_path_status.get("target_source_snippets")
            if isinstance(target_path_status.get("target_source_snippets"), list)
            else []
        )
        shown_snippets = snippets[:3]
        yaml_lines.extend(
            [
                f"  target_source_snippets_shown: {len(shown_snippets)}",
                f"  target_source_snippets_omitted: {max(0, len(snippets) - len(shown_snippets))}",
                "  target_source_snippet_status: "
                + json.dumps(
                    "included"
                    if shown_snippets
                    else ("not_available_for_current_scope" if target_spans else "no_target_span"),
                    ensure_ascii=False,
                ),
            ]
        )
        if target_spans and not shown_snippets:
            yaml_lines.append(
                "  target_source_snippet_next_action: "
                + json.dumps(
                    "Select one listed target span, then call inspect_file(target_file, line_start=start_line, line_end=end_line) before editing.",
                    ensure_ascii=False,
                )
            )
        yaml_lines.extend(render_target_source_snippets(snippets, indent="  ", item_indent="    ", max_items=3))
    else:
        yaml_lines.extend(
            [
                "  target_source_snippets_shown: 0",
                "  target_source_snippets_omitted: 0",
                '  target_source_snippet_status: "not_available_until_single_target_selected"',
                '  target_source_snippet_next_action: "Choose one target_ref, then request a narrow inspection for that target before editing."',
                "  target_source_snippets:",
                "    []",
            ]
        )
    if missing_target_files:
        yaml_lines.append("  missing_target_files_sample:")
        yaml_lines.extend(f"    - {json.dumps(path, ensure_ascii=False)}" for path in missing_target_files[:6])
        yaml_lines.append(f"  omitted_missing_target_files: {max(0, len(missing_target_files) - 6)}")
        yaml_lines.append("  action: \"Do not edit missing paths; refresh analysis for this exact target root first.\"")
    local_results = payload.get("client_local_parser_result_evidence")
    if isinstance(local_results, dict):
        yaml_lines.append("client_local_parser_result_evidence: " + json.dumps(local_results, ensure_ascii=False))
    local_inputs = payload.get("client_local_parser_input_sink_evidence")
    if isinstance(local_inputs, dict):
        yaml_lines.append("client_local_parser_input_sink_evidence: " + json.dumps(local_inputs, ensure_ascii=False))
    imported_parsers = payload.get("client_imported_parser_evidence")
    if isinstance(imported_parsers, dict):
        yaml_lines.append("client_imported_parser_evidence: " + json.dumps(imported_parsers, ensure_ascii=False))
    response_returns = payload.get("response_json_return_evidence")
    if isinstance(response_returns, dict):
        yaml_lines.append("response_json_return_evidence: " + json.dumps(response_returns, ensure_ascii=False))
    yaml_lines.extend(
        [
            "path_contract:",
            "  open_files_with: \"analysis_root + target_files item, or analysis_root + findings[].target_file\"",
            "  target_refs_usage: \"SAGE/MCP follow-up references only; not filesystem paths\"",
            "  target_refs_counting: \"target_refs count SAGE/MCP follow-up matches; target_files count openable filesystem paths\"",
        ]
    )
    ambiguous_target = len(target_refs) > 1
    if ambiguous_target:
        yaml_lines.append("  ambiguity_note: \"Multiple project-scoped targets matched. Pick one target_ref before editing.\"")
    yaml_lines.extend(
        [
            "  summary:",
            f"    ui_runtime_matches: {int(summary.get('ui_runtime_matches') or 0)}",
            "    dead_code_matches: " + (
                str(int(summary.get("dead_code_matches") or 0))
                if str(summary.get("dead_code_matches_status") or "AVAILABLE") == "AVAILABLE"
                else "UNKNOWN"
            ),
            "    dead_code_matches_status: " + json.dumps(
                summary.get("dead_code_matches_status") or "AVAILABLE",
                ensure_ascii=False,
            ),
            f"    audit_violations: {int(summary.get('audit_violations') or 0)}",
            f"    merge_decisions: {int(summary.get('merge_decisions') or 0)}",
            f"    blast_radius_matches: {int(summary.get('blast_radius_matches') or 0)}",
            "findings:",
        ]
    )
    if findings:
        for finding in findings:
            yaml_lines.append("  - type: " + json.dumps(finding.get("type") or "finding", ensure_ascii=False))
            for key, value in finding.items():
                if key == "type" or value in (None, "", [], {}):
                    continue
                yaml_lines.append(f"    {key}: {json.dumps(value, ensure_ascii=False)}")
    elif not target_files:
        yaml_lines.extend(
            [
                "  - type: \"target_not_found_in_current_artifacts\"",
                "    evidence: \"The requested target was not found in the current SAGE artifacts for this repository.\"",
                "    next_step: \"Check the path or run/refresh external target analysis before editing.\"",
            ]
        )
    else:
        yaml_lines.append("  []")
    if isinstance(payload.get("primary_tool_followup"), dict):
        yaml_lines.append("primary_tool_followup: " + json.dumps(payload["primary_tool_followup"], ensure_ascii=False))
    yaml_lines.append("do:")
    if ambiguous_target:
        yaml_lines.extend(
            [
                "  - Do not edit yet; this target maps to multiple project-scoped targets.",
                "  - Choose exactly one target_ref/project scope, then request a narrower inspection for that target before patching.",
                "  - Use target_files only to inspect and disambiguate the intended repository target.",
            ]
        )
    else:
        yaml_lines.extend(
            [
                "  - Open the target files and verify the listed findings against the code before editing.",
                "  - Make the smallest code change that directly addresses the confirmed finding.",
                "  - If evidence is only contextual, use it to avoid regressions rather than inventing extra cleanup.",
            ]
        )
    yaml_lines.extend(
        [
            "do_not:",
            "  - Do not refactor unrelated files.",
            "  - Do not treat contextual risk as proof of a bug without checking the code.",
            "  - Do not add allowlists, suppressions, deletions, moves, or broad rewrites without human approval.",
        ]
    )
    if ambiguous_target:
        yaml_lines.append("  - Do not patch while multiple target_refs are present.")
    if debug:
        yaml_lines.extend(
            [
                "debug_internal_refs:",
                "  atlas_nodes:",
            ]
        )
        atlas_nodes = [
            str(row.get("atlas_node"))
            for row in payload.get("target_file_context", [])
            if isinstance(row, dict) and row.get("atlas_node")
        ]
        yaml_lines.extend(f"    - {json.dumps(node, ensure_ascii=False)}" for node in atlas_nodes[:10])
        if not atlas_nodes:
            yaml_lines.append("    []")
    return "\n".join(
        [
            "# Target Inspection Brief",
            "",
            "Use this brief to inspect the analyzed repository target with the smallest safe scope.",
            "",
            "```yaml",
            *_with_context_budget(yaml_lines),
            "```",
            "",
        ]
    )


def _safe_name(kind: str, target: str) -> str:
    safe = "".join(ch.lower() if ch.isalnum() else "_" for ch in f"{kind}_{target}")[:120].strip("_")
    return safe or "target_inspection"


def render_report(payload: dict[str, Any]) -> str:
    target = payload.get("target", {})
    summary = payload.get("summary", {})
    lines = [
        "# Target Inspection",
        "",
        f"- kind: `{target.get('kind')}`",
        f"- target: `{target.get('value')}`",
        f"- atlas_symbols: `{summary.get('atlas_symbols')}`",
        f"- ui_runtime_matches: `{summary.get('ui_runtime_matches')}`",
        f"- ui_risk_tiers: `{summary.get('ui_risk_tiers')}`",
        f"- dead_code_matches: `{summary.get('dead_code_matches')}`",
        f"- audit_violations: `{summary.get('audit_violations')}`",
        f"- merge_decisions: `{summary.get('merge_decisions')}`",
        f"- blast_radius_matches: `{summary.get('blast_radius_matches')}`",
        "",
        "## Merge Decisions",
        "",
        "| Candidate | Action | Target | Reasons |",
        "|---|---|---|---|",
    ]
    for row in payload.get("merge_decisions", [])[:20]:
        lines.append(
            f"| `{row.get('candidate')}` | `{row.get('action')}` | `{row.get('target_path')}` | `{', '.join(row.get('reasons') or [])}` |"
        )
    lines.extend(["", "## UI Runtime Matches", "", "| Project | File | Risk | Reasons |", "|---|---|---|---|"])
    for row in payload.get("ui_runtime_matches", [])[:20]:
        lines.append(
            f"| `{row.get('project')}` | `{row.get('file')}` | `{row.get('risk_tier')}` | `{', '.join(row.get('risk_reasons') or [])}` |"
        )
    lines.extend(["", "## Dead Code Matches", "", "| Project | File | Symbol | Confidence | Reason |", "|---|---|---|---|---|"])
    for row in payload.get("dead_code_matches", [])[:20]:
        lines.append(
            f"| `{row.get('project')}` | `{row.get('file')}` | `{row.get('symbol')}` | `{row.get('confidence')}` | `{row.get('reason')}` |"
        )
    return "\n".join(lines) + "\n"


def write_inspection(kind: str, target: str, raw_dir: Path | None = None, reports_dir: Path | None = None) -> dict[str, Any]:
    source_raw_dir = Path(raw_dir) if raw_dir is not None else RAW_DIR
    target_reports_dir = Path(reports_dir) if reports_dir is not None else REPORTS_DIR
    payload = build_inspection(kind, target, raw_dir=source_raw_dir)
    name = _safe_name(kind, target)
    save_json_atomic(source_raw_dir / f"inspect_{name}.json", payload)
    save_text_atomic(target_reports_dir / f"inspect_{name}.md", render_report(payload))
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect a file, folder, or symbol from existing Nexora SAGE artifacts.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--file", dest="file_target")
    group.add_argument("--folder", dest="folder_target")
    group.add_argument("--symbol", dest="symbol_target")
    args = parser.parse_args()

    if args.file_target:
        payload = write_inspection("file", args.file_target)
    elif args.folder_target:
        payload = write_inspection("folder", args.folder_target)
    else:
        payload = write_inspection("symbol", args.symbol_target)
    print(json.dumps(payload.get("summary", {}), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
