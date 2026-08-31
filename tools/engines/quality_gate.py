import json
import os
from collections import Counter
from pathlib import Path

from tools.core.artifact_contracts import AUDIT_REPORT_TEXT_PATH, MASTER_REPORT_PATH
from tools.core.analysis_snapshot_lineage import write_current_atlas_lineage
from tools.core.audit_report import load_audit_report
from tools.core.config import OUTPUT_DIR, RAW_DIR, REPORTS_DIR, CONFIG_FILE, DISCOVERY_FILE, DYNAMIC_CONFIG, DOCTRINE, save_json_atomic, save_text_atomic
from tools.core.doctrine_contract import require_dead_code_policy, require_doctrine_mapping
from tools.core.artifact_validator import validate_all_artifacts, ensure_valid_payload
from tools.core.atlas_io import load_atlas_data
from tools.core.runtime_project_scope import project_runtime_atlas
from tools.core.genome_io import load_genome_data
from tools.core.json_io import load_json_file
from tools.core.fractal_io import load_fractal_map_data
from tools.core.logger import logger
from tools.core.path_engine import to_posix_path
from tools.core.pipeline_policy import get_quality_gates
from tools.core.projects_registry import project_display_name, release_scope_project_keys
from tools.core.source_files import is_analysis_source_file
from tools.core.text_normalizer import SUSPICIOUS_MARKERS
from tools.core.workspace_mode import get_workspace_mode
from tools.core.workload_profile import manual_review_budget_selection
from tools.engines.generate_atlas import AST_CONTRACT_VERSION


def _audit_summary(audit_report: dict | None) -> dict:
    summary = audit_report.get("summary", {}) if isinstance(audit_report, dict) else {}
    return summary if isinstance(summary, dict) else {}


def _extract_total_audit_violations(audit_report: dict) -> int:
    return int(_audit_summary(audit_report).get("total", 0) or 0)


def _extract_project_audit_violations(audit_report: dict) -> dict:
    by_project = _audit_summary(audit_report).get("by_project", {})
    if not isinstance(by_project, dict):
        return {}
    return {str(key): int(value or 0) for key, value in by_project.items()}


def _audit_mode_breakdown(audit_report: dict):
    summary = _audit_summary(audit_report)
    taxonomy = summary.get("rule_taxonomy", {})
    profiles = taxonomy.get('profiles', {}) if isinstance(taxonomy, dict) else {}
    violations = audit_report.get("violations", []) if isinstance(audit_report, dict) else []

    totals = {'enforced': 0, 'heal': 0, 'advisory': 0, 'disabled': 0, 'unknown': 0}
    by_project = {}
    for item in violations:
        if not isinstance(item, dict):
            continue
        rule = str(item.get('rule', ''))
        project = str(item.get('project', 'UNKNOWN'))
        mode = str((profiles.get(rule) or {}).get('mode', 'unknown'))
        if mode not in totals:
            mode = 'unknown'
        totals[mode] += 1
        by_project.setdefault(project, {'enforced': 0, 'heal': 0, 'advisory': 0, 'disabled': 0, 'unknown': 0})
        by_project[project][mode] += 1

    return {
        'totals': totals,
        'by_project': by_project,
        'taxonomy_summary': taxonomy.get('summary', {}) if isinstance(taxonomy, dict) else {},
    }


def _audit_enforcement_authority() -> dict:
    return {
        'native_enforcement': 'sage_native_audit_taxonomy',
        'target_native_enforcement': 'not_ingested',
        'combined_verdict': 'not_available',
        'claim_boundary': (
            'Audit enforcement counts cover SAGE-native rules only. Target repository lint, '
            'compiler or policy enforcement is not represented unless a provenance-bound adapter says otherwise.'
        ),
    }


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator


def _project_dna_framework_ids(payload: dict | None) -> set[str]:
    if not isinstance(payload, dict):
        return set()
    ids: set[str] = set()
    for project in payload.get("projects", []) or []:
        if not isinstance(project, dict):
            continue
        for item in project.get("frameworks", []) or []:
            if isinstance(item, dict):
                value = item.get("id") or item.get("framework")
            else:
                value = item
            if value:
                ids.add(str(value).lower())
    return ids


def _react_gate_applicable(project_dna_payload: dict | None, react_support_matrix: dict | None) -> tuple[bool, dict]:
    frameworks = _project_dna_framework_ids(project_dna_payload)
    react_frameworks = {
        "react",
        "next",
        "nextjs",
        "vite-react",
        "remix",
        "react-router",
        "tanstack-router",
        "astro-react",
        "expo",
    }
    summary = react_support_matrix.get("summary", {}) if isinstance(react_support_matrix, dict) else {}
    applicable = bool(frameworks & react_frameworks)
    return applicable, {
        "project_dna_frameworks": sorted(frameworks),
        "react_framework_signal": sorted(frameworks & react_frameworks),
        "react_support_summary": summary if isinstance(summary, dict) else {},
        "applicability_rule": "project_dna_framework_signal",
    }


def _mark_not_applicable(check: dict, reason: dict) -> dict:
    updated = dict(check)
    updated["passed"] = True
    updated["operator"] = "not_applicable"
    updated["enforced"] = False
    details = list(updated.get("details") or [])
    details.append(reason)
    updated["details"] = details
    return updated


def _normalize_contract_keys(payload):
    if not isinstance(payload, dict):
        return set()

    normalized = set(payload.keys())
    camel_to_snake = {
        'dependencyImports': 'dependency_imports',
        'uiDependencies': 'ui_dependencies',
        'dynamicImports': 'dynamic_imports',
        'architecturalMarkers': 'architectural_markers',
        'sideEffectMarkers': 'side_effect_markers',
        'sideEffectImports': 'side_effect_imports',
        'sideEffectCalls': 'side_effect_calls',
    }
    for camel, snake in camel_to_snake.items():
        if camel in payload:
            normalized.add(snake)
    return normalized


