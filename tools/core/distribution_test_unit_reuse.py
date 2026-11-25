"""Persisted execution reuse for declared isolated development test units.

Uses the existing capture and execution owners. Development receipts cannot
satisfy canonical families. The family adapter issues a separate canonical
execution scope; neither scope grants release or publication authority.
The operational cache is trusted local state, not a hostile-writer boundary.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import threading
from types import MappingProxyType
import uuid

from tools.core import distribution_test_unit_pilot as owner
from tools.core.distribution_unit_input_identity import (
    PERSISTED_POLICY_SCHEMA as POLICY_SCHEMA, PERSISTED_AUTHORITY as AUTHORITY,
    UNIT_CACHE_DIRECTORY as CACHE, validate_persisted_unit_policy as validate_policy,
)

RECEIPT_SCHEMA = "nexora_sage_completed_development_test_unit_v1"
CANONICAL_SCHEMA = "nexora_sage_completed_canonical_test_unit_v1"
CANONICAL_AUTHORITY = "canonical_test_unit_execution_only"


class SharedUnitInputs:
    """Sequential invocation-owned payloads; never memoize a validation result."""

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.invocation = uuid.uuid4().hex
        self._origin = self._binding()
        self._groups = {}
        self._closed = False

    @staticmethod
    def _binding():
        return (os.getpid(), threading.get_ident(), sys.executable, owner.STARTUP_PROTOCOL,
                tuple((k, os.environ.get(k, "")) for k in ("SYSTEMROOT", "WINDIR")))

    def _check(self, root):
        if self._closed or Path(root).resolve() != self.root or self._binding() != self._origin:
            raise ValueError("shared_unit_inputs_outside_owned_invocation")

    def __enter__(self):
        self._check(self.root)
        return self

    def __exit__(self, *_):
        self._groups.clear()
        self._closed = True

    def capture(self, root, unit):
        self._check(root)
        observation = {}

        def capture_dependencies(names):
            from packaging.utils import canonicalize_name

            self._check(root)
            group = tuple(sorted({canonicalize_name(name) for name in names}))
            reused = group in self._groups
            if not reused:
                files = owner._package_inputs(list(group))
                if (not files or any(type(p) is not str or type(v) is not bytes for p, v in files.items())):
                    raise ValueError("shared_unit_dependency_capture_incomplete")
                digest = _digest({p: hashlib.sha256(v).hexdigest() for p, v in files.items()})
                self._groups[group] = (MappingProxyType(dict(files)), digest)
            files, digest = self._groups[group]
            observation.update(capture_id=self.invocation, dependency_inputs_sha256=digest,
                status="SHARED_HELD_BYTES" if reused else "CAPTURED",
                file_count=len(files), total_bytes=sum(map(len, files.values())),
                authority="payload_sharing_only_no_validation_skip")
            return files, digest

        context = owner._build_input_context(self.root, unit, dependency_capture=capture_dependencies)
        context["shared_dependency_capture"] = observation
        return context


def _digest(value):
    return owner._digest(value)


def _io(path):
    return Path(owner.native_filesystem_path(path))


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_unit_reuse_key")
        result[key] = value
    return result




def _policy(root, unit):
    contract = json.loads(_io(root / "config/release_validation_dependency_contract.json").read_text(
        encoding="utf-8-sig"), object_pairs_hook=_unique)
    policy = validate_policy(contract.get("persisted_test_unit_reuse"), contract.get("diagnostic_test_unit_pilots"))
    if not policy["enabled"] or unit not in policy["allowed_units"]:
        raise ValueError("persisted_unit_reuse_not_enabled_for_unit")
    return policy


def _owned(root, path):
    if path.resolve() != path or not path.is_relative_to(root):
        raise ValueError("unit_reuse_path_alias_or_escape")
    return path


def _publish(root, path, payload):
    """Publish a complete immutable file, without exposing a partial JSON body."""
    _owned(root, path)
    _io(path.parent).mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    content = (json.dumps(payload, sort_keys=True, ensure_ascii=True, indent=2) + "\n").encode("utf-8")
    try:
        with _io(temporary).open("xb") as stream:
            stream.write(content); stream.flush(); os.fsync(stream.fileno())
        os.link(_io(temporary), _io(path))  # Exclusive publication; never overwrite evidence.
        return hashlib.sha256(content).hexdigest()
    finally:
        _io(temporary).unlink(missing_ok=True)


def _entry(path):
    with _io(path).open("rb") as stream:
        content = stream.read(65537)
    if len(content) > 65536:
        raise ValueError("unit_reuse_receipt_size_limit")
    return json.loads(content.decode("utf-8"), object_pairs_hook=_unique), hashlib.sha256(content).hexdigest()


def _clean(result, expected):
    return (isinstance(result, dict) and result.get("status") == "PASS"
            and type(result.get("returncode")) is int and result["returncode"] == 0
            and result.get("timed_out") is False
            and type(result.get("skipped")) is int and result["skipped"] == 0
            and isinstance(result.get("test_names"), list)
            and sorted(result["test_names"]) == sorted(expected)
            and isinstance(result.get("stdout_sha256"), str)
            and re.fullmatch(r"[0-9a-f]{64}", result["stdout_sha256"]) is not None)


def _key(root, context, policy, *, canonical=False):
    source = _io(__file__).read_bytes()
    if _io(root / "tools/core/distribution_test_unit_reuse.py").read_bytes() != source:
        raise ValueError("unit_reuse_engine_does_not_match_executed_owner")
    # Other admitted units do not change this unit's execution inputs. Its own
    # membership is checked on every policy read, including the final recheck.
    effective = {k: v for k, v in policy.items() if k != "allowed_units"}
    return _digest({"schema": CANONICAL_SCHEMA if canonical else RECEIPT_SCHEMA,
                    "identity": context["identity"], "policy": effective,
                    "reuse_engine_sha256": hashlib.sha256(source).hexdigest()})


def _verify(entry, context, key, *, canonical=False):
    fields = {"schema", "authority", "key", "origin_invocation", "identity",
              "provenance_before", "provenance_after", "result", "receipt_sha256"}
    if (not isinstance(entry, dict) or set(entry) != fields
            or entry.get("schema") != (CANONICAL_SCHEMA if canonical else RECEIPT_SCHEMA)
            or entry.get("authority") != (CANONICAL_AUTHORITY if canonical else AUTHORITY)
            or entry.get("key") != key or entry.get("identity") != context["identity"]
            or not isinstance(entry.get("origin_invocation"), str)
            or re.fullmatch(r"[0-9a-f]{32}", entry["origin_invocation"]) is None
            or entry.get("provenance_before") != entry.get("provenance_after")
            or not owner.unit_identity.provenance_is_complete(entry.get("provenance_before"), context["identity"]["unit"],
                project_paths=context["spec"]["project_inputs"])
            or not _clean(entry.get("result"), context["identity"]["expected_tests"])
            or entry.get("receipt_sha256") != _digest({k: v for k, v in entry.items() if k != "receipt_sha256"})):
        raise ValueError("persisted_unit_receipt_not_clean_or_bound")


def _recheck(root, unit, before, invocation, policy, key, *, policy_reader=None, canonical=False):
    print(f"[distribution-unit] phase=recheck unit={unit}", flush=True)
    after = owner._recheck_input_context(root, unit, before, invocation)
    if (after.get("identity") != before["identity"] or after.get("provenance") != before["provenance"]
            or (policy_reader or _policy)(root, unit) != policy
            or _key(root, after, policy, canonical=canonical) != key):
        raise ValueError("unit_reuse_inputs_changed_during_operation")


def _verify_origin(root, directory, entry, receipt_sha256, *, canonical=False):
    path = _owned(root, directory / "attempts" / (entry["origin_invocation"] + ".json"))
    origin, _ = _entry(path)
    if (not isinstance(origin, dict)
            or origin.get("schema") != (CANONICAL_SCHEMA if canonical else RECEIPT_SCHEMA)
            or origin.get("authority") != (CANONICAL_AUTHORITY if canonical else AUTHORITY)
            or origin.get("status") != "PASS" or origin.get("execution_status") != "EXECUTED"
            or origin.get("invocation") != entry["origin_invocation"]
            or origin.get("origin_invocation") != entry["origin_invocation"]
            or origin.get("cache_key") != entry["key"]
            or origin.get("unit") != entry["identity"]["unit"]
            or origin.get("cache_receipt_sha256") != receipt_sha256
            or origin.get("input_identity_sha256") != _digest(entry["identity"])
            or any(type(origin.get(k)) is not int for k in
                   ("executed_test_processes", "actual_passed_cases", "reused_passed_cases"))
            or origin.get("executed_test_processes") != 1
            or origin.get("actual_passed_cases") != len(entry["identity"]["expected_tests"])
            or origin.get("reused_passed_cases") != 0 or not origin.get("ended_at")
            or origin.get("canonical_profile_reuse_enabled") is not canonical
            or (canonical and not re.fullmatch(r"[0-9a-f]{64}", str(origin.get("parent_context_sha256", ""))))
            or origin.get("publication_authority") is not False):
        raise ValueError("unit_reuse_origin_not_completed_successfully")


def run_unit(root: Path, unit: str) -> dict:
    return _run_unit(root, unit)


def _run_unit(root: Path, unit: str, *, policy_reader=None, canonical=False, parent_context_sha256=None,
              shared_inputs=None) -> dict:
    root = Path(root).resolve()
    if type(canonical) is not bool or (canonical and (policy_reader is None
            or not re.fullmatch(r"[0-9a-f]{64}", str(parent_context_sha256)))):
        raise ValueError("canonical_unit_requires_owned_parent_context")
    policy = (policy_reader or _policy)(root, unit)
    directory = _owned(root, (root / CACHE / "canonical" / unit) if canonical else (root / CACHE / unit))
    _io(directory).mkdir(parents=True, exist_ok=True)
    invocation = uuid.uuid4().hex
    attempt = {"schema": CANONICAL_SCHEMA if canonical else RECEIPT_SCHEMA,
               "authority": CANONICAL_AUTHORITY if canonical else AUTHORITY, "unit": unit,
               "invocation": invocation, "started_at": datetime.now(timezone.utc).isoformat(),
               "status": "FAIL", "execution_status": "REFUSED", "executed_test_processes": 0,
               "actual_passed_cases": 0, "reused_passed_cases": 0,
               "canonical_profile_reuse_enabled": canonical, "publication_authority": False}
    if canonical:
        attempt["parent_context_sha256"] = parent_context_sha256
    claim = directory / "active.claim"
    claimed = False
    failure = None
    try:
        with _io(claim).open("x", encoding="ascii") as stream:
            stream.write(invocation)
        claimed = True
        print(f"[distribution-unit] phase=capture unit={unit}", flush=True)
        before = (owner._build_input_context(root, unit) if shared_inputs is None
                  else shared_inputs.capture(root, unit))
        if "shared_dependency_capture" in before:
            attempt["shared_dependency_capture"] = dict(before["shared_dependency_capture"])
        if (not owner._identity_is_complete(before.get("identity"))
                or before["identity"].get("schema") != owner.unit_identity.IDENTITY_SCHEMA
                or not owner.unit_identity.provenance_is_complete(before.get("provenance"), unit,
                    project_paths=before["spec"]["project_inputs"] )):
            raise ValueError("unit_reuse_current_inputs_incomplete")
        key = _key(root, before, policy, canonical=canonical)
        attempt["input_identity_sha256"] = _digest(before["identity"])
        cached = _owned(root, directory / (key + ".json"))
        if _io(cached).exists():
            print(f"[distribution-unit] phase=verify_persisted unit={unit}", flush=True)
            entry, receipt_sha256 = _entry(cached)
            _verify(entry, before, key, canonical=canonical)
            _verify_origin(root, directory, entry, receipt_sha256, canonical=canonical)
            _recheck(root, unit, before, invocation, policy, key, policy_reader=policy_reader, canonical=canonical)
            attempt.update(status="PASS", execution_status="REUSED",
                           reused_passed_cases=len(before["identity"]["expected_tests"]))
        else:
            print(f"[distribution-unit] phase=execute unit={unit}", flush=True)
            attempt["executed_test_processes"] = 1
            attempt["execution_status"] = "EXECUTED_UNVERIFIED"
            result = owner._execute(before["files"], before["spec"])
            attempt["observed_execution"] = result
            _recheck(root, unit, before, invocation, policy, key, policy_reader=policy_reader, canonical=canonical)
            if not _clean(result, before["identity"]["expected_tests"]):
                attempt["execution_status"] = "EXECUTED_FAILED"
                raise ValueError("unit_reuse_execution_not_complete_clean_pass")
            entry = {"schema": CANONICAL_SCHEMA if canonical else RECEIPT_SCHEMA,
                     "authority": CANONICAL_AUTHORITY if canonical else AUTHORITY, "key": key,
                     "origin_invocation": invocation, "identity": before["identity"],
                     "provenance_before": before["provenance"], "provenance_after": before["provenance"],
                     "result": result}
            entry["receipt_sha256"] = _digest(entry)
            receipt_sha256 = _publish(root, cached, entry)
            attempt.update(status="PASS", execution_status="EXECUTED",
                           actual_passed_cases=len(result["test_names"]))
        if hashlib.sha256(_io(cached).read_bytes()).hexdigest() != receipt_sha256:
            attempt.update(status="FAIL", execution_status="REFUSED",
                           actual_passed_cases=0, reused_passed_cases=0)
            raise ValueError("unit_reuse_receipt_changed_during_operation")
        attempt.update(cache_key=key, origin_invocation=entry["origin_invocation"],
                       cache_receipt=cached.relative_to(root).as_posix(),
                       cache_receipt_sha256=receipt_sha256)
        return attempt
    except BaseException as exc:
        failure = exc
        if attempt["status"] == "PASS":
            attempt.update(status="FAIL", execution_status="EXECUTED_UNVERIFIED" if
                           attempt["executed_test_processes"] else "REFUSED",
                           actual_passed_cases=0, reused_passed_cases=0)
        attempt["error_type"] = type(exc).__name__
        raise
    finally:
        attempt["ended_at"] = datetime.now(timezone.utc).isoformat()
        try:
            _publish(root, directory / "attempts" / (invocation + ".json"), attempt)
        except OSError as exc:
            if failure is None:
                raise
            failure.add_note("unit_attempt_publication_failed:" + type(exc).__name__)
            print("[distribution-unit] attempt_record_unavailable error_type=" + type(exc).__name__, flush=True)
        finally:
            if claimed and _io(claim).read_text(encoding="ascii") == invocation:
                _io(claim).unlink()
