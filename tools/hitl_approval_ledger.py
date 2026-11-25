from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.core.config import CONFIG_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.hitl_ledger_state import summarize_ledger_entries
from tools.core.json_io import load_json_file


LEDGER_PATH = RAW_DIR / "hitl_approval_ledger.json"
REPORT_PATH = REPORTS_DIR / "hitl_approval_ledger.md"
GOVERNANCE_CONTRACT_PATH = CONFIG_DIR / "hitl_governance_contract.json"
SIGNATURE_ALGORITHM = "HMAC-SHA256"
# Receiver compatibility pin for this supplied-schema v1, not file/code attestation.
_COMPARISON_DECISION_SCHEMA_SHA256 = "d68779644fa2ecf75e28e6b5a8041da0d50e994c47aea71dcf3c199e3156a088"
GENESIS_CHAIN_HASH = "GENESIS"
SECRET_PATH = CONFIG_DIR / ".nexora_hitl_secret"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_ledger() -> dict[str, Any]:
    payload = load_json_file(LEDGER_PATH, {})
    if isinstance(payload, dict) and isinstance(payload.get("entries"), list):
        return payload
    return {
        "meta": {
            "kind": "hitl_approval_ledger",
            "version": "v1",
            "created_at": _utc_now(),
            "generator": "tools.hitl_approval_ledger",
        },
        "summary": {
            "entries": 0,
            "active_approvals": 0,
            "rejected": 0,
            "deferred": 0,
            "revoked": 0,
        },
        "entries": [],
    }


def _safe_text(value: str) -> str:
    return str(value or "").strip()


def _valid_decisions() -> set[str]:
    payload = load_json_file(GOVERNANCE_CONTRACT_PATH, {})
    validation = payload.get("validation_contract", {}) if isinstance(payload, dict) else {}
    values = validation.get("valid_ledger_decisions")
    return {str(item).strip() for item in values if str(item).strip()} if isinstance(values, list) else set()


def _configured_signing_secret_text() -> str | None:
    # Capture configured native process context once. No file or initializer.
    return os.environ.get("NEXORA_HITL_SECRET") or os.environ.get("CODEMAPS_HITL_SECRET")


def _get_signing_secret() -> tuple[bytes, str]:
    env_secret = _configured_signing_secret_text()
    if env_secret:
        return env_secret.encode("utf-8"), "env:NEXORA_HITL_SECRET"

    if not SECRET_PATH.exists():
        SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
        SECRET_PATH.write_text(secrets.token_hex(32), encoding="utf-8")
    return SECRET_PATH.read_text(encoding="utf-8").strip().encode("utf-8"), str(SECRET_PATH)


def load_existing_ledger_observation(raw_dir: Path | None = None) -> Any:
    """Read unchanged JSON from the selected fixed store, without initialization.

    Internal adapters may supply their source-owned raw root; it must match the
    existing store/manager binding. Public tools expose no path/backend selector.
    This observation is not trusted native currentness or approval evidence.
    """
    import tools.core.artifact_store as artifact_store
    from tools.core.json_io import load_json_strict, load_raw_artifact_primary_with_identity

    store = artifact_store.STORE
    root = Path(LEDGER_PATH.parent if raw_dir is None else raw_dir).resolve()
    if (Path(store._raw_dir).resolve() != root
            or Path(store.db_manager.base_db_path).resolve() != root / "codemaps.db"):
        raise ValueError("approval_ledger_observation_binding_mismatch")
    # Reuse tenant routing without raw_path's directory creation.
    path = store.db_manager.db_path.parent / "hitl_approval_ledger.json"
    if store.use_sqlite:
        observed = load_raw_artifact_primary_with_identity(path)
        return None if observed is None else observed[0]
    return load_json_strict(path, bypass_proxy=True)


def _native_primary_preimage_route() -> Path:
    """Fixed cooperative route only; no native origin or provisioning authority."""
    import tools.core.config as config
    from tools.core.db import _ambient_connections, get_current_tenant

    if get_current_tenant() is not None or _ambient_connections.get() is not None:
        raise ValueError("native_preimage_context_not_allowed")
    if (config._TARGET_OUTPUT_ROOT is not None or config._TARGET_BASE_DIR is not None
            or config._TARGET_RUN_ID != ""):
        raise ValueError("native_preimage_external_output_not_allowed")
    if type(config.DYNAMIC_CONFIG) is not dict or config.DYNAMIC_CONFIG.get("use_sqlite") is not True:
        raise ValueError("native_preimage_sqlite_required")
    raw = _ROOT / "output" / ".raw"
    database = raw / "codemaps.db"
    try:
        matches = (config.CODE_MAPS_DIR.resolve() == _ROOT
                   and config.OUTPUT_DIR.resolve() == _ROOT / "output"
                   and config.RAW_DIR.resolve() == raw and RAW_DIR.resolve() == raw
                   and LEDGER_PATH.resolve() == raw / "hitl_approval_ledger.json"
                   and database.resolve() == database)
    except (OSError, RuntimeError, TypeError, ValueError):
        raise ValueError("native_preimage_route_unavailable") from None
    if not matches:
        raise ValueError("native_preimage_route_mismatch")
    return database