def _structural_contract_coverage(atlas: dict | None = None, genome: dict | None = None):
    atlas = atlas if isinstance(atlas, dict) else project_runtime_atlas(load_atlas_data())[0]
    genome = genome if isinstance(genome, dict) else load_genome_data()

    total_files = 0
    current_contract_files = 0
    current_version_files = 0
    observed_member_details = 0
    total_member_details = 0
    rich_member_details = 0
    excluded_member_details = 0
    member_detail_profile_counts = Counter()
    excluded_member_detail_profile_counts = Counter()
    unknown_member_detail_profile_counts = Counter()
    relevant_occurrences = 0
    rich_occurrences = 0
    current_version_occurrences = 0

    from tools.core.config import DOCTRINE
    q_defaults = require_dead_code_policy("quality_defaults")
    reqs = q_defaults.get("contract_requirements", {})
    required_member_keys = set(reqs.get("member_keys", []))
    required_occurrence_keys = set(reqs.get("occurrence_keys", []))
    raw_member_detail_applicability = reqs.get("member_detail_contract_applicability", {})
    if not isinstance(raw_member_detail_applicability, dict) or not raw_member_detail_applicability:
        raise ValueError(
            "dead_code quality_defaults.contract_requirements must declare "
            "member_detail_contract_applicability"
        )
    member_detail_applicability = dict(raw_member_detail_applicability)
    invalid_applicability = {
        str(profile): str(status)
        for profile, status in member_detail_applicability.items()
        if status not in {"required", "not_applicable"}
    }
    if invalid_applicability:
        raise ValueError(
            "Invalid member-detail contract applicability states: "
            f"{invalid_applicability}"
        )

    if isinstance(atlas, dict):
        for pdata in atlas.values():
            files = (pdata or {}).get('files', {}) or {}
            for fdata in files.values():
                if not isinstance(fdata, dict):
                    continue
                total_files += 1
                version = str(fdata.get('ast_contract_version') or '').strip()
                if version:
                    current_contract_files += 1
                if version == AST_CONTRACT_VERSION:
                    current_version_files += 1
                for symbol in fdata.get('symbols', []) or []:
                    if not isinstance(symbol, dict):
                        continue
                    normalization_profile = str(
                        symbol.get('normalization_profile')
                        or symbol.get('normalizationProfile')
                        or ''
                    ).strip() or 'undeclared'
                    for member in symbol.get('member_details', []) or []:
                        if not isinstance(member, dict):
                            continue
                        observed_member_details += 1
                        member_detail_profile_counts[normalization_profile] += 1
                        applicability = member_detail_applicability.get(normalization_profile)
                        if applicability == 'not_applicable':
                            excluded_member_details += 1
                            excluded_member_detail_profile_counts[normalization_profile] += 1
                            continue
                        if applicability is None:
                            unknown_member_detail_profile_counts[normalization_profile] += 1
                        total_member_details += 1
                        if required_member_keys.issubset(_normalize_contract_keys(member)):
                            rich_member_details += 1

    if isinstance(genome, dict):
        for occs in genome.values():
            for occ in occs or []:
                if not isinstance(occ, dict):
                    continue
                if (occ.get('member_details') or []) or int(occ.get('member_count', 0) or 0) > 0:
                    relevant_occurrences += 1
                else:
                    continue
                if required_occurrence_keys.issubset(occ.keys()):
                    rich_occurrences += 1
                if str(occ.get('ast_contract_version') or '').strip() == AST_CONTRACT_VERSION:
                    current_version_occurrences += 1

    return {
        'atlas_contract_file_ratio': round(_ratio(current_contract_files, total_files), 3),
        'atlas_current_version_ratio': round(_ratio(current_version_files, total_files), 3),
        'member_detail_contract_ratio': round(_ratio(rich_member_details, total_member_details), 3),
        'genome_occurrence_contract_ratio': round(_ratio(rich_occurrences, relevant_occurrences), 3),
        'genome_current_version_ratio': round(_ratio(current_version_occurrences, relevant_occurrences), 3),
        'totals': {
            'files': total_files,
            'member_details': total_member_details,
            'observed_member_details': observed_member_details,
            'excluded_member_details': excluded_member_details,
            'genome_occurrences': relevant_occurrences,
        },
        'member_detail_applicability': {
            'policy': {
                str(profile): str(status)
                for profile, status in sorted(member_detail_applicability.items())
            },
            'observed_by_profile': dict(sorted(member_detail_profile_counts.items())),
            'excluded_by_profile': dict(sorted(excluded_member_detail_profile_counts.items())),
            'unknown_profiles_treated_as_required': dict(
                sorted(unknown_member_detail_profile_counts.items())
            ),
            'claim_boundary': (
                'The rich member-detail ratio applies only to profiles declared required. '
                'Known lower-depth profiles remain observed but are excluded explicitly; '
                'undeclared profiles are treated as required and cannot escape the gate.'
            ),
        },
    }


def _count_encoding_findings(paths):
    findings = []
    for path in paths:
        if not path.exists():
            continue
        text = path.read_text(encoding='utf-8', errors='ignore')
        count = sum(text.count(marker) for marker in SUSPICIOUS_MARKERS)
        if count:
            findings.append({'file': str(path), 'count': count})
    return findings


def _atlas_symbol_coverage(atlas: dict | None = None, genome: dict | None = None):
    atlas = atlas if isinstance(atlas, dict) else project_runtime_atlas(load_atlas_data())[0]
    genome = genome if isinstance(genome, dict) else load_genome_data()
    
    total_files = 0
    symbol_rich_files_set = set()
    total_symbols = 0
    
    atlas_file_refs = set()
    for project_key, pdata in atlas.items() if isinstance(atlas, dict) else []:
        if not isinstance(pdata, dict):
            continue
        project = str(project_key)
        files_data = pdata.get('files') or {}
        if not isinstance(files_data, dict):
            continue
        total_files += len(files_data)
        if project:
            atlas_file_refs.update(f"{project}::{rel}" for rel in files_data)

    # 2. Harvest symbols and rich files from Genome
    if isinstance(genome, dict):
        for sym_name, occurrences in genome.items():
            for occ in (occurrences or []):
                total_symbols += 1
                proj = occ.get('project')
                rel_path = occ.get('file')
                if proj and rel_path:
                    symbol_rich_files_set.add(f"{proj}::{rel_path}")

    symbol_rich_files = len(symbol_rich_files_set & atlas_file_refs) if atlas_file_refs else len(symbol_rich_files_set)
    return {
        'atlas_symbol_file_ratio': round(_ratio(symbol_rich_files, total_files), 3),
        'totals': {
            'files': total_files,
            'symbol_rich_files': symbol_rich_files,
            'symbols': total_symbols,
        },
    }


def _effective_min_atlas_symbol_ratio(configured_threshold: float, total_files: int) -> float:
    from tools.core.config import DOCTRINE
    scaling = require_doctrine_mapping("quality_gate_heuristics").get("atlas_symbol_scaling")
    
    # Apply doctrine-driven scaling (e.g. smaller projects have lower symbol requirements)
    for rule in scaling:
        if total_files <= rule.get("max_files", 0):
            return min(configured_threshold, rule.get("min_ratio", 0.9))
            
    return configured_threshold


def _state_flow_coverage(atlas: dict | None = None, state_flow: dict | None = None):
    atlas = atlas if isinstance(atlas, dict) else project_runtime_atlas(load_atlas_data())[0]
    state_flow = state_flow if isinstance(state_flow, dict) else load_json_file(RAW_DIR / 'state_flow.json', {})
    
    monitoring_policy = require_doctrine_mapping("framework_monitoring_keys")
    check_fields = monitoring_policy.get("check_fields", [
        "has_zustand_store", "zustand_consumers", "zustand_no_selector_calls",
        "zustand_broad_selector_calls", "query_keys", "mutation_keys", "client_actions"
    ])
    state_keys = monitoring_policy.get("state_keys", [
        "zustand_stores", "zustand_consumers", "tanstack_queries", "tanstack_mutations"
    ])

    atlas_stateful_files = set()
    if isinstance(atlas, dict):
        for pkey, pdata in atlas.items():
            files = (pdata or {}).get('files', {}) or {}
            for rel, fdata in files.items():
                if not isinstance(fdata, dict):
                    continue
                payload = fdata.get('state_flow') or {}
                if any(payload.get(field) for field in check_fields):
                    atlas_stateful_files.add(f"{pkey}::{rel}")

    scanner_stateful_files = set()
    for key in state_keys:
        bucket = state_flow.get(key, {}) if isinstance(state_flow, dict) else {}
        if isinstance(bucket, dict):
            scanner_stateful_files.update(bucket.keys())
    boundary_signals = state_flow.get('boundary_signals', {}) if isinstance(state_flow, dict) else {}
    if isinstance(boundary_signals, dict):
        scanner_stateful_files.update(boundary_signals.keys())

    detected_stateful_files = atlas_stateful_files & scanner_stateful_files
    scanner_only_files = scanner_stateful_files - atlas_stateful_files
    return {
        'state_flow_detection_ratio': round(_ratio(len(detected_stateful_files), len(atlas_stateful_files)), 3),
        'totals': {
            'atlas_stateful_files': len(atlas_stateful_files),
            'scanner_stateful_files': len(scanner_stateful_files),
            'detected_atlas_stateful_files': len(detected_stateful_files),
            'scanner_only_files': len(scanner_only_files),
        },
    }


