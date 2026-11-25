"""Canonical family coverage from scoped units and the ordinary remainder runner.

No diagnostic receipt promotion: canonical units have a separate cache namespace,
schema and authority. A fresh family context binds their current admissions.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
import uuid

from tools.core import distribution_test_unit_reuse as reuse
from tools.core.distribution_unit_input_identity import (
    CANONICAL_POLICY_SCHEMA as POLICY_SCHEMA, TEST_EVIDENCE_AUTHORITY as AUTHORITY,
    validate_canonical_unit_policy as validate_policy,
)
from tools.core.source_state_identity import source_state_identity

CONTRACT = "config/release_validation_dependency_contract.json"
COMPOSITION_SCHEMA = "nexora_sage_canonical_family_composition_v1"
PARTITION_SCHEMA = "nexora_sage_distribution_test_partition_receipt_v2"


def _sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None




def _load(root):
    contract = json.loads(reuse._io(root / CONTRACT).read_text(encoding="utf-8-sig"), object_pairs_hook=reuse._unique)
    specs = contract.get("diagnostic_test_unit_pilots")
    return validate_policy(contract.get("canonical_test_unit_admission"), specs), specs


def plan_families(root, payload, profile, partitions):
    marker = payload.get("profiles", {}).get(profile, {}).get("execution_units_contract")
    if profile != "canonical_all" or marker is None or "public_projection" in payload:
        return {}
    if marker != CONTRACT:
        raise ValueError("canonical_unit_admission_foreign_contract")
    policy, specs = _load(Path(root))
    if not policy["enabled"]:
        return {}
    from tools.core.distribution_test_selection import _partition_contract, _test_token

    _, tokens, _ = _partition_contract(payload)
    for unit, row in policy["units"].items():
        if tokens.get(_test_token(specs[unit]["test_path"])) != row["family"]:
            raise ValueError("canonical_unit_admission_family_mismatch")
    plans = {}
    for family, tests in partitions.items():
        if (not isinstance(tests, list) or any(not isinstance(p, str) for p in tests)
                or len(tests) != len(set(tests))):
            raise ValueError("canonical_family_selection_not_unique")
        units = [unit for unit, row in policy["units"].items()
                 if row["family"] == family and specs[unit]["test_path"] in tests]
        if units:
            covered = {specs[unit]["test_path"] for unit in units}
            plans[family] = {"units": units, "remaining": [path for path in tests if path not in covered]}
    return plans


def _unit_policy(root, unit, family):
    policy, specs = _load(root)
    if not policy["enabled"] or policy["units"].get(unit) != {"family": family}:
        raise ValueError("canonical_unit_not_admitted_for_family")
    source = reuse._io(__file__).read_bytes()
    if reuse._io(root / "tools/core/distribution_test_family_units.py").read_bytes() != source:
        raise ValueError("canonical_unit_bridge_owner_mismatch")
    return {"schema": policy["schema"], "authority": policy["authority"], "profile": policy["profile"],
            "family": family, "unit": unit, "protocol": reuse.owner.STARTUP_PROTOCOL,
            "bridge_engine_sha256": hashlib.sha256(source).hexdigest()}


def _validate_parent(parent, family, tests, profile_payload):
    if (not isinstance(parent, dict)
            or set(parent) != {"invocation", "profile", "family", "partition_sha256", "selected_paths_sha256", "identity"}
            or not isinstance(parent["invocation"], str) or not re.fullmatch(r"[0-9a-f]{32}", parent["invocation"])
            or parent["profile"] != "canonical_all" or parent["family"] != family
            or parent["partition_sha256"] != reuse._digest(profile_payload["partition"])
            or parent["selected_paths_sha256"] != reuse._digest(tests)):
        raise ValueError("canonical_family_parent_context_invalid")
    identity = parent["identity"]
    if (not isinstance(identity, dict) or set(identity) != {"source", "native_environment_sha256", "native_environment_complete"}
            or not _sha(identity["native_environment_sha256"]) or type(identity["native_environment_complete"]) is not bool):
        raise ValueError("canonical_family_execution_identity_incomplete")
    source = identity["source"]
    if (not isinstance(source, dict) or set(source) != {"algorithm", "fingerprint", "file_count", "total_bytes"}
            or source["algorithm"] != "sha256_path_and_content_v1" or not _sha(source["fingerprint"])
            or any(type(source[k]) is not int or source[k] < 0 for k in ["file_count", "total_bytes"])):
        raise ValueError("canonical_family_source_identity_incomplete")


def run_family(root, *, payload, family, tests, identity, plan, run_remainder, shared_inputs=None):
    root = Path(root).resolve()
    if plan != plan_families(root, payload, "canonical_all", {family: tests}).get(family):
        raise ValueError("canonical_family_plan_changed_before_execution")
    parent = {"invocation": uuid.uuid4().hex, "profile": "canonical_all", "family": family,
              "partition_sha256": reuse._digest(payload["partition"]), "selected_paths_sha256": reuse._digest(tests),
              "identity": identity}
    _validate_parent(parent, family, tests, payload)
    composition = {"schema": COMPOSITION_SCHEMA, "authority": AUTHORITY, "status": "FAIL",
                   "parent": parent, "unit_admissions": [], "remaining_paths": plan["remaining"],
                   "remaining": {"status": "NOT_STARTED"}, "publication_authority": False}
    try:
        for unit in plan["units"]:
            reader = lambda r, u: _unit_policy(r, u, family)
            result = reuse._run_unit(root, unit, canonical=True, policy_reader=reader,
                                     parent_context_sha256=reuse._digest(parent),
                                     **({"shared_inputs": shared_inputs} if shared_inputs is not None else {}))
            path = root / reuse.CACHE / "canonical" / unit / "attempts" / (result["invocation"] + ".json")
            composition["unit_admissions"].append({"unit": unit, "attempt": path.relative_to(root).as_posix(),
                "sha256": hashlib.sha256(reuse._io(path).read_bytes()).hexdigest()})
        if plan["remaining"]:
            code, diagnostics = run_remainder(plan["remaining"], reuse._digest(parent))
            composition["remaining"] = {"status": "EXECUTED", "returncode": code, "diagnostics": diagnostics}
        else:
            code = 0
            composition["remaining"] = {"status": "NOT_REQUIRED", "returncode": 0}
        composition["source_end"] = source_state_identity(root)
        if composition["source_end"] != identity["source"]:
            raise ValueError("canonical_family_source_changed")
        if code == 0:
            composition["status"] = "PASS"
            validate_composition(root, payload, family, tests, composition)
        return code, composition
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        composition.update(status="FAIL", error_type=type(exc).__name__, error=str(exc)[:200])
        return 2, composition


def _admission(root, unit, ref, parent, family):
    if (not isinstance(ref, dict) or set(ref) != {"unit", "attempt", "sha256"}
            or not isinstance(ref.get("attempt"), str)
            or ref["unit"] != unit or not _sha(ref["sha256"])):
        raise ValueError("canonical_unit_reference_invalid")
    directory = root / reuse.CACHE / "canonical" / unit
    path = reuse._owned(root, root / ref["attempt"])
    attempt, digest = reuse._entry(path)
    if (digest != ref["sha256"] or not isinstance(attempt, dict)
            or attempt.get("schema") != reuse.CANONICAL_SCHEMA
            or attempt.get("authority") != reuse.CANONICAL_AUTHORITY or attempt.get("status") != "PASS"
            or attempt.get("unit") != unit or attempt.get("parent_context_sha256") != reuse._digest(parent)
            or attempt.get("canonical_profile_reuse_enabled") is not True
            or attempt.get("publication_authority") is not False
            or not isinstance(attempt.get("invocation"), str)
            or not re.fullmatch(r"[0-9a-f]{32}", attempt["invocation"])
            or path != directory / "attempts" / (attempt["invocation"] + ".json")
            or not _sha(attempt.get("cache_key"))):
        raise ValueError("canonical_unit_current_attempt_not_bound")
    cached = reuse._owned(root, directory / (attempt["cache_key"] + ".json"))
    entry, cached_sha = reuse._entry(cached)
    project = reuse.owner._capture_project_inputs(root, unit)
    if (not isinstance(entry, dict) or not reuse.owner._identity_is_complete(entry.get("identity"))
            or entry["identity"].get("unit") != unit
            or entry["identity"].get("project_inputs_sha256") != project["project_inputs_sha256"]
            or entry["identity"].get("spec_sha256") != reuse._digest(project["spec"])
            or entry["identity"].get("expected_tests") != project["spec"]["expected_tests"]
            or attempt.get("cache_receipt") != cached.relative_to(root).as_posix()
            or attempt.get("cache_receipt_sha256") != cached_sha
            or attempt.get("input_identity_sha256") != reuse._digest(entry["identity"])):
        raise ValueError("canonical_unit_inputs_or_receipt_changed")
    context = {"identity": entry["identity"], "spec": project["spec"]}
    key = reuse._key(root, context, _unit_policy(root, unit, family), canonical=True)
    reuse._verify(entry, context, key, canonical=True)
    reuse._verify_origin(root, directory, entry, cached_sha, canonical=True)
    count = len(project["spec"]["expected_tests"])
    status = attempt.get("execution_status")
    expected = (1, count, 0) if status == "EXECUTED" else (0, 0, count) if status == "REUSED" else None
    keys = ("executed_test_processes", "actual_passed_cases", "reused_passed_cases")
    if (expected is None or any(type(attempt.get(k)) is not int for k in keys)
            or tuple(attempt[k] for k in keys) != expected or not attempt.get("ended_at")
            or attempt.get("origin_invocation") != entry["origin_invocation"]):
        raise ValueError("canonical_unit_execution_counts_invalid")
    return {"unit": unit, "test_path": project["spec"]["test_path"],
            "execution_status": status, "fresh_cases": expected[1], "reused_cases": expected[2]}


def _remaining_evidence(root, payload, parent, paths, remaining):
    if (not isinstance(remaining, dict) or type(remaining.get("returncode")) is not int or remaining["returncode"] != 0):
        raise ValueError("canonical_family_remainder_not_complete")
    if not paths:
        if remaining != {"status": "NOT_REQUIRED", "returncode": 0}:
            raise ValueError("canonical_family_empty_remainder_not_skipped")
        return {}
    if (set(remaining) != {"status", "returncode", "diagnostics"} or remaining["status"] != "EXECUTED"
            or not isinstance(remaining["diagnostics"], dict) or remaining["diagnostics"].get("status") != "FINISHED"):
        raise ValueError("canonical_family_remainder_not_complete")
    from tools.core import distribution_test_execution as execution

    diagnostics = remaining["diagnostics"]
    path = reuse._owned(root, Path(diagnostics.get("path", "")))
    if (path.name != "events.jsonl" or path.parent.parent != root / "output/reports/distribution-test-runs"
            or not re.fullmatch(r"pytest-[0-9a-f]{32}", path.parent.name)):
        raise ValueError("canonical_family_remainder_journal_path_invalid")
    contract = execution.execution_contract(payload)
    limit = contract["diagnostics"]["maximum_line_bytes"]
    with reuse._io(path).open("rb") as stream:
        line = stream.readline(limit + 1)
    if len(line) > limit or not line.endswith(b"\n"):
        raise ValueError("canonical_family_remainder_header_incomplete")
    header = json.loads(line, object_pairs_hook=reuse._unique)
    context = header.get("context") if isinstance(header, dict) else None
    if (not isinstance(context, dict) or context.get("identity") != parent["identity"]
            or context.get("family_parent_context_sha256") != reuse._digest(parent)
            or context.get("test_paths") != sorted(paths)
            or context.get("selection_sha256") != execution.digest(paths) or context.get("collect_only") is not False
            or context.get("diagnostics") != contract["diagnostics"] or "candidate_preflight" in context
            or context.get("authority") != "diagnostic_only_no_cache_release_or_publication_authority"
            or not isinstance(context.get("overlay"), dict)
            or context["overlay"].get("contract_sha256") != execution.digest(contract)):
        raise ValueError("canonical_family_remainder_context_mismatch")
    actual = execution.observe_journal(reuse._io(path), context, 0)
    if actual["status"] != "FINISHED" or any(actual.get(k) != diagnostics.get(k) for k in
            ("journal_sha256", "context_sha256", "terminal", "collection")):
        raise ValueError("canonical_family_remainder_journal_changed")
    return actual["terminal"]["phase_counts"]


def validate_composition(root, payload, family, tests, composition):
    root = Path(root).resolve()
    fields = {"schema", "authority", "status", "parent", "unit_admissions", "remaining_paths",
              "remaining", "source_end", "publication_authority"}
    if (not isinstance(composition, dict) or set(composition) != fields
            or composition.get("schema") != COMPOSITION_SCHEMA or composition.get("authority") != AUTHORITY
            or composition.get("status") != "PASS" or composition.get("publication_authority") is not False):
        raise ValueError("canonical_family_composition_invalid")
    plans = plan_families(root, payload, "canonical_all", {family: tests})
    plan = plans.get(family)
    if plan is None or composition["remaining_paths"] != plan["remaining"]:
        raise ValueError("canonical_family_unit_coverage_changed")
    parent = composition["parent"]
    _validate_parent(parent, family, tests, payload)
    if composition["source_end"] != parent["identity"]["source"] or source_state_identity(root) != composition["source_end"]:
        raise ValueError("canonical_family_composition_source_stale")
    refs = composition["unit_admissions"]
    if (not isinstance(refs, list) or [r.get("unit") for r in refs if isinstance(r, dict)] != plan["units"]
            or len(refs) != len(plan["units"])):
        raise ValueError("canonical_family_unit_union_invalid")
    units = [_admission(root, unit, ref, parent, family) for unit, ref in zip(plan["units"], refs)]
    covered = [u["test_path"] for u in units]
    if (len(tests) != len(set(tests)) or len(covered + plan["remaining"]) != len(tests)
            or set(covered).intersection(plan["remaining"]) or set(covered + plan["remaining"]) != set(tests)):
        raise ValueError("canonical_family_file_coverage_not_exact")
    phases = _remaining_evidence(root, payload, parent, plan["remaining"], composition["remaining"])
    return {"fresh_unit_cases": sum(u["fresh_cases"] for u in units),
            "reused_unit_cases": sum(u["reused_cases"] for u in units), "units": units,
            "fresh_remaining_passed_cases": phases.get("call:passed", 0),
            "fresh_remaining_skipped_cases": phases.get("call:skipped", 0)}
