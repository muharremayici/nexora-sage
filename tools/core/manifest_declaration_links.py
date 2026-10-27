"""Literal manifest-to-captured-header correspondence, never package resolution."""
import hashlib
import json
from pathlib import PurePosixPath

from tools.core.atlas_integrity import payload_sha256
from tools.core.atlas_typescript_inputs import auxiliary_input_policy, declaration_contract_policy, declaration_file_shape_valid
from tools.core.package_contracts import _type_entry_policy


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def manifest_link_policy():
    policy = auxiliary_input_policy()["manifest_declaration_links"]
    if policy.get("version") != "v1-literal-manifest-declaration-links" or policy.get("scope") != "top_level_types_typings_literal_path_correspondence":
        raise ValueError("Invalid manifest/declaration link version or scope")
    for name in ("max_packages", "max_candidates", "max_records", "max_text_bytes", "max_record_bytes", "max_total_bytes"):
        if type(policy.get(name)) is not int or policy[name] < 1:
            raise ValueError("Invalid manifest/declaration link budget: " + name)
    for name in ("literal_fields", "reason_codes"):
        if not isinstance(policy.get(name), list) or not policy[name] or any(not isinstance(v, str) or not v for v in policy[name]) or len(set(policy[name])) != len(policy[name]):
            raise ValueError("Invalid manifest/declaration link vocabulary: " + name)
    if policy["max_text_bytes"] > policy["max_record_bytes"] or policy["max_record_bytes"] > policy["max_total_bytes"]:
        raise ValueError("Manifest/declaration link byte budgets are inconsistent")
    return policy, hashlib.sha256(_encoded(policy)).hexdigest()


def _sha(value):
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _text(value, limit):
    try:
        return isinstance(value, str) and len(value.encode("utf-8")) <= limit
    except UnicodeError:
        return False


def _canonical_relative(value):
    return (isinstance(value, str) and bool(value) and not any(c in value for c in ("\\", ":", "\x00"))
        and not value.startswith("/") and all(part not in {"", ".", ".."} for part in value.split("/")))


def _candidate(manifest, row, policy):
    if row["field_path"] not in [[name] for name in policy["literal_fields"]] or row["condition_path"] or row["version_selector"] is not None or row["subpath"] is not None:
        return None, "conditional_or_versioned_entry"
    literal = row["target_literal"]
    relative = literal[2:] if literal.startswith("./") else literal
    if not _canonical_relative(relative) or any(c in relative for c in ("*", "?", "#", "%")):
        return None, "unsupported_target_form"
    return (PurePosixPath(manifest).parent / relative).as_posix(), ""


def _literal_shape(row, policy, depth):
    fields = {"field_path", "sibling_order", "condition_path", "subpath", "version_selector", "target_literal", "resolution_status"}
    if not isinstance(row, dict) or set(row) != fields or row["resolution_status"] != "not_evaluated":
        return False
    path, order, conditions = row["field_path"], row["sibling_order"], row["condition_path"]
    return (isinstance(path, list) and 0 < len(path) <= depth and _text(path[0], policy["max_text_bytes"]) and path[0] in {"types", "typings", "exports", "typesVersions"}
        and all(type(v) is int and v >= 0 or _text(v, policy["max_text_bytes"]) for v in path)
        and isinstance(order, list) and len(order) == len(path) and all(type(v) is int and v >= 0 for v in order)
        and isinstance(conditions, list) and len(conditions) <= depth and all(_text(v, policy["max_text_bytes"]) for v in conditions)
        and all(row[key] is None or _text(row[key], policy["max_text_bytes"]) for key in ("subpath", "version_selector"))
        and _text(row["target_literal"], policy["max_text_bytes"]))


def _capture_entry(entry):
    return (isinstance(entry, dict) and entry.get("status") == "ok" and entry.get("canonical_file_path") is True
        and type(entry.get("size_bytes")) is int and 0 <= entry["size_bytes"] <= auxiliary_input_policy()["max_file_bytes"]
        and _sha(entry.get("raw_sha256")))


