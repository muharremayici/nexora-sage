"""Stored declaration syntax orientation; never implementation/edit authority."""
from copy import deepcopy
import heapq
import json
from pathlib import Path, PurePosixPath

from tools.core.agent_packet_budget import COMPACT_AGENT_PACKET_TOKENS, context_budget_profile
from tools.core.atlas_io import load_atlas_declaration_metadata
from tools.core.atlas_typescript_inputs import auxiliary_input_policy, declaration_contract_policy, declaration_file_shape_valid
from tools.core.manifest_declaration_links import manifest_link_group_valid

_INTRO = "Stored declaration headers only; no live-source, runtime, type-safety or edit authority.\n\n```json\n"


def _relative_declaration(path, policy):
    try:
        if isinstance(path, str):
            path.encode("utf-8")
    except UnicodeError:
        return False
    return (isinstance(path, str) and bool(path) and not path.startswith("/")
        and not any(c in path for c in ("\\", ":", "\x00"))
        and ".." not in PurePosixPath(path).parts and PurePosixPath(path).as_posix() == path
        and path.lower().endswith(tuple(policy["supported_suffixes"])))


def _plane_valid(plane, policy, digest):
    fields = {"version", "coverage", "project_root", "policy_sha256", "status", "reason",
              "selected_files", "omitted_files", "files", "runtime_execution"}
    if not isinstance(plane, dict) or set(plane) - {"manifest_type_links"} != fields:
        return False
    try:
        for key in ("project_root", "reason"):
            if isinstance(plane[key], str):
                plane[key].encode("utf-8")
    except UnicodeError:
        return False
    files, omitted, selected = plane["files"], plane["omitted_files"], plane["selected_files"]
    return (plane["version"] == policy["version"] and plane["policy_sha256"] == digest
        and plane["coverage"] == "finite_top_level_headers_only" and plane["runtime_execution"] == "not_established"
        and plane["status"] in ("observed", "partial", "unavailable")
        and isinstance(plane["project_root"], str) and bool(plane["project_root"])
        and isinstance(plane["reason"], str) and len(plane["reason"]) <= policy["max_name_bytes"]
        and isinstance(files, dict) and len(files) <= policy["max_files"]
        and all(_relative_declaration(name, policy) for name in files)
        and type(selected) is int and selected == len(files)
        and (omitted is None or type(omitted) is int and omitted >= 0)
        and (plane["status"] != "unavailable" or not files)
        and (plane["status"] != "observed" or omitted == 0
             and all(isinstance(row, dict) and row.get("status") == "observed" for row in files.values())))


def _fits(payload):
    return context_budget_profile(_INTRO + json.dumps(payload, ensure_ascii=False) + "\n```",
        budget_tokens=COMPACT_AGENT_PACKET_TOKENS)["status"] == "pass"


def _project_manifest_links(payload, plane, rel, record_limit):
    """Keep link admission/omissions independent of valid header evidence."""
    group = plane.get("manifest_type_links")
    section = {"status": "unavailable", "reason": "manifest_link_evidence_missing_or_invalid",
        "records": [], "shown": 0, "omitted": None, "resolution_status": "not_evaluated"}
    candidate = deepcopy(payload)
    candidate["manifest_type_links"] = section
    if not _fits(candidate):
        return payload  # Absent section grants no link authority.
    if not manifest_link_group_valid(group, plane):
        return candidate
    rows = [row for row in group["records"] if row["candidate_declaration_ref"] == rel]
    section.update(status=group["status"], reason="stored_literal_path_correspondence_only",
        producer_omitted=group["omitted_count"], omitted=len(rows), matching_stored_records=len(rows), policy_sha256=group["policy_sha256"])
    if not _fits(candidate):
        section.clear()
        section.update(status="unavailable", reason="manifest_link_display_over_budget")
        return candidate
    for row in rows:
        next_payload = deepcopy(candidate)
        shown = next_payload["manifest_type_links"]
        shown["records"].append(deepcopy(row))
        shown["shown"] += 1
        shown["omitted"] -= 1
        if shown["shown"] > record_limit or not _fits(next_payload):
            break
        candidate = next_payload
    if candidate["manifest_type_links"]["omitted"]:
        candidate["manifest_type_links"]["reason"] = "compact_link_display_omission"
    return candidate


