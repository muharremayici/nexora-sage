from __future__ import annotations

from copy import deepcopy
import hashlib
import heapq
import json
import re
from pathlib import Path
from typing import Any, Mapping

from tools.core.audit_finding_facts import (
    AUDIT_FINDINGS_GENERATION_KIND,
    AUDIT_FINDINGS_GENERATION_VERSION,
    AUDIT_FINDINGS_FACT_ARTIFACT,
    AUDIT_FINDINGS_FACT_KEY,
    audit_finding_severity,
    audit_finding_code,
    canonical_audit_finding_message,
    _import_evidence_digest,
    normalize_finding_scope_refs,
    finding_scope_sha256,
    validated_scoped_generation_transition,
    audit_payload_snapshot_id,
    build_canonical_finding_manifest,
    build_scoped_finding_manifest,
)


GENERATION_COMPOSITION_CONTRACT_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "artifact_freshness_contract.json"
)
AUDIT_PRESENTATION_CONTRACT_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "agent_surface_contract.json"
)


def audit_import_subject_contract() -> dict[str, Any]:
    from tools.core.json_io import load_json_object_strict_cached
    return load_json_object_strict_cached(AUDIT_PRESENTATION_CONTRACT_PATH)["audit_import_subject_evidence"]


def audit_import_restriction_rows() -> list[dict]:
    """Consume the extension's one declared rule source, not a local rule list."""
    from tools.core.config import DOCTRINE
    extension = audit_import_subject_contract()["extensions"]["declared_layer_import_restriction"]
    value = DOCTRINE
    for part in extension["rule_source"].lstrip("/").split("/"):
        value = value[part.replace("~1", "/").replace("~0", "~")]
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        raise ValueError("Invalid declared import restriction source")
    return value


def audit_import_rule_contract(rule_key: str) -> dict | None:
    root = audit_import_subject_contract()
    if rule_key == root["finding_rule"]:
        return root
    from tools.core.config import DOCTRINE
    extension = root["extensions"]["declared_layer_import_restriction"]
    if (DOCTRINE.get("audit_rule_profiles", {}).get(rule_key, {}).get("layer") == "architectural"
            and any(row.get(extension["rule_key_property"]) == rule_key for row in audit_import_restriction_rows())):
        return {**root, **extension, "finding_rule": rule_key}
    extension = root["extensions"]["module_api_import"]
    if (rule_key in extension["rules"]
            and DOCTRINE.get("audit_rule_profiles", {}).get(rule_key, {}).get("layer") == "architectural"):
        return {**root, **extension, "finding_rule": rule_key, "decision_branch": extension["rules"][rule_key]}
    return None


def unavailable_audit_import_subject(reason: str, *, rule_key: str | None = None) -> dict[str, Any]:
    root = audit_import_subject_contract()
    contract = audit_import_rule_contract(rule_key) if rule_key else root
    contract = contract or root
    result = {"kind": contract["kind"], "version": contract["version"], "status": "unavailable",
            "reason": reason, "subjects": [], "causal_relation": "not_evaluated",
            "target_native_effective_policy": "not_evaluated", "runtime_proof": "not_performed"}
    if contract["version"] != root["version"]:
        result.update(rule=contract["finding_rule"], decision_kind=contract["decision_kind"])
    return result


def capture_audit_import_doctrine_identity() -> dict[str, Any]:
    """One capture per Audit run, from modular owners and consumed runtime data."""
    from tools.core.config import DOCTRINE
    from tools.doctrine_compiler import (compile_doctrine_registry, doctrine_source_fingerprint,
                                        doctrine_without_compile_metadata)
    try:
        fingerprint = doctrine_source_fingerprint()
        compiled = doctrine_without_compile_metadata(DOCTRINE)
        if (DOCTRINE.get("_meta", {}).get("source_fingerprint") != fingerprint
                or compiled != doctrine_without_compile_metadata(compile_doctrine_registry())):
            return {"status": "unavailable"}
        return {"status": "bound", "source_fingerprint": fingerprint,
                "compiled_semantic_sha256": _import_evidence_digest(compiled)}
    except (OSError, ValueError, TypeError, KeyError):
        return {"status": "unavailable"}


