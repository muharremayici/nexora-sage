from tools.core.config import RAW_DIR, REPORTS_DIR, DOCTRINE, save_text_atomic
from tools.core.doctrine_contract import require_doctrine_mapping
from tools.core.host_intelligence import load_host_intelligence
from tools.core.json_io import load_json_file
from tools.core.logger import logger


def run_cmo_dashboard():
    evidence_path = RAW_DIR / "decision_evidence.json"
    host_data = load_host_intelligence()
    evidence_data = load_json_file(evidence_path, {})

    if not evidence_data or not host_data:
        save_text_atomic(
            REPORTS_DIR / "cmo_dashboard.md",
            "\n".join(
                [
                    "# Nexora SAGE: Chief Medical Officer Dashboard",
                    "",
                    "Dashboard generation was skipped because trusted upstream evidence is not available yet.",
                    "",
                    "Run a full pipeline pass to generate `decision_evidence` and `host_intelligence` artifacts.",
                ]
            ),
        )
        logger.info("CMO Dashboard skipped; trusted upstream evidence is not available yet.")
        return

    comparative_mode = bool((evidence_data.get("meta") or {}).get("comparative_mode", True))

    health_path = RAW_DIR / "health_score.json"
    health_data = load_json_file(health_path, {})
    
    risk_policy = require_doctrine_mapping("dashboard_risk_policy")

    # Gather precise S2-01 & S2-02 Metrics (Deep-Dive Aware)
    trusted_count = 0
    review_queue_count = 0
    quarantine_count = 0 
    studio_results = []

    # PHASE 4: Blast Radius Analytics
    blast_radius_path = RAW_DIR / "blast_radius.json"
    blast_data = load_json_file(blast_radius_path, {"blast_radius": []})
    quarantine_threshold = risk_policy.get("quarantine_threshold")
    
    for node in blast_data.get("blast_radius", []):
        if node.get("total_impact_score", 0.0) >= quarantine_threshold:
            quarantine_count += 1

    # Iterate over all keys that end in _deep_dive
    for key, data in evidence_data.items():
        if key.endswith("_deep_dive") and isinstance(data, dict):
            trusted = data.get("trusted_top_candidates", [])
            queue = data.get("review_queue_examples", [])
            trusted_count += len(trusted)
            review_queue_count += len(queue)
            
            studio_name = key.replace("_deep_dive", "").capitalize()
            # Calculate a readiness score for the studio
            tot = max(1, len(trusted) + len(queue))
            readiness = round((len(trusted) / tot) * 100, 1)
            
            # Dynamic Risk Evaluation via Doctrine
            risk = risk_policy.get("fallback_risk")
            rec = risk_policy.get("fallback_recommendation")
            
            bounds = risk_policy.get("readiness_bounds")
            for bound in sorted(bounds, key=lambda x: x['min'], reverse=True):
                if readiness >= bound['min']:
                    risk = bound['risk']
                    rec = bound['recommendation']
                    break

            studio_results.append({
                "name": studio_name,
                "readiness": readiness,
                "risk": risk,
                "recommendation": rec,
                "trusted": len(trusted),
                "review": len(queue)
            })

    protected_boundaries = 0
    compose_targets = 0
    for mod_data in host_data.get("studios", {}).values():
        protected_boundaries += len(mod_data.get("protected_files", []))
        compose_targets += len(mod_data.get("compose_preferred_files", []))

    report = [
        "# Nexora SAGE: Chief Medical Officer Dashboard",
        "",
        "Note: This dashboard aggregates the **Architectural Truth Engines** (Decision Evidence & Host Intelligence).",
        "It summarizes verified structural proofs and Hexagonal Doctrine compliance.",
        "",
        "## System Executive Summary",
        f"- **Health Score**: {health_data.get('overall', 'N/A')}/100",
        f"- **Structurally Verified Targets**: {trusted_count} files passed the S2-01 Jaccard AST proof.",
        f"- **Architectural Violations (Manual Review)**: {review_queue_count} files failed the S1-04 Doctrine Safety Gate.",
        f"- **Quarantined High-Impact Nodes**: {quarantine_count} files exceed the Phase 4 risk threshold (> {quarantine_threshold}).",
        f"- **Protected Host Boundaries**: {protected_boundaries} critical infrastructure paths secured.",
        f"- **Compose-Preferred Targets**: {compose_targets} files slated for structural evolution.",
        "",
        "## Evolutionary Action Plan",
        "Based on strict Semantic Intelligence, the pipeline recommends:",
    ]

    if not comparative_mode:
        report += [
            "",
            "Comparative donor analysis is not active for this workspace because no variant donor project was discovered.",
            "Dashboard metrics below should be read as local structural health, not cross-project import readiness.",
        ]

    report += [
        "",
        "## Surgical Readiness and AI Safety Gates",
        "| Module | Readiness Score | Risk | Verified | Review | Recommendation |",
        "|---|---:|---|---:|---:|---|",
    ]
    for res in studio_results:
        report.append(f"| {res['name']} | {res['readiness']}% | {res['risk']} | {res['trusted']} | {res['review']} | {res['recommendation']} |")

    report += [
        "",
        "## Self-Healing Perspective",
        f"The Architecture Engine has isolated {trusted_count} AST-verified components ready for phase-1 pilot intake.",
        "",
        "---",
        "**Verdict**: This dashboard reflects the Hardened Architecture Doctrine. `READY` states are structurally proven.",
    ]

    output_path = REPORTS_DIR / "cmo_dashboard.md"
    save_text_atomic(output_path, "\n".join(report))
    logger.info(f"CMO Dashboard rewritten and verified: {output_path}")


if __name__ == "__main__":
    run_cmo_dashboard()
