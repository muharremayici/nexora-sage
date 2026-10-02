import json
import sys
import fnmatch
import re
import time
from collections import defaultdict
from pathlib import Path
from tools.core.artifact_contracts import AUDIT_REPORT_JSON_PATH, AUDIT_REPORT_TEXT_PATH
from tools.core.analysis_snapshot_lineage import load_atlas_commit, write_current_atlas_lineage
from tools.core.atlas_integrity import validate_atlas_commit
from tools.core.artifact_store import flush_shadow_writes
from tools.core.architecture_blueprints import load_effective_architecture_policy_context
from tools.core.audit_report import invalidate_audit_report_cache
from tools.core.audit_rules import (
    build_effective_project_rule_taxonomy,
    build_rule_taxonomy,
    canonical_alias_boundary_decision,
    filter_violations_by_project_taxonomy,
    path_alias_applies_to_file,
    violates_canonical_alias_boundary,
)
from tools.core.atlas_io import load_atlas_data, resolve_atlas_data
from tools.core.config import ROOT, RAW_DIR, REPORTS_DIR, DOCTRINE, save_json_atomic, save_text_atomic
from tools.core.doctrine_contract import require_doctrine_mapping
from tools.core.logger import logger
from tools.core.operational_limits import artifact_shadow_flush_timeout_seconds
from tools.core.pipeline_policy import get_api_entry_filenames, get_audit_report_sections, get_module_root_name, resolve_loc_finding_semantics
from tools.core.projects_registry import project_display_name, resolve_runtime_projects
from tools.core.layer_resolver import resolve_layer, is_violation
from tools.core.language_registry import language_for_extension
from tools.core.watchdog_runtime_contract import (
    normalize_watchdog_scope_refs,
    watchdog_artifact_identity,
    watchdog_artifact_path,
)
from tools.core.analysis_scope_authority import (
    BOUNDED_PROJECT_SELECTION,
    COMPLETE_REPOSITORY,
    bind_consumer_projects,
    load_scope_authority_for_consumer,
)

VIOLATION_LABELS = require_doctrine_mapping("violation_labels")
RELATIVE_IMPORTS_NO_ALIAS_RULE = "relative_imports_no_alias"

DEFAULT_VIOLATION_KEYS = list(VIOLATION_LABELS.keys())
IMMUTABLE_KEY_TRUTHINESS_FEATURE = re.compile(
    r"TypeScript:ImmutableKeyTruthinessGuard:([A-Za-z_$][A-Za-z0-9_$]*):([1-9][0-9]*):([1-9][0-9]*)"
)
FINITE_NUMBER_NAN_ONLY_FEATURE = re.compile(
    r"TypeScript:FiniteNumberNaNOnlyGuard:([A-Za-z_$][A-Za-z0-9_$]*):([1-9][0-9]*):([1-9][0-9]*)"
)
ZOD_OBJECT_BROADER_CAST_FEATURE = re.compile(
    r"TypeScript:ZodObjectBroaderCast:([A-Za-z_$][A-Za-z0-9_$]*):([A-Za-z_$][A-Za-z0-9_$]*):([A-Za-z_$][A-Za-z0-9_$]*):([1-9][0-9]*):([1-9][0-9]*)"
)
UNAWAITED_ASYNC_HELPER_FEATURE = re.compile(
    r"TypeScript:UnawaitedAsyncHelperCall:([A-Za-z_$][A-Za-z0-9_$]*):([1-9][0-9]*):([1-9][0-9]*)"
)
CAUGHT_ASYNC_AWAIT_FEATURE = re.compile(
    r"TypeScript:CaughtAsyncAwaitNoRethrow:([A-Za-z_$][A-Za-z0-9_$]*):([1-9][0-9]*):([1-9][0-9]*)"
)
RETURNED_QUEUE_CATCH_FEATURE = re.compile(
    r"TypeScript:ReturnedQueueCatchNoRethrow:([A-Za-z_$][A-Za-z0-9_$]*):([1-9][0-9]*):([1-9][0-9]*)"
)
QUEUED_LOOKUP_FALSY_FEATURE = re.compile(
    r"TypeScript:QueuedLookupFalsyFallthrough:([A-Za-z_$][A-Za-z0-9_$]*):([1-9][0-9]*):([1-9][0-9]*)"
)
REPOSITORY_BULK_DIRECT_WRITE_FEATURE = re.compile(
    r"TypeScript:RepositoryBulkDirectWrite:([A-Za-z_$][A-Za-z0-9_$]*):(bulkCreate|bulkUpdate):([1-9][0-9]*):([1-9][0-9]*)"
)
FAILED_SAFE_PARSE_RAW_FALLBACK_FEATURE = re.compile(
    r"TypeScript:FailedSafeParseReturnsRaw:([A-Za-z_$][A-Za-z0-9_$]*):([A-Za-z_$][A-Za-z0-9_$]*):([1-9][0-9]*):([1-9][0-9]*)"
)
REPOSITORY_DIRECT_READ_RETURN_FEATURE = re.compile(
    r"TypeScript:RepositoryDirectReadReturn:([A-Za-z_$][A-Za-z0-9_$]*):(getById):([1-9][0-9]*):([1-9][0-9]*)"
)
REPOSITORY_INLINE_READ_RETURN_FEATURE = re.compile(
    r"TypeScript:RepositoryInlineReadReturn:([A-Za-z_$][A-Za-z0-9_$]*):(getById):([1-9][0-9]*):([1-9][0-9]*)"
)
REPOSITORY_COLLECTION_READ_RETURN_FEATURE = re.compile(
    r"TypeScript:RepositoryCollectionReadReturn:([A-Za-z_$][A-Za-z0-9_$]*):(getAll(?:By[A-Z][A-Za-z0-9_$]*)?):(const_binding|inline_await|filtered_binding):([1-9][0-9]*):([1-9][0-9]*)"
)


def _runtime_review_file_evidence(project: str, rel_path: str, file_data: dict):
    """Require observed, source-bound TypeScript Atlas evidence for review candidates."""
    if not isinstance(file_data, dict) or file_data.get("language") != "typescript":
        return None
    if file_data.get("project_key", project) != project \
            or file_data.get("atlas_rel_path", rel_path) != rel_path:
        return None
    parser = file_data.get("parser_evidence")
    if not isinstance(parser, dict) or parser.get("status") != "observed" \
            or parser.get("parser_kind") != "typescript_compiler_api":
        return None
    source_hash = str(file_data.get("hash") or "")
    if not source_hash or source_hash == "err":
        return None
    expected_ref = f"{project}::{file_data.get('repo_relative_path') or rel_path}"
    target_ref = str(file_data.get("target_ref") or expected_ref)
    if target_ref != expected_ref:
        return None
    try:
        loc = int(file_data.get("loc") or 0)
    except (TypeError, ValueError):
        return None
    if loc <= 0:
        return None
    features = file_data.get("features")
    if not isinstance(features, list):
        return None
    return source_hash, target_ref, loc, sorted({item for item in features if isinstance(item, str)})


