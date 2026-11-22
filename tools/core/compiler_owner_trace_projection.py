"""Disabled pure projection of supplied owner-trace metadata, never a settled ledger.

Independent supplied anchors establish linkage only. They do not authenticate the
producer, its original context, actual source bytes, live storage or authority.
"""
from __future__ import annotations
from re import error as RegexError

from tools.core.compiler_text_selection import (
    MAX_SAFE, TextSelectionRefusal, _canonical, _checked, _hash, _hex,
    _plain, _schema_supported,
)

PROFILE = "private_compiler_owner_trace_projection"
TRACE = "private_compiler_entry_lifetime_trace"
BASIS = "private_compiler_entry_lifetime_ledger"
VERSION = "v1-private-partial-owner-trace-projection"
COMPLETED = "SUPPLIED_OWNER_TRACE_PARTIALLY_PROJECTED_NOT_TRANSACTION_PROOF"
FAILED = "FAILED_PRODUCER_OWNER_TRACE_PARTIALLY_PROJECTED_NOT_SUCCESS"
_OFF = ("default_enabled", "native_execution_enabled", "production_enabled", "semantic_lineage", "cache_eligible")
_DENIED = ("actual_ownership_attested", "input_origin_authenticated", "source_freshness_verified",
           "read_authority_granted", "compiler_loaded", "input_code_evaluated", "complete_cost_priced",
           "physical_fit_verified", "replacement_factor_approved", "production_enabled", "semantic_lineage",
           "cache_eligible", "publication_authority")
_MISSING = ("metadata_role_lifetimes", "compiled_storage", "declaration_and_image_lifetimes",
            "parent_transport_settlement", "full_work_cost", "physical_memory", "input_origin_authentication")


class OwnerTraceProjectionRefusal(ValueError):
    def __init__(self, code, stage):
        super().__init__(code)
        self.code, self.stage = code, stage


def _need(condition, code="projection_invalid", stage="input"):
    if not condition:
        raise OwnerTraceProjectionRefusal(code, stage)


def _keys(value, names, stage="input"):
    _need(type(value) is dict and set(value) == set(names), "projection_shape", stage)


def _nat(value):
    return type(value) is int and 0 <= value <= MAX_SAFE


def _context(profiles, schemas):
    # The initial fixed metadata ceiling also bounds malformed policy/schema input.
    context = _plain({"profiles": profiles, "schemas": schemas}, 65536, "policy")
    profiles, schemas = context["profiles"], context["schemas"]
    _keys(profiles, (PROFILE, TRACE, BASIS), "policy")
    _keys(schemas, ("projection", "trace", "entry"), "policy")
    d, t, b = (profiles[k] for k in (PROFILE, TRACE, BASIS))
    s, ts, es = (schemas[k] for k in ("projection", "trace", "entry"))
    for schema in schemas.values():
        _schema_supported(schema, "policy")
    _checked(d, "policy", s, "projection_policy", "policy")
    _checked(t, "policy", ts, "projection_policy", "policy")
    _need(d["version"] == VERSION and d["result_status"] == COMPLETED and d["failure_status"] == FAILED,
          "projection_policy", "policy")
    _need(d["denied_flags"] == list(_DENIED) and d["unobserved_domains"] == list(_MISSING)
          and t["version"] == "v1-private-synthetic-entry-owner-trace"
          and t["scope"] == "temporary_synthetic_entry_owner_lifetime_only"
          and type(t["limits"]["role_bytes"]) is int and 0 < t["limits"]["role_bytes"] <= 65536,
          "projection_policy", "policy")
    _need(all(p[k] is False for p in (d, t, b) for k in _OFF), "projection_policy", "policy")
    _need(d["schema_sha256"] == _hash(s) and d["trace_policy_sha256"] == _hash(t)
          and d["basis_policy_sha256"] == _hash(b) and d["trace_schema_sha256"] == t["schema_sha256"] == _hash(ts)
          and d["owner_schema_sha256"] == t["owner_schema_sha256"] == _hash(es)
          and d["trace_source_sha256"] == t["source_sha256"]
          and d["owner_source_sha256"] == t["owner_source_sha256"], "projection_policy", "policy")
    _need(b["basis_multipliers"] == {"raw_bytes": 1, "utf16_code_units": 2}
          and all(type(n) is int for n in b["basis_multipliers"].values()), "projection_policy", "policy")
    limits = d["limits"]
    _keys(limits, ("metadata_bytes", "events", "output_bytes", "logical_component_bytes"), "policy")
    _need(all(_nat(n) and n > 0 for n in limits.values())
          and limits["metadata_bytes"] <= 65536 and limits["output_bytes"] <= 16384
          and limits["events"] <= t["limits"]["events"] <= 128
          and limits["logical_component_bytes"] <= b["limits"]["named_bytes"], "projection_policy", "policy")
    _need(len(_canonical(context)) <= limits["metadata_bytes"], "projection_budget", "policy")
    return d, t, b, s, ts, es


