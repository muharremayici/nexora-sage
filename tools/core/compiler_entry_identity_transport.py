"""Disabled synthetic metadata pipe. No target directory, input reader or evaluator.

First-party code/schema loading is not target-payload observation. The parent
owns one reservation and the original deadline; lost child outcomes stay unknown.
"""
from __future__ import annotations

from pathlib import Path
import subprocess
import time
from tools.core import compiler_entry_identity_dispatch as binder
from tools.core import compiler_metadata_dispatch as shared
from tools.core import compiler_text_selection as metadata
from tools.core.compiler_stream_driver import _PipeExchange
from tools.core.compiler_identity_pipe import _owned_identity_pipe, _identity_node

PROFILE = "private_compiler_entry_identity_transport"
CHILD = Path(__file__).with_name("compiler_entry_identity_transport_child.cjs")
_now = time.monotonic
_VERSION = "v1-private-synthetic-entry-identity-transport"
_CHILD = "v1-fixed-nonreading-entry-identity-child"
_STATUS = "SYNTHETIC_FIVE_ROLE_TRANSPORT_SETTLED_NOT_READ_AUTHORITY"


def _result(d, e, q, prepared, node, c, named, child_named):
    """Single result shape, also used for a pre-reservation lexical upper bound."""
    return {"version": d["version"], "status": _STATUS, "expected": e,
            "request_binding_sha256": q["request_binding_sha256"], "transport_binding_sha256": q["transport_binding_sha256"],
            "decision_key_sha256": prepared["decision_key"], "intent_store_ref_sha256": prepared["store_ref"],
            "node": node, "node_attempts": 1, "node_reaped": True, "io_threads_remaining": 0,
            "reservation": {k: v for k, v in c.items() if k.startswith("intent_") or k == "known_intent_bytes"},
            "known_parent_metadata_bytes": named, "known_child_named_partial_bytes": child_named,
            "full_native_cost": None, "physical_heap_rss": None, "flags": {k: False for k in d["denied_flags"]}}


def _output_bound(d, s, e, q, prepared, intent_wire, named, child_named):
    """Metadata-only success/refusal bounds, never emitted as observations."""
    node = {"version": _CHILD, "status": "SYNTHETIC_FIVE_ROLE_METADATA_NOT_OBSERVED",
            **{k: q[k] for k in ("request_binding_sha256", "expected_sha256", "transport_binding_sha256")},
            "probe_context_sha256": e["probe_context_sha256"],
            "identities": {role: {"expectation": row["expectation"], "observed_sha256": None, "expected_match": None}
                           for role, row in q["request"]["roles"].items()}, "payload_open_attempts": 0,
            "error": None, "wrapper_steps": d["limits"]["wrapper_steps"], "flags": {k: False for k in d["denied_flags"]}}
    failure = dict(node, status="SYNTHETIC_FIVE_ROLE_METADATA_REFUSED", identities=None, payload_open_attempts=1,
                   error={"code": max(s["$defs"]["refusal_code"]["enum"], key=len), "stage": "publication"})
    reservation = {"intent_open_attempts": 1, "intent_created": True, "intent_open_unknown": False,
                   "intent_write_attempts": len(intent_wire), "intent_write_unknown": False, "known_intent_bytes": len(intent_wire),
                   "intent_fsync_completed": True, "intent_close_attempts": 1, "intent_close_returns": 1, "intent_close_unknown": False}
    shared._need(max(len(metadata._canonical(v)) + 1 for v in (node, failure)) <= d["limits"]["child_output_bytes"]
                 and len(metadata._canonical(_result(d, e, q, prepared, node, reservation, named, child_named))) <= d["limits"]["result_bytes"],
                 "transport_budget", "before_reserve")


def _pins(profile, schema, prepared):
    return {"policy_sha256": metadata._hash(profile), "schema_sha256": metadata._hash(schema),
            "implementation_sha256": profile["source_sha256"], "child_source_sha256": profile["child_source_sha256"],
            "dispatch_policy_sha256": metadata._hash(prepared["descriptor"]),
            "dispatch_expected_sha256": metadata._hash(prepared["expected"]),
            "dispatch_decision_sha256": metadata._hash(prepared["decision"]),
            "probe_context_sha256": prepared["expected"]["probe_context_sha256"], "child_id": _CHILD}