def _immutable_key_review_candidates(project: str, rel_path: str, file_data: dict) -> list[dict]:
    """Project bounded AST facts outside the violation/quality-gate lane."""
    evidence = _runtime_review_file_evidence(project, rel_path, file_data)
    if evidence is None:
        return []
    source_hash, target_ref, loc, features = evidence
    rows = []
    for feature in features:
        match = IMMUTABLE_KEY_TRUTHINESS_FEATURE.fullmatch(feature)
        if not match:
            continue
        key, guard_text, sink_text = match.groups()
        guard_line, sink_line = int(guard_text), int(sink_text)
        if not (guard_line < sink_line <= loc):
            continue
        rows.append({
            "kind": "immutable_key_truthiness_guard_bypass",
            "project": project,
            "file": rel_path,
            "target_ref": target_ref,
            "property": key,
            "guard_line": guard_line,
            "sink_line": sink_line,
            "source_hash": source_hash,
            "evidence_source": "typescript_syntax_ast",
            "atlas_feature": feature,
            "confidence": "needs_runtime_proof",
            "actionability": "review",
            "claim_boundary": "Static guard-to-spread candidate only. Upstream validation, callers and persisted effects are unproven.",
        })
    return rows


def _finite_number_review_candidates(project: str, rel_path: str, file_data: dict) -> list[dict]:
    """Expose a NaN-only numeric acceptance guard as unproven review evidence."""
    evidence = _runtime_review_file_evidence(project, rel_path, file_data)
    if evidence is None:
        return []
    source_hash, target_ref, loc, features = evidence
    rows = []
    for feature in features:
        match = FINITE_NUMBER_NAN_ONLY_FEATURE.fullmatch(feature)
        if not match:
            continue
        subject, guard_text, sink_text = match.groups()
        guard_line, sink_line = int(guard_text), int(sink_text)
        if not (guard_line < sink_line <= loc):
            continue
        rows.append({
            "kind": "finite_number_nan_only_acceptance",
            "project": project,
            "file": rel_path,
            "target_ref": target_ref,
            "subject": subject,
            "guard_line": guard_line,
            "sink_line": sink_line,
            "source_hash": source_hash,
            "evidence_source": "typescript_syntax_ast",
            "atlas_feature": feature,
            "confidence": "needs_runtime_proof",
            "actionability": "review",
            "claim_boundary": "The local guard rejects non-number and NaN before returning the same value, but upstream finite validation, callers and persistence are unproven.",
        })
    return rows


def _schema_broader_cast_review_candidates(project: str, rel_path: str, file_data: dict) -> list[dict]:
    """Keep same-file Zod object/cast mismatch in the review-only lane."""
    evidence = _runtime_review_file_evidence(project, rel_path, file_data)
    if evidence is None:
        return []
    source_hash, target_ref, loc, features = evidence
    rows = []
    for feature in features:
        match = ZOD_OBJECT_BROADER_CAST_FEATURE.fullmatch(feature)
        if not match:
            continue
        _schema_name, _type_name, subject, schema_text, cast_text = match.groups()
        schema_line, cast_line = int(schema_text), int(cast_text)
        if not (schema_line < cast_line <= loc):
            continue
        rows.append({
            "kind": "zod_object_broader_cast",
            "project": project,
            "file": rel_path,
            "target_ref": target_ref,
            "subject": subject,
            "guard_line": schema_line,
            "sink_line": cast_line,
            "source_hash": source_hash,
            "evidence_source": "typescript_syntax_ast",
            "atlas_feature": feature,
            "confidence": "needs_runtime_proof",
            "actionability": "review",
            "claim_boundary": "A direct Zod object shape omits a required field of the directly asserted local type. Package version, input, transformations, callers and actual runtime stripping are unproven.",
        })
    return rows


def _unawaited_async_helper_review_candidates(project: str, rel_path: str, file_data: dict) -> list[dict]:
    """Project a direct same-file async call as review evidence, never a runtime verdict."""
    evidence = _runtime_review_file_evidence(project, rel_path, file_data)
    if evidence is None:
        return []
    source_hash, target_ref, loc, features = evidence
    rows = []
    for feature in features:
        match = UNAWAITED_ASYNC_HELPER_FEATURE.fullmatch(feature)
        if not match:
            continue
        helper, helper_text, call_text = match.groups()
        helper_line, call_line = int(helper_text), int(call_text)
        if not (helper_line < call_line <= loc):
            continue
        rows.append({
            "kind": "unawaited_async_helper_call",
            "project": project,
            "file": rel_path,
            "target_ref": target_ref,
            "subject": helper,
            "guard_line": helper_line,
            "sink_line": call_line,
            "source_hash": source_hash,
            "evidence_source": "typescript_syntax_ast",
            "atlas_feature": feature,
            "confidence": "needs_runtime_proof",
            "actionability": "review",
            "claim_boundary": "A direct call to a same-file async helper containing await is not awaited or returned by its async caller. Whether this is intentional detached work, whether errors are handled elsewhere, and persistence effects are unproven.",
        })
    return rows


def _caught_async_await_review_candidates(project: str, rel_path: str, file_data: dict) -> list[dict]:
    """Expose a log-named catch around an awaited operation, without inferring data loss."""
    evidence = _runtime_review_file_evidence(project, rel_path, file_data)
    if evidence is None:
        return []
    source_hash, target_ref, loc, features = evidence
    rows = []
    for feature in features:
        match = CAUGHT_ASYNC_AWAIT_FEATURE.fullmatch(feature)
        if not match:
            continue
        helper, helper_text, catch_text = match.groups()
        helper_line, catch_line = int(helper_text), int(catch_text)
        if not (helper_line < catch_line <= loc):
            continue
        rows.append({
            "kind": "caught_async_await_without_rethrow",
            "project": project,
            "file": rel_path,
            "target_ref": target_ref,
            "subject": helper,
            "guard_line": helper_line,
            "sink_line": catch_line,
            "source_hash": source_hash,
            "evidence_source": "typescript_syntax_ast",
            "atlas_feature": feature,
            "confidence": "needs_runtime_proof",
            "actionability": "review",
            "claim_boundary": "A same-function awaited operation has a catch containing only log-named calls and no explicit rethrow. Call behavior, authoritative persistence, intentional best-effort handling, caller expectations and data loss are unproven.",
        })
    return rows


def _queued_mutation_review_candidates(project: str, rel_path: str, file_data: dict) -> list[dict]:
    """Project exact returned-queue and nested falsy-lookup syntax without a runtime verdict."""
    evidence = _runtime_review_file_evidence(project, rel_path, file_data)
    if evidence is None:
        return []
    source_hash, target_ref, loc, features = evidence
    patterns = (
        (
            RETURNED_QUEUE_CATCH_FEATURE,
            "returned_queue_catch_without_rethrow",
            "A bound queue element is assigned a then/catch chain and returned by the same async helper; the catch contains only log-named calls. Promise identity, callback behavior, intended queue recovery, caller expectations and persistence outcome are unproven.",
            [
                "When the queued operation rejects, does awaiting the returned mutation promise reject or resolve?",
                "After a rejected operation, does the next mutation for the same key execute and report its own result?",
            ],
        ),
        (
            QUEUED_LOOKUP_FALSY_FEATURE,
            "queued_lookup_falsy_fallthrough",
            "A bound queued callback awaits a lookup and only its truthy branch awaits a later operation; the falsy branch has no explicit rejection. Whether falsy means missing, an intentional no-op, handled elsewhere, or affects durability is unproven.",
            [
                "For each repository-defined falsy lookup result, does the returned mutation promise reject, report absence, or resolve as a documented no-op?",
                "Does the expected durable side effect occur before the public mutation settles on a found record?",
            ],
        ),
    )
    rows = []
    for feature in features:
        for pattern, kind, boundary, verification_questions in patterns:
            match = pattern.fullmatch(feature)
            if not match:
                continue
            helper, guard_text, sink_text = match.groups()
            guard_line, sink_line = int(guard_text), int(sink_text)
            if not (guard_line < sink_line <= loc):
                continue
            rows.append({
                "kind": kind,
                "project": project,
                "file": rel_path,
                "target_ref": target_ref,
                "subject": helper,
                "guard_line": guard_line,
                "sink_line": sink_line,
                "source_hash": source_hash,
                "evidence_source": "typescript_syntax_ast",
                "atlas_feature": feature,
                "confidence": "needs_runtime_proof",
                "actionability": "review",
                "claim_boundary": boundary,
                "verification_questions": verification_questions,
            })
    return rows


