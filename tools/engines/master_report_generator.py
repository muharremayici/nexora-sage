import json
import logging
from collections import Counter

from tools.core.artifact_contracts import MASTER_REPORT_PATH
from tools.core.config import RAW_DIR, REPORTS_DIR, DOCTRINE, save_text_atomic
from tools.core.json_io import load_json_file
from tools.core.projects_registry import project_display_name
from tools.core.report_index import list_report_names
from tools.core.workspace_mode import get_workspace_mode
from tools.engines.generate_atlas import AST_CONTRACT_VERSION

logger = logging.getLogger("master_generator")

def section_evidence(filename: str) -> tuple[str, str]:
    default = DOCTRINE.get("master_report_evidence_default", {})
    profiles = DOCTRINE.get("master_report_evidence_profiles", {})
    profile = profiles.get(filename, {}) if isinstance(profiles, dict) else {}
    evidence_type = profile.get("type") or default.get("type") or "heuristic"
    trust = profile.get("trust") or default.get("trust") or "medium"
    return str(evidence_type), str(trust)


def build_sections():
    sections = [
        (sec["title"], sec["file"])
        for sec in DOCTRINE.get("master_report_sections", [])
    ]

    diff_files = list_report_names("nanometric_diff_*.md")
    for idx, filename in enumerate(diff_files, start=1):
        title = filename.replace("nanometric_diff_", "").replace(".md", "")
        sections.append((f"14.{idx} AST Structural Diff ({title})", filename))

    for title, filename in [
        ("24 Live Surface Findings", "live_surface_findings.md"),
        ("25 Live Surface Priority Pack", "live_surface_priority_pack.md"),
        ("26 Adapter Registry", "adapter_registry.md"),
        ("27 Distribution Hardening", "distribution_hardening_validation.md"),
        ("28 Entrypoint Failure Drills", "entrypoint_failure_validation.md"),
        ("29 Operational Parity", "operational_parity_validation.md"),
        ("30 Performance Budget", "performance_budget_validation.md"),
        ("31 Performance Ledger", "performance_ledger.md"),
        ("32 Release Readiness", "release_readiness.md"),
        ("33 Framework Routes", "framework_routes.md"),
        ("34 UI Runtime Contracts", "ui_runtime_contracts.md"),
        ("35 UI Smoke Spec Templates", "ui_smoke_specs.md"),
        ("36 UI Smoke Execution Readiness", "ui_smoke_execution.md"),
        ("37 Next Boundary Analysis", "next_boundary_analysis.md"),
        ("38 State/Data Graph", "state_data_graph.md"),
        ("39 A11y i18n Contracts", "a11y_i18n_contracts.md"),
        ("40 React Ecosystem Surgical Analysis", "react_ecosystem_analysis.md"),
        ("41 React Runtime Intelligence", "react_runtime_intelligence.md"),
        ("42 React Compiler Readiness", "react_compiler_readiness.md"),
        ("43 React Frontier Intelligence", "react_frontier_intelligence.md"),
        ("44 Merge Dependency Packages", "merge_dependency_packages.md"),
        ("45 Merge Simulation", "merge_simulation.md"),
        ("46 Merge Decision Cockpit", "merge_decision_cockpit.md"),
        ("47 AI Task Packs", "ai_task_packs.md"),
        ("48 Merge Intelligence Regression", "merge_intelligence_regression.md"),
        ("49 Optional Feature Smokes", "optional_feature_smokes.md"),
    ]:
        if (title, filename) not in sections:
            sections.append((title, filename))

    return sections


def render_keyword_heatmap(master_content):
    master_content.append("## Appendix A: Semantic Keyword Heatmap")
    kw_path = RAW_DIR / "keyword_scanner_all.json"
    try:
        kw_data = load_json_file(kw_path, {})
        keywords_section = kw_data.get("keywords", [])
        if not keywords_section:
            master_content.append("*(No keyword data available)*")
            master_content.append("\n---\n")
            return

        cat_counter = Counter()
        for entry in keywords_section:
            for category in entry.get("found", []):
                cat_counter[category] += 1

        master_content.append("| Category | Files Hit |")
        master_content.append("|---|---:|")
        for category, count in cat_counter.most_common():
            master_content.append(f"| `{category}` | {count} |")
    except Exception as exc:
        master_content.append(f"*(Error reading keyword data: {exc})*")

    master_content.append("\n---\n")


