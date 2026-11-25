"""Explicit, accounted reads for private preservation consumers.

An authority describes the caller's already approved scope; it cannot grant it.
This is not a filesystem sandbox or an interceptor for other readers. Historical
external identities never satisfy a requirement for current environment proof.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import stat
import time
from typing import Mapping

from tools.core.source_layer_classifier import classify_source_layer, is_ignored


class PreservationReadError(ValueError):
    """A refusal with a stable reason, retaining ordinary I/O causes when present."""

    def __init__(self, code: str, path: str = "") -> None:
        self.code = code
        self.path = path
        super().__init__(f"{code}: {path}")


def _relative(value: str) -> str:
    if (not isinstance(value, str) or not value or "\\" in value or ":" in value
            or "\0" in value or PurePosixPath(value).is_absolute()
            or any(part in ("", ".", "..") or part.rstrip(" .") != part
                   for part in value.split("/"))):
        raise PreservationReadError("noncanonical_path", str(value))
    return value


def _identities(values: Mapping[str, str]) -> dict[str, str]:
    result: dict[str, str] = {}
    seen: set[str] = set()
    for path, digest in values.items():
        _relative(path)
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise PreservationReadError("invalid_digest", path)
        if path.casefold() in seen:
            raise PreservationReadError("aliased_path", path)
        seen.add(path.casefold())
        result[path] = digest
    return result


def preservation_domain(root: Path, relative: str) -> str:
    """Metadata-only routing using the governed-source owner's ignore/layer truth.

    Folded spelling additionally prevents Windows case aliases bypassing the
    same canonical classifier; it defines no second ignore or layer table.
    """
    path = root / _relative(relative)
    folded = root / relative.casefold()
    if is_ignored(path, root=root) or is_ignored(folded, root=root):
        return "external_input"
    if any(classify_source_layer(p, root=root)[0] == "generated_runtime_artifact"
           for p in (path, folded)):
        return "evidence_receipt"
    return "governed_source"


def partition_preservation_identities(
    root: Path, identities: Mapping[str, str], *,
    historical_external: Mapping[str, str], receipt_paths: tuple[str, ...],
) -> dict[str, dict[str, str]]:
    """Migrate an inherited inventory without reading any of its file contents.

    Every non-source entry must have an explicit receipt or historical identity.
    Unexplained external entries refuse rather than disappearing from a baseline.
    """
    current, historical = _identities(identities), _identities(historical_external)
    receipts = {_relative(p) for p in receipt_paths}
    if not receipts <= current.keys() or not historical.keys() <= current.keys():
        raise PreservationReadError("unbound_partition_entry")
    result: dict[str, dict[str, str]] = {
        "governed_source": {}, "evidence_receipt": {}, "historical_external": {},
    }
    for path, digest in current.items():
        domain = preservation_domain(root, path)
        if path in historical:
            if domain != "external_input" or path in receipts or historical[path] != digest:
                raise PreservationReadError("historical_identity_mismatch", path)
            result["historical_external"][path] = digest
        elif path in receipts:
            if domain != "evidence_receipt":
                raise PreservationReadError("receipt_domain_mismatch", path)
            result["evidence_receipt"][path] = digest
        elif domain == "governed_source":
            result[domain][path] = digest
        else:
            raise PreservationReadError("unexplained_non_source", path)
    return result


def compare_historical_identities(
    expected: Mapping[str, str], recorded: Mapping[str, str], *,
    receipt_reference: str, require_current: bool = False,
) -> dict[str, object]:
    """Compare receipt metadata only, including exact path sets. Never open inputs."""
    if not isinstance(receipt_reference, str) or not receipt_reference.strip():
        raise PreservationReadError("missing_receipt_reference")
    if _identities(expected) != _identities(recorded):
        raise PreservationReadError("historical_identity_mismatch")
    if require_current:
        raise PreservationReadError("current_external_verification_required")
    return {"status": "HISTORICAL_IDENTITY_MATCH", "receipt_reference": receipt_reference,
            "current_freshness": "unknown", "fresh_external_reads": 0}


@dataclass(frozen=True)
class ReadAuthority:
    """Finite exact scope and budgets supplied by an already authorized caller."""

    principal: str
    scope_id: str
    domain: str
    paths: tuple[str, ...]
    max_files: int
    max_file_bytes: int
    max_total_bytes: int
    authority_reference: str

    def __post_init__(self) -> None:
        if any(not isinstance(v, str) or not v.strip() for v in (
                self.principal, self.scope_id, self.authority_reference)):
            raise PreservationReadError("missing_authority_context")
        if self.domain not in ("governed_source", "evidence_receipt", "external_input"):
            raise PreservationReadError("invalid_domain")
        if not isinstance(self.paths, tuple) or not self.paths:
            raise PreservationReadError("missing_exact_paths")
        for path in self.paths:
            _relative(path)
        if len({p.casefold() for p in self.paths}) != len(self.paths):
            raise PreservationReadError("aliased_path")
        if any(type(v) is not int or v <= 0 for v in (
                self.max_files, self.max_file_bytes, self.max_total_bytes)):
            raise PreservationReadError("invalid_budget")


def _fingerprint(info: os.stat_result) -> tuple[int, int, int, int, int]:
    # On Windows, path stat and descriptor stat can expose different historical
    # ctime semantics. Birth time is comparable across those views; compare ctime
    # separately within the same descriptor to retain the change-time check.
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            getattr(info, "st_birthtime_ns", info.st_ctime_ns))


class PreservationReader:
    """One bounded authority ledger. No cached digests or historical fallback.

    Paths are trusted-workspace inputs, with symlink/reparse and ordinary change
    checks. This is not hostile concurrent filesystem/secure-erasure isolation.
    """

    def __init__(self, root: Path, authority: ReadAuthority) -> None:
        if not isinstance(authority, ReadAuthority):
            raise PreservationReadError("missing_authority")
        self._root = Path(root).absolute()
        self._authority = authority
        self._opens = 0
        self._bytes = 0
        self._failures = 0
        self._completed: dict[str, str] = {}
        self._selection_active = self._selection_poisoned = False
        self._selection_completed: dict[str, str] = {}
        self._selection_cleanup_failures = 0
        self._directory_entries: dict[Path, tuple[tuple[int, int, int, int, int], set[str]]] = {}

    def accounting(self) -> dict[str, object]:
        return {"scope_id": self._authority.scope_id, "domain": self._authority.domain,
                "authority_reference": self._authority.authority_reference,
                "open_attempts": self._opens, "raw_read_bytes": self._bytes,
                "completed_hashes": dict(self._completed), "failures": self._failures,
                "coverage": "authorized_payload_reads_only",
                "classification_support_io": "not_instrumented", "other_workflow_io": "unknown"}

    def _checked_path(self, relative: str) -> tuple[Path, os.stat_result]:
        current = self._root
        for part in (None, *relative.split("/")):
            if part is not None:
                # Refuse aliases, including Windows case/trailing-name aliases.
                # Reuse names only while that directory identity/mtime is equal.
                # lstat/reparse and file identity checks still run on every path.
                parent_identity = _fingerprint(current.lstat())
                cached = self._directory_entries.get(current)
                if cached is None or cached[0] != parent_identity:
                    cached = (parent_identity, set(os.listdir(current)))
                    self._directory_entries[current] = cached
                if part not in cached[1]:
                    raise PreservationReadError("noncanonical_or_missing_path", relative)
                current = current / part
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
                raise PreservationReadError("linked_path", relative)
            if current != self._root / relative and not stat.S_ISDIR(info.st_mode):
                raise PreservationReadError("non_directory_parent", relative)
        if not stat.S_ISREG(info.st_mode):
            raise PreservationReadError("non_regular_file", relative)
        return current, info

    def _prepare_payload(self, relative):
        authority = self._authority
        if relative not in authority.paths:
            raise PreservationReadError("path_not_authorized", relative)
        if preservation_domain(self._root, relative) != authority.domain:
            raise PreservationReadError("read_domain_mismatch", relative)
        if self._opens >= authority.max_files:
            raise PreservationReadError("file_budget_exceeded", relative)
        path, before = self._checked_path(relative)
        if before.st_size > authority.max_file_bytes:
            raise PreservationReadError("file_byte_budget_exceeded", relative)
        if self._bytes + before.st_size > authority.max_total_bytes:
            raise PreservationReadError("total_byte_budget_exceeded", relative)
        return path, before

    def _settle_payload(self, relative, opened, descriptor):
        settled = os.fstat(descriptor)
        if (_fingerprint(opened) != _fingerprint(settled)
                or opened.st_ctime_ns != settled.st_ctime_ns):
            raise PreservationReadError("file_changed_during_read", relative)
        _, after = self._checked_path(relative)
        if _fingerprint(opened) != _fingerprint(after):
            raise PreservationReadError("file_changed_after_read", relative)

    def sha256(self, relative: str) -> str:
        """Refuse authority/domain/budget violations before opening file content."""
        if self._selection_active:
            self._selection_poisoned = True
            self._failures += 1
            raise PreservationReadError("selection_capture_order", relative)
        try:
            relative = _relative(relative)
            path, before = self._prepare_payload(relative)
            self._opens += 1  # Failed opens count too; refusal never resets a budget.
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags)
            try:
                opened = os.fstat(descriptor)
                if not stat.S_ISREG(opened.st_mode) or _fingerprint(before) != _fingerprint(opened):
                    raise PreservationReadError("file_changed_before_read", relative)
                digest = hashlib.sha256()
                remaining = opened.st_size
                while remaining:
                    chunk = os.read(descriptor, min(1024 * 1024, remaining))
                    self._bytes += len(chunk)
                    if not chunk:
                        raise PreservationReadError("file_truncated", relative)
                    digest.update(chunk)
                    remaining -= len(chunk)
                self._settle_payload(relative, opened, descriptor)
                result = digest.hexdigest()
                self._completed[relative] = result
                return result
            finally:
                os.close(descriptor)
        except PreservationReadError:
            self._failures += 1
            raise
        except OSError as error:
            self._failures += 1
            raise PreservationReadError("read_io_error", relative) from error

    def selection_accounting(self):
        """Metadata only; legacy hash accounting/result shape remains unchanged."""
        return {"completed_captures": dict(self._selection_completed),
                "cleanup_failures": self._selection_cleanup_failures,
                "read_ledger": self.accounting()}

    def produce_selection_receipt(self, relative, scope, profiles, schemas, *, now=time.monotonic, deadline=None):
        """Fixed private file-to-selection transaction; returns two metadata byte strings.

        Scope records an already granted purpose, not caller authentication. Only
        temporary synthetic files have been verified; this is no production API.
        """
        from tools.core import compiler_text_selection as kernel

        if self._selection_active:
            self._selection_poisoned = True
            self._failures += 1
            raise PreservationReadError("selection_capture_order", relative)
        self._selection_active, self._selection_poisoned = True, False
        raw = chunk = selection_receipt = envelope_bytes = None
        chunks = []
        def need(value, code):
            if not value:
                raise PreservationReadError(code, relative)
        try:
            clock = kernel._Clock(now, deadline)
            def check(stage):
                need(not self._selection_poisoned, "selection_capture_order")
                clock.check(stage)
                need(not self._selection_poisoned, "selection_capture_order")
            need(type(relative) is str, "noncanonical_path")
            relative = _relative(relative)
            need(type(self._authority) is ReadAuthority, "selection_capture_authority")
            need(type(schemas) is dict and set(schemas) == {"selection", "capture"}, "selection_capture_policy")
            context = kernel._context(profiles, schemas["selection"], clock)
            k, ks, kl = context["descriptor"], context["schema"], context["descriptor"]["limits"]
            profile = "private_compiler_selection_capture"
            need(type(profiles.get(profile)) is dict, "selection_capture_policy")
            copied = kernel._plain({"descriptor": profiles[profile], "schema": schemas["capture"], "scope": scope}, kl["metadata_bytes"], "capture")
            d, schema, scope = copied["descriptor"], copied["schema"], copied["scope"]
            kernel._schema_supported(schema, "capture")
            kernel._checked(d, "policy", schema, "selection_policy_invalid", "capture")
            kernel._checked(scope, "scope", schema, "selection_expected_invalid", "capture")
            need(d["version"] == "v1-private-owned-selection-capture" and d["model"] == "single_file_fixed_selection_kernel_v1"
                 and d["purpose"] == "produce_exact_file_selection_receipt" and d["root_scheme"] == "preservation_absolute_path_text_utf8_v1"
                 and d["complete_status"] == "OWNED_FILE_SELECTION_CAPTURE_COMPLETED_NOT_CURRENT_FRESHNESS"
                 and all(d[key] is False for key in ("default_enabled", "native_execution_enabled", "production_enabled", "semantic_lineage", "cache_eligible")), "selection_capture_policy")
            need(d["schema_sha256"] == kernel._hash(schema) and d["selection_policy_sha256"] == kernel._hash(k)
                 and d["selection_schema_sha256"] == kernel._hash(ks), "selection_capture_policy")
            limits = d["limits"]
            need(all(type(n) is int and 0 < n <= kernel.MAX_SAFE for n in limits.values()), "selection_capture_policy")
            need(limits["file_bytes"] <= kl["source_utf8_bytes"] and limits["chunk_bytes"] <= min(limits["file_bytes"], 1024 * 1024)
                 and limits["metadata_bytes"] <= kl["metadata_bytes"] and limits["envelope_bytes"] <= limits["metadata_bytes"]
                 and limits["image_bytes"] <= kl["image_bytes"] and limits["operation_ms"] <= kl["operation_ms"], "selection_capture_policy")
            if deadline is None:
                clock.end = min(clock.end, clock.started + limits["operation_ms"] / 1000)
            clock.bind(limits["operation_ms"]); check("capture_scope")
            grant = asdict(self._authority); grant["paths"] = list(grant["paths"])
            need(scope["purpose"] == d["purpose"] and scope["root_scheme"] == d["root_scheme"]
                 and scope["read_authority_sha256"] == kernel._hash(grant)
                 and scope["principal_sha256"] == hashlib.sha256(self._authority.principal.encode()).hexdigest()
                 and scope["authority_reference_sha256"] == hashlib.sha256(self._authority.authority_reference.encode()).hexdigest()
                 and scope["root_sha256"] == hashlib.sha256(str(self._root).encode()).hexdigest()
                 and scope["relative_path_sha256"] == hashlib.sha256(relative.encode()).hexdigest()
                 and scope["capture_policy_sha256"] == kernel._hash(d) and scope["capture_schema_sha256"] == kernel._hash(schema)
                 and scope["selection_policy_sha256"] == kernel._hash(k) and scope["selection_schema_sha256"] == kernel._hash(ks), "selection_capture_authority")
            intent_sha = kernel._hash(scope)
            expected = {key: scope[key] for key in ("source_utf8_bytes", "source_raw_sha256", "start_line", "end_line")}
            expected["origin_ref_sha256"] = intent_sha
            kernel._checked(expected, "producer_expected", ks, "selection_expected_invalid", "capture")
            need(all(kernel._hex(value) for key, value in scope.items() if key.endswith("_sha256")), "selection_capture_authority")
            need(type(expected["source_utf8_bytes"]) is int and 0 <= expected["source_utf8_bytes"] <= limits["file_bytes"]
                 and type(expected["start_line"]) is int and type(expected["end_line"]) is int
                 and 1 <= expected["start_line"] <= expected["end_line"], "selection_capture_budget")
            size = expected["source_utf8_bytes"]
            metadata = 2 * len(kernel._canonical(context)) + 2 * len(kernel._canonical(copied)) + len(kernel._canonical(expected))
            capture_reserved = 2 * size + min(size, limits["chunk_bytes"]) + metadata + limits["envelope_bytes"]
            kernel_reserved = 3 * size + metadata + kl["view_utf8_bytes"] + kl["cursor_bytes"] + kl["receipt_bytes"] + limits["envelope_bytes"]
            need(metadata <= limits["metadata_bytes"] and max(capture_reserved, kernel_reserved) <= limits["image_bytes"], "selection_capture_budget")
            check("capture_preopen")
            path, before = self._prepare_payload(relative)
            need(before.st_size == size, "selection_capture_identity")
            start_opens, start_bytes = self._opens, self._bytes
            self._opens += 1
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
            primary = None
            try:
                opened = os.fstat(descriptor)
                need(stat.S_ISREG(opened.st_mode) and _fingerprint(before) == _fingerprint(opened), "file_changed_before_read")
                remaining = opened.st_size
                while remaining:
                    check("capture_read")
                    chunk = os.read(descriptor, min(limits["chunk_bytes"], remaining))
                    self._bytes += len(chunk)
                    need(type(chunk) is bytes and 0 < len(chunk) <= remaining, "file_truncated")
                    chunks.append(chunk); remaining -= len(chunk); chunk = None
                self._settle_payload(relative, opened, descriptor)
                check("capture_read_settled")
            except BaseException as error:
                primary = error
                raise
            finally:
                try:
                    os.close(descriptor)
                except BaseException:
                    self._selection_cleanup_failures += 1
                    if primary is None:
                        raise
            raw = b"".join(chunks); chunks.clear(); chunk = None
            check("capture_kernel")
            admitted = {kernel.PROFILE: k, "compiler_library_inputs": context["capture"], "private_input_image_experiment": context["image"]}
            selection_receipt = kernel.produce_selection_receipt(raw, expected, admitted, ks, now=now, deadline=clock.end)
            raw = None
            check("capture_result")
            result = kernel.loads_json_strict(selection_receipt.decode())
            need(self._opens - start_opens == 1 and self._bytes - start_bytes == size, "selection_capture_order")
            envelope = {"version": d["version"], "status": d["complete_status"], "capture_policy_sha256": kernel._hash(d),
                "capture_schema_sha256": kernel._hash(schema), "origin_intent": scope, "origin_intent_sha256": intent_sha,
                "selection_receipt_sha256": hashlib.sha256(selection_receipt).hexdigest(), "content_sha256": result["content_sha256"],
                "observed_source_sha256": result["content"]["source_raw_sha256"], "observed_source_bytes": size,
                "capture_open_attempts": 1, "capture_read_bytes": size, "cumulative_open_attempts": self._opens, "cumulative_read_bytes": self._bytes,
                "metadata_reserved_bytes": metadata, "capture_reserved_bytes": capture_reserved, "kernel_reserved_bytes": kernel_reserved,
                "known_inputs_reserved_bytes": max(capture_reserved, kernel_reserved), "kernel_scan_work_units": result["cost"]["scan_work_units"],
                "flags": {"checked_capture_completed": True, "source_freshness_verified": False, "read_authority_granted": False,
                          "filesystem_capture_attested": False, "production_enabled": False, "semantic_lineage": False, "cache_eligible": False, "publication_authority": False}}
            kernel._checked(envelope, "envelope", schema, "selection_receipt_invalid", "capture")
            envelope_bytes = kernel._canonical(envelope)
            need(len(envelope_bytes) <= limits["envelope_bytes"], "selection_capture_budget")
            envelope_sha = hashlib.sha256(envelope_bytes).hexdigest()
            check("capture_publish")
            self._selection_completed[scope["relative_path_sha256"]] = envelope_sha
            return selection_receipt, envelope_bytes
        except OSError as error:
            self._failures += 1
            raise PreservationReadError("read_io_error", relative) from error
        except BaseException:
            self._failures += 1
            raise
        finally:
            raw = chunk = selection_receipt = envelope_bytes = None
            chunks.clear()
            self._selection_active = self._selection_poisoned = False