def _repository_bulk_direct_write_review_candidates(project: str, rel_path: str, file_data: dict) -> list[dict]:
    """Report a subclass bulk-method direct write without assuming invalid data."""
    evidence = _runtime_review_file_evidence(project, rel_path, file_data)
    if evidence is None:
        return []
    source_hash, target_ref, loc, features = evidence
    rows = []
    for feature in features:
        match = REPOSITORY_BULK_DIRECT_WRITE_FEATURE.fullmatch(feature)
        if not match:
            continue
        cls, method, method_text, write_text = match.groups()
        method_line, write_line = int(method_text), int(write_text)
        if not (method_line < write_line <= loc):
            continue
        rows.append({
            "kind": "repository_bulk_method_direct_storage_write",
            "project": project,
            "file": rel_path,
            "target_ref": target_ref,
            "subject": f"{cls}.{method}",
            "guard_line": method_line,
            "sink_line": write_line,
            "source_hash": source_hash,
            "evidence_source": "typescript_syntax_ast",
            "atlas_feature": feature,
            "confidence": "needs_runtime_proof",
            "actionability": "review",
            "claim_boundary": "A repository subclass bulk method directly awaits an imported storage adapter or this.table write, without same-method base delegation or a recognized collection-wide local validation. Base behavior, schema strength, helper/upstream validation, intentional bypass and invalid-data persistence are unproven.",
            "verification_questions": [
                "What validation or transformation owns each bulk input before the direct storage write?",
                "Does a target-native malformed-record test reject before storage mutates, while a valid record persists?",
            ],
        })
    return rows


def _failed_safeparse_raw_fallback_review_candidates(project: str, rel_path: str, file_data: dict) -> list[dict]:
    """Surface a failed validation read fallback without judging legacy intent."""
    evidence = _runtime_review_file_evidence(project, rel_path, file_data)
    if evidence is None:
        return []
    source_hash, target_ref, loc, features = evidence
    rows = []
    for feature in features:
        match = FAILED_SAFE_PARSE_RAW_FALLBACK_FEATURE.fullmatch(feature)
        if not match:
            continue
        cls, method, guard_text, return_text = match.groups()
        guard_line, return_line = int(guard_text), int(return_text)
        if not (guard_line < return_line <= loc):
            continue
        rows.append({
            "kind": "failed_schema_parse_returns_raw_input",
            "project": project,
            "file": rel_path,
            "target_ref": target_ref,
            "subject": f"{cls}.{method}",
            "guard_line": guard_line,
            "sink_line": return_line,
            "source_hash": source_hash,
            "evidence_source": "typescript_syntax_ast",
            "atlas_feature": feature,
            "confidence": "needs_runtime_proof",
            "actionability": "review",
            "claim_boundary": "A same-method failed safeParse guard returns its original input while the success path returns parsed data. Caller reachability, schema ownership, intentional legacy-data handling and downstream persistence are unproven.",
            "verification_questions": [
                "Is returning invalid legacy data an intentional, documented read contract or should it be quarantined?",
                "Do target-native malformed-read tests prove the intended caller-visible and storage behavior?",
            ],
        })
    return rows


def _repository_direct_read_return_review_candidates(project: str, rel_path: str, file_data: dict) -> list[dict]:
    """Keep a subclass direct-read return in the review lane, not violation counts."""
    evidence = _runtime_review_file_evidence(project, rel_path, file_data)
    if evidence is None:
        return []
    source_hash, target_ref, loc, features = evidence
    rows = []
    for feature in features:
        collection_match = REPOSITORY_COLLECTION_READ_RETURN_FEATURE.fullmatch(feature)
        inline_match = REPOSITORY_INLINE_READ_RETURN_FEATURE.fullmatch(feature)
        match = collection_match or inline_match or REPOSITORY_DIRECT_READ_RETURN_FEATURE.fullmatch(feature)
        if not match:
            continue
        if collection_match:
            cls, method, return_form, read_text, return_text = match.groups()
        else:
            cls, method, read_text, return_text = match.groups()
            return_form = "inline_await" if inline_match else "const_binding"
        read_line, return_line = int(read_text), int(return_text)
        if not (read_line < return_line <= loc):
            continue
        rows.append({
            "kind": (
                "repository_collection_read_return_without_local_validation" if collection_match
                else "repository_direct_read_return_without_local_validation"
            ),
            "project": project,
            "file": rel_path,
            "target_ref": target_ref,
            "subject": f"{cls}.{method}",
            "return_form": return_form,
            "guard_line": read_line,
            "sink_line": return_line,
            "source_hash": source_hash,
            "evidence_source": "typescript_syntax_ast",
            "atlas_feature": feature,
            "confidence": "needs_runtime_proof",
            "actionability": "review",
            "claim_boundary": (
                "A repository subclass collection read returns awaited toArray records directly "
                "or through a bounded selection-only predicate. Selection is not schema validation; "
                "inline guard_line is method declaration context, not an executed validation guard. "
                if collection_match else
                "A repository subclass getById returns an inline awaited storage get result; "
                "guard_line identifies the method declaration, not an executed validation guard. "
                if inline_match else
                "A repository subclass getById returns a const-bound same-method storage get result directly. "
            ) + "No recognized same-method base read or validateReadData call was observed. The base read path may itself return raw input after failed validation; storage and returned-domain type correspondence, relative data-integrity impact, indirect validation, remote fallback, intentional legacy behavior and caller reachability are unproven.",
            "verification_questions": [
                "How does this direct return compare with the base read path on malformed data, logging, parsed transformations and intentional legacy handling?",
                (
                    "Do target-native malformed-record collection tests establish per-element validation, "
                    "intentional legacy loading or quarantine, and selected-record behavior?"
                    if collection_match else
                    "Do target-native malformed-record tests show the intended getById result and distinguish local from fallback storage?"
                ),
                "Does the storage record type actually satisfy the returned-domain contract, including required fields, or does a type assertion conceal a mismatch? Resolve both declarations and check target-native types before deciding.",
            ],
        })
    return rows


def _canonical_audit_artifact_identity(atlas: dict) -> dict:
    commit = load_atlas_commit(RAW_DIR)
    checks = (
        validate_atlas_commit(atlas, commit)
        if isinstance(atlas, dict) and atlas and isinstance(commit, dict) and commit
        else []
    )
    failures = [
        str(row.get("name") or "unknown")
        for row in checks
        if isinstance(row, dict) and not row.get("passed")
    ]
    snapshot_id = str(commit.get("snapshot_id") or "") if not failures else ""
    return {
        "status": "BOUND" if checks and snapshot_id else "INCOMPLETE",
        "atlas_snapshot_id": snapshot_id or None,
        "errors": failures or ([] if checks else ["atlas_commit_unavailable"]),
    }