def _events(events, t, limit):
    """Check a valid source-shaped prefix; do not synthesize missing transitions."""
    _need(type(events) is list and len(events) <= limit, "projection_budget", "events")
    state = "start"
    allowed = {
        "transaction_begin": {"start"}, "open_attempt": {"begun"}, "open_return": {"opening"},
        "open_unknown": {"opening"}, "allocation_request": {"opened"}, "backing_ready": {"allocating"},
        "read_attempt": {"ready"}, "read_return": {"reading"}, "read_unknown": {"reading"},
        "close_attempt": {"opened", "allocating", "ready", "reading", "read_unknown", "eof"},
        "close_return": {"closing"}, "close_unknown": {"closing"}, "view_handoff": {"closed"},
        "text_ready": {"view"}, "owner_publish": {"text"}, "borrow_begin": {"owned"},
        "text_use": {"borrowing"}, "owner_release": {"text", "owned", "borrowing", "used"},
        "owner_abort": {"text", "owned", "borrowing", "used"}, "owner_scope_complete": {"released"},
    }
    next_state = dict(zip(
        ("transaction_begin", "open_attempt", "open_return", "open_unknown", "allocation_request", "backing_ready",
         "read_attempt", "read_unknown", "close_attempt", "close_return", "close_unknown", "view_handoff", "text_ready",
         "owner_publish", "borrow_begin", "text_use", "owner_release", "owner_abort", "owner_scope_complete"),
        ("begun", "opening", "opened", "open_unknown", "allocating", "ready", "reading", "read_unknown", "closing",
         "closed", "close_unknown", "view", "text", "owned", "borrowing", "used", "released", "aborted", "complete")))
    dimensions = dict.fromkeys(("planned_payload_bytes", "requested_backing_bytes", "ready_backing_bytes",
                                "view_payload_bytes", "decoded_utf16_units"))
    known = attempts = returns = 0
    eof = unknown_read = False
    kinds = {}
    relations = []
    accepted = None
    for i, e in enumerate(events):
        _keys(e, ("sequence", "kind", "role", "first", "second"), "events")
        k, a, z = e["kind"], e["first"], e["second"]
        _need(type(e["sequence"]) is int and e["sequence"] == i and k in allowed and state in allowed[k],
              "projection_order", "events")
        _need(e["role"] == (None if k in ("transaction_begin", "owner_scope_complete") else "entry"),
              "projection_role", "events")
        _need(all(n is None or _nat(n) for n in (a, z)), "projection_units", "events")
        if k in ("allocation_request", "backing_ready"):
            _need(_nat(a) and _nat(z) and a == z + 1 and z <= t["limits"]["role_bytes"], "projection_dimensions", "events")
            if k == "allocation_request":
                dimensions.update(planned_payload_bytes=z, requested_backing_bytes=a)
            else:
                _need(a == dimensions["requested_backing_bytes"] and z == dimensions["planned_payload_bytes"],
                      "projection_dimensions", "events")
                dimensions["ready_backing_bytes"] = a
        elif k in ("text_ready", "borrow_begin", "text_use"):
            _need(_nat(a) and _nat(z) and a == dimensions["planned_payload_bytes"] and z <= a <= 3 * z,
                  "projection_dimensions", "events")
            if k == "text_ready":
                dimensions["decoded_utf16_units"] = z
            else:
                _need(z == dimensions["decoded_utf16_units"], "projection_dimensions", "events")
                relations.append({"event_sequence": i, "relation": "same_text_borrow" if k == "borrow_begin" else "same_text_use"})
        elif k == "view_handoff":
            _need(_nat(a) and z is None and eof and not unknown_read and known == a == dimensions["planned_payload_bytes"],
                  "projection_dimensions", "events")
            dimensions["view_payload_bytes"] = a
            relations.append({"event_sequence": i, "relation": "view_aliases_entry_backing"})
        elif k == "read_return":
            _need(z is None, "projection_units", "events")
            returns += 1
            if a is None:
                unknown_read = True
            else:
                known += a
                _need(known <= dimensions["ready_backing_bytes"], "projection_dimensions", "events")
                eof = a == 0
        elif k == "owner_publish":
            _need(_nat(a) and z is None and known <= a <= 5 * t["limits"]["role_bytes"], "projection_units", "events")
            accepted = a
        elif k in ("owner_release", "owner_abort"):
            _need(type(a) is int and type(z) is int and a == z == 0, "projection_units", "events")
        else:
            _need(a is z is None, "projection_units", "events")
        if k == "read_attempt":
            attempts += 1
        if k == "read_unknown":
            unknown_read = True
        if k == "owner_scope_complete":
            _need(all(name in kinds for name in ("owner_publish", "borrow_begin", "text_use")), "projection_order", "events")
        kinds[k] = i
        if k == "read_return":
            state = "read_unknown" if a is None else "eof" if a == 0 else "ready"
        else:
            state = next_state[k]
    return dimensions, kinds, relations, known, attempts, returns, eof, unknown_read, accepted, state


