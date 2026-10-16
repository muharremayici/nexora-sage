"""Wire compatibility and dependency-free storage facts, not new Audit authority."""
from __future__ import annotations

import ast
from copy import deepcopy
import hashlib
import inspect

import pytest

from tools.core import audit_finding_facts as facts
from tools.core import audit_finding_generation as legacy


def _repository_imports(source: str) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            if node.level or (node.module or "").startswith("tools"):
                found.append(node.module or "relative")
        elif isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names if alias.name.startswith("tools"))
    return found


def _canonical() -> dict:
    return {"meta": {"kind": "audit_report", "version": "v1"},
        "artifact_identity": {"status": "BOUND", "atlas_snapshot_id": "parent"},
        "audit_scope": {"audited_projects": ["MAIN", "MAIN"], "scope_kind": "full_repository", "full_repository_claim": True}}


def _scoped() -> tuple[dict, dict]:
    encoded = b'["MAIN::a.ts"]'
    signed = hashlib.sha256(encoded).hexdigest()
    commit = {"state": "complete", "generation_mode": "surgical", "snapshot_id": "current",
        "generation_transition": {"kind": "scoped_delta", "parent_snapshot_id": "parent",
            "changed_files": ["MAIN::a.ts"], "changed_files_sha256": signed}}
    payload = {"meta": {"kind": "watchdog_audit_report", "version": "v1"},
        "artifact_identity": {"status": "BOUND", "atlas_snapshot_id": "current"},
        "audit_scope": {"scope_kind": "scoped_change", "full_repository_claim": False, "scope_status": "complete",
            "requested_files": ["MAIN::a.ts"], "audited_files": ["MAIN::a.ts"], "unresolved_requested_files": []}}
    return commit, payload


def test_storage_facts_are_dependency_free_and_guard_detects_nested_imports():
    assert not _repository_imports(inspect.getsource(facts))
    assert _repository_imports("def f():\n    from tools.core.atlas_io import load_atlas_data\n") == ["tools.core.atlas_io"]
    assert _repository_imports("from . import policy\n")


def test_legacy_exports_and_storage_share_the_same_wire_functions():
    from tools.core import artifact_store
    names = ["audit_finding_severity", "audit_finding_code", "canonical_audit_finding_message",
        "_import_evidence_digest", "normalize_finding_scope_refs", "finding_scope_sha256",
        "validated_scoped_generation_transition", "audit_payload_snapshot_id",
        "build_canonical_finding_manifest", "build_scoped_finding_manifest"]
    for name in names:
        assert getattr(legacy, name) is getattr(facts, name)
    for name in ["canonical_audit_finding_message", "build_canonical_finding_manifest", "build_scoped_finding_manifest"]:
        assert getattr(artifact_store, name) is getattr(facts, name)
    for name in ["AUDIT_FINDINGS_GENERATION_KIND", "AUDIT_FINDINGS_GENERATION_VERSION", "AUDIT_FINDINGS_FACT_ARTIFACT", "AUDIT_FINDINGS_FACT_KEY"]:
        assert getattr(legacy, name) == getattr(facts, name)
    assert "tools.core.audit_finding_generation" not in _repository_imports(inspect.getsource(artifact_store))


def test_policy_digest_does_not_depend_on_source_verification_owner():
    from tools.core.pipeline_policy import capture_api_entry_policy
    assert "tools.core.audit_finding_generation" not in _repository_imports(inspect.getsource(capture_api_entry_policy))
    context = capture_api_entry_policy()
    signed = deepcopy(context)
    identity = signed.pop("identity")
    assert identity == facts._import_evidence_digest(signed)


def test_unicode_message_and_digest_preserve_exact_canonical_bytes():
    value = {"z": "İstanbul", "a": {"n": 1}}
    message = '{"a":{"n":1},"z":"İstanbul"}'
    assert facts.canonical_audit_finding_message(value) == message
    assert facts._import_evidence_digest(value) == hashlib.sha256(message.encode("utf-8")).hexdigest()
    assert facts.audit_finding_severity({"mode": "warning", "severity": "error"}) == "warning"
    assert facts.audit_finding_code({}) == "architecture_violation"


def test_canonical_manifest_keeps_exact_coverage_and_contract_fields():
    expected = {"kind": "nexora.audit_findings_generation", "version": "v1", "snapshot_id": "parent",
        "generation_mode": "canonical_replacement", "coverage_kind": "full_repository", "full_repository_claim": True,
        "covered_projects": ["MAIN"], "composition_depth": 0, "last_parent_snapshot_id": None,
        "last_changed_files": [], "last_changed_files_sha256": hashlib.sha256(b"[]").hexdigest(),
        "last_deleted_files": [], "last_deleted_files_sha256": hashlib.sha256(b"[]").hexdigest(),
        "source_artifact": "audit_report", "audit_contract": "audit_report:v1"}
    assert facts.build_canonical_finding_manifest(_canonical()) == expected


@pytest.mark.parametrize("damage", ["identity", "projects"])
def test_unbound_or_uncovered_canonical_manifest_is_refused(damage):
    payload = _canonical()
    if damage == "identity":
        payload["artifact_identity"]["status"] = "UNBOUND"
    else:
        payload["audit_scope"]["audited_projects"] = []
    with pytest.raises(ValueError):
        facts.build_canonical_finding_manifest(payload)


def test_signed_scoped_manifest_retains_parent_and_non_full_claim():
    commit, payload = _scoped()
    result = facts.build_scoped_finding_manifest(facts.build_canonical_finding_manifest(_canonical()), commit, payload)
    assert result["snapshot_id"] == "current" and result["last_parent_snapshot_id"] == "parent"
    assert result["generation_mode"] == "scoped_composition" and result["composition_depth"] == 1
    assert result["full_repository_claim"] is False and result["last_changed_files"] == ["MAIN::a.ts"]


@pytest.mark.parametrize("damage", ["hash", "parent", "payload", "coverage", "deletion"])
def test_scoped_manifest_refuses_invalid_identity_or_coverage(damage):
    baseline = facts.build_canonical_finding_manifest(_canonical())
    commit, payload = _scoped()
    if damage == "hash":
        commit["generation_transition"]["changed_files_sha256"] = "wrong"
    elif damage == "parent":
        commit["generation_transition"]["parent_snapshot_id"] = "other"
    elif damage == "payload":
        payload["artifact_identity"]["atlas_snapshot_id"] = "other"
    elif damage == "coverage":
        payload["audit_scope"]["audited_files"] = []
    else:
        commit["generation_transition"]["deleted_files"] = ["MAIN::unrequested.ts"]
        commit["generation_transition"]["deleted_files_sha256"] = "wrong"
    with pytest.raises(ValueError):
        facts.build_scoped_finding_manifest(baseline, commit, payload)