def capture_audit_import_policy(project: str, effective_policy: dict, taxonomy: dict,
                                doctrine_identity: dict, runtime_context: dict, *, rule_key: str | None = None) -> dict:
    """Capture the actual filtered project's decision, not a global rule-name guess."""
    contract = audit_import_rule_contract(rule_key) if rule_key else audit_import_subject_contract()
    if contract is None:
        return {"status": "unavailable"}
    project_policy = (effective_policy.get("projects") or {}).get(project, {})
    rule = (taxonomy.get("profiles") or {}).get(contract["finding_rule"], {})
    if (not taxonomy.get("architecture_sensitive_rules_enabled")
            or taxonomy.get("effective_policy_status") != "ACTIVE"
            or taxonomy.get("expected_snapshot_id") != taxonomy.get("observed_snapshot_id")
            or not taxonomy.get("expected_snapshot_id") or doctrine_identity.get("status") != "bound"
            or rule.get("mode") == "disabled" or rule.get("applicability_reason") != "active_effective_profile"
            or not project_policy.get("proposal_identity") or not project_policy.get("approval_entry_id")):
        return {"status": "unavailable"}
    result = {"authority": "sage_effective_architecture_policy_v1", "project": project,
              "atlas_snapshot_id": taxonomy["expected_snapshot_id"], "source_project": deepcopy(project_policy),
              "effective_rule": deepcopy(rule),
              "runtime_context": {k: deepcopy(v) for k, v in runtime_context.items()
                                  if k != "effective_target_policy"},
              "doctrine": deepcopy(doctrine_identity)}
    result["identity"] = _import_evidence_digest(result)
    return result


def _audit_import_source_refusal(file_data: dict, import_record: dict, snapshot_id: str,
                               source_binding: dict, contract: dict) -> str | None:
    """Shared current record/source admission; no second parser or snapshot read."""
    from tools.core.atlas_ast_contract import AST_CONTRACT_VERSION
    parser = file_data.get("parser_evidence")
    if (file_data.get("language") not in contract["languages"]
            or not isinstance(parser, dict) or parser.get("status") != "observed"
            or file_data.get("ast_contract_version") != AST_CONTRACT_VERSION):
        return "parser_or_language_not_supported"
    record_keys = {"source", "raw_source", "name", "kind", "scope"}
    if (not isinstance(import_record, dict) or set(import_record) != record_keys
            or any(not isinstance(v, str) or not v for v in import_record.values())
            or import_record["kind"] not in contract["record_kinds"]
            or import_record["scope"] != contract["record_scope"]):
        return "unsupported_import_record"
    if (not snapshot_id or source_binding.get("status") != "ok"
            or source_binding.get("source_hash") != file_data.get("hash")):
        return "source_snapshot_unavailable_or_mismatch"
    return None