def _add_violation(violations, rule_key: str, project: str, file_path: str, detail: str, metadata=None):
    if rule_key not in violations:
        return
    
    # [Polyglot] Fetch remediation policy from Doctrine
    remediation_policy = require_doctrine_mapping("audit_remediation_policy").get("waves")
    rule_policy = remediation_policy.get_or_contract_default(rule_key)
    action = rule_policy.get("action", "Review and remediate this rule cluster with AST-backed boundary-safe refactors.")
    
    finding = {
        "project": str(project or "UNKNOWN"),
        "file": str(file_path or "").replace("\\", "/"),
        "detail": str(detail),
        "recommended_action": action
    }
    if isinstance(metadata, dict):
        finding.update(metadata)
    violations[rule_key].append(finding)


def _reportable_violation_keys(project_count: int) -> set[str]:
    taxonomy = build_rule_taxonomy(project_count=max(int(project_count or 0), 1))
    profiles = taxonomy.get("profiles", {}) if isinstance(taxonomy, dict) else {}
    reportable = set()
    for rule_key, profile in profiles.items():
        if not isinstance(profile, dict):
            continue
        if str(profile.get("mode") or "").strip().lower() != "disabled":
            reportable.add(str(rule_key))
    return reportable


def _dedupe_violations(violations):
    """Collapse duplicate import/member findings into one actionable finding."""
    deduped = {}
    for rule_key, items in violations.items():
        seen = set()
        clean_items = []
        for item in items:
            key = (
                str(item.get("project", "UNKNOWN")),
                str(item.get("file", "")).replace("\\", "/"),
                str(item.get("detail", "")),
            )
            if key in seen:
                continue
            seen.add(key)
            clean_items.append(item)
        deduped[rule_key] = clean_items
    return deduped


def _import_matches_forbidden_fragment(import_path: str, raw_import_path: str, fragment: str) -> bool:
    """
    Match architectural restrictions on path/package segments, not arbitrary substrings.

    This keeps real domain -> platform/ui leaks visible while avoiding false positives
    such as a domain-owned port file named platformPorts.
    """
    needle = str(fragment or "").strip().lower().strip("/")
    if not needle:
        return False

    candidates = [str(import_path or ""), str(raw_import_path or "")]
    for candidate in candidates:
        normalized = candidate.replace("\\", "/").lower().strip()
        if not normalized:
            continue
        normalized = normalized.removeprefix("@/")
        segments = [part for part in normalized.split("/") if part and part not in {".", ".."}]

        if needle.endswith("/"):
            prefix = needle.rstrip("/")
            if segments and segments[0] == prefix:
                return True
            if f"/{prefix}/" in f"/{'/'.join(segments)}/":
                return True
            continue

        if "/" in needle:
            if f"/{needle}/" in f"/{'/'.join(segments)}/":
                return True
            continue

        if needle in segments:
            return True
        if normalized == needle or normalized.startswith(f"{needle}/"):
            return True

    return False


def _restriction_allows_source(restriction: dict, rel_path: str) -> bool:
    normalized = str(rel_path or "").replace("\\", "/")
    for pattern in restriction.get("allowed_source_globs", []) or []:
        glob = str(pattern or "").replace("\\", "/")
        if glob and fnmatch.fnmatch(normalized, glob):
            return True
    return False


def _load_structural_contract_health():
    # Audit runs before Quality Gate in the pipeline. Reading quality_gate.json
    # here would turn a downstream artifact into stale upstream evidence.
    return {
        'passed': None,
        'status': 'not_available_in_audit_phase',
        'reason': 'quality_gate is produced downstream; structural contract health is owned by Quality Gate and release-proof surfaces.',
        'atlas_contract_file_ratio': None,
        'member_detail_contract_ratio': None,
        'genome_occurrence_contract_ratio': None,
        'atlas_current_version_ratio': None,
        'genome_current_version_ratio': None,
    }


def _directory_bucket(file_path: str) -> str:
    normalized = str(file_path or "").replace("\\", "/").strip("/")
    parts = [p for p in normalized.split("/") if p]
    if len(parts) <= 2:
        return normalized or "unknown"
    return "/".join(parts[: min(4, len(parts) - 1)])


def _atlas_file_context(atlas: dict, project: str, file_path: str) -> dict[str, str]:
    project_key = str(project or "UNKNOWN")
    normalized = str(file_path or "").replace("\\", "/").strip().lstrip("/")
    project_data = atlas.get(project_key, {}) if isinstance(atlas, dict) else {}
    files = project_data.get("files", {}) if isinstance(project_data, dict) else {}
    meta = files.get(normalized) if isinstance(files, dict) else {}
    atlas_rel = normalized
    if not isinstance(meta, dict):
        meta = {}
    if not meta and isinstance(files, dict):
        for rel_path, file_meta in files.items():
            if not isinstance(file_meta, dict):
                continue
            workspace_rel = str(file_meta.get("repo_relative_path") or file_meta.get("workspace_rel") or "").replace("\\", "/").strip("/")
            if workspace_rel == normalized:
                meta = file_meta
                atlas_rel = str(rel_path).replace("\\", "/").strip("/")
                break
    repo_relative = str(meta.get("repo_relative_path") or meta.get("workspace_rel") or normalized).replace("\\", "/").strip("/")
    atlas_rel = str(meta.get("atlas_rel_path") or atlas_rel).replace("\\", "/").strip("/")
    return {
        "project_key": project_key,
        "file": atlas_rel,
        "atlas_rel_path": atlas_rel,
        "workspace_rel": repo_relative,
        "repo_relative_path": repo_relative,
        "target_ref": str(meta.get("target_ref") or (f"{project_key}::{repo_relative}" if project_key and repo_relative else repo_relative)),
    }


def _build_remediation_backlog(violations):
    all_items = []
    for rule, items in violations.items():
        for item in items:
            file_path = str(item.get("file", "")).replace("\\", "/")
            all_items.append({"rule": rule, "file": file_path, "detail": item.get("detail", "")})

    by_rule = defaultdict(list)
    for item in all_items:
        by_rule[item["rule"]].append(item)

    backlog = []
    for rule, items in by_rule.items():
        by_bucket = defaultdict(int)
        for item in items:
            by_bucket[_directory_bucket(item["file"])] += 1
        top_buckets = [
            {"path": path, "count": count}
            for path, count in sorted(by_bucket.items(), key=lambda pair: (-pair[1], pair[0]))[:5]
        ]

        remediation_policy = require_doctrine_mapping("audit_remediation_policy").get("waves")
        rule_policy = remediation_policy.get_or_contract_default(rule)
        
        action = rule_policy.get("action", "Review and remediate this rule cluster with AST-backed boundary-safe refactors.")
        wave = rule_policy.get("wave", "wave_4_structural_cleanup")
        priority = rule_policy.get("priority", "P3")

        backlog.append({
            "rule": rule,
            "label": VIOLATION_LABELS.get(rule, rule),
            "count": len(items),
            "priority": priority,
            "wave": wave,
            "suggested_action": action,
            "top_buckets": top_buckets,
            "sample_details": [item["detail"] for item in items[:5]],
        })

    backlog.sort(key=lambda item: (-item["count"], item["priority"], item["rule"]))
    return backlog


