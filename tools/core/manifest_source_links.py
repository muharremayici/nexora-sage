"""Literal type-entry correspondence to indexed source, never compiler resolution."""
from copy import deepcopy
import hashlib
from pathlib import PurePosixPath

from tools.core.atlas_ast_contract import AST_CONTRACT_VERSION
from tools.core.atlas_typescript_inputs import auxiliary_context_identity, auxiliary_input_policy
from tools.core.language_registry import extensions_for_language, non_source_compound_suffixes
from tools.core.manifest_declaration_links import _candidate, _canonical_relative, _encoded, _literal_shape, _manifest_evidence, _sha, _text
from tools.core.package_contracts import _type_entry_policy
from tools.core.source_snapshot_integrity import snapshot_hash_algorithms


def manifest_source_link_policy():
    policy = auxiliary_input_policy()["manifest_source_links"]
    if (policy.get("version") != "v1-literal-manifest-source-links"
            or policy.get("scope") != "top_level_types_typings_indexed_source_correspondence"):
        raise ValueError("Invalid manifest/source link version or scope")
    for key in ("max_packages", "max_candidates", "max_records", "max_text_bytes", "max_record_bytes", "max_group_bytes", "max_total_bytes"):
        if type(policy.get(key)) is not int or policy[key] < 1:
            raise ValueError("Invalid manifest/source link budget: " + key)
    for key in ("literal_fields", "reason_codes"):
        if (not isinstance(policy.get(key), list) or not policy[key]
                or any(not isinstance(v, str) or not v for v in policy[key]) or len(set(policy[key])) != len(policy[key])):
            raise ValueError("Invalid manifest/source link vocabulary: " + key)
    if not policy["max_text_bytes"] <= policy["max_record_bytes"] <= policy["max_group_bytes"] <= policy["max_total_bytes"]:
        raise ValueError("Inconsistent manifest/source link byte budgets")
    return policy, hashlib.sha256(_encoded(policy)).hexdigest()


def _source_path(value, vocabulary=None):
    if not _canonical_relative(value):
        return False
    extensions, compound = vocabulary or (extensions_for_language("typescript"), tuple(non_source_compound_suffixes()))
    return PurePosixPath(value).suffix.lower() in extensions and not value.lower().endswith(compound)


def _source_identity(file, project, rel, context, text_limit):
    if not isinstance(file, dict) or not _source_path(rel) or not _sha(context):
        return None
    parser, value = file.get("parser_evidence"), file.get("hash")
    workspace = file.get("workspace_rel")
    if (file.get("project_key") != project or file.get("atlas_rel_path") != rel
            or file.get("canonical_file_path") is not True or file.get("language") != "typescript"
            or file.get("ast_contract_version") != AST_CONTRACT_VERSION or file.get("auxiliary_input_identity") != context
            or not isinstance(parser, dict) or parser.get("status") != "observed"
            or parser.get("parser_kind") != "typescript_compiler_api"
            or not _canonical_relative(workspace) or not _text(workspace, text_limit) or file.get("repo_relative_path") != workspace
            or file.get("target_ref") != f"{project}::{workspace}"
            or not isinstance(value, str) or not value or any(c not in "0123456789abcdef" for c in value)):
        return None
    algorithm = snapshot_hash_algorithms().get(str(len(value)))
    return {"value": value, "algorithm": algorithm, "basis": "atlas_file_text_identity_not_auxiliary_raw_capture"} if algorithm else None


def _envelope(policy, digest, manifest_digest, root, project, rel, context, identity):
    return {"version": policy["version"], "scope": policy["scope"], "status": "observed", "policy_sha256": digest,
        "manifest_policy_sha256": manifest_digest, "project_root": root, "project_key": project, "atlas_rel_path": rel,
        "ast_contract_version": AST_CONTRACT_VERSION, "auxiliary_context_identity": context, "source_text_identity": identity,
        "records": [], "shown_count": 0, "producer_omitted": 0, "reasons": [],
        "runtime_execution": "not_established", "resolution_status": "not_evaluated"}