def declaration_file_inspection(raw_dir: Path, file_path: str, *, record_limit: int, line_requested=False):
    """Return None for ordinary files; declaration requests never fall back.

    No source text, filesystem freshness, config execution or implementation
    lookup occurs. A primary checksum binds stored bytes, not producer truth.
    """
    policy, digest = declaration_contract_policy()
    if not isinstance(file_path, str) or not file_path.lower().endswith(tuple(policy["supported_suffixes"])):
        return None
    payload = {"inspection_plane": "declaration_contract", "declaration_ref": "",
        "source_binding": "unavailable", "live_source_binding": "not_checked",
        "source_grounded_for_inspection": False, "one_shot_edit_ready": False, "mutation_proposed": False,
        "target_files": [], "runtime_execution": "not_established", "semantic_type_safety": "not_established",
        "conditional_resolution": "not_evaluated", "stored_atlas_identity": None,
        "declaration_contract": {"status": "unavailable", "reason": "exact_project_qualified_declaration_required",
            "records": [], "record_count": None, "shown": 0, "omitted": None}}
    section = payload["declaration_contract"]
    project, separator, rel = file_path.partition("::")
    if not separator or not project or not _relative_declaration(rel, policy):
        return payload
    try:
        project.encode("utf-8")
    except UnicodeError:
        return payload
    payload["declaration_ref"] = file_path
    if not _fits(payload):
        payload["declaration_ref"] = ""
        section["reason"] = "query_header_over_budget"
        return payload
    if line_requested:
        section["reason"] = "source_line_range_not_available_for_declaration_metadata"
        return payload
    read = load_atlas_declaration_metadata(raw_dir, project)
    if not read.primary_verified:
        section["reason"] = "unsupported_primary_read_mode" if read.source == "unsupported_primary_mode" else "primary_declaration_read_unavailable"
        return payload
    project_data = read.data.get(project)
    plane = project_data.get("declaration_contract_evidence") if isinstance(project_data, dict) else None
    if not _plane_valid(plane, policy, digest):
        section["reason"] = "declaration_plane_missing_or_invalid"
        return payload
    payload["stored_atlas_identity"] = read.identity
    section.update(project_status=plane["status"], omitted_files=plane["omitted_files"])
    if plane["status"] == "unavailable":
        section["reason"] = "declaration_plane_unavailable"
        return payload
    value = plane["files"].get(rel)
    if (not declaration_file_shape_valid(value, policy, digest)
            or value["size_bytes"] > auxiliary_input_policy()["max_file_bytes"]):
        section["reason"] = "exact_declaration_missing_or_invalid"
        return payload
    section.update({key: deepcopy(value[key]) for key in ("version", "coverage", "status", "policy_sha256",
        "parser_kind", "parser_version", "size_bytes", "offset_unit", "record_count", "omitted_statements",
        "omitted_name_nodes", "uninspected_module_bodies", "reasons")})
    section.update(reason="stored_headers_only", captured_input_sha256=value["parser_input_sha256"],
        omitted=value["record_count"])
    payload["source_binding"] = "stored_declaration_metadata_only"
    if not _fits(payload):
        payload["declaration_contract"] = {"status": "unavailable", "reason": "declaration_header_over_budget",
            "records": [], "record_count": None, "shown": 0, "omitted": None}
        return payload
    for row in value["records"]:
        candidate = deepcopy(payload)
        part = candidate["declaration_contract"]
        part["records"].append(deepcopy(row))
        part["shown"] += 1
        part["omitted"] -= 1
        if part["shown"] > record_limit or not _fits(candidate):
            break
        payload = candidate
    if payload["declaration_contract"]["omitted"]:
        payload["declaration_contract"]["reason"] = "compact_display_omission"
    return _project_manifest_links(payload, plane, rel, record_limit)