def _project(envelope, expected_context, anchors, profiles, schemas):
    d, t, b, s, ts, es = _context(profiles, schemas)
    data = _plain({"envelope": envelope, "expected": expected_context, "anchors": anchors}, d["limits"]["metadata_bytes"], "input")
    e, x, anchors = (data[k] for k in ("envelope", "expected", "anchors"))
    _keys(e, ("evidence_ref_sha256", "expected_context_sha256", "outcome", "trace", "error"))
    _checked(e, "envelope", s, "projection_shape", "input")
    _need(type(x) is dict, "projection_original_context_required", "linkage")
    _checked(x, "expected", ts, "projection_context", "linkage")
    _keys(x, ("policy_sha256", "schema_sha256", "owner_source_sha256", "entry_expected"), "linkage")
    _checked(x["entry_expected"], "expected", es, "projection_context", "linkage")
    ex = x["entry_expected"]
    _need(x["policy_sha256"] == _hash(t) and x["schema_sha256"] == _hash(ts)
          and x["owner_source_sha256"] == t["owner_source_sha256"]
          and ex["policy_sha256"] == t["owner_policy_sha256"] and ex["schema_sha256"] == _hash(es)
          and ex["compiler_version"] == t["synthetic_version"]
          and _nat(ex["entry_bytes"]) and ex["entry_bytes"] <= t["limits"]["role_bytes"], "projection_context", "linkage")
    required = {
        "receipt_sha256": _hash(e), "expected_context_sha256": _hash(x), "evidence_ref_sha256": e["evidence_ref_sha256"],
        "projection_policy_sha256": _hash(d), "projection_schema_sha256": _hash(s),
        "trace_policy_sha256": _hash(t), "trace_schema_sha256": _hash(ts), "trace_source_sha256": t["source_sha256"],
        "owner_source_sha256": t["owner_source_sha256"], "scope": t["scope"],
    }
    _keys(anchors, required, "linkage")
    _need(anchors == required and _hex(e["evidence_ref_sha256"]) and e["expected_context_sha256"] == _hash(x),
          "projection_anchor_mismatch", "linkage")
    r = e["trace"]
    completed = e["outcome"] == "completed"
    _need(completed or e["outcome"] == "failed", "projection_outcome")
    _checked(r, "result" if completed else "failure", ts if completed else s, "projection_trace", "trace")
    # Explicit booleans and nulls also hold if a supplied schema was weakened.
    for key in ("recording_incomplete", "owner_released", "owner_scope_complete"):
        _need(type(r[key]) is bool, "projection_trace", "trace")
    _need(r["transaction_settled"] is False and r["physical_fit"] is None, "projection_trace", "trace")
    _need(_nat(r["role_opens"]) and _nat(r["role_closes"]) and r["role_closes"] <= r["role_opens"] <= 5,
          "projection_trace", "trace")
    if completed:
        _need(e["error"] is None and r["status"] == t["result_status"] and r["version"] == t["version"]
              and r["scope"] == t["scope"] and r["expected_sha256"] == _hash(x)
              and r["entry_sha256"] == ex["entry_sha256"] and r["text_sha256"] == ex["text_sha256"]
              and r["compiled_retention_bytes"] is None and all(r["flags"][k] is False for k in t["denied_flags"]),
              "projection_trace", "trace")
        _need(_nat(r["metadata_reserved_bytes"]) and r["metadata_reserved_bytes"] <= t["limits"]["metadata_bytes"]
              and _nat(r["known_named_inputs_reserved_bytes"]) and r["known_named_inputs_reserved_bytes"] <= t["limits"]["image_bytes"],
              "projection_trace", "trace")
    else:
        _need(r["status"] == "INCOMPLETE_SYNTHETIC_OWNER_TRACE_NOT_SUCCESS" and r["owner_scope_complete"] is False
              and type(r["close_unknown"]) is bool and r["compiler_loaded"] is False, "projection_trace", "trace")
        _keys(e["error"], ("code", "original_identity_retained"))
        _checked(e["error"], "error", s, "projection_trace", "trace")
        _need(type(e["error"]["original_identity_retained"]) is bool, "projection_trace", "trace")
    dims, kinds, relations, known, attempts, returns, eof, unknown, accepted, state = _events(r["events"], t, d["limits"]["events"])
    _need(r["role_opens"] >= int("open_return" in kinds) and r["role_closes"] >= int("close_return" in kinds),
          "projection_trace", "trace")
    lost = r["recording_incomplete"]
    if not lost:
        _need(r["owner_released"] == (state in ("released", "aborted", "complete")), "projection_trace", "trace")
        if not completed:
            _need("close_unknown" not in kinds or r["close_unknown"] is True, "projection_trace", "trace")
    if completed:
        _need(state == "complete" and not lost and not unknown and r["owner_released"] is True
              and r["owner_scope_complete"] is True and r["role_opens"] == r["role_closes"] == 5
              and type(r["delivered_entry_bytes"]) is int and r["delivered_entry_bytes"] == known == ex["entry_bytes"]
              and dims["planned_payload_bytes"] == ex["entry_bytes"], "projection_incomplete_success", "trace")
    amount = None
    if dims["ready_backing_bytes"] is not None and dims["decoded_utf16_units"] is not None:
        amount = dims["ready_backing_bytes"] * b["basis_multipliers"]["raw_bytes"] + dims["decoded_utf16_units"] * b["basis_multipliers"]["utf16_code_units"]
        _need(_nat(amount) and amount <= d["limits"]["logical_component_bytes"], "projection_budget", "result")
    delivery_complete = eof and not unknown and not lost and known == dims["planned_payload_bytes"]
    result = {
        "version": VERSION, "status": COMPLETED if completed else FAILED, "producer_outcome": e["outcome"],
        "producer_error": e["error"], "anchors": required, "linkage_basis": "supplied_independent_anchors_not_authenticated",
        "dimensions": dims, "conditional_component_bytes": amount,
        "component_basis": "ready_entry_backing_plus_two_per_decoded_utf16_unit_not_peak",
        "relations": relations, "recorded_events": len(r["events"]), "unpriced_event_sequences": list(range(len(r["events"]))),
        "entry_read_attempts": attempts, "entry_read_returns": returns, "known_returned_prefix_bytes": known,
        "complete_delivered_entry_bytes": known if delivery_complete else None,
        "accepted_all_role_bytes": accepted, "recording_incomplete": lost,
        "entry_close_observation": "unknown" if "close_unknown" in kinds else "returned" if "close_return" in kinds else "unobserved",
        "aggregate_close_unknown": False if completed else r["close_unknown"], "role_opens": r["role_opens"], "role_closes": r["role_closes"],
        "owner_terminal_observation": "aborted" if "owner_abort" in kinds else "released" if "owner_release" in kinds else "unobserved",
        "owner_scope_complete_event_observed": "owner_scope_complete" in kinds,
        "producer_owner_scope_complete": r["owner_scope_complete"], "reported_owner_released": r["owner_released"],
        "expected_payload_size_matches_observation": None if dims["planned_payload_bytes"] is None else dims["planned_payload_bytes"] == ex["entry_bytes"],
        "metadata_reserved_bytes": r["metadata_reserved_bytes"] if completed else None,
        "known_named_inputs_reserved_bytes": r["known_named_inputs_reserved_bytes"] if completed else None,
        "reservation_basis": "producer_preflight_reservations_not_measured_live_peaks",
        "unobserved_domains": d["unobserved_domains"], "full_work_units": None, "compiled_retention_bytes": None,
        "physical_fit": None, "transaction_settled": False, "input_origin_attested": False,
        "reservation_change_eligible": False, "flags": {k: False for k in d["denied_flags"]},
    }
    _checked(result, "result", s, "projection_result", "result")
    _need(len(_canonical(result)) <= d["limits"]["output_bytes"], "projection_budget", "result")
    return result


def project_owner_trace(envelope, expected_context, anchors, profiles, schemas):
    """Accept supplied data only; return detached partial metadata or bounded refusal."""
    try:
        return _project(envelope, expected_context, anchors, profiles, schemas)
    except OwnerTraceProjectionRefusal:
        raise
    except TextSelectionRefusal as error:
        raise OwnerTraceProjectionRefusal("projection_metadata_or_schema", error.stage) from error
    except (KeyError, TypeError, ValueError, IndexError, OverflowError, RecursionError, AttributeError, RegexError) as error:
        raise OwnerTraceProjectionRefusal("projection_structure", "input") from error
