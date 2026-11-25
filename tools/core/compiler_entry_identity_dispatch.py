"""Disabled five-role declaration linkage; never reads compiler input files.

Only the trusted caller's owned temporary intent store is touched. Supplied
matching hashes are linkage metadata, not authenticated permission or observed
bytes. The fixed endpoint cannot select a process, callback, probe or reader.
"""
from __future__ import annotations

import hashlib
from pathlib import PurePosixPath, PureWindowsPath
import re
import time
from tools.core import compiler_metadata_dispatch as shared
from tools.core import compiler_text_selection as metadata
from tools.core.source_preservation_reads import _relative

PROFILE = "private_compiler_entry_identity_dispatch"
PROBE = "private_compiler_entry_identity"
_DEPENDENCIES = (PROBE, "private_compiler_owned_view_basis", "private_compiler_entry_owner",
                 "compiler_library_inputs", "private_input_image_experiment")
_OFF = ("default_enabled", "native_execution_enabled", "production_enabled", "semantic_lineage", "cache_eligible")
_VERSION = "v1-private-synthetic-entry-identity-dispatch"
_SCOPE = "synthetic_five_role_identity_linkage_only"
_STATUS = "SYNTHETIC_FIVE_ROLE_DISPATCH_LINKED_NOT_READ_AUTHORITY"


def _probe_context(p, schemas, expected):
    """Exactly the existing probe's canonical context; no probe invocation."""
    return {"descriptor": p[PROBE], "expected": expected,
            "profiles": {k: p[k] for k in _DEPENDENCIES if k != PROBE},
            "schemas": {"identity": schemas["probe"], "entry": schemas["entry"]}}


def _pins(p, schemas, probe_expected):
    d, probe = p[PROFILE], p[PROBE]
    basis, path = p["private_compiler_owned_view_basis"], p["private_compiler_entry_owner"]
    return {"policy_sha256": metadata._hash(d), "schema_sha256": metadata._hash(schemas["dispatch"]),
            "implementation_sha256": d["source_sha256"], "shared_source_sha256": d["shared_source_sha256"],
            "probe_policy_sha256": metadata._hash(probe), "probe_schema_sha256": metadata._hash(schemas["probe"]),
            "role_policy_sha256": metadata._hash(basis["role_expected_fields"]),
            "path_policy_sha256": metadata._hash({k: path[k] for k in ("owner_id", "relative_path_pattern")}),
            "probe_source_sha256": probe["source_sha256"], "probe_identity_source_sha256": probe["identity_source_sha256"],
            "probe_expected_sha256": metadata._hash(probe_expected),
            "probe_context_sha256": metadata._hash(_probe_context(p, schemas, probe_expected)),
            "child_id": d["child_id"]}


def _policies(p, schemas, expected):
    shared._keys(p, (PROFILE, *_DEPENDENCIES), "policy")
    shared._keys(schemas, ("dispatch", "probe", "entry"), "policy")
    for s in schemas.values():
        metadata._schema_supported(s, "dispatch_policy")
    d, probe = p[PROFILE], p[PROBE]
    metadata._checked(d, "policy", schemas["dispatch"], "dispatch_policy", "policy")
    metadata._checked(probe, "policy", schemas["probe"], "dispatch_policy", "policy")
    metadata._checked(p["private_compiler_entry_owner"], "policy", schemas["entry"], "dispatch_policy", "policy")
    shared._need(d["version"] == _VERSION and d["scope"] == _SCOPE and d["status"] == _STATUS
                 and d["model"] == "synthetic_five_role_exclusive_intent_fixed_nonreading_child_v1"
                 and d["child_id"] == "v1-fixed-nonreading-metadata-child"
                 and all(d[k] is False and probe[k] is False for k in _OFF), "dispatch_policy", "policy")
    shared._need(d["identity_chars"] == 96 and d["intent_store_path_chars"] == 1024
                 and d["declared_scope_limits"] == {"root_chars": 1024, "path_chars": 256}
                 and {"permission_authenticated", "read_authority_granted", "actual_entry_observed",
                      "atomic_snapshot_verified", "complete_compiler_inputs", "snapshot_bound", "compiler_loaded",
                      "native_execution_attested", "production_enabled", "semantic_lineage", "cache_eligible",
                      "publication_authority"} <= set(d["denied_flags"]), "dispatch_policy", "policy")
    limits, ceiling = d["limits"], probe["limits"]
    shared._need(all(type(v) is int and 0 < v <= metadata.MAX_SAFE for v in limits.values())
                 and limits["context_bytes"] <= ceiling["context_bytes"]
                 and limits["metadata_bytes"] <= min(ceiling["metadata_bytes"], p["private_input_image_experiment"]["limits"]["metadata_bytes"])
                 and all(limits[k] <= ceiling["output_bytes"] for k in ("request_bytes", "decision_bytes", "intent_bytes", "result_bytes"))
                 and limits["operation_ms"] <= ceiling["operation_ms"], "dispatch_policy", "limits")
    pins = _pins(p, schemas, expected)
    shared._need(d["schema_sha256"] == pins["schema_sha256"]
                 and all(d[k] == pins[k] for k in ("probe_policy_sha256", "probe_schema_sha256", "role_policy_sha256",
                                                  "path_policy_sha256", "probe_source_sha256", "probe_identity_source_sha256")),
                 "dispatch_identity", "pins")
    owner = p["private_compiler_entry_owner"]
    shared._need(probe["schema_sha256"] == pins["probe_schema_sha256"]
                 and probe["entry_policy_sha256"] == metadata._hash(owner)
                 and probe["entry_schema_sha256"] == owner["schema_sha256"] == metadata._hash(schemas["entry"])
                 and probe["capture_policy_sha256"] == metadata._hash(p["compiler_library_inputs"])
                 and probe["image_policy_sha256"] == metadata._hash(p["private_input_image_experiment"])
                 and probe["role_policy_sha256"] == pins["role_policy_sha256"]
                 and probe["role_aliases_sha256"] == metadata._hash(p["private_compiler_owned_view_basis"]["role_aliases"]),
                 "dispatch_identity", "probe_pins")
    return pins