def build_audit_import_subject(*, project: str, rel_path: str, file_data: dict,
                               snapshot_id: str, source_binding: dict, policy: dict,
                               import_record: dict, source_layer: dict | None = None,
                               target_layer: dict | None = None, mapping: dict | None = None,
                               restriction_trace: dict | None = None, module_trace: dict | None = None,
                               rule_key: str | None = None) -> dict:
    """Finite captured record evidence. It never changes a finding or its policy."""
    root = audit_import_subject_contract()
    contract = audit_import_rule_contract(rule_key) if rule_key else root
    if contract is None:
        return unavailable_audit_import_subject("unsupported_import_rule")
    refusal = _audit_import_source_refusal(file_data, import_record, snapshot_id, source_binding, contract)
    if refusal:
        return unavailable_audit_import_subject(refusal, rule_key=rule_key)
    is_extended = contract["version"] != root["version"]
    is_module = "decision_branch" in contract
    is_restriction = is_extended and not is_module
    source_layer = source_layer or {}
    if is_module:
        from tools.core.import_classifier import module_source_context, module_api_import_evidence
        from tools.core.language_registry import language_for_extension
        context = policy.get("runtime_context", {})
        if not isinstance(module_trace, dict):
            return unavailable_audit_import_subject("module_binding_unavailable", rule_key=rule_key)
        entries = module_trace.get("api_entry_policy")
        if entries is not None and (not isinstance(entries, dict) or not isinstance(entries.get("configured_entries"), list)):
            return unavailable_audit_import_subject("api_entry_policy_shape_unsupported", rule_key=rule_key)
        observed = module_api_import_evidence(module_source_context(rel_path, context.get("module_container", "")),
            import_record["raw_source"], module_container=context.get("module_container", ""),
            primary_alias=context.get("primary_alias", ""), api_entry_policy=module_trace.get("api_entry_policy"))
        if (file_data["language"] != language_for_extension(Path(rel_path).suffix)
                or context.get("module_container_resolution") != "captured_runtime_value"
                or not isinstance(context.get("module_runtime_configuration"), dict)
                or context.get("primary_alias") != context.get("module_runtime_configuration", {}).get("primary_alias")
                or policy.get("effective_rule", {}).get("rule") != rule_key
                or not observed["violated"] or observed["trace"]["decision_branch"] != contract["decision_branch"]
                or _import_evidence_digest(module_trace) != _import_evidence_digest(observed["trace"])):
            return unavailable_audit_import_subject("module_binding_unavailable", rule_key=rule_key)
    if is_restriction:
        from tools.core.language_registry import language_for_extension
        trace = restriction_trace or {}
        if (file_data["language"] != language_for_extension(Path(rel_path).suffix)
                or trace.get("actual_language") != file_data["language"]
                or trace.get("row", {}).get(contract["rule_key_property"]) != rule_key
                or policy.get("effective_rule", {}).get("rule") != rule_key
                or not trace.get("violated") or not trace.get("pointer")
                or not source_layer.get("pointer") or trace.get("matched_source_glob") is not None
                or trace.get("primary_alias") != policy.get("runtime_context", {}).get("primary_alias")):
            return unavailable_audit_import_subject("restriction_binding_unavailable", rule_key=rule_key)
    elif not is_module:
        if not (mapping or {}).get("violated") or not (mapping or {}).get("pointer") \
                or not source_layer.get("pointer") or not (target_layer or {}).get("pointer"):
            return unavailable_audit_import_subject("policy_binding_unavailable")
    if not policy.get("identity") or policy.get("atlas_snapshot_id") != snapshot_id:
        return unavailable_audit_import_subject("policy_binding_unavailable", rule_key=rule_key)
    subject = {"record": deepcopy(import_record), "source_range": None,
               "occurrence_binding": "not_recorded", "target_file_binding": "not_established"}
    context = {"project_key": project, "atlas_rel_path": rel_path, "atlas_snapshot_id": snapshot_id,
               "source_hash": file_data["hash"], "ast_contract_version": file_data.get("ast_contract_version"),
               "language": file_data["language"]}
    identity_context = {**context, "record": subject["record"]}
    if is_extended:
        identity_context.update(kind=contract["kind"], version=contract["version"])
    subject["id"] = _import_evidence_digest(identity_context)
    result = {"kind": contract["kind"], "version": contract["version"], "status": "bound_record_set",
              "evidence_granularity": contract["granularity"], **context, "subjects": [subject],
              "policy": deepcopy(policy), "source_layer": deepcopy(source_layer),
              "omitted_subject_count": 0, "live_source_currentness": "not_evaluated",
              "causal_relation": "not_evaluated", "target_native_effective_policy": "not_evaluated",
              "runtime_proof": "not_performed"}
    if is_module:
        result.pop("source_layer")
        result.update(rule=rule_key, decision_kind=contract["decision_kind"], module_trace=deepcopy(module_trace))
    elif is_restriction:
        result.update(rule=rule_key, decision_kind=contract["decision_kind"], restriction_trace=deepcopy(restriction_trace))
    else:
        result.update(target_layer=deepcopy(target_layer), mapping=deepcopy(mapping))
    result["binding_id"] = _import_evidence_digest(result)
    return result


