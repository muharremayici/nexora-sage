"""Disabled pure comparison-metadata linkage. No acquisition, dispatch or authority.

Expected operands are held separately before the reported operation. Their
matching hashes are supplied linkage labels, never authenticated source identity.
"""
from __future__ import annotations

from re import error as RegexError
from tools.core.compiler_text_selection import (
    MAX_SAFE, TextSelectionRefusal, _plain, _canonical, _checked, _hash, _hex, _schema_supported,
)
from tools.core.compiler_entry_context_projection import (
    project_entry_context, EntryContextProjectionRefusal, COMPLETE as BASE_COMPLETE,
    PROFILE as BASE_PROFILE,
)
from tools.core.compiler_owner_trace_projection import PROFILE as TRACE_PROJECTION, TRACE, BASIS

VERSION = "v1-private-comparison-metadata-projection"
SCOPE = "supplied_comparison_metadata_only"
_INPUT_NODES, _INPUT_SCALARS = 65536, 262144


class EntryComparisonProjectionRefusal(ValueError):
    def __init__(self, code, stage="comparison_linkage"):
        super().__init__(code)
        self.code, self.stage = code, stage


def _need(condition, code):
    if not condition:
        raise EntryComparisonProjectionRefusal(code)


def _measure(value, nodes, scalars):
    """Reserve before copying/encoding; native lengths do not execute user hooks.

    Count strict UTF-8 scalar bytes without constructing an encoding. The cheap
    length check precedes the bounded scan; no whole oversize string is encoded.
    """
    budget, seen = [0, 0], set()

    def visit(v, depth):
        budget[0] += 1
        _need(depth <= 32 and budget[0] <= nodes, "comparison_copy_budget")
        kind = type(v)
        if kind is str:
            _need(len(v) <= scalars - budget[1], "comparison_copy_budget")
            for char in v:
                n = ord(char)
                _need(not 0xD800 <= n <= 0xDFFF, "comparison_native_metadata")
                budget[1] += 1 if n < 128 else 2 if n < 2048 else 3 if n < 65536 else 4
                _need(budget[1] <= scalars, "comparison_copy_budget")
        elif v is None or kind in (bool, int, float):
            budget[1] += 8
        else:
            _need(kind in (dict, list) and id(v) not in seen, "comparison_native_metadata")
            _need(len(v) <= nodes - budget[0], "comparison_copy_budget")
            seen.add(id(v))
            try:
                if kind is dict:
                    for key, item in v.items():
                        _need(type(key) is str, "comparison_native_metadata")
                        visit(key, depth + 1)
                        visit(item, depth + 1)
                else:
                    for item in v:
                        visit(item, depth + 1)
            finally:
                seen.remove(id(v))
        _need(budget[1] <= scalars, "comparison_copy_budget")

    visit(value, 0)
    return budget


def _naturals(value):
    # Schema integer/const equality alone must not accept True or float 1.0.
    if type(value) is dict:
        for item in value.values():
            _naturals(item)
    elif type(value) is list:
        for item in value:
            _naturals(item)
    elif type(value) in (int, float):
        _need(type(value) is int and 0 <= value <= MAX_SAFE, "comparison_integer")


def _nullable(value, definition, schema):
    if value is not None:
        _checked(value, "value", {"$defs": {**schema["$defs"], "value": {**definition, "type": "object"}}},
                 "comparison_shape", "comparison_linkage")