def _manifest_evidence(package, capture, digest, policy, manifest_policy):
    if not isinstance(package, dict):
        return None
    manifest, evidence = package.get("manifest"), package.get("type_entry_evidence")
    if not _canonical_relative(manifest) or not _text(manifest, policy["max_text_bytes"]) or PurePosixPath(manifest).name != "package.json":
        return None
    directory = PurePosixPath(manifest).parent.as_posix()
    entry = capture["files"].get(manifest)
    if (package.get("package_root") != ("" if directory == "." else directory) or not _capture_entry(entry)
            or not isinstance(evidence, dict) or evidence.get("source_manifest") != manifest
            or evidence.get("source_hash") != entry["raw_sha256"] or evidence.get("policy_sha256") != digest
            or evidence.get("version") != "v1" or evidence.get("hash_basis") != "sha256_raw_bytes"
            or evidence.get("scope") != "literal_type_fields_and_explicit_type_conditions"
            or evidence.get("runtime_execution") != "not_established" or evidence.get("status") not in ("observed", "partial")
            or not isinstance(evidence.get("records"), list) or len(evidence["records"]) > manifest_policy["max_records"]
            or type(evidence.get("shown_count")) is not int or evidence["shown_count"] != len(evidence["records"])
            or not (evidence.get("omitted_count") is None or type(evidence["omitted_count"]) is int and evidence["omitted_count"] >= 0)
            or not isinstance(evidence.get("reasons"), list)
            or evidence["status"] == "observed" and (evidence["omitted_count"] != 0 or evidence["reasons"])):
        return None
    return evidence


