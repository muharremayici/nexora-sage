"""Disabled synthetic decision linkage and exclusive dispatch; no input reader.

A supplied synthetic decision is not permission. The trusted caller fixes the
temporary intent directory and independently supplies linkage metadata. This
module never grants or performs actual compiler acquisition.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import stat
import tempfile
import threading
import time
from tools.core import compiler_text_selection as metadata
from tools.core.source_preservation_reads import _relative

PROFILE = "private_compiler_metadata_dispatch"
_LOCK = threading.Lock()
_ACTIVE = None


class MetadataDispatchRefusal(ValueError):
    def __init__(self, code, stage):
        super().__init__(code)
        self.code, self.stage = code, stage


def _need(condition, code, stage):
    if not condition:
        raise MetadataDispatchRefusal(code, stage)


def _keys(value, names, stage):
    _need(type(value) is dict and set(value) == set(names), "dispatch_shape", stage)


def _ascii(value, limit, stage):
    _need(type(value) is str and 0 < len(value) <= limit and all(32 <= ord(c) <= 126 for c in value),
          "dispatch_text", stage)


def _directory(value, path_chars):
    """Only owned temporary intent metadata is touched; no target path is opened."""
    _need(type(value) is str and 0 < len(value) <= path_chars and "\0" not in value, "dispatch_store", "store")
    root = Path(value)
    temporary = Path(tempfile.gettempdir()).resolve(strict=True)
    _need(root.is_absolute() and root != temporary and root.is_relative_to(temporary)
          and ".." not in root.parts, "dispatch_store", "store")
    return _directory_identity(root, temporary, "synthetic_intent_directory_identity_v1")


def _directory_identity(root, ancestor, scheme):
    """Shared component-before-following gate; callers fix the root namespace."""
    cursor = ancestor
    info = cursor.lstat()
    for part in root.relative_to(ancestor).parts:
        cursor = cursor / part
        info = cursor.lstat()
        _need(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode)
              and not getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0),
              "dispatch_store", "store")
    # Reject aliases component by component before resolve can follow a link.
    resolved = root.resolve(strict=True)
    _need(root == resolved, "dispatch_store", "store")
    _need(type(info.st_ino) is int and info.st_ino > 0, "dispatch_store_identity", "store")
    identity = metadata._hash({"scheme": scheme,
                              "root_text": str(resolved), "device": info.st_dev, "inode": info.st_ino})
    return resolved, identity


def _nonreading_child(request, binding):
    """Fixed synthetic endpoint. No callback, executable selector or reader."""
    return {"version": "v1-fixed-nonreading-metadata-child", "status": "SYNTHETIC_METADATA_NOT_OBSERVED",
            "request_binding_sha256": binding, "payload_open_attempts": 0,
            "identities": {role: {"expectation": dict(row["expectation"]), "observed_sha256": None,
                                  "expected_match": None} for role, row in request["roles"].items()}}


def _pins(profiles, descriptor, schema, *, _actual=False):
    basis = profiles["private_compiler_owned_view_basis"]
    path_policy = profiles["private_compiler_entry_owner"]
    return {"policy_sha256": metadata._hash(descriptor), "schema_sha256": metadata._hash(schema),
            "implementation_sha256": descriptor["source_sha256"],
            "observer_policy_sha256": metadata._hash(profiles["private_compiler_historical_metadata_observer" if _actual else "private_compiler_metadata_only_observer"]),
            "role_policy_sha256": metadata._hash({"metadata_roles": basis["metadata_roles"], "entry_role": basis["entry_role"]}),
            "path_policy_sha256": metadata._hash({k: path_policy[k] for k in ("owner_id", "relative_path_pattern")}),
            "child_id": descriptor["child_id"]}


def _reserve(path, wire, c, check):
    stream = None
    primary = None
    c["stage"] = "reserve"
    c["intent_open_attempts"] += 1
    try:
        try:
            stream = path.open("xb")
            c["intent_created"] = True
        except FileExistsError as error:
            raise MetadataDispatchRefusal("dispatch_spent", "reserve") from error
        except BaseException:
            c["intent_open_unknown"] = True
            raise
        offset = 0
        while offset < len(wire):
            c["intent_write_attempts"] += 1
            try:
                n = stream.write(wire[offset:])
            except BaseException:
                c["intent_write_unknown"] = True
                raise
            if type(n) is not int or not 0 < n <= len(wire) - offset:
                c["intent_write_unknown"] = True
                raise MetadataDispatchRefusal("dispatch_intent_write", "reserve")
            offset += n
            c["known_intent_bytes"] += n
            check("intent_write")
        stream.flush()
        os.fsync(stream.fileno())
        c["intent_fsync_completed"] = True
    except BaseException as error:
        primary = error
        raise
    finally:
        try:
            if stream is not None:
                c["intent_close_attempts"] += 1
                try:
                    stream.close()
                    c["intent_close_returns"] += 1
                except BaseException as error:
                    c["intent_close_unknown"] = True
                    if primary is not None:
                        c["secondary_errors"].append(error)
                        raise primary
                    raise
        finally:
            stream = None


def _enter():
    global _ACTIVE
    with _LOCK:
        if _ACTIVE is not None:
            _ACTIVE["poisoned"] = True
            raise MetadataDispatchRefusal("dispatch_reentry", "start")
        c = {"poisoned": False, "stage": "start", "intent_open_attempts": 0, "intent_created": False,
             "intent_open_unknown": False, "intent_write_attempts": 0, "intent_write_unknown": False,
             "known_intent_bytes": 0, "intent_fsync_completed": False, "intent_close_attempts": 0,
             "intent_close_returns": 0, "intent_close_unknown": False, "child_attempts": 0,
             "child_returns": 0, "publications": 0, "secondary_errors": []}
        _ACTIVE = c
    return c

def _finish(c):
    global _ACTIVE
    with _LOCK:
        if _ACTIVE is c:
            _ACTIVE = None


def _note_failure(error, c):
    observation = {k: v for k, v in c.items() if k not in ("secondary_errors",)}
    observation.update(result_published=False, references_cleared=True, retry_authorized=False,
                       secondary_cleanup_errors=len(c["secondary_errors"]),
                       actual_payload_reads=None if c.get("historical_lane") and c.get("node_attempts", 0) else 0,
                       intent_creation_state="unknown" if c["intent_open_unknown"] else
                       "created_returned" if c["intent_created"] else
                       "existing_marker_refused" if type(error) is MetadataDispatchRefusal and error.code == "dispatch_spent" else "not_created_by_this_attempt",
                       known_open_intent_descriptors=None if c["intent_open_unknown"] or c["intent_close_unknown"]
                       else int(c["intent_created"]) - c["intent_close_returns"],
                       full_native_cost=None, physical_heap_rss=None)
    try:
        BaseException.__setattr__(error, "metadata_dispatch_observation", observation)
        BaseException.__setattr__(error, "metadata_dispatch_cleanup_errors", tuple(c["secondary_errors"]))
    except BaseException:
        raise error  # Retain the original failure with attachment failure as context.


def _prepare_binding(request, decision, expected, profiles, schema, fixture_root, c, clock, check, *, _actual=False):
    """One interpretation shared by the two fixed synthetic facades."""
    profile_key = "private_compiler_historical_metadata_dispatch" if _actual else PROFILE
    observer_key = "private_compiler_historical_metadata_observer" if _actual else "private_compiler_metadata_only_observer"
    _need(type(profiles) is dict and type(profiles.get(profile_key)) is dict, "dispatch_policy", "policy")
    raw = profiles[profile_key].get("limits")
    _need(type(raw) is dict and type(raw.get("context_bytes")) is int and 0 < raw["context_bytes"] <= metadata.MAX_SAFE,
          "dispatch_policy", "policy")
    held = metadata._plain({"profiles": profiles, "schema": schema}, raw["context_bytes"], "dispatch_context")
    p, schema = held["profiles"], held["schema"]
    _keys(p, (profile_key, observer_key, "private_compiler_owned_view_basis",
              "private_compiler_entry_owner", "private_input_image_experiment"), "policy")
    d, observer, basis = p[profile_key], p[observer_key], p["private_compiler_owned_view_basis"]
    metadata._schema_supported(schema, "dispatch_policy")
    metadata._checked(d, "policy", schema, "dispatch_policy", "policy")
    if _actual:
        _need(d["version"] == "v1-private-historical-metadata-dispatch"
              and d["model"] == "historical_decision_exclusive_intent_fixed_observer_v1"
              and d["scope"] == "historical_four_metadata_one_shot_comparison"
              and d["child_id"] == "v1-fixed-historical-metadata-observer"
              and d["status"] == "HISTORICAL_METADATA_DISPATCH_LINKAGE_NOT_AUTHENTICATION"
              and all(d[k] is False for k in ("default_enabled", "native_execution_enabled", "production_enabled", "semantic_lineage", "cache_eligible")),
              "dispatch_policy", "policy")
    else:
        _need(d["version"] == "v1-private-synthetic-metadata-dispatch"
          and d["model"] == "synthetic_decision_exclusive_intent_fixed_nonreading_child_v1"
          and d["scope"] == "synthetic_four_metadata_authority_linkage_only"
          and d["child_id"] == "v1-fixed-nonreading-metadata-child"
          and d["status"] == "SYNTHETIC_METADATA_DISPATCH_LINKED_NOT_READ_AUTHORITY"
          and all(d[k] is False for k in ("default_enabled", "native_execution_enabled", "production_enabled", "semantic_lineage", "cache_eligible")),
          "dispatch_policy", "policy")
    limits = d["limits"]
    _need(all(type(v) is int and 0 < v <= metadata.MAX_SAFE for v in limits.values()), "dispatch_policy", "policy")
    _need(limits["context_bytes"] <= observer["limits"]["fixed_context_bytes"]
          and limits["metadata_bytes"] <= min(observer["limits"]["metadata_bytes"], p["private_input_image_experiment"]["limits"]["metadata_bytes"])
          and all(limits[k] <= observer["limits"]["output_bytes"] for k in ("request_bytes", "decision_bytes", "intent_bytes", "result_bytes"))
          and limits["operation_ms"] <= observer["limits"]["operation_ms"], "dispatch_policy", "limits")
    _need(type(d["intent_store_path_chars"]) is int and 0 < d["intent_store_path_chars"] <= observer["scope_limits"]["root_chars"],
          "dispatch_policy", "limits")
    _need(len(metadata._canonical(held)) <= limits["context_bytes"], "dispatch_budget", "context")
    clock.bind(limits["operation_ms"])
    check("capture")
    held.update(request=metadata._plain(request, limits["request_bytes"], "dispatch_request"),
                decision=metadata._plain(decision, limits["decision_bytes"], "dispatch_decision"),
                expected=metadata._plain(expected, limits["request_bytes"], "dispatch_expected"))
    r, a, e = (held[k] for k in ("request", "decision", "expected"))
    for value, definition in ((r, "request"), (a, "decision"), (e, "expected")):
        metadata._checked(value, definition, schema, "dispatch_shape", definition)
    pins = _pins(p, d, schema, _actual=_actual)
    _need(e == pins and d["schema_sha256"] == pins["schema_sha256"]
          and all(d[k] == pins[k] for k in ("observer_policy_sha256", "role_policy_sha256", "path_policy_sha256")),
          "dispatch_identity", "pins")
    _check_decision(r, a, d, clock, check, _actual=_actual)
    _need(r["root_scheme"] == d["target_root_scheme"], "dispatch_scope", "scope")
    _ascii(r["root_text"], observer["scope_limits"]["root_chars"], "scope")
    _need(PureWindowsPath(r["root_text"]).is_absolute() or PurePosixPath(r["root_text"]).is_absolute(),
          "dispatch_scope", "scope")
    roles = basis["metadata_roles"]
    _need(len(roles) == 4 and basis["entry_role"] not in roles, "dispatch_scope", "scope")
    _keys(r["roles"], roles, "roles")
    _need(set(d["expectation_classes"]) == ({"historical_supplied"} if _actual else {"independent_supplied", "historical_supplied", "unanchored"}),
          "dispatch_policy", "policy")
    paths = []
    for role in roles:
        row = r["roles"][role]
        _ascii(row["relative_path"], observer["scope_limits"]["path_chars"], "path")
        _need(_relative(row["relative_path"]) == row["relative_path"]
              and re.fullmatch(p["private_compiler_entry_owner"]["relative_path_pattern"], row["relative_path"]),
              "dispatch_scope", "path")
        paths.append(row["relative_path"].casefold())
        _need(type(row["max_raw_bytes"]) is int and 0 < row["max_raw_bytes"] <= observer["scope_limits"]["raw_bytes"],
              "dispatch_scope", "bytes")
        x = row["expectation"]
        _need(x["class"] in d["expectation_classes"], "dispatch_scope", "expectation")
        if x["class"] == "unanchored":
            _need(x["sha256"] is x["anchor_ref_sha256"] is None, "dispatch_expectation", "expectation")
        else:
            _need(metadata._hex(x["sha256"]) and metadata._hex(x["anchor_ref_sha256"])
                  and x["sha256"] != "0" * 64 and x["anchor_ref_sha256"] != "0" * 64,
                  "dispatch_expectation", "expectation")
    raw_total = sum(r["roles"][role]["max_raw_bytes"] for role in roles)
    _need(len(set(paths)) == len(roles) and r["max_open_attempts"] == observer["scope_limits"]["opens"]
          and type(r["unassigned_headroom_bytes"]) is int and r["unassigned_headroom_bytes"] >= 0
          and r["max_total_raw_bytes"] == raw_total
          and r["max_delivered_bytes"] == raw_total + len(roles) + r["unassigned_headroom_bytes"]
          and r["max_delivered_bytes"] <= observer["scope_limits"]["delivered_bytes"], "dispatch_scope", "scope")
    return _prepare_reservation(held, p, schema, d, fixture_root)


def _check_decision(r, a, d, clock, check, *, _actual=False):
    """Shared identity/time interpretation; fixed facades select the declaration kind."""
    limits = d["limits"]
    if _actual:
        _need(a["kind"] == "trusted_parent_one_shot_metadata_declaration" and a["real_read_granted"] is True
              and a["state"] == "unused", "dispatch_decision", "decision")
    else:
        _need(a["kind"] == "synthetic_fixture_not_real_permission" and a["real_read_granted"] is False
          and a["state"] == "unused", "dispatch_decision", "decision")
    return _check_decision_identity_and_time(r, a, d, clock, check)


def _check_decision_identity_and_time(r, a, d, clock, check):
    """Neutral identity/expiry after a fixed facade has validated decision kind."""
    limits = d["limits"]
    for name in ("principal_ref", "decision_ref", "invocation_id"):
        _ascii(r[name], d["identity_chars"], "scope")
    _need(r["purpose"] == a["purpose"] == d["scope"]
          and r["principal_ref"] == a["principal_ref"] and r["decision_ref"] == a["decision_ref"],
          "dispatch_decision", "decision")
    _need(metadata._finite_time(a["issued_at"]) and metadata._finite_time(a["expires_at"])
          and a["issued_at"] <= clock.started < a["expires_at"]
          and a["expires_at"] - a["issued_at"] <= limits["operation_ms"] / 1000,
          "dispatch_decision_stale", "decision")
    clock.end = min(clock.end, a["expires_at"])
    check("scope")


def _prepare_reservation(held, p, schema, d, fixture_root):
    """The same principal/decision key across fixed scopes; no lane reset."""
    r, a, e = (held[k] for k in ("request", "decision", "expected"))
    limits = d["limits"]
    request_wire, decision_wire = metadata._canonical(r), metadata._canonical(a)
    _need(len(request_wire) <= limits["request_bytes"] and len(decision_wire) <= limits["decision_bytes"],
          "dispatch_budget", "capture")
    binding = hashlib.sha256(request_wire).hexdigest()
    _need(a["request_binding_sha256"] == binding and a["expected"] == e, "dispatch_identity", "binding")
    directory, store_ref = _directory(fixture_root, d["intent_store_path_chars"])
    _need(a["intent_store_ref_sha256"] == store_ref, "dispatch_store_identity", "store")
    decision_key = metadata._hash({k: a[k] for k in ("principal_ref", "decision_ref")})
    intent = {"version": d["version"], "state": "DISPATCH_RESERVED_OUTCOME_UNKNOWN",
              "decision_key_sha256": decision_key, "request_binding_sha256": binding,
              "invocation_id": r["invocation_id"], "intent_store_ref_sha256": store_ref, "expected": e}
    wire = metadata._canonical(intent)
    reserved = 2 * len(metadata._canonical(held)) + 3 * len(request_wire) + 2 * len(decision_wire) + limits["intent_bytes"] + limits["result_bytes"]
    _need(len(wire) <= limits["intent_bytes"] and reserved <= limits["metadata_bytes"], "dispatch_budget", "reserve")
    return {"held": held, "profiles": p, "schema": schema, "descriptor": d,
            "request": r, "decision": a, "expected": e, "directory": directory,
            "store_ref": store_ref, "decision_key": decision_key, "intent": intent,
            "wire": wire, "reserved": reserved}


def _complete_nonreading(prepared, c, clock, check, fixture_root):
    """Only exclusive intent metadata and the fixed in-process nonreading stub."""
    child = result = wire = held = None
    try:
        held, p, schema, d = (prepared[k] for k in ("held", "profiles", "schema", "descriptor"))
        r, a, e = (prepared[k] for k in ("request", "decision", "expected"))
        directory, store_ref, decision_key = (prepared[k] for k in ("directory", "store_ref", "decision_key"))
        wire, reserved = prepared["wire"], prepared["reserved"]
        limits, binding = d["limits"], prepared["intent"]["request_binding_sha256"]
        check("before_reserve")
        _need(_directory(fixture_root, d["intent_store_path_chars"])[1] == store_ref, "dispatch_store_identity", "reserve")
        _reserve(directory / (decision_key + ".intent"), wire, c, check)
        check("before_child")
        _need(_directory(fixture_root, d["intent_store_path_chars"])[1] == store_ref, "dispatch_store_identity", "child")
        c["child_attempts"] += 1
        child = _nonreading_child(metadata._plain(r, limits["request_bytes"], "dispatch_child"), binding)
        c["child_returns"] += 1
        check("after_child")
        child = metadata._plain(child, limits["result_bytes"], "dispatch_child")
        metadata._checked(child, "child", schema, "dispatch_child_invalid", "child")
        _need(child["request_binding_sha256"] == binding
              and child["identities"] == {role: {"expectation": row["expectation"], "observed_sha256": None, "expected_match": None}
                                          for role, row in r["roles"].items()}, "dispatch_child_invalid", "child")
        result = {"version": d["version"], "status": d["status"], "invocation_id": r["invocation_id"],
                  "request_binding_sha256": binding, "decision_key_sha256": decision_key, "intent_store_ref_sha256": store_ref,
                  "expected": e, "child": child, "child_sha256": metadata._hash(child),
                  "reservation": {k: v for k, v in c.items() if k.startswith("intent_") or k == "known_intent_bytes"},
                  "known_metadata_reservation_bytes": reserved,
                  "flags": {k: False for k in d["denied_flags"]}}
        metadata._checked(result, "result", schema, "dispatch_result_invalid", "result")
        _need(len(metadata._canonical(result)) <= limits["result_bytes"], "dispatch_budget", "result")
        check("publication")
        _need(_directory(fixture_root, d["intent_store_path_chars"])[1] == store_ref, "dispatch_store_identity", "publication")
        c["publications"] += 1
        return result
    finally:
        held = p = schema = d = r = a = e = directory = wire = child = result = None


def _dispatch_synthetic_compiler_metadata(request, decision, expected, profiles, schema, *,
                                           fixture_root, now=time.monotonic, deadline=None):
    """One declared synthetic decision and one fixed non-reading endpoint.

    Caller-controlled matching metadata can be forged; this is not human
    authentication, real read permission, OS isolation or actual acquisition.
    The caller owns one fixed temporary store. Its identity is independently
    decision-bound; replacing it cannot silently renew a decision.
    """
    c = _enter()
    held = child = result = wire = None
    try:
        clock = metadata._Clock(now, deadline)
        def check(stage):
            c["stage"] = stage
            _need(_ACTIVE is c and not c["poisoned"], "dispatch_reentry", stage)
            clock.check(stage)
            _need(_ACTIVE is c and not c["poisoned"], "dispatch_reentry", stage)
        prepared = _prepare_binding(request, decision, expected, profiles, schema, fixture_root, c, clock, check)
        return _complete_nonreading(prepared, c, clock, check, fixture_root)
    except BaseException as error:
        _note_failure(error, c)
        raise
    finally:
        held = child = result = wire = prepared = None
        _finish(c)
