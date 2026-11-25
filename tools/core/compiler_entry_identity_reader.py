"""Disabled five-role identity pipe for authored temporary installations only.

Synthetic declarations are not actual read permission. Ordered fixture equality
is not atomic/physical origin, loaded code, compiler/Atlas or native acceptance.
"""
from __future__ import annotations

import hashlib
import posixpath
from pathlib import Path
import tempfile
import time
from tools.core import compiler_entry_identity_dispatch as binder
from tools.core import compiler_metadata_dispatch as shared
from tools.core import compiler_text_selection as metadata
from tools.core.compiler_identity_pipe import _owned_identity_pipe, _identity_node

PROFILE = "private_compiler_entry_identity_reader"
CHILD = Path(__file__).with_name("compiler_entry_identity_reader_child.cjs")
_VERSION = "v1-private-synthetic-entry-identity-reader"
_CHILD = "v1-fixed-synthetic-entry-identity-reader"
_STATUS = "SYNTHETIC_READER_TRANSPORT_SETTLED_NOT_COMPILER_ADMISSION"
_now = time.monotonic


def _closed_json_bound(node, schema, probe_bytes, depth=0):
    """Finite UTF8 JSON bound for this closed result schema, not heap/RSS."""
    shared._need(depth < 32, "reader_bound_schema", "policy")
    if "$ref" in node:
        key = node["$ref"].removeprefix("#/$defs/")
        if key == "payload_roles":
            n = schema["$defs"]["payload_role_count"]["const"]
            return 2 + max(0, n - 1) + n * _closed_json_bound(schema["$defs"][key]["items"], schema, probe_bytes, depth + 1)
        return max(4, probe_bytes) if key == "probe_result" else _closed_json_bound(schema["$defs"][key], schema, probe_bytes, depth + 1)
    if "const" in node:
        return len(metadata._canonical(node["const"]))
    if "enum" in node:
        return max(len(metadata._canonical(v)) for v in node["enum"])
    kinds = node["type"] if type(node["type"]) is list else [node["type"]]
    sizes = []
    for kind in kinds:
        if kind in ("null", "boolean", "integer"):
            sizes.append({"null": 4, "boolean": 5, "integer": len(str(metadata.MAX_SAFE))}[kind])
        elif kind == "string":
            shared._need(node.get("pattern") == "^[a-f0-9]{64}$", "reader_bound_schema", "policy")
            sizes.append(66)
        elif kind == "array":
            n = node["maxItems"]
            sizes.append(2 + max(0, n - 1) + n * _closed_json_bound(node["items"], schema, probe_bytes, depth + 1))
        elif kind == "object":
            props = node["properties"]
            shared._need(node.get("additionalProperties") is False and set(node["required"]) == set(props), "reader_bound_schema", "policy")
            sizes.append(2 + max(0, len(props) - 1) + sum(len(metadata._canonical(k)) + 1 + _closed_json_bound(v, schema, probe_bytes, depth + 1) for k, v in props.items()))
        else:
            shared._need(False, "reader_bound_schema", "policy")
    return max(sizes)


def _target(root, limit):
    shared._ascii(root, limit, "target")
    path, temporary = Path(root), Path(tempfile.gettempdir()).resolve(strict=True)
    shared._need(path.is_absolute() and path != temporary and path.is_relative_to(temporary)
                 and ".." not in path.parts, "reader_temporary_root", "target")
    return shared._directory_identity(path, temporary, "synthetic_reader_target_directory_v1")


def _pins(d, s, prepared):
    return {"policy_sha256": metadata._hash(d), "schema_sha256": metadata._hash(s),
            "implementation_sha256": d["source_sha256"], "child_source_sha256": d["child_source_sha256"],
            "lifecycle_source_sha256": d["lifecycle_source_sha256"],
            "dispatch_policy_sha256": metadata._hash(prepared["descriptor"]),
            "dispatch_expected_sha256": metadata._hash(prepared["expected"]),
            "dispatch_decision_sha256": metadata._hash(prepared["decision"]),
            "probe_context_sha256": prepared["expected"]["probe_context_sha256"], "child_id": _CHILD}


