"""Disabled authored-temporary fixed use. No installed grant or evaluator.

One purpose, shared spent decision, one owned process and one five-file capture.
Logical named bounds are partial accounting, never native/heap/RSS acceptance.
"""
from pathlib import Path
import time
from tools.core import compiler_metadata_dispatch as shared
from tools.core import compiler_text_selection as metadata
from tools.core import compiler_entry_identity_dispatch as binder
from tools.core import compiler_entry_identity_reader as reader
from tools.core.compiler_five_role_scope import check_five_role_scope
from tools.core.compiler_identity_observation import validate_identity_observation, require_literal_domains
from tools.core.compiler_identity_pipe import _owned_identity_pipe, _identity_node

PROFILE = "private_compiler_entry_same_read_use_transport"
DISPATCH = "private_compiler_entry_same_read_use_dispatch"
USE = "private_compiler_entry_same_read_use"
CHILD = Path(__file__).with_name("compiler_entry_same_read_use_child.cjs")
_ACTUAL_CHILD = Path(__file__).with_name("compiler_entry_actual_use_child.cjs")
ID = "v1-fixed-synthetic-entry-same-read-use"
VERSION = "v1-private-synthetic-entry-same-read-use-transport"
STATUS = "SYNTHETIC_FIXED_USE_TRANSPORT_SETTLED_NOT_COMPILER_ADMISSION"
PURPOSE = "synthetic_five_role_fixed_use_linkage_only"
_now = time.monotonic
from collections import namedtuple
_FixedUseContract = namedtuple("_FixedUseContract", "child version status purpose identity_status use_status node_completed node_refused")
_SYNTHETIC = _FixedUseContract(ID, VERSION, STATUS, PURPOSE,
    "SYNTHETIC_FIVE_ROLE_IDENTITY_OBSERVED_NOT_COMPILER_ADMISSION", "SYNTHETIC_SAME_READ_TEXT_USE_COMPLETED_NOT_COMPILER_ADMISSION",
    "SYNTHETIC_FIXED_USE_COMPLETED", "SYNTHETIC_FIXED_USE_REFUSED")
_ACTUAL = _FixedUseContract("v1-fixed-historical-five-role-entry-use", "v1-private-historical-five-role-entry-use-transport",
    "HISTORICAL_FIVE_ROLE_FIXED_USE_TRANSPORT_SETTLED_NOT_COMPILER_ADMISSION", "historical_five_role_fixed_text_use_one_shot",
    "FIVE_ROLE_ACTUAL_PURPOSE_IDENTITY_OBSERVED_NOT_COMPILER_ADMISSION", "ACTUAL_PURPOSE_SAME_READ_TEXT_USE_COMPLETED_NOT_COMPILER_ADMISSION",
    "HISTORICAL_FIVE_ROLE_FIXED_USE_COMPLETED", "HISTORICAL_FIVE_ROLE_FIXED_USE_REFUSED")


def _check_external_authority(authority, request, decision, expected, descriptor, schema):
    """Upstream request/dispatch exclude downstream authority/result hashes."""
    metadata._checked(authority, "external_authority", schema, "actual_use_authority", "external")
    shared._need(decision["kind"] == "trusted_parent_one_shot_five_role_fixed_use_declaration"
                 and decision["real_read_granted"] is True and decision["state"] == "unused"
                 and authority["kind"] == "externally_held_one_shot_five_role_fixed_use_decision"
                 and authority["granted"] is True and authority["state"] == "unused"
                 and type(authority["maximum_dispatches"]) is int and authority["maximum_dispatches"] == 1 and authority["retry_authorized"] is False,
                 "actual_use_authority", "kind")
    shared._need(all(authority[k] == decision[k] == request[k] for k in ("principal_ref", "decision_ref", "purpose"))
                 and all(authority[k] == decision[k] for k in ("issued_at", "expires_at", "request_binding_sha256", "intent_store_ref_sha256"))
                 and authority["invocation_id"] == request["invocation_id"]
                 and authority["dispatch_expected_sha256"] == metadata._hash(expected)
                 and authority["target_metadata_scope_sha256"] == request["target_metadata_scope_sha256"]
                 == expected["target_metadata_scope_sha256"] == metadata._hash(descriptor["target_metadata_scope"])
                 and metadata._hash(authority) == decision["external_authority_sha256"],
                 "actual_use_authority", "binding")