def _observe_native_primary_preimage(expected: dict) -> dict:
    """Private bounded correspondence observation, never approval or a CAS lease.

    No caller path/manager/connection, STORE, shadow, key or schema initializer.
    The same fixed route is rechecked before opening and around the held read.
    Metadata is published only after the original read-only owner has closed.
    """
    import sqlite3
    import stat
    from tools.core.db import SQLiteManager
    from tools.core.json_io import PrimarySnapshotRefusal, read_comparison_primary_snapshot_from_connection

    def result(state, *, error_type=None):
        value = {"state": state, "native_authority": False, "native_generation": None,
                 "whole_revision": None, "write_reservation": False,
                 "native_baseline_status": "not_acquired"}
        if error_type is not None:
            value["error_type"] = error_type
        return value

    database = _native_primary_preimage_route()
    try:
        mode = database.stat().st_mode
    except FileNotFoundError:
        return result("DATABASE_MISSING")
    except OSError as error:
        return result("DATABASE_IO_UNAVAILABLE", error_type=type(error).__name__)
    if not stat.S_ISREG(mode):
        return result("DATABASE_IO_UNAVAILABLE", error_type="not_regular_file")
    manager = SQLiteManager(database)

    def check_binding():
        if (_native_primary_preimage_route() != database or type(manager) is not SQLiteManager
                or manager.base_db_path != database or manager.db_path != database):
            raise ValueError("native_preimage_manager_binding_changed")

    check_binding()
    observed, state, error_type = None, "SCHEMA_UNAVAILABLE", None
    try:
        with manager.get_read_only_connection() as connection:
            check_binding()
            connection.execute("BEGIN")
            # Fixed main-table existence, not an unbounded schema/body inventory.
            present = connection.execute(
                "SELECT 1 FROM main.sqlite_schema WHERE type = 'table' AND name = 'state_payloads' LIMIT 1"
            ).fetchone()
            if present is not None:
                try:
                    observed = read_comparison_primary_snapshot_from_connection(connection, expected)
                except PrimarySnapshotRefusal as error:
                    state = "PRIMARY_ABSENT" if error.args == ("primary_snapshot_missing",) else "CONTENT_REFUSED"
                except ValueError:
                    state = "CONTENT_REFUSED"
                except sqlite3.Error as error:
                    state, error_type = "PRIMARY_QUERY_UNAVAILABLE", type(error).__name__
            check_binding()
    except (sqlite3.Error, OSError) as error:
        return result("DATABASE_IO_UNAVAILABLE", error_type=type(error).__name__)
    if observed is None:
        return result(state, error_type=error_type)
    # Drop the tree instead of publishing raw decisions or treating content as trust.
    del observed["entries_projection"]
    return {**result("PREIMAGE_OBSERVED_UNBOUND"), "snapshot_observation": observed}


class _NativeLedgerBaselineRefusal(ValueError):
    """Bounded supplied-metadata refusal, never a native storage outcome."""