def _inner_budget(prepared):
    p, q = prepared["profiles"], prepared["held"]["probe_expected"]
    probe, entry = p[binder.PROBE], p["private_compiler_entry_owner"]
    C = len(metadata._canonical(binder._probe_context(p, prepared["held"]["schemas"], q)))
    inner = {"expected": q["entry_expected"], "descriptor": entry, "capture": p["compiler_library_inputs"],
             "image": p["private_input_image_experiment"], "schema": prepared["held"]["schemas"]["entry"]}
    M = len(metadata._canonical(inner))
    names = set(p["private_compiler_owned_view_basis"]["role_expected_fields"]) | set(p["private_compiler_owned_view_basis"]["role_aliases"])
    record = len(metadata._canonical({"role": "x" * max(map(len, names)), "size_bytes": metadata.MAX_SAFE,
                                     "raw_sha256": "a" * 64, "relative_path_sha256": "a" * 64}))
    dimension = len(metadata._canonical({"text_sha256": "a" * 64, "text_code_units": metadata.MAX_SAFE}))
    meta = sum(row["max_raw_bytes"] for role, row in q["roles"].items() if role != probe["entry_role"])
    session = M + meta + len(q["roles"]) * record + dimension
    reserve = 7 * C + 4 * probe["limits"]["output_bytes"] + q["max_delivered_bytes"] + 2 * q["roles"][probe["entry_role"]]["max_raw_bytes"] + M
    shared._need(session <= p["private_input_image_experiment"]["limits"]["metadata_bytes"], "reader_budget", "before_open")
    return {"probe_context_bytes": C, "inner_input_bytes": M, "session_metadata_upper": session, "probe_named_plus_input": reserve}