def _closed(value, definition, schema, depth=0):
    """Apply existing schema checks to nullable objects and strict constants.

    The shared kernel deliberately does not descend a union-typed object. Keep
    that kernel unchanged, and close every reached nullable metadata branch.
    """
    _need(depth <= 32, "comparison_schema_depth")
    if "$ref" in definition:
        ref = definition["$ref"]
        _need(type(ref) is str and ref.startswith("#/$defs/"), "comparison_schema_reference")
        return _closed(value, schema["$defs"][ref[len("#/$defs/"):]], schema, depth + 1)
    kind = definition.get("type")
    if type(kind) is list and "object" in kind and value is not None:
        _nullable(value, definition, schema)
    if kind == "integer" or type(kind) is list and "integer" in kind and value is not None:
        _need(type(value) is int and 0 <= value <= MAX_SAFE, "comparison_integer")
    constant = definition.get("const")
    if type(constant) is bool:
        _need(value is constant, "comparison_constant")
    elif type(constant) is int:
        _need(type(value) is int and value == constant, "comparison_constant")
    if type(value) is dict:
        for key, child in definition.get("properties", {}).items():
            if key in value:
                _closed(value[key], child, schema, depth + 1)
    elif type(value) is list and "items" in definition:
        for item in value:
            _closed(item, definition["items"], schema, depth + 1)


def _root_counts(r):
    _need(all(type(v) is int and 0 <= v <= MAX_SAFE for v in r.values())
          and r["entered"] <= 6 and r["threw"] <= 1
          and r["returned"] + r["threw"] == r["entered"], "comparison_roots")


def _counters(c, q, B, pair):
    _need(all(type(v) is int and 0 <= v <= MAX_SAFE for v in c.values())
          and c["entered"] == c["admitted"] + c["denied"] and c["admitted"] <= q["fuel"]
          and c["entered"] <= q["fuel"] + 1 and c["denied"] <= 1
          and c["reserved_payload"] == 2 * B * c["admitted"] <= min(q["payload"], pair)
          and c["buffer_requests"] == c["buffer_returns"] + c["buffer_unknown"] <= 2 * c["admitted"]
          and c["compare_requests"] == c["compare_returns"] + c["compare_unknown"] <= c["admitted"],
          "comparison_counters")