def _blast_radius_coverage(blast: dict | None = None):
    blast = blast if isinstance(blast, dict) else load_json_file(RAW_DIR / 'blast_radius.json', {})
    entries = blast.get('blast_radius', []) if isinstance(blast, dict) else []
    total = len(entries)
    nonzero = 0
    for row in entries:
        if not isinstance(row, dict):
            continue
        if (
            float(row.get('boundary_bonus', 0) or 0) > 0
            or int(row.get('direct_dependents', 0) or 0) > 0
            or int(row.get('transitive_dependents', 0) or 0) > 0
        ):
            nonzero += 1
    return {
        'blast_nonzero_ratio': round(_ratio(nonzero, total), 3),
        'totals': {
            'blast_entries': total,
            'blast_nonzero_entries': nonzero,
        },
    }


def _optional_lte_check(gates: dict, name: str, actual: int, default_threshold: int):
    if name not in gates:
        expected = int(default_threshold)
        return {
            'name': name,
            'actual': actual,
            'expected': expected,
            'operator': '<=',
            'passed': actual <= expected,
            'details': ['policy_auto_default'],
        }
    expected = gates.get(name, default_threshold)
    return {
        'name': name,
        'actual': actual,
        'expected': expected,
        'operator': '<=',
        'passed': actual <= expected,
    }


def _react_support_coverage(matrix: dict | None = None):
    matrix_path = RAW_DIR / 'react_support_matrix.json'
    matrix = matrix if isinstance(matrix, dict) else load_json_file(matrix_path, {})
    if not isinstance(matrix, dict) or not matrix:
        return {
            'react_detected_present_ratio': 0.0,
            'totals': {
                'repo_present': 0,
                'detected': 0,
                'partial': 0,
                'missing': 0,
            },
        }
    summary = matrix.get('summary', {}) if isinstance(matrix, dict) else {}
    repo_present = int(summary.get('repo_present', 0) or 0)
    detected = int(summary.get('detected', 0) or 0)
    partial = int(summary.get('partial', 0) or 0)
    missing = int(summary.get('missing', 0) or 0)
    return {
        'react_detected_present_ratio': round(_ratio(detected, repo_present), 3),
        'totals': {
            'repo_present': repo_present,
            'detected': detected,
            'partial': partial,
            'missing': missing,
        },
    }


def _dead_code_gate_metrics(dead_payload: dict | None = None, atlas: dict | None = None):
    dead_payload = dead_payload if isinstance(dead_payload, dict) else load_json_file(RAW_DIR / 'dead_code.json', {})
    atlas = atlas if isinstance(atlas, dict) else project_runtime_atlas(load_atlas_data())[0]

    dead_summary = dead_payload.get('summary', {}) if isinstance(dead_payload, dict) else {}
    dead_total = int(dead_summary.get('total', 0) or 0)
    dead_high = int(dead_summary.get('high', 0) or 0)
    dead_medium = int(dead_summary.get('medium', 0) or 0)

    export_universe = 0
    if isinstance(atlas, dict):
        for pdata in atlas.values():
            files = (pdata or {}).get('files', {}) or {}
            for rel_path, fdata in files.items():
                n_file = to_posix_path(rel_path)
                if not is_analysis_source_file(n_file):
                    continue
                for exp in (fdata or {}).get('exports', []) or []:
                    if isinstance(exp, dict) and exp.get('type') == 'ProxyExport':
                        continue
                    export_universe += 1

    return {
        'dead_total': dead_total,
        'dead_high': dead_high,
        'dead_medium': dead_medium,
        'dead_high_ratio': round(_ratio(dead_high, export_universe), 4),
        'dead_medium_ratio': round(_ratio(dead_medium, export_universe), 4),
        'totals': {
            'export_universe': export_universe,
            'dead_total': dead_total,
            'dead_high': dead_high,
            'dead_medium': dead_medium,
        },
    }


def _effective_dead_code_ratio_threshold(configured_threshold: float, export_universe: int, ratio_kind: str) -> float:
    if export_universe <= 0:
        return configured_threshold

    scaling = require_doctrine_mapping("quality_gate_heuristics").get("dead_code_ratio_scaling")
    rules = scaling.get(ratio_kind, []) if isinstance(scaling, dict) else []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        max_export_universe = rule.get("max_export_universe")
        min_export_universe = rule.get("min_export_universe")
        if max_export_universe is not None and export_universe > int(max_export_universe):
            continue
        if min_export_universe is not None and export_universe < int(min_export_universe):
            continue
        return max(configured_threshold, float(rule.get("min_ratio", configured_threshold)))

    return configured_threshold