def merge_audit_import_subjects(left: dict, right: dict, *, apply_budget: bool = True) -> dict:
    """Union members inside existing finding grouping; never invent occurrence counts."""
    cap = audit_import_subject_contract()["max_subjects"]
    refusal_rule = left.get("rule") or right.get("rule")
    if left == right and len(left.get("subjects", [])) <= cap:
        return left
    if left.get("status") == "unavailable" or right.get("status") == "unavailable":
        if left == right:
            return left
        return unavailable_audit_import_subject("subject_metadata_incomplete", rule_key=refusal_rule)
    if left.get("omitted_subject_count") or right.get("omitted_subject_count"):
        return unavailable_audit_import_subject("omitted_subject_membership_unavailable", rule_key=refusal_rule)
    ignored = {"subjects", "binding_id", "status", "omitted_subject_count"}
    if {k: v for k, v in left.items() if k not in ignored} != {k: v for k, v in right.items() if k not in ignored}:
        return unavailable_audit_import_subject("subject_metadata_conflict", rule_key=refusal_rule)
    subjects = {row["id"]: row for row in [*left["subjects"], *right["subjects"]]}
    ordered = [subjects[key] for key in sorted(subjects)]
    cap = cap if apply_budget else len(ordered)
    result = deepcopy(left)
    result.pop("binding_id", None)
    result["subjects"] = ordered[:cap]
    result["omitted_subject_count"] = max(0, len(ordered) - cap) + left["omitted_subject_count"] + right["omitted_subject_count"]
    result["status"] = "partial" if result["omitted_subject_count"] else "bound_record_set"
    result["binding_id"] = _import_evidence_digest(result)
    return result


