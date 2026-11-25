"""Disabled one-intent/one-Node observer integration on owned temporary fixtures.

Shared dispatch preparation/reservation and the existing four-file observer own
their decisions. This facade adds no generic child, input-evaluation or real
permission surface. Supplied anchors are linkage, not authentication.
"""
from pathlib import Path
import math
import os
import shutil
import subprocess
import time
from tools.core import compiler_metadata_dispatch as dispatch
from tools.core import compiler_text_selection as metadata
from tools.core.compiler_stream_driver import _PipeExchange, _parse_line

PROFILE = "private_compiler_metadata_integration"
CHILD = Path(__file__).with_name("compiler_metadata_integration_child.cjs")
_ACTUAL_CHILD = Path(__file__).with_name("compiler_metadata_actual_child.cjs")
_ACTUAL_DISPATCH = "private_compiler_historical_metadata_dispatch"
_ACTUAL_OBSERVER = "private_compiler_historical_metadata_observer"
_now = time.monotonic


class MetadataIntegrationRefusal(ValueError):
    def __init__(self, code, stage):
        super().__init__(code)
        self.code, self.stage = code, stage


def _need(condition, code, stage):
    if not condition:
        raise MetadataIntegrationRefusal(code, stage)


def _observer_schema(schema):
    return {"$defs": {k: schema["$defs"][k] for k in ("calls", "sources", "component_refs", "metadata_result")}}


def _target_directory(value, path_chars):
    dispatch._need(type(value) is str and 0 < len(value) <= path_chars and "\0" not in value,
                   "actual_metadata_root", "target")
    root = Path(value)
    dispatch._need(root.is_absolute() and ".." not in root.parts and root != Path(root.anchor),
                   "actual_metadata_root", "target")
    return dispatch._directory_identity(root, Path(root.anchor), "declared_metadata_target_directory_identity_v1")

def _check_authority(authority, bundle, decision, expected, schema):
    """Independent held metadata is linkage, not self-authenticating permission."""
    metadata._checked(authority, "external_authority", schema, "actual_metadata_authority", "authority")
    r, a = bundle["request"], bundle["decision"]
    dispatch._need(authority["kind"] == "externally_held_one_shot_metadata_decision"
                   and authority["granted"] is True and authority["state"] == "unused"
                   and type(authority["maximum_dispatches"]) is int and authority["maximum_dispatches"] == 1
                   and authority["retry_authorized"] is False,
                   "actual_metadata_authority", "authority")
    dispatch._need(all(authority[k] == a[k] == r[k] for k in ("principal_ref", "decision_ref", "purpose"))
                   and all(authority[k] == a[k] for k in ("issued_at", "expires_at", "request_binding_sha256", "intent_store_ref_sha256"))
                   and authority["dispatch_expected_sha256"] == metadata._hash(bundle["expected"])
                   and metadata._hash(authority) == expected["external_authority_sha256"] == decision["external_authority_sha256"],
                   "actual_metadata_authority", "authority")
    dispatch._need(all(row["expectation"]["class"] == "historical_supplied" for row in r["roles"].values()),
                   "actual_metadata_historical_expectation", "authority")


def _integrate_synthetic_compiler_metadata(bundle, outer_decision, expected, profile, schema, *, intent_root, deadline=None):
    """Reserve once, run the fixed observer on temporary metadata, settle once."""
    return _integrate_metadata(bundle, outer_decision, expected, profile, schema, intent_root=intent_root, deadline=deadline)


