"""Disabled metadata-only linkage under supplied anchors, never their authentication."""
from __future__ import annotations

import hashlib
import time
from tools.core import compiler_text_selection as core

PROFILE = "private_compiler_selection_controller"
_FLAGS = {"linkage_checked": True, "anchor_authenticated": False, "permission_authenticated": False,
          "read_authority_granted": False, "physical_path_binding_verified": False, "source_freshness_verified": False,
          "filesystem_capture_attested": False, "compiler_loaded": False, "input_code_evaluated": False,
          "node_observation_executed": False, "production_enabled": False, "semantic_lineage": False,
          "cache_eligible": False, "publication_authority": False}


class SelectionControllerRefusal(ValueError):
    def __init__(self, code, stage):
        super().__init__(code)
        self.code, self.stage = code, stage


def _need(condition, code, stage):
    if not condition:
        raise SelectionControllerRefusal(code, stage)


def _parse(raw, schema, definition, limit):
    try:
        value = core.loads_json_strict(raw.decode("utf-8"))
    except (ValueError, UnicodeError) as error:
        raise SelectionControllerRefusal("controller_receipt_invalid", "parse") from error
    value = core._plain(value, limit, "controller_parse")
    _need(core._canonical(value) == raw, "controller_receipt_invalid", "canonical")
    core._checked(value, definition, schema, "selection_receipt_invalid", "controller_parse")
    return value