def build_manifest_source_link_index(project, capture, public_contracts):
    """Build once over owned manifest observations; no target I/O or source lookup."""
    policy, digest = manifest_source_link_policy()
    manifest_policy, manifest_digest = _type_entry_policy()
    out = {"status": "unavailable", "by_source": {}, "project_key": project, "project_root": None,
        "context": "", "policy_sha256": digest, "manifest_policy_sha256": manifest_digest,
        "producer_omitted": None, "reasons": ["auxiliary_scope_unavailable"]}
    if not isinstance(capture, dict) or not _text(project, policy["max_text_bytes"]) or not project or "::" in project:
        return out
    root, files = capture.get("project_root"), capture.get("files")
    if (not _text(root, policy["max_text_bytes"]) or not root or not isinstance(files, dict)
            or len(files) > auxiliary_input_policy()["max_files"]):
        return out
    context = auxiliary_context_identity(capture)
    if not _sha(context):
        return out
    packages = public_contracts.get("packages") if isinstance(public_contracts, dict) else None
    if not isinstance(packages, list):
        return out
    out.update(status="observed", project_root=root, context=context, producer_omitted=0, reasons=[])
    reasons, admitted = set(), set()
    source_vocabulary = (extensions_for_language("typescript"), tuple(non_source_compound_suffixes()))
    def partial(reason, unknown=False):
        reasons.add(reason)
        out["status"] = "partial"
        if unknown:
            out["producer_omitted"] = None
        elif out["producer_omitted"] is not None:
            out["producer_omitted"] += 1
    # Reserve the complete repeated envelope per destination, including the
    # longest allowed identity/reason representation. Actual groups can only
    # be smaller. Both records and envelopes count toward the project budget.
    algorithms = snapshot_hash_algorithms()
    reserved_identity = {"value": "0" * max(map(int, algorithms)), "algorithm": max(algorithms.values(), key=len),
        "basis": "atlas_file_text_identity_not_auxiliary_raw_capture"}
    sizes, total, visited, retained, previous = {}, 0, 0, 0, None
    stopped = False
    for index, package in enumerate(packages):
        if index >= policy["max_packages"]:
            partial("package_budget_exceeded", True)
            break
        evidence = _manifest_evidence(package, capture, manifest_digest, policy, manifest_policy)
        manifest = package.get("manifest") if isinstance(package, dict) else None
        if evidence is None or previous is not None and manifest <= previous:
            partial("manifest_evidence_unavailable", True)
            continue
        previous = manifest
        admitted.add(manifest)
        if evidence["status"] == "partial":
            partial("manifest_evidence_partial", True)
        for literal in evidence["records"]:
            if visited >= policy["max_candidates"] or retained >= policy["max_records"]:
                partial("candidate_budget_exceeded" if visited >= policy["max_candidates"] else "record_budget_exceeded", True)
                stopped = True
                break
            visited += 1
            if not _literal_shape(literal, policy, manifest_policy["max_depth"]):
                partial("record_shape_unavailable", True)
                continue
            candidate, reason = _candidate(manifest, literal, policy)
            if reason or not _source_path(candidate, source_vocabulary):
                partial(reason or "outside_source_subset")
                continue
            if not _text(candidate, policy["max_text_bytes"]):
                partial("record_byte_budget_exceeded", True)
                continue
            row = {"source_manifest": manifest, "source_manifest_sha256": evidence["source_hash"], **deepcopy(literal),
                "link_status": "observed", "reason": "literal_indexed_source_correspondence"}
            row_bytes = len(_encoded(row))
            if row_bytes > policy["max_record_bytes"]:
                partial("record_byte_budget_exceeded", True)
                continue
            if candidate not in sizes:
                reserved = _envelope(policy, digest, manifest_digest, root, project, candidate, context, reserved_identity)
                reserved.update(reasons=policy["reason_codes"],
                    producer_omitted=policy["max_candidates"] if len(str(policy["max_candidates"])) > 4 else None,
                    shown_count=policy["max_records"])
                overhead = len(_encoded(reserved))
            else:
                overhead = 1  # Comma between records in the existing group.
            addition = row_bytes + overhead
            if sizes.get(candidate, 0) + addition > policy["max_group_bytes"] or total + addition > policy["max_total_bytes"]:
                partial("group_or_project_byte_budget_exceeded", True)
                stopped = True
                break
            out["by_source"].setdefault(candidate, []).append(row)
            sizes[candidate] = sizes.get(candidate, 0) + addition
            total += addition
            retained += 1
        if stopped:
            break
    if {name for name in files if isinstance(name, str) and PurePosixPath(name).name == "package.json"} - admitted:
        partial("manifest_evidence_unavailable", True)
    out["reasons"] = sorted(reasons)
    return out