def project_audit_import_subject(violation: dict, *, raw_dir: Path,
                               project: str, rel_path: str, cache: dict | None = None) -> dict:
    """Verify additive context against selected primary/source/generation; no fallback."""
    from tools.core.analysis_snapshot_lineage import load_atlas_commit
    from tools.core.architecture_blueprints import load_effective_architecture_policy_context, resolve_effective_architecture_project
    from tools.core.audit_rules import build_effective_project_rule_taxonomy
    from tools.core.atlas_io import load_atlas_file_metadata
    from tools.core.source_snapshot_reader import read_source_snapshot_binding
    from tools.core.layer_resolver import resolve_layer_evidence, layer_violation_evidence, layer_import_restriction_evidence
    root = audit_import_subject_contract()
    rule_key = violation.get("rule") or root["finding_rule"]
    contract = audit_import_rule_contract(rule_key) or root
    is_extended = contract["version"] != root["version"]
    is_module = "decision_branch" in contract
    is_restriction = is_extended and not is_module
    evidence = violation.get("finding_evidence")
    if not isinstance(evidence, dict):
        return unavailable_audit_import_subject("legacy_evidence_missing", rule_key=rule_key)
    if evidence.get("status") == "unavailable":
        return unavailable_audit_import_subject(str(evidence.get("reason") or "unavailable"), rule_key=rule_key)
    memo = cache if cache is not None else {}
    try:
        unsigned = {k: v for k, v in evidence.items() if k != "binding_id"}
        if (evidence.get("binding_id") != _import_evidence_digest(unsigned)
                or evidence.get("kind") != contract["kind"] or evidence.get("version") != contract["version"]
                or evidence.get("status") not in {"bound_record_set", "partial"}
                or evidence.get("project_key") != project or evidence.get("atlas_rel_path") != rel_path
                or violation.get("rule") != contract["finding_rule"]):
            raise ValueError("invalid_or_mismatched_subject")
        if is_extended and (evidence.get("rule") != rule_key or evidence.get("decision_kind") != contract["decision_kind"]):
            raise ValueError("invalid_restriction_discriminant")
        root_key = (str(Path(raw_dir).resolve()), "root")
        if root_key not in memo:
            policy, snapshot = load_effective_architecture_policy_context(raw_dir)
            commit = load_atlas_commit(raw_dir)
            memo[root_key] = (policy, snapshot, commit, capture_audit_import_doctrine_identity(), read_audit_finding_manifest(raw_dir))
        current_policy, snapshot, commit, doctrine, manifest = memo[root_key]
        if (commit.get("state") != "complete" or evidence["atlas_snapshot_id"] != snapshot
                or evaluate_finding_manifest(manifest, expected_snapshot_id=snapshot, requested_project=project)["status"] != "PASS"):
            raise ValueError("generation_unavailable_or_mismatch")
        if is_extended:
            row_key = (root_key[0], project, rel_path, rule_key, _import_evidence_digest(violation))
            if row_key not in memo:
                memo[row_key] = _audit_import_finding_is_recorded(raw_dir, project, rel_path, violation)
            if not memo[row_key]:
                raise ValueError("restriction_finding_not_recorded")
        captured = evidence["policy"]
        if is_module:
            from tools.core.pipeline_policy import capture_api_entry_policy, module_import_runtime_configuration
            config_key = (root_key[0], "module_runtime_configuration")
            if config_key not in memo:
                memo[config_key] = module_import_runtime_configuration()
            if (_import_evidence_digest(captured["runtime_context"]["module_runtime_configuration"])
                    != _import_evidence_digest(memo[config_key])):
                raise ValueError("module_runtime_configuration_changed")
            entries = evidence["module_trace"]["api_entry_policy"]
            if entries is not None:
                entry_key = (root_key[0], "api_entry_policy")
                if entry_key not in memo:
                    memo[entry_key] = capture_api_entry_policy()
                if _import_evidence_digest(entries) != _import_evidence_digest(memo[entry_key]):
                    raise ValueError("api_entry_policy_changed")
        current_taxonomy = build_effective_project_rule_taxonomy(project, current_policy,
            expected_snapshot_id=snapshot, runtime_context=captured["runtime_context"])
        expected_policy = capture_audit_import_policy(project, current_policy, current_taxonomy,
            doctrine, captured["runtime_context"], rule_key=rule_key)
        if (_import_evidence_digest({k: v for k, v in captured.items() if k != "identity"}) != captured["identity"]
                or captured["source_project"] != current_policy["projects"][project]
                or captured["doctrine"] != doctrine
                or captured != expected_policy
                or not resolve_effective_architecture_project(current_policy, project, expected_snapshot_id=snapshot)["architecture_sensitive_rules_enabled"]):
            raise ValueError("policy_unavailable_or_mismatch")
        file_key = (root_key[0], project, rel_path)
        if file_key not in memo:
            meta = load_atlas_file_metadata(raw_dir, project, rel_path)
            binding = read_source_snapshot_binding(raw_dir, project, rel_path, expected_hash=meta.get("hash") if isinstance(meta, dict) else None)
            binding.pop("content", None)
            memo[file_key] = (meta, binding)
        meta, binding = memo[file_key]
        if not isinstance(meta, dict) or meta.get("hash") != evidence["source_hash"]:
            raise ValueError("source_unavailable_or_mismatch")
        subjects = evidence["subjects"]
        if not isinstance(subjects, list) or not 0 < len(subjects) <= contract["max_subjects"]:
            raise ValueError("invalid_subject_set")
        for subject in subjects:
            record = subject["record"]
            if record not in meta.get("import_records", []):
                raise ValueError("subject_not_in_primary_atlas")
            if is_module:
                from tools.core.import_classifier import module_source_context, module_api_import_evidence
                context = captured["runtime_context"]
                trace = module_api_import_evidence(module_source_context(rel_path, context["module_container"]),
                    record["raw_source"], module_container=context["module_container"],
                    primary_alias=context["primary_alias"], api_entry_policy=evidence["module_trace"]["api_entry_policy"])
                expected = build_audit_import_subject(project=project, rel_path=rel_path, file_data=meta,
                    snapshot_id=snapshot, source_binding=binding, policy=captured, import_record=record,
                    module_trace=trace["trace"], rule_key=rule_key)
                semantic_fields = ["module_trace", "rule", "decision_kind"]
            elif is_restriction:
                source_trace = resolve_layer_evidence(rel_path)
                pointer = evidence["restriction_trace"]["pointer"]
                index_text = pointer.removeprefix(contract["rule_source"] + "/")
                if not index_text.isdecimal() or str(int(index_text)) != index_text:
                    raise ValueError("invalid_restriction_pointer")
                index = int(index_text)
                restriction = audit_import_restriction_rows()[index]
                trace = layer_import_restriction_evidence(restriction, index, rel_path=rel_path,
                    source_layer=source_trace["layer"], import_path=record["source"], raw_import_path=record["raw_source"],
                    language=meta.get("language", ""), primary_alias=captured["runtime_context"]["primary_alias"])
                expected = build_audit_import_subject(project=project, rel_path=rel_path, file_data=meta,
                    snapshot_id=snapshot, source_binding=binding, policy=captured, import_record=record,
                    source_layer=source_trace, restriction_trace=trace, rule_key=rule_key)
                semantic_fields = ["source_layer", "restriction_trace", "rule", "decision_kind"]
            else:
                source_trace = resolve_layer_evidence(rel_path)
                # Exact recorded resolver label only; not independently resolved endpoint.
                from tools.core.config import PRIMARY_ALIAS
                target = record["source"].replace(PRIMARY_ALIAS, "") if record["source"].startswith(PRIMARY_ALIAS) else record["source"]
                target_trace = resolve_layer_evidence(target)
                mapping = layer_violation_evidence(source_trace["layer"], target_trace["layer"], meta.get("language", ""))
                expected = build_audit_import_subject(project=project, rel_path=rel_path, file_data=meta,
                    snapshot_id=snapshot, source_binding=binding, policy=captured, import_record=record,
                    source_layer=source_trace, target_layer=target_trace, mapping=mapping)
                semantic_fields = ["source_layer", "target_layer", "mapping"]
            if expected.get("status") != "bound_record_set" or expected["subjects"][0] != subject:
                raise ValueError("subject_source_binding_invalid")
            if set(evidence) != set(expected):
                raise ValueError("unsupported_subject_fields")
            semantic_fields += ["evidence_granularity", "ast_contract_version", "language", "live_source_currentness", "causal_relation", "target_native_effective_policy", "runtime_proof"]
            # Extended typed source context: Python's bool/int and int/float
            # equality must not admit a differently typed primary JSON value.
            if any((_import_evidence_digest(evidence.get(k)) != _import_evidence_digest(expected[k])
                    if is_extended else evidence.get(k) != expected[k]) for k in semantic_fields):
                raise ValueError("subject_semantics_invalid")
        omitted = evidence["omitted_subject_count"]
        if type(omitted) is not int or omitted < 0 or (evidence["status"] == "partial") != (omitted > 0):
            raise ValueError("invalid_omission_status")
        if len({s["id"] for s in subjects}) != len(subjects) or load_atlas_commit(raw_dir).get("snapshot_id") != snapshot:
            raise ValueError("duplicate_or_changed_generation")
        if is_extended and read_audit_finding_manifest(raw_dir) != manifest:
            raise ValueError("changed_finding_generation")
        return deepcopy(evidence)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError):
        return unavailable_audit_import_subject("source_policy_or_subject_verification_failed", rule_key=rule_key)