def run_quality_gates():
    logger.info('Running pipeline quality gates...')

    gates = get_quality_gates()
    health = load_json_file(RAW_DIR / 'health_score.json', {})
    expected_failure_drill = os.environ.get("CODEMAPS_EXPECTED_FAILURE_DRILL") == "1"
    if expected_failure_drill and not health:
        min_health = int(gates.get('min_health_score', 0) or 0)
        payload = {
            'passed': False,
            'release_gate_status': 'FAIL',
            'ecosystem_signal_status': 'CLEAR',
            'ecosystem_warning_signals': {},
            'audit_enforcement_authority': _audit_enforcement_authority(),
            'checks': [
                {
                    'name': 'min_health_score',
                    'actual': 0,
                    'expected': min_health,
                    'operator': '>=',
                    'passed': 0 >= min_health,
                    'details': ['expected_failure_drill', 'health_score_missing'],
                }
            ],
        }
        ensure_valid_payload("quality_gate", payload)
        save_json_atomic(RAW_DIR / 'quality_gate.json', payload)
        write_current_atlas_lineage(
            artifact_id="quality_gate",
            producer="tools.engines.quality_gate",
            artifact_payload=payload,
            atlas={},
            dependency_payloads={},
        )
        save_text_atomic(
            REPORTS_DIR / 'quality_gate.md',
            "\n".join(
                [
                    "# Pipeline Quality Gates",
                    "",
                    "Release Gate: FAIL",
                    "Ecosystem Signal: CLEAR",
                    "",
                    "> Expected failure drill: `health_score.json` was intentionally removed.",
                    "",
                    "| Check | Scope | Enforced | Actual | Rule | Expected | Result |",
                    "|---|---|---|---:|---|---:|---|",
                    f"| `min_health_score` | pipeline | YES | 0 | `>=` | {min_health} | FAIL |",
                ]
            )
            + "\n",
        )
        logger.info("[EXPECTED_FAILURE_DRILL] Minimal Quality Gate red state produced for missing health_score.json.")
        return

    fractal = load_fractal_map_data()
    atlas, _execution_scope = project_runtime_atlas(load_atlas_data())
    genome = load_genome_data()
    audit_report = load_audit_report()
    state_flow = load_json_file(RAW_DIR / 'state_flow.json', {})
    blast_radius = load_json_file(RAW_DIR / 'blast_radius.json', {})
    react_support_matrix = load_json_file(RAW_DIR / 'react_support_matrix.json', {})
    project_dna_payload = load_json_file(RAW_DIR / 'project_dna_profile.json', {})
    dead_code_payload = load_json_file(RAW_DIR / 'dead_code.json', {})
    ui_runtime_payload = load_json_file(RAW_DIR / 'ui_runtime_contracts.json', {})
    merge_packages_payload = load_json_file(RAW_DIR / 'merge_dependency_packages.json', {})
    merge_simulation_payload = load_json_file(RAW_DIR / 'merge_simulation.json', {})
    merge_cockpit_payload = load_json_file(RAW_DIR / 'merge_decision_cockpit.json', {})
    quality_review_payload = load_json_file(RAW_DIR / 'quality_review.json', {})
    proof_obligations_payload = load_json_file(RAW_DIR / 'proof_obligations.json', {})
    artifact_validation = validate_all_artifacts()
    workspace_mode = get_workspace_mode()
    comparative_enabled = bool((workspace_mode or {}).get('comparative_enabled', False))
    from tools.core.config import DOCTRINE
    q_defaults = require_dead_code_policy("quality_defaults")

    required_artifacts = gates.get('required_artifacts', [])
    missing_required = [name for name in required_artifacts if artifact_validation.get(name)]

    meta = fractal.get('meta', {})
    ui_runtime_summary = ui_runtime_payload.get('summary', {}) if isinstance(ui_runtime_payload, dict) else {}
    ui_candidate_tiers = ui_runtime_summary.get('candidate_risk_tiers', {}) if isinstance(ui_runtime_summary, dict) else {}
    ui_file_tiers = ui_runtime_summary.get('risk_tiers', {}) if isinstance(ui_runtime_summary, dict) else {}
    ui_high_risk_merge_candidates = int(ui_candidate_tiers.get('high', 0) or 0)
    ui_browser_smoke_required = 0
    if isinstance(ui_runtime_payload, dict):
        ui_browser_smoke_required = sum(
            1
            for item in ui_runtime_payload.get('merge_candidates', []) or []
            if isinstance(item, dict) and item.get('recommended_gate') == 'browser_smoke_required'
        )
    ui_main_high_risk_files = int(
        ((ui_runtime_summary.get('by_project', {}) or {}).get('MAIN', {}) or {}).get('high', 0) or 0
    ) if isinstance(ui_runtime_summary, dict) else 0
    merge_package_summary = merge_packages_payload.get('summary', {}) if isinstance(merge_packages_payload, dict) else {}
    merge_package_tiers = merge_package_summary.get('package_tiers', {}) if isinstance(merge_package_summary, dict) else {}
    manual_merge_packages = int(merge_package_tiers.get('manual_package_review', 0) or 0)
    assisted_merge_packages = int(merge_package_tiers.get('assisted_package', 0) or 0)
    merge_simulation_summary = merge_simulation_payload.get('summary', {}) if isinstance(merge_simulation_payload, dict) else {}
    merge_simulation_decisions = merge_simulation_summary.get('decisions', {}) if isinstance(merge_simulation_summary, dict) else {}
    unsafe_merge_simulations = int(merge_simulation_decisions.get('DO_NOT_IMPORT_YET', 0) or 0)
    assisted_merge_simulations = int(merge_simulation_decisions.get('ASSISTED_IMPORT', 0) or 0)
    merge_cockpit_summary = merge_cockpit_payload.get('summary', {}) if isinstance(merge_cockpit_payload, dict) else {}
    merge_cockpit_actions = merge_cockpit_summary.get('actions', {}) if isinstance(merge_cockpit_summary, dict) else {}
    merge_cockpit_effective_actions = merge_cockpit_summary.get('effective_actions', merge_cockpit_actions) if isinstance(merge_cockpit_summary, dict) else {}
    merge_cockpit_suppressed = int(merge_cockpit_summary.get('suppressed', 0) or 0) if isinstance(merge_cockpit_summary, dict) else 0
    cockpit_do_not_import = int(merge_cockpit_actions.get('Do Not Import Yet', 0) or 0)
    cockpit_import_with_review = int(merge_cockpit_actions.get('Import With Review', 0) or 0)
    effective_cockpit_do_not_import = int(merge_cockpit_effective_actions.get('Do Not Import Yet', 0) or 0)
    effective_cockpit_import_with_review = int(merge_cockpit_effective_actions.get('Import With Review', 0) or 0)
    high_risk_candidates = int(meta.get('high', 0) or 0)
    manual_review = int(meta.get('manual_review', 0) or 0)
    unresolved_targets = int(meta.get('unresolved_targets', 0) or 0)
    total_audit_violations = _extract_total_audit_violations(audit_report)
    project_audit_violations = _extract_project_audit_violations(audit_report)
    audit_mode_breakdown = _audit_mode_breakdown(audit_report)
    health_score = int(health.get('overall', 0) or 0)
    structural_coverage = _structural_contract_coverage(atlas=atlas, genome=genome)
    atlas_symbol_coverage = _atlas_symbol_coverage(atlas=atlas, genome=genome)
    state_flow_coverage = _state_flow_coverage(atlas=atlas, state_flow=state_flow)
    blast_radius_coverage = _blast_radius_coverage(blast=blast_radius)
    react_support_coverage = _react_support_coverage(matrix=react_support_matrix)
    react_gate_applicable, react_gate_reason = _react_gate_applicable(project_dna_payload, react_support_matrix)
    dead_code_metrics = _dead_code_gate_metrics(dead_payload=dead_code_payload, atlas=atlas)
    export_universe = int(dead_code_metrics['totals'].get('export_universe', 0) or 0)
    configured_high_ratio = float(gates.get('max_dead_code_high_ratio', 0.02) or 0.02)
    configured_medium_ratio = float(gates.get('max_dead_code_medium_ratio', 0.04) or 0.04)
    effective_high_ratio = _effective_dead_code_ratio_threshold(
        configured_threshold=configured_high_ratio,
        export_universe=export_universe,
        ratio_kind='high',
    )
    effective_medium_ratio = _effective_dead_code_ratio_threshold(
        configured_threshold=configured_medium_ratio,
        export_universe=export_universe,
        ratio_kind='medium',
    )
    configured_high_abs = int(gates.get('max_dead_code_high', 140) or 140)
    configured_medium_abs = int(gates.get('max_dead_code_medium', 320) or 320)
    expected_high_abs = max(configured_high_abs, int(round(export_universe * effective_high_ratio)))
    expected_medium_abs = max(configured_medium_abs, int(round(export_universe * effective_medium_ratio)))
    quality_review_summary = quality_review_payload.get('summary', {}) if isinstance(quality_review_payload, dict) else {}
    quality_review_score = float(quality_review_summary.get('score', 0.0) or 0.0)
    quality_review_proof_level = int(quality_review_summary.get('proof_ladder_level', 0) or 0)
    quality_review_blockers = int(quality_review_summary.get('blockers', 0) or 0)
    proof_summary = proof_obligations_payload.get('summary', {}) if isinstance(proof_obligations_payload, dict) else {}
    proof_ratio = float(proof_summary.get('verification_ratio', 0.0) or 0.0)
    proof_failed_required = int(proof_summary.get('failed_required', 0) or 0)
    atlas_symbol_expected = _effective_min_atlas_symbol_ratio(
        float(gates.get('min_atlas_symbol_file_ratio', 0.9) or 0.9),
        int(atlas_symbol_coverage['totals'].get('files', 0) or 0),
    )
    atlas_symbol_configured = float(gates.get('min_atlas_symbol_file_ratio', 0.9) or 0.9)
    project_count = max(len(atlas) if isinstance(atlas, dict) else 0, 1)
    default_high_risk_cap = 5 + (project_count * 3)
    manual_review_budget = manual_review_budget_selection(
        atlas,
        DYNAMIC_CONFIG.get('project_roles', {}) if isinstance(DYNAMIC_CONFIG, dict) else {},
        meta,
        gates,
    )
    
    # Doctrine Gating
    from tools.core.doctrine_contract import require_doctrine_path
    max_side_effect_limit = require_doctrine_path("governance_policy", "max_side_effect_intensity", expected_type=(int, float))
    # Side effect intensity is usually in health score or genome. 
    # For now we use health score's 'complexity' or a similar proxy if available.
    current_side_effect_load = health.get("risk_matrix", {}).get("global_intensity", 0) 

    encoding_targets = [
        CONFIG_FILE,
        DISCOVERY_FILE,
        AUDIT_REPORT_TEXT_PATH,
        REPORTS_DIR / 'health_score.md',
        REPORTS_DIR / 'module_risk_matrix.md',
        MASTER_REPORT_PATH,
    ]
    encoding_findings = _count_encoding_findings(encoding_targets)
    encoding_total = sum(item['count'] for item in encoding_findings)

    max_project_audit_violations = gates.get('max_project_audit_violations', gates.get('max_total_audit_violations', 0))
    max_main_audit_violations = gates.get('max_main_audit_violations', max_project_audit_violations)
    max_enforced_audit_violations = gates.get('max_enforced_audit_violations', gates.get('max_total_audit_violations', 0))
    worst_project = None
    if project_audit_violations:
        worst_project = max(project_audit_violations.items(), key=lambda pair: pair[1])
    main_breakdown = audit_mode_breakdown['by_project'].get('MAIN', {})
    worst_enforced_project = None
    if audit_mode_breakdown['by_project']:
        worst_enforced_project = max(
            audit_mode_breakdown['by_project'].items(),
            key=lambda pair: pair[1].get('enforced', 0),
        )
    release_scope_projects = release_scope_project_keys(audit_mode_breakdown['by_project'])
    release_audit_breakdown = {
        project: audit_mode_breakdown['by_project'].get(project, {})
        for project in sorted(release_scope_projects)
        if project in audit_mode_breakdown['by_project']
    }
    release_enforced_total = sum(int(data.get('enforced', 0) or 0) for data in release_audit_breakdown.values())
    worst_release_enforced_project = None
    if release_audit_breakdown:
        worst_release_enforced_project = max(
            release_audit_breakdown.items(),
            key=lambda pair: pair[1].get('enforced', 0),
        )

    checks = [
        {
            'name': 'required_artifacts',
            'actual': len(missing_required),
            'expected': 0,
            'operator': '==',
            'passed': len(missing_required) == 0,
            'details': missing_required,
        },
        {
            'name': 'min_health_score',
            'actual': health_score,
            'expected': gates.get('min_health_score', 0),
            'operator': '>=',
            'passed': health_score >= gates.get('min_health_score', 0),
        },
        {
            'name': 'max_unresolved_targets',
            'actual': unresolved_targets,
            'expected': gates.get('max_unresolved_targets', 0),
            'operator': '<=',
            'passed': unresolved_targets <= gates.get('max_unresolved_targets', 0),
        },
        {
            **_optional_lte_check(gates, 'max_high_risk_candidates', high_risk_candidates, default_high_risk_cap),
            'enforced': comparative_enabled,
            'details': ['non_comparative_workspace'] if not comparative_enabled else ['policy_auto_default'],
        },
        {
            'name': 'max_manual_review',
            'actual': manual_review,
            'expected': manual_review_budget['selected_budget'],
            'operator': '<=',
            'passed': manual_review <= manual_review_budget['selected_budget'],
            'enforced': comparative_enabled,
            'details': (
                ['non_comparative_workspace']
                if not comparative_enabled
                else [
                    'adaptive_workload_budget',
                    {
                        'source': 'fractal_map',
                        'manual_review_source': 'fractal_meta',
                        'budget_selection': manual_review_budget,
                    },
                ]
            ),
        },
        {
            **_optional_lte_check(gates, 'max_ui_high_risk_merge_candidates', ui_high_risk_merge_candidates, 0),
            'enforced': 'max_ui_high_risk_merge_candidates' in gates,
            'details': [{'source': 'ui_runtime_contracts', 'summary': ui_runtime_summary}],
        },
        {
            **_optional_lte_check(gates, 'max_ui_browser_smoke_required_candidates', ui_browser_smoke_required, 0),
            'enforced': 'max_ui_browser_smoke_required_candidates' in gates,
            'details': [{'source': 'ui_runtime_contracts', 'summary': ui_runtime_summary}],
        },
        {
            **_optional_lte_check(gates, 'max_main_ui_high_risk_files', ui_main_high_risk_files, 0),
            'enforced': 'max_main_ui_high_risk_files' in gates,
            'details': [{'source': 'ui_runtime_contracts', 'main_high': ui_main_high_risk_files, 'risk_tiers': ui_file_tiers}],
        },
        {
            **_optional_lte_check(gates, 'max_manual_merge_dependency_packages', manual_merge_packages, 0),
            'enforced': 'max_manual_merge_dependency_packages' in gates,
            'details': [{'source': 'merge_dependency_packages', 'summary': merge_package_summary}],
        },
        {
            **_optional_lte_check(gates, 'max_assisted_merge_dependency_packages', assisted_merge_packages, 0),
            'enforced': 'max_assisted_merge_dependency_packages' in gates,
            'details': [{'source': 'merge_dependency_packages', 'summary': merge_package_summary}],
        },
        {
            **_optional_lte_check(gates, 'max_unsafe_merge_simulations', unsafe_merge_simulations, 0),
            'enforced': 'max_unsafe_merge_simulations' in gates,
            'details': [{'source': 'merge_simulation', 'summary': merge_simulation_summary}],
        },
        {
            **_optional_lte_check(gates, 'max_assisted_merge_simulations', assisted_merge_simulations, 0),
            'enforced': 'max_assisted_merge_simulations' in gates,
            'details': [{'source': 'merge_simulation', 'summary': merge_simulation_summary}],
        },
        {
            **_optional_lte_check(gates, 'max_cockpit_do_not_import', effective_cockpit_do_not_import, 0),
            'enforced': 'max_cockpit_do_not_import' in gates,
            'details': [{'source': 'merge_decision_cockpit', 'summary': merge_cockpit_summary, 'raw_actual': cockpit_do_not_import}],
        },
        {
            **_optional_lte_check(gates, 'max_cockpit_import_with_review', effective_cockpit_import_with_review, 0),
            'enforced': 'max_cockpit_import_with_review' in gates,
            'details': [{'source': 'merge_decision_cockpit', 'summary': merge_cockpit_summary, 'raw_actual': cockpit_import_with_review}],
        },
        {
            'name': 'ecosystem_total_audit_violations',
            'actual': total_audit_violations,
            'expected': gates.get('max_total_audit_violations', 0),
            'operator': '<=',
            'passed': total_audit_violations <= gates.get('max_total_audit_violations', 0),
            'enforced': False,
            'details': [project_audit_violations, audit_mode_breakdown['totals']],
        },
        {
            'name': 'max_enforced_audit_violations',
            'actual': release_enforced_total,
            'expected': max_enforced_audit_violations,
            'operator': '<=',
            'passed': release_enforced_total <= max_enforced_audit_violations,
            'details': [
                {
                    'release_scope_projects': sorted(release_scope_projects),
                    'release_scope': release_audit_breakdown,
                    'ecosystem_totals': audit_mode_breakdown['totals'],
                },
                audit_mode_breakdown['taxonomy_summary'],
            ],
        },
        {
            'name': 'advisory_audit_violations',
            'actual': int(audit_mode_breakdown['totals'].get('advisory', 0) or 0),
            'expected': 0,
            'operator': 'info',
            'passed': True,
            'enforced': False,
            'details': [audit_mode_breakdown['totals'], audit_mode_breakdown['taxonomy_summary']],
        },
        {
            'name': 'max_encoding_findings',
            'actual': encoding_total,
            'expected': gates.get('max_encoding_findings', 0),
            'operator': '<=',
            'passed': encoding_total <= gates.get('max_encoding_findings', 0),
            'details': encoding_findings,
        },
        {
            'name': 'min_atlas_contract_file_ratio',
            'actual': structural_coverage['atlas_contract_file_ratio'],
            'expected': gates.get('min_atlas_contract_file_ratio', q_defaults.get('min_atlas_contract_file_ratio', 0.99)),
            'operator': '>=',
            'passed': structural_coverage['atlas_contract_file_ratio'] >= gates.get('min_atlas_contract_file_ratio', q_defaults.get('min_atlas_contract_file_ratio', 0.99)),
            'details': [structural_coverage['totals']],
        },
        {
            'name': 'min_member_detail_contract_ratio',
            'actual': structural_coverage['member_detail_contract_ratio'],
            'expected': gates.get('min_member_detail_contract_ratio', q_defaults.get('min_member_detail_contract_ratio', 0.99)),
            'operator': '>=',
            'passed': structural_coverage['member_detail_contract_ratio'] >= gates.get('min_member_detail_contract_ratio', q_defaults.get('min_member_detail_contract_ratio', 0.99)),
            'details': [
                structural_coverage['totals'],
                structural_coverage['member_detail_applicability'],
            ],
        },
        {
            'name': 'min_genome_occurrence_contract_ratio',
            'actual': structural_coverage['genome_occurrence_contract_ratio'],
            'expected': gates.get('min_genome_occurrence_contract_ratio', q_defaults.get('min_genome_occurrence_contract_ratio', 0.99)),
            'operator': '>=',
            'passed': structural_coverage['genome_occurrence_contract_ratio'] >= gates.get('min_genome_occurrence_contract_ratio', q_defaults.get('min_genome_occurrence_contract_ratio', 0.99)),
            'details': [structural_coverage['totals']],
        },
        {
            'name': 'min_atlas_current_version_ratio',
            'actual': structural_coverage['atlas_current_version_ratio'],
            'expected': gates.get('min_atlas_current_version_ratio', q_defaults.get('min_atlas_current_version_ratio', 0.99)),
            'operator': '>=',
            'passed': structural_coverage['atlas_current_version_ratio'] >= gates.get('min_atlas_current_version_ratio', q_defaults.get('min_atlas_current_version_ratio', 0.99)),
            'details': [{'expected_ast_contract_version': AST_CONTRACT_VERSION, **structural_coverage['totals']}],
        },
        {
            'name': 'min_genome_current_version_ratio',
            'actual': structural_coverage['genome_current_version_ratio'],
            'expected': gates.get('min_genome_current_version_ratio', q_defaults.get('min_genome_current_version_ratio', 0.99)),
            'operator': '>=',
            'passed': structural_coverage['genome_current_version_ratio'] >= gates.get('min_genome_current_version_ratio', q_defaults.get('min_genome_current_version_ratio', 0.99)),
            'details': [{'expected_ast_contract_version': AST_CONTRACT_VERSION, **structural_coverage['totals']}],
        },
        {
            'name': 'min_atlas_symbol_file_ratio',
            'actual': atlas_symbol_coverage['atlas_symbol_file_ratio'],
            'expected': atlas_symbol_expected,
            'operator': '>=',
            'passed': atlas_symbol_coverage['atlas_symbol_file_ratio'] >= atlas_symbol_expected,
            'details': [
                {
                    **atlas_symbol_coverage['totals'],
                    'configured_threshold': atlas_symbol_configured,
                    'effective_threshold': atlas_symbol_expected,
                    'is_scaled': atlas_symbol_expected != atlas_symbol_configured,
                }
            ],
        },
        {
            'name': 'min_state_flow_detection_ratio',
            'actual': state_flow_coverage['state_flow_detection_ratio'],
            'expected': gates.get('min_state_flow_detection_ratio', q_defaults.get('min_state_flow_detection_ratio', 0.95)),
            'operator': '>=',
            'passed': state_flow_coverage['state_flow_detection_ratio'] >= gates.get('min_state_flow_detection_ratio', q_defaults.get('min_state_flow_detection_ratio', 0.95)),
            'details': [state_flow_coverage['totals']],
        },
        {
            'name': 'min_blast_nonzero_ratio',
            'actual': blast_radius_coverage['blast_nonzero_ratio'],
            'expected': gates.get('min_blast_nonzero_ratio', q_defaults.get('min_blast_nonzero_ratio', 0.05)),
            'operator': '>=',
            'passed': blast_radius_coverage['blast_nonzero_ratio'] >= gates.get('min_blast_nonzero_ratio', q_defaults.get('min_blast_nonzero_ratio', 0.05)),
            'details': [blast_radius_coverage['totals']],
        },
        {
            'name': 'min_react_detected_present_ratio',
            'actual': react_support_coverage['react_detected_present_ratio'],
            'expected': gates.get('min_react_detected_present_ratio', q_defaults.get('min_react_detected_present_ratio', 1.0)),
            'operator': '>=',
            'passed': react_support_coverage['react_detected_present_ratio'] >= gates.get('min_react_detected_present_ratio', q_defaults.get('min_react_detected_present_ratio', 1.0)),
            'details': [react_support_coverage['totals']],
        },
        {
            'name': 'max_react_partial_capabilities',
            'actual': react_support_coverage['totals']['partial'],
            'expected': gates.get('max_react_partial_capabilities', q_defaults.get('max_react_partial_capabilities', 0)),
            'operator': '<=',
            'passed': react_support_coverage['totals']['partial'] <= gates.get('max_react_partial_capabilities', q_defaults.get('max_react_partial_capabilities', 0)),
            'details': [react_support_coverage['totals']],
        },
        _optional_lte_check(gates, 'max_dead_code_total', dead_code_metrics['dead_total'], 1400),
        {
            'name': 'max_dead_code_high',
            'actual': dead_code_metrics['dead_high'],
            'expected': expected_high_abs,
            'operator': '<=',
            'passed': dead_code_metrics['dead_high'] <= expected_high_abs,
            'details': [{
                **dead_code_metrics['totals'],
                'configured_threshold': configured_high_abs,
                'effective_threshold': expected_high_abs,
                'effective_ratio': effective_high_ratio,
                'is_scaled': expected_high_abs != configured_high_abs,
            }],
        },
        {
            'name': 'max_dead_code_medium',
            'actual': dead_code_metrics['dead_medium'],
            'expected': expected_medium_abs,
            'operator': '<=',
            'passed': dead_code_metrics['dead_medium'] <= expected_medium_abs,
            'details': [{
                **dead_code_metrics['totals'],
                'configured_threshold': configured_medium_abs,
                'effective_threshold': expected_medium_abs,
                'effective_ratio': effective_medium_ratio,
                'is_scaled': expected_medium_abs != configured_medium_abs,
            }],
        },
        {
            'name': 'min_quality_review_score',
            'actual': quality_review_score,
            'expected': float(gates.get('min_quality_review_score', 0.6) or 0.6),
            'operator': '>=',
            'passed': quality_review_score >= float(gates.get('min_quality_review_score', 0.6) or 0.6),
            'details': [quality_review_summary],
        },
        {
            'name': 'min_quality_proof_ladder_level',
            'actual': quality_review_proof_level,
            'expected': int(gates.get('min_quality_proof_ladder_level', 2) or 2),
            'operator': '>=',
            'passed': quality_review_proof_level >= int(gates.get('min_quality_proof_ladder_level', 2) or 2),
            'details': [quality_review_summary],
        },
        _optional_lte_check(gates, 'max_quality_blockers', quality_review_blockers, 0),
        {
            'name': 'min_proof_obligation_ratio',
            'actual': proof_ratio,
            'expected': float(gates.get('min_proof_obligation_ratio', 0.95) or 0.95),
            'operator': '>=',
            'passed': proof_ratio >= float(gates.get('min_proof_obligation_ratio', 0.95) or 0.95),
            'details': [proof_summary],
        },
        _optional_lte_check(gates, 'max_proof_required_failures', proof_failed_required, 0),
        {
            'name': 'max_side_effect_intensity',
            'actual': current_side_effect_load,
            'expected': max_side_effect_limit,
            'operator': '<=',
            'passed': current_side_effect_load <= max_side_effect_limit,
            'details': ['Doctrine Enforced'],
        },
        {
            'name': 'healable_violations',
            'actual': int(audit_mode_breakdown['totals'].get('heal', 0) or 0),
            'expected': 0,
            'operator': 'heal_mode',
            'passed': True, # Healable don't block by default, they are handled by merge_engine
            'enforced': False,
        }
    ]
    if not react_gate_applicable:
        react_bound_checks = {
            'min_state_flow_detection_ratio',
            'min_react_detected_present_ratio',
            'max_react_partial_capabilities',
        }
        checks = [
            _mark_not_applicable(check, react_gate_reason)
            if check.get('name') in react_bound_checks
            else check
            for check in checks
        ]
    empty_scope_checks = set()
    if int(structural_coverage['totals'].get('member_details', 0) or 0) <= 0:
        empty_scope_checks.add('min_member_detail_contract_ratio')
    if int(structural_coverage['totals'].get('genome_occurrences', 0) or 0) <= 0:
        empty_scope_checks.update({
            'min_genome_occurrence_contract_ratio',
            'min_genome_current_version_ratio',
        })
    if int(state_flow_coverage['totals'].get('atlas_stateful_files', 0) or 0) <= 0:
        empty_scope_checks.add('min_state_flow_detection_ratio')
    if empty_scope_checks:
        empty_scope_reason = {
            'applicability_rule': 'non_empty_metric_denominator',
            'empty_scope_checks': sorted(empty_scope_checks),
        }
        checks = [
            _mark_not_applicable(check, empty_scope_reason)
            if check.get('name') in empty_scope_checks
            else check
            for check in checks
        ]
    for check in checks:
        if check.get('name') in {'max_dead_code_total'}:
            check['details'] = [dead_code_metrics['totals']]

    if 'max_dead_code_high_ratio' in gates:
        expected_high_ratio = effective_high_ratio
        checks.append({
            'name': 'max_dead_code_high_ratio',
            'actual': dead_code_metrics['dead_high_ratio'],
            'expected': expected_high_ratio,
            'operator': '<=',
            'passed': dead_code_metrics['dead_high_ratio'] <= expected_high_ratio,
            'details': [{
                **dead_code_metrics['totals'],
                'configured_threshold': configured_high_ratio,
                'effective_threshold': expected_high_ratio,
                'is_scaled': expected_high_ratio != configured_high_ratio,
            }],
        })
    else:
        checks.append({
            'name': 'max_dead_code_high_ratio',
            'actual': dead_code_metrics['dead_high_ratio'],
            'expected': 0,
            'operator': 'disabled',
            'passed': True,
            'enforced': False,
            'details': [dead_code_metrics['totals'], 'policy_not_configured'],
        })

    if 'max_dead_code_medium_ratio' in gates:
        expected_medium_ratio = effective_medium_ratio
        checks.append({
            'name': 'max_dead_code_medium_ratio',
            'actual': dead_code_metrics['dead_medium_ratio'],
            'expected': expected_medium_ratio,
            'operator': '<=',
            'passed': dead_code_metrics['dead_medium_ratio'] <= expected_medium_ratio,
            'details': [{
                **dead_code_metrics['totals'],
                'configured_threshold': configured_medium_ratio,
                'effective_threshold': expected_medium_ratio,
                'is_scaled': expected_medium_ratio != configured_medium_ratio,
            }],
        })
    else:
        checks.append({
            'name': 'max_dead_code_medium_ratio',
            'actual': dead_code_metrics['dead_medium_ratio'],
            'expected': 0,
            'operator': 'disabled',
            'passed': True,
            'enforced': False,
            'details': [dead_code_metrics['totals'], 'policy_not_configured'],
        })

    if project_audit_violations:
        main_violations = int(main_breakdown.get('enforced', project_audit_violations.get('MAIN', 0)) or 0)
        checks.append({
            'name': 'max_main_audit_violations',
            'actual': main_violations,
            'expected': max_main_audit_violations,
            'operator': '<=',
            'passed': main_violations <= max_main_audit_violations,
            'details': [main_breakdown or project_audit_violations],
        })
        if worst_release_enforced_project:
            checks.append({
                'name': 'max_any_project_audit_violations',
                'actual': worst_release_enforced_project[1].get('enforced', 0),
                'expected': max_project_audit_violations,
                'operator': '<=',
                'passed': worst_release_enforced_project[1].get('enforced', 0) <= max_project_audit_violations,
                'details': [
                    {
                        'project': worst_release_enforced_project[0],
                        'count': worst_release_enforced_project[1].get('enforced', 0),
                        'release_scope_projects': sorted(release_scope_projects),
                    },
                    audit_mode_breakdown['by_project'],
                ],
            })

    overall_pass = all(check['passed'] for check in checks if check.get('enforced', True))
    ecosystem_attention = any(
        not check.get('enforced', True) and not check.get('passed', True)
        for check in checks
    )
    ecosystem_warning_signals = {
        'total_audit_violations': total_audit_violations,
        'healable_violations': int(audit_mode_breakdown['totals'].get('heal', 0) or 0),
        'advisory_violations': int(audit_mode_breakdown['totals'].get('advisory', 0) or 0),
        'ui_high_risk_merge_candidates': ui_high_risk_merge_candidates,
        'ui_browser_smoke_required_candidates': ui_browser_smoke_required,
        'main_ui_high_risk_files': ui_main_high_risk_files,
        'manual_merge_dependency_packages': manual_merge_packages,
        'assisted_merge_dependency_packages': assisted_merge_packages,
        'unsafe_merge_simulations': unsafe_merge_simulations,
        'assisted_merge_simulations': assisted_merge_simulations,
        'cockpit_do_not_import': cockpit_do_not_import,
        'cockpit_import_with_review': cockpit_import_with_review,
        'effective_cockpit_do_not_import': effective_cockpit_do_not_import,
        'effective_cockpit_import_with_review': effective_cockpit_import_with_review,
        'suppressed_cockpit_decisions': merge_cockpit_suppressed,
    }
    payload = {
        'passed': overall_pass,
        'release_gate_status': 'PASS' if overall_pass else 'FAIL',
        'ecosystem_signal_status': 'ATTENTION' if ecosystem_attention else 'CLEAR',
        'ecosystem_warning_signals': ecosystem_warning_signals,
        'audit_enforcement_authority': _audit_enforcement_authority(),
        'checks': checks,
    }

    ensure_valid_payload("quality_gate", payload)

    json_path = RAW_DIR / 'quality_gate.json'
    save_json_atomic(json_path, payload)
    write_current_atlas_lineage(
        artifact_id="quality_gate",
        producer="tools.engines.quality_gate",
        artifact_payload=payload,
        atlas=atlas,
        dependency_payloads={
            "genome": genome,
            "audit_report": audit_report,
        },
    )

    lines = [
        '# Pipeline Quality Gates',
        '',
        f"Release Gate: {'PASS' if overall_pass else 'FAIL'}",
        f"Ecosystem Signal: {'ATTENTION' if ecosystem_attention else 'CLEAR'}",
        '',
        '> Enforced checks decide PASS/FAIL. Informational checks stay visible but do not block green status.',
        '> Audit enforcement is SAGE-native only; target-repository lint or policy enforcement is not yet ingested into the combined verdict.',
        '',
        '| Check | Scope | Enforced | Actual | Rule | Expected | Result |',
        '|---|---|---|---:|---|---:|---|',
    ]
    for check in checks:
        scope = 'ecosystem'
        name = str(check.get('name', ''))
        if 'main_' in name:
            scope = 'MAIN'
        elif 'any_project' in name:
            scope = 'per-project'
        elif name.startswith('ecosystem_'):
            scope = 'ecosystem'
        elif name.startswith('min_') or name.startswith('max_'):
            scope = 'pipeline'
        lines.append(
            f"| `{check['name']}` | {scope} | "
            f"{'YES' if check.get('enforced', True) else 'NO'} | "
            f"{check['actual']} | `{check['operator']}` | "
            f"{check['expected']} | {'PASS' if check['passed'] else 'FAIL'} |"
        )
        if check.get('details'):
            lines.append(f"| details |  |  | `{json.dumps(check['details'], ensure_ascii=False)}` |  |  |  |")

    if project_audit_violations:
        lines.extend([
            '',
            '## Project Audit Totals',
            '',
            '| Project | Enforced | Healable | Advisory | Disabled | Unknown | Total |',
            '|---|---:|---:|---:|---:|---:|---:|',
        ])
        for project, count in sorted(project_audit_violations.items(), key=lambda pair: (-pair[1], pair[0])):
            breakdown = audit_mode_breakdown['by_project'].get(project, {})
            project_label = f"{project_display_name(project)} [{project}]"
            lines.append(
                f"| `{project_label}` | {int(breakdown.get('enforced', 0) or 0)} | "
                f"{int(breakdown.get('heal', 0) or 0)} | "
                f"{int(breakdown.get('advisory', 0) or 0)} | "
                f"{int(breakdown.get('disabled', 0) or 0)} | "
                f"{int(breakdown.get('unknown', 0) or 0)} | {count} |"
            )

    md_path = REPORTS_DIR / 'quality_gate.md'
    save_text_atomic(md_path, '\n'.join(lines))

    if expected_failure_drill and not overall_pass:
        logger.info(
            "[EXPECTED_FAILURE_DRILL] Quality gates produced the expected controlled red result: "
            f"{to_posix_path(md_path.relative_to(REPORTS_DIR.parent))}"
        )
    else:
        logger.info(
            f"Quality gates {'passed' if overall_pass else 'failed'}: "
            f"{to_posix_path(md_path.relative_to(REPORTS_DIR.parent))}"
        )
    if not overall_pass:
        failed_checks = [check for check in checks if check.get('enforced', True) and not check.get('passed')]
        primary = failed_checks[0] if failed_checks else None
        failed_names = ", ".join(check.get('name', '?') for check in failed_checks[:3])
        if len(failed_checks) > 3:
            failed_names += f" (+{len(failed_checks) - 3} more)"

        if expected_failure_drill:
            logger.info(
                "[EXPECTED_FAILURE_DRILL] Quality gate red state was intentionally induced by entrypoint failure validation."
            )
        elif primary:
            logger.warning(
                "Quality gate is red; reporting continues by policy. "
                f"Primary cause: {primary.get('name')} "
                f"({primary.get('actual')} {primary.get('operator')} {primary.get('expected')} failed). "
                f"Failed checks: {failed_names}. "
                f"See {to_posix_path(md_path.relative_to(REPORTS_DIR.parent))}."
            )
        else:
            logger.warning(
                "Quality gate is red; reporting continues by policy. "
                f"See {to_posix_path(md_path.relative_to(REPORTS_DIR.parent))}."
            )


if __name__ == "__main__":
    run_quality_gates()