def _target_actual(root_text, path_chars):
    shared._ascii(root_text, path_chars, "actual_target")
    root = Path(root_text)
    shared._need(root.is_absolute() and root != Path(root.anchor) and ".." not in root.parts,
                 "actual_use_root", "target")
    return shared._directory_identity(root, Path(root.anchor), "declared_five_role_fixed_use_target_directory_identity_v1")


def _context(p, schemas, expected):
    return {"descriptor": p[USE], "expected": expected,
            "profiles": {k: p[k] for k in binder._DEPENDENCIES},
            "schemas": {"identity": schemas["probe"], "entry": schemas["entry"], "use": schemas["use"]}}


def _pins(d, schema, context, *, _contract=_SYNTHETIC):
    u = context["expected"]
    pins = {"policy_sha256": metadata._hash(d), "schema_sha256": metadata._hash(schema),
            "implementation_sha256": d["source_sha256"], "shared_source_sha256": d["shared_source_sha256"],
            "scope_source_sha256": d["scope_source_sha256"], "child_id": _contract.child,
            "probe_expected_sha256": metadata._hash(u["identity_expected"]),
            "probe_context_sha256": u["identity_context_sha256"],
            "use_expected_sha256": metadata._hash(u), "use_context_sha256": metadata._hash(context)}

    if _contract is _ACTUAL:
        pins["target_metadata_scope_sha256"] = metadata._hash(d["target_metadata_scope"])
    return pins


def _transport_pins(d, s, prepared, *, _contract=_SYNTHETIC):
    pins = {"policy_sha256": metadata._hash(d), "schema_sha256": metadata._hash(s),
            "implementation_sha256": d["source_sha256"], "child_source_sha256": d["child_source_sha256"],
            "lifecycle_source_sha256": d["lifecycle_source_sha256"],
            "guard_source_sha256": d["guard_source_sha256"], "observation_source_sha256": d["observation_source_sha256"],
            "dispatch_policy_sha256": metadata._hash(prepared["descriptor"]),
            "dispatch_expected_sha256": metadata._hash(prepared["expected"]),
            "dispatch_decision_sha256": metadata._hash(prepared["decision"]),
            "probe_context_sha256": prepared["expected"]["probe_context_sha256"],
            "use_context_sha256": prepared["expected"]["use_context_sha256"], "child_id": _contract.child}

    if _contract is _ACTUAL:
        pins["external_authority_sha256"] = prepared["decision"]["external_authority_sha256"]
        pins["common_source_sha256"] = d["common_source_sha256"]
        pins["child_protocol_source_sha256"] = d["child_protocol_source_sha256"]
    return pins


def _upper(n, schema, expected, key="", depth=0):
    """Same closed core grammar and five-record source cardinality as fixed use."""
    shared._need(depth < 32, "use_bound", "schema")
    if "$ref" in n:
        return _upper(schema["$defs"][n["$ref"].removeprefix("#/$defs/")], schema, expected, key, depth + 1)
    if "const" in n:
        return len(metadata._canonical(n["const"]))
    if "enum" in n:
        return max(len(metadata._canonical(v)) for v in n["enum"])
    sizes = []
    for kind in n["type"] if type(n["type"]) is list else [n["type"]]:
        if kind in ("null", "boolean", "integer"):
            sizes.append({"null": 4, "boolean": 5, "integer": 16}[kind])
        elif kind == "string":
            if n.get("pattern") == "^[a-f0-9]{64}$":
                sizes.append(66)
            elif key == "invocation_id":
                sizes.append(len(metadata._canonical(expected["entry_expected"][key])))
            else:
                shared._need(key == "relative_path", "use_bound", "schema")
                sizes.append(max(len(metadata._canonical(r[key])) for r in expected["roles"].values()))
        elif kind == "array":
            shared._need(key == "records" and len(expected["roles"]) == 5, "use_bound", "schema")
            sizes.append(6 + 5 * _upper(n["items"], schema, expected, "", depth + 1))
        else:
            shared._need(kind == "object" and n.get("additionalProperties") is False
                         and set(n["required"]) == set(n["properties"]), "use_bound", "schema")
            sizes.append(2 + max(0, len(n["required"]) - 1) + sum(len(metadata._canonical(k)) + 1
                         + _upper(v, schema, expected, k, depth + 1) for k, v in n["properties"].items()))
    return max(sizes)