def _audit_import_finding_is_recorded(raw_dir: Path, project: str, rel_path: str, violation: dict) -> bool:
    """Bind consumed execution context to the existing complete primary finding, not caller hashes."""
    from contextlib import closing
    import sqlite3
    from tools.core.operational_limits import sqlite_read_timeout_seconds
    from tools.core.unmanaged_atomic_io import sqlite_read_only_uri
    try:
        with closing(sqlite3.connect(sqlite_read_only_uri(Path(raw_dir) / "codemaps.db"), uri=True,
                                     timeout=float(sqlite_read_timeout_seconds()))) as conn:
            row = conn.execute(
                """SELECT 1 FROM findings fi JOIN files f ON f.file_id = fi.file_id
                   WHERE fi.engine_name = ? AND fi.code = ? AND f.project_key = ?
                     AND f.rel_path = ? AND fi.message = ? LIMIT 1;""",
                ("audit_report", violation.get("rule"), project, rel_path,
                 canonical_audit_finding_message(violation)),
            ).fetchone()
        return row is not None
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return False


def read_audit_finding_manifest(raw_dir: Path) -> dict:
    """Shared read of the existing relational generation owner, never JSON rescue."""
    from contextlib import closing
    import sqlite3
    from tools.core.operational_limits import sqlite_read_timeout_seconds
    from tools.core.unmanaged_atomic_io import native_filesystem_path, sqlite_read_only_uri
    from tools.core.json_syntax import loads_json_strict
    db = Path(raw_dir) / "codemaps.db"
    if not Path(native_filesystem_path(db)).is_file():
        return {}
    try:
        with closing(sqlite3.connect(sqlite_read_only_uri(db), uri=True, timeout=float(sqlite_read_timeout_seconds()))) as conn:
            row = conn.execute("SELECT fact_value FROM artifact_facts WHERE artifact_name=? AND fact_key=?;",
                               (AUDIT_FINDINGS_FACT_ARTIFACT, AUDIT_FINDINGS_FACT_KEY)).fetchone()
        value = loads_json_strict(row[0]) if row else {}
        return value if isinstance(value, dict) else {}
    except (sqlite3.Error, TypeError, ValueError):
        return {}


