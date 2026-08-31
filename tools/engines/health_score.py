"""
Health Score Engine produces per-project 0-100 composite health scores
by aggregating audit results, dead code ratios, circular deps, and coupling metrics.
"""

import json
from collections import defaultdict
from tools.core.audit_report import get_total_violations, get_violations, get_project_violations
from tools.core.audit_rules import build_rule_taxonomy

from tools.core.artifact_validator import ensure_valid_payload
from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic, DOCTRINE
from tools.core.doctrine_contract import require_doctrine_mapping
from tools.core.genome_io import load_genome_data
from tools.core.json_io import load_json_file
from tools.core.artifact_freshness_contract import evaluate_named_artifact_chain
from tools.core.logger import logger
from tools.core.path_engine import to_posix_path
from tools.core.projects_registry import project_display_name


def _resolve_rule_cap(rule_caps, rule_key: str, default_rule_cap: float) -> float:
    """Use the declared per-rule cap when present, otherwise the policy default."""
    return float(rule_caps[rule_key] if rule_key in rule_caps else default_rule_cap)


def _health_input_shape_status(artifact, payload):
    if artifact == "surgical_discovery":
        return "valid" if isinstance(payload, list) else "invalid"
    if not isinstance(payload, dict):
        return "invalid"
    required_lists = {
        "dead_code": ("items",),
        "circular_deps": ("cycles", "edges"),
        "audit_report": ("violations",),
        "keyword_scanner_all": ("keywords",),
    }
    if artifact == "genome":
        return "valid" if payload else "invalid"
    return "valid" if all(isinstance(payload.get(field), list) for field in required_lists.get(artifact, ())) else "invalid"


def _health_input_evidence(payloads):
    chain = evaluate_named_artifact_chain("health_score_input_chain", RAW_DIR)
    rows = {
        str(row.get("artifact")): row
        for row in chain.get("artifacts", [])
        if isinstance(row, dict) and row.get("artifact")
    }
    evidence = {}
    unavailable = []
    for artifact in chain.get("required_artifacts", []):
        name = str(artifact)
        row = rows.get(name, {})
        shape_status = _health_input_shape_status(name, payloads.get(name))
        available = bool(row.get("exists")) and shape_status == "valid"
        evidence[name] = {
            "status": "available" if available else "unavailable",
            "source": str(row.get("source") or "missing"),
            "shape_status": shape_status,
            "payload_bytes": int(row.get("payload_bytes") or 0),
            "updated_at": str(row.get("updated_at") or ""),
        }
        if not available:
            unavailable.append(name)
    if chain.get("missing_contract"):
        unavailable.append("health_score_input_chain")
    return sorted(set(unavailable)), evidence


def _write_unknown_health_score(weights, unavailable_inputs, input_evidence):
    breakdown = {str(key): 0 for key in weights}
    payload = {
        "overall": 0,
        "grade": "UNKNOWN",
        "projects": {},
        "modules": {},
        "project_details": {},
        "breakdown": breakdown,
        "weights": weights,
        "evidence_status": "PARTIAL",
        "unavailable_inputs": unavailable_inputs,
        "input_evidence": input_evidence,
    }
    save_json_atomic(RAW_DIR / "health_score.json", payload)
    ensure_valid_payload("health_score", payload)
    save_text_atomic(
        REPORTS_DIR / "health_score.md",
        "# Codebase Health Score\n\n"
        "- Status: `UNKNOWN`\n"
        f"- Unavailable inputs: `{', '.join(unavailable_inputs)}`\n\n"
        "Numeric health claims are blocked until the declared input evidence is refreshed.\n",
    )
    logger.warning("Health Score evidence is partial; numeric health claim blocked: %s", ", ".join(unavailable_inputs))
    return payload