def _local(r, expected, schema, base_schema, base_policy):
    _checked(r, "outcome", schema, "comparison_shape", "comparison_linkage")
    _closed(r, schema["$defs"]["outcome"], schema)
    p = schema["$defs"]["outcome"]["properties"]
    for key in ("declared_binding", "comparison_observation"):
        _nullable(r[key], p[key], schema)
    _need(not any(r["flags"].values()) and all(v is False for v in r["flags"].values()), "comparison_flags")
    succeeded = r["outcome"] == "completed"
    _need((r["primary_stage"] is None and r["primary_kind"] is None and r["primary_code"] is None)
          if succeeded else (r["primary_stage"] is not None and r["primary_kind"] is not None), "comparison_primary")
    if r["primary_code"] is not None:
        _need(r["primary_code"].isascii() and r["primary_code"].isalnum()
              or (r["primary_code"].isascii() and all(c.isalnum() or c == "_" for c in r["primary_code"])), "comparison_primary")
        _need(not r["primary_code"].endswith("\n"), "comparison_primary")
    b = r["base_handoff"]
    _need((b is None) == (r["base_gap"] == "base_observation_unavailable"), "comparison_base_gap")
    if b is not None:
        _checked(b, "result" if b["status"] == BASE_COMPLETE else "failure", base_schema,
                 "comparison_base_shape", "comparison_linkage")
        _closed(b, base_schema["$defs"]["result" if b["status"] == BASE_COMPLETE else "failure"], base_schema)
        _need(type(b["producer_calls"]) is int and b["producer_calls"] in (0, 1), "comparison_base_calls")
        _need(not succeeded or b["status"] == BASE_COMPLETE, "comparison_outer_outcome")
        _need(b["status"] != BASE_COMPLETE or b["producer_calls"] == 1, "comparison_base_calls")
    elif succeeded:
        _need(False, "comparison_completed_base_unavailable")
    L, contract = r["logical_reservation"], schema["$defs"]["contract"]["const"]
    _need(all(type(L[k]) is int and 0 <= L[k] <= MAX_SAFE for k in
          ("added_named_reserved_bytes", "comparison_pair_reserved_bytes", "terminal_forms_used",
           "terminal_forms_reserved", "terminal_output_bound_bytes")), "comparison_integer")
    named = 3 * (contract["held_reference_model_bytes"] + contract["state_model_bytes"]
                 + 2 * contract["snapshot_bytes"] + contract["binding_bytes"]
                 + 2 * (contract["base_bytes"] + contract["output_bytes"]))
    pair = L["comparison_pair_reserved_bytes"]
    _need(named == 1674240 and 0 <= pair <= contract["pair_payload_limit_bytes"] == 4194304
          and L["added_named_reserved_bytes"] == named + pair <= contract["added_named_limit_bytes"] == 8388608
          and 1 <= L["terminal_forms_used"] <= L["terminal_forms_reserved"] == contract["terminal_forms"] == 2
          and L["terminal_output_bound_bytes"] == contract["output_bytes"] == 131072, "comparison_reservation")
    if b is not None and b["producer_calls"] == 1:
        _need(b["reservation"]["known_named_reserved_bytes"] + L["added_named_reserved_bytes"]
              <= base_policy["limits"]["named_bytes"], "comparison_join")
    s = r["comparison_observation"]
    if s is None:
        _need(r["declared_binding"] is None and r["comparison_gap"] is not None, "comparison_gap")
        return False
    sp = p["comparison_observation"]["properties"]
    for key in ("counters", "roots", "refused_dimensions", "recorded_prefix"):
        _nullable(s[key], sp[key], schema)
    _need(s["declared_binding"] == r["declared_binding"] == expected["binding"]
          and s["capture_binding"]["exact_quota_declaration"] == expected["declaration"], "comparison_binding")
    _need(s["capture_binding"]["copy_calls"] == 1 and type(s["capture_binding"]["copy_calls"]) is int
          and s["capture_binding"]["local_copy_witness_verified"] is True
          and s["capture_binding"]["same_captured_tree_references"] is True
          and s["capture_binding"]["quota_authority_verified"] is False
          and s["capture_binding"]["runtime_authority_available"] is False, "comparison_witness_metadata")
    _need(all(s[k] is False for k in ("input_origin_authenticated", "budget_authority_verified", "source_identity_verified",
          "current_owner_enforcement", "compiler_loaded", "production_enabled", "semantic_lineage", "cache_eligible",
          "publication_authority", "complete_cost_priced")), "comparison_flags")
    copy, q = s["copy_charges"], expected["declaration"]
    N, B = copy["nodes"], copy["scalar_key_charges"]
    _need(type(N) is type(B) is int and 1 <= N <= 65536 and 0 <= B <= 65536 and type(copy["ceiling"]) is int
          and copy["ceiling"] == 65536 and type(copy["starting_depth"]) is int and copy["starting_depth"] == 0
          and type(copy["depth_ceiling"]) is int and copy["depth_ceiling"] == 32, "comparison_copy")
    bound = min(q["payload"], 2 * B * q["fuel"])
    c, roots, prefix = s["counters"], s["roots"], s["recorded_prefix"]
    lost = s["metadata_loss"] is not None
    _need(s["metadata_loss"] in (None, "declared_snapshot_loss", "snapshot_unavailable")
          and (c is None) == (roots is None) == lost
          and s["final_bookkeeping_complete"] == (s["phase"] == "SEALED" and not lost), "comparison_bookkeeping")
    uncharged = (not succeeded and r["primary_stage"] == "admission" and pair == 0
                 and (c is None or not any(c.values())) and (roots is None or not any(roots.values())))
    _need(pair == bound or uncharged, "comparison_pair_reservation")
    if c is not None:
        _counters(c, q, B, pair)
    if roots is not None:
        _root_counts(roots)
        prep = b["preparation_validation"] if b is not None else None
        if prep is not None:
            _need((prep["attempted_roots"], prep["normal_returned_roots"], prep["threw_roots"])
                  == (roots["entered"], roots["returned"], roots["threw"]), "comparison_roots")
    if prefix is not None:
        _counters(prefix["counters"], q, B, pair)
        _root_counts(prefix["roots"])
        _need(s["metadata_loss"] is not None and not s["final_bookkeeping_complete"], "comparison_prefix")
    _need(not s["final_bookkeeping_complete"] or (c is not None and roots is not None and s["metadata_loss"] is None),
          "comparison_bookkeeping")
    if r["cleanup_state"] == "settled":
        _need(s["phase"] == "SEALED" and s["references_cleared"] is True, "comparison_cleanup")
    if b is not None and b["initial_copy_charges"] is not None:
        bc = b["initial_copy_charges"]
        _need((N, B) == (bc["helper_node_charges"], bc["logical_scalar_key_byte_charges"]), "comparison_copy")
    return (r["comparison_gap"] is None and r["cleanup_state"] == "settled"
            and s["final_bookkeeping_complete"] and c is not None and roots == {"entered": 6, "returned": 6, "threw": 0}
            and c["denied"] == c["buffer_unknown"] == c["compare_unknown"] == 0)