def attach_manifest_source_links(file, project, rel, index, *, max_file_bytes=None):
    """Replace only optional evidence on a finalized fresh or copied lifted file."""
    if max_file_bytes is not None and (type(max_file_bytes) is not int or max_file_bytes < 1):
        raise ValueError("Invalid selected source-file byte budget")
    file.pop("manifest_type_entry_links", None)
    if index.get("status") not in {"observed", "partial"} or index.get("project_key") != project:
        return
    rows = index["by_source"].get(rel)
    if not rows:
        return  # Most source files have no retained type pointer: no policy/hash work.
    policy, digest = manifest_source_link_policy()
    if index.get("policy_sha256") != digest:
        return
    identity = _source_identity(file, project, rel, index["context"], policy["max_text_bytes"])
    if identity is None:
        return
    group = _envelope(policy, digest, index["manifest_policy_sha256"], index["project_root"], project, rel, index["context"], identity)
    group.update(status=index["status"], records=deepcopy(rows), shown_count=len(rows),
        producer_omitted=index["producer_omitted"], reasons=list(index["reasons"]))
    if (len(_encoded(group)) > policy["max_group_bytes"]
            or max_file_bytes is not None and len(_encoded({**file, "manifest_type_entry_links": group})) > max_file_bytes):
        return  # Valid ordinary file survives without optional link authority.
    file["manifest_type_entry_links"] = group


def manifest_source_link_group_valid(group, file, project, rel):
    """Admission against the same stored file object, not live freshness/attestation."""
    policy, digest = manifest_source_link_policy()
    manifest_policy, manifest_digest = _type_entry_policy()
    try:
        if not isinstance(group, dict):
            return False
        identity = _source_identity(file, project, rel, group.get("auxiliary_context_identity"), policy["max_text_bytes"])
        if identity is None:
            return False
        base = _envelope(policy, digest, manifest_digest, group.get("project_root"), project, rel, group.get("auxiliary_context_identity"), identity)
        if (set(group) != set(base) or not _text(group["project_root"], policy["max_text_bytes"]) or not group["project_root"]
                or not _text(project, policy["max_text_bytes"]) or not _text(rel, policy["max_text_bytes"])
                or any(group[key] != value for key, value in base.items() if key not in {"status", "records", "shown_count", "producer_omitted", "reasons"})
                or group["status"] not in {"observed", "partial"}
                or not isinstance(group["records"], list) or not 0 < len(group["records"]) <= policy["max_records"]
                or type(group["shown_count"]) is not int or group["shown_count"] != len(group["records"])
                or not (group["producer_omitted"] is None or type(group["producer_omitted"]) is int and group["producer_omitted"] >= 0)
                or not isinstance(group["reasons"], list) or len(group["reasons"]) > len(policy["reason_codes"])
                or any(not isinstance(v, str) or v not in policy["reason_codes"] for v in group["reasons"])
                or group["reasons"] != sorted(set(group["reasons"]))
                or (group["status"] == "observed") != (group["producer_omitted"] == 0 and not group["reasons"])):
            return False
        previous = None
        seen = set()
        for row in group["records"]:
            extra = {"source_manifest", "source_manifest_sha256", "link_status", "reason"}
            if not isinstance(row, dict) or len(row) != 11:
                return False
            literal = {key: value for key, value in row.items() if key not in extra}
            manifest = row.get("source_manifest")
            if (set(row) != set(literal) | extra or not _literal_shape(literal, policy, manifest_policy["max_depth"])
                    or not _canonical_relative(manifest) or PurePosixPath(manifest).name != "package.json"
                    or not _text(manifest, policy["max_text_bytes"]) or not _sha(row["source_manifest_sha256"])
                    or _candidate(manifest, literal, policy) != (rel, "") or row["link_status"] != "observed"
                    or row["reason"] != "literal_indexed_source_correspondence" or len(_encoded(row)) > policy["max_record_bytes"]):
                return False
            key = (manifest, tuple(row["field_path"]))
            order = (manifest, tuple(row["sibling_order"]))
            if key in seen or previous is not None and order <= previous:
                return False
            seen.add(key)
            previous = order
        return len(_encoded(group)) <= policy["max_group_bytes"]
    except (KeyError, TypeError, ValueError, UnicodeError):
        return False
