"""Isolated synthetic persistence proof; no live ArtifactStore or approval route.

Factories mint temporary database bindings only. V1 keeps its opaque anchor;
the explicit primary fixture binds a serialized payload and separate entries.
Neither mode authenticates a ledger, signed decision or runtime authority.
Python private members are a cooperative API boundary, not a hostile-code sandbox.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path

from tools.core import db as sqlite_owner


_SCHEMA = Path(__file__).resolve().parents[2] / "config/schemas/artifact_store_claim_fixture.schema.json"
_CONTRACT_SHA256 = "35daf2b8171cb340e9e4e3c56835c51086789dd1b05f2d9fd615e290951873da"


class ComparisonClaimFixtureRefusal(ValueError):
    """Fixture refused without issuing any runtime permission."""


class ComparisonClaimFixtureCommitUnknown(RuntimeError):
    """Commit or finalization did not return normally; no retry/handle is issued."""


def _digest(value: str) -> str:
    if type(value) is not str or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ComparisonClaimFixtureRefusal("invalid_sha256")
    return value


def _revision(value: int, maximum: int) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise ComparisonClaimFixtureRefusal("invalid_revision")
    return value


def _contract(primary: bool = False) -> dict:
    with _SCHEMA.open("rb") as stream:
        raw = stream.read(8193)
    if len(raw) > 8192 or hashlib.sha256(raw).hexdigest() != _CONTRACT_SHA256:
        raise ComparisonClaimFixtureRefusal("fixture_contract_changed")
    definitions = json.loads(raw.decode("utf-8"))["$defs"]
    contract = definitions["contract"]["const"]
    if primary:
        contract.update(definitions["composition_contract"]["const"])
    return contract


def _receipt(status: str, domain: str, revision: int, snapshot: str, request: str | None = None) -> dict:
    return {
        "status": status, "fixture_only": True,
        "authority_domain_sha256": domain, "decision_revision": revision,
        "synthetic_snapshot_sha256": snapshot, "original_request_sha256": request,
        "flags": {name: False for name in (
            "approval_verified", "human_origin_authenticated", "key_origin_authenticated",
            "current_primary_revision_verified", "live_single_use_consumed", "issuer_token_created",
            "runtime_admission", "production_enabled", "publication_authority", "complete_cost_priced",
        )},
    }


class _Binding:
    def __init__(self, directory: tempfile.TemporaryDirectory, contract: dict):
        self.directory = directory
        self.path = Path(directory.name).resolve() / contract["database_filename"]
        self.domain = hashlib.sha256(("synthetic-fixture-only:" + uuid.uuid4().hex).encode("ascii")).hexdigest()
        self.contract = contract
        self.closed = False
        self.outcome_unknown = False
        self.guard = threading.RLock()
        self.active = False


class ComparisonClaimFixture:
    """Views on one internally created temporary fixture; no caller DB argument."""

    def __init__(self):
        raise ComparisonClaimFixtureRefusal("use_fixture_factory")

    def _check(self) -> _Binding:
        if type(self) is not ComparisonClaimFixture or type(getattr(self, "_binding", None)) is not _Binding:
            raise ComparisonClaimFixtureRefusal("invalid_fixture_binding")
        binding = self._binding
        if binding.closed or not binding.path.is_file():
            raise ComparisonClaimFixtureRefusal("fixture_closed_or_missing")
        if sqlite_owner.get_current_tenant() is not None:
            raise ComparisonClaimFixtureRefusal("ambient_tenant_not_allowed")
        if sqlite_owner._ambient_connections.get():
            raise ComparisonClaimFixtureRefusal("ambient_transaction_not_allowed")
        if self._manager.base_db_path != binding.path or self._manager.db_path != binding.path:
            raise ComparisonClaimFixtureRefusal("fixture_binding_changed")
        return binding

    def clone(self) -> ComparisonClaimFixture:
        """Another view for concurrency controls, never another database opener."""
        with self._operation() as binding:
            result = object.__new__(ComparisonClaimFixture)
            result._binding = binding
            result._manager = sqlite_owner.SQLiteManager(binding.path)
        return result

    @contextmanager
    def _operation(self, *, write: bool = False):
        # Recheck after admission to the shared guard, including callers which
        # queued before an uncertain commit. RLock permits explicit nested refusal
        # instead of same-thread deadlock; it is not a second DB transaction.
        self._check()
        with self._binding.guard:
            binding = self._check()
            if binding.active:
                raise ComparisonClaimFixtureRefusal("nested_fixture_operation")
            if write and binding.outcome_unknown:
                raise ComparisonClaimFixtureRefusal("fixture_outcome_unknown_read_only")
            binding.active = True
            try:
                yield binding
            finally:
                binding.active = False

    @contextmanager
    def _transaction(self):
        with self._operation(write=True) as binding:
            body_finished, body_error = False, None
            try:
                with self._manager.get_connection() as connection:
                    try:
                        if connection.in_transaction:
                            raise ComparisonClaimFixtureRefusal("transaction_not_owned")
                        connection.execute(binding.contract["lock_statement"])
                        yield connection
                        body_finished = True
                    except BaseException as error:
                        body_error = error
                        raise
            except BaseException as error:
                # Publish BEFORE releasing guard; queued writers cannot pass on
                # an earlier quarantine check. A replaced rollback/close error
                # is uncertain too. Plain body errors with known rollback retain
                # their original error and are retryable with unchanged state.
                if body_finished or (body_error is not None and error is not body_error):
                    binding.outcome_unknown = True
                    raise ComparisonClaimFixtureCommitUnknown("fixture_commit_or_finalization_unknown_no_retry") from error
                raise

    def _mode(self, primary: bool) -> _Binding:
        binding = self._check()
        if (binding.contract.get("primary_payload_storage") is True) != primary:
            raise ComparisonClaimFixtureRefusal("fixture_mode_mismatch")
        return binding

    def _claim_available(self, connection, domain: str, request: str) -> None:
        if connection.execute(
            "SELECT 1 FROM fixture_claims WHERE authority_domain_sha256 = ? AND original_request_sha256 = ?",
            (domain, request),
        ).fetchone() is not None:
            raise ComparisonClaimFixtureRefusal("already_consumed")
        if connection.execute("SELECT COUNT(*) FROM fixture_claims").fetchone()[0] >= self._binding.contract["maximum_claims"]:
            raise ComparisonClaimFixtureRefusal("fixture_claim_capacity")

    def _anchor(self, connection) -> tuple[int, str]:
        row = connection.execute(
            "SELECT version, authority_domain_sha256, revision, snapshot_sha256 FROM fixture_primary WHERE singleton = 1"
        ).fetchone()
        binding = self._binding
        if row is None or row["version"] != binding.contract["version"] or row["authority_domain_sha256"] != binding.domain:
            raise ComparisonClaimFixtureRefusal("fixture_primary_identity_mismatch")
        return _revision(row["revision"], binding.contract["maximum_revision"]), _digest(row["snapshot_sha256"])

    def consume_fixture_claim(self, original_request_sha256: str, expected_revision: int,
                              expected_snapshot_sha256: str, expected_domain_sha256: str) -> dict:
        """Consume only synthetic persistence identity; never validate approval."""
        binding = self._mode(False)
        request, snapshot, domain = map(_digest, (original_request_sha256, expected_snapshot_sha256, expected_domain_sha256))
        revision = _revision(expected_revision, binding.contract["maximum_revision"])
        if domain != binding.domain:
            raise ComparisonClaimFixtureRefusal("fixture_domain_mismatch")
        with self._transaction() as connection:
            current_revision, current_snapshot = self._anchor(connection)
            if current_revision != revision:
                raise ComparisonClaimFixtureRefusal("stale_revision")
            if current_snapshot != snapshot:
                raise ComparisonClaimFixtureRefusal("snapshot_mismatch")
            self._claim_available(connection, domain, request)
            connection.execute(
                "INSERT INTO fixture_claims (authority_domain_sha256, original_request_sha256, decision_revision, snapshot_sha256) VALUES (?, ?, ?, ?)",
                (domain, request, revision, snapshot),
            )
        # SQLiteManager has now committed and finalized the owned connection.
        # Failure allocating this result cannot roll back or refund the claim.
        return _receipt("CONSUMED_FIXTURE_ONLY_NOT_ADMITTED", domain, revision, snapshot, request)

    def advance_fixture_primary(self, expected_revision: int, expected_snapshot_sha256: str,
                                next_snapshot_sha256: str) -> dict:
        """CAS a synthetic anchor; this is not a HITL decision writer."""
        binding = self._mode(False)
        revision = _revision(expected_revision, binding.contract["maximum_revision"])
        snapshot, next_snapshot = map(_digest, (expected_snapshot_sha256, next_snapshot_sha256))
        if revision == binding.contract["maximum_revision"]:
            raise ComparisonClaimFixtureRefusal("revision_exhausted")
        with self._transaction() as connection:
            self._advance_anchor(connection, revision, snapshot, next_snapshot)
        return _receipt("ADVANCED_SYNTHETIC_ANCHOR_NOT_APPROVAL", binding.domain, revision + 1, next_snapshot)

    def _advance_anchor(self, connection, revision, snapshot, next_snapshot):
        if self._anchor(connection) != (revision, snapshot):
            raise ComparisonClaimFixtureRefusal("stale_primary_anchor")
        changed = connection.execute(
            "UPDATE fixture_primary SET revision = ?, snapshot_sha256 = ? WHERE singleton = 1 AND revision = ? AND snapshot_sha256 = ?",
            (revision + 1, next_snapshot, revision, snapshot),
        ).rowcount
        if changed != 1:
            raise ComparisonClaimFixtureRefusal("stale_primary_anchor")

    def consume_primary_fixture_claim(self, original_request_sha256: str, expected_revision: int,
                                      expected_full_payload_sha256: str, expected_entries_sha256: str,
                                      expected_domain_sha256: str) -> dict:
        """Link bounded content to one synthetic spend, never signed approval."""
        from tools.core.json_io import read_comparison_primary_snapshot_from_connection

        binding = self._mode(True)
        request, full, entries, domain = map(_digest, (original_request_sha256, expected_full_payload_sha256,
                                                     expected_entries_sha256, expected_domain_sha256))
        revision = _revision(expected_revision, binding.contract["maximum_revision"])
        if domain != binding.domain:
            raise ComparisonClaimFixtureRefusal("fixture_domain_mismatch")
        with self._transaction() as connection:
            if self._anchor(connection) != (revision, full):
                raise ComparisonClaimFixtureRefusal("stale_primary_anchor")
            self._claim_available(connection, domain, request)
            read = read_comparison_primary_snapshot_from_connection(connection, {
                "full_payload_sha256": full, "canonical_entries_sha256": entries,
            })
            if read["full_payload_identity"] != "sqlite:" + full or read["canonical_entries_sha256"] != entries:
                raise ComparisonClaimFixtureRefusal("reader_identity_mismatch")
            self._insert_primary_claim(connection, domain, request, revision, full, entries)
        # No entries tree escapes and no public connection-owning consume is
        # nested. Metadata publication happens only after known finalization.
        result = _receipt("PRIMARY_CONTENT_AND_CONSUMPTION_LINKED_FIXTURE_ONLY_NOT_ADMITTED", domain, revision, full, request)
        result.update(full_payload_sha256=full, canonical_entries_sha256=entries,
                      primary_anchor_kind=binding.contract["primary_anchor_kind"],
                      reader={k: read[k] for k in ("status", "storage_mode", "payload_bytes", "parts", "io_observation", "logical_cuts")})
        return result

    def _insert_primary_claim(self, connection, domain, request, revision, full, entries):
        """Fixed connection-local step, only inside an owned fixture transaction."""
        connection.execute(
            "INSERT INTO fixture_claims (authority_domain_sha256, original_request_sha256, decision_revision, snapshot_sha256, canonical_entries_sha256) VALUES (?, ?, ?, ?, ?)",
            (domain, request, revision, full, entries),
        )

    def replace_primary_fixture_payload(self, expected_revision: int, expected_full_payload_sha256: str,
                                        next_payload_utf8: bytes, *, part_bytes: int | None = None) -> dict:
        """Replace fixture body/parts and full anchor+revision atomically."""
        binding = self._mode(True)
        revision = _revision(expected_revision, binding.contract["maximum_revision"])
        full = _digest(expected_full_payload_sha256)
        if revision == binding.contract["maximum_revision"]:
            raise ComparisonClaimFixtureRefusal("revision_exhausted")
        prepared = _prepare_primary(next_payload_utf8, part_bytes)
        with self._transaction() as connection:
            if self._anchor(connection) != (revision, full):
                raise ComparisonClaimFixtureRefusal("stale_primary_anchor")
            _store_primary(connection, prepared)
            self._advance_anchor(connection, revision, full, prepared["full"])
        return _receipt("REPLACED_PRIMARY_FIXTURE_PAYLOAD_NOT_APPROVAL", binding.domain, revision + 1, prepared["full"])

    def compare_and_replace_primary_fixture(
        self, expected_revision: int, expected_full_payload_sha256: str,
        expected_entries_sha256: str, expected_domain_sha256: str,
        next_payload_utf8: bytes, next_entries_sha256: str, *, part_bytes: int | None = None,
    ) -> dict:
        """Compose strict reads and original storage SQL on one owned temp DB.

        No external connection/manager/callback is accepted. The fixture domain
        is a synthetic lifetime, never authenticated native DB generation.
        This replacement proves no approval, append semantics or live admission.
        """
        from io import BytesIO
        from tools.core.artifact_store import ArtifactStore
        from tools.core.json_io import read_comparison_primary_snapshot_from_connection

        binding = self._mode(True)
        full, entries, domain, next_entries = map(_digest, (
            expected_full_payload_sha256, expected_entries_sha256,
            expected_domain_sha256, next_entries_sha256,
        ))
        revision = _revision(expected_revision, binding.contract["maximum_revision"])
        if domain != binding.domain:
            raise ComparisonClaimFixtureRefusal("fixture_domain_mismatch")
        if revision == binding.contract["maximum_revision"]:
            raise ComparisonClaimFixtureRefusal("revision_exhausted")
        with self._transaction() as connection:
            if self._anchor(connection) != (revision, full):
                raise ComparisonClaimFixtureRefusal("stale_primary_anchor")
            current = read_comparison_primary_snapshot_from_connection(connection, {
                "full_payload_sha256": full, "canonical_entries_sha256": entries,
            })
            prior_read = {k: current[k] for k in ("storage_mode", "payload_bytes", "parts", "io_observation", "logical_cuts")}
            del current  # Drop this owned tree alias, not a physical-erasure claim.
            prepared = _prepare_primary(next_payload_utf8, part_bytes)
            store = ArtifactStore(raw_dir=binding.path.parent)
            store.db_manager = self._manager
            with BytesIO(next_payload_utf8) as spool:
                part_count = store._write_prepared_state_payload_on_connection(
                    connection, prepared["name"], spool,
                    storage_mode=prepared["mode"], generation_id=prepared["generation"] or "",
                    payload_sha=prepared["full"], serialized_bytes_count=prepared["bytes"],
                    part_size=part_bytes if part_bytes is not None else prepared["bytes"],
                    source_mtime=0.0,  # Synthetic fixture metadata, not approval time.
                )
            written = read_comparison_primary_snapshot_from_connection(connection, {
                "full_payload_sha256": prepared["full"], "canonical_entries_sha256": next_entries,
            })
            written_read = {k: written[k] for k in ("storage_mode", "payload_bytes", "parts", "io_observation", "logical_cuts")}
            del written
            self._advance_anchor(connection, revision, full, prepared["full"])
        # This is outside the owned transaction/finalization. Publication cannot
        # roll back a committed replacement or justify another stale attempt.
        result = _receipt("PRIMARY_STORAGE_CAS_COMPOSED_FIXTURE_ONLY_NOT_ADMITTED",
                          domain, revision + 1, prepared["full"])
        result.update(full_payload_sha256=prepared["full"], canonical_entries_sha256=next_entries,
                      previous_revision=revision, previous_full_payload_sha256=full,
                      prior_reader=prior_read, written_reader=written_read, written_parts=part_count,
                      synthetic_domain_only=True, native_db_generation_authenticated=False)
        return result

    def inspect_fixture(self) -> dict:
        """Read-only reconciliation metadata; it cannot reissue a claim."""
        with self._operation() as binding:
            with self._manager.get_read_only_connection() as connection:
                connection.execute("BEGIN")
                revision, snapshot = self._anchor(connection)
                extra = ", canonical_entries_sha256" if binding.contract.get("primary_payload_storage") else ""
                rows = connection.execute(
                    "SELECT authority_domain_sha256, original_request_sha256, decision_revision, snapshot_sha256" + extra + " FROM fixture_claims ORDER BY original_request_sha256 LIMIT ?",
                    (binding.contract["maximum_claims"] + 1,),
                ).fetchall()
                if len(rows) > binding.contract["maximum_claims"]:
                    raise ComparisonClaimFixtureRefusal("fixture_claim_capacity")
                claims = [dict(row) for row in rows]
        return {**_receipt("FIXTURE_INSPECTED_NOT_ADMITTED", binding.domain, revision, snapshot), "claims": claims}

    def close(self) -> None:
        """Closing any view closes the whole fixture; it never reopens."""
        binding = self._binding
        with binding.guard:
            if binding.active:
                raise ComparisonClaimFixtureRefusal("nested_fixture_operation")
            if not binding.closed:
                binding.closed = True
                binding.directory.cleanup()

    def __enter__(self):
        with self._operation():
            pass
        return self

    def __exit__(self, *_):
        self.close()


def create_comparison_claim_fixture(initial_snapshot_sha256: str) -> ComparisonClaimFixture:
    """Mint a fresh temporary synthetic DB. Accept no external path or manager."""
    return _create_fixture(_digest(initial_snapshot_sha256), _contract())


def create_primary_comparison_claim_fixture(initial_payload_utf8: bytes, *, part_bytes: int | None = None) -> ComparisonClaimFixture:
    """Explicit fixed primary fixture; no live path, host function or verifier."""
    contract = _contract(primary=True)
    prepared = _prepare_primary(initial_payload_utf8, part_bytes)
    return _create_fixture(prepared["full"], contract, prepared)


def _prepare_primary(raw: bytes, part_bytes: int | None) -> dict:
    from tools.core.json_io import _primary_snapshot_contract

    limits = _primary_snapshot_contract()
    if type(raw) is not bytes or not 0 < len(raw) <= limits["maximum_payload_bytes"]:
        raise ComparisonClaimFixtureRefusal("fixture_payload_bytes_invalid")
    if part_bytes is not None and (type(part_bytes) is not int or not 0 < part_bytes <= limits["maximum_part_bytes"]
                                  or (len(raw) + part_bytes - 1) // part_bytes > limits["maximum_parts"]):
        raise ComparisonClaimFixtureRefusal("fixture_partition_size_invalid")
    text = raw.decode("utf-8", errors="strict")
    full = hashlib.sha256(raw).hexdigest()
    generation = "payload-" + uuid.uuid4().hex if part_bytes is not None else None
    parts = tuple(raw[i:i + part_bytes] for i in range(0, len(raw), part_bytes)) if part_bytes is not None else ()
    mode = "partitioned_json_v1" if parts else "inline_json"
    payload = json.dumps({"__sage_partitioned_payload__": {
        "format": mode, "generation_id": generation, "part_count": len(parts), "part_size_bytes": part_bytes,
        "payload_bytes": len(raw), "payload_sha256": full,
    }}, separators=(",", ":")) if parts else text
    return {"name": limits["artifact_id"], "payload": payload, "full": full, "bytes": len(raw),
            "mode": mode, "generation": generation, "parts": parts}


def _store_primary(connection, prepared):
    # Fixed fixture writer only, never the live ArtifactStore route. Delete old
    # parts before replacing their parent; rollback covers body AND anchor.
    connection.execute("DELETE FROM state_payload_parts WHERE name = ?", (prepared["name"],))
    connection.execute("DELETE FROM state_payloads WHERE name = ?", (prepared["name"],))
    connection.execute(
        "INSERT INTO state_payloads (name,payload,payload_sha,payload_bytes,storage_mode,generation_id,part_count) VALUES (?,?,?,?,?,?,?)",
        (prepared["name"], prepared["payload"], prepared["full"], prepared["bytes"], prepared["mode"], prepared["generation"], len(prepared["parts"])),
    )
    for index, part in enumerate(prepared["parts"]):
        connection.execute("INSERT INTO state_payload_parts VALUES (?,?,?,?,?,?)",
                           (prepared["name"], prepared["generation"], index, part, len(part), hashlib.sha256(part).hexdigest()))


def _create_fixture(snapshot, contract, prepared=None):
    if sqlite_owner.get_current_tenant() is not None or sqlite_owner._ambient_connections.get():
        raise ComparisonClaimFixtureRefusal("ambient_context_not_allowed")
    directory = tempfile.TemporaryDirectory(prefix="sage-comparison-claim-fixture-")
    binding = _Binding(directory, contract)
    result = object.__new__(ComparisonClaimFixture)
    result._binding = binding
    result._manager = sqlite_owner.SQLiteManager(binding.path)
    try:
        with result._manager.get_connection() as connection:
            connection.execute(contract["lock_statement"])
            connection.execute(
                f"CREATE TABLE fixture_primary (singleton INTEGER PRIMARY KEY CHECK(singleton = 1), version TEXT NOT NULL, authority_domain_sha256 TEXT NOT NULL UNIQUE, revision INTEGER NOT NULL CHECK(revision >= 0 AND revision <= {contract['maximum_revision']}), snapshot_sha256 TEXT NOT NULL)"
            )
            extra = ", canonical_entries_sha256 TEXT NOT NULL" if prepared is not None else ""
            connection.execute(
                "CREATE TABLE fixture_claims (authority_domain_sha256 TEXT NOT NULL, original_request_sha256 TEXT NOT NULL, decision_revision INTEGER NOT NULL, snapshot_sha256 TEXT NOT NULL" + extra + ", PRIMARY KEY(authority_domain_sha256, original_request_sha256), FOREIGN KEY(authority_domain_sha256) REFERENCES fixture_primary(authority_domain_sha256))"
            )
            connection.execute(
                "INSERT INTO fixture_primary VALUES (1, ?, ?, 0, ?)", (contract["version"], binding.domain, snapshot)
            )
            if prepared is not None:
                connection.execute("CREATE TABLE state_payloads (name TEXT PRIMARY KEY, payload TEXT NOT NULL, payload_sha TEXT, payload_bytes INTEGER, storage_mode TEXT NOT NULL DEFAULT 'inline_json', generation_id TEXT, part_count INTEGER NOT NULL DEFAULT 0, source_mtime REAL, updated_at TEXT DEFAULT CURRENT_TIMESTAMP)")
                connection.execute("CREATE TABLE state_payload_parts (name TEXT NOT NULL, generation_id TEXT NOT NULL, part_index INTEGER NOT NULL, payload BLOB NOT NULL, payload_bytes INTEGER NOT NULL, payload_sha TEXT NOT NULL, PRIMARY KEY(name,generation_id,part_index), FOREIGN KEY(name) REFERENCES state_payloads(name) ON DELETE CASCADE)")
                _store_primary(connection, prepared)
    except BaseException:
        result.close()
        raise
    return result
