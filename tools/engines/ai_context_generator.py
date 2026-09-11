"""
AI Context Generator v2.0 - produces a compact JSON index of the entire codebase.

This global index helps an AI assistant orient itself without reading thousands
of lines. It is not a surgical mutation packet; target-repository edits should
use ContextOS/MCP bounded packet surfaces.

v2.0 enrichments:
  - Quality Gate result
  - Studio Intelligence per-studio merge summary
  - Hexagonal binding status
  - Clone Pressure duplication hotspots
  - State Flow map
  - Merge Overview
  - Pipeline Meta
  - Audit by Module breakdown
  - Report Index
  - AI Action Hints
  - SAGE AI/HITL brief and agent contract
"""
import json
import os
import time
from collections import Counter, defaultdict
from pathlib import Path
from tools.core.audit_report import get_rule_counts, get_total_violations, get_violations, get_rule_taxonomy
from tools.core.config import CONFIG_DIR, RAW_DIR, REPORTS_DIR, SNAPSHOTS_DIR, DYNAMIC_CONFIG, save_json_atomic
from tools.core.capability_registry import build_agent_capability_map, load_capability_registry
from tools.core.reality_scope import TARGET_REPOSITORY_PROJECTION_ID, projection_capability_scopes
from tools.core.host_intelligence import get_host_studios
from tools.core.atlas_io import load_atlas_data
from tools.core.genome_io import load_genome_data
from tools.core.json_io import load_json_file
from tools.core.fractal_io import load_fractal_map_data
from tools.core.logger import logger
from tools.core.projects_registry import project_display_name
from tools.core.workspace_mode import get_workspace_mode


def _ai_context_generator_policy() -> dict:
    policy = load_json_file(CONFIG_DIR / "pipeline_execution_policy.json", {})
    if not isinstance(policy, dict):
        return {}
    surface_policy = policy.get("ai_context_generator_policy", {})
    return surface_policy if isinstance(surface_policy, dict) else {}


def _load_product_governance_artifact(name: str, *, enabled: bool) -> dict:
    surface_policy = _ai_context_generator_policy()
    enrichment_policy = surface_policy.get("product_governance_enrichment", {})
    declared_artifacts = {
        str(item)
        for item in (
            enrichment_policy.get("excluded_artifacts", [])
            if isinstance(enrichment_policy, dict)
            else []
        )
    }
    if name not in declared_artifacts:
        return {}
    if not enabled:
        return {}
    payload = load_json_file(RAW_DIR / name)
    return payload if isinstance(payload, dict) else {}