def _core_bounds(prepared):
    p, ctx = prepared["profiles"], prepared["core"]
    base = reader._inner_budget(prepared)
    q, limits = ctx["expected"]["identity_expected"], p[USE]["limits"]
    E = q["roles"][p[binder.PROBE]["entry_role"]]["max_raw_bytes"]
    C, M = len(metadata._canonical(ctx)), base["inner_input_bytes"]
    state = len(metadata._canonical({"phase": "fixed_use_returned", "attempts": metadata.MAX_SAFE,
                                    "returns": metadata.MAX_SAFE, "unknown": metadata.MAX_SAFE, "hash_live": True}))
    extra = 7 * C + 4 * limits["output_bytes"] + E + state
    meta = (7 * base["probe_context_bytes"] + 4 * p[binder.PROBE]["limits"]["output_bytes"]
            + base["session_metadata_upper"] + 7 * C + 4 * limits["output_bytes"] + state)
    result = {"identity_named_with_input": base["probe_named_plus_input"], "use_context_bytes": C,
              "inner_input_bytes": M, "session_metadata_upper": base["session_metadata_upper"],
              "use_extra_named_bytes": extra, "metadata_joined_upper": meta,
              "joined_named_upper": base["probe_named_plus_input"] + extra,
              "result_upper_bytes": _upper(ctx["schemas"]["use"]["$defs"]["result"], ctx["schemas"]["use"], q),
              "max_consumer_utf8_bytes": E}
    shared._need(C <= limits["context_bytes"] and meta <= limits["metadata_bytes"]
                 and result["joined_named_upper"] <= limits["image_bytes"]
                 and result["result_upper_bytes"] <= limits["output_bytes"] and E <= limits["max_entry_bytes"],
                 "use_budget", "core_before_reserve")
    return result