def load_audit_presentation_order() -> dict[str, Any]:
    """Load the one presentation descriptor; invalid policy has no local fallback."""
    payload = json.loads(AUDIT_PRESENTATION_CONTRACT_PATH.read_text(encoding="utf-8"))
    order = payload.get("work_queue_brief_policy", {}).get("audit_presentation_order")
    if not isinstance(order, dict) or order.get("version") != "v1" or order.get("basis") != "presentation_only":
        raise ValueError("Audit presentation descriptor is missing or unsupported.")
    keys = order.get("keys")
    if (not isinstance(keys, list) or len(keys) != 4
        or keys[0] != "stored_row_level_tier" or keys[1] != "indexed_rel_path_binary"
        or keys[2] != "rule_code_binary" or keys[3] != "canonical_stored_finding_json_binary"):
        raise ValueError("Audit presentation descriptor has unsupported sort keys.")
    authority_rule = order.get("authority_rule")
    if not isinstance(authority_rule, str) or not authority_rule.strip():
        raise ValueError("Audit presentation descriptor requires an explicit authority boundary.")
    tiers = order.get("severity_tiers")
    unknown = order.get("unknown_tier")
    if (not isinstance(tiers, dict) or not tiers or type(unknown) is not int
        or any(not isinstance(k, str) or not re.fullmatch(r"[a-z_]+", k)
            or type(v) is not int or v < 0 for k,v in tiers.items())
        or unknown <= max(tiers.values())):
        raise ValueError("Audit presentation tiers must be safe literals with an explicit last unknown tier.")
    return order


def audit_severity_order_sql(order: Mapping[str, Any]) -> str:
    """Only validated policy literals enter SQL; user filters remain parameters."""
    clauses = " ".join(f"WHEN '{name}' THEN {rank}" for name,rank in order["severity_tiers"].items())
    return f"CASE LOWER(fi.severity) {clauses} ELSE {order['unknown_tier']} END"


def audit_presentation_order_sql(order: Mapping[str, Any]) -> str:
    return f"{audit_severity_order_sql(order)}, f.rel_path COLLATE BINARY, fi.code COLLATE BINARY, fi.message COLLATE BINARY"


def audit_presentation_summary(order: Mapping[str, Any]) -> dict[str, Any]:
    return {"status":"applied", "basis":order["basis"], "keys":list(order["keys"]),
        "policy_sha256":hashlib.sha256(canonical_audit_finding_message(order).encode("utf-8")).hexdigest(),
        "unknown_row_level":"preserved", "causal_relation":"not_evaluated",
        "candidate_universe_equality":"not_evaluated",
        "meaning":"Presentation order only; not root-cause priority, equal coverage or mutation authority."}


class _AuditPresentationKey:
    """Compare whole-row JSON lazily, only after the cheaper fields tie."""
    __slots__ = ("prefix", "row", "message")

    def __init__(self, row: Mapping[str, Any], order: Mapping[str, Any]) -> None:
        self.prefix = (order["severity_tiers"].get(audit_finding_severity(row).lower(), order["unknown_tier"]),
            row["atlas_rel_path"], audit_finding_code(row))
        self.row = row
        self.message: str | None = None

    def _message(self) -> str:
        if self.message is None:
            self.message = canonical_audit_finding_message(self.row)
        return self.message

    def __lt__(self, other: _AuditPresentationKey) -> bool:
        if self.prefix != other.prefix:
            return self.prefix < other.prefix
        return self._message() < other._message()