def _prepare_reader(profile, schema, expected, prepared, clock, check):
    p = prepared["profiles"]
    probe = p[binder.PROBE]
    held = metadata._plain({"profile": profile, "schema": schema, "expected": expected}, probe["limits"]["context_bytes"], "reader_context")
    d, s, e = (held[k] for k in ("profile", "schema", "expected"))
    metadata._schema_supported(s, "reader_policy")
    for value, definition in ((d, "policy"), (e, "expected")):
        metadata._checked(value, definition, s, "reader_shape", definition)
    shared._need(d["version"] == _VERSION and d["child_id"] == _CHILD and d["status"] == _STATUS
                 and d["scope"] == "authored_temporary_five_role_identity_reads"
                 and all(d[k] is False for k in binder._OFF), "reader_policy", "policy")
    limits, ref = d["limits"], probe["limits"]
    bounds = prepared["descriptor"]["declared_scope_limits"]
    directories = 2 * bounds["root_chars"] + 11 * bounds["path_chars"] + 12
    work = limits["child_input_bytes"] + 1 + probe["scope_limits"]["delivered_bytes"] + limits["child_output_bytes"] + directories + 8 * 5 + 16
    shared._need(all(type(v) is int and 0 < v <= metadata.MAX_SAFE for v in limits.values())
                 and limits["context_bytes"] <= ref["context_bytes"] and limits["child_input_bytes"] <= ref["context_bytes"]
                 and limits["child_output_bytes"] <= 2 * ref["output_bytes"] and limits["result_bytes"] <= 4 * ref["output_bytes"]
                 and limits["metadata_bytes"] <= ref["metadata_bytes"] and limits["image_bytes"] <= ref["image_bytes"]
                 and limits["operation_ms"] <= ref["operation_ms"] and limits["wrapper_steps"] == work,
                 "reader_policy", "limits")
    shared._need(e == _pins(d, s, prepared) and d["schema_sha256"] == e["schema_sha256"]
                 and d["dispatch_policy_sha256"] == e["dispatch_policy_sha256"], "reader_identity", "pins")
    clock.end = min(clock.end, clock.started + limits["operation_ms"] / 1000)
    check("reader_capture")
    q = prepared["held"]["probe_expected"]
    profiles = {k: p[k] for k in binder._DEPENDENCIES}
    payload = {"root": q["root_text"], "expected": q, "profiles": profiles,
               "schema_refs": {k: metadata._hash(prepared["held"]["schemas"][k]) for k in ("probe", "entry")}}
    binding = metadata._hash({"transport_expected": e, "request_binding_sha256": prepared["intent"]["request_binding_sha256"]})
    request = {"version": _CHILD, "request": prepared["request"], "expected": prepared["expected"],
               "transport_expected": e, "probe": payload, "transport_binding_sha256": binding,
               "request_binding_sha256": prepared["intent"]["request_binding_sha256"],
               "operation_ms": limits["operation_ms"], "wrapper_steps": work, "limits": limits}
    metadata._checked(request, "child_request", s, "reader_shape", "child_request")
    wire = metadata._canonical(request)
    reserve = _inner_budget(prepared)
    # The fixed schema owns a finite success/refusal envelope bound; the probe
    # result contributes its separate already bounded canonical receipt capacity.
    output_bound = _closed_json_bound(s["$defs"]["node_result"], s, probe["limits"]["output_bytes"]) + 1
    parent_output_bound = _closed_json_bound(s["$defs"]["result"], s, probe["limits"]["output_bytes"])
    intent = dict(prepared["intent"])
    intent["reader"] = {"binding_sha256": binding, "expected_sha256": metadata._hash(e)}
    intent_wire = metadata._canonical(intent)
    parent = prepared["reserved"] + 2 * len(metadata._canonical(held)) + 5 * limits["child_input_bytes"] + 2 * limits["child_output_bytes"] + limits["result_bytes"]
    wrapper = 5 * (limits["child_input_bytes"] + 1) + 4 * limits["child_output_bytes"] + 32 * (bounds["root_chars"] + bounds["path_chars"] + 1)
    joined = parent + wrapper + reserve["probe_named_plus_input"]
    shared._need(len(metadata._canonical(held)) <= limits["context_bytes"] and len(wire) <= limits["child_input_bytes"]
                 and len(intent_wire) <= prepared["descriptor"]["limits"]["intent_bytes"]
                 and output_bound <= limits["child_output_bytes"]
                 and parent_output_bound <= limits["result_bytes"]
                 and parent <= limits["metadata_bytes"] and joined <= limits["image_bytes"], "reader_budget", "before_reserve")
    return held, request, wire, intent_wire, reserve, parent, wrapper, joined


def _validate_node(node, prepared, q, s, probe_named_plus_input):
    node = metadata._plain(node, q["limits"]["child_output_bytes"], "reader_output")
    metadata._checked(node, "node_result", s, "reader_output", "child")
    shared._need(len(node["payload_accounting"]["roles"]) <= s["$defs"]["payload_role_count"]["const"] == 5,
                 "reader_accounting", "child")
    shared._need(node["version"] == _CHILD and node["request_binding_sha256"] == q["request_binding_sha256"]
                 and node["transport_binding_sha256"] == q["transport_binding_sha256"]
                 and node["probe_context_sha256"] == q["expected"]["probe_context_sha256"]
                 and node["wrapper_steps"] <= q["wrapper_steps"], "reader_identity", "child")
    if node["status"] != "SYNTHETIC_FIVE_ROLE_READER_COMPLETED":
        shared._need(node["status"] == "SYNTHETIC_FIVE_ROLE_READER_REFUSED" and node["observation"] is None and type(node["error"]) is dict,
                     "reader_output", "child")
        return node
    from tools.core.compiler_identity_observation import validate_identity_observation
    metadata._checked(node["observation"], "result", prepared["held"]["schemas"]["probe"], "reader_output", "probe_result")
    return validate_identity_observation(node, prepared, q, probe_named_plus_input)


