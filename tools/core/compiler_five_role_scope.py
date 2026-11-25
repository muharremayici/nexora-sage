"""Shared closed five-role declarations; no filesystem acquisition or authority."""
import hashlib,re
from pathlib import PurePosixPath,PureWindowsPath
from tools.core import compiler_metadata_dispatch as shared
from tools.core import compiler_text_selection as metadata
from tools.core.source_preservation_reads import _relative

def check_five_role_scope(r, probe_expected, p, schemas, d):
    probe, owner, basis = (p[k] for k in ("private_compiler_entry_identity", "private_compiler_entry_owner", "private_compiler_owned_view_basis"))
    metadata._checked(probe_expected, "expected", schemas["probe"], "dispatch_shape", "probe_expected")
    entry = probe_expected["entry_expected"]
    fields = basis["role_expected_fields"]
    shared._need(len(fields) == 5 and fields == schemas["probe"]["$defs"]["role_fields"]["const"]
                 and set(fields) == set(basis["metadata_roles"]) | {basis["entry_role"]}
                 and probe["entry_role"] == basis["entry_role"], "dispatch_policy", "roles")
    shared._keys(r["roles"], fields, "roles")
    shared._keys(probe_expected["roles"], fields, "roles")
    shared._need(r["roles"]["provenance"]["relative_path"] == owner["provenance_relative_path"],
                 "dispatch_scope", "provenance")
    shared._need(probe_expected["policy_sha256"] == metadata._hash(probe)
                 and probe_expected["schema_sha256"] == probe["schema_sha256"]
                 and entry["policy_sha256"] == probe["entry_policy_sha256"]
                 and entry["schema_sha256"] == probe["entry_schema_sha256"]
                 and entry["capture_policy_sha256"] == probe["capture_policy_sha256"]
                 and entry["image_policy_sha256"] == probe["image_policy_sha256"]
                 and entry["text_sha256"] == entry["entry_sha256"]
                 and entry["owner_id"] == owner["owner_id"]
                 and entry["invocation_id"] == r["invocation_id"], "dispatch_identity", "probe_expected")
    shared._ascii(r["root_text"], d["declared_scope_limits"]["root_chars"], "scope")
    win, posix = PureWindowsPath(r["root_text"]), PurePosixPath(r["root_text"])
    shared._need(r["root_scheme"] == d["target_root_scheme"] and r["root_text"] == probe_expected["root_text"]
                 and ((win.is_absolute() and str(win) == r["root_text"] == r["root_text"].lower())
                      or (posix.is_absolute() and str(posix) == r["root_text"] and "\\" not in r["root_text"]))
                 and ".." not in win.parts and ".." not in posix.parts, "dispatch_scope", "root")
    paths = []
    for role, field in fields.items():
        row, supplied = r["roles"][role], probe_expected["roles"][role]
        shared._ascii(row["relative_path"], d["declared_scope_limits"]["path_chars"], "path")
        shared._need(_relative(row["relative_path"]) == row["relative_path"]
                     and re.fullmatch(owner["relative_path_pattern"], row["relative_path"]), "dispatch_scope", "path")
        paths.append(row["relative_path"].casefold())
        cap = probe["scope_limits"]["entry_raw_bytes" if role == basis["entry_role"] else "metadata_raw_bytes"]
        shared._need(type(row["max_raw_bytes"]) is int and 0 <= row["max_raw_bytes"] <= cap, "dispatch_scope", "bytes")
        x = row["expectation"]
        shared._need(x["class"] == "historical_supplied" and metadata._hex(x["sha256"]) and metadata._hex(x["anchor_ref_sha256"])
                     and x["sha256"] != "0" * 64 and x["anchor_ref_sha256"] != "0" * 64, "dispatch_expectation", "expectation")
        shared._need(row["relative_path"] == supplied["relative_path"] and row["max_raw_bytes"] == supplied["max_raw_bytes"]
                     and x["class"] == supplied["expectation_class"] and x["sha256"] == supplied["expected_sha256"] == entry[field]
                     and x["anchor_ref_sha256"] == supplied["anchor_ref_sha256"], "dispatch_identity", "role_binding")
    total = sum(row["max_raw_bytes"] for row in r["roles"].values())
    shared._need(len(set(paths)) == 5 and type(r["max_open_attempts"]) is int and r["max_open_attempts"] == 5
                 and type(r["unassigned_headroom_bytes"]) is int and r["unassigned_headroom_bytes"] == 0
                 and r["max_total_raw_bytes"] == probe_expected["max_total_raw_bytes"] == total <= probe["scope_limits"]["raw_bytes"]
                 and r["max_delivered_bytes"] == probe_expected["max_delivered_bytes"] == total + 5 <= probe["scope_limits"]["delivered_bytes"],
                 "dispatch_scope", "scope")
    # Declared namespaces only: no package.json/main read, stat or realpath.
    package = PurePosixPath(r["roles"][basis["role_aliases"]["package"]]["relative_path"])
    path = PurePosixPath(r["roles"][basis["entry_role"]]["relative_path"])
    shared._need(package.name == "package.json" and path.is_relative_to(package.parent), "dispatch_scope", "member")
    member = path.relative_to(package.parent).as_posix()
    shared._need(hashlib.sha256(member.encode()).hexdigest() == entry["entry_relative_sha256"]
                 and type(entry["entry_bytes"]) is int and entry["entry_bytes"] <= r["roles"][basis["entry_role"]]["max_raw_bytes"],
                 "dispatch_identity", "member")