def _scope(r, probe_expected, p, schemas):
    from tools.core.compiler_five_role_scope import check_five_role_scope
    return check_five_role_scope(r, probe_expected, p, schemas, p[PROFILE])


def _prepare(request, decision, expected, probe_expected, profiles, schemas, fixture_root, clock, check):
    shared._need(type(profiles) is dict and type(profiles.get(PROFILE)) is dict, "dispatch_policy", "policy")
    raw = profiles[PROFILE].get("limits")
    shared._need(type(raw) is dict and type(raw.get("context_bytes")) is int and 0 < raw["context_bytes"] <= 65536,
                 "dispatch_policy", "policy")
    held = metadata._plain({"profiles": profiles, "schemas": schemas, "probe_expected": probe_expected,
                            "request": request, "decision": decision, "expected": expected}, raw["context_bytes"], "dispatch_context")
    p, schemas, q = (held[k] for k in ("profiles", "schemas", "probe_expected"))
    pins = _policies(p, schemas, q)
    d, schema = p[PROFILE], schemas["dispatch"]
    limits = d["limits"]
    shared._need(len(metadata._canonical(held)) <= limits["context_bytes"], "dispatch_budget", "context")
    clock.bind(limits["operation_ms"])
    check("capture")
    r, a, e = (held[k] for k in ("request", "decision", "expected"))
    for value, definition in ((r, "request"), (a, "decision"), (e, "expected")):
        metadata._checked(value, definition, schema, "dispatch_shape", definition)
    shared._keys(r, ("principal_ref", "decision_ref", "invocation_id", "purpose", "root_scheme", "root_text", "roles",
                     "max_open_attempts", "max_total_raw_bytes", "max_delivered_bytes", "unassigned_headroom_bytes",
                     "probe_expected_sha256", "probe_context_sha256"), "request")
    shared._need(e == pins and all(r[k] == pins[k] for k in ("probe_expected_sha256", "probe_context_sha256")), "dispatch_identity", "pins")
    shared._check_decision(r, a, d, clock, check)
    _scope(r, q, p, schemas)
    return shared._prepare_reservation(held, p, schema, d, fixture_root)


def _dispatch_synthetic_compiler_entry_identity(request, decision, expected, probe_expected, profiles, schemas, *,
                                                  fixture_root, now=time.monotonic, deadline=None):
    """Single-use synthetic declaration. No backend or actual-mode switch."""
    c = shared._enter()
    prepared = None
    try:
        clock = metadata._Clock(now, deadline)
        def check(stage):
            c["stage"] = stage
            shared._need(shared._ACTIVE is c and not c["poisoned"], "dispatch_reentry", stage)
            clock.check(stage)
            shared._need(shared._ACTIVE is c and not c["poisoned"], "dispatch_reentry", stage)
        prepared = _prepare(request, decision, expected, probe_expected, profiles, schemas, fixture_root, clock, check)
        return shared._complete_nonreading(prepared, c, clock, check, fixture_root)
    except BaseException as error:
        shared._note_failure(error, c)
        raise
    finally:
        prepared = None
        shared._finish(c)