def _check_native_ledger_baseline_transition(current, expected, replacement) -> dict:
    """Link supplied rewrite metadata only; open no DB/key or approval owner."""
    from jsonschema import Draft202012Validator
    from jsonschema.exceptions import SchemaError
    from referencing import Registry
    from referencing.exceptions import Unresolvable
    from tools.core.compiler_entry_comparison_projection import _measure, EntryComparisonProjectionRefusal
    from tools.core.compiler_text_selection import MAX_SAFE, _plain, _canonical, TextSelectionRefusal
    from tools.core.json_io import load_json_object_strict, _primary_snapshot_integer, PrimarySnapshotRefusal

    def need(ok, code):
        if not ok:
            raise _NativeLedgerBaselineRefusal(code)

    try:
        schema = load_json_object_strict(
            _ROOT / "config" / "schemas" / "native_approval_ledger_baseline.schema.json", bypass_proxy=True)
        contract = schema["$defs"]["contract"]["const"]
        need(contract["version"] == "v1-private-native-ledger-baseline-contract"
             and contract["state"] == "specified_not_installed", "native_baseline_contract")
        need(schema["$ref"] == contract["record_definition"], "native_baseline_contract")
        disabled = {k: v for k, v in contract.items()
                    if k.endswith("_enabled") or k.endswith("_authority") or k == "runtime_admission"}
        need(bool(disabled) and all(v is False for v in disabled.values()), "native_baseline_contract")
        ceiling = _primary_snapshot_integer(contract["metadata_copy_charge_ceiling"], MAX_SAFE, 1)
        form_ceiling = _primary_snapshot_integer(contract["canonical_form_byte_ceiling"], MAX_SAFE, 1)
        raw = {"current": current, "expected": expected, "replacement": replacement}
        nodes, scalar = _measure(raw, ceiling, ceiling)
        data = _plain(raw, ceiling, "native_baseline_transition")
        c, e, n = (data[k] for k in ("current", "expected", "replacement"))
        Draft202012Validator.check_schema(schema)
        # Empty retrieval registry: local schema refs only, no file/network loader.
        registry = Registry()
        record_validator = Draft202012Validator(schema, registry=registry)
        expected_validator = Draft202012Validator(
            {**schema, "$ref": contract["preimage_definition"]}, registry=registry)
        need(record_validator.is_valid(c) and record_validator.is_valid(n)
             and expected_validator.is_valid(e), "native_baseline_shape")
        maximum = _primary_snapshot_integer(schema["$defs"]["revision"]["maximum"], MAX_SAFE)
        try:
            for value in (c, n):
                _primary_snapshot_integer(value["baseline_revision"], 0)
                _primary_snapshot_integer(value["revision"], maximum)
            _primary_snapshot_integer(e["revision"], maximum)
        except PrimarySnapshotRefusal:
            raise _NativeLedgerBaselineRefusal("native_baseline_integer") from None
        for value in (c, n):
            need(value["revision"] != value["baseline_revision"]
                 or value["full_payload_sha256"] == value["baseline_full_payload_sha256"],
                 "native_baseline_initial_identity")
        fields = schema["$defs"]["preimage"]["properties"]
        need(all(c[k] == e[k] for k in fields), "native_baseline_expected_preimage")
        mutable = set(contract["mutable_properties"])
        need(mutable <= set(c), "native_baseline_contract")
        need(all(c[k] == n[k] for k in c if k not in mutable), "native_baseline_immutable_origin")
        need(c["revision"] < maximum, "native_baseline_revision_exhausted")
        need(n["revision"] == c["revision"] + 1, "native_baseline_revision_not_next")
        form_sizes = []

        def fingerprint(value):
            form = _canonical(value)
            need(len(form) <= form_ceiling, "native_baseline_canonical_budget")
            form_sizes.append(len(form))
            return hashlib.sha256(form).hexdigest()

        out = {"status": "SUPPLIED_BASELINE_REWRITE_LINKED_NOT_NATIVE_STATE",
               "schema_sha256": fingerprint(schema), "current_metadata_sha256": fingerprint(c),
               "expected_metadata_sha256": fingerprint(e), "replacement_metadata_sha256": fingerprint(n),
               "previous_revision": c["revision"], "next_revision": n["revision"],
               "declared_full_identity_repeated": c["full_payload_sha256"] == n["full_payload_sha256"],
               "native_authority": False, "provisioning_authenticated": False,
               "current_primary_revision_verified": False, "write_reservation": False,
               "disabled_features": disabled,
               "logical_cuts": {"joint_input_nodes": nodes, "joint_input_scalar_bytes": scalar,
                                "metadata_copy_ceiling": ceiling, "canonical_form_byte_ceiling": form_ceiling,
                                "canonical_role_bytes": form_sizes, "canonical_forms": 5}}
        need(len(_canonical(out)) <= form_ceiling, "native_baseline_output_budget")
        return out
    except _NativeLedgerBaselineRefusal:
        raise
    except (EntryComparisonProjectionRefusal, TextSelectionRefusal):
        raise _NativeLedgerBaselineRefusal("native_baseline_metadata_or_budget") from None
    except (OSError, SchemaError, Unresolvable, KeyError, TypeError, ValueError, AttributeError,
            RecursionError, OverflowError):
        raise _NativeLedgerBaselineRefusal("native_baseline_metadata_or_schema") from None


def _read_existing_verification_secret() -> tuple[bytes, str]:
    """Capture one configured key or read an existing local key; never create."""
    configured = _configured_signing_secret_text()
    if configured:
        return configured.encode("utf-8"), "configured_process_key"
    # Read directly: missing, denied or invalid input must not enter a creator.
    secret = SECRET_PATH.read_text(encoding="utf-8").strip().encode("utf-8")
    if not secret:
        raise ValueError("existing_verification_key_empty")
    return secret, "existing_local_key"


def _read_only_verification_failure(reason: str) -> dict[str, Any]:
    return {
        "status": "FAIL", "signed_entries": 0, "legacy_unsigned_entries": 0,
        "invalid_entries": [{"index": None, "reason": reason}], "last_chain_hash": None,
        "verification_scope": "existing_ledger_integrity_observation_only",
    }


def verify_ledger_observation(*, raw_dir: Path | None = None) -> dict[str, Any]:
    """Verify unnormalized observed entries with an existing key, without writes.

    Reuses the original algorithm. PASS is integrity under the observed key,
    never human/key origin, current approval, latest decision or runtime authority.
    Legacy signing/bootstrap verification remains a separate unchanged route.
    """
    return _observe_verified_ledger(raw_dir=raw_dir)[1]


def _observe_verified_ledger(*, raw_dir: Path | None = None) -> tuple[Any, dict[str, Any]]:
    """Acquire once and verify that same payload; no currentness attestation."""
    try:
        observed = load_existing_ledger_observation(raw_dir)
    except FileNotFoundError:
        return None, _read_only_verification_failure("ledger_unavailable:missing")
    except Exception as exc:
        return None, _read_only_verification_failure(f"ledger_unavailable:{type(exc).__name__}")
    return observed, verify_supplied_ledger_observation(observed)


def verify_supplied_ledger_observation(observed: Any) -> dict[str, Any]:
    """Verify supplied values with an existing key, without acquiring a ledger.

    An integrity result does not attest supplied origin, currentness or authority.
    The existing policy consumer separately retains its gate/scope/expiry rules.
    """
    if observed is None:
        return _read_only_verification_failure("ledger_unavailable:missing")
    if type(observed) is not dict or type(observed.get("entries")) is not list:
        return _read_only_verification_failure("entries_not_list")
    secret = None
    try:
        secret, source = _read_existing_verification_secret()
    except Exception as exc:
        return _read_only_verification_failure(f"secret_unavailable:{type(exc).__name__}")
    try:
        result = _verify_ledger_entries(observed["entries"], secret, source)
        return {**result, "verification_scope": "existing_ledger_integrity_observation_only"}
    except Exception as exc:
        return _read_only_verification_failure(f"verification_unavailable:{type(exc).__name__}")
    finally:
        secret = None  # Drop owned alias; no physical erasure/GC claim.


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _signature_payload(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in entry.items()
        if key not in {"signature", "chain_hash", "signature_algorithm", "signature_key_source"}
    }