def render_coupling_appendix(master_content):
    master_content.append("## Appendix B: Top 20 High-Coupling Risk Files")
    try:
        surgical = load_json_file(RAW_DIR / "surgical_discovery.json", [])
        sorted_items = sorted(surgical, key=lambda item: item.get("coupling", 0), reverse=True)[:20]
        if not sorted_items:
            master_content.append("*(No coupling data available)*")
            master_content.append("\n---\n")
            return

        master_content.append("| Project | File | Symbol | Coupling | Decoupling% |")
        master_content.append("|---|---|---|---:|---:|")
        for item in sorted_items:
            master_content.append(
                f"| {item.get('project', '?')} | `{item.get('file', '?')}` | "
                f"`{item.get('name', '?')}` | {item.get('coupling', 0)} | {item.get('decoupling', 0)}% |"
            )
    except Exception as exc:
        master_content.append(f"*(Error reading surgical data: {exc})*")

    master_content.append("\n---\n")


def render_structural_contract_appendix(master_content):
    master_content.append("## Appendix C: Structural Contract Health")
    gate_path = RAW_DIR / "quality_gate.json"
    try:
        gate = load_json_file(gate_path, {})
        checks = {
            item.get("name"): item
            for item in gate.get("checks", [])
            if isinstance(item, dict) and item.get("name")
        }
        rows = [
            ("Quality Gate", "PASS" if gate.get("passed") else "FAIL", "overall pipeline enforcement"),
            ("Atlas Contract Coverage", checks.get("min_atlas_contract_file_ratio", {}).get("actual", "?"), "all atlas files carry contract metadata"),
            ("Member Detail Coverage", checks.get("min_member_detail_contract_ratio", {}).get("actual", "?"), "member details carry the rich AST nested contract"),
            ("Genome Occurrence Coverage", checks.get("min_genome_occurrence_contract_ratio", {}).get("actual", "?"), "member-bearing genome occurrences carry nanometric fields"),
            ("Atlas Current Version", checks.get("min_atlas_current_version_ratio", {}).get("actual", "?"), AST_CONTRACT_VERSION),
            ("Genome Current Version", checks.get("min_genome_current_version_ratio", {}).get("actual", "?"), AST_CONTRACT_VERSION),
        ]

        master_content.append("| Signal | Value | Target |")
        master_content.append("|---|---:|---|")
        for signal, value, target in rows:
            master_content.append(f"| {signal} | {value} | `{target}` |")
    except Exception as exc:
        master_content.append(f"*(Error reading structural contract health: {exc})*")

    master_content.append("\n---\n")


def render_remediation_backlog_appendix(master_content):
    master_content.append("## Appendix D: Audit Remediation Backlog")
    audit_path = RAW_DIR / "audit_report.json"
    try:
        audit = load_json_file(audit_path, {})
        backlog = audit.get("summary", {}).get("remediation_backlog", [])
        if not backlog:
            master_content.append("*(No remediation backlog available)*")
            master_content.append("\n---\n")
            return

        master_content.append("| Priority | Rule | Count | Wave | Suggested Action | Hotspots |")
        master_content.append("|---|---|---:|---|---|---|")
        for item in backlog[:10]:
            hotspots = ", ".join(f"{entry['path']} ({entry['count']})" for entry in item.get("top_buckets", [])[:3]) or "-"
            master_content.append(
                f"| `{item.get('priority', '?')}` | {item.get('label', item.get('rule', '?'))} | {item.get('count', 0)} | "
                f"`{item.get('wave', '?')}` | {item.get('suggested_action', '-')} | {hotspots} |"
            )
    except Exception as exc:
        master_content.append(f"*(Error reading remediation backlog: {exc})*")

    master_content.append("\n---\n")


