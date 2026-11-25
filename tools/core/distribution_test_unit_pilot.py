"""Opt-in, isolated test-unit experiment; never canonical release evidence.

Only a freshly executed in-memory result can be reused in this invocation. No
persisted receipt is imported, and ordinary distribution profiles are unchanged.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import csv
import io
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import stat
import sys
import sysconfig
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
from dataclasses import asdict

from tools.core.unmanaged_atomic_io import native_filesystem_path
from tools.core import distribution_unit_input_identity as unit_identity
from tools.core import distribution_dependency_recheck as dependency_recheck

AUTHORITY = unit_identity.INPUT_AUTHORITY
SCHEMA = "nexora_sage_diagnostic_test_unit_pilot_v1"
BINDING_SCHEMA = "nexora_sage_diagnostic_unit_parent_binding_v1"
STARTUP_PROTOCOL = "isolated_no_site_no_autoload_no_conftest_explicit_empty_config_v1"
BOOTSTRAP = (
    "import os,sys; "
    "sys.path[:0]=[os.path.join(os.getcwd(),'deps'),os.getcwd()]; "
    "import pytest; "
    "raise SystemExit(pytest.main(sys.argv[1:]))"
)


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _bytes(path: Path) -> bytes:
    return Path(native_filesystem_path(path)).read_bytes()


def _review_digest(content: bytes) -> str:
    # Review consent is checkout-EOL portable; execution identity below is still raw bytes.
    return hashlib.sha256(content.decode("utf-8").replace("\r\n", "\n").encode("utf-8")).hexdigest()


def _under(root: Path, relative: str) -> Path:
    path = root / relative
    if (not relative or "\\" in relative or Path(relative).is_absolute()
            or ".." in Path(relative).parts or root.resolve() not in path.resolve().parents):
        raise ValueError(f"Unsafe pilot input: {relative}")
    return path


def _capture_project_inputs(root: Path, unit: str) -> dict:
    policy_bytes = _bytes(root / "config/release_validation_dependency_contract.json")
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate unit policy key")
            result[key] = value
        return result
    contract = json.loads(policy_bytes, object_pairs_hook=unique_object)
    spec = contract.get("diagnostic_test_unit_pilots", {}).get(unit)
    if (not isinstance(spec, dict) or spec.get("authority") != AUTHORITY
            or spec.get("canonical_profile_reuse_enabled") is not False):
        raise ValueError("Unknown or over-authorized diagnostic test unit")
    if "input_contract_version" in spec:
        from jsonschema import Draft202012Validator, ValidationError

        schema_bytes = _bytes(root / "config/schemas/release_validation_dependency_contract.schema.json")
        schema = json.loads(schema_bytes, object_pairs_hook=unique_object)
        if spec["input_contract_version"] != "v2":
            raise ValueError("Unsupported diagnostic input version")
        effective_schema = unit_identity.unit_schema_projection(schema, unit)
        try:
            Draft202012Validator(effective_schema).validate(spec)
        except ValidationError as exc:
            raise ValueError("Malformed v2 pilot input contract") from exc
        unit_identity.project_roles(spec)
        files = {p: (_read_unit_file_snapshot(root, p, unit_identity.METADATA_PROJECTION["max_bytes"])
                     if p == "pyproject.toml" else _bytes(_under(root, p)))
                 for p in spec["project_inputs"]}
        projected, provenance = unit_identity.project_facts(unit, spec, files, policy_bytes)
        for relative in spec["required_absent_paths"]:
            if os.path.lexists(native_filesystem_path(_under(root, relative))):
                raise ValueError(f"Pilot startup boundary changed: {relative}")
        return {"spec": spec, "files": files, "project_inputs_sha256": projected,
                "provenance": provenance, "schema_bytes": schema_bytes, "effective_schema": effective_schema,
                "output_policy": contract.get("diagnostic_unit_output_observation")}
    required = {"authority", "canonical_profile_reuse_enabled", "test_path", "expected_tests",
        "reviewed_source_sha256", "review_digest_algorithm", "required_absent_paths", "runtime_distributions", "timeout_seconds"}
    if (set(spec) != required or not isinstance(spec["reviewed_source_sha256"], dict)
            or not isinstance(spec["expected_tests"], list)
            or len(spec["expected_tests"]) != len(set(spec["expected_tests"]))
            or not isinstance(spec["required_absent_paths"], list)
            or not isinstance(spec["runtime_distributions"], list)
            or not isinstance(spec["timeout_seconds"], int) or spec["timeout_seconds"] <= 0):
        raise ValueError("Malformed pilot input contract")
    if spec["review_digest_algorithm"] != "sha256_utf8_lf_v1":
        raise ValueError("Unknown source-review digest algorithm")
    if not spec.get("reviewed_source_sha256") or not spec.get("expected_tests"):
        raise ValueError("Pilot input review is incomplete")
    if spec.get("test_path") not in spec["reviewed_source_sha256"]:
        raise ValueError("Pilot test is outside the reviewed input set")
    files = {}
    for relative, expected in spec["reviewed_source_sha256"].items():
        files[relative] = _bytes(_under(root, relative))
        if _review_digest(files[relative]) != expected:
            raise ValueError(f"Pilot input requires a new source review: {relative}")
    for relative in spec["required_absent_paths"]:
        if _under(root, relative).exists():
            raise ValueError(f"Pilot startup boundary changed: {relative}")
    return {"spec": spec, "files": files, "provenance": None,
            "output_policy": contract.get("diagnostic_unit_output_observation")}


def load_spec(root: Path, unit: str) -> dict:
    return _capture_project_inputs(root, unit)["spec"]


def _wheel_inventory(distribution, name: str) -> tuple[list[str], str, str]:
    # Distribution.files can both reread metadata and silently filter missing files.
    # Read the declared wheel inventory once; actual byte reads below must fail on loss.
    record = distribution.read_text("RECORD")
    if not record:
        raise ValueError(f"Missing wheel inventory: {name}")
    paths: list[str] = []
    seen: set[str] = set()
    try:
        for row in csv.reader(io.StringIO(record), strict=True):
            if len(row) != 3 or not row[0] or (row[1] and "=" not in row[1]):
                raise ValueError(f"Malformed wheel inventory: {name}")
            if row[2] and int(row[2]) < 0:
                raise ValueError(f"Invalid wheel inventory size: {name}")
            relative = row[0].replace("\\", "/")
            if relative in seen:
                raise ValueError(f"Duplicate wheel inventory path: {name}")
            seen.add(relative)
            paths.append(relative)
    except csv.Error as exc:
        raise ValueError(f"Malformed wheel inventory CSV: {name}") from exc
    manifests = [p for p in paths if Path(p).name == "RECORD"
                 and Path(p).parent.name.endswith(".dist-info")]
    if len(manifests) != 1:
        raise ValueError(f"Missing or ambiguous wheel self-inventory: {name}")
    return paths, record, manifests[0]


def _distribution_inputs(names: list[str]):
    """Shared fresh discovery/requirements/RECORD owner for both observation modes."""
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name

    declared = {canonicalize_name(name) for name in names}
    for name in sorted(declared):
        distribution = importlib.metadata.distribution(name)
        for requirement in distribution.requires or []:
            parsed = Requirement(requirement)
            if parsed.marker is None or parsed.marker.evaluate({"extra": ""}):
                if canonicalize_name(parsed.name) not in declared:
                    raise ValueError(f"Undeclared pytest dependency: {parsed.name}")
        entries, record, manifest = _wheel_inventory(distribution, name)
        yield name, distribution, entries, record, manifest


def _dependency_path_selected(relative: str) -> bool:
    # Installed CLI scripts and bytecode are not loaded by this -I -S protocol.
    return not (".." in Path(relative).parts or Path(relative).is_absolute()
                or "__pycache__" in Path(relative).parts or relative.endswith(".pyc"))


def _package_inputs(names: list[str]) -> dict[str, bytes]:
    # Copy a finite wheel-file set, not the host site-packages directory or .pth hooks.
    result: dict[str, bytes] = {}
    for name, distribution, entries, record, manifest in _distribution_inputs(names):
        captured_manifest = None
        for relative in entries:
            if not _dependency_path_selected(relative):
                continue
            path = Path(distribution.locate_file(relative))
            if path.is_symlink():
                raise ValueError("Symlink in pytest wheel input")
            content = _bytes(path)
            if relative in result and result[relative] != content:
                raise ValueError("Conflicting pytest wheel inputs")
            result[relative] = content
            if relative == manifest:
                captured_manifest = content
        if (captured_manifest is None
                or captured_manifest.decode("utf-8").splitlines() != record.splitlines()):
            raise ValueError(f"Wheel inventory changed during capture: {name}")
    return result


def _recheck_package_inputs(names: list[str], expected: dict[str, bytes], *,
                            unit: str, invocation: str) -> dict:
    """Fresh wheel acquisition against owner-held bytes; never a second payload map."""
    row = {"schema": "nexora_sage_dependency_payload_recheck_v1",
        "authority": "diagnostic_only_no_test_or_release_admission", "unit_id": unit,
        "owner_invocation_id": invocation, "status": "UNAVAILABLE", "payload_checks_complete": False,
        "dependency_inputs_sha256": None, "distribution_observations": 0, "inventory_resolutions": 0,
        "candidate_paths_observed": 0, "unique_paths_observed": 0, "payload_bytes_seen": 0,
        "max_chunk_bytes_seen": 0, "eof_probe_reads": 0, "reason_codes": [],
        "reuse": False, "may_skip_tests": False, "fresh_native_baseline": False,
        "canonical_profile_reuse_enabled": False}
    if (not isinstance(unit, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", unit)
            or not isinstance(invocation, str) or not invocation.strip()
            or type(expected) is not dict or any(type(p) is not str or not p or type(v) is not bytes
                                                for p, v in expected.items())):
        row["reason_codes"].append("invalid_held_dependency_request")
        return row
    hashes, seen = {}, set()
    try:
        for name, distribution, entries, record, manifest in _distribution_inputs(names):
            # These counters describe completed discovery, not failed read attempts.
            row["distribution_observations"] += 1
            row["inventory_resolutions"] += 1
            captured_manifest = None
            for relative in entries:
                if not _dependency_path_selected(relative):
                    continue
                row["candidate_paths_observed"] += 1
                seen.add(relative)
                row["unique_paths_observed"] = len(seen)
                if relative not in expected:
                    row.update(status="CHANGED", reason_codes=["dependency_path_added"])
                    return row
                path = Path(distribution.locate_file(relative))
                if path.is_symlink():
                    raise ValueError("Symlink in pytest wheel input")
                with Path(native_filesystem_path(path)).open("rb", buffering=0) as stream:
                    observed = dependency_recheck.compare_stream(stream.read, expected[relative])
                    row["payload_bytes_seen"] += observed["bytes_seen"]
                    row["max_chunk_bytes_seen"] = max(row["max_chunk_bytes_seen"], observed["max_chunk_bytes_seen"])
                    row["eof_probe_reads"] += observed["eof_probe_reads"]
                if observed["status"] != "MATCH":
                    row.update(status=observed["status"], reason_codes=[observed["reason"]])
                    return row
                hashes[relative] = observed["sha256"]
                if relative == manifest:
                    # Substitution is valid only after a fresh exact read through EOF.
                    captured_manifest = expected[relative]
            if (captured_manifest is None
                    or captured_manifest.decode("utf-8").splitlines() != record.splitlines()):
                raise ValueError("Wheel inventory changed during capture")
        if seen != set(expected):
            row.update(status="CHANGED", reason_codes=["dependency_path_missing"])
            return row
    except (OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
        row["reason_codes"].append(f"dependency_input_unavailable:{type(exc).__name__}")
        return row
    row.update(status="MATCH", payload_checks_complete=True, dependency_inputs_sha256=_digest(hashes))
    return row


def _runtime_identity() -> str:
    # Bind the interpreter's stdlib (including readable bytecode) and native modules.
    # This remains a trusted-local-runtime experiment, not a hostile-process sandbox.
    stdlib = Path(sysconfig.get_path("stdlib"))
    paths = {Path(sys.executable)}
    def refuse_unreadable(error: OSError) -> None:
        raise error

    for directory, dirs, files in os.walk(stdlib, onerror=refuse_unreadable):
        dirs[:] = sorted(d for d in dirs if d != "site-packages")
        if any((Path(directory) / name).is_symlink() for name in dirs):
            raise ValueError("Unknown symlinked runtime input directory")
        paths.update(Path(directory) / name for name in files)
    base = Path(sys.base_prefix)
    if (base / "DLLs").is_dir():
        paths.update(p for p in (base / "DLLs").rglob("*") if p.is_file())
    for pattern in ("*.dll", "*.zip", "*.exe"):
        paths.update(base.glob(pattern))
    rows = [(str(p.resolve()), hashlib.sha256(_bytes(p)).hexdigest()) for p in sorted(paths)]
    return _digest([sys.version, platform.platform(), rows])


def _build_input_context(root: Path, unit: str, *, project_context=None, dependency_capture=None) -> dict:
    context = _capture_project_inputs(root, unit) if project_context is None else project_context
    spec, files = context["spec"], context["files"]
    component = None
    if dependency_capture is None:
        dependencies = _package_inputs(spec["runtime_distributions"])
    else:
        if context["provenance"] is None:
            raise ValueError("Shared dependency capture requires reviewed v2 inputs")
        dependencies, component = dependency_capture(spec["runtime_distributions"])
    files.update({"deps/" + relative: value for relative, value in dependencies.items()})
    if component is None and context["provenance"] is not None:
        component = _digest({p: hashlib.sha256(v).hexdigest() for p, v in dependencies.items()})
    context["identity"] = _compose_input_identity(root, unit, context, dependency_component=component)
    return context


def _compose_input_identity(root: Path, unit: str, context: dict, *, dependency_component=None) -> dict:
    """One identity interpretation shared by full capture and v2 after observation."""
    spec = context["spec"]
    engine_paths = ["tools/core/distribution_test_unit_pilot.py", "tools/run_distribution_tests.py",
                    "tools/core/unmanaged_atomic_io.py", "tools/core/distribution_unit_input_identity.py",
                    "tools/core/distribution_dependency_recheck.py", "tools/core/distribution_test_selection.py"]
    engine = {p: hashlib.sha256(_bytes(root / p)).hexdigest() for p in engine_paths}
    if context["provenance"] is not None:
        if not isinstance(dependency_component, str) or not re.fullmatch("[0-9a-f]{64}", dependency_component):
            raise ValueError("Incomplete dependency input observation")
        engine["config/schemas/release_validation_dependency_contract.schema.json"] = _digest(context["effective_schema"])
        input_digests = {"canonical_profile_reuse_enabled": False,
            "project_inputs_sha256": context["project_inputs_sha256"],
            "dependency_inputs_sha256": dependency_component}
    else:
        input_digests = {"source_inputs_sha256": _digest({k: hashlib.sha256(v).hexdigest() for k, v in context["files"].items()})}
    identity = {
        "schema": unit_identity.IDENTITY_SCHEMA if context["provenance"] is not None else SCHEMA,
        "authority": AUTHORITY, "unit": unit, **input_digests,
        "spec_sha256": _digest(spec), "runtime_sha256": _runtime_identity(),
        "engine_sha256": _digest(engine),
        "environment": {k: os.environ.get(k, "") for k in ("SYSTEMROOT", "WINDIR")},
        "expected_tests": spec["expected_tests"],
        "startup": STARTUP_PROTOCOL,
    }
    return identity


def _recheck_input_context(root: Path, unit: str, before: dict, invocation: str) -> dict:
    if (not isinstance(before, dict) or not _identity_is_complete(before.get("identity"))
            or before["identity"]["schema"] != unit_identity.IDENTITY_SCHEMA
            or before["identity"]["unit"] != unit or not isinstance(invocation, str) or not invocation.strip()
            or not unit_identity.provenance_is_complete(before.get("provenance"), unit,
                project_paths=before.get("spec", {}).get("project_inputs", {}))):
        raise ValueError("V2 after observation requires owner-held current context")
    context = _capture_project_inputs(root, unit)
    if context["provenance"] is None:
        raise ValueError("Input contract version changed during execution")
    # Release fresh project payloads before reading dependencies; raw facts are held.
    context.pop("files")
    expected = {p[len("deps/"):]: value for p, value in before["files"].items() if p.startswith("deps/")}
    observed = _recheck_package_inputs(context["spec"]["runtime_distributions"], expected,
        unit=unit, invocation=invocation)
    context["dependency_recheck"] = observed
    context["identity"] = (_compose_input_identity(root, unit, context,
        dependency_component=observed["dependency_inputs_sha256"])
        if observed["status"] == "MATCH" and observed["payload_checks_complete"] else None)
    return context


def build_inputs(root: Path, unit: str) -> tuple[dict, dict[str, bytes]]:
    context = _build_input_context(root, unit)
    return context["identity"], context["files"]


def _identity_is_complete(identity: object) -> bool:
    fields = {"schema", "authority", "unit", "source_inputs_sha256", "spec_sha256", "runtime_sha256",
              "engine_sha256", "environment", "startup", "expected_tests"}
    version2 = isinstance(identity, dict) and identity.get("schema") == unit_identity.IDENTITY_SCHEMA
    hashes = {"source_inputs_sha256", "spec_sha256", "runtime_sha256", "engine_sha256"}
    if version2:
        fields = (fields - {"source_inputs_sha256"}) | {"project_inputs_sha256", "dependency_inputs_sha256", "canonical_profile_reuse_enabled"}
        hashes = (hashes - {"source_inputs_sha256"}) | {"project_inputs_sha256", "dependency_inputs_sha256"}
        if (identity.get("canonical_profile_reuse_enabled") is not False
                or not isinstance(identity.get("unit"), str)
                or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", identity["unit"])):
            return False
    if (not isinstance(identity, dict) or set(identity) != fields
            or identity.get("schema") != (unit_identity.IDENTITY_SCHEMA if version2 else SCHEMA)
            or identity.get("authority") != AUTHORITY or not identity.get("unit")
            or not identity.get("startup") or not identity.get("expected_tests")
            or any(not re.fullmatch("[0-9a-f]{64}", str(identity.get(key, "")))
                   for key in hashes)
            or not isinstance(identity.get("environment"), dict)
            or set(identity["environment"]) != {"SYSTEMROOT", "WINDIR"}):
        return False
    return True


def compare_parent_unit_binding(issued: object, envelope: object, current: object,
                                observed: object) -> list[str]:
    """Compare parent-held observations only; no execution, receipt import or authority."""
    return _compare_parent_unit_binding(issued, envelope, current, observed, version2=False)


def compare_parent_unit_binding_v2(issued: object, envelope: object, current: object,
                                   observed: object, provenance_before: object,
                                   provenance_after: object) -> list[str]:
    """Diagnostic comparison only; provenance must be independently held by the caller."""
    if (not unit_identity.provenance_is_complete(provenance_before, "jsonc_parser")
            or not unit_identity.provenance_is_complete(provenance_after, "jsonc_parser")
            or provenance_before != provenance_after):
        return ["missing_or_changed_parent_held_provenance"]
    return _compare_parent_unit_binding(issued, envelope, current, observed,
        version2=True, provenance=provenance_before)


def _compare_parent_unit_binding(issued, envelope, current, observed, *, version2, provenance=None):
    reasons = []
    context_fields = {"dispatch_event_binding", "dispatch_decision", "dispatch_id", "parent_step_id",
        "parent_attempt_id", "parent_execution_status", "parent_dependency_results", "run_identity",
        "profile", "selection_scope_sha256", "partition_contract_sha256", "parent_step_contract_sha256",
        "materialized_command_sha256", "direct_dependency_evidence_sha256", "unit_id"}
    if not isinstance(issued, dict) or set(issued) != context_fields:
        return ["incomplete_issued_parent_context"]
    sha_keys = [key for key in context_fields if key.endswith("sha256")]
    text_keys = ["dispatch_id", "parent_step_id", "parent_attempt_id", "profile", "unit_id"]
    if (any(not isinstance(issued[k], str) or not issued[k].strip() for k in text_keys)
            or any(not isinstance(issued[k], str) or not re.fullmatch("[0-9a-f]{64}", issued[k]) for k in sha_keys)
            or issued["parent_execution_status"] != "RUNNING"):
        reasons.append("invalid_issued_parent_context")
    if (not _identity_is_complete(current)
            or current.get("schema") != (unit_identity.IDENTITY_SCHEMA if version2 else SCHEMA)
            or current.get("unit") != issued["unit_id"]
            or current.get("startup") != STARTUP_PROTOCOL
            or not isinstance(current.get("expected_tests"), list)
            or not all(isinstance(n, str) and n for n in current.get("expected_tests", []))
            or len(current["expected_tests"]) != len(set(current["expected_tests"]))):
        return [*reasons, "incomplete_or_unsupported_current_unit_identity"]
    required = {"schema", "authority", "canonical_profile_reuse_enabled", "parent_context",
        "unit_context", "actual_execution", "issued_context_sha256", "receipt_sha256"}
    if not isinstance(envelope, dict) or set(envelope) != required:
        return [*reasons, "unsupported_or_incomplete_binding_envelope"]
    if (envelope["schema"] != (unit_identity.BINDING_SCHEMA if version2 else BINDING_SCHEMA)
            or envelope["authority"] != AUTHORITY
            or envelope["canonical_profile_reuse_enabled"] is not False):
        reasons.append("binding_scope_or_authority_invalid")
    if envelope["parent_context"] != issued or envelope["issued_context_sha256"] != _digest(issued):
        reasons.append("issued_parent_context_mismatch")
    unit = {"unit_id": current["unit"], "unit_spec_sha256": current["spec_sha256"],
        "expected_test_nodeids": current["expected_tests"], "execution_protocol": STARTUP_PROTOCOL,
        "input_identity_before": current, "input_identity_after": current}
    if version2:
        unit.update(input_provenance_before=provenance, input_provenance_after=provenance)
    if envelope["unit_context"] != unit:
        reasons.append("unit_identity_protocol_or_test_union_mismatch")
    execution_fields = {"child_invocation_id", "execution_status", "returncode", "timed_out", "skipped",
        "collected_and_executed_nodeids", "junit_sha256", "stdout_sha256", "stderr_sha256"}
    if not isinstance(observed, dict) or set(observed) != execution_fields:
        return [*reasons, "missing_parent_observed_execution"]
    if (not isinstance(observed["child_invocation_id"], str) or not observed["child_invocation_id"].strip()
            or observed["execution_status"] != "COMPLETED" or type(observed["returncode"]) is not int
            or observed["returncode"] != 0 or observed["timed_out"] is not False
            or type(observed["skipped"]) is not int or observed["skipped"] != 0
            or observed["collected_and_executed_nodeids"] != current["expected_tests"]
            or any(not isinstance(observed[k], str) or not re.fullmatch("[0-9a-f]{64}", observed[k])
                   for k in execution_fields if k.endswith("sha256"))):
        reasons.append("parent_observed_child_not_clean_or_complete")
    expected_execution = {**observed, "parent_observed_result_sha256": _digest(observed)}
    if envelope["actual_execution"] != expected_execution:
        reasons.append("parent_observed_execution_mismatch")
    if envelope["receipt_sha256"] != _digest({k: v for k, v in envelope.items() if k != "receipt_sha256"}):
        reasons.append("binding_receipt_digest_mismatch")
    return reasons


def reuse_decision(identity: dict, previous: dict, invocation: str, *, provenance=None) -> dict:
    reasons = []
    version2 = identity.get("schema") == unit_identity.IDENTITY_SCHEMA
    if not _identity_is_complete(identity):
        reasons.append("incomplete_current_unit_identity")
    if (previous.get("schema") != (unit_identity.BASELINE_SCHEMA if version2 else SCHEMA)
            or previous.get("authority") != AUTHORITY
            or previous.get("canonical_profile_reuse_enabled") is not False):
        reasons.append("wrong_scope_or_authority")
    if not invocation or previous.get("invocation") != invocation:
        reasons.append("not_same_invocation_fresh_evidence")
    if (previous.get("status") != "PASS" or previous.get("returncode") != 0
            or previous.get("execution_status") != "COMPLETED"
            or previous.get("skipped") != 0 or previous.get("timed_out") is not False):
        reasons.append("prior_not_clean_actual_execution")
    if previous.get("identity") != identity:
        reasons.append("unit_input_identity_changed")
    if version2 and (not unit_identity.provenance_is_complete(provenance, identity.get("unit"))
            or previous.get("input_provenance_before") != provenance
            or previous.get("input_provenance_after") != provenance):
        reasons.append("missing_or_changed_input_provenance")
    if sorted(previous.get("test_names") or []) != sorted(identity.get("expected_tests") or []):
        reasons.append("test_union_mismatch")
    body = {k: v for k, v in previous.items() if k != "receipt_sha256"}
    if previous.get("receipt_sha256") != _digest(body):
        reasons.append("receipt_digest_mismatch")
    return {"reuse": not reasons, "reasons": reasons, "authority": AUTHORITY}


def _execute(files: dict[str, bytes], spec: dict) -> dict:
    with tempfile.TemporaryDirectory(prefix="sage-unit-pilot-", ignore_cleanup_errors=True) as scratch:
        root = Path(scratch)
        for relative, content in files.items():
            destination = _under(root, relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        (root / "pytest.ini").write_bytes(b"[pytest]\n")
        version2 = spec.get("input_contract_version") == "v2"
        if version2 and not _unit_capsule_inputs_match(root, files, b"[pytest]\n"):
            raise ValueError("Copied pilot inputs differ from captured bytes")
        command = [sys.executable, "-I", "-S", "-B", "-c", BOOTSTRAP,
            "-q", "-c", "pytest.ini", "--noconftest", "--import-mode=importlib",
            "-p", "no:cacheprovider", "--junitxml=result.xml", spec["test_path"]]
        environment = {k: v for k in ("SYSTEMROOT", "WINDIR") if (v := os.environ.get(k))}
        environment.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", TEMP=scratch, TMP=scratch)
        result = subprocess.run(command, cwd=root, env=environment, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=spec["timeout_seconds"], check=False)
        if version2 and not _unit_capsule_inputs_match(root, files, b"[pytest]\n"):
            raise ValueError("Copied pilot inputs changed during execution")
        print(result.stdout[-6000:], flush=True)
        if result.stderr:
            print(result.stderr[-2000:], file=sys.stderr, flush=True)
        xml = ET.parse(root / "result.xml").getroot()
        cases = list(xml.iter("testcase"))
        names = [case.get("name") for case in cases]
        skipped = sum(len(list(case.iter("skipped"))) for case in cases)
        bad = any(list(case.iter("failure")) or list(case.iter("error")) for case in cases)
        clean = (result.returncode == 0 and not bad and not skipped
                 and sorted(names) == sorted(spec["expected_tests"]))
        return {"status": "PASS" if clean else "FAIL", "returncode": result.returncode,
            "skipped": skipped, "test_names": names, "timed_out": False,
            "stdout_sha256": hashlib.sha256(result.stdout.encode()).hexdigest()}


def run_pilot(root: Path, unit: str) -> dict:
    started = time.perf_counter()
    print(f"[distribution-unit-pilot] unit={unit} phase=review_and_identity diagnostic_only", flush=True)
    before = _build_input_context(root, unit)
    identity, files, spec = before["identity"], before["files"], before["spec"]
    identity_seconds = time.perf_counter() - started
    print(f"[distribution-unit-pilot] phase=isolated_execution identity_seconds={identity_seconds:.3f}", flush=True)
    invocation = str(uuid.uuid4())
    result = _execute(files, spec)
    executed = time.perf_counter()
    print("[distribution-unit-pilot] phase=recheck_inputs", flush=True)
    after = (_recheck_input_context(root, unit, before, invocation)
             if before["provenance"] is not None else _build_input_context(root, unit))
    if before["provenance"] is not None and after["dependency_recheck"]["status"] == "UNAVAILABLE":
        reasons = after["dependency_recheck"].get("reason_codes", [])
        raise ValueError(f"Pilot input recheck unavailable ({','.join(reasons)}); result is not reusable")
    if after["identity"] != identity or after["provenance"] != before["provenance"]:
        raise ValueError("Pilot inputs changed during execution; result is not reusable")
    schema = unit_identity.BASELINE_SCHEMA if before["provenance"] is not None else SCHEMA
    baseline = {**result, "schema": schema, "authority": AUTHORITY,
        "canonical_profile_reuse_enabled": False, "identity": identity,
        "invocation": invocation, "execution_status": "COMPLETED"}
    if before["provenance"] is not None:
        baseline.update(input_provenance_before=before["provenance"], input_provenance_after=after["provenance"])
    baseline["receipt_sha256"] = _digest(baseline)
    warm = reuse_decision(after["identity"], baseline, invocation, provenance=after["provenance"])
    return {"schema": schema, "authority": AUTHORITY,
        "canonical_profile_reuse_enabled": False,
        "status": "DIAGNOSTIC_PASS" if warm["reuse"] else "FAIL", "baseline": baseline,
        "warm_decision": warm, "fresh_test_processes": 1, "warm_test_processes": 0,
        "timing_seconds": {"initial_identity": round(identity_seconds, 3),
            "isolated_execution": round(executed - started - identity_seconds, 3),
            "recheck_identity": round(time.perf_counter() - executed, 3),
            "total": round(time.perf_counter() - started, 3)},
        "claim_boundary": "Same-invocation experiment only. No persisted receipt import, canonical family skip, cross-invocation reuse, full proof or release authority."}


def _unit_output_digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _load_unit_output_policy(root: Path, *, context=None) -> dict:
    """Validate the diagnostic extension through its owning schema, without defaults."""
    from jsonschema import Draft202012Validator, SchemaError, ValidationError
    from tools.core.json_io import load_json_object_strict

    if context is None:
        contract = load_json_object_strict(root / "config/release_validation_dependency_contract.json")
        section = contract.get("diagnostic_unit_output_observation")
    else:
        section = context["output_policy"]
    schema = (json.loads(context["schema_bytes"]) if context is not None and "schema_bytes" in context
              else load_json_object_strict(root / "config/schemas/release_validation_dependency_contract.schema.json"))
    section_schema = schema["properties"]["diagnostic_unit_output_observation"]
    try:
        Draft202012Validator.check_schema(section_schema)
        Draft202012Validator(section_schema).validate(section)
    except (SchemaError, ValidationError) as exc:
        raise ValueError("Invalid diagnostic unit-output policy") from exc
    if (section["protocol"] != "isolated_no_site_exact_env_closed_stdin_xunit2_v2"
            or section["digest_algorithm"] != "sha256_json_utf8_canonical_v1"
            or section["authority"] != "diagnostic_only_no_test_or_release_admission"
            or any(type(v) is not int or not 0 < v < sys.maxsize for v in section["limits"].values())
            or section["limits"]["xml_depth"] > section["limits"]["xml_elements"]):
        raise ValueError("Unsupported diagnostic output protocol or limits")
    return section


def _junit_file_facts(path: Path) -> tuple:
    facts = os.lstat(native_filesystem_path(path))
    if (not stat.S_ISREG(facts.st_mode) or facts.st_nlink != 1
            or getattr(facts, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
        raise ValueError("JUnit output must be an unlinked regular file")
    return _junit_stat_identity(facts)


def _junit_stat_identity(facts: os.stat_result) -> tuple:
    return (facts.st_dev, facts.st_ino, facts.st_mode, facts.st_nlink, facts.st_size,
            facts.st_mtime_ns, facts.st_ctime_ns, getattr(facts, "st_birthtime_ns", None))


def _read_unit_file_snapshot(capsule: Path, relative: str, limit: int) -> bytes:
    """One bounded handle read; stable facts do not attest immutable final output."""
    root_facts = os.lstat(native_filesystem_path(capsule))
    if (not stat.S_ISDIR(root_facts.st_mode)
            or getattr(root_facts, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
        raise ValueError("JUnit capsule ownership changed")
    output = _under(capsule, relative)
    # Inspect every actual directory beneath the owned root, not only the leaf link.
    parent = capsule
    parents = [capsule]
    for segment in Path(relative).parts[:-1]:
        parent = parent / segment
        parents.append(parent)
    parent_facts = []
    for parent in parents:
        info = os.lstat(native_filesystem_path(parent))
        if (not stat.S_ISDIR(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise ValueError("Unit file parent is linked or not owned")
        parent_facts.append(_junit_stat_identity(info))
    before = _junit_file_facts(output)
    if before[4] > limit:
        raise ValueError("JUnit byte limit exceeded")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(native_filesystem_path(output), flags), "rb") as stream:
        opened = _junit_stat_identity(os.fstat(stream.fileno()))
        # On Windows the path and handle APIs can expose different ctime semantics.
        # Keep ctime stable within each lane; compare only equivalent facts across lanes.
        if opened[:6] + opened[7:] != before[:6] + before[7:]:
            raise ValueError("JUnit output changed before read")
        content = stream.read(limit + 1)
        if (len(content) > limit or len(content) != before[4]
                or _junit_stat_identity(os.fstat(stream.fileno())) != opened):
            raise ValueError("JUnit output changed or exceeded its byte limit")
    if (_junit_file_facts(output) != before or parent_facts != [
            _junit_stat_identity(os.lstat(native_filesystem_path(p))) for p in parents]):
        raise ValueError("JUnit path or capsule changed during read")
    return content


def _unit_capsule_inputs_match(capsule: Path, files: dict, ini: bytes) -> bool:
    return all(_read_unit_file_snapshot(capsule, relative, len(content)) == content
               for relative, content in {**files, "pytest.ini": ini}.items())


class _BoundedJUnitBuilder(ET.TreeBuilder):
    def __init__(self, limits: dict):
        super().__init__()
        self.limits, self.depth, self.elements = limits, 0, 0

    def start(self, tag, attrs):
        self.depth += 1
        self.elements += 1
        if self.depth > self.limits["xml_depth"] or self.elements > self.limits["xml_elements"]:
            raise ValueError("JUnit XML structure limit exceeded")
        return super().start(tag, attrs)

    def end(self, tag):
        self.depth -= 1
        return super().end(tag)

    def comment(self, text):
        raise ValueError("JUnit comments are outside the diagnostic grammar")

    def pi(self, target, text):
        raise ValueError("JUnit processing instructions are outside the diagnostic grammar")


def _parse_unit_junit(content: bytes, spec: dict, limits: dict) -> dict:
    if len(content) > limits["junit_bytes"]:
        raise ValueError("JUnit byte limit exceeded")
    text = content.decode("utf-8-sig", errors="strict")
    declaration = re.match(r"<\?xml\s+[^?]*\?>", text)
    if declaration and not re.fullmatch(
            r"<\?xml\s+version=['\"]1\.0['\"](?:\s+encoding=['\"]utf-8['\"])?\s*\?>",
            declaration[0], re.IGNORECASE):
        raise ValueError("Unsupported JUnit XML declaration")
    if "\0" in text or "<!" in text:
        raise ValueError("JUnit declarations/entities are outside the diagnostic grammar")
    root = ET.fromstring(text, parser=ET.XMLParser(target=_BoundedJUnitBuilder(limits)))
    if (root.tag != "testsuites" or set(root.attrib) - {"name"} or len(root) != 1
            or root[0].tag != "testsuite" or (root.text or "").strip()
            or any((child.tail or "").strip() for child in root)):
        raise ValueError("Unsupported JUnit suite grammar")
    suite = root[0]
    required = {"name", "tests", "failures", "errors", "skipped"}
    if (not required <= set(suite.attrib)
            or set(suite.attrib) - required - {"time", "hostname", "timestamp"}
            or (suite.text or "").strip()):
        raise ValueError("Unsupported JUnit suite attributes")
    counts = {}
    for key in ("tests", "failures", "errors", "skipped"):
        value = suite.get(key, "")
        if not re.fullmatch(r"0|[1-9][0-9]*", value):
            raise ValueError("JUnit outcome count is not a nonnegative integer")
        counts[key] = int(value)
    pairs = []
    outcomes = {"failures": 0, "errors": 0, "skipped": 0}
    for case in suite:
        if (case.tag != "testcase" or not {"name", "classname"} <= set(case.attrib)
                or set(case.attrib) - {"name", "classname", "time"}
                or (case.text or "").strip() or (case.tail or "").strip()):
            raise ValueError("Unsupported JUnit testcase grammar")
        pairs.append((case.get("classname"), case.get("name")))
        seen = set()
        for node in case:
            if (node.tag not in {"failure", "error", "skipped", "system-out", "system-err"}
                    or node.tag in seen or len(node) or (node.tail or "").strip()
                    or set(node.attrib) - ({"message", "type"} if node.tag in {"failure", "error", "skipped"} else set())):
                raise ValueError("Unsupported JUnit outcome grammar")
            seen.add(node.tag)
        if len(seen & {"failure", "error", "skipped"}) > 1:
            raise ValueError("Ambiguous JUnit outcome")
        outcomes["failures"] += "failure" in seen
        outcomes["errors"] += "error" in seen
        outcomes["skipped"] += "skipped" in seen
    expected_class = spec["test_path"][:-3].replace("/", ".")
    expected = {(expected_class, name) for name in spec["expected_tests"]}
    return {"junit_reported_case_pairs": [list(p) for p in pairs], "reported_outcomes": outcomes,
        "membership_matches": len(pairs) == len(set(pairs)) == len(expected) and set(pairs) == expected,
        "suite_counts_consistent": counts == {"tests": len(pairs), **outcomes}}


def _unit_raw_capture_complete(raw, plan: dict) -> bool:
    from tools.core.subprocess_telemetry import RawSubprocessObservation

    return (isinstance(raw, RawSubprocessObservation) and raw.status == "CAPTURED"
        and raw.protocol == "sha256_stream_bytes_exact_env_closed_stdin_v1"
        and raw.capture_complete is True and type(raw.returncode) is int and type(raw.timed_out) is bool
        and raw.termination_reason is None and raw.cleanup.parent_exited is True
        and raw.cleanup.readers_joined is True and not raw.cleanup.errors
        and raw.authority == "diagnostic_only_no_test_or_release_admission"
        and all(getattr(raw, k) is False for k in (
            "reuse", "may_skip_tests", "fresh_native_baseline", "canonical_profile_reuse_enabled"))
        and all(stream.complete is True and stream.error is None and type(stream.bytes_seen) is int
            and stream.bytes_seen >= 0 and re.fullmatch("[0-9a-f]{64}", stream.sha256 or "")
            for stream in (raw.stdout, raw.stderr))
        and all(getattr(raw, key) == value for key, value in plan.items()))


def _observe_diagnostic_unit_outputs(root: Path, unit: str) -> dict:
    """Private direct observer only; no CLI, receipt import, family or native binding."""
    from tools.core.subprocess_telemetry import load_raw_observation_policy, run_observed_subprocess

    row = {"schema": "nexora_sage_diagnostic_unit_output_observation_v1",
        "authority": "diagnostic_only_no_test_or_release_admission", "status": "REFUSED",
        "protocol": "isolated_no_site_exact_env_closed_stdin_xunit2_v2", "owner_invocation_id": None,
        "unit_id": unit, "launch_attempted": False, "prepared_plan_identity": None,
        "raw_capture": None, "junit_snapshot": {"read_complete": False, "sha256": None,
            "bytes_seen": None, "snapshot_finality": "not_verified"},
        "junit_reported_case_pairs": [], "reported_outcomes": None, "input_recheck": "not_performed",
        "producer_identity_complete": False, "producer_identity_references": {},
        "capsule_cleanup": "not_created", "reasons": [], "reuse": False, "may_skip_tests": False,
        "fresh_native_baseline": False, "canonical_profile_reuse_enabled": False}
    capsule = None
    raw = None
    try:
        project = _capture_project_inputs(root, unit)
        policy = _load_unit_output_policy(root, context=project)
        stream_policy = load_raw_observation_policy()
        if unit not in policy["units"]:
            raise ValueError("Unit is not registered for diagnostic output observation")
        before = _build_input_context(root, unit, project_context=project)
        identity, files, spec = before["identity"], before["files"], before["spec"]
        version2 = before["provenance"] is not None
        expected_schema = (unit_identity.OUTPUT_SCHEMA if version2 else "nexora_sage_diagnostic_unit_output_observation_v1")
        if policy["schema"] != expected_schema:
            raise ValueError("Unit input and output schema versions differ")
        row["schema"] = expected_schema
        if version2:
            row.update(input_identity_before=identity, input_identity_after=None,
                input_provenance_before=before["provenance"], input_provenance_after=None)
        if (not _identity_is_complete(identity) or identity["unit"] != unit
                or identity["startup"] != STARTUP_PROTOCOL or identity["spec_sha256"] != _digest(spec)
                or identity["expected_tests"] != spec["expected_tests"] or not spec["test_path"].endswith(".py")
                or (not version2 and identity["source_inputs_sha256"] != _digest({k: hashlib.sha256(v).hexdigest() for k, v in files.items()}))):
            raise ValueError("Prepared unit identity is incomplete or inconsistent")
        capsule = tempfile.TemporaryDirectory(prefix="sage-unit-output-")
        directory = Path(capsule.name)
        for relative, content in files.items():
            destination = _under(directory, relative)
            Path(native_filesystem_path(destination.parent)).mkdir(parents=True, exist_ok=True)
            Path(native_filesystem_path(destination)).write_bytes(content)
        output = directory / "result.xml"
        if any(os.path.lexists(native_filesystem_path(p)) for p in (output, directory / "pytest.ini")):
            raise ValueError("JUnit output or generated config was already present before launch")
        ini = b"[pytest]\njunit_family = xunit2\n"
        Path(native_filesystem_path(directory / "pytest.ini")).write_bytes(ini)
        if not _unit_capsule_inputs_match(directory, files, ini):
            raise ValueError("Copied capsule inputs differ from their prepared bytes")
        command = [sys.executable, "-I", "-S", "-B", "-c", BOOTSTRAP, "-q", "-c", "pytest.ini",
            "--noconftest", "--import-mode=importlib", "-p", "no:cacheprovider", "--junitxml=result.xml", spec["test_path"]]
        environment = {k: v for k in ("SYSTEMROOT", "WINDIR") if (v := os.environ.get(k))}
        environment.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", TEMP=capsule.name, TMP=capsule.name)
        plan = {"command_sha256": _unit_output_digest(command),
            "environment_sha256": _unit_output_digest(environment),
            "cwd_sha256": _unit_output_digest(str(directory.resolve())),
            "policy_sha256": _unit_output_digest(stream_policy)}
        row["prepared_plan_identity"] = {**plan, "digest_algorithm": policy["digest_algorithm"],
            "junit_policy_sha256": _unit_output_digest(policy), "pytest_ini_sha256": hashlib.sha256(ini).hexdigest()}
        row["producer_identity_references"] = {"launcher_source_sha256": hashlib.sha256(_bytes(Path(__file__))).hexdigest(),
            ("unit_execution_identity_sha256" if version2 else "legacy_unit_input_identity_sha256"): _digest(identity),
            "boundary": "Source references only; loaded producer/runtime closure is not attested."}
        row["owner_invocation_id"] = str(uuid.uuid4())
        row["launch_attempted"], row["status"] = True, "INCOMPLETE_OBSERVATION"
        raw = run_observed_subprocess(command, cwd=directory, env=environment,
            timeout=spec["timeout_seconds"], label="diagnostic_unit_output", raw_observation=True)
        if _unit_raw_capture_complete(raw, plan):
            content = _read_unit_file_snapshot(directory, "result.xml", policy["limits"]["junit_bytes"])
            row["junit_snapshot"].update(read_complete=True, sha256=hashlib.sha256(content).hexdigest(), bytes_seen=len(content))
            parsed = _parse_unit_junit(content, spec, policy["limits"])
            row.update(parsed)
            if not parsed["membership_matches"] or not parsed["suite_counts_consistent"]:
                row["reasons"].append("junit_membership_or_counts_mismatch")
            else:
                row["status"] = ("OBSERVED_TEST_FAILURE" if raw.returncode != 0 or raw.timed_out
                    or any(parsed["reported_outcomes"].values()) else "OBSERVED_REPORT_CONFORMANT_DIAGNOSTIC_ONLY")
        else:
            row["reasons"].append("raw_capture_or_plan_incomplete")
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, ET.ParseError, subprocess.SubprocessError) as exc:
        row["reasons"].append(f"unit_output_unavailable:{type(exc).__name__}")
    finally:
        try:
            if raw is not None:
                from tools.core.subprocess_telemetry import RawSubprocessObservation

                if isinstance(raw, RawSubprocessObservation):
                    row["raw_capture"] = asdict(raw)
            if row["launch_attempted"]:
                try:
                    after = (_recheck_input_context(root, unit, before, row["owner_invocation_id"])
                             if version2 else _build_input_context(root, unit))
                    if version2:
                        row.update(input_identity_after=after["identity"], input_provenance_after=after["provenance"])
                        if after["dependency_recheck"]["status"] != "MATCH":
                            row["reasons"].extend("dependency_input_recheck:" + code
                                for code in after["dependency_recheck"].get("reason_codes", []))
                    unchanged = (after["identity"] == identity and after["provenance"] == before["provenance"]
                        and after["spec"] == spec
                        and _unit_capsule_inputs_match(directory, files, ini)
                        and _load_unit_output_policy(root, context=after) == policy
                        and load_raw_observation_policy() == stream_policy)
                    row["input_recheck"] = ("unavailable"
                        if version2 and after["dependency_recheck"]["status"] == "UNAVAILABLE"
                        else "unchanged" if unchanged else "changed")
                except (OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
                    row["input_recheck"] = "unavailable"
                    row["reasons"].append(f"unit_input_recheck:{type(exc).__name__}")
                if row["input_recheck"] != "unchanged":
                    row["reasons"].append("unit_input_identity_not_current")
        finally:
            if capsule is not None:
                try:
                    capsule.cleanup()
                    row["capsule_cleanup"] = "completed"
                except OSError as exc:
                    row["capsule_cleanup"] = "failed"
                    row["reasons"].append(f"capsule_cleanup:{type(exc).__name__}")
            if row["reasons"] and row["launch_attempted"]:
                row["status"] = "INCOMPLETE_OBSERVATION"
            if row["capsule_cleanup"] == "failed":
                row["status"] = "INCOMPLETE_OBSERVATION"
    return row