def _originals(i, a, p, s):
    """Validate held-input association even when no producer is eligible.

    This does not accept a base completion, trace, envelope or producer. Those
    decisions remain exclusively with the fixed existing projector.
    """
    _need(type(s) is dict and set(s) == {"handoff", "projection", "trace", "entry"}
          and type(p) is dict and set(p) == {BASE_PROFILE, TRACE_PROJECTION, TRACE, BASIS}, "comparison_originals")
    for schema in s.values():
        _schema_supported(schema, "comparison_originals")
    for v, definition in ((i, "intent"), (a, "anchors"), (p[BASE_PROFILE], "policy")):
        _checked(v, definition, s["handoff"], "comparison_originals", "comparison_linkage")
        _closed(v, s["handoff"]["$defs"][definition], s["handoff"])
    for v, key in ((p[TRACE], "trace"), (p[TRACE_PROJECTION], "projection")):
        _checked(v, "policy", s[key], "comparison_originals", "comparison_linkage")
    d, t, x = p[BASE_PROFILE], p[TRACE], i["expected_context"]
    _checked(x, "expected", s["trace"], "comparison_originals", "comparison_linkage")
    _checked(x["entry_expected"], "expected", s["entry"], "comparison_originals", "comparison_linkage")
    _need(i["version"] == d["intent_version"] and i["purpose"] == d["purpose"] and i["scope"] == d["scope"]
          and x["policy_sha256"] == _hash(t) and x["schema_sha256"] == _hash(s["trace"])
          and x["owner_source_sha256"] == t["owner_source_sha256"]
          and x["entry_expected"]["policy_sha256"] == t["owner_policy_sha256"]
          and x["entry_expected"]["schema_sha256"] == _hash(s["entry"]), "comparison_originals")
    required = {"intent_sha256": _hash(i), "expected_context_sha256": _hash(x), "attempt_id": i["attempt_id"], "scope": d["scope"],
        "handoff_policy_sha256": _hash(d), "handoff_schema_sha256": _hash(s["handoff"]), "handoff_source_sha256": d["source_sha256"],
        "trace_policy_sha256": _hash(t), "trace_schema_sha256": _hash(s["trace"]), "trace_source_sha256": t["source_sha256"],
        "owner_source_sha256": t["owner_source_sha256"]}
    _need(a == required and d["schema_sha256"] == required["handoff_schema_sha256"]
          and d["trace_policy_sha256"] == required["trace_policy_sha256"]
          and d["trace_schema_sha256"] == required["trace_schema_sha256"]
          and d["projection_policy_sha256"] == _hash(p[TRACE_PROJECTION])
          and d["projection_schema_sha256"] == _hash(s["projection"]), "comparison_originals")
    _need(all(v[k] is False for v in p.values() for k in
              ("default_enabled", "native_execution_enabled", "production_enabled", "semantic_lineage", "cache_eligible")),
          "comparison_originals")