def build_manifest_declaration_links(capture, public_contracts, plane, *, max_plane_bytes=None):
    """Derive from owned in-memory inputs only, including positive partial scope."""
    policy, digest = manifest_link_policy()
    if max_plane_bytes is not None and (type(max_plane_bytes) is not int or max_plane_bytes < 1):
        raise ValueError("Invalid selected declaration-plane byte budget")
    decl_policy, decl_digest = declaration_contract_policy()
    manifest_policy, manifest_digest = _type_entry_policy()
    root = plane.get("project_root") if isinstance(plane, dict) else None
    out = {"version": policy["version"], "scope": policy["scope"], "status": "unavailable", "policy_sha256": digest,
        "manifest_policy_sha256": manifest_digest, "declaration_policy_sha256": decl_digest,
        "capture_sha256": None, "project_root": root if _text(root, policy["max_text_bytes"]) else "",
        "records": [], "shown_count": 0, "omitted_count": None, "reasons": ["auxiliary_scope_unavailable"], "runtime_execution": "not_established"}
    if (not isinstance(capture, dict) or capture.get("version") != "v1" or capture.get("status") not in ("captured", "partial")
            or capture.get("scope") != "owned_walk_positive_inputs_only" or capture.get("negative_resolution_authority") is not False
            or not isinstance(capture.get("files"), dict) or len(capture["files"]) > auxiliary_input_policy()["max_files"]
            or not out["project_root"] or capture.get("project_root") != out["project_root"]):
        return out
    if (not isinstance(plane, dict) or plane.get("status") not in ("observed", "partial") or plane.get("policy_sha256") != decl_digest
            or plane.get("version") != decl_policy["version"] or not isinstance(plane.get("files"), dict)
            or plane.get("coverage") != "finite_top_level_headers_only" or plane.get("runtime_execution") != "not_established"
            or len(plane["files"]) > decl_policy["max_files"]
            or type(plane.get("selected_files")) is not int or plane["selected_files"] != len(plane["files"])):
        out["reasons"] = ["declaration_scope_unavailable"]
        return out
    packages = public_contracts.get("packages") if isinstance(public_contracts, dict) else None
    if not isinstance(packages, list):
        out["reasons"] = ["package_scope_unavailable"]
        return out
    try:
        capture_digest = payload_sha256(capture)
    except (TypeError, ValueError, UnicodeError):
        return out
    out.update(status="observed", capture_sha256=capture_digest, omitted_count=0, reasons=[])
    reasons = set()
    def partial(reason, unknown=False):
        reasons.add(reason)
        out["status"] = "partial"
        if unknown: out["omitted_count"] = None
    if capture["status"] == "partial": partial("auxiliary_scope_partial", True)
    if plane["status"] == "partial": partial("declaration_scope_partial", plane.get("omitted_files") is None)
    # Producer already sorts canonical manifests; preserve that order without
    # sorting/copying an unbounded or malformed caller-supplied package list.
    visited = 0
    retained_bytes = 0
    previous_manifest = None
    captured_manifests = {name for name in capture["files"] if isinstance(name, str) and PurePosixPath(name).name == "package.json"}
    admitted_manifests = set()
    for index, package in enumerate(packages):
        if index >= policy["max_packages"]:
            partial("package_budget_exceeded", True)
            break
        evidence = _manifest_evidence(package, capture, manifest_digest, policy, manifest_policy)
        manifest = package.get("manifest") if isinstance(package, dict) else None
        if evidence is None or previous_manifest is not None and manifest <= previous_manifest:
            partial("manifest_evidence_unavailable", True)
            continue
        previous_manifest = manifest
        admitted_manifests.add(manifest)
        if evidence["status"] == "partial": partial("manifest_evidence_partial", True)
        for literal in evidence["records"]:
            if visited >= policy["max_candidates"] or len(out["records"]) >= policy["max_records"]:
                partial("candidate_budget_exceeded" if visited >= policy["max_candidates"] else "record_budget_exceeded", True)
                break
            visited += 1
            if not _literal_shape(literal, policy, manifest_policy["max_depth"]):
                partial("record_shape_unavailable", True)
                continue
            candidate, reason = _candidate(manifest, literal, policy)
            target, target_hash = plane["files"].get(candidate), None
            if not reason:
                if not candidate.lower().endswith(tuple(decl_policy["supported_suffixes"])):
                    reason = "outside_declaration_subset"
                elif candidate not in plane["files"]:
                    reason = "target_not_in_captured_headers"
                elif not declaration_file_shape_valid(target, decl_policy, decl_digest):
                    reason = "declaration_headers_unavailable"
                else:
                    entry = capture["files"].get(candidate)
                    if not _capture_entry(entry) or entry["raw_sha256"] != target["parser_input_sha256"] or entry["size_bytes"] != target["size_bytes"]:
                        reason = "declaration_capture_mismatch"
                    else:
                        target_hash = target["parser_input_sha256"]
            row = {"source_manifest": manifest, "source_manifest_sha256": evidence["source_hash"], **literal,
                "candidate_declaration_ref": candidate, "captured_declaration_sha256": target_hash,
                "link_status": "unresolved" if reason else "observed", "reason": reason or "literal_target_correspondence"}
            row_bytes = len(_encoded(row))
            if row_bytes > policy["max_record_bytes"]:
                partial("record_byte_budget_exceeded", True)
                continue
            # Limit the whole serialized group, not merely each row. Construct
            # no duplicate list or complete Atlas serialization for this check.
            out["records"].append(row)
            out["shown_count"] = len(out["records"])
            if reason: partial(reason)
            out["reasons"] = sorted(reasons)
            retained_bytes += row_bytes
            size = len(_encoded({**out, "records": []})) + retained_bytes + max(0, len(out["records"]) - 1)
            if size > policy["max_total_bytes"]:
                out["records"].pop()
                retained_bytes -= row_bytes
                out["shown_count"] = len(out["records"])
                partial("group_byte_budget_exceeded", True)
                break
        if reasons.intersection({"candidate_budget_exceeded", "record_budget_exceeded", "group_byte_budget_exceeded"}):
            break
    if captured_manifests - admitted_manifests:
        # The existing manifest reader can skip malformed/unreadable input.
        # Missing projection is unknown, never an observed-empty link scope.
        partial("manifest_evidence_unavailable", True)
    out["reasons"] = sorted(reasons)
    if len(_encoded({**out, "records": []})) + retained_bytes + max(0, len(out["records"]) - 1) > policy["max_total_bytes"]:
        out.update(status="unavailable", records=[], shown_count=0, omitted_count=None, reasons=["group_byte_budget_exceeded"])
    if max_plane_bytes is not None and len(_encoded({**plane, "manifest_type_links": out})) > max_plane_bytes:
        # Preserve the independently valid header plane. The missing optional
        # extension means link-unavailable, never empty/complete link authority.
        return None
    return out