def render_declaration_inspection(payload: dict) -> str:
    """Render the same bounded stored-only contract, without clean/edit prose."""
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    # Formatting whitespace must not turn a compact packet into an oversized one.
    if context_budget_profile(_INTRO + body + "\n```", budget_tokens=COMPACT_AGENT_PACKET_TOKENS)["status"] != "pass":
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return _INTRO + body + "\n```"


def _search_unavailable(payload, reason):
    payload.pop("candidate_count_semantics", None)
    payload.update(status="unavailable", reason=reason, source_binding="unavailable", stored_atlas_identity=None,
                   matches=[], candidate_count=None, shown=0, omitted=None, search_truncated=None, display_truncated=False)
    if not _fits(payload):
        payload.update(query="", project="", coverage={"status": "unavailable"})
    return payload


def _declaration_name_candidates(plane, project, query, policy, digest, coverage, score, rank, target_root):
    """Visit only the already finite stored plane; no implementation/file graph lookup."""
    needle = query.lower()
    file_limit = auxiliary_input_policy()["max_file_bytes"]
    for rel, value in sorted(plane["files"].items()):
        if not declaration_file_shape_valid(value, policy, digest) or value["size_bytes"] > file_limit:
            coverage["invalid_files"] += 1
            continue
        coverage["valid_files"] += 1
        coverage["partial_files"] += int(value["status"] == "partial")
        for key in ("omitted_statements", "omitted_name_nodes", "uninspected_module_bodies"):
            coverage["known_"+key] += value[key]
        for reason, count in value["reasons"].items():
            coverage["known_reasons"][reason] = coverage["known_reasons"].get(reason, 0) + count
        ref = project + "::" + rel
        for row in value["records"]:
            coverage["unnamed_header_records"] += int(not row["names"])
            for ordinal, name in enumerate(row["names"]):
                if needle not in name.lower() and needle not in rel.lower():
                    continue
                coverage["matching_name_occurrences"] += 1
                item = {"candidate_kind": "DeclarationHeaderNameCandidate", "project": project,
                    "declaration_ref": ref, "name": name, "name_ordinal": ordinal,
                    "captured_input_sha256": value["parser_input_sha256"],
                    **{key: row[key] for key in ("syntax_kind", "start_byte", "end_byte", "export_modifier",
                        "declare_modifier", "default_modifier", "global_augmentation", "module_specifier")},
                    "followup": {"tool": "inspect_file", "arguments": {"file_path": ref, "target_root": target_root}}}
                match_score = score(query, name, rel)[0]
                order = (*rank(query, {"name": name, "file": rel, "project": project, "match_score": match_score}),
                         row["start_byte"], row["end_byte"], ordinal)
                yield order, item