def render_project_audit_totals_appendix(master_content):
    master_content.append("## Appendix E: Project Audit Totals")
    audit_path = RAW_DIR / "audit_report.json"
    try:
        audit = load_json_file(audit_path, {})
        summary = audit.get("summary", {}) if isinstance(audit, dict) else {}
        by_project = summary.get("by_project", {}) if isinstance(summary, dict) else {}
        by_project_rule = summary.get("by_project_rule", {}) if isinstance(summary, dict) else {}
        if not by_project:
            master_content.append("*(No project audit totals available)*")
            master_content.append("\n---\n")
            return

        master_content.append("| Project | Violations | Top Rule Mix |")
        master_content.append("|---|---:|---|")
        for project, count in sorted(by_project.items(), key=lambda pair: (-pair[1], pair[0])):
            rule_mix = by_project_rule.get(project, {})
            top_rules = ", ".join(
                f"{rule}={value}" for rule, value in sorted(rule_mix.items(), key=lambda pair: (-pair[1], pair[0]))[:3]
            ) or "-"
            project_label = f"{project_display_name(project)} [{project}]"
            master_content.append(f"| `{project_label}` | {count} | `{top_rules}` |")
    except Exception as exc:
        master_content.append(f"*(Error reading project audit totals: {exc})*")

    master_content.append("\n---\n")


def render_workspace_mode_appendix(master_content):
    master_content.append("## Appendix F: Workspace Mode")
    mode = get_workspace_mode()
    master_content.append("| Signal | Value |")
    master_content.append("|---|---|")
    master_content.append(f"| Mode | `{mode.get('mode')}` |")
    master_content.append(f"| Project Count | {mode.get('project_count')} |")
    master_content.append(f"| Comparative Enabled | {'YES' if mode.get('comparative_enabled') else 'NO'} |")
    master_content.append(f"| Projects | `{', '.join(mode.get('projects', []))}` |")
    master_content.append("\n---\n")


def _load_raw(name: str) -> dict:
    path = RAW_DIR / name
    if not path.exists():
        logger.warning("Master report raw input missing: %s", name)
        return {"_missing": True, "_artifact": name}
    try:
        payload = load_json_file(path, {})
        return payload if isinstance(payload, dict) else {}
    except Exception as exc:
        logger.warning("Master report raw input unreadable: %s (%s)", name, exc)
        return {"_load_error": str(exc), "_artifact": name}


def render_executive_triage(master_content):
    release = _load_raw("release_readiness.json")
    quality = _load_raw("quality_gate.json")
    live = _load_raw("live_surface_findings.json")
    dead = _load_raw("dead_code.json")
    ui_smoke = _load_raw("ui_smoke_execution.json")
    performance = _load_raw("performance_budget_validation.json")

    release_summary = release.get("summary", {}) if isinstance(release.get("summary"), dict) else {}
    live_summary = live.get("summary", {}) if isinstance(live.get("summary"), dict) else {}
    dead_summary = dead.get("summary", {}) if isinstance(dead.get("summary"), dict) else {}
    ui_summary = ui_smoke.get("summary", {}) if isinstance(ui_smoke.get("summary"), dict) else {}
    perf_summary = performance.get("summary", {}) if isinstance(performance.get("summary"), dict) else {}

    master_content.extend(
        [
            "## Executive Triage",
            "",
            "| Question | Answer | Evidence |",
            "|---|---|---|",
            f"| Platform release-ready? | `{release_summary.get('platform_readiness', release.get('readiness', 'UNKNOWN'))}` | release checks `{release_summary.get('passed', '?')}/{release_summary.get('checks', '?')}` |",
            f"| Repo ecosystem attention? | `{release_summary.get('ecosystem_attention', False)}` | quality signal `{release_summary.get('ecosystem_signal_status', quality.get('ecosystem_signal_status', 'n/a'))}` |",
            f"| Repository analysis scope | `{quality.get('scope_gate_status', 'INCOMPLETE_EVIDENCE')}` | authority `{(quality.get('analysis_scope_authority') or {}).get('scope_authority_id', 'n/a')}`; claim `{(quality.get('analysis_scope_authority') or {}).get('claim_scope', 'n/a')}` |",
            f"| Live-surface critical signals | `{(live_summary.get('risk_tiers') or {}).get('critical', 0)}` | broken `{live_summary.get('broken_live', 0)}`, duplicate `{live_summary.get('duplicate_live', 0)}` |",
            f"| Dead-code actionability | `{(dead_summary.get('actionability') or {}).get('actionable', 0)}` actionable | total `{dead_summary.get('total', 0)}` |",
            f"| Browser smoke readiness | `{ui_summary.get('ready_to_run', 0)}` ready | required `{ui_summary.get('browser_gate_required', 0)}`, next `{ui_summary.get('next_action', 'n/a')}` |",
            f"| Performance budget | `{perf_summary.get('passed_checks', 0)}/{perf_summary.get('total_checks', 0)}` | rolling policy included in performance artifact |",
            "",
            "> Read this table first: it separates Nexora SAGE platform readiness from repository-level work still detected by the analysis.",
            "---",
            "",
        ]
    )