def _read_synthetic_compiler_entry_identity(request, decision, expected, probe_expected, profiles, schemas,
                                           reader_profile, reader_schema, reader_expected, *, fixture_root, deadline=None):
    c = shared._enter()
    c.update(node_attempts=0, node_returns=0, node_reaped=False, io_threads_remaining=None,
             node_outcome_received=False, node_outcome_verified=False)
    prepared = held = q = node = result = wire = None
    try:
        clock = metadata._Clock(_now, deadline)
        def check(stage):
            c["stage"] = stage
            shared._need(shared._ACTIVE is c and not c["poisoned"], "dispatch_reentry", stage)
            clock.check(stage)
            shared._need(shared._ACTIVE is c and not c["poisoned"], "dispatch_reentry", stage)
        prepared = binder._prepare(request, decision, expected, probe_expected, profiles, schemas, fixture_root, clock, check)
        held, q, wire, intent_wire, reserve, parent, wrapper, joined = _prepare_reader(reader_profile, reader_schema, reader_expected, prepared, clock, check)
        d, s, e = (held[k] for k in ("profile", "schema", "expected"))
        executable = _identity_node()
        chars = prepared["descriptor"]["declared_scope_limits"]["root_chars"]
        target, target_ref = _target(q["probe"]["root"], chars)
        shared._need(target != prepared["directory"] and not target.is_relative_to(prepared["directory"])
                     and not prepared["directory"].is_relative_to(target), "reader_store_overlap", "target")
        check("before_reserve")
        shared._need(shared._directory(fixture_root, prepared["descriptor"]["intent_store_path_chars"])[1] == prepared["store_ref"], "dispatch_store_identity", "reserve")
        shared._reserve(prepared["directory"] / (prepared["decision_key"] + ".intent"), intent_wire, c, check)
        shared._need(_target(str(target), chars)[1] == target_ref
                     and shared._directory(fixture_root, prepared["descriptor"]["intent_store_path_chars"])[1] == prepared["store_ref"],
                     "reader_target_changed", "launch")
        with _owned_identity_pipe(executable, CHILD, wire, c, clock, check, d["limits"]) as outcome:
            node = metadata._plain(outcome, d["limits"]["child_output_bytes"], "reader_output")
            node = _validate_node(node, prepared, q, s, reserve["probe_named_plus_input"])
            c["node_outcome_verified"] = True
            shared._need(node["status"] == "SYNTHETIC_FIVE_ROLE_READER_COMPLETED", "reader_refused", "child")
        check("publication")
        shared._need(_target(str(target), chars)[1] == target_ref
                     and shared._directory(fixture_root, prepared["descriptor"]["intent_store_path_chars"])[1] == prepared["store_ref"], "reader_target_changed", "publication")
        result = {"version": _VERSION, "status": _STATUS, "expected": e, "node": node,
                  "request_binding_sha256": q["request_binding_sha256"], "transport_binding_sha256": q["transport_binding_sha256"],
                  "node_attempts": c["node_attempts"], "node_reaped": True, "io_threads_remaining": 0,
                  "target_directory_ref_sha256": target_ref, "intent_store_ref_sha256": prepared["store_ref"],
                  "resource_bounds": dict(reserve, parent_named=parent, wrapper_named=wrapper, joined_named=joined),
                  "flags": {k: False for k in d["denied_flags"]}, "full_native_cost": None, "physical_heap_rss": None}
        metadata._checked(result, "result", s, "reader_output", "result")
        shared._need(len(metadata._canonical(result)) <= d["limits"]["result_bytes"], "reader_budget", "result")
        check("final")
        c["publications"] += 1
        return result
    except BaseException as error:
        shared._note_failure(error, c)
        try:
            o = dict(error.metadata_dispatch_observation)
            o["actual_payload_reads"] = (node["payload_accounting"]["read_attempts"] if c["node_outcome_verified"]
                                        else None if c["node_attempts"] else 0)
            BaseException.__setattr__(error, "metadata_dispatch_observation", o)
            if node is not None:
                BaseException.__setattr__(error, "node_observation", node)
        except BaseException:
            # Preserve the primary error and expose failed observation attachment
            # through Python's exception context without an out-of-scope write.
            raise error
        raise
    finally:
        prepared = held = q = node = result = wire = None
        shared._finish(c)