def declaration_symbol_search(raw_dir, query, project, *, evidence_plane, search_policy, score, rank,
                              target_root="", machine=False, invalid_context=False):
    """Explicit stored-only plane; default indexed search never enters this path."""
    settings = search_policy.get("declaration_search")
    if (not isinstance(settings, dict) or settings.get("version") != "v1-stored-declaration-search"
            or settings.get("default_plane") != "indexed" or settings.get("plane") != "declaration"
            or settings.get("scope") != "one_exact_project_retained_named_headers"):
        raise ValueError("Missing or invalid stored declaration search policy")
    for key in ("max_candidate_rows_per_source", "brief_max_visible_items", "machine_max_visible_items"):
        if type(search_policy.get(key)) is not int or search_policy[key] < 1:
            raise ValueError("Invalid declaration search limit: " + key)
    payload = {"version": settings["version"], "search_plane": settings["plane"], "status": "unavailable",
        "reason": "not_evaluated", "query": "", "project": "", "stored_atlas_identity": None,
        "search_scope": settings["scope"], "source_binding": "unavailable", "live_source_binding": "not_checked",
        "source_grounded_for_inspection": False, "one_shot_edit_ready": False, "mutation_proposed": False,
        "target_files": [], "runtime_execution": "not_established", "semantic_type_safety": "not_established",
        "conditional_resolution": "not_evaluated", "candidate_count": None, "shown": 0, "omitted": None,
        "search_truncated": None, "display_truncated": False, "coverage": {"status": "unavailable"}, "matches": []}
    if evidence_plane != settings["plane"]:
        return _search_unavailable(payload, "unsupported_evidence_plane")
    if (not isinstance(project, str) or not project or project.strip() != project or "::" in project
            or "\x00" in project or project.lower() in {"*", "all", "any"}):
        return _search_unavailable(payload, "one_exact_project_required")
    if not isinstance(query, str) or not query.strip():
        return _search_unavailable(payload, "nonblank_literal_query_required")
    try:
        project.encode("utf-8"); query.encode("utf-8"); target_root.encode("utf-8")
    except (UnicodeError, AttributeError):
        return _search_unavailable(payload, "invalid_query_encoding")
    payload.update(query=query, project=project)
    if not _fits(payload):
        return _search_unavailable(payload, "query_header_over_budget")
    if invalid_context or raw_dir is None:
        return _search_unavailable(payload, "invalid_external_target")
    policy, digest = declaration_contract_policy()
    read = load_atlas_declaration_metadata(raw_dir, project)
    if not read.primary_verified:
        return _search_unavailable(payload, "primary_declaration_read_unavailable")
    data = read.data.get(project)
    plane = data.get("declaration_contract_evidence") if isinstance(data, dict) else None
    if not _plane_valid(plane, policy, digest):
        return _search_unavailable(payload, "declaration_plane_missing_or_invalid")
    if plane["status"] == "unavailable":
        return _search_unavailable(payload, "declaration_plane_unavailable")
    coverage = {"status": "available", "project_status": plane["status"], "selected_files": plane["selected_files"],
        "omitted_files": plane["omitted_files"], "valid_files": 0, "invalid_files": 0, "partial_files": 0,
        "unnamed_header_records": 0, "known_omitted_statements": 0, "known_omitted_name_nodes": 0,
        "known_uninspected_module_bodies": 0, "known_reasons": {}, "matching_name_occurrences": 0}
    candidates = _declaration_name_candidates(plane, project, query, policy, digest, coverage, score, rank, target_root)
    retained = heapq.nsmallest(search_policy["max_candidate_rows_per_source"], candidates, key=lambda pair: pair[0])
    if plane["status"] == "partial" or coverage["invalid_files"] or coverage["partial_files"] or coverage["unnamed_header_records"]:
        coverage["status"] = "partial"
    payload.update(coverage=coverage)
    if coverage["invalid_files"] and not coverage["valid_files"]:
        coverage["status"] = "unavailable"
        return _search_unavailable(payload, "no_admissible_declaration_files")
    capped = coverage["matching_name_occurrences"] > len(retained)
    payload.update(status=coverage["status"], reason="stored_named_headers_only" if retained else "no_matching_retained_names",
        stored_atlas_identity=read.identity, source_binding="stored_declaration_metadata_only", candidate_count=len(retained),
        candidate_count_semantics="lower_bound" if capped else "complete_retained_named_header_match_set",
        search_truncated=capped, shown=0, omitted=len(retained))
    if not _fits(payload):
        return _search_unavailable(payload, "coverage_header_over_budget")
    item_limit = search_policy["machine_max_visible_items" if machine else "brief_max_visible_items"]
    for _, row in retained[:item_limit]:
        candidate = deepcopy(payload)
        candidate["matches"].append(row)
        candidate.update(shown=len(candidate["matches"]), omitted=len(retained)-len(candidate["matches"]),
                         display_truncated=len(candidate["matches"]) < len(retained))
        if not _fits(candidate):
            break
        payload = candidate
    payload["display_truncated"] = payload["omitted"] > 0
    return payload