def generate_master_report():
    logger.info("Compiling MASTER_ARCHITECTURE_REPORT.md...")

    sections = build_sections()
    workspace_mode = get_workspace_mode()
    host_projects = workspace_mode.get("host_projects", []) or []
    variant_projects = workspace_mode.get("variant_projects", []) or []
    companion_projects = workspace_mode.get("companion_projects", []) or []
    master_content = [
        "# NEXORA SAGE MASTER ARCHITECTURE REPORT",
        "> This report is auto-generated by the Nexora SAGE Pipeline.",
        "",
        "## Evidence Trust Model",
        "| Label | Meaning |",
        "|---|---|",
        "| `mechanical / high` | Derived from direct parsing, graphing, or deterministic counting. Highest trust for enforcement and regression gating. |",
        "| `heuristic / medium` | Derived from scoring, inference, or mapping heuristics. Strong for prioritization, but not final merge authority alone. |",
        "",
        "## Scope Model",
        "| Signal | Value |",
        "|---|---|",
        f"| Workspace Mode | `{workspace_mode.get('mode')}` |",
        f"| Comparative Enabled | {'YES' if workspace_mode.get('comparative_enabled') else 'NO'} |",
        f"| Host Projects | `{', '.join(f'{project_display_name(p)} [{p}]' for p in host_projects) or '-'}` |",
        f"| Variant Projects | `{', '.join(f'{project_display_name(p)} [{p}]' for p in variant_projects) or '-'}` |",
        f"| Companion Projects | `{', '.join(f'{project_display_name(p)} [{p}]' for p in companion_projects) or '-'}` |",
        "",
        "> Interpretation: project-level truth is primary. Workspace-wide summaries are secondary and should only be read as ecosystem context.",
        "---",
        "",
    ]
    render_executive_triage(master_content)

    for title, filename in sections:
        file_path = REPORTS_DIR / filename
        evidence_type, trust = section_evidence(filename)
        master_content.append(f"## {title}")
        master_content.append(f"> Evidence: `{evidence_type}` | Trust: `{trust}`")

        if file_path.exists():
            content = file_path.read_text("utf-8")
            if filename.endswith(".txt"):
                master_content.append(f"```text\n{content}\n```")
            else:
                master_content.append(content)
        else:
            master_content.append(f"*(No data generated for {filename})*")

        master_content.append("---\n")

    render_keyword_heatmap(master_content)
    render_coupling_appendix(master_content)
    render_structural_contract_appendix(master_content)
    render_remediation_backlog_appendix(master_content)
    render_project_audit_totals_appendix(master_content)
    render_workspace_mode_appendix(master_content)

    out_file = MASTER_REPORT_PATH
    save_text_atomic(out_file, "\n".join(master_content))
    logger.info(f"Master report compiled: {out_file.name}")


if __name__ == "__main__":
    generate_master_report()