def project_retained_selection_request(selection_raw, envelope_raw, anchor, binding, entry_expected, authority, profiles, schemas,
                                       *, now=time.monotonic, deadline=None):
    """Return linkage metadata/request only, using externally established trust inputs.

    Independent anchors and present permission must come from a trusted caller.
    A caller controlling every input can forge consistent metadata; no file,
    physical-path, grant or current-freshness authentication is performed here.
    """
    clock = core._Clock(now, deadline)
    _need(type(schemas) is dict and set(schemas) == {"selection", "capture", "controller", "entry", "text_window"}, "controller_policy", "policy")
    context = core._context(profiles, schemas["selection"], clock)
    k, ks, kl = context["descriptor"], context["schema"], context["descriptor"]["limits"]
    _need(type(profiles.get(PROFILE)) is dict, "controller_policy", "policy")
    copied = core._plain({"descriptor": profiles[PROFILE], "capture": profiles.get("private_compiler_selection_capture"),
        "schemas": {key: schemas[key] for key in ("capture", "controller", "entry", "text_window")},
        "anchor": anchor, "binding": binding, "entry_expected": entry_expected, "authority": authority,
        "owner": profiles.get("private_compiler_text_owner"), "window": profiles.get("private_compiler_text_window")}, kl["metadata_bytes"], "controller_policy")
    d, capture, sc = copied["descriptor"], copied["capture"], copied["schemas"]
    for schema in sc.values():
        core._schema_supported(schema, "controller_policy")
    core._checked(d, "policy", sc["controller"], "selection_policy_invalid", "controller_policy")
    core._checked(capture, "policy", sc["capture"], "selection_policy_invalid", "controller_policy")
    _need(d["version"] == "v1-private-selection-receipt-controller" and d["model"] == "retained_capture_anchor_and_current_request_v1"
          and d["complete_status"] == "METADATA_LINKAGE_CHECKED_UNDER_SUPPLIED_ANCHORS_NOT_AUTHORITY_OR_FRESHNESS"
          and capture["version"] == "v1-private-owned-selection-capture" and capture["model"] == "single_file_fixed_selection_kernel_v1"
          and capture["purpose"] == "produce_exact_file_selection_receipt" and capture["root_scheme"] == "preservation_absolute_path_text_utf8_v1"
          and capture["complete_status"] == "OWNED_FILE_SELECTION_CAPTURE_COMPLETED_NOT_CURRENT_FRESHNESS"
          and all(p[key] is False for p in (d, capture) for key in ("default_enabled", "native_execution_enabled", "production_enabled", "semantic_lineage", "cache_eligible")), "controller_policy", "policy")
    _need(d["schema_sha256"] == core._hash(sc["controller"]) and capture["schema_sha256"] == core._hash(sc["capture"])
          and d["capture_policy_sha256"] == core._hash(capture) and d["capture_schema_sha256"] == capture["schema_sha256"]
          and d["selection_policy_sha256"] == capture["selection_policy_sha256"] == core._hash(k)
          and d["selection_schema_sha256"] == capture["selection_schema_sha256"] == core._hash(ks), "controller_policy", "identity")
    limits = d["limits"]
    _need(all(type(n) is int and 0 < n <= core.MAX_SAFE for n in limits.values()), "controller_policy", "limits")
    _need(limits["receipt_bytes"] <= kl["receipt_bytes"] and limits["envelope_bytes"] <= capture["limits"]["envelope_bytes"]
          and limits["metadata_bytes"] <= kl["metadata_bytes"] and limits["request_bytes"] <= kl["projection_bytes"]
          and limits["result_bytes"] <= kl["metadata_bytes"] and limits["operation_ms"] <= min(kl["operation_ms"], capture["limits"]["operation_ms"]), "controller_policy", "limits")
    if deadline is None:
        clock.end = min(clock.end, clock.started + limits["operation_ms"] / 1000)
    clock.bind(limits["operation_ms"])
    _need(type(selection_raw) is bytes and type(envelope_raw) is bytes
          and len(selection_raw) <= limits["receipt_bytes"] and len(envelope_raw) <= limits["envelope_bytes"], "controller_input", "length")
    metadata = 2 * len(core._canonical(context)) + 2 * len(core._canonical(copied))
    reserved = metadata + 4 * (len(selection_raw) + len(envelope_raw)) + 2 * limits["request_bytes"] + limits["result_bytes"]
    _need(reserved <= limits["metadata_bytes"], "controller_budget", "preparse")
    anchor, binding, entry_expected, authority = (copied[key] for key in ("anchor", "binding", "entry_expected", "authority"))
    core._checked(anchor, "anchor", sc["controller"], "selection_expected_invalid", "controller_anchor")
    core._checked(binding, "binding", sc["controller"], "selection_expected_invalid", "controller_binding")
    origin = anchor["expected_origin_intent"]
    core._checked(origin, "scope", sc["capture"], "selection_expected_invalid", "controller_anchor")
    _need(all(core._hex(v) for obj in (anchor, binding, origin) for key, v in obj.items() if key.endswith("_sha256")), "controller_anchor", "shape")
    selection_sha, envelope_sha = hashlib.sha256(selection_raw).hexdigest(), hashlib.sha256(envelope_raw).hexdigest()
    _need(anchor["selection_receipt_sha256"] == selection_sha and anchor["capture_envelope_sha256"] == envelope_sha
          and anchor["origin_intent_sha256"] == core._hash(origin), "controller_anchor", "literal_identity")
    clock.check("controller_parse")
    receipt = _parse(selection_raw, ks, "receipt", limits["metadata_bytes"])
    envelope = _parse(envelope_raw, sc["capture"], "envelope", limits["metadata_bytes"])
    c, cost = receipt["content"], receipt["cost"]
    _need(envelope["origin_intent"] == origin and envelope["origin_intent_sha256"] == receipt["origin_ref_sha256"] == anchor["origin_intent_sha256"]
          and envelope["selection_receipt_sha256"] == selection_sha
          and envelope["content_sha256"] == receipt["content_sha256"] == core._hash(c), "controller_linkage", "origin")
    _need(envelope["capture_policy_sha256"] == origin["capture_policy_sha256"] == core._hash(capture)
          and envelope["capture_schema_sha256"] == origin["capture_schema_sha256"] == core._hash(sc["capture"])
          and receipt["producer_policy_sha256"] == origin["selection_policy_sha256"] == core._hash(k)
          and receipt["producer_schema_sha256"] == origin["selection_schema_sha256"] == core._hash(ks), "controller_linkage", "producer")
    _need(envelope["observed_source_sha256"] == origin["source_raw_sha256"] == c["source_raw_sha256"] == c["source_text_sha256"]
          and envelope["observed_source_bytes"] == origin["source_utf8_bytes"] == c["source_utf8_bytes"]
          and (origin["start_line"], origin["end_line"]) == (c["start_line"], c["end_line"]), "controller_linkage", "content")
    expected_flags = {key: False for key in ("source_freshness_verified", "read_authority_granted", "filesystem_capture_attested", "production_enabled", "semantic_lineage", "cache_eligible", "publication_authority")}
    expected_flags["checked_capture_completed"] = True
    _need(envelope["flags"] == expected_flags and set(receipt["flags"]) == set(core._DENIED_FLAGS)
          and all(value is False for value in receipt["flags"].values()), "controller_linkage", "claims")
    size = c["source_utf8_bytes"]
    _need(envelope["capture_open_attempts"] == 1 and envelope["capture_read_bytes"] == size
          and envelope["cumulative_open_attempts"] >= 1 and envelope["cumulative_read_bytes"] >= size
          and 0 < envelope["metadata_reserved_bytes"] <= capture["limits"]["metadata_bytes"]
          and 0 < envelope["capture_reserved_bytes"] <= capture["limits"]["image_bytes"]
          and 0 < envelope["kernel_reserved_bytes"] <= capture["limits"]["image_bytes"]
          and envelope["known_inputs_reserved_bytes"] == max(envelope["capture_reserved_bytes"], envelope["kernel_reserved_bytes"])
          and envelope["capture_reserved_bytes"] >= size + envelope["metadata_reserved_bytes"]
          and envelope["kernel_reserved_bytes"] >= max(envelope["metadata_reserved_bytes"], cost["known_inputs_reserved_bytes"])
          and envelope["kernel_scan_work_units"] == cost["scan_work_units"] <= min(kl["scan_work_units"], 2 * c["source_code_units"])
          and cost["scan_work_units"] >= c["source_code_units"]
          and cost["input_bytes"] == size and cost["measured_source_code_units"] == c["source_code_units"]
          and cost["decoded_text_reserved_bytes"] == 2 * size
          and 0 < cost["metadata_reserved_bytes"] <= kl["metadata_bytes"]
          and size + cost["metadata_reserved_bytes"] <= cost["known_inputs_reserved_bytes"] <= kl["image_bytes"]
          and cost["input_bytes"] + cost["decoded_text_reserved_bytes"] + sum(cost[key] for key in
              ("metadata_reserved_bytes", "view_reserved_bytes", "cursor_reserved_bytes", "receipt_reserved_bytes")) <= cost["known_inputs_reserved_bytes"]
          and c["view_utf8_bytes"] <= cost["view_reserved_bytes"] <= kl["view_utf8_bytes"]
          and 0 < cost["cursor_reserved_bytes"] <= kl["cursor_bytes"] and 0 < cost["receipt_reserved_bytes"] <= kl["receipt_bytes"]
          and cost["view_bytes_hashed"] == c["view_utf8_bytes"] and size <= capture["limits"]["file_bytes"], "controller_linkage", "ledger")
    _need(binding["anchor_ref_sha256"] == anchor["anchor_ref_sha256"]
          and binding["capture_root_scheme"] == origin["root_scheme"]
          and binding["capture_root_sha256"] == origin["root_sha256"]
          and binding["capture_relative_path_sha256"] == origin["relative_path_sha256"]
          and type(entry_expected) is dict and type(authority) is dict
          and binding["current_entry_context_sha256"] == core._hash(entry_expected)
          and binding["current_authority_sha256"] == core._hash(authority)
          and binding["node_root_sha256"] == authority.get("root_sha256")
          and binding["node_entry_relative_sha256"] == entry_expected.get("entry_relative_sha256"), "controller_binding", "current")
    clock.check("controller_projection")
    admitted = {core.PROFILE: k, "compiler_library_inputs": context["capture"], "private_input_image_experiment": context["image"],
                "private_compiler_text_owner": copied["owner"], "private_compiler_text_window": copied["window"]}
    request = core.project_text_window_request(selection_raw, selection_sha, entry_expected, authority, admitted,
        {"selection": ks, "entry": sc["entry"], "text_window": sc["text_window"]}, now=now, deadline=clock.end)
    _need(len(core._canonical(request)) <= limits["request_bytes"], "controller_budget", "request")
    result = {"version": d["version"], "status": d["complete_status"], "controller_policy_sha256": core._hash(d),
        "controller_schema_sha256": core._hash(sc["controller"]), "anchor_ref_sha256": anchor["anchor_ref_sha256"],
        "binding_ref_sha256": binding["binding_ref_sha256"], "selection_receipt_sha256": selection_sha, "capture_envelope_sha256": envelope_sha,
        "origin_intent_sha256": anchor["origin_intent_sha256"], "content_sha256": receipt["content_sha256"],
        "current_binding_sha256": core._hash(binding), "request_sha256": core._hash(request), "request": request,
        "metadata_reserved_bytes": metadata, "known_metadata_reserved_bytes": reserved, "flags": dict(_FLAGS)}
    core._checked(result, "result", sc["controller"], "selection_receipt_invalid", "controller_result")
    _need(len(core._canonical(result)) <= limits["result_bytes"], "controller_budget", "result")
    clock.check("controller_publish")
    return result