def _prepare(request, decision, expected, use_expected, profiles, schemas, dispatch_profile,
             transport_profile, transport_schema, transport_expected, fixture_root, clock, check, *, _contract=_SYNTHETIC, external_authority=None):
    shared._need(_contract is _SYNTHETIC or _contract is _ACTUAL, "use_policy", "fixed_contract")
    # Three independently owned contexts: core, authority and transport. Every
    # live form is also charged in the aggregate; no split evades a context cap.
    shared._keys(profiles, (USE, *binder._DEPENDENCIES), "use_profiles")
    shared._keys(schemas, ("probe", "entry", "use"), "use_schemas")
    core = metadata._plain(_context(profiles, schemas, use_expected), 65536, "use_core_context")
    p = {USE: core["descriptor"], **core["profiles"]}
    ps = {"probe": core["schemas"]["identity"], "entry": core["schemas"]["entry"], "use": core["schemas"]["use"]}
    own = metadata._plain({"profile": transport_profile, "schema": transport_schema, "expected": transport_expected}, 65536, "use_transport_context")
    d, s, te = (own[k] for k in ("profile", "schema", "expected"))
    auth = metadata._plain({"descriptor": dispatch_profile, "request": request, "decision": decision, "expected": expected}, 65536, "use_authority_context")
    if _contract is _ACTUAL:
        auth["external_authority"] = metadata._plain(external_authority, 8192, "actual_use_authority")
    a, r, e, dd = (auth[k] for k in ("decision", "request", "expected", "descriptor"))
    metadata._schema_supported(s, "use_policy")
    for value, definition in ((d, "policy"), (dd, "dispatch_policy"), (te, "expected"), (e, "dispatch_expected"), (r, "request"), (a, "decision")):
        metadata._checked(value, definition, s, "use_shape", definition)
    for value, definition in ((p[USE], "policy"), (core["expected"], "expected")):
        metadata._checked(value, definition, ps["use"], "use_shape", definition)
    shared._keys(p, (USE, *binder._DEPENDENCIES), "use_profiles")
    shared._need(dd["scope"] == _contract.purpose and d["child_id"] == dd["child_id"] == _contract.child
                 and d["version"] == _contract.version and d["status"] == _contract.status
                 and all(x[k] is False for x in (d, dd, p[USE], p[binder.PROBE]) for k in binder._OFF), "use_policy", "policy")
    ref, L, A = p[binder.PROBE]["limits"], d["limits"], dd["limits"]
    U = p[USE]["limits"]
    shared._need(all(type(v) is int and 0 < v <= metadata.MAX_SAFE for v in U.values())
                 and all(U[k] <= ref[k] for k in ("context_bytes", "metadata_bytes", "image_bytes", "operation_ms"))
                 and U["output_bytes"] <= 2 * ref["output_bytes"]
                 and U["max_entry_bytes"] <= p[binder.PROBE]["scope_limits"]["entry_raw_bytes"]
                 and p[USE]["identity_policy_sha256"] == metadata._hash(p[binder.PROBE])
                 and p[USE]["identity_schema_sha256"] == metadata._hash(ps["probe"])
                 and p[USE]["entry_source_sha256"] == p[binder.PROBE]["source_sha256"]
                 and p[USE]["identity_source_sha256"] == p[binder.PROBE]["identity_source_sha256"], "use_policy", "core_limits_and_pins")
    scopes = dd["declared_scope_limits"]
    work = L["child_input_bytes"] + 1 + p[binder.PROBE]["scope_limits"]["delivered_bytes"] + L["child_output_bytes"] + 2 * scopes["root_chars"] + 11 * scopes["path_chars"] + 12 + 8 * 5 + 24
    shared._need(L["wrapper_steps"] == work and all(type(v) is int and 0 < v <= metadata.MAX_SAFE for v in [*L.values(), *A.values()])
                 and all(L[k] <= ref[k] for k in ("context_bytes", "metadata_bytes", "image_bytes", "operation_ms"))
                 and L["child_input_bytes"] <= ref["context_bytes"] and L["child_output_bytes"] <= 2 * ref["output_bytes"]
                 and L["result_bytes"] <= 4 * ref["output_bytes"]
                 and A["context_bytes"] <= ref["context_bytes"] and A["metadata_bytes"] <= ref["metadata_bytes"]
                 and A["operation_ms"] <= ref["operation_ms"]
                 and all(A[k] <= ref["output_bytes"] for k in ("request_bytes", "decision_bytes", "intent_bytes", "result_bytes")), "use_policy", "limits")
    shared._need(len(metadata._canonical(auth)) <= A["context_bytes"] and len(metadata._canonical(own)) <= L["context_bytes"], "use_budget", "capture")
    clock.bind(min(A["operation_ms"], L["operation_ms"], p[USE]["limits"]["operation_ms"]))
    check("use_capture")
    pins = _pins(dd, s, core, _contract=_contract)
    shared._need(e == pins and dd["schema_sha256"] == metadata._hash(s)
                 and all(r[k] == pins[k] for k in ("probe_expected_sha256", "probe_context_sha256", "use_expected_sha256", "use_context_sha256")), "use_identity", "dispatch")
    ue, pe = core["expected"], core["expected"]["identity_expected"]
    shared._need(ue["identity_context_sha256"] == metadata._hash(binder._probe_context(p, ps, pe))
                 and ue["policy_sha256"] == metadata._hash(p[USE]) and ue["schema_sha256"] == metadata._hash(ps["use"])
                 and ue["consumer_id"] == p[USE]["consumer_id"] and ue["consumer_source_sha256"] == p[USE]["source_sha256"], "use_identity", "core")
    if _contract is _ACTUAL:
        _check_external_authority(auth["external_authority"], r, a, e, dd, s)
        shared._check_decision_identity_and_time(r, a, dd, clock, check)
        metadata._schema_supported(ps["probe"], "actual_use_policy")
        metadata._checked(p[binder.PROBE], "policy", ps["probe"], "actual_use_policy", "identity_profile")
        shared._need(p[binder.PROBE]["schema_sha256"] == metadata._hash(ps["probe"]), "use_identity", "actual_identity_schema")
        capture = p["compiler_library_inputs"]
        capture_scopes = p[binder.PROBE]["scope_limits"]
        shared._need(capture_scopes["entry_raw_bytes"] <= capture["max_module_bytes"]
                     and capture_scopes["metadata_raw_bytes"] <= min(4096, capture["max_file_bytes"])
                     and capture_scopes["raw_bytes"] <= capture["max_total_bytes"]
                     and capture_scopes["delivered_bytes"] == capture_scopes["raw_bytes"] + 5,
                     "use_policy", "actual_capture_caps")
    else:
        shared._need(external_authority is None, "use_policy", "unexpected_authority")
        shared._check_decision(r, a, dd, clock, check)
    check_five_role_scope(r, pe, p, ps, dd)
    prepared = shared._prepare_reservation(auth, p, s, dd, fixture_root)
    prepared.update(core=core, own=own)
    # Aliases into the immutable held core, not second captures.
    prepared["held"] = dict(auth, schemas=ps, probe_expected=pe)
    shared._need(te == _transport_pins(d, s, prepared, _contract=_contract) and d["schema_sha256"] == metadata._hash(s)
                 and d["dispatch_policy_sha256"] == metadata._hash(dd), "use_identity", "transport")
    cb = _core_bounds(prepared)
    binding = metadata._hash({"transport_expected": te, "request_binding_sha256": prepared["intent"]["request_binding_sha256"]})
    q = {"version": _contract.child, "request": r, "expected": e, "transport_expected": te,
         "probe": {"root": pe["root_text"], "expected": ue, "profiles": p,
                   "schema_refs": {k: metadata._hash(ps[k]) for k in ("probe", "entry", "use")}},
         "request_binding_sha256": prepared["intent"]["request_binding_sha256"], "transport_binding_sha256": binding,
         "operation_ms": L["operation_ms"], "wrapper_steps": work, "limits": L, "core_bounds": cb}
    if _contract is _ACTUAL:
        q["external_authority_sha256"] = a["external_authority_sha256"]
    metadata._checked(q, "child_request", s, "use_shape", "child_request")
    wire = metadata._canonical(q)
    intent = dict(prepared["intent"], fixed_use={"binding_sha256": binding, "expected_sha256": metadata._hash(te)})
    iw = metadata._canonical(intent)
    # Both envelope variants are closed and finite; core contributes its tighter
    # schema/declared-string bound, never its entire 16KiB ceiling plus envelope.
    output = reader._closed_json_bound(s["$defs"]["node_result"], s, cb["result_upper_bytes"]) + 1
    parent_output = reader._closed_json_bound(s["$defs"]["result"], s, cb["result_upper_bytes"])
    parent = prepared["reserved"] + 2 * len(metadata._canonical(own)) + 5 * L["child_input_bytes"] + 2 * L["child_output_bytes"] + L["result_bytes"]
    wrapper = 5 * (L["child_input_bytes"] + 1) + 4 * L["child_output_bytes"] + 32 * (scopes["root_chars"] + scopes["path_chars"] + 1)
    joined = parent + wrapper + cb["joined_named_upper"]
    meta_join = parent + wrapper + cb["metadata_joined_upper"]
    shared._need(len(wire) <= L["child_input_bytes"] and len(iw) <= A["intent_bytes"]
                 and output <= L["child_output_bytes"] and parent_output <= L["result_bytes"]
                 and meta_join <= L["metadata_bytes"] and joined <= L["image_bytes"], "use_budget", "before_reserve")
    reserve = {"core": cb, "parent_named": parent, "wrapper_named": wrapper, "joined_named": joined,
               "metadata_joined": meta_join, "child_output_upper": output, "parent_output_upper": parent_output}
    return prepared, q, wire, iw, reserve