def manifest_link_group_valid(group, plane):
    """Validate stored correspondence, not live capture or producer authenticity."""
    policy, digest = manifest_link_policy()
    decl_policy, decl_digest = declaration_contract_policy()
    manifest_policy, manifest_digest = _type_entry_policy()
    fields = {"version", "scope", "status", "policy_sha256", "manifest_policy_sha256", "declaration_policy_sha256",
        "capture_sha256", "project_root", "records", "shown_count", "omitted_count", "reasons", "runtime_execution"}
    try:
        if (not isinstance(group, dict) or set(group) != fields or group["version"] != policy["version"] or group["scope"] != policy["scope"]
                or group["policy_sha256"] != digest or group["manifest_policy_sha256"] != manifest_digest
                or group["declaration_policy_sha256"] != decl_digest or group["project_root"] != plane["project_root"]
                or not _text(group["project_root"], policy["max_text_bytes"]) or not _sha(group["capture_sha256"])
                or group["status"] not in ("observed", "partial") or group["runtime_execution"] != "not_established"
                or not isinstance(group["records"], list) or len(group["records"]) > policy["max_records"]
                or type(group["shown_count"]) is not int or group["shown_count"] != len(group["records"])
                or not (group["omitted_count"] is None or type(group["omitted_count"]) is int and group["omitted_count"] >= 0)
                or not isinstance(group["reasons"], list) or len(group["reasons"]) > len(policy["reason_codes"])
                or group["reasons"] != sorted(set(group["reasons"]))
                or any(reason not in policy["reason_codes"] for reason in group["reasons"])
                or (group["status"] == "observed") != (group["omitted_count"] == 0 and not group["reasons"])):
            return False
        extras = {"source_manifest", "source_manifest_sha256", "candidate_declaration_ref", "captured_declaration_sha256", "link_status", "reason"}
        seen = set()
        total = len(_encoded({**group, "records": []}))
        previous = None
        for row in group["records"]:
            if not isinstance(row, dict) or len(row) != 13:
                return False
            literal = {key: value for key, value in row.items() if key not in extras}
            if (set(row) != set(literal) | extras or not _literal_shape(literal, policy, manifest_policy["max_depth"])
                    or not _canonical_relative(row["source_manifest"]) or PurePosixPath(row["source_manifest"]).name != "package.json"
                    or not _text(row["source_manifest"], policy["max_text_bytes"]) or not _sha(row["source_manifest_sha256"])):
                return False
            key = (row["source_manifest"], tuple(row["field_path"]))
            if key in seen: return False
            seen.add(key)
            order = (row["source_manifest"], tuple(row["sibling_order"]))
            if previous is not None and order <= previous: return False
            previous = order
            candidate, reason = _candidate(row["source_manifest"], literal, policy)
            if row["candidate_declaration_ref"] != candidate: return False
            if row["link_status"] == "observed":
                target = plane["files"].get(candidate)
                if (reason or not candidate.lower().endswith(tuple(decl_policy["supported_suffixes"]))
                        or row["reason"] != "literal_target_correspondence" or not declaration_file_shape_valid(target, decl_policy, decl_digest)
                        or row["captured_declaration_sha256"] != target["parser_input_sha256"]):
                    return False
            elif (row["link_status"] != "unresolved" or row["captured_declaration_sha256"] is not None
                    or row["reason"] not in policy["reason_codes"] or row["reason"] == "literal_target_correspondence"
                    or row["reason"] not in group["reasons"] or reason and row["reason"] != reason):
                return False
            row_bytes = len(_encoded(row))
            total += row_bytes + (len(seen) > 1)
            if row_bytes > policy["max_record_bytes"] or total > policy["max_total_bytes"]:
                return False
        return total <= policy["max_total_bytes"]
    except (KeyError, TypeError, ValueError, UnicodeError):
        return False