def select_audit_presentation_rows(
    rows: list[dict[str, Any]], *, limit: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Rank already scoped producer rows before capping, without resolving target paths."""
    unavailable = {"status":"unavailable", "basis":"presentation_only",
        "causal_relation":"not_evaluated", "candidate_universe_equality":"not_evaluated"}
    try:
        order = load_audit_presentation_order()
    except (OSError, ValueError, TypeError, AttributeError):
        return rows[:limit], {**unavailable, "reason":"invalid_or_missing_presentation_descriptor; existing input order retained"}
    for row in rows:
        path = row.get("atlas_rel_path")
        project = str(row.get("project_key") or row.get("project") or "").strip()
        if (not project or not isinstance(path, str) or not path or path != path.strip()
            or "\\" in path or ":" in path or path.startswith("/")
            or any(part in {"", ".", ".."} for part in path.split("/"))):
            return rows[:limit], {**unavailable, "reason":"producer_recorded_indexed_identity_unavailable; existing input order retained"}
    selected = heapq.nsmallest(limit, rows, key=lambda row: _AuditPresentationKey(row, order))
    return selected, audit_presentation_summary(order)


def load_generation_composition_contract() -> dict[str, Any]:
    payload = json.loads(
        GENERATION_COMPOSITION_CONTRACT_PATH.read_text(encoding="utf-8")
    )
    contract = payload.get("generation_composition") if isinstance(payload, dict) else None
    if not isinstance(contract, dict):
        raise ValueError("Artifact freshness contract has no generation_composition section.")
    sqlite_fact = contract.get("sqlite_fact")
    if (
        contract.get("kind") != AUDIT_FINDINGS_GENERATION_KIND
        or contract.get("version") != AUDIT_FINDINGS_GENERATION_VERSION
        or not isinstance(sqlite_fact, dict)
        or sqlite_fact.get("artifact_name") != AUDIT_FINDINGS_FACT_ARTIFACT
        or sqlite_fact.get("fact_key") != AUDIT_FINDINGS_FACT_KEY
    ):
        raise ValueError("Audit finding generation constants drifted from the freshness SSoT.")
    return contract


def generation_consumer_profile(profile: str) -> dict[str, Any]:
    contract = load_generation_composition_contract()
    profiles = contract.get("consumer_profiles")
    profiles = profiles if isinstance(profiles, dict) else {}
    row = profiles.get(str(profile or ""))
    if not isinstance(row, dict):
        raise ValueError(f"Unknown finding-generation consumer profile: {profile}")
    return row


def evaluate_finding_manifest(
    manifest: Mapping[str, Any] | None,
    *,
    expected_snapshot_id: str,
    requested_project: str = "",
) -> dict[str, Any]:
    row = manifest if isinstance(manifest, Mapping) else {}
    project = str(requested_project or "").strip()
    all_projects = project in {"", "*", "all", "ALL"}
    covered_projects = {
        str(item).strip()
        for item in row.get("covered_projects", [])
        if str(item).strip()
    }
    structural = (
        row.get("kind") == AUDIT_FINDINGS_GENERATION_KIND
        and row.get("version") == AUDIT_FINDINGS_GENERATION_VERSION
        and bool(row.get("snapshot_id"))
        and isinstance(row.get("composition_depth"), int)
        and bool(covered_projects)
    )
    snapshot_match = structural and str(row.get("snapshot_id") or "") == str(expected_snapshot_id or "")
    coverage_match = bool(
        structural
        and (
            row.get("full_repository_claim") is True
            if all_projects
            else project in covered_projects
        )
    )
    passed = bool(snapshot_match and coverage_match)
    return {
        "status": "PASS" if passed else "FAIL",
        "snapshot_match": bool(snapshot_match),
        "coverage_match": bool(coverage_match),
        "expected_snapshot_id": str(expected_snapshot_id or "") or None,
        "observed_snapshot_id": str(row.get("snapshot_id") or "") or None,
        "requested_project": project or "*",
        "covered_projects": sorted(covered_projects),
        "full_repository_claim": row.get("full_repository_claim") is True,
        "generation_mode": str(row.get("generation_mode") or "unavailable"),
        "composition_depth": int(row.get("composition_depth") or 0)
        if isinstance(row.get("composition_depth"), int)
        else 0,
        "reason": (
            "current_generation_and_coverage"
            if passed
            else "manifest_unavailable_or_invalid"
            if not structural
            else "snapshot_mismatch"
            if not snapshot_match
            else "requested_scope_not_covered"
        ),
    }