def _validate_node(node, prepared, q, s, reserve, *, _contract=_SYNTHETIC):
    node = metadata._plain(node, q["limits"]["child_output_bytes"], "use_output")
    metadata._checked(node, "node_result", s, "use_output", "child")
    require_literal_domains(node, s["$defs"]["node_result"], s)
    shared._need(len(node["payload_accounting"]["roles"]) <= 5
                 and node["version"] == _contract.child and node["wrapper_steps"] <= q["wrapper_steps"]
                 and all(node[k] == q[k] for k in ("request_binding_sha256", "transport_binding_sha256"))
                 and node["probe_context_sha256"] == q["expected"]["probe_context_sha256"], "use_identity", "child")
    if node["status"] == _contract.node_refused:
        shared._need(node["observation"] is None and type(node["error"]) is dict, "use_output", "refusal")
        return node
    shared._need(node["status"] == _contract.node_completed and node["error"] is None, "use_output", "status")
    v, ctx = node["observation"], prepared["core"]
    metadata._checked(v, "result", ctx["schemas"]["use"], "use_output", "core")
    require_literal_domains(v, ctx["schemas"]["use"]["$defs"]["result"], ctx["schemas"]["use"])
    metadata._checked(v["identity"], "result", ctx["schemas"]["identity"], "use_output", "identity")
    identity_node = dict(node, observation=v["identity"])
    validate_identity_observation(identity_node, prepared, q, reserve["core"]["identity_named_with_input"], identity_status=_contract.identity_status)
    e = ctx["expected"]
    shared._need(v["version"] == prepared["profiles"][USE]["version"]
                 and v["status"] == _contract.use_status
                 and v["invocation_id"] == e["identity_expected"]["entry_expected"]["invocation_id"]
                 and v["expected_sha256"] == metadata._hash(e)
                 and all(v[k] == e[k] for k in ("policy_sha256", "schema_sha256", "consumer_id", "consumer_source_sha256"))
                 and v["use"]["attempts"] == v["use"]["returns"] == 1
                 and v["use"]["text_sha256"] == v["identity"]["entry"]["text_sha256"]
                 and v["use"]["utf8_bytes"] == v["identity"]["entry"]["size_bytes"]
                 and v["use"]["text_code_units"] == v["identity"]["entry"]["text_code_units"]
                 and v["input_references_released"] is True and v["entry_owner_allocated"] is False
                 and not any(v["flags"].values()) and v["full_native_cost"] is v["physical_heap_rss"] is None
                 and v["resource_bounds"] == reserve["core"], "use_claim", "core")
    return node


