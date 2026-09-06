import json
import sys
import fnmatch
import time
from collections import defaultdict
from pathlib import Path
from tools.core.artifact_contracts import AUDIT_REPORT_JSON_PATH, AUDIT_REPORT_TEXT_PATH
from tools.core.analysis_snapshot_lineage import write_current_atlas_lineage
from tools.core.artifact_store import flush_shadow_writes
from tools.core.audit_report import invalidate_audit_report_cache
from tools.core.audit_rules import (
    build_rule_taxonomy,
    canonical_alias_boundary_decision,
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

    lines.append('--- AUDIT SCOPE ---')
    lines.append(f"Atlas Projects: {int(atlas_project_count or 0)}")
    lines.append(f"Audited Projects: {audited_project_count}")
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

    if project_counts:
        lines.append('--- PROJECT AUDIT TOTALS ---')
        for project, count in sorted(project_counts.items(), key=lambda pair: (-pair[1], pair[0])):
            display = project_display_name(project)
            lines.append(f"{display} [{project}]: {count}")
        lines.append('')

    lines.append('------------------------------')
    if summary['total'] == 0: lines.append('RESULT: 100% SEALED. NO ARCHITECTURAL VIOLATIONS DETECTED.')
    else: lines.append(f"RESULT: {summary['total']} TOTAL VIOLATIONS REMAIN.")
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
                        "limit",
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
    summary["remediation_backlog"] = remediation_backlog
    payload = {
        'meta': {
            'kind': 'watchdog_audit_report' if is_scoped else 'audit_report',
            'version': 'v15-atlas-pure',
        },
        'summary': summary,
        'audit_scope': summary["audit_scope"],
        'atlas_project_count': int(atlas_project_count or 0),
        'audited_project_count': audited_project_count,
        'audited_projects': audited_projects,
        'violation_project_count': len(by_project),
        'violations': structured_violations,
        'report_sections': report_sections
    }
    if is_scoped:
        payload["artifact_identity"] = watchdog_artifact_identity()
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
    reportable_keys = _reportable_violation_keys(project_count=len(projects))
    violations = {key: [] for key in DEFAULT_VIOLATION_KEYS if key in reportable_keys}
    
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
                            sym.get('type', 'unknown'),
                            sym_loc,
                            symbol_name=sym.get('name', 'unknown'),
                            start_line=start,
                            end_line=end,
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
                    except (KeyError, TypeError, ValueError): pass

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
