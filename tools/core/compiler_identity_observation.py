"""One identity result predicate shared by fixed guarded endpoints."""
import hashlib,posixpath
from tools.core import compiler_metadata_dispatch as shared
from tools.core import compiler_text_selection as metadata


def require_literal_domains(value, node, schema, depth=0):
    """Bounded private-result supplement: Python equality merges bool/int/float.

    Preserve exact JSON literal domains here without changing the global schema
    validator or its other consumers. Input/output captures already bound size.
    """
    shared._need(depth < 32, "reader_literal_domain", "child")
    if "$ref" in node:
        return require_literal_domains(value, schema["$defs"][node["$ref"].removeprefix("#/$defs/")], schema, depth + 1)
    if "const" in node:
        shared._need(metadata._canonical(value) == metadata._canonical(node["const"]), "reader_literal_domain", "child")
    if "enum" in node:
        encoded = metadata._canonical(value)
        shared._need(any(encoded == metadata._canonical(v) for v in node["enum"]), "reader_literal_domain", "child")
    if type(value) is dict:
        for key, item in node.get("properties", {}).items():
            if key in value:
                require_literal_domains(value[key], item, schema, depth + 1)
    elif type(value) is list and "items" in node:
        for item in value:
            require_literal_domains(item, node["items"], schema, depth + 1)


def validate_identity_observation(node, prepared, q, probe_named_plus_input, *, identity_status="SYNTHETIC_FIVE_ROLE_IDENTITY_OBSERVED_NOT_COMPILER_ADMISSION"):
    value, accounting = node["observation"], node["payload_accounting"]
    require_literal_domains(value, prepared["held"]["schemas"]["probe"]["$defs"]["result"], prepared["held"]["schemas"]["probe"])
    expected = prepared["held"]["probe_expected"]
    shared._need(value["status"] == identity_status
                 and value["expected_sha256"] == metadata._hash(expected) and not any(value["flags"].values())
                 and value["invocation_id"] == expected["entry_expected"]["invocation_id"]
                 and value["policy_sha256"] == expected["policy_sha256"]
                 and value["schema_sha256"] == expected["schema_sha256"]
                 and value["entry_owner_allocated"] is False and node["error"] is None,
                 "reader_claim", "child")
    records = {r["role"]: r for r in value["records"]}
    payloads = {r["role"]: r for r in accounting["roles"]}
    shared._need(len(value["records"]) == len(records) == len(payloads) == len(accounting["roles"]) == 5
                 and set(records) == set(payloads) == set(expected["roles"])
                 and accounting["open_attempts"] == accounting["open_returns"] == accounting["close_attempts"] == accounting["close_returns"] == 5
                 and all(accounting[k] == 0 for k in ("open_unknown", "read_unknown", "close_unknown", "emergency_closes", "known_open_descriptors"))
                 and value["accounting"]["open_attempts"] == value["accounting"]["open_returns"] == value["accounting"]["close_attempts"] == value["accounting"]["close_returns"] == 5
                 and all(value["accounting"][k] == 0 for k in ("open_unknown", "read_unknown", "close_unknown", "secondary_cleanup_errors"))
                 and value["accounting"]["read_attempts"] == value["accounting"]["read_returns"] == accounting["read_attempts"]
                 and sum(r["read_attempts"] for r in payloads.values()) == accounting["read_attempts"]
                 and node["metadata_accounting"]["attempts"] == node["metadata_accounting"]["returns"]
                 and 0 < node["metadata_accounting"]["attempts"] <= node["wrapper_steps"]
                 and node["metadata_accounting"]["unknown"] == 0 and node["forbidden_attempts"] == 0,
                 "reader_accounting", "child")
    for role, row in records.items():
        declared, native = expected["roles"][role], payloads[role]
        member = (expected["entry_expected"]["entry_relative_sha256"] if role == "installed_entry" else
                  hashlib.sha256(posixpath.basename(declared["relative_path"]).encode()).hexdigest() if role == "installed_package" else
                  hashlib.sha256(declared["relative_path"].encode()).hexdigest())
        shared._need(row["relative_path"] == declared["relative_path"]
                     and row["raw_sha256"] == row["expected_sha256"] == declared["expected_sha256"]
                     and row["declared_path_sha256"] == hashlib.sha256(declared["relative_path"].encode()).hexdigest()
                     and row["member_path_sha256"] == member
                     and row["expectation_class"] == declared["expectation_class"] and row["anchor_ref_sha256"] == declared["anchor_ref_sha256"]
                     and native["known_delivered_bytes"] == row["size_bytes"] <= declared["max_raw_bytes"]
                     and 1 <= native["read_attempts"] <= native["known_delivered_bytes"] + 1
                     and native["cap"] == declared["max_raw_bytes"] and native["close_attempted"] is True
                     and native["closed"] is True and native["close_unknown"] is False, "reader_identity", "record")
    entry_record = records[prepared["profiles"]["private_compiler_entry_identity"]["entry_role"]]
    entry_bytes, units = expected["entry_expected"]["entry_bytes"], value["entry"]["text_code_units"]
    shared._need(sum(r["size_bytes"] for r in records.values()) == accounting["known_delivered_bytes"]
                 == value["accounting"]["known_delivered_bytes"]
                 and value["entry"]["raw_sha256"] == expected["entry_expected"]["entry_sha256"]
                 and value["entry"]["text_sha256"] == expected["entry_expected"]["text_sha256"]
                 and entry_record["size_bytes"] == value["entry"]["size_bytes"] == entry_bytes
                 and units <= entry_bytes <= 3 * units, "reader_identity", "entry")
    shared._need(value["known_named_partial_bytes"] == probe_named_plus_input, "reader_identity", "reserve")
    return node