def calculate_health_score():
    logger.info("Calculating codebase health scores (Project-Aware)...")
    
    # Load scoring policy from doctrine
    policy = require_doctrine_mapping("health_scoring_policy")
    weights = policy.get("weights")
    penalties = policy.get("penalties")

    # Load shared data sources
    dead_path = RAW_DIR / "dead_code.json"
    circ_path = RAW_DIR / "circular_deps.json"
    surg_path = RAW_DIR / "surgical_discovery.json"
    kw_path = RAW_DIR / "keyword_scanner_all.json"

    genome = load_genome_data()
    dead_payload = load_json_file(dead_path, {})
    circ = load_json_file(circ_path, {})
    surgical = load_json_file(surg_path, [])
    audit_json_path = RAW_DIR / 'audit_report.json'
    audit_data = load_json_file(audit_json_path, {})
    kw_data = load_json_file(kw_path, {})
    unavailable_inputs, input_evidence = _health_input_evidence({
        "genome": genome,
        "dead_code": dead_payload,
        "circular_deps": circ,
        "surgical_discovery": surgical,
        "audit_report": audit_data,
        "keyword_scanner_all": kw_data,
    })
    if unavailable_inputs:
        return _write_unknown_health_score(weights, unavailable_inputs, input_evidence)
        
    # Read the High-Fidelity Genome
    
    # Identify unique projects and symbol counts in a single genome pass.
    projects = set()
    genome_project_counts = defaultdict(int)
    for occs in genome.values():
        for o in occs:
            p = o.get('project')
            if p:
                projects.add(p)
                genome_project_counts[p] += 1
    
    # Create a project-level map
    results_map = {}
    audit_violations = get_violations() # dict: rule_id -> list[violation]
    total_audit_violations = get_total_violations()

    # Create a module-level map (Universal Categories)
    from tools.core.studio_resolver import get_module_name
    module_violations = defaultdict(int)
    for v in audit_violations:
        mod = get_module_name(v.get('file', ''))
        module_violations[mod] += 1


    from tools.core.config import DYNAMIC_CONFIG
    roles = DYNAMIC_CONFIG.get("project_roles", {})
    host_key = next((k for k, v in roles.items() if v == "host" and k in projects), None)
    if not host_key:
        host_key = sorted(list(projects))[0] if projects else None

    dead_by_project = defaultdict(int)
    dead = dead_payload.get("items", []) if isinstance(dead_payload, dict) else dead_payload
    dead_items = dead if isinstance(dead, list) else []
    for d in dead_items:
        p = d.get("project")
        if p:
            dead_by_project[p] += 1

    cycles_by_project = defaultdict(int)
    cycles = circ.get("cycles", []) if isinstance(circ, dict) else []
    for cycle in cycles:
        cycle_text = " ".join(str(node) for node in cycle).lower()
        for pkey in projects:
            if pkey and pkey.lower() in cycle_text:
                cycles_by_project[pkey] += 1

    coupling_totals = defaultdict(float)
    coupling_counts = defaultdict(int)
    surgical_items = surgical if isinstance(surgical, list) else []
    for item in surgical_items:
        p = item.get("project")
        if not p:
            continue
        coupling_totals[p] += float(item.get("decoupling", 50) or 50)
        coupling_counts[p] += 1

    # Load project-rule violations from audit_report.json
    by_project_rule = {}
    by_project_rule = audit_data.get("summary", {}).get("by_project_rule", {}) if isinstance(audit_data, dict) else {}

    # 5. Keyword Coverage - Dynamic scaling
    keyword_coverage_score = 50
    keywords = kw_data.get("keywords", []) if isinstance(kw_data, dict) else []
    all_found_cats = {cat for entry in keywords for cat in entry.get("found", [])}

    # Determine total unique categories from doctrine glossary or the scan itself
    glossary = require_doctrine_mapping("discovery_taxonomy").get("semantic_tokens")
    total_cats = len(glossary) if glossary else 19

    keyword_coverage_score = min(100, round((len(all_found_cats) / max(total_cats, 1)) * 100))

    # Weight multipliers and caps for architecture purity.
    # Release health should distinguish blocking rules from heal/advisory backlog.
    taxonomy_profiles = (build_rule_taxonomy(project_count=len(projects)).get("profiles", {}) or {})
    severity_weights = policy.get("rule_weights")
    mode_multipliers = policy.get("mode_penalty_multipliers")

    # Formatting/size debt should remain visible without collapsing the whole score.
    rule_caps = policy.get("rule_caps")
    default_rule_cap = float(policy.get("default_rule_cap"))

    def _rule_mode(rule_key: str) -> str:
        return str((taxonomy_profiles.get(rule_key) or {}).get("mode", "unknown"))

    def _rule_penalty(rule_key: str, count: int) -> float:
        weight = float(severity_weights.get(rule_key, 1.0) or 1.0)
        mode_multiplier = float(mode_multipliers.get(_rule_mode(rule_key), mode_multipliers.get("unknown", 0.2)) or 0.0)
        raw_penalty = float(count or 0) * weight * mode_multiplier
        cap = _resolve_rule_cap(rule_caps, rule_key, default_rule_cap)
        return min(raw_penalty, cap)

    for pkey in sorted(list(projects)):
        if pkey == "UNKNOWN": continue
        logger.info(f"[HEALTH] Auditing project: {pkey}...")
        
        scores = {}

        # 1. Architecture Purity (Smarter Weighted Penalty with Caps)
        project_violations_map = by_project_rule.get(pkey, {}) # Map: rule_key -> count
        
        weighted_penalty = 0.0
        for rule_key, count in project_violations_map.items():
            weighted_penalty += _rule_penalty(rule_key, int(count or 0))
            
        scores["architecture_purity"] = max(0, 100 - weighted_penalty) if project_violations_map else 50
        scores["keyword_coverage"] = keyword_coverage_score

        # 2. Dead Code Ratio
        if dead_by_project:
            dead_count = dead_by_project.get(pkey, 0)
            project_genome_count = genome_project_counts.get(pkey, 0)
            total_exports = max(project_genome_count, dead_count + 100)
            ratio = dead_count / max(total_exports, 1)
            scores["dead_code_ratio"] = max(0, round((1 - ratio) * 100))
        else: scores["dead_code_ratio"] = 50

        # 3. Circular Deps
        if cycles_by_project:
            penalty_per_c = penalties.get("per_circular_dep", 15.0)
            scores["circular_deps"] = max(0, 100 - (cycles_by_project.get(pkey, 0) * penalty_per_c))
        else: scores["circular_deps"] = 100

        # 4. Coupling Health
        if coupling_counts:
            count = coupling_counts.get(pkey, 0)
            if count:
                scores["coupling_health"] = min(100, round(coupling_totals.get(pkey, 0.0) / count))
            else:
                scores["coupling_health"] = 50
        else: scores["coupling_health"] = 50

        # 5. Keyword Coverage
        scores["keyword_coverage"] = keyword_coverage_score

        overall = sum(scores[key] * weights.get(key, 0) for key in weights)

        result = {"overall": round(overall), "grade": _grade(overall), "breakdown": scores, "weights": weights}
        results_map[pkey] = result

        # Export individual project report
        display_name = project_display_name(pkey)
        md_lines = [f"# Codebase Health Score: {display_name} [{pkey}]", "", f"## Overall: **{result['overall']}/100** ({result['grade']})", "", "| Metric | Score | Weight |", "|---|---:|---:|"]
        for key, value in scores.items():
            md_lines.append(f"| {'GOOD' if value >= 75 else 'WATCH' if value >= 50 else 'FAIL'} {key.replace('_', ' ').title()} | {value}/100 | {int(weights.get(key, 0) * 100)}% |")
        
        md_path = REPORTS_DIR / f"health_score_{pkey.lower()}.md"
        save_text_atomic(md_path, "\n".join(md_lines))

    # [PHASE 4] Module-Level Health (Universal Categories)
    module_health = {}
    penalty_per_m = penalties.get("per_module_violation", 5.0)
    for mod, v_count in module_violations.items():
        score = max(0, 100 - (v_count * penalty_per_m)) # Doctrine-driven penalty
        module_health[mod] = {"score": score, "violations": v_count, "grade": _grade(score)}

    # Generate a consolidated global summary JSON
    main_res = results_map.get(host_key, list(results_map.values())[0] if results_map else {"overall": 0, "grade": "F", "breakdown": {}, "weights": {}})
    
    consolidated = {
        "overall": main_res['overall'],
        "grade": main_res.get('grade', 'F'),
        "projects": {p: r['overall'] for p, r in results_map.items()},
        "modules": module_health, # [NEW] Universal Module Health
        "project_details": results_map,
        "breakdown": main_res.get('breakdown', {}),
        "weights": weights,
        "evidence_status": "PASS",
        "unavailable_inputs": [],
        "input_evidence": input_evidence,
    }
    
    json_path = RAW_DIR / "health_score.json"
    save_json_atomic(json_path, consolidated)
    
    ensure_valid_payload("health_score", consolidated)
    return main_res



def _project_audit_violations(project_key, violations):
    needle = str(project_key or "").lower()
    if not needle:
        return 0

    total = 0
    for violation in violations:
        file_path = str(violation.get("file", "")).lower()
        detail = str(violation.get("detail", "")).lower()
        if needle in file_path or needle in detail:
            total += 1
    return total

def _grade(score):
    policy = require_doctrine_mapping("health_scoring_policy")
    bounds = policy.get("grading_bounds", [])
    
    for bound in bounds:
        if score >= bound.get("min", 0):
            return bound.get("grade", "?")
            
    return policy.get("fallback_grade", "F")

if __name__ == "__main__":
    calculate_health_score()