def _prepare_transport(profile, schema, expected, prepared, clock, check):
    reference = prepared["profiles"][binder.PROBE]["limits"]
    held = metadata._plain({"profile": profile, "schema": schema, "expected": expected}, reference["context_bytes"], "transport_context")
    d, s, e = (held[k] for k in ("profile", "schema", "expected"))
    metadata._schema_supported(s, "transport_policy")
    for v, definition in ((d, "policy"), (e, "expected")):
        metadata._checked(v, definition, s, "transport_shape", definition)
    shared._need(d["version"] == _VERSION and d["child_id"] == _CHILD and d["status"] == _STATUS
                 and d["scope"] == "temporary_synthetic_nonreading_five_role_pipe"
                 and all(d[k] is False for k in binder._OFF), "transport_policy", "policy")
    limits = d["limits"]
    ceilings = {"input_bytes": reference["context_bytes"], "output_bytes": reference["output_bytes"],
                "operation_ms": reference["operation_ms"]}
    shared._need(all(type(n) is int and 0 < n <= metadata.MAX_SAFE for n in limits.values())
                 and limits["context_bytes"] <= reference["context_bytes"] and limits["metadata_bytes"] <= reference["metadata_bytes"]
                 and limits["child_input_bytes"] <= ceilings["input_bytes"] and limits["child_output_bytes"] <= ceilings["output_bytes"]
                 and limits["result_bytes"] <= 2 * ceilings["output_bytes"] and limits["image_bytes"] <= reference["image_bytes"]
                 and s["$defs"]["wire_ceilings"]["const"] == ceilings
                 and limits["operation_ms"] <= prepared["descriptor"]["limits"]["operation_ms"]
                 and limits["wrapper_steps"] == limits["child_input_bytes"] + 1 + 5 + 6,
                 "transport_policy", "limits")
    pins = _pins(d, s, prepared)
    shared._need(e == pins and d["schema_sha256"] == e["schema_sha256"]
                 and d["dispatch_policy_sha256"] == e["dispatch_policy_sha256"]
                 and d["denied_flags"] == prepared["descriptor"]["denied_flags"], "transport_identity", "pins")
    shared._need(len(metadata._canonical(held)) + len(metadata._canonical(prepared["held"])) <= limits["context_bytes"],
                 "transport_budget", "context")
    clock.end = min(clock.end, clock.started + limits["operation_ms"] / 1000)
    check("transport_capture")
    binding = metadata._hash({"transport_expected": e, "request_binding_sha256": prepared["intent"]["request_binding_sha256"]})
    q = {"version": _CHILD, "request": prepared["request"], "expected": prepared["expected"], "transport_expected": e,
         "role_expected_fields": prepared["profiles"]["private_compiler_owned_view_basis"]["role_expected_fields"],
         "request_binding_sha256": prepared["intent"]["request_binding_sha256"],
         "expected_sha256": metadata._hash(prepared["expected"]), "transport_binding_sha256": binding,
         "operation_ms": limits["operation_ms"], "wrapper_steps": limits["wrapper_steps"]}
    metadata._checked(q, "child_request", s, "transport_shape", "child_request")
    wire = metadata._canonical(q)
    intent = dict(prepared["intent"])
    intent["transport"] = {k: e[k] for k in ("policy_sha256", "schema_sha256", "child_source_sha256")}
    intent["transport"]["binding_sha256"] = binding
    intent_wire = metadata._canonical(intent)
    named = (prepared["reserved"] + 2 * len(metadata._canonical(held)) + 5 * limits["child_input_bytes"]
             + 2 * limits["child_output_bytes"] + limits["result_bytes"])
    # Declared child wire/decoded/canonical/result forms only, not native/RSS.
    child_named = 5 * (limits["child_input_bytes"] + 1) + 4 * limits["child_output_bytes"]
    shared._need(len(wire) <= limits["child_input_bytes"]
                 and len(intent_wire) <= prepared["descriptor"]["limits"]["intent_bytes"]
                 and named <= limits["metadata_bytes"] and named + child_named <= limits["image_bytes"],
                 "transport_budget", "before_reserve")
    _output_bound(d, s, e, q, prepared, intent_wire, named, child_named)
    return held, q, wire, intent_wire, named, child_named