def _write_outputs(
    violations,
    report_sections,
    summary,
    *,
    audited_projects=None,
    atlas_project_count: int = 0,
    atlas=None,
    profile_timings=None,
    scoped_audit: dict | None = None,
    scope_authority: dict | None = None,
    scope_authority_artifact: dict | None = None,
    architecture_policy_application: dict | None = None,
    analysis_gaps: list[dict] | None = None,
    runtime_review_candidates: list[dict] | None = None,
):
    output_start = time.perf_counter()
    lines = ['=== NEXORA SAGE ARCHITECTURAL AUDIT V15 (ATLAS-PURE) ===', '']
    is_scoped = isinstance(scoped_audit, dict)
    report_path = None if is_scoped else AUDIT_REPORT_TEXT_PATH
    structural_contract = _load_structural_contract_health()
    if atlas is None:
        logger.warning("[AUDIT_TELEMETRY] output writer received no live Atlas payload; loading fallback Atlas snapshot.")
        atlas = load_atlas_data()
    remediation_backlog = _build_remediation_backlog(violations)
    audited_projects = sorted(str(project) for project in (audited_projects or []) if project)
    audited_project_count = len(audited_projects)
    rule_taxonomy = build_rule_taxonomy(project_count=max(audited_project_count, 1))
    architecture_policy_application = (
        architecture_policy_application
        if isinstance(architecture_policy_application, dict)
        else {}
    )
    analysis_gaps = list(analysis_gaps or [])
    runtime_review_candidates = list(runtime_review_candidates or [])
    project_counts = defaultdict(int)
    for items in violations.values():
        for item in items:
            project_counts[str(item.get("project", "UNKNOWN"))] += 1
    
    for section in report_sections:
        lines.append(f"--- {section.get('title', 'UNTITLED SECTION')} ---")
        for key in section.get('keys', []):
            label = VIOLATION_LABELS.get(key, key)
            entries = violations.get(key, [])
            lines.append(f"{label}: {len(entries)}")
            for entry in entries:
                lines.append(f"  - [{entry.get('project', 'UNKNOWN')}] {entry.get('detail', '')}")
        lines.append('')
    
    lines.append('--- STRUCTURAL CONTRACT HEALTH ---')
    if structural_contract.get('status') == 'not_available_in_audit_phase':
        lines.append('Quality Gate Passed: NOT AVAILABLE IN AUDIT PHASE')
        lines.append(f"Reason: {structural_contract.get('reason')}")
    else:
        lines.append(f"Quality Gate Passed: {'YES' if structural_contract['passed'] else 'NO'}")
    lines.append(f"Atlas Contract Coverage: {structural_contract['atlas_contract_file_ratio']}")
    lines.append(f"Member Detail Coverage: {structural_contract['member_detail_contract_ratio']}")
    lines.append(f"Genome Occurrence Coverage: {structural_contract['genome_occurrence_contract_ratio']}")
    lines.append(f"Atlas Current Version Coverage: {structural_contract['atlas_current_version_ratio']}")
    lines.append(f"Genome Current Version Coverage: {structural_contract['genome_current_version_ratio']}")
    lines.append('')

    lines.append('--- EFFECTIVE ARCHITECTURE POLICY ---')
    lines.append(f"Authority: {architecture_policy_application.get('authority', 'unavailable')}")
    lines.append(
        "Architecture Enabled Projects: "
        f"{architecture_policy_application.get('architecture_enabled_projects', [])}"
    )
    lines.append(
        "Architecture Incomplete Projects: "
        f"{architecture_policy_application.get('architecture_incomplete_projects', [])}"
    )
    lines.append(
        "Suppressed Unauthorised Findings: "
        f"{int(architecture_policy_application.get('suppressed_finding_count') or 0)}"
    )
    for project, policy_row in architecture_policy_application.get("projects", {}).items():
        lines.append(
            f"  - [{project}] status={policy_row.get('effective_policy_status')} "
            f"profile={policy_row.get('recommended_profile')} "
            f"rules_enabled={policy_row.get('architecture_sensitive_rules_enabled')}"
        )
    lines.append('')

    lines.append('--- AUDIT SCOPE ---')
    lines.append(f"Atlas Projects: {int(atlas_project_count or 0)}")
    lines.append(f"Audited Projects: {audited_project_count}")
    lines.append(f"Analysis Gaps: {len(analysis_gaps)}")
    if audited_projects:
        for project in audited_projects:
            lines.append(f"  - {project_display_name(project)} [{project}]")
    lines.append('')

    lines.append('--- RULE TAXONOMY ---')
    taxonomy_summary = rule_taxonomy.get("summary", {})
    lines.append(f"By Layer: {taxonomy_summary.get('by_layer', {})}")
    lines.append(f"By Mode: {taxonomy_summary.get('by_mode', {})}")
    for profile in rule_taxonomy.get("profiles", {}).values():
        lines.append(
            f"  - [{profile['layer']}/{profile['mode']}] {profile['label']} ({profile['rule']})"
        )
    lines.append('')

    lines.append('--- REMEDIATION BACKLOG ---')
    for item in remediation_backlog:
        lines.append(
            f"[{item['priority']}] {item['label']} ({item['count']}) -> {item['wave']}"
        )
        lines.append(f"  Action: {item['suggested_action']}")
        for bucket in item['top_buckets'][:3]:
            lines.append(f"  Hotspot: {bucket['path']} ({bucket['count']})")
    lines.append('')

    lines.append('--- RUNTIME INVARIANT REVIEW CANDIDATES (NOT VIOLATIONS) ---')
    lines.append(f"Review-only candidates: {len(runtime_review_candidates)}")
    lines.append('Static source/guard/sink evidence does not prove a runtime or persistence defect.')
    for row in runtime_review_candidates[:20]:
        subject_field = "property" if "property" in row else "subject"
        lines.append(
            f"  - [{row['project']}] {row['file']}:{row['guard_line']} "
            f"kind={row['kind']} {subject_field}={row[subject_field]} sink_line={row['sink_line']}"
        )
        for question in row.get("verification_questions", []):
            lines.append(f"    Target-native question (unverified): {question}")
    if len(runtime_review_candidates) > 20:
        lines.append(f"  - {len(runtime_review_candidates) - 20} more in audit_report.json")
    lines.append('')

    if project_counts:
        lines.append('--- PROJECT AUDIT TOTALS ---')
        for project, count in sorted(project_counts.items(), key=lambda pair: (-pair[1], pair[0])):
            display = project_display_name(project)
            lines.append(f"{display} [{project}]: {count}")
        lines.append('')

    lines.append('------------------------------')
    incomplete_architecture = architecture_policy_application.get(
        "architecture_incomplete_projects", []
    )
    if analysis_gaps:
        lines.append(
            f"RESULT: INCOMPLETE EVIDENCE; {len(analysis_gaps)} AUDIT ANALYSIS GAP(S) "
            "PREVENT A SEALED CLAIM."
        )
    elif incomplete_architecture:
        lines.append(
            f"RESULT: {summary['total']} ACTIVE-POLICY VIOLATIONS; ARCHITECTURE-SENSITIVE "
            f"COVERAGE INCOMPLETE FOR {len(incomplete_architecture)} PROJECT(S)."
        )
    elif summary['total'] == 0 and runtime_review_candidates:
        lines.append(
            'RESULT: 0 ACTIVE-POLICY VIOLATIONS; RUNTIME REVIEW CANDIDATES '
            'REMAIN UNPROVEN.'
        )
    elif summary['total'] == 0:
        lines.append('RESULT: 100% SEALED. NO ACTIVE-POLICY VIOLATIONS DETECTED.')
    else:
        lines.append(f"RESULT: {summary['total']} TOTAL VIOLATIONS REMAIN.")
    report_text_built_at = time.perf_counter()
    report_text = '\n'.join(lines)

    structured_violations = []
    by_module = defaultdict(int)
    by_project = defaultdict(int)
    by_project_rule = defaultdict(lambda: defaultdict(int))
    # Define the module root dynamically
    module_root_name = get_module_root_name()
    
    for rule_key, items in violations.items():
        for item in items:
            file_path = str(item.get("file", "")).replace("\\", "/")
            project = str(item.get("project", "UNKNOWN"))
            module_name = "unknown"
            if f"/{module_root_name}/" in file_path:

                parts = file_path.split(f"/{module_root_name}/")[1].split("/")
                if len(parts) > 0: module_name = parts[0]
            else:
                # Total Sovereignty: Categorize by top-level folder in src (e.g. shared, platform, or ROOT)
                parts = file_path.split("/")
                if len(parts) > 1:
                    module_name = f"@{parts[0].upper()}" # Use @ prefix for non-module folders
                else:
                    module_name = "@ROOT"
            file_context = _atlas_file_context(atlas, project, file_path)
            structured_violations.append({
                "project": project,
                "project_key": file_context.get("project_key"),
                "file": file_context.get("file") or file_path,
                "atlas_rel_path": file_context.get("atlas_rel_path"),
                "workspace_rel": file_context.get("workspace_rel"),
                "repo_relative_path": file_context.get("repo_relative_path"),
                "target_ref": file_context.get("target_ref"),
                "rule": rule_key,
                "detail": item.get("detail", ""),
                "recommended_action": item.get("recommended_action", ""),
                **{
                    field: item.get(field)
                    for field in (
                        "symbol_name",
                        "symbol_kind",
                        "threshold_kind",
                        "start_line",
                        "end_line",
                        "observed_loc",
                        "limit", "limit_authority", "target_policy_resolution",
                    )
                    if field in item
                },
            })
            by_module[module_name] += 1
            by_project[project] += 1
            by_project_rule[project][rule_key] += 1

    summary["by_module"] = dict(by_module)
    summary["by_project"] = dict(sorted(by_project.items()))
    summary["by_project_rule"] = {
        project: dict(sorted(rule_counts.items()))
        for project, rule_counts in sorted(by_project_rule.items())
    }
    rule_taxonomy = build_rule_taxonomy(project_count=max(audited_project_count, 1))
    summary["structural_contract"] = structural_contract
    scope_authority = scope_authority if isinstance(scope_authority, dict) else {}
    evidence_status = str(scope_authority.get("evidence_status") or "")
    scope_kind = (
        "scoped_change"
        if is_scoped
        else "full_repository"
        if evidence_status == COMPLETE_REPOSITORY
        else "bounded_project_selection"
        if evidence_status == BOUNDED_PROJECT_SELECTION
        else "incomplete_evidence"
    )
    audit_scope = {
        "scope_kind": scope_kind,
        "full_repository_claim": bool(
            not is_scoped
            and scope_authority.get("full_repository_claim_eligible") is True
            and evidence_status == COMPLETE_REPOSITORY
        ),
        "atlas_project_count": int(atlas_project_count or 0),
        "audited_project_count": audited_project_count,
        "audited_projects": audited_projects,
        "violation_project_count": len(by_project),
        "scope_authority": scope_authority,
    }
    if is_scoped:
        audit_scope.update(scoped_audit)
    summary["audit_scope"] = audit_scope
    summary["rule_taxonomy"] = rule_taxonomy
    summary["architecture_policy_application"] = architecture_policy_application
    summary["analysis_gaps"] = analysis_gaps
    summary["analysis_gap_count"] = len(analysis_gaps)
    summary["runtime_review_candidate_count"] = len(runtime_review_candidates)
    summary["remediation_backlog"] = remediation_backlog
    payload = {
        'meta': {
            'kind': 'watchdog_audit_report' if is_scoped else 'audit_report',
            'version': 'v15-atlas-pure',
        },
        'summary': summary,
        'audit_scope': summary["audit_scope"],
        'architecture_policy_application': architecture_policy_application,
        'atlas_project_count': int(atlas_project_count or 0),
        'audited_project_count': audited_project_count,
        'audited_projects': audited_projects,
        'violation_project_count': len(by_project),
        'violations': structured_violations,
        'runtime_review_candidates': runtime_review_candidates,
        'report_sections': report_sections
    }
    payload["artifact_identity"] = (
        watchdog_artifact_identity()
        if is_scoped
        else _canonical_audit_artifact_identity(atlas)
    )
    payload_built_at = time.perf_counter()

    backlog_lines = [
        '# Audit Remediation Backlog',
        '',
        '> AST-first prioritized execution plan derived from the current audit output.',
        '',
        '| Priority | Rule | Count | Wave | Suggested Action | Hotspots |',
        '|---|---|---:|---|---|---|',
    ]
    for item in remediation_backlog:
        hotspots = ", ".join(f"{entry['path']} ({entry['count']})" for entry in item['top_buckets'][:5]) or '-'
        backlog_lines.append(
            f"| `{item['priority']}` | {item['label']} | {item['count']} | `{item['wave']}` | "
            f"{item['suggested_action']} | {hotspots} |"
        )
    backlog_built_at = time.perf_counter()
    if not is_scoped:
        save_text_atomic(REPORTS_DIR / 'audit_remediation_backlog.md', '\n'.join(backlog_lines))
    backlog_saved_at = time.perf_counter()
    if profile_timings is not None:
        profile_timings["report_text_build_seconds"] = round(report_text_built_at - output_start, 3)
        profile_timings["json_payload_build_seconds"] = round(payload_built_at - report_text_built_at, 3)
        profile_timings["backlog_build_seconds"] = round(backlog_built_at - payload_built_at, 3)
        profile_timings["backlog_save_seconds"] = round(backlog_saved_at - backlog_built_at, 3)
    if profile_timings is not None:
        summary["profile_timings"] = dict(profile_timings)
    canonical_save_started_at = time.perf_counter()
    output_json_path = watchdog_artifact_path("audit") if is_scoped else AUDIT_REPORT_JSON_PATH
    save_json_atomic(output_json_path, payload)
    if not is_scoped:
        scope_authority_artifact = (
            scope_authority_artifact
            if isinstance(scope_authority_artifact, dict)
            else {}
        )
        write_current_atlas_lineage(
            artifact_id="audit_report",
            producer="tools.engines.audit",
            artifact_payload=payload,
            atlas=atlas,
            dependency_payloads={
                "analysis_scope_authority": scope_authority_artifact,
            },
        )
    if profile_timings is not None:
        profile_timings["canonical_artifact_save_seconds"] = round(
            time.perf_counter() - canonical_save_started_at,
            3,
        )
    if is_scoped:
        if profile_timings is not None:
            profile_timings["shadow_flush_seconds"] = 0.0
            profile_timings["shadow_flush_complete"] = False
            profile_timings["shadow_flush_policy"] = "deferred_nonblocking_scoped_artifact"
            profile_timings["report_text_save_seconds"] = 0.0
            profile_timings["output_materialize_seconds"] = round(time.perf_counter() - output_start, 3)
        logger.info(
            "[AUDIT_PROFILE] scoped artifact committed without canonical report/backlog overwrite or global shadow flush: %s",
            output_json_path,
        )
        return output_json_path

    shadow_flush_started_at = time.perf_counter()
    shadow_flushed = flush_shadow_writes(timeout=float(artifact_shadow_flush_timeout_seconds()))
    if profile_timings is not None:
        profile_timings["shadow_flush_seconds"] = round(time.perf_counter() - shadow_flush_started_at, 3)
        profile_timings["shadow_flush_complete"] = bool(shadow_flushed)
    if not shadow_flushed:
        logger.error("[AUDIT_TELEMETRY] audit_report shadow flush did not complete within the configured timeout.")
    report_text_save_started_at = time.perf_counter()
    save_text_atomic(report_path, report_text)
    if profile_timings is not None:
        profile_timings["report_text_save_seconds"] = round(time.perf_counter() - report_text_save_started_at, 3)
        profile_timings["output_materialize_seconds"] = round(time.perf_counter() - output_start, 3)
    invalidate_audit_report_cache()
    return report_path