def _legacy_entry_hash(entry: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(_signature_payload(entry)).encode("utf-8")).hexdigest()


def _sign_entry(entry: dict[str, Any], prev_chain_hash: str) -> dict[str, Any]:
    secret, source = _get_signing_secret()
    entry["prev_chain_hash"] = prev_chain_hash
    payload = _signature_payload(entry)
    signature = hmac.new(secret, _canonical_json(payload).encode("utf-8"), hashlib.sha256).hexdigest()
    chain_hash = hashlib.sha256(f"{prev_chain_hash}:{signature}".encode("utf-8")).hexdigest()
    entry["signature_algorithm"] = SIGNATURE_ALGORITHM
    entry["signature_key_source"] = source
    entry["signature"] = signature
    entry["chain_hash"] = chain_hash
    return entry


def verify_ledger(ledger: dict[str, Any] | None = None) -> dict[str, Any]:
    ledger = ledger if isinstance(ledger, dict) else _load_ledger()
    entries = ledger.get("entries", [])
    if not isinstance(entries, list):
        return {
            "status": "FAIL",
            "signed_entries": 0,
            "legacy_unsigned_entries": 0,
            "invalid_entries": [{"index": None, "reason": "entries_not_list"}],
            "last_chain_hash": None,
        }

    try:
        secret, source = _get_signing_secret()
    except Exception as exc:
        return {
            "status": "FAIL",
            "signed_entries": 0,
            "legacy_unsigned_entries": 0,
            "invalid_entries": [{"index": None, "reason": f"secret_unavailable:{exc}"}],
            "last_chain_hash": None,
        }

    return _verify_ledger_entries(entries, secret, source)


