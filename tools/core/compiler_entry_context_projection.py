"""Pure supplied original-context/handoff linkage; delegates the partial projector."""
from __future__ import annotations
from re import error as RegexError
from tools.core.compiler_text_selection import _plain, _canonical, _checked, _hash, _schema_supported, TextSelectionRefusal
from tools.core.compiler_owner_trace_projection import (
    PROFILE as PROJECTION, TRACE, BASIS, project_owner_trace, OwnerTraceProjectionRefusal,
)

PROFILE = "private_compiler_entry_context_handoff"
VERSION = "v1-private-synthetic-entry-context-handoff"
COMPLETE = "SYNTHETIC_CONTEXT_TRACE_HANDOFF_COMPLETED_NOT_TRANSACTION_PROOF"
FAILED = "INCOMPLETE_SYNTHETIC_CONTEXT_TRACE_HANDOFF_NOT_SUCCESS"


class EntryContextProjectionRefusal(ValueError):
    def __init__(self, code, stage="linkage"):
        super().__init__(code)
        self.code, self.stage = code, stage


def _need(condition, code):
    if not condition:
        raise EntryContextProjectionRefusal(code)


def _project(intent, anchors, completion, profiles, schemas):
    data = _plain({"intent": intent, "anchors": anchors, "completion": completion,
                   "profiles": profiles, "schemas": schemas}, 131072, "context_linkage")
    i, a, r, p, s = (data[k] for k in ("intent", "anchors", "completion", "profiles", "schemas"))
    _need(type(p) is dict and set(p) == {PROFILE, PROJECTION, TRACE, BASIS}, "context_profiles")
    _need(type(s) is dict and set(s) == {"handoff", "projection", "trace", "entry"}, "context_schemas")
    for schema in s.values():
        _schema_supported(schema, "context_policy")
    d = p[PROFILE]
    _checked(d, "policy", s["handoff"], "context_policy", "context_policy")
    _need(d["preparation_validation_observation"] == {
        "version": "v1-private-preparation-validation-roots", "scope": "six_existing_preproducer_checked_roots_only",
        "unit": "validation_root_calls", "site_count": 6, "state_bytes": 512, "snapshot_bytes": 1024}, "context_policy")
    _need(d["version"] == VERSION and d["result_status"] == COMPLETE and d["failure_status"] == FAILED
          and all(d[k] is False for k in ("default_enabled", "native_execution_enabled", "production_enabled", "semantic_lineage", "cache_eligible")), "context_policy")
    _need(d["schema_sha256"] == _hash(s["handoff"]) and d["trace_policy_sha256"] == _hash(p[TRACE])
          and d["projection_policy_sha256"] == _hash(p[PROJECTION])
          and d["projection_schema_sha256"] == _hash(s["projection"])
          and d["projection_source_sha256"] == p[PROJECTION]["source_sha256"]
          and d["trace_source_sha256"] == p[TRACE]["source_sha256"]
          and d["trace_schema_sha256"] == _hash(s["trace"])
          and d["owner_schema_sha256"] == _hash(s["entry"])
          and d["owner_source_sha256"] == p[TRACE]["owner_source_sha256"], "context_policy")
    limits = d["limits"]
    _need(all(type(v) is int and v > 0 for v in limits.values())
          and limits["controller_context_bytes"] <= 131072 and limits["controller_output_bytes"] <= 32768
          and len(_canonical(data)) <= limits["controller_context_bytes"], "context_budget")
    _checked(i, "intent", s["handoff"], "context_intent", "context_linkage")
    _checked(a, "anchors", s["handoff"], "context_anchors", "context_linkage")
    x = i["expected_context"]
    required = {"intent_sha256": _hash(i), "expected_context_sha256": _hash(x), "attempt_id": i["attempt_id"], "scope": d["scope"],
                "handoff_policy_sha256": _hash(d), "handoff_schema_sha256": _hash(s["handoff"]), "handoff_source_sha256": d["source_sha256"],
                "trace_policy_sha256": _hash(p[TRACE]), "trace_schema_sha256": _hash(s["trace"]),
                "trace_source_sha256": p[TRACE]["source_sha256"], "owner_source_sha256": p[TRACE]["owner_source_sha256"]}
    _need(a == required and i["version"] == d["intent_version"] and i["purpose"] == d["purpose"] and i["scope"] == d["scope"], "context_anchor_mismatch")
    _need(type(r) is dict, "context_completion_unavailable")
    succeeded = r["status"] == COMPLETE
    _checked(r, "result" if succeeded else "failure", s["handoff"], "context_completion", "context_linkage")
    _need(r["version"] == VERSION and r["status"] in (COMPLETE, FAILED) and r["anchors"] == a
          and r["scope"] == d["scope"] and r["attempt_id"] == i["attempt_id"]
          and type(r["producer_calls"]) is int and r["producer_calls"] == 1
          and r["references_cleared"] is True and r["new_deadline"] is False
          and r["transaction_settled"] is False and r["physical_fit"] is None, "context_completion")
    _need(succeeded or (r["producer_outcome"] == "failed" and r["metadata_gap"] is None), "context_completion_unavailable")
    e = r["envelope"]
    _need(type(e) is dict and r["envelope_sha256"] == _hash(e)
          and e["evidence_ref_sha256"] == a["intent_sha256"]
          and e["expected_context_sha256"] == a["expected_context_sha256"]
          and e["outcome"] == r["producer_outcome"] == ("completed" if succeeded else "failed"), "context_envelope")
    if succeeded:
        _need(all(r["flags"][k] is False for k in d["denied_flags"]) and r["input_origin_attested"] is False
              and r["full_work_units"] is None, "context_completion")
    else:
        _need(r["original_identity_retained"] is True and r["primary_code"] == e["error"]["code"]
              and e["error"]["original_identity_retained"] is True, "context_completion")
    copy_charges = r["initial_copy_charges"]
    _need(r["root_copy_outcome"] == "returned", "context_copy_observation")
    if copy_charges is None:
        _need(r["copy_observation_gap"] == "snapshot_unavailable", "context_copy_observation")
    else:
        _checked(copy_charges, "initial_copy_charges", s["handoff"], "context_copy_observation", "context_linkage")
        _need(r["copy_observation_gap"] is None
              and copy_charges["version"] == d["initial_copy_observation"]["version"]
              and type(copy_charges["helper_node_charges"]) is int
              and type(copy_charges["logical_scalar_key_byte_charges"]) is int
              and 1 <= copy_charges["helper_node_charges"] <= copy_charges["ceiling"] == 65536
              and 0 <= copy_charges["logical_scalar_key_byte_charges"] <= copy_charges["ceiling"], "context_copy_observation")
    preparation = r["preparation_validation"]
    if preparation is None:
        _need(r["preparation_validation_gap"] == "snapshot_unavailable", "context_preparation_validation")
    else:
        _checked(preparation, "preparation_validation", s["handoff"], "context_preparation_validation", "context_linkage")
        _need(set(preparation) == {"version", "scope", "unit", "site_count", "attempted_roots", "normal_returned_roots",
              "threw_roots", "measurement_sealed", "coverage_complete", "recursive_validation_entries", "equality_attempts",
              "canonical_operand_utf8_bytes", "complete_cost_priced", "full_work_units", "physical_fit"}
              and preparation["version"] == "v1-private-preparation-validation-roots"
              and preparation["scope"] == "six_existing_preproducer_checked_roots_only"
              and preparation["unit"] == "validation_root_calls" and type(preparation["site_count"]) is int
              and preparation["site_count"] == 6 and r["preparation_validation_gap"] is None,
              "context_preparation_validation")
        _need(all(type(preparation[k]) is int for k in ("attempted_roots", "normal_returned_roots", "threw_roots"))
              and 0 <= preparation["normal_returned_roots"] <= preparation["attempted_roots"] <= 6
              and 0 <= preparation["threw_roots"] <= 1
              and preparation["normal_returned_roots"] + preparation["threw_roots"] == preparation["attempted_roots"]
              and (preparation["attempted_roots"], preparation["normal_returned_roots"], preparation["threw_roots"]) == (6, 6, 0)
              and preparation["measurement_sealed"] is True and preparation["coverage_complete"] is False
              and preparation["complete_cost_priced"] is False
              and all(preparation[k] is None for k in ("recursive_validation_entries", "equality_attempts",
                      "canonical_operand_utf8_bytes", "full_work_units", "physical_fit")), "context_preparation_validation")
    work = r["direct_metadata_work"]
    _checked(work, "direct_metadata_work", s["handoff"], "context_metadata_work", "context_linkage")
    counters = ("canonical_attempts", "canonical_returns", "canonical_unknown_attempts", "canonical_returned_utf8_bytes",
                "hash_requests", "hash_attempts", "hash_returns", "hash_unknown_attempts", "hash_input_requested_utf8_bytes",
                "hash_input_admitted_utf8_bytes", "hash_input_returned_utf8_bytes", "modeled_work_units", "attempted_work_units",
                "limit_units", "terminal_reserved_units", "terminal_publication_bytes", "terminal_failure_bytes")
    _need(all(type(work[k]) is int and work[k] >= 0 for k in counters)
          and work["version"] == d["metadata_work"]["version"] and work["scope"] == d["metadata_work"]["scope"]
          and work["limit_units"] == d["metadata_work"]["limit_units"] and work["measurement_sealed"] is True
          and work["terminal_work_measured"] is False and work["coverage_complete"] is False
          and work["complete_cost_priced"] is False and work["full_work_units"] is work["physical_fit"] is None,
          "context_metadata_work")
    _need(work["canonical_returns"] + work["canonical_unknown_attempts"] == work["canonical_attempts"]
          and work["hash_returns"] + work["hash_unknown_attempts"] == work["hash_attempts"] <= work["hash_requests"]
          and work["hash_input_returned_utf8_bytes"] <= work["hash_input_admitted_utf8_bytes"] <= work["hash_input_requested_utf8_bytes"]
          and work["modeled_work_units"] == 2 * work["canonical_returned_utf8_bytes"] + work["hash_input_admitted_utf8_bytes"]
          and work["attempted_work_units"] == 2 * work["canonical_returned_utf8_bytes"] + work["hash_input_requested_utf8_bytes"]
          and work["terminal_publication_bytes"] == limits["output_bytes"]
          and work["terminal_failure_bytes"] == d["metadata_work"]["terminal_failure_bytes"]
          and work["terminal_reserved_units"] == 2 * (limits["output_bytes"] + work["terminal_failure_bytes"])
          and type(work["pre_producer_units"]) is int and 0 <= work["pre_producer_units"] <= work["modeled_work_units"],
          "context_metadata_work")
    if succeeded:
        _need(work["budget_exhausted"] is False and work["unknown_direct_work"] is False
              and work["attempted_work_units"] + work["terminal_reserved_units"] <= work["limit_units"], "context_metadata_work")
    projection_anchors = {"receipt_sha256": r["envelope_sha256"], "expected_context_sha256": a["expected_context_sha256"],
        "evidence_ref_sha256": a["intent_sha256"], "projection_policy_sha256": _hash(p[PROJECTION]),
        "projection_schema_sha256": _hash(s["projection"]), "trace_policy_sha256": a["trace_policy_sha256"],
        "trace_schema_sha256": a["trace_schema_sha256"], "trace_source_sha256": a["trace_source_sha256"],
        "owner_source_sha256": a["owner_source_sha256"], "scope": a["scope"]}
    projected = project_owner_trace(e, x, projection_anchors, {k: p[k] for k in (PROJECTION, TRACE, BASIS)},
                                    {k: s[k] for k in ("projection", "trace", "entry")})
    out = {"version": VERSION, "status": "SUPPLIED_CONTEXT_HANDOFF_LINKED_NOT_ORIGIN_ATTESTATION" if succeeded else "FAILED_CONTEXT_HANDOFF_LINKED_NOT_SUCCESS",
           "intent_sha256": a["intent_sha256"], "handoff_receipt_sha256": _hash(r), "producer_outcome": e["outcome"],
           "projection": projected, "supplied_linkage_only": True, "input_origin_attested": False,
           "transaction_settled": False, "full_work_units": None, "physical_fit": None,
           "reservation_change_eligible": False, "flags": {k: False for k in d["denied_flags"]},
           "direct_metadata_work": work, "root_copy_outcome": r["root_copy_outcome"],
           "initial_copy_charges": copy_charges, "copy_observation_gap": r["copy_observation_gap"],
           "preparation_validation": preparation, "preparation_validation_gap": r["preparation_validation_gap"]}
    _checked(out, "projection_result", s["handoff"], "context_projection_result", "context_result")
    _need(len(_canonical(out)) <= limits["controller_output_bytes"], "context_budget")
    return out


def project_entry_context(intent, anchors, completion, profiles, schemas):
    """Check a separately held supplied pair; never invoke a producer or replay it."""
    try:
        return _project(intent, anchors, completion, profiles, schemas)
    except EntryContextProjectionRefusal:
        raise
    except (TextSelectionRefusal, OwnerTraceProjectionRefusal) as error:
        raise EntryContextProjectionRefusal("context_projection_refused", getattr(error, "stage", "linkage")) from error
    except (KeyError, TypeError, ValueError, AttributeError, IndexError, RecursionError, OverflowError, RegexError) as error:
        raise EntryContextProjectionRefusal("context_metadata_or_schema") from error