def generate_ai_context(*, include_sage_product_governance: bool = False):
    logger.info("Generating AI context index v2.0...")

    surface_policy = _ai_context_generator_policy()
    enrichment_policy = surface_policy.get("product_governance_enrichment", {})
    excluded_product_artifacts = {
        str(item)
        for item in (
            enrichment_policy.get("excluded_artifacts", [])
            if isinstance(enrichment_policy, dict)
            else []
        )
    }
    ctx = {
        "_meta": {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "purpose": "Compact codebase index for AI assistant consumption",
            "version": "2.0",
            "system_scope": "SAGE_ON_SAGE" if include_sage_product_governance else "SAGE_ON_REPOSITORY",
            "product_governance_enrichment": (
                "included_explicitly"
                if include_sage_product_governance
                else "excluded_by_policy"
            ),
            "excluded_product_artifacts": (
                []
                if include_sage_product_governance
                else sorted(excluded_product_artifacts)
            ),
        },
    }
    ctx["workspace_mode"] = get_workspace_mode()
    ctx["capabilities"] = {
        "bundler": ((DYNAMIC_CONFIG.get("environment") or {}).get("bundler") or "unknown"),
        "path_aliases": sorted(((DYNAMIC_CONFIG.get("environment") or {}).get("path_aliases") or {}).keys()),
        "plugins": sorted(DYNAMIC_CONFIG.get("plugins") or []),
    }
    capability_map = build_agent_capability_map(
        load_capability_registry(),
        allowed_system_scopes=projection_capability_scopes(TARGET_REPOSITORY_PROJECTION_ID),
    )
    if capability_map.get("summary"):
        ctx["capability_registry"] = {
            "summary": capability_map.get("summary", {}),
            "artifact_to_capabilities": capability_map.get("artifact_to_capabilities", {}),
            "source": "config/capability_registry.json",
        }

    # ---------------------------------------------------------------------------
    # ORIGINAL SECTIONS (1-10)
    # ---------------------------------------------------------------------------

    # 1. Health Score
    hs = load_json_file(RAW_DIR / "health_score.json")
    if hs:
        ctx["health"] = {
            "overall": hs.get("overall", 0),
            "grade": hs.get("grade", "?"),
            "breakdown": hs.get("breakdown", {}),
        }
        project_details = hs.get("project_details", {}) or {}
        if project_details:
            ctx["health_by_project"] = {
                project: {
                    "display_name": project_display_name(project),
                    "overall": details.get("overall", 0),
                    "grade": details.get("grade", "?"),
                    "breakdown": details.get("breakdown", {}),
                }
                for project, details in project_details.items()
                if isinstance(details, dict)
            }

    # 2. Project Overview (from landscape_map.json)
    landscape = load_json_file(RAW_DIR / "landscape_map.json")
    if landscape:
        hierarchy = landscape.get("hierarchy", {})
        ctx["projects"] = {}
        for pkey, dirs_dict in hierarchy.items():
            dir_count = len(dirs_dict)
            file_count = sum(len(files) for files in dirs_dict.values())
            ctx["projects"][pkey] = {"directories": dir_count, "files": file_count}

    # 3. Module Risk Summary (top 15)
    risk = load_json_file(RAW_DIR / "module_risk_matrix.json")
    risk_rows = risk.get("rows", []) if isinstance(risk, dict) else risk
    if isinstance(risk_rows, list):
        ctx["module_risks"] = [
            {
                "module": r["module"],
                "risk": r["risk_level"].replace("🔴 ", "").replace("🟡 ", "").replace("🟢 ", ""),
                "score": r["risk_score"],
                "files": r["files"],
                "dead_code": r["dead_code"],
                "coupling": r["avg_coupling"],
            }
            for r in risk_rows[:15]
        ]

    # 4. Audit Summary
    audit_categories = get_rule_counts()
    audit_total = get_total_violations()
    if audit_categories or audit_total:
        ctx["audit"] = {
            "total_violations": audit_total,
            "categories": audit_categories,
            "rule_taxonomy": get_rule_taxonomy(),
        }

    # 5. Dead Code Hotspots (top 15 files with most dead exports)
    dead_payload = load_json_file(RAW_DIR / "dead_code.json")
    dead = dead_payload.get("items", []) if isinstance(dead_payload, dict) else dead_payload
    dead_total = 0
    if isinstance(dead, list):
        dead_total = len(dead)
        file_counter = Counter(item.get("file", "?") for item in dead)
        ctx["dead_code"] = {
            "total": dead_total,
            "hotspots": [
                {"file": f, "dead_exports": c}
                for f, c in file_counter.most_common(15)
            ]
        }

    # 6. Circular Dependencies
    circ = load_json_file(RAW_DIR / "circular_deps.json")
    circ_count = 0
    if circ:
        circ_count = circ.get("cycles_found", 0)
        ctx["circular_deps"] = {
            "count": circ_count,
            "total_edges": circ.get("total_edges", 0),
            "cycles": [
                " → ".join(c.get("chain", []))
                for c in circ.get("cycles", [])[:10]
            ],
        }

    # 7. Architectural Markers Summary (from landscape)
    if landscape:
        arch = landscape.get("architectural", {})
        markers = {}
        for marker, items in arch.items():
            if items:
                markers[marker] = len(items)
        ctx["architecture_markers"] = markers

    # 8. Top Semantic Concepts (from landscape concepts)
    if landscape:
        concepts = landscape.get("concepts", {})
        concept_summary = {}
        for pkey, cats in concepts.items():
            for cat, hits in cats.items():
                concept_summary[cat] = concept_summary.get(cat, 0) + len(hits)
        sorted_concepts = sorted(concept_summary.items(), key=lambda x: x[1], reverse=True)[:10]
        ctx["top_concepts"] = {k: v for k, v in sorted_concepts}

    # 9. Feature modules list (from atlas)
    atlas = load_atlas_data()
    if atlas and isinstance(atlas, dict):
        main_atlas = atlas.get("MAIN", {})
        features = main_atlas.get("features", {})
        if features:
            ctx["features"] = {
                feat: len(data.get("files", []) if isinstance(data, dict) else [])
                for feat, data in sorted(features.items())
            }

    # 10. Temporal trend (if snapshot exists)
    diff = load_json_file(RAW_DIR / "temporal_diff.json")
    if diff and diff.get("status") == "compared":
        changes = diff.get("changes", [])
        ctx["trend"] = {
            c["metric"]: {
                "delta": c["delta"],
                "status": c["status"]
            }
            for c in changes
            if c["delta"] != 0
        }

    # ---------------------------------------------------------------------------
    # TIER 1: CRITICAL ENRICHMENTS (11-14)
    # ---------------------------------------------------------------------------

    # 11. Quality Gate Result
    qg = load_json_file(RAW_DIR / "quality_gate.json")
    if qg:
        checks_summary = {}
        for check in qg.get("checks", []):
            checks_summary[check["name"]] = {
                "actual": check.get("actual"),
                "expected": check.get("expected"),
                "passed": check.get("passed", False),
            }
        ctx["quality_gate"] = {
            "passed": qg.get("passed", False),
            "checks": checks_summary,
        }
    qr = load_json_file(RAW_DIR / "quality_review.json")
    if qr:
        ctx["quality_review"] = {
            "summary": qr.get("summary", {}),
            "signals": qr.get("signals", {}),
        }
    proof = load_json_file(RAW_DIR / "proof_obligations.json")
    if proof:
        ctx["proof_obligations"] = {
            "summary": proof.get("summary", {}),
            "failed_required_ids": proof.get("failed_required_ids", []),
        }

    react_probe = load_json_file(RAW_DIR / "react_capability_probe.json")
    react_support = load_json_file(RAW_DIR / "react_support_matrix.json")
    if react_support:
        ctx["react_support"] = {
            "workspace_summary": react_support.get("summary", {}),
            "by_project": {
                project: {
                    "role": payload.get("role", "unknown"),
                    "display_name": payload.get("display_name", project_display_name(project)),
                    "summary": payload.get("summary", {}),
                }
                for project, payload in (react_support.get("by_project", {}) or {}).items()
                if isinstance(payload, dict)
            },
        }
    if react_probe:
        ctx["react_capability_probe"] = {
            "workspace_summary": react_probe.get("summary", {}),
            "by_project": {
                project: {
                    "role": payload.get("role", "unknown"),
                    "display_name": payload.get("display_name", project_display_name(project)),
                    "summary": payload.get("summary", {}),
                }
                for project, payload in (react_probe.get("by_project", {}) or {}).items()
                if isinstance(payload, dict)
            },
        }

    # 12. Studio Intelligence (from host_merge_intelligence.json)
    host_studios = get_host_studios()
    if host_studios:
        studios_ctx = {}
        for studio_key, studio_data in host_studios.items():
            summary = studio_data.get("host_summary", {})
            merge = studio_data.get("merge_summary", {})
            policy_counts = summary.get("policy_counts", {})
            top_donors = merge.get("top_donors", [])
            studios_ctx[studio_key] = {
                "module": studio_data.get("module", ""),
                "files": summary.get("file_count", 0),
                "protected": policy_counts.get("host_locked", 0),
                "compose_preferred": policy_counts.get("compose_preferred", 0),
                "manual_only": policy_counts.get("manual_only", 0),
                "replace_allowed": policy_counts.get("replace_allowed", 0),
                "merge_candidates": merge.get("decision_count", 0),
                "top_donors": [
                    {"source": d["source"], "count": d["count"]}
                    for d in top_donors[:3]
                ],
            }
        ctx["studios"] = studios_ctx

    # 13. Hexagonal Binding Status
    hex_data = load_json_file(RAW_DIR / "hexagonal_bindings.json")
    if hex_data:
        bound_list = hex_data.get("bound", [])
        unbound_list = hex_data.get("unbound_ports", [])
        ctx["hexagonal"] = {
            "total_ports": hex_data.get("total_ports_discovered", 0),
            "total_adapters": hex_data.get("total_adapters_discovered", 0),
            "bound": len(bound_list),
            "unbound": [
                {"port": u.get("port", "?"), "file": u.get("file", "?")}
                for u in unbound_list
            ],
        }

    # 14. Clone Pressure
    clones = load_json_file(RAW_DIR / "clone_detector.json")
    if clones:
        metrics = clones.get("metrics", {})
        clusters = clones.get("clusters", [])
        top_clones = []
        for c in clusters[:10]:
            instances = c.get("instances", [])
            projects = list({
                inst.get("id", "").split("::")[0]
                for inst in instances
                if "::" in inst.get("id", "")
            })
            symbols = [
                inst.get("id", "").split("::")[-1]
                for inst in instances
                if "::" in inst.get("id", "")
            ]
            top_clones.append({
                "lines": c.get("lines", 0),
                "symbol": symbols[0] if symbols else "?",
                "instances": len(instances),
                "projects": projects,
            })
        ctx["clone_pressure"] = {
            "total_clusters": metrics.get("clone_clusters", 0),
            "total_wasted_lines": metrics.get("wasted_lines_due_to_clones", 0),
            "max_clone_lines": metrics.get("max_clone_lines", 0),
            "top_clones": top_clones,
        }

    live_surface = load_json_file(RAW_DIR / "live_surface_findings.json")
    if live_surface:
        live_summary = live_surface.get("summary", {}) if isinstance(live_surface, dict) else {}
        findings = live_surface.get("findings", []) if isinstance(live_surface, dict) else []
        ctx["live_surface"] = {
            "summary": live_summary,
            "top_findings": [
                {
                    "project": item.get("project"),
                    "file": item.get("file"),
                    "classification": item.get("classification"),
                    "risk_tier": item.get("risk_tier"),
                    "related_files": item.get("related_files", []),
                }
                for item in findings[:10]
                if isinstance(item, dict)
            ],
        }

    framework_routes = load_json_file(RAW_DIR / "framework_routes.json")
    if framework_routes:
        routes = framework_routes.get("routes", []) if isinstance(framework_routes, dict) else []
        ctx["framework_routes"] = {
            "summary": framework_routes.get("summary", {}) if isinstance(framework_routes, dict) else {},
            "top_routes": [
                {
                    "project": item.get("project"),
                    "framework": item.get("framework"),
                    "route": item.get("route"),
                    "smoke_path": item.get("smoke_path"),
                    "file": item.get("file"),
                }
                for item in routes[:20]
                if isinstance(item, dict)
            ],
        }

    ui_runtime = load_json_file(RAW_DIR / "ui_runtime_contracts.json")
    if ui_runtime:
        ui_summary = ui_runtime.get("summary", {}) if isinstance(ui_runtime, dict) else {}
        merge_candidates = ui_runtime.get("merge_candidates", []) if isinstance(ui_runtime, dict) else []
        smoke_required = [
            item for item in merge_candidates
            if isinstance(item, dict) and item.get("recommended_gate") == "browser_smoke_required"
        ]
        high_risk_candidates = [
            item for item in merge_candidates
            if isinstance(item, dict) and item.get("risk_tier") == "high"
        ]
        ctx["ui_runtime_contracts"] = {
            "summary": ui_summary,
            "browser_smoke_required": len(smoke_required),
            "high_risk_merge_candidates": len(high_risk_candidates),
            "top_merge_gates": [
                {
                    "name": item.get("name"),
                    "source": item.get("source"),
                    "target_path": item.get("target_path"),
                    "risk_tier": item.get("risk_tier"),
                    "recommended_gate": item.get("recommended_gate"),
                    "contract_origin": item.get("contract_origin"),
                    "source_contract_file": item.get("source_contract_file"),
                    "required_contracts": (item.get("dependency_closure_plan") or {}).get("required_contracts", []),
                    "suggested_route": (item.get("smoke_plan") or {}).get("suggested_route"),
                }
                for item in sorted(
                    merge_candidates,
                    key=lambda entry: (entry.get("risk_points", 0), entry.get("name") or ""),
                    reverse=True,
                )[:12]
                if isinstance(item, dict)
            ],
        }

    ui_smoke_specs = load_json_file(RAW_DIR / "ui_smoke_specs.json")
    if ui_smoke_specs:
        specs = ui_smoke_specs.get("specs", []) if isinstance(ui_smoke_specs, dict) else []
        ctx["ui_smoke_specs"] = {
            "summary": ui_smoke_specs.get("summary", {}) if isinstance(ui_smoke_specs, dict) else {},
            "top_specs": [
                {
                    "candidate": item.get("candidate"),
                    "source": item.get("source"),
                    "risk_tier": item.get("risk_tier"),
                    "suggested_route": item.get("suggested_route"),
                    "spec_path": item.get("spec_path"),
                    "required_contracts": item.get("required_contracts", []),
                }
                for item in specs[:12]
                if isinstance(item, dict)
            ],
        }

    ui_smoke_execution = load_json_file(RAW_DIR / "ui_smoke_execution.json")
    if ui_smoke_execution:
        ctx["ui_smoke_execution"] = {
            "summary": ui_smoke_execution.get("summary", {}) if isinstance(ui_smoke_execution, dict) else {},
            "top_runs": [
                {
                    "name": item.get("name"),
                    "route": item.get("route"),
                    "status": item.get("status"),
                    "reason": item.get("reason"),
                }
                for item in (ui_smoke_execution.get("runs", []) if isinstance(ui_smoke_execution, dict) else [])[:12]
                if isinstance(item, dict)
            ],
        }

    next_boundary = load_json_file(RAW_DIR / "next_boundary_analysis.json")
    if next_boundary:
        ctx["next_boundary_analysis"] = {
            "summary": next_boundary.get("summary", {}) if isinstance(next_boundary, dict) else {},
            "top_risks": [
                {
                    "project": item.get("project"),
                    "file": item.get("file"),
                    "risk_tier": item.get("risk_tier"),
                    "signals": item.get("signals", []),
                    "risks": item.get("risks", []),
                }
                for item in (next_boundary.get("files", []) if isinstance(next_boundary, dict) else [])
                if isinstance(item, dict) and item.get("risks")
            ][:12],
        }

    state_data_graph = load_json_file(RAW_DIR / "state_data_graph.json")
    if state_data_graph:
        ctx["state_data_graph"] = {
            "summary": state_data_graph.get("summary", {}) if isinstance(state_data_graph, dict) else {},
            "sample_edges": (state_data_graph.get("edges", []) if isinstance(state_data_graph, dict) else [])[:12],
        }

    a11y_i18n = load_json_file(RAW_DIR / "a11y_i18n_contracts.json")
    if a11y_i18n:
        ctx["a11y_i18n_contracts"] = {
            "summary": a11y_i18n.get("summary", {}) if isinstance(a11y_i18n, dict) else {},
            "top_risks": [
                {
                    "project": item.get("project"),
                    "file": item.get("file"),
                    "risk_tier": item.get("risk_tier"),
                    "signals": item.get("signals", []),
                    "risks": item.get("risks", []),
                }
                for item in (a11y_i18n.get("files", []) if isinstance(a11y_i18n, dict) else [])
                if isinstance(item, dict) and item.get("risks")
            ][:12],
        }

    react_ecosystem = load_json_file(RAW_DIR / "react_ecosystem_analysis.json")
    if react_ecosystem:
        findings = react_ecosystem.get("findings", []) if isinstance(react_ecosystem, dict) else []
        merge_intelligence = react_ecosystem.get("merge_intelligence", []) if isinstance(react_ecosystem, dict) else []
        ctx["react_ecosystem_analysis"] = {
            "summary": react_ecosystem.get("summary", {}) if isinstance(react_ecosystem, dict) else {},
            "top_findings": [
                {
                    "project": item.get("project"),
                    "file": item.get("file"),
                    "dimension": item.get("dimension"),
                    "risk_tier": item.get("risk_tier"),
                    "risk": item.get("risk"),
                    "evidence": item.get("evidence"),
                    "recommended_action": item.get("recommended_action"),
                }
                for item in findings[:12]
                if isinstance(item, dict)
            ],
            "top_merge_gates": [
                {
                    "source": item.get("source"),
                    "file": item.get("file"),
                    "risk_score": item.get("risk_score"),
                    "recommendation": item.get("recommendation"),
                    "required_gates": item.get("required_gates", []),
                }
                for item in merge_intelligence[:12]
                if isinstance(item, dict)
            ],
        }

    react_runtime = load_json_file(RAW_DIR / "react_runtime_intelligence.json")
    if react_runtime:
        findings = react_runtime.get("findings", []) if isinstance(react_runtime, dict) else []
        calibration = react_runtime.get("calibration", {}) if isinstance(react_runtime, dict) else {}
        ctx["react_runtime_intelligence"] = {
            "summary": react_runtime.get("summary", {}) if isinstance(react_runtime, dict) else {},
            "calibration": {
                "lane_counts": calibration.get("lane_counts", {}) if isinstance(calibration, dict) else {},
                "top_act_now": calibration.get("top_act_now", [])[:8] if isinstance(calibration, dict) else [],
                "top_runtime_probe": calibration.get("top_runtime_probe", [])[:8] if isinstance(calibration, dict) else [],
            },
            "top_findings": [
                {
                    "project": item.get("project"),
                    "file": item.get("file"),
                    "dimension": item.get("dimension"),
                    "risk_tier": item.get("risk_tier"),
                    "confidence": item.get("confidence"),
                    "calibration_lane": item.get("calibration_lane"),
                    "false_positive_risk": item.get("false_positive_risk"),
                    "risk": item.get("risk"),
                    "evidence": item.get("evidence"),
                    "recommended_action": item.get("recommended_action"),
                }
                for item in findings[:12]
                if isinstance(item, dict)
            ],
        }

    react_frontier = load_json_file(RAW_DIR / "react_frontier_intelligence.json")
    if react_frontier:
        findings = react_frontier.get("findings", []) if isinstance(react_frontier, dict) else []
        refactor_plan = react_frontier.get("refactor_plan", {}) if isinstance(react_frontier, dict) else {}
        ctx["react_frontier_intelligence"] = {
            "summary": react_frontier.get("summary", {}) if isinstance(react_frontier, dict) else {},
            "evidence_imports": react_frontier.get("evidence_imports", {}) if isinstance(react_frontier, dict) else {},
            "top_findings": [
                {
                    "project": item.get("project"),
                    "file": item.get("file"),
                    "dimension": item.get("dimension"),
                    "risk_tier": item.get("risk_tier"),
                    "confidence": item.get("confidence"),
                    "risk": item.get("risk"),
                    "evidence": item.get("evidence"),
                    "recommended_action": item.get("recommended_action"),
                }
                for item in findings[:12]
                if isinstance(item, dict)
            ],
            "refactor_tasks": (refactor_plan.get("tasks", []) if isinstance(refactor_plan, dict) else [])[:12],
        }

    merge_packages = load_json_file(RAW_DIR / "merge_dependency_packages.json")
    if merge_packages:
        packages = merge_packages.get("packages", []) if isinstance(merge_packages, dict) else []
        ctx["merge_dependency_packages"] = {
            "summary": merge_packages.get("summary", {}) if isinstance(merge_packages, dict) else {},
            "top_packages": [
                {
                    "candidate": item.get("candidate"),
                    "source": item.get("source"),
                    "package_tier": item.get("package_tier"),
                    "closure_size": item.get("closure_size"),
                    "closure_truncated": item.get("closure_truncated"),
                    "unresolved_internal_deps": item.get("unresolved_internal_deps", [])[:12],
                    "external_deps": item.get("external_deps", [])[:12],
                    "target_path": item.get("target_path"),
                    "smoke_route": item.get("smoke_route"),
                }
                for item in packages[:12]
                if isinstance(item, dict)
            ],
        }

    merge_simulation = load_json_file(RAW_DIR / "merge_simulation.json")
    if merge_simulation:
        simulations = merge_simulation.get("simulations", []) if isinstance(merge_simulation, dict) else []
        ctx["merge_simulation"] = {
            "summary": merge_simulation.get("summary", {}) if isinstance(merge_simulation, dict) else {},
            "top_decisions": [
                {
                    "candidate": item.get("candidate"),
                    "source": item.get("source"),
                    "decision": item.get("decision"),
                    "target_path": item.get("target_path"),
                    "signals": item.get("signals", {}),
                    "required_actions": item.get("required_actions", []),
                    "smoke_route": item.get("smoke_route"),
                }
                for item in simulations[:12]
                if isinstance(item, dict)
            ],
        }

    merge_cockpit = load_json_file(RAW_DIR / "merge_decision_cockpit.json")
    if merge_cockpit:
        decisions = merge_cockpit.get("decisions", []) if isinstance(merge_cockpit, dict) else []
        ctx["merge_decision_cockpit"] = {
            "summary": merge_cockpit.get("summary", {}) if isinstance(merge_cockpit, dict) else {},
            "top_decisions": [
                {
                    "candidate": item.get("candidate"),
                    "source": item.get("source"),
                    "action": item.get("action"),
                    "target_path": item.get("target_path"),
                    "route": item.get("route", {}),
                    "reasons": item.get("reasons", []),
                    "required_actions": item.get("required_actions", []),
                }
                for item in decisions[:15]
                if isinstance(item, dict)
            ],
        }

    ai_task_packs = load_json_file(RAW_DIR / "ai_task_packs.json")
    if ai_task_packs:
        taskpacks = ai_task_packs.get("taskpacks", []) if isinstance(ai_task_packs, dict) else []
        ctx["ai_task_packs"] = {
            "summary": ai_task_packs.get("summary", {}) if isinstance(ai_task_packs, dict) else {},
            "top_taskpacks": [
                {
                    "candidate": item.get("candidate"),
                    "source": item.get("source"),
                    "action": item.get("action"),
                    "intent": item.get("intent"),
                    "taskpack_path": item.get("taskpack_path"),
                    "required_actions": item.get("required_actions", []),
                }
                for item in taskpacks[:20]
                if isinstance(item, dict)
            ],
        }

    merge_regression = _load_product_governance_artifact(
        "merge_intelligence_regression.json",
        enabled=include_sage_product_governance,
    )
    if merge_regression:
        ctx["merge_intelligence_regression"] = {
            "summary": merge_regression.get("summary", {}) if isinstance(merge_regression, dict) else {},
            "failed_checks": [
                item
                for item in (merge_regression.get("checks", []) if isinstance(merge_regression, dict) else [])
                if isinstance(item, dict) and not item.get("passed")
            ][:20],
        }

    adapter_registry = _load_product_governance_artifact(
        "adapter_registry.json",
        enabled=include_sage_product_governance,
    )
    if adapter_registry:
        ctx["adapter_registry"] = {
            "summary": adapter_registry.get("summary", {}) if isinstance(adapter_registry, dict) else {},
            "adapters": [
                {
                    "id": item.get("id"),
                    "ecosystem": item.get("ecosystem"),
                    "frameworks": item.get("frameworks"),
                    "capabilities": item.get("capabilities"),
                    "maturity": item.get("maturity"),
                    "valid": item.get("valid"),
                }
                for item in (adapter_registry.get("adapters", []) if isinstance(adapter_registry, dict) else [])
                if isinstance(item, dict)
            ],
        }

    release_readiness = _load_product_governance_artifact(
        "release_readiness.json",
        enabled=include_sage_product_governance,
    )
    if release_readiness:
        ctx["release_readiness"] = {
            "readiness": release_readiness.get("readiness") if isinstance(release_readiness, dict) else None,
            "summary": release_readiness.get("summary", {}) if isinstance(release_readiness, dict) else {},
            "failed_checks": [
                item
                for item in (release_readiness.get("checks", []) if isinstance(release_readiness, dict) else [])
                if isinstance(item, dict) and not item.get("passed")
            ],
        }

    nexora_operator_packet = _load_product_governance_artifact(
        "nexora_operator_packet.json",
        enabled=include_sage_product_governance,
    )
    nexora_brief = _load_product_governance_artifact(
        "nexora_brief.json",
        enabled=include_sage_product_governance,
    )
    nexora_contract = _load_product_governance_artifact(
        "nexora_agent_contract.json",
        enabled=include_sage_product_governance,
    )
    mcp_surface = _load_product_governance_artifact(
        "mcp_agent_surface_validation.json",
        enabled=include_sage_product_governance,
    )
    watchdog_session = _load_product_governance_artifact(
        "watchdog_session.json",
        enabled=include_sage_product_governance,
    )
    if nexora_operator_packet or nexora_brief or nexora_contract or mcp_surface or watchdog_session:
        operator_mission = nexora_operator_packet.get("mission_control", {}) if isinstance(nexora_operator_packet, dict) else {}
        contract_identity = nexora_contract.get("identity", {}) if isinstance(nexora_contract, dict) else {}
        contract_protocol = nexora_contract.get("response_protocol", {}) if isinstance(nexora_contract, dict) else {}
        contract_posture = nexora_contract.get("current_posture", {}) if isinstance(nexora_contract, dict) else {}
        ctx["nexora_agent_surface"] = {
            "role": contract_identity.get("product_role", "AI-native codebase intelligence substrate"),
            "relationship": contract_identity.get(
                "relationship",
                {
                    "nexora": "assistant_to_ai_agent",
                    "ai_agent": "assistant_to_human",
                    "human": "authority_and_approval_owner",
                },
            ),
            "brief_summary": nexora_brief.get("summary", {}) if isinstance(nexora_brief, dict) else {},
            "operator_packet": operator_mission,
            "relevant_capabilities": (
                nexora_operator_packet.get("relevant_capabilities", [])
                if isinstance(nexora_operator_packet, dict)
                else []
            ),
            "current_posture": contract_posture,
            "required_answer_fields": contract_protocol.get("required_fields", []) if isinstance(contract_protocol, dict) else [],
            "approval_gates": [
                {
                    "id": gate.get("id"),
                    "human_approval_required": gate.get("human_approval_required"),
                    "risk": gate.get("risk"),
                    "agent_rule": gate.get("agent_rule"),
                }
                for gate in (nexora_contract.get("approval_gates", []) if isinstance(nexora_contract, dict) else [])
                if isinstance(gate, dict)
            ],
            "mcp_agent_surface": mcp_surface.get("summary", {}) if isinstance(mcp_surface, dict) else {},
            "watchdog_session": watchdog_session.get("summary", {}) if isinstance(watchdog_session, dict) else {},
            "source_artifacts": {
                "brief": "output/.raw/nexora_brief.json",
                "agent_contract": "output/.raw/nexora_agent_contract.json",
                "operator_packet": "output/.raw/nexora_operator_packet.json",
                "mcp_agent_surface": "output/.raw/mcp_agent_surface_validation.json",
                "watchdog_session": "output/.raw/watchdog_session.json",
                "capability_registry": "config/capability_registry.json",
            },
        }

    performance_budget = _load_product_governance_artifact(
        "performance_budget_validation.json",
        enabled=include_sage_product_governance,
    )
    performance_ledger = _load_product_governance_artifact(
        "performance_ledger.json",
        enabled=include_sage_product_governance,
    )
    if performance_budget or performance_ledger:
        ledger_runs = performance_ledger.get("runs", []) if isinstance(performance_ledger, dict) else []
        ctx["performance_readiness"] = {
            "budget_summary": performance_budget.get("summary", {}) if isinstance(performance_budget, dict) else {},
            "metrics": performance_budget.get("metrics", {}) if isinstance(performance_budget, dict) else {},
            "budgets": performance_budget.get("budgets", {}) if isinstance(performance_budget, dict) else {},
            "latest_ledger_run": ledger_runs[0] if ledger_runs else {},
            "ledger_run_count": len(ledger_runs) if isinstance(ledger_runs, list) else 0,
        }

    distribution_hardening = _load_product_governance_artifact(
        "distribution_hardening_validation.json",
        enabled=include_sage_product_governance,
    )
    if distribution_hardening:
        ctx["distribution_hardening"] = {
            "summary": distribution_hardening.get("summary", {}) if isinstance(distribution_hardening, dict) else {},
            "failed_checks": [
                item
                for item in (distribution_hardening.get("checks", []) if isinstance(distribution_hardening, dict) else [])
                if isinstance(item, dict) and not item.get("passed")
            ],
        }

    entrypoint_failures = _load_product_governance_artifact(
        "entrypoint_failure_validation.json",
        enabled=include_sage_product_governance,
    )
    if entrypoint_failures:
        ctx["entrypoint_failure_validation"] = {
            "summary": entrypoint_failures.get("summary", {}) if isinstance(entrypoint_failures, dict) else {},
            "failed_checks": [
                item
                for item in (entrypoint_failures.get("checks", []) if isinstance(entrypoint_failures, dict) else [])
                if isinstance(item, dict) and not item.get("passed")
            ],
        }

    operational_parity = _load_product_governance_artifact(
        "operational_parity_validation.json",
        enabled=include_sage_product_governance,
    )
    if operational_parity:
        ctx["operational_parity_validation"] = {
            "summary": operational_parity.get("summary", {}) if isinstance(operational_parity, dict) else {},
            "changed_sections": sorted((operational_parity.get("diff", {}) or {}).keys()) if isinstance(operational_parity, dict) else [],
        }

    # 15. Blast Radius Summary (highest-impact files per project)
    br = load_json_file(RAW_DIR / "blast_radius.json")
    if br:
        br_list = br.get("blast_radius", [])
        # Group by project and pick top-5 per project
        by_project = defaultdict(list)
        for entry in br_list:
            raw_file = entry.get("file", "")
            if "::" in raw_file:
                project, path = raw_file.split("::", 1)
            else:
                project, path = "UNKNOWN", raw_file
            by_project[project].append({
                "file": path,
                "direct": entry.get("direct_dependents", 0),
                "transitive": entry.get("transitive_dependents", 0),
                "impact": entry.get("total_impact_score", 0),
            })
        blast_ctx = {}
        for proj, entries in by_project.items():
            blast_ctx[proj] = entries[:5]  # already sorted by impact
        ctx["blast_radius"] = {
            "most_critical": br.get("metrics", {}).get("most_critical_node", "?"),
            "highest_impact": br.get("metrics", {}).get("highest_impact_score", 0),
            "by_project": blast_ctx,
        }

    # ---------------------------------------------------------------------------
    # TIER 2: WORKFLOW ENRICHMENTS (16-18)
    # ---------------------------------------------------------------------------


    # 16. State Flow Map (Zustand Store Inventory)
    sf = load_json_file(RAW_DIR / "state_flow.json")
    if sf:
        stores = sf.get("zustand_stores", {})
        by_layer = defaultdict(list)
        for store_path in sorted(stores.keys()):
            parts = store_path.replace("\\", "/").split("/")
            store_name = Path(store_path).stem
            if parts[0] == "lifecycle-modules" and len(parts) > 1:
                layer_key = f"{parts[0]}/{parts[1]}"
            else:
                layer_key = parts[0]
            by_layer[layer_key].append(store_name)
        ctx["state_stores"] = {
            "count": len(stores),
            "by_layer": dict(by_layer),
        }

    # 17. Merge Overview (from fractal_map.json meta + host_merge_intelligence)
    fractal = load_fractal_map_data()
    if fractal and "merge_candidates" in fractal:
        candidates = fractal["merge_candidates"]
        total = len(candidates)
        by_readiness = Counter(c.get("merge_readiness", "unknown") for c in candidates)
        by_risk = Counter(c.get("risk", "UNKNOWN") for c in candidates)
        donor_counts = Counter(c.get("source", "?") for c in candidates)
        top_5 = sorted(candidates, key=lambda x: x.get("delta", 0), reverse=True)[:5]
        ctx["merge_overview"] = {
            "total_candidates": total,
            "by_readiness": dict(by_readiness),
            "by_risk": dict(by_risk),
            "top_5_donors": [
                {
                    "symbol": c.get("name", "?"),
                    "from": c.get("source", "?"),
                    "target_studio": c.get("target_studio", "?"),
                    "delta": c.get("delta", 0),
                    "risk": c.get("risk", "?"),
                }
                for c in top_5
            ],
            "donor_ranking": dict(donor_counts.most_common()),
        }
    elif host_studios:
        # Fallback: build merge overview from host_merge_intelligence per-studio data
        total_candidates = 0
        donor_counter = Counter()
        top_candidates = []
        for studio_data in host_studios.values():
            merge = studio_data.get("merge_summary", {})
            total_candidates += merge.get("decision_count", 0)
            for d in merge.get("top_donors", []):
                donor_counter[d["source"]] += d["count"]
            for cand in studio_data.get("top_merge_candidates", [])[:3]:
                top_candidates.append({
                    "symbol": cand.get("name", "?"),
                    "from": cand.get("source", "?"),
                    "target_studio": studio_data.get("studio", "?"),
                    "delta": cand.get("delta", 0),
                    "risk": cand.get("risk", "?"),
                })
        top_candidates.sort(key=lambda x: x.get("delta", 0), reverse=True)
        ctx["merge_overview"] = {
            "total_candidates": total_candidates,
            "top_5_donors": top_candidates[:5],
            "donor_ranking": dict(donor_counter.most_common()),
        }

    # 18. Pipeline Meta (freshness + snapshot info)
    snapshot_count = 0
    if SNAPSHOTS_DIR.exists():
        snapshot_count = len([f for f in SNAPSHOTS_DIR.iterdir() if f.suffix == ".json"])
    ctx["pipeline_meta"] = {
        "last_run": ctx["_meta"]["generated_at"],
        "snapshot_count": snapshot_count,
        "data_freshness": "current",
    }

    # ---------------------------------------------------------------------------
    # TIER 3: ADVANCED ENRICHMENTS (18-20)
    # ---------------------------------------------------------------------------

    # 19. Audit by Module Breakdown
    violations_list = get_violations()
    if violations_list:
        module_audit = defaultdict(lambda: defaultdict(int))
        for v in violations_list:
            file_path = v.get("file", "")
            rule = v.get("rule", v.get("category", "other"))
            module = _extract_module(file_path)
            module_audit[module][rule] += 1
            module_audit[module]["total"] += 1
        if module_audit:
            sorted_modules = sorted(module_audit.items(), key=lambda x: x[1]["total"], reverse=True)
            ctx["audit_by_module"] = {
                mod: dict(cats) for mod, cats in sorted_modules[:10]
            }

    # 20. Report Index (raw + summary with sizes)
    report_index = {}
    # Map of logical names to their raw and summary files
    _report_pairs = {
        "fractal_map": {"topic": "donor comparison & symbol mapping"},
        "blast_radius": {"topic": "merge impact analysis"},
        "audit_report": {"topic": "doctrine violations", "summary_ext": ".txt"},
        "circular_deps_report": {"topic": "circular dependencies", "raw_name": "circular_deps"},
        "dead_code_report": {"topic": "dead code detection", "raw_name": "dead_code"},
        "clone_detector": {"topic": "code duplication"},
        "health_score": {"topic": "project health"},
        "host_merge_intelligence": {"topic": "host-aware merge strategy"},
        "decision_evidence": {"topic": "merge decision reasoning"},
        "hexagonal_bindings": {"topic": "port-adapter bindings"},
        "landscape_map": {"topic": "project landscape"},
        "module_risk_matrix": {"topic": "module risk ranking"},
        "nanometric_diff": {"topic": "variation nanometric diff"},
        "quality_gate": {"topic": "quality gate results"},
        "framework_routes": {"topic": "framework-aware route map"},
        "quality_review": {"topic": "quality review oracle"},
        "proof_obligations": {"topic": "proof obligations verification"},
        "state_flow": {"topic": "zustand store & state flow"},
        "temporal_diff": {"topic": "temporal evolution"},
        "ui_architecture_map": {"topic": "UI surface mapping"},
        "live_surface_findings": {"topic": "live but problematic surfaces", "raw_name": "live_surface_findings"},
        "live_surface_priority_pack": {"topic": "priority duplicate-live review pack", "raw_name": "live_surface_priority_pack"},
        "ui_runtime_contracts": {"topic": "UI merge runtime contracts and browser smoke gates"},
        "ui_smoke_specs": {"topic": "generated UI pre-merge smoke spec templates"},
        "ui_smoke_execution": {"topic": "UI smoke execution readiness evidence"},
        "next_boundary_analysis": {"topic": "Next.js client/server/cache boundary contracts"},
        "state_data_graph": {"topic": "React state/data relationship graph"},
        "a11y_i18n_contracts": {"topic": "accessibility and i18n runtime contracts"},
        "merge_dependency_packages": {"topic": "Atlas-backed merge transfer manifests"},
        "merge_simulation": {"topic": "static dry-run merge decisions"},
        "merge_decision_cockpit": {"topic": "single-screen merge readiness decisions"},
        "ai_task_packs": {"topic": "AI/human execution task packs"},
        "merge_intelligence_regression": {"topic": "universal merge intelligence regression gate"},
        "adapter_registry": {"topic": "declarative ecosystem adapter capabilities"},
        "release_readiness": {"topic": "production readiness evidence envelope"},
        "distribution_hardening_validation": {"topic": "CLI/docs/distribution hardening gate"},
        "entrypoint_failure_validation": {"topic": "entrypoint and controlled failure drills"},
        "operational_parity_validation": {"topic": "cached full-pipeline semantic parity gate"},
        "performance_budget_validation": {"topic": "repo-scaled performance budget gate"},
        "performance_ledger": {"topic": "historical pipeline performance ledger"},
    }
    for name, info in _report_pairs.items():
        entry = {"topic": info["topic"]}
        raw_name = info.get("raw_name", name)
        raw_path = RAW_DIR / f"{raw_name}.json"
        raw_payload = load_json_file(raw_path, None)
        if raw_payload is not None:
            entry["raw"] = f".raw/{raw_name}.json"
            try:
                entry["raw_kb"] = round(raw_path.stat().st_size / 1024, 1)
            except OSError:
                entry["raw_storage"] = "sqlite_primary_shadow_missing"
        summary_ext = info.get("summary_ext", ".md")
        summary_path = REPORTS_DIR / f"{name}{summary_ext}"
        if summary_path.exists():
            entry["summary"] = f"reports/{name}{summary_ext}"
            entry["summary_kb"] = round(summary_path.stat().st_size / 1024, 1)
        if "raw" in entry or "summary" in entry:
            report_index[name] = entry
    ctx["report_index"] = report_index

    # 21. AI Action Hints (auto-generated priority actions)
    hints = []
    # Hint: deep relative imports dominate audit
    if audit_categories:
        deep_rel = audit_categories.get("relative_imports_no_alias", 0) + audit_categories.get("deep_imports", 0)
        if deep_rel > 0 and audit_total > 0 and deep_rel / audit_total > 0.5:
            hints.append({
                "priority": 1,
                "action": "Refactor deep relative imports to @/ aliases",
                "reason": f"{deep_rel} of {audit_total} audit violations ({round(deep_rel/audit_total*100)}%) are deep relative imports",
            })
    # Hint: largest dead code hotspot
    if isinstance(dead, list) and dead_total > 0:
        dc_counter = Counter(item.get("file", "?") for item in dead)
        top_dc = dc_counter.most_common(1)
        if top_dc:
            hints.append({
                "priority": 2,
                "action": f"Clean dead code hotspot: {top_dc[0][0]}",
                "reason": f"{top_dc[0][1]} dead exports in a single file",
            })
    # Hint: unbound hexagonal ports
    if hex_data:
        unbound = hex_data.get("unbound_ports", [])
        for u in unbound[:2]:
            hints.append({
                "priority": 3,
                "action": f"Implement adapter for {u.get('port', '?')}",
                "reason": f"Unbound hexagonal port in {u.get('file', '?')}",
            })
    # Hint: high clone pressure
    if clones:
        wasted = clones.get("metrics", {}).get("wasted_lines_due_to_clones", 0)
        if wasted > 10000:
            hints.append({
                "priority": 4,
                "action": "Reduce code duplication across variations",
                "reason": f"{wasted:,} lines wasted due to clones across {clones.get('metrics', {}).get('clone_clusters', 0)} clusters",
            })
    if live_surface:
        summary = live_surface.get("summary", {}) if isinstance(live_surface, dict) else {}
        if int(summary.get("broken_live", 0) or 0) > 0:
            hints.append({
                "priority": 1,
                "action": "Resolve broken live surfaces before trusting merge automation",
                "reason": f"{summary.get('broken_live', 0)} reachable files currently have oracle-backed structural issues",
            })
        if int(summary.get("duplicate_live", 0) or 0) > 0:
            hints.append({
                "priority": 2,
                "action": "Review duplicate-live priority pack",
                "reason": f"{summary.get('duplicate_live', 0)} live duplicate candidates detected outside dead-code scope",
            })
    if ui_runtime:
        smoke_count = len([
            item for item in (ui_runtime.get("merge_candidates", []) if isinstance(ui_runtime, dict) else [])
            if isinstance(item, dict) and item.get("recommended_gate") == "browser_smoke_required"
        ])
        if smoke_count > 0:
            hints.append({
                "priority": 1,
                "action": "Run browser smoke gates before importing high-risk UI candidates",
                "reason": f"{smoke_count} UI merge candidates require route-level runtime validation",
            })
    # Hint: quality gate close to failing
    if qg:
        for check in qg.get("checks", []):
            if check.get("passed") and check.get("operator") in [">=", "<="]:
                actual = check.get("actual", 0)
                expected = check.get("expected", 0)
                if check["operator"] == ">=" and expected > 0:
                    margin = (actual - expected) / expected
                    if 0 < margin < 0.15:
                        hints.append({
                            "priority": 2,
                            "action": f"Improve {check['name']} — close to failing",
                            "reason": f"Actual {actual} vs threshold {expected} (margin: {round(margin*100)}%)",
                        })
                elif check["operator"] == "<=" and expected > 0:
                    usage = actual / expected
                    if usage > 0.85:
                        hints.append({
                            "priority": 2,
                            "action": f"Reduce {check['name']} — approaching limit",
                            "reason": f"Actual {actual} vs limit {expected} (usage: {round(usage*100)}%)",
                        })
    # Sort hints by priority
    hints.sort(key=lambda h: h["priority"])
    if hints:
        ctx["action_hints"] = hints

    # 22. Architectural Signatures (Top 75 most influential symbols from genome)
    genome = load_genome_data()
    if genome:
        # Calculate impact based on dependency count
        symbol_impacts = []
        for symbol_name, variations in genome.items():
            if not variations: continue
            var = variations[0] # Take primary variation
            # Impact = count of dependencies it has 
            impact_score = len(var.get("dependencies", []))
            
            symbol_impacts.append({
                "name": symbol_name,
                "signature": var.get("signature", ""),
                "impact": impact_score,
                "file": var.get("file", ""),
                "type": var.get("type", "unknown")
            })
        
        # Sort by impact score
        top_symbols = sorted(symbol_impacts, key=lambda x: x["impact"], reverse=True)[:75]
        
        if top_symbols:
            ctx["architectural_signatures"] = {
                "count": len(top_symbols),
                "ranking_method": "dependency_count_impact",
                "top_75": top_symbols
            }

    # 23. AI Action Layer (global-index safety contract)
    ai_context_policy = _ai_context_generator_policy()
    ctx["ai_action_layer"] = {
        "surface_role": ai_context_policy.get("surface_role", "global_index_not_surgical_packet"),
        "agent_rule": ai_context_policy.get(
            "agent_rule",
            "AI Context is a broad repository index. Request a bounded ContextOS/MCP packet before mutation.",
        ),
        "recommended_followups": ai_context_policy.get("recommended_followups", []),
        "decision_rules": ai_context_policy.get("decision_rules", {}),
        "policy_source": "config/pipeline_execution_policy.json:ai_context_generator_policy",
    }

    # ---------------------------------------------------------------------------
    # SAVE
    # ---------------------------------------------------------------------------
    ctx_path = RAW_DIR / "ai_context.json"
    save_json_atomic(ctx_path, ctx)
    return ctx



def _extract_module(file_path: str) -> str:
    """Extract module name or top-level category from a file path (Universal)."""
    from tools.core.studio_resolver import get_module_name
    return get_module_name(file_path)


if __name__ == "__main__":

    generate_ai_context()