def _verify_ledger_entries(entries: list[Any], secret: bytes, source: str) -> dict[str, Any]:
    """Original verification algorithm shared by legacy and strict evidence paths."""
    prev = GENESIS_CHAIN_HASH
    signed = 0
    unsigned = 0
    invalid: list[dict[str, Any]] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            invalid.append({"index": index, "reason": "entry_not_object"})
            continue

        signature = entry.get("signature")
        chain_hash = entry.get("chain_hash")
        if not signature or not chain_hash:
            unsigned += 1
            prev = _legacy_entry_hash(entry)
            continue

        if entry.get("signature_algorithm") != SIGNATURE_ALGORITHM:
            invalid.append({"index": index, "id": entry.get("id"), "reason": "unsupported_signature_algorithm"})
        if entry.get("prev_chain_hash") != prev:
            invalid.append(
                {
                    "index": index,
                    "id": entry.get("id"),
                    "reason": "prev_chain_hash_mismatch",
                    "expected": prev,
                    "actual": entry.get("prev_chain_hash"),
                }
            )

        expected_sig = hmac.new(
            secret,
            _canonical_json(_signature_payload(entry)).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        expected_chain = hashlib.sha256(f"{entry.get('prev_chain_hash')}:{expected_sig}".encode("utf-8")).hexdigest()
        if not hmac.compare_digest(str(signature), expected_sig):
            invalid.append({"index": index, "id": entry.get("id"), "reason": "signature_mismatch"})
        if not hmac.compare_digest(str(chain_hash), expected_chain):
            invalid.append({"index": index, "id": entry.get("id"), "reason": "chain_hash_mismatch"})
        signed += 1
        prev = str(chain_hash)

    status = "PASS" if not invalid else "FAIL"
    if unsigned and not invalid:
        status = "PASS_WITH_LEGACY_UNSIGNED"
    return {
        "status": status,
        "signed_entries": signed,
        "legacy_unsigned_entries": unsigned,
        "invalid_entries": invalid[:50],
        "last_chain_hash": prev if entries else GENESIS_CHAIN_HASH,
        "signature_algorithm": SIGNATURE_ALGORITHM,
        "signature_key_source": source,
    }


class SelectedSignedDecisionRefusal(ValueError):
    """Refused supplied evidence, never a denied or granted runtime allowance."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def project_comparison_signed_decision(ledger, request, expected, schema):
    """Verify a bounded supplied snapshot under one configured process key.

    Disabled by default: no CLI/MCP dispatch, live ledger/file-key loading,
    approval recording, current-primary assertion or one-use token is provided.
    A configured key's origin and the actor's human identity are not attested.
    """
    # Fixed imports only on this separate path; ordinary HITL startup stays as is.
    from tools.core.compiler_entry_comparison_projection import _measure, EntryComparisonProjectionRefusal
    from tools.core.compiler_text_selection import (
        MAX_SAFE, _plain, _canonical, _hash, _hex, _checked, _schema_supported, TextSelectionRefusal,
    )
    from tools.core.hitl_ledger_state import latest_decisions_by_gate_scope
    from re import error as RegexError

    def need(ok, code):
        if not ok:
            raise SelectedSignedDecisionRefusal(code)

    def timestamp(value):
        # Match the existing recorder's canonical UTC form before its lexical
        # latest-decision helper is used. Do not change legacy parsing policy.
        need(type(value) is str and 20 <= len(value) <= 32, "signed_timestamp")
        instant = datetime.fromisoformat(value)
        need(instant.utcoffset() is not None and instant.utcoffset().total_seconds() == 0
             and instant.isoformat() == value and value.endswith("+00:00"), "signed_timestamp")
        return instant

    secret_text = secret = None
    try:
        N, C, K = 16384, 131072, 1310720
        raw = {"ledger": ledger, "request": request, "expected": expected, "schema": schema}
        nodes, scalar = _measure(raw, N, C)
        data = _plain(raw, C, "signed_snapshot")
        l, r, e, s = (data[k] for k in ("ledger", "request", "expected", "schema"))
        schema_digest = _hash(s)
        need(schema_digest == _COMPARISON_DECISION_SCHEMA_SHA256, "signed_schema_identity")
        _schema_supported(s, "signed_contract")
        d = s["$defs"]["contract"]["const"]
        supported = {"version": "v1-private-selected-comparison-signed-evidence", "scope": "supplied_signed_snapshot_evidence_only",
            "default_enabled": False, "runtime_admission_enabled": False, "production_enabled": False,
            "gate": "compiler_preparation_comparison", "scope_prefix": "compiler_preparation_comparison:",
            "input_nodes": N, "input_scalar_bytes": C, "maximum_entries": 64, "maximum_key_utf8_bytes": 1024,
            "canonical_form_bound_bytes": K, "new_canonical_form_slots": 8, "output_bytes": 16384,
            "key_mode": "preconfigured_process_environment_only"}
        need(type(d) is dict and set(d) == set(supported)
             and all(type(d[k]) is type(v) and d[k] == v for k, v in supported.items()), "signed_contract")
        for value, definition in ((l, "ledger"), (r, "request"), (e, "expected")):
            _checked(value, definition, s, "signed_shape", "signed_snapshot")
        need(all(_hex(v) for v in e.values()), "signed_expected")
        need(schema_digest == e["schema_sha256"] == r["request_contract_sha256"], "signed_schema_identity")
        need(_hash(r) == e["request_sha256"] and _hash(r["policy"]) == e["policy_sha256"]
             and _hash(l) == e["ledger_snapshot_sha256"], "signed_original_binding")
        need(all(_hex(v) for v in r["domain"].values()) and all(_hex(v) for v in r["source_binding"].values())
             and _hex(r["invocation_id"]), "signed_digest")
        q, op = r["policy"], r["operation"]
        need(all(type(v) is int and 0 <= v <= MAX_SAFE for v in [q["fuel"], q["payload"], *op.values()])
             and q["fuel"] + 1 <= MAX_SAFE and 2 * q["fuel"] <= MAX_SAFE
             and 2 * 65536 * q["fuel"] <= MAX_SAFE
             and 0 < op["maximum_duration_ms"] <= MAX_SAFE
             and 0 < op["copy_node_limit"] <= 65536 and 0 < op["copy_scalar_limit"] <= 65536, "signed_units")
        start, end = timestamp(r["validity"]["not_before"]), timestamp(r["validity"]["expires_at"])
        need(start < end, "signed_validity")
        entries = l["entries"]
        need(0 < len(entries) <= d["maximum_entries"], "signed_chain_budget")
        scope = d["scope_prefix"] + e["request_sha256"]
        previous_matching_created = None
        for row in entries:
            created_in_chain = timestamp(row["created_at"])
            if row["gate"] == d["gate"] and row["scope"] == scope:
                need(previous_matching_created is None or previous_matching_created <= created_in_chain, "signed_decision_order")
                previous_matching_created = created_in_chain
            need(0 < len(row["id"]) <= 128 and len(row["gate"]) <= 128 and len(row["scope"]) <= 256,
                 "signed_entry_budget")
        row = latest_decisions_by_gate_scope(entries).get((d["gate"], scope))
        need(row is not None, "signed_matching_decision_unavailable")
        # A latest unsigned/rejected/revoked row must never fall back to an old
        # signed approval. The old whole-chain compatibility remains untouched.
        need(_hex(row.get("signature")) and _hex(row.get("chain_hash"))
             and row.get("signature_algorithm") == SIGNATURE_ALGORITHM, "signed_selected_row_required")
        need(row["decision"] == "approved" and not row.get("revoked_by"), "signed_latest_not_approved")
        need(row["request_id"] == r["request_id"] and len(row["evidence"]) == 2
             and set(row["evidence"]) == {"request-sha256:" + e["request_sha256"], "policy-sha256:" + e["policy_sha256"]},
             "signed_decision_binding")
        created, expiry = timestamp(row["created_at"]), timestamp(row["expires_at"])
        need(start <= created < expiry <= end, "signed_validity")
        # Snapshot the environment value exactly once, then use the original
        # verification core. There is no fallback into file creation/loading if
        # the environment changes while HMAC runs.
        secret_text = _configured_signing_secret_text()
        need(type(secret_text) is str and bool(secret_text), "signed_native_key_unavailable")
        _measure(secret_text, 1, d["maximum_key_utf8_bytes"])
        secret = secret_text.encode("utf-8")
        integrity = _verify_ledger_entries(entries, secret, "configured_process_key")
        need(integrity["status"] in ("PASS", "PASS_WITH_LEGACY_UNSIGNED"), "signed_chain_invalid")
        checked_at = _utc_now()
        now = timestamp(checked_at)
        need(start <= created <= now < expiry <= end, "signed_expired_or_future")
        out = {"version": d["version"], "scope": d["scope"],
            "status": "SUPPLIED_SELECTED_SIGNED_DECISION_LINKED_NOT_CURRENT_APPROVAL",
            "request_sha256": e["request_sha256"], "policy_sha256": e["policy_sha256"],
            "ledger_snapshot_sha256": e["ledger_snapshot_sha256"], "selected_entry_sha256": _hash(row),
            "selected_chain_hash": row["chain_hash"], "request_id": r["request_id"],
            "selected_signature_verified": True, "latest_in_supplied_snapshot": True,
            "legacy_unsigned_history_present": integrity["legacy_unsigned_entries"] > 0, "whole_supplied_chain_checked": True,
            "validity_checked_at": checked_at, "selected_expires_at": row["expires_at"],
            "spend_identity": {"domain_sha256": _hash(r["domain"]), "original_request_sha256": e["request_sha256"],
                               "decision_revision_in_key": False},
            "logical_budget": {"input_nodes": nodes, "input_scalar_upper_bytes": scalar,
                "copy_scalar_reserved_bytes": (2 + d["maximum_entries"]) * C + d["maximum_key_utf8_bytes"],
                "copy_node_slots_reserved": (2 + d["maximum_entries"]) * N,
                "canonical_forms_reserved_bytes": 3 * (d["new_canonical_form_slots"] + d["maximum_entries"]) * K
                    + 129 * d["maximum_entries"],
                "maximum_hmac_verifications": d["maximum_entries"], "key_utf8_bytes": len(secret)},
            "flags": {k: False for k in s["$defs"]["result"]["properties"]["flags"]["properties"]},
            "full_work_units": None, "physical_fit": None}
        _measure(out, N, C)
        _checked(out, "result", s, "signed_output", "signed_result")
        need(len(_canonical(out)) <= d["output_bytes"], "signed_output_budget")
        return out
    except SelectedSignedDecisionRefusal:
        raise
    except (EntryComparisonProjectionRefusal, TextSelectionRefusal) as error:
        raise SelectedSignedDecisionRefusal("signed_metadata_or_budget") from error
    except (KeyError, TypeError, ValueError, AttributeError, IndexError, RecursionError, OverflowError, RegexError) as error:
        raise SelectedSignedDecisionRefusal("signed_metadata_or_schema") from error
    finally:
        secret_text = secret = None  # Own aliases cleared; no physical/GC claim.


_SIGNED_PRIMARY_FIXTURE_SCHEMA = Path(__file__).resolve().parents[1] / "config/schemas/comparison_signed_primary_fixture.schema.json"
_SIGNED_PRIMARY_FIXTURE_SCHEMA_SHA256 = "995bd0ffff4f239b08500c47413409b71d121709fcb74fc293eaa68d21c22a7d"


def _signed_primary_fixture_contract():
    # Source compatibility metadata only; no ledger, key or live policy read.
    with _SIGNED_PRIMARY_FIXTURE_SCHEMA.open("rb") as stream:
        raw = stream.read(8193)
    if len(raw) > 8192 or hashlib.sha256(raw).hexdigest() != _SIGNED_PRIMARY_FIXTURE_SCHEMA_SHA256:
        raise SelectedSignedDecisionRefusal("signed_fixture_contract_changed")
    return json.loads(raw.decode("utf8"))["$defs"]["contract"]["const"]


def _signed_primary_fixture_result(contract, fixture, revision, full, expected, read, signed):
    from tools.core.artifact_store_claim_fixture import _receipt
    from tools.core.compiler_text_selection import _canonical

    result = _receipt(contract["result_status"], fixture._binding.domain, revision, full, expected["request_sha256"])
    result.update(version=contract["version"], full_payload_sha256=full,
                  canonical_entries_sha256=expected["ledger_snapshot_sha256"],
                  primary_anchor_kind=fixture._binding.contract["primary_anchor_kind"],
                  declared_request_domain_sha256=signed["spend_identity"]["domain_sha256"],
                  domain_link_authenticated=False,
                  signed_evidence={key: signed[key] for key in contract["selected_result_fields"]},
                  reader={key: read[key] for key in ("status", "storage_mode", "payload_bytes", "parts", "io_observation", "logical_cuts")})
    # A failed result publication after known commit cannot refund consumption.
    if len(_canonical(result)) > contract["maximum_result_bytes"]:
        raise SelectedSignedDecisionRefusal("signed_fixture_output_budget")
    return result


class SignedPrimaryComparisonFixture:
    """Disabled synthetic signed-content/spend owner; never native permission.

    Private Python members are cooperative ownership, not a hostile-code sandbox.
    The underlying structural fixture keeps its original public meaning.
    """

    def __init__(self):
        raise SelectedSignedDecisionRefusal("use_signed_fixture_factory")

    def _check(self):
        from tools.core.artifact_store_claim_fixture import ComparisonClaimFixture
        if type(self) is not SignedPrimaryComparisonFixture or type(getattr(self, "_fixture", None)) is not ComparisonClaimFixture:
            raise SelectedSignedDecisionRefusal("signed_fixture_binding_invalid")
        return self._fixture._mode(True)

    def clone(self):
        self._check()
        view = object.__new__(SignedPrimaryComparisonFixture)
        view._fixture, view._contract = self._fixture.clone(), self._contract
        return view

    def consume_signed_fixture_claim(self, request, expected, schema, expected_revision: int,
                                     expected_full_payload_sha256: str, expected_fixture_domain_sha256: str) -> dict:
        """Check the exact held entries with the original owner before insertion.

        Expected digest values are captured before entry; bounded request/schema
        capture belongs to the original projector inside this transaction. Neither
        caller object is read again after that projector returns its owned result.
        """
        from tools.core.artifact_store_claim_fixture import _digest, _revision, ComparisonClaimFixtureRefusal
        from tools.core.json_io import read_comparison_primary_snapshot_from_connection

        binding = self._check()
        fields = self._contract["expected_fields"]
        if type(expected) is not dict or len(expected) != len(fields) or any(type(k) is not str for k in expected) or set(expected) != set(fields):
            raise SelectedSignedDecisionRefusal("signed_fixture_expected_invalid")
        held = {key: _digest(expected[key]) for key in fields}
        full, domain = _digest(expected_full_payload_sha256), _digest(expected_fixture_domain_sha256)
        revision = _revision(expected_revision, binding.contract["maximum_revision"])
        if domain != binding.domain:
            raise ComparisonClaimFixtureRefusal("fixture_domain_mismatch")
        entries, original_request = held["ledger_snapshot_sha256"], held["request_sha256"]
        with self._fixture._transaction() as connection:
            if self._fixture._anchor(connection) != (revision, full):
                raise ComparisonClaimFixtureRefusal("stale_primary_anchor")
            self._fixture._claim_available(connection, domain, original_request)
            read = read_comparison_primary_snapshot_from_connection(connection, {
                "full_payload_sha256": full, "canonical_entries_sha256": entries,
            })
            if read["full_payload_identity"] != "sqlite:" + full or read["canonical_entries_sha256"] != entries:
                raise ComparisonClaimFixtureRefusal("reader_identity_mismatch")
            signed = project_comparison_signed_decision(read["entries_projection"], request, held, schema)
            if (signed["status"] != self._contract["selected_status"]
                    or any(signed[key] is not True for key in self._contract["required_selected_booleans"])
                    or any(signed[key] != held[key] for key in ("request_sha256", "policy_sha256", "ledger_snapshot_sha256"))
                    or signed["spend_identity"]["original_request_sha256"] != original_request):
                raise SelectedSignedDecisionRefusal("signed_fixture_result_mismatch")
            self._fixture._insert_primary_claim(connection, domain, original_request, revision, full, entries)
        return _signed_primary_fixture_result(self._contract, self._fixture, revision, full, held, read, signed)

    def replace_fixture_payload(self, expected_revision: int, expected_full_payload_sha256: str,
                                next_payload_utf8: bytes, *, part_bytes: int | None = None):
        self._check()
        return self._fixture.replace_primary_fixture_payload(expected_revision, expected_full_payload_sha256,
                                                              next_payload_utf8, part_bytes=part_bytes)

    def inspect_fixture(self):
        self._check()
        return self._fixture.inspect_fixture()

    def close(self):
        if type(self) is not SignedPrimaryComparisonFixture:
            raise SelectedSignedDecisionRefusal("signed_fixture_binding_invalid")
        self._fixture.close()

    def __enter__(self):
        self._check()
        self._fixture.__enter__()
        return self

    def __exit__(self, *_):
        self.close()


def create_signed_primary_comparison_fixture(initial_payload_utf8: bytes, *, part_bytes: int | None = None):
    """Internally mint a temporary fixture only; accepts no key or live DB path."""
    from tools.core.artifact_store_claim_fixture import create_primary_comparison_claim_fixture
    contract = _signed_primary_fixture_contract()
    result = object.__new__(SignedPrimaryComparisonFixture)
    result._contract = contract
    result._fixture = create_primary_comparison_claim_fixture(initial_payload_utf8, part_bytes=part_bytes)
    return result


def _entry_id(gate: str, decision: str, scope: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    raw = f"{gate}-{decision}-{scope}"[:80]
    safe = "".join(ch.lower() if ch.isalnum() else "_" for ch in raw).strip("_") or "approval"
    return f"{stamp}_{safe}"


def _summarize(entries: list[dict[str, Any]]) -> dict[str, Any]:
    return summarize_ledger_entries(entries)


def _write_ledger(ledger: dict[str, Any]) -> dict[str, Any]:
    entries = ledger.get("entries", [])
    ledger["meta"]["updated_at"] = _utc_now()
    ledger["summary"] = _summarize(entries if isinstance(entries, list) else [])
    ledger["integrity"] = verify_ledger(ledger)
    save_json_atomic(LEDGER_PATH, ledger)
    save_text_atomic(REPORT_PATH, render_report(ledger))
    return ledger


def record_decision(
    gate: str,
    decision: str,
    scope: str,
    actor: str,
    rationale: str,
    evidence: list[str] | None = None,
    expires_at: str = "",
    request_id: str = "",
) -> dict[str, Any]:
    decision = _safe_text(decision).lower()
    valid_decisions = _valid_decisions()
    if decision not in valid_decisions:
        raise ValueError(f"decision must be one of: {', '.join(sorted(valid_decisions))}")
    gate = _safe_text(gate)
    scope = _safe_text(scope)
    if not gate:
        raise ValueError("gate is required")
    if not scope:
        raise ValueError("scope is required")

    ledger = _load_ledger()
    integrity = verify_ledger(ledger)
    if integrity.get("status") == "FAIL":
        raise ValueError(f"HITL ledger integrity check failed: {integrity.get('invalid_entries')}")
    prev_chain_hash = str(integrity.get("last_chain_hash") or GENESIS_CHAIN_HASH)
    entry = {
        "id": _entry_id(gate, decision, scope),
        "created_at": _utc_now(),
        "gate": gate,
        "decision": decision,
        "scope": scope,
        "actor": _safe_text(actor) or "human",
        "rationale": _safe_text(rationale),
        "evidence": [item for item in (evidence or []) if _safe_text(item)],
        "expires_at": _safe_text(expires_at) or None,
        "request_id": _safe_text(request_id) or None,
    }
    _sign_entry(entry, prev_chain_hash)
    ledger["entries"].append(entry)
    _write_ledger(ledger)
    return entry


def render_report(ledger: dict[str, Any]) -> str:
    summary = ledger.get("summary", {})
    lines = [
        "# HITL Approval Ledger",
        "",
        f"- updated_at: `{ledger.get('meta', {}).get('updated_at')}`",
        f"- entries: `{summary.get('entries')}`",
        f"- active_approvals: `{summary.get('active_approvals')}`",
        f"- effective_active_approvals: `{summary.get('effective_active_approvals')}`",
        f"- approved: `{summary.get('approved')}`",
        f"- expired_approvals: `{summary.get('expired_approvals')}`",
        f"- rejected: `{summary.get('rejected')}`",
        f"- deferred: `{summary.get('deferred')}`",
        f"- revoked: `{summary.get('revoked')}`",
        f"- integrity: `{(ledger.get('integrity') or {}).get('status')}`",
        f"- signed_entries: `{(ledger.get('integrity') or {}).get('signed_entries')}`",
        f"- legacy_unsigned_entries: `{(ledger.get('integrity') or {}).get('legacy_unsigned_entries')}`",
        "",
        "## Entries",
        "",
    ]
    entries = ledger.get("entries", [])
    if not entries:
        lines.append("- No approval decisions recorded yet.")
        return "\n".join(lines) + "\n"

    lines.extend(["| Created | Gate | Decision | Scope | Actor | Rationale | Evidence |", "|---|---|---|---|---|---|---|"])
    for entry in entries:
        evidence = "<br>".join(f"`{item}`" for item in entry.get("evidence", []))
        lines.append(
            f"| `{entry.get('created_at')}` | `{entry.get('gate')}` | `{entry.get('decision')}` | `{entry.get('scope')}` | `{entry.get('actor')}` | {entry.get('rationale') or ''} | {evidence} |"
        )
    return "\n".join(lines) + "\n"


def init_ledger() -> dict[str, Any]:
    ledger = _load_ledger()
    return _write_ledger(ledger)


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage the Human-in-the-Loop approval ledger.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Create or refresh the ledger report.")
    init_parser.set_defaults(func=lambda args: init_ledger())

    verify_parser = subparsers.add_parser("verify", help="Verify HMAC signatures and the ledger hash chain.")
    verify_parser.set_defaults(func=lambda args: verify_ledger_observation())

    record_parser = subparsers.add_parser("record", help="Record a human approval decision.")
    record_parser.add_argument("--gate", required=True, help="Approval gate id, e.g. mutation_scripts.")
    record_parser.add_argument("--decision", required=True, choices=sorted(_valid_decisions()))
    record_parser.add_argument("--scope", required=True, help="Action/file/repo scope covered by the decision.")
    record_parser.add_argument("--actor", default="human", help="Human approver/reviewer name.")
    record_parser.add_argument("--rationale", default="", help="Reason for the decision.")
    record_parser.add_argument("--evidence", action="append", default=[], help="Evidence artifact path; repeatable.")
    record_parser.add_argument("--expires-at", default="", help="Optional ISO timestamp/date for approval expiry.")
    record_parser.add_argument("--request-id", default="", help="Optional HITL decision request id this decision resolves.")
    record_parser.set_defaults(
        func=lambda args: record_decision(
            gate=args.gate,
            decision=args.decision,
            scope=args.scope,
            actor=args.actor,
            rationale=args.rationale,
            evidence=args.evidence,
            expires_at=args.expires_at,
            request_id=args.request_id,
        )
    )

    args = parser.parse_args()
    result = args.func(args)
    if isinstance(result, dict) and result.get("entries") is not None:
        print(json.dumps(result.get("summary", {}), ensure_ascii=False))
    elif isinstance(result, dict) and result.get("invalid_entries") is not None:
        print(json.dumps(result, ensure_ascii=False))
        return 0 if str(result.get("status", "")).startswith("PASS") else 1
    else:
        print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