def _project(intent, anchors, outcome, profiles, schemas, expected, local_schema, projection_schema):
    raw = {"intent": intent, "anchors": anchors, "outcome": outcome, "profiles": profiles, "schemas": schemas,
           "expected": expected, "local_schema": local_schema, "projection_schema": projection_schema}
    nodes, scalar = _measure(raw, _INPUT_NODES, _INPUT_SCALARS)
    # Conservative canonical upper bound: six escaped bytes/scalar byte plus
    # 32 syntax/numeric bytes/node. Reserve all forms; no alias/GC discount.
    canonical_bound = 32 * _INPUT_NODES + 6 * _INPUT_SCALARS
    data = _plain(raw, _INPUT_SCALARS, "comparison_copy")
    i, a, r, p, s, e, ls, ps = (data[k] for k in raw)
    _schema_supported(ls, "comparison_schema")
    _schema_supported(ps, "comparison_schema")
    d = ps["$defs"]["contract"]["const"]
    _need(d["version"] == VERSION and d["scope"] == SCOPE
          and d["default_enabled"] is d["runtime_admission_enabled"] is d["production_enabled"] is False,
          "comparison_contract")
    expected_limits = {"input_nodes": _INPUT_NODES, "input_scalar_bytes": _INPUT_SCALARS,
        "output_nodes": _INPUT_NODES, "output_scalar_bytes": _INPUT_SCALARS,
        "input_canonical_bytes": canonical_bound, "local_canonical_bytes": 131072,
        "output_canonical_bytes": 262144, "new_canonical_form_slots": 32, "delegate_canonical_form_slots": 64,
        "delegate_native_limit": 131072, "delegate_policy_limit": 65536, "delegate_data_limit": 65536,
        "delegate_output_bytes": 32768}
    _need(set(d) == {"version", "scope", "default_enabled", "runtime_admission_enabled", "production_enabled",
                   "local_schema_sha256", *expected_limits}, "comparison_contract")
    _need(all(type(d[k]) is int and d[k] == v for k, v in expected_limits.items()), "comparison_contract")
    _checked(e, "expected", ps, "comparison_expected", "comparison_linkage")
    _naturals(e)
    _need(all(_hex(e[k]) for k in ("local_schema_sha256", "projection_schema_sha256"))
          and _hash(ls) == d["local_schema_sha256"] == e["local_schema_sha256"]
          and _hash(ps) == e["projection_schema_sha256"], "comparison_schema_identity")
    q, bind = e["declaration"], e["binding"]
    _need(all(_hex(bind[k]) for k in ("invocation_id", "source_sha256", "tree_sha256"))
          and all(_hex(q[k]) for k in ("invocation_id", "source_sha256"))
          and q["invocation_id"] == bind["invocation_id"] and q["source_sha256"] == bind["source_sha256"]
          and q["cut"] == bind["cut"] and type(q["fuel"]) is type(q["payload"]) is int
          and q["fuel"] + 1 <= MAX_SAFE and 2 * q["fuel"] <= MAX_SAFE
          and 2 * 65536 * q["fuel"] <= MAX_SAFE, "comparison_expected")
    _need(len(_canonical(data)) <= canonical_bound, "comparison_encoding_budget")
    # Closed original inputs are still required for an unavailable/preproducer
    # observation. Do not manufacture their identity from the returned record.
    _originals(i, a, p, s)
    local_complete = False
    if r is not None:
        _need(len(_canonical(r)) <= d["local_canonical_bytes"], "comparison_local_budget")
        local_complete = _local(r, e, ls, s["handoff"], p[BASE_PROFILE])
    base = r["base_handoff"] if r is not None else None
    if base is not None:
        _need(base["version"] == p[BASE_PROFILE]["version"] and base["scope"] == p[BASE_PROFILE]["scope"]
              and (base["anchors"] is None or base["anchors"] == a)
              and (base["attempt_id"] is None or base["attempt_id"] == i["attempt_id"]), "comparison_base_originals")
    delegated, gap, calls = None, "not_attempted", 0
    if base is not None and base["producer_calls"] == 0:
        gap = "preproducer"
    elif base is not None:
        # Bound the fixed delegate's existing copy/canonical forms, including
        # its nested trace projector; retain all of their defensive copies.
        _need(p[BASE_PROFILE]["limits"]["controller_context_bytes"] <= 131072
              and p[BASE_PROFILE]["limits"]["controller_output_bytes"] <= 32768
              and p[TRACE_PROJECTION]["limits"]["metadata_bytes"] <= 65536
              and p[TRACE_PROJECTION]["limits"]["output_bytes"] <= 32768, "comparison_delegate_budget")
        calls = 1
        try:
            delegated = project_entry_context(i, a, base, p, s)
            gap = None
        except EntryContextProjectionRefusal as error:
            # Its applicability gate can precede later counter/envelope checks.
            # Until it owns a complete failed-base metadata classification,
            # conservatively refuse even that narrow gap rather than duplicate
            # its validation or hide an unchecked contradiction in a partial.
            raise EntryComparisonProjectionRefusal("comparison_base_refused") from error
    status = ("LOCAL_OBSERVATION_UNAVAILABLE" if r is None else "FAILED_LOCAL_METADATA_NOT_SUCCESS"
              if r["outcome"] == "failed" else "SUPPLIED_COMPARISON_LINKED_NOT_ATTESTED"
              if local_complete and delegated is not None else "PARTIAL_COMPARISON_METADATA_NOT_COMPLETE")
    delegate_limit = d["delegate_native_limit"]
    out = {"version": VERSION, "scope": SCOPE, "status": status, "local_observation": r,
        "local_receipt_sha256": _hash(r) if r is not None else None, "base_projection": delegated, "base_gap": gap,
        "comparison_complete": local_complete, "supplied_linkage_only": True,
        "witness_identity_authenticated": False, "original_error_identity_authenticated": False,
        "logical_budget": {"input_node_charges": nodes, "input_scalar_upper_bytes": scalar,
            "input_canonical_upper_bytes": 32 * nodes + 6 * scalar,
            "copy_scalar_reserved_bytes": 2 * _INPUT_SCALARS + delegate_limit + 2 * d["delegate_policy_limit"],
            "copy_node_slots_reserved": 2 * _INPUT_NODES + delegate_limit + 2 * d["delegate_policy_limit"],
            "canonical_forms_reserved_bytes": d["new_canonical_form_slots"] * canonical_bound
                + d["delegate_canonical_form_slots"] * (32 + 6) * delegate_limit,
            "delegate_calls": calls, "model": "source_conditioned_logical_forms_not_physical_or_complete_cost"},
        "flags": {k: False for k in ls["$defs"]["outcome"]["properties"]["flags"]["properties"]},
        "full_work_units": None, "physical_fit": None}
    _measure(out, d["output_nodes"], d["output_scalar_bytes"])
    _checked(out, "result", ps, "comparison_output", "comparison_result")
    _need(len(_canonical(out)) <= d["output_canonical_bytes"], "comparison_output_budget")
    return out


def project_entry_comparison(intent, anchors, outcome, profiles, schemas, expected, local_schema, projection_schema):
    """Link supplied metadata only; no file, producer, exception or callback input."""
    try:
        return _project(intent, anchors, outcome, profiles, schemas, expected, local_schema, projection_schema)
    except EntryComparisonProjectionRefusal:
        raise
    except TextSelectionRefusal as error:
        raise EntryComparisonProjectionRefusal("comparison_metadata_or_schema", error.stage) from error
    except (KeyError, TypeError, ValueError, AttributeError, IndexError, RecursionError, OverflowError, RegexError) as error:
        raise EntryComparisonProjectionRefusal("comparison_metadata_or_schema") from error