def analyze_project(changed_files=None, atlas=None):
    profile_start = time.perf_counter()
    logger.info("Generating sovereign audit report (Atlas-Only)...")
    if changed_files is not None and not [item for item in changed_files if str(item or "").strip()]:
        changed_files = None
    requested_scope_refs = normalize_watchdog_scope_refs(changed_files)
    is_scoped = changed_files is not None
    atlas, atlas_input_source = resolve_atlas_data(atlas)
    atlas_loaded_at = time.perf_counter()
    if not atlas:
        logger.error("Atlas not found. Cannot perform audit.")
        return False
    projects = resolve_runtime_projects(ROOT)
    projects_resolved_at = time.perf_counter()
    atlas_project_count = len(atlas) if isinstance(atlas, dict) else 0
    atlas_file_counts = {
        str(project): len(payload.get("files", {}))
        for project, payload in atlas.items()
        if isinstance(payload, dict) and isinstance(payload.get("files"), dict)
    }
    logger.info(
        "[SCOPE] Audit runtime_projects=%s atlas_projects=%s atlas_files=%s atlas_input_source=%s",
        sorted(projects),
        sorted(atlas),
        atlas_file_counts,
        atlas_input_source,
    )
    audited_projects = []
    audited_file_refs: set[str] = set()
    effective_policy, atlas_snapshot_id = load_effective_architecture_policy_context(RAW_DIR)
    project_taxonomies = {
        pkey: build_effective_project_rule_taxonomy(
            pkey,
            effective_policy,
            expected_snapshot_id=atlas_snapshot_id,
            project_count=len(projects),
        )
        for pkey in projects
    }
    # Collect candidate evidence first, then let the exact project taxonomy decide
    # applicability. This also keeps suppressed false-positive counts observable.
    violations = {key: [] for key in DEFAULT_VIOLATION_KEYS}
    analysis_gaps: list[dict] = []
    runtime_review_candidates: list[dict] = []
    
    report_sections = get_audit_report_sections()
    module_root_name = get_module_root_name()
    policy_loaded_at = time.perf_counter()
    
    for pkey, ppath in projects.items():
        proj_data = atlas.get(pkey, {})
        files = proj_data.get("files", {})
        
        # [Phase 9] Strict Surgical Gating: Only scan changed files in THIS project
        target_rel_paths = None
        if changed_files:
            target_rel_paths = set()
            for item in changed_files:
                text = str(item or "").replace("\\", "/").strip()
                if not text.startswith(f"{pkey}::"):
                    continue
                rel = text.split("::", 1)[1].strip("/")
                target_rel_paths.add(rel)
                if isinstance(files, dict):
                    for atlas_rel, file_meta in files.items():
                        if not isinstance(file_meta, dict):
                            continue
                        workspace_rel = str(file_meta.get("repo_relative_path") or file_meta.get("workspace_rel") or "").replace("\\", "/").strip("/")
                        target_ref_rel = str(file_meta.get("target_ref") or "").split("::", 1)[-1].replace("\\", "/").strip("/")
                        if rel in {workspace_rel, target_ref_rel}:
                            target_rel_paths.add(str(atlas_rel).replace("\\", "/").strip("/"))


        if changed_files is not None and not target_rel_paths:
            # If changed_files exists but none are in this project, skip the whole project loop
            continue
        # A present empty file map is covered, not a missing Atlas project.
        # Scoped pulses still count only projects with requested audited files.
        if not is_scoped and pkey in atlas and isinstance(proj_data.get("files"), dict) and not files:
            audited_projects.append(pkey)
        for rel_path, a_data in files.items():
            # [Polyglot] Detect file language context
            f_ext = Path(rel_path).suffix.lower()
            f_lang = language_for_extension(f_ext)

            if target_rel_paths is not None and rel_path not in target_rel_paths: 
                continue
            if pkey not in audited_projects:
                audited_projects.append(pkey)
            audited_file_refs.add(f"{pkey}::{str(rel_path).replace(chr(92), '/')}")
            
            all_feats = set(a_data.get("features", []))
            runtime_review_candidates.extend(
                _immutable_key_review_candidates(pkey, rel_path, a_data)
            )
            runtime_review_candidates.extend(
                _finite_number_review_candidates(pkey, rel_path, a_data)
            )
            runtime_review_candidates.extend(
                _schema_broader_cast_review_candidates(pkey, rel_path, a_data)
            )
            runtime_review_candidates.extend(
                _unawaited_async_helper_review_candidates(pkey, rel_path, a_data)
            )
            runtime_review_candidates.extend(
                _caught_async_await_review_candidates(pkey, rel_path, a_data)
            )
            runtime_review_candidates.extend(
                _queued_mutation_review_candidates(pkey, rel_path, a_data)
            )
            runtime_review_candidates.extend(
                _repository_bulk_direct_write_review_candidates(pkey, rel_path, a_data)
            )
            runtime_review_candidates.extend(
                _failed_safeparse_raw_fallback_review_candidates(pkey, rel_path, a_data)
            )
            runtime_review_candidates.extend(
                _repository_direct_read_return_review_candidates(pkey, rel_path, a_data)
            )
            imports = a_data.get("import_records", [])
            if not imports:
                imports = [
                    {
                        "source": imp,
                        "raw_source": imp,
                        "name": "*",
                        "kind": "module",
                    }
                    for imp in (a_data.get("imports") or a_data.get("raw_imports") or [])
                    if isinstance(imp, str) and imp
                ]
            symbols = a_data.get("symbols", [])
            for s in symbols: 
                if isinstance(s, dict): all_feats.update(s.get("features", []))

            # Detect current module
            current_mod = None
            parts = rel_path.split('/')
            if module_root_name in parts:
                idx = parts.index(module_root_name)
                if idx + 1 < len(parts):
                    current_mod = parts[idx + 1]

            # 1. Complexity (LOC) from AST
            file_loc = int(a_data.get("loc") or 0) if isinstance(a_data, dict) else 0
            for sym in symbols:
                if not isinstance(sym, dict): continue
                s_lines = sym.get('source_lines', '')
                if s_lines.startswith('L') and '-L' in s_lines:
                    try:
                        start, end = map(int, s_lines.replace('L', '').split('-'))
                        if file_loc and (start > file_loc or end > file_loc):
                            continue
                        sym_loc = end - start + 1
                        finding = resolve_loc_finding_semantics(
                            sym.get('canonical_symbol_type') or sym.get('canonicalSymbolType') or sym.get('type', 'unknown'),
                            sym_loc,
                            symbol_name=sym.get('name', 'unknown'),
                            start_line=start,
                            end_line=end,
                            project=pkey,
                            file_path=rel_path,
                        )
                        if sym_loc > finding["limit"]:
                            _add_violation(
                                violations,
                                finding["rule"],
                                pkey,
                                rel_path,
                                f"{rel_path} symbol:{sym.get('name')} ({sym_loc} lines)",
                                metadata={key: value for key, value in finding.items() if key != "rule"},
                            )
                    except (KeyError, TypeError, ValueError) as exc:
                        gap = {
                            "project": pkey, "file": rel_path,
                            "symbol": str(sym.get("name") or "unknown"),
                            "stage": "symbol_loc_evaluation",
                            "error_type": type(exc).__name__,
                        }
                        analysis_gaps.append(gap)
                        logger.warning("[AUDIT_GAP] %s", gap)

            # 2. Tech Violations (markers from ast_sequencer) - Completely config-driven via feature_tag_audit_rules
            tag_rules = require_doctrine_mapping("architectural_integrity_rules").get("feature_tag_audit_rules")
            for rule in tag_rules:
                tag_name = rule.get("tag")
                rule_key = rule.get("rule_key")
                if tag_name in all_feats:
                    _add_violation(violations, rule_key, pkey, rel_path, rel_path)

            # 3. Import Purity (from Sovereign records)
            from tools.core.config import PRIMARY_ALIAS
            for imp_rec in imports:
                if str(imp_rec.get("kind") or "").strip().lower() == "type": continue
                imp = imp_rec.get('source', '')
                raw_imp = imp_rec.get("raw_source") or imp
                module_import_prefix = f"{PRIMARY_ALIAS}{module_root_name}/"
                
                if current_mod and module_import_prefix in raw_imp:
                    imp_parts = raw_imp.split(module_import_prefix)[1].split("/")
                    if len(imp_parts) > 0:
                        imp_mod = imp_parts[0]
                        if imp_mod != current_mod:
                            # [Debt Fixed] Generic API boundary check
                            if not raw_imp.endswith('/api') and not raw_imp.endswith('/api/'):
                                _add_violation(violations, 'deep_imports', pkey, rel_path, f"{rel_path} imports {raw_imp}")
                        else:
                            # [Debt Fixed] Language-agnostic API entry check from config
                            api_entries = get_api_entry_filenames()
                            is_api_entry = any(rel_path.endswith(f"/api/{x}") for x in api_entries)
                            if "/api" in raw_imp and not is_api_entry:
                                _add_violation(violations, 'own_api_imports', pkey, rel_path, f"{rel_path} imports {raw_imp}")
                
                # Domain Integrity (Hexagonal)
                source_layer = resolve_layer(rel_path)
                
                # Resolve target layer for the import
                target_rel_path = imp.replace(PRIMARY_ALIAS, "") if imp.startswith(PRIMARY_ALIAS) else imp
                target_layer = resolve_layer(target_rel_path)
                
                if is_violation(source_layer, target_layer, language=f_lang):
                    members = imp_rec.get('members', [])
                    members_str = f" ({', '.join(members)})" if members else ""
                    _add_violation(
                        violations, 
                        'hexagonal_layer_violation', 
                        pkey, 
                        rel_path, 
                        f"[{f_lang}] [{source_layer} -> {target_layer}] {rel_path} imports {imp}{members_str}"
                    )
                
                # [Debt Cleaned] Layer-based import restrictions (Config-driven and Framework-agnostic)
                import_restrictions = require_doctrine_mapping("architectural_integrity_rules").get("layer_import_restrictions")
                for restriction in import_restrictions:
                    res_layer = restriction.get("layer")
                    res_lang = restriction.get("language")
                    
                    if source_layer == res_layer and (not res_lang or res_lang == f_lang):
                        if _restriction_allows_source(restriction, rel_path):
                            continue
                        for sub in restriction.get("forbidden_substrings", []):
                            aliased_sub = f"{PRIMARY_ALIAS}{sub}"
                            if (
                                _import_matches_forbidden_fragment(imp, raw_imp, sub)
                                or _import_matches_forbidden_fragment(imp, raw_imp, aliased_sub)
                            ):
                                _add_violation(violations, restriction.get("rule_key"), pkey, rel_path, f"{rel_path} imports {raw_imp}")
                                break

                if violates_canonical_alias_boundary(
                    rel_path,
                    raw_imp,
                    language=f_lang,
                    primary_alias=PRIMARY_ALIAS,
                    module_root_name=module_root_name,
                    alias_contract_applies=path_alias_applies_to_file(rel_path),
                ):
                    alias_decision = canonical_alias_boundary_decision(
                        rel_path,
                        raw_imp,
                        language=f_lang,
                        primary_alias=PRIMARY_ALIAS,
                        module_root_name=module_root_name,
                        alias_contract_applies=path_alias_applies_to_file(rel_path),
                    )
                    _add_violation(
                        violations,
                        RELATIVE_IMPORTS_NO_ALIAS_RULE,
                        pkey,
                        rel_path,
                        (
                            f"{rel_path} imports {raw_imp}; "
                            f"ownership_root={alias_decision.get('ownership_root') or 'unresolved'} "
                            f"reason={alias_decision.get('reason')}"
                        ),
                    )

    violations = _dedupe_violations(violations)
    violations, architecture_policy_application = filter_violations_by_project_taxonomy(
        violations,
        project_taxonomies,
    )
    scan_finished_at = time.perf_counter()
    if not is_scoped and atlas_project_count > 0 and not audited_projects:
        logger.error(
            "[SCOPE] Audit refused to publish a zero-scope report: atlas_projects=%s runtime_projects=%s",
            sorted(atlas),
            sorted(projects),
        )
        return False
    scope_authority_artifact, scope_authority = load_scope_authority_for_consumer(
        RAW_DIR
    )
    if not is_scoped:
        scope_authority = bind_consumer_projects(
            scope_authority,
            layer="audit",
            observed_projects=audited_projects,
        )
    summary = {'total': sum(len(v) for v in violations.values()), 'by_rule': {k: len(v) for k, v in violations.items()}}
    profile_timings = {
        "atlas_load_seconds": round(atlas_loaded_at - profile_start, 3),
        "project_registry_seconds": round(projects_resolved_at - atlas_loaded_at, 3),
        "policy_load_seconds": round(policy_loaded_at - projects_resolved_at, 3),
        "scan_seconds": round(scan_finished_at - policy_loaded_at, 3),
    }
    scoped_audit = None
    if is_scoped:
        audited_files = sorted(audited_file_refs)
        requested_set = set(requested_scope_refs)
        unresolved = sorted(requested_set - set(audited_files))
        scope_status = "complete" if requested_set and not unresolved else ("partial" if audited_files else "empty")
        scoped_audit = {
            "requested_file_count": len(requested_scope_refs),
            "requested_files": requested_scope_refs,
            "audited_file_count": len(audited_files),
            "audited_files": audited_files,
            "unresolved_requested_files": unresolved,
            "scope_status": scope_status,
        }
        logger.info(
            "[SCOPE] Watchdog Audit requested=%s audited=%s unresolved=%s status=%s",
            len(requested_scope_refs),
            len(audited_files),
            len(unresolved),
            scope_status,
        )
    _write_outputs(
        violations,
        report_sections,
        summary,
        audited_projects=audited_projects,
        atlas_project_count=atlas_project_count,
        atlas=atlas,
        profile_timings=profile_timings,
        scoped_audit=scoped_audit,
        scope_authority=scope_authority,
        scope_authority_artifact=scope_authority_artifact,
        architecture_policy_application=architecture_policy_application,
        analysis_gaps=analysis_gaps,
        runtime_review_candidates=runtime_review_candidates,
    )
    outputs_written_at = time.perf_counter()
    profile_timings["write_outputs_seconds"] = round(outputs_written_at - scan_finished_at, 3)
    profile_timings["total_inside_engine_seconds"] = round(outputs_written_at - profile_start, 3)
    summary["profile_timings"] = profile_timings
    logger.info("[AUDIT_PROFILE] %s", json.dumps(profile_timings, ensure_ascii=False))
    return True

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--changed-files', help="Comma-separated list of changed files")
    args = parser.parse_args()
    changed_list = args.changed_files.split(',') if args.changed_files else None
    success = analyze_project(changed_files=changed_list)
    if not success: sys.exit(1)