def _node_result(value, s, q, limits):
    value = metadata._plain(value, limits["child_output_bytes"], "transport_result")
    metadata._checked(value, "node_result", s, "transport_output", "child")
    shared._need(value["version"] == _CHILD and all(value[k] == q[k] for k in
                 ("request_binding_sha256", "expected_sha256", "transport_binding_sha256"))
                 and value["probe_context_sha256"] == q["expected"]["probe_context_sha256"]
                 and value["wrapper_steps"] <= limits["wrapper_steps"]
                 and not any(value["flags"].values()), "transport_identity", "child")
    if value["status"] == "SYNTHETIC_FIVE_ROLE_METADATA_NOT_OBSERVED":
        shared._need(value["error"] is None and value["payload_open_attempts"] == 0
                     and value["identities"] == {role: {"expectation": row["expectation"], "observed_sha256": None,
                                                       "expected_match": None} for role, row in q["request"]["roles"].items()},
                     "transport_output", "child")
    else:
        shared._need(value["status"] == "SYNTHETIC_FIVE_ROLE_METADATA_REFUSED"
                     and value["identities"] is None and type(value["error"]) is dict, "transport_output", "child")
    return value


def _transport_synthetic_compiler_entry_identity(request, decision, expected, probe_expected, profiles, schemas,
                                                transport_profile, transport_schema, transport_expected, *,
                                                fixture_root, deadline=None):
    """One closed, nonreading child. No executable, backend, retry or actual mode."""
    c = shared._enter()
    c.update(node_attempts=0, node_returns=0, node_reaped=False, io_threads_remaining=None,
             node_outcome_received=False, node_outcome_verified=False)
    prepared = held = q = wire = intent_wire = node = result = process = exchange = None
    try:
        clock = metadata._Clock(_now, deadline)
        def check(stage):
            c["stage"] = stage
            shared._need(shared._ACTIVE is c and not c["poisoned"], "dispatch_reentry", stage)
            clock.check(stage)
            shared._need(shared._ACTIVE is c and not c["poisoned"], "dispatch_reentry", stage)
        prepared = binder._prepare(request, decision, expected, probe_expected, profiles, schemas, fixture_root, clock, check)
        held, q, wire, intent_wire, named, child_named = _prepare_transport(
            transport_profile, transport_schema, transport_expected, prepared, clock, check)
        d, s, e = (held[k] for k in ("profile", "schema", "expected"))
        limits = d["limits"]
        executable = _identity_node()
        check("before_reserve")
        shared._need(shared._directory(fixture_root, prepared["descriptor"]["intent_store_path_chars"])[1] == prepared["store_ref"],
                     "dispatch_store_identity", "reserve")
        shared._reserve(prepared["directory"] / (prepared["decision_key"] + ".intent"), intent_wire, c, check)
        # Serialization and directory work precede this fresh sample. Never
        # derive remaining duration from a time preceding that work.
        shared._need(shared._directory(fixture_root, prepared["descriptor"]["intent_store_path_chars"])[1] == prepared["store_ref"],
                     "dispatch_store_identity", "launch")
        with _owned_identity_pipe(executable, CHILD, wire, c, clock, check, limits) as outcome:
            node = _node_result(outcome, s, q, limits)
            c["node_outcome_verified"] = True
            shared._need(node["status"] == "SYNTHETIC_FIVE_ROLE_METADATA_NOT_OBSERVED", "transport_child_refused", "child")
        check("after_child")
        shared._need(c["node_attempts"] == c["node_returns"] == 1
                     and c["node_reaped"] and c["io_threads_remaining"] == 0, "transport_cleanup", "settlement")
        check("publication")
        shared._need(shared._directory(fixture_root, prepared["descriptor"]["intent_store_path_chars"])[1] == prepared["store_ref"],
                     "dispatch_store_identity", "publication")
        result = _result(d, e, q, prepared, node, c, named, child_named)
        metadata._checked(result, "result", s, "transport_output", "result")
        shared._need(len(metadata._canonical(result)) <= limits["result_bytes"], "transport_budget", "result")
        check("final")
        c["publications"] += 1
        return result
    except BaseException as exc:
        shared._note_failure(exc, c)
        # A missing/invalid child outcome is not measured zero payload attempts.
        try:
            observation = dict(exc.metadata_dispatch_observation)
            observation["actual_payload_reads"] = (0 if not c["node_attempts"] or c["node_outcome_verified"] else None)
            observation["source_scope"] = "fixed_nonreading_metadata_pipe_not_reader_authority"
            BaseException.__setattr__(exc, "metadata_dispatch_observation", observation)
        except BaseException:
            raise exc  # Attachment failure remains context, not the primary error.
        raise
    finally:
        prepared = held = q = wire = intent_wire = node = result = process = exchange = None
        shared._finish(c)