def _run_fixed_use(request, decision, expected, use_expected, profiles, schemas,
                                 dispatch_profile, transport_profile, transport_schema, transport_expected, *, fixture_root, deadline=None, _contract=_SYNTHETIC, external_authority=None):
    c = shared._enter()
    c.update(node_attempts=0, node_returns=0, node_reaped=False, io_threads_remaining=None,
             node_outcome_received=False, node_outcome_verified=False)
    prepared = q = wire = node = result = None
    try:
        clock = metadata._Clock(_now, deadline)
        def check(stage):
            c["stage"] = stage
            shared._need(shared._ACTIVE is c and not c["poisoned"], "dispatch_reentry", stage)
            clock.check(stage)
            shared._need(shared._ACTIVE is c and not c["poisoned"], "dispatch_reentry", stage)
        prepared, q, wire, iw, reserve = _prepare(request, decision, expected, use_expected, profiles, schemas, dispatch_profile,
                                                transport_profile, transport_schema, transport_expected, fixture_root, clock, check, _contract=_contract, external_authority=external_authority)
        d, s, e = (prepared["own"][k] for k in ("profile", "schema", "expected"))
        executable = _identity_node()
        chars = prepared["descriptor"]["declared_scope_limits"]["root_chars"]
        target_gate = reader._target
        if _contract is _ACTUAL:
            target_gate = _target_actual
        target, ref = target_gate(q["probe"]["root"], chars)
        shared._need(target != prepared["directory"] and not target.is_relative_to(prepared["directory"])
                     and not prepared["directory"].is_relative_to(target), "reader_store_overlap", "target")
        def identities():
            shared._need(target_gate(str(target), chars)[1] == ref
                         and shared._directory(fixture_root, prepared["descriptor"]["intent_store_path_chars"])[1] == prepared["store_ref"], "reader_target_changed", "identity")
        check("before_reserve")
        identities()
        shared._reserve(prepared["directory"] / (prepared["decision_key"] + ".intent"), iw, c, check)
        identities()
        child = _ACTUAL_CHILD if _contract is _ACTUAL else CHILD
        with _owned_identity_pipe(executable, child, wire, c, clock, check, d["limits"]) as outcome:
            node = _validate_node(outcome, prepared, q, s, reserve, _contract=_contract)
            c["node_outcome_verified"] = True
            shared._need(node["status"] == _contract.node_completed, "use_refused", "child")
        check("publication")
        identities()
        result = {"version": _contract.version, "status": _contract.status, "expected": e, "node": node,
                  "request_binding_sha256": q["request_binding_sha256"], "transport_binding_sha256": q["transport_binding_sha256"],
                  "node_attempts": c["node_attempts"], "node_reaped": True, "io_threads_remaining": 0,
                  "target_directory_ref_sha256": ref, "intent_store_ref_sha256": prepared["store_ref"],
                  "resource_bounds": reserve, "flags": {k: False for k in d["denied_flags"]}, "full_native_cost": None, "physical_heap_rss": None}
        metadata._checked(result, "result", s, "use_output", "parent")
        shared._need(len(metadata._canonical(result)) <= d["limits"]["result_bytes"], "use_budget", "result")
        check("final")
        c["publications"] += 1
        return result
    except BaseException as error:
        shared._note_failure(error, c)
        try:
            o = dict(error.metadata_dispatch_observation)
            o["actual_payload_reads"] = node["payload_accounting"]["read_attempts"] if c["node_outcome_verified"] else None if c["node_attempts"] else 0
            core = node["observation"] if c["node_outcome_verified"] else None
            failure = node["error"]["use_failure"] if c["node_outcome_verified"] and node["error"] else None
            o["consumer_attempts"] = core["use"]["attempts"] if core else failure["consumer_attempts"] if failure else None if c["node_attempts"] else 0
            BaseException.__setattr__(error, "metadata_dispatch_observation", o)
            if node is not None:
                BaseException.__setattr__(error, "node_observation", node)
        except BaseException:
            raise error  # Keep the primary failure and the attachment context.
        raise
    finally:
        prepared = q = wire = node = result = None
        shared._finish(c)


def _use_synthetic_compiler_entry(request, decision, expected, use_expected, profiles, schemas,
                                 dispatch_profile, transport_profile, transport_schema, transport_expected, *, fixture_root, deadline=None):
    """Fixed synthetic facade; actual authority/mode/backend is not a parameter."""
    return _run_fixed_use(request, decision, expected, use_expected, profiles, schemas,
                          dispatch_profile, transport_profile, transport_schema, transport_expected,
                          fixture_root=fixture_root, deadline=deadline, _contract=_SYNTHETIC)