def _integrate_metadata(bundle, outer_decision, expected, profile, schema, *, intent_root, deadline=None,
                        _actual=False, _authority=None):
    """Shared fixed execution; only the two first-party facades select their lane."""
    c = dispatch._enter()
    c.update(node_attempts=0, node_returns=0, node_reaped=False, io_threads_remaining=None, node_outcome_received=False)
    if _actual:
        c["historical_lane"] = True
    held = prepared = observation = result = wire = process = exchange = None
    error = None
    try:
        try:
            clock = metadata._Clock(_now, deadline)
            def check(stage):
                c["stage"] = stage
                _need(dispatch._ACTIVE is c and not c["poisoned"], "dispatch_reentry", stage)
                clock.check(stage)
                _need(dispatch._ACTIVE is c and not c["poisoned"], "dispatch_reentry", stage)
            _need(type(profile) is dict and type(profile.get("limits")) is dict, "integration_policy", "policy")
            cap = profile["limits"].get("metadata_bytes")
            _need(type(cap) is int and 0 < cap <= metadata.MAX_SAFE, "integration_policy", "policy")
            inputs = {"bundle": bundle, "decision": outer_decision, "expected": expected, "profile": profile, "schema": schema}
            if _actual:
                inputs["external_authority"] = _authority
            held = metadata._plain(inputs, cap, "integration_context")
            inputs = None
            b, a, e, d, schema = (held[k] for k in ("bundle", "decision", "expected", "profile", "schema"))
            metadata._schema_supported(schema, "integration_policy")
            for value, definition in ((d, "policy"), (a, "decision"), (e, "expected")):
                metadata._checked(value, definition, schema, "integration_shape", definition)
            if _actual:
                _need(d["version"] == "v1-private-historical-metadata-adapter"
                      and d["scope"] == "historical_anchor_one_intent_one_observer_v1"
                      and d["child_id"] == "v1-fixed-historical-metadata-observer"
                      and all(d[k] is False for k in ("default_enabled", "native_execution_enabled", "production_enabled", "semantic_lineage", "cache_eligible")),
                      "integration_policy", "policy")
            else:
                _need(d["version"] == "v1-private-synthetic-metadata-integration"
                  and d["scope"] == "temporary_synthetic_one_intent_one_observer_v1"
                  and d["child_id"] == "v1-fixed-temporary-metadata-observer"
                  and all(d[k] is False for k in ("default_enabled", "native_execution_enabled", "production_enabled", "semantic_lineage", "cache_eligible")),
                  "integration_policy", "policy")
            dispatch._keys(b, ("request", "decision", "expected", "profiles", "schema", "observer"), "bundle")
            observer = b["observer"]
            dispatch._keys(observer, ("root", "expected", "profiles", "schemas"), "observer")
            observer_key = _ACTUAL_OBSERVER if _actual else "private_compiler_metadata_only_observer"
            dispatch_key = _ACTUAL_DISPATCH if _actual else dispatch.PROFILE
            op = observer["profiles"][observer_key]
            limits = d["limits"]
            _need(all(type(v) is int and 0 < v <= metadata.MAX_SAFE for v in limits.values())
                  and limits["metadata_bytes"] <= op["limits"]["metadata_bytes"]
                  and limits["image_bytes"] <= b["profiles"]["private_input_image_experiment"]["limits"]["image_bytes"]
                  and limits["child_input_bytes"] <= op["limits"]["fixed_context_bytes"]
                  and limits["child_output_bytes"] <= limits["result_bytes"] <= op["limits"]["fixed_context_bytes"]
                  and limits["wrapper_steps"] <= op["limits"]["primitive_steps"]
                  and limits["operation_ms"] <= op["limits"]["operation_ms"], "integration_policy", "limits")
            clock.bind(limits["operation_ms"])
            check("capture")
            pins = {"policy_sha256": metadata._hash(d), "schema_sha256": metadata._hash(schema),
                    "implementation_sha256": d["source_sha256"], "child_source_sha256": d["child_source_sha256"],
                    "observer_context_sha256": metadata._hash(observer), "child_id": d["child_id"]}
            if _actual:
                pins["external_authority_sha256"] = metadata._hash(held["external_authority"])
            _need(e == pins and d["schema_sha256"] == pins["schema_sha256"]
                  and d["dispatch_policy_sha256"] == metadata._hash(b["profiles"][dispatch_key])
                  and d["observer_policy_sha256"] == metadata._hash(op)
                  and op == b["profiles"][observer_key]
                  and d["observer_result_schema_sha256"] == metadata._hash(_observer_schema(schema)),
                  "integration_identity", "pins")
            if _actual:
                _need(a["kind"] == "trusted_parent_metadata_integration_declaration" and a["real_read_granted"] is True
                      and a["dispatch_decision_sha256"] == metadata._hash(b["decision"])
                      and a["binding_sha256"] == metadata._hash({"bundle_sha256": metadata._hash(b), "expected": e}),
                      "integration_decision", "decision")
                _check_authority(held["external_authority"], b, a, e, schema)
            else:
                _need(a["kind"] == "synthetic_integration_not_real_permission" and a["real_read_granted"] is False
                  and a["dispatch_decision_sha256"] == metadata._hash(b["decision"])
                  and a["binding_sha256"] == metadata._hash({"bundle_sha256": metadata._hash(b), "expected": e}),
                  "integration_decision", "decision")
            prepared = dispatch._prepare_binding(b["request"], b["decision"], b["expected"], b["profiles"], b["schema"],
                                                 intent_root, c, clock, check, _actual=_actual)
            r, decision = prepared["request"], prepared["decision"]
            oe = observer["expected"]
            scope = oe["scope"]
            _need(oe["invocation_id"] == r["invocation_id"]
                  and scope["root_text"] == r["root_text"] and scope["root_scheme"] == r["root_scheme"]
                  and scope["principal_ref"] == r["principal_ref"] and scope["decision_ref"] == r["decision_ref"],
                  "integration_scope", "mapping")
            roles = prepared["profiles"]["private_compiler_owned_view_basis"]["metadata_roles"]
            dispatch._keys(scope["roles"], roles, "mapping")
            _need(scope["purpose"] == op["scope"], "integration_scope", "mapping")
            for name in ("max_open_attempts", "max_total_raw_bytes", "max_delivered_bytes", "unassigned_headroom_bytes"):
                _need(scope[name] == r[name], "integration_scope", "mapping")
            for role in roles:
                row, node = r["roles"][role], scope["roles"][role]
                _need(row["expectation"]["class"] != "unanchored"
                      and row["expectation"]["sha256"] == node["expected_sha256"]
                      and row["relative_path"] == node["relative_path"] and row["max_raw_bytes"] == node["max_raw_bytes"],
                      "integration_expectation", "mapping")
            target_check = _target_directory if _actual else dispatch._directory
            target, target_ref = target_check(observer["root"], op["scope_limits"]["root_chars"])
            _need(target != prepared["directory"] and not target.is_relative_to(prepared["directory"])
                  and not prepared["directory"].is_relative_to(target), "integration_store_overlap", "mapping")
            node = shutil.which("node")
            _need(node is not None, "integration_node_unavailable", "prepare")
            child_input = {"version": d["child_id"], "observer": observer,
                           "context_sha256": e["observer_context_sha256"], "decision_binding_sha256": a["binding_sha256"],
                           "dispatch_request_sha256": prepared["intent"]["request_binding_sha256"],
                           "operation_ms": limits["operation_ms"], "wrapper_steps": limits["wrapper_steps"]}
            # Numeric remaining duration is filled immediately before the one launch.
            intent = dict(prepared["intent"])
            intent["integration"] = {"binding_sha256": a["binding_sha256"], "expected": e, "target_store_ref_sha256": target_ref}
            wire = metadata._canonical(intent)
            parent_named = prepared["reserved"] + 2 * len(metadata._canonical(held)) + 5 * limits["child_input_bytes"] + 2 * limits["child_output_bytes"] + limits["result_bytes"]
            joined_image = parent_named + op["limits"]["metadata_bytes"]
            _need(len(wire) <= prepared["descriptor"]["limits"]["intent_bytes"]
                  and parent_named <= limits["metadata_bytes"] and joined_image <= limits["image_bytes"],
                  "integration_budget", "before_reserve")
            check("before_reserve")
            _need(dispatch._directory(intent_root, prepared["descriptor"]["intent_store_path_chars"])[1] == prepared["store_ref"]
                  and target_check(str(target), op["scope_limits"]["root_chars"])[1] == target_ref,
                  "integration_store_identity", "before_reserve")
            dispatch._reserve(prepared["directory"] / (prepared["decision_key"] + ".intent"), wire, c, check)
            check("before_node")
            _need(dispatch._directory(intent_root, prepared["descriptor"]["intent_store_path_chars"])[1] == prepared["store_ref"]
                  and target_check(str(target), op["scope_limits"]["root_chars"])[1] == target_ref,
                  "integration_store_identity", "before_node")
            remaining = math.floor((clock.end - clock.last) * 1000)
            _need(remaining > 0, "integration_deadline", "launch")
            wire = metadata._canonical(child_input)
            _need(len(wire) <= limits["child_input_bytes"], "integration_budget", "launch")
            c["node_attempts"] += 1
            child = _ACTUAL_CHILD if _actual else CHILD
            process = subprocess.Popen([node, str(child), str(limits["child_input_bytes"]), str(limits["child_output_bytes"]), str(remaining)],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0,
                                       env=dict(os.environ, NODE_OPTIONS="", NODE_PATH=""), cwd=CHILD.parents[2])
            exchange = _PipeExchange(process, limits["child_output_bytes"], clock.end, None)
            exchange.start()
            exchange.write(wire, close=True)
            observation = _parse_line(exchange)
            c["node_outcome_received"] = True
            _need(exchange.read()[0] == "eof", "integration_extra_output", "node")
            status = process.wait(timeout=max(0, clock.end - _now()))
            exchange._wait(lambda: exchange.stderr_done)
            _need(status == 0 and exchange.stderr_bytes == 0, "integration_child_error", "node")
            c["node_returns"] += 1
            observation = metadata._plain(observation, limits["child_output_bytes"], "integration_output")
            metadata._checked(observation, "node_result", schema, "integration_output", "node")
            _need(observation["context_sha256"] == e["observer_context_sha256"]
                  and observation["decision_binding_sha256"] == a["binding_sha256"]
                  and observation["dispatch_request_sha256"] == prepared["intent"]["request_binding_sha256"],
                  "integration_identity", "node")
            if observation["status"] != d["node_success_status"]:
                refusal = MetadataIntegrationRefusal("integration_observer_refused", "node")
                refusal.node_observation = observation
                raise refusal
            out = observation["observation"]
            _need(type(out) is dict, "integration_output", "node")
            metadata._checked(out, "metadata_result", schema, "integration_output", "observer_result")
            accounting = observation["payload_accounting"]
            _need(accounting["open_attempts"] == accounting["open_returns"] == accounting["close_returns"] == len(roles)
                  and accounting["close_attempts"] == len(roles) and accounting["emergency_closes"] == 0
                  and accounting["open_unknown"] == accounting["read_unknown"] == accounting["close_unknown"] == 0
                  and accounting["known_open_descriptors"] == 0
                  and observation["error"] is None and observation["wrapper_steps"] <= limits["wrapper_steps"]
                  and observation["late_module_loads"] == observation["unscoped_accesses"] == observation["backend_attempts"] == 0,
                  "integration_accounting", "node")
            _need(out["binding_sha256"] == oe["binding_sha256"] and out["installed_entry_observed"] is False
                  and out["entry_owner_allocated"] is False and out["entry_owner_disposed"] is None
                  and out["metadata_transaction_closed"] is True
                  and all(v is False for v in out["flags"].values()), "integration_claim", "node")
            records = {row["role"]: row for row in out["metadata_records"]}
            _need(set(records) == set(roles), "integration_accounting", "node")
            for role in roles:
                row = records[role]
                _need(row["raw_sha256"] == row["expected_sha256"] == scope["roles"][role]["expected_sha256"]
                      and 0 <= row["size_bytes"] <= scope["roles"][role]["max_raw_bytes"], "integration_identity", "node")
            payload_rows = {row["role"]: row for row in accounting["roles"]}
            _need(len(accounting["roles"]) == len(payload_rows) == len(roles) and set(payload_rows) == set(roles),
                  "integration_accounting", "node")
            _need(all(payload_rows[role]["closed"] is True
                      and payload_rows[role]["cap"] == scope["roles"][role]["max_raw_bytes"]
                      and payload_rows[role]["known_delivered_bytes"] == records[role]["size_bytes"] for role in roles)
                  and sum(row["read_attempts"] for row in payload_rows.values()) == accounting["read_attempts"],
                  "integration_accounting", "node")
            _need(sum(row["size_bytes"] for row in records.values()) == accounting["known_delivered_bytes"]
                  == out["read_accounting"]["known_delivered_bytes"], "integration_accounting", "node")
            check("after_node")
        except BaseException as exc:
            error = exc
            raise
        finally:
            if exchange is not None:
                try:
                    reaped, threads = exchange.cleanup()
                    c.update(node_reaped=reaped, io_threads_remaining=threads)
                except BaseException as exc:
                    if error is not None:
                        c["secondary_errors"].append(exc)
                        raise error
                    raise
            elif process is not None:
                try:
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=max(0, clock.end - _now()))
                    for stream in (process.stdin, process.stdout, process.stderr):
                        stream.close()
                    c.update(node_reaped=True, io_threads_remaining=0)
                except BaseException as exc:
                    if error is not None:
                        c["secondary_errors"].append(exc)
                        raise error
                    raise
        _need(c["node_reaped"] and c["io_threads_remaining"] == 0, "integration_cleanup", "settlement")
        check("publication")
        _need(dispatch._directory(intent_root, prepared["descriptor"]["intent_store_path_chars"])[1] == prepared["store_ref"]
              and target_check(str(target), op["scope_limits"]["root_chars"])[1] == target_ref,
              "integration_store_identity", "publication")
        result = {"version": d["version"], "status": d["status"], "binding_sha256": a["binding_sha256"],
                  "expected": e, "dispatch_request_sha256": prepared["intent"]["request_binding_sha256"],
                  "observer": observation, "expectation_classes": {role: r["roles"][role]["expectation"]["class"] for role in roles},
                  "known_parent_metadata_bytes": parent_named, "joined_named_image_bytes": joined_image,
                  "node_attempts": c["node_attempts"], "node_reaped": True, "io_threads_remaining": 0,
                  "flags": {k: False for k in d["denied_flags"]}}
        if _actual:
            result["external_authority_sha256"] = e["external_authority_sha256"]
            result["historical_expectations"] = {role: dict(r["roles"][role]["expectation"]) for role in roles}
        metadata._checked(result, "result", schema, "integration_output", "result")
        _need(len(metadata._canonical(result)) <= limits["result_bytes"], "integration_budget", "result")
        check("final")
        _need(dispatch._directory(intent_root, prepared["descriptor"]["intent_store_path_chars"])[1] == prepared["store_ref"]
              and target_check(str(target), op["scope_limits"]["root_chars"])[1] == target_ref,
              "integration_store_identity", "final")
        c["publications"] += 1
        return result
    except BaseException as exc:
        dispatch._note_failure(exc, c)
        try:
            BaseException.__setattr__(exc, "metadata_integration_observation",
                {k: v for k, v in c.items() if k != "secondary_errors"})
            if observation is not None:
                BaseException.__setattr__(exc, "node_observation", observation)
        except BaseException:
            raise exc  # Preserve primary failure and secondary attachment context.
        raise
    finally:
        held = prepared = observation = result = wire = process = exchange = None
        dispatch._finish(c)
