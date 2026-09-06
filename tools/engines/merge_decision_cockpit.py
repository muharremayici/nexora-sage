from __future__ import annotations

from collections import Counter

from tools.core.analysis_snapshot_lineage import write_current_atlas_lineage
from tools.core.atlas_io import load_atlas_data
from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file
from tools.core.logger import logger
from tools.core.suppression import find_suppression, load_suppressions, stable_decision_key


ACTION_BY_DECISION = {
    "SAFE_TO_IMPORT": "Import Now",
    "ASSISTED_IMPORT": "Import With Review",
    "DO_NOT_IMPORT_YET": "Do Not Import Yet",
}

ACTION_RANK = {
    "Do Not Import Yet": 3,
    "Import With Review": 2,
    "Import Now": 1,
}


def _key(source: str | None, candidate: str | None) -> tuple[str, str]:
    return (str(source or ""), str(candidate or ""))


def _top_reasons(simulation: dict, ui_candidate: dict | None, package: dict | None) -> list[str]:
    reasons = []
    decision = simulation.get("decision")
    signals = simulation.get("signals", {}) or {}
    if decision == "SAFE_TO_IMPORT":
        reasons.append("static_dry_run_clean")
    if int(signals.get("target_conflicts", 0) or 0) > 0:
        reasons.append("target_conflict")
    if int(signals.get("missing_source_files", 0) or 0) > 0:
        reasons.append("missing_source_files")
    if int(signals.get("unresolved_internal_deps", 0) or 0) > 0:
        reasons.append("unresolved_internal_deps")
    if int(signals.get("external_deps", 0) or 0) > 0:
        reasons.append("external_package_verification")
    if package and package.get("closure_truncated"):
        reasons.append("dependency_closure_truncated")
    if ui_candidate and ui_candidate.get("recommended_gate") == "browser_smoke_required":
        reasons.append("browser_smoke_required")
    if ui_candidate and ui_candidate.get("risk_tier") == "high":
        reasons.append("high_ui_runtime_risk")
    if package and package.get("package_tier") == "manual_package_review":
        reasons.append("manual_dependency_package_review")
    return list(dict.fromkeys(reasons))[:10]


def confidence_for_decision(simulation: dict, ui_candidate: dict | None = None, package: dict | None = None, smoke_spec: dict | None = None) -> dict:
    if simulation.get("confidence_score") is not None:
        score = round(float(simulation.get("confidence_score") or 0) / 100, 2)
        if score >= 0.8:
            tier = "high"
        elif score >= 0.65:
            tier = "medium"
        else:
            tier = "low"
        return {
            "score": score,
            "tier": tier,
            "factors": ["simulation_confidence_components"],
            "recommendation": simulation.get("import_recommendation"),
        }
    decision = simulation.get("decision")
    signals = simulation.get("signals", {}) or {}
    factors = []
    if decision == "SAFE_TO_IMPORT":
        score = 0.78
        factors.append("clean_static_dry_run")
        if not simulation.get("unresolved_internal_deps") and not simulation.get("external_deps"):
            score += 0.1
            factors.append("closed_dependency_surface")
    elif decision == "DO_NOT_IMPORT_YET":
        score = 0.72
        factors.append("blocking_static_signal")
        if int(signals.get("missing_source_files", 0) or 0) > 0 or int(signals.get("target_conflicts", 0) or 0) > 0:
            score += 0.12
            factors.append("hard_file_or_target_blocker")
        if package and package.get("closure_truncated"):
            score += 0.08
            factors.append("truncated_dependency_closure")
    else:
        score = 0.62
        factors.append("assisted_review_boundary")
        if int(signals.get("external_deps", 0) or 0) > 0:
            score += 0.05
            factors.append("external_package_signal")
        if int(signals.get("unresolved_internal_deps", 0) or 0) > 0:
            score += 0.05
            factors.append("internal_resolution_signal")

    if (ui_candidate or {}).get("source_contract_file") or (package or {}).get("source_contract_file"):
        score += 0.04
        factors.append("source_runtime_contract")
    if smoke_spec or ((ui_candidate or {}).get("smoke_plan") or {}).get("route_framework"):
        score += 0.03
        factors.append("route_or_smoke_contract")
    if not _top_reasons(simulation, ui_candidate, package):
        score -= 0.08
        factors.append("thin_reason_surface")

    score = round(max(0.0, min(score, 0.99)), 2)
    if score >= 0.8:
        tier = "high"
    elif score >= 0.65:
        tier = "medium"
    else:
        tier = "low"
    return {"score": score, "tier": tier, "factors": list(dict.fromkeys(factors))}


def build_cockpit_row(simulation: dict, ui_candidate: dict | None = None, package: dict | None = None, smoke_spec: dict | None = None) -> dict:
    action = ACTION_BY_DECISION.get(simulation.get("decision"), "Import With Review")
    signals = simulation.get("signals", {}) or {}
    copy_files = ((simulation.get("copy_plan") or {}).get("copy_files") or [])
    row = {
        "candidate": simulation.get("candidate"),
        "source": simulation.get("source"),
        "target_path": simulation.get("target_path"),
        "action": action,
        "decision": simulation.get("decision"),
        "import_recommendation": simulation.get("import_recommendation"),
        "risk_tier": (ui_candidate or {}).get("risk_tier") or simulation.get("package_tier"),
        "package_tier": simulation.get("package_tier"),
        "closure_size": int(signals.get("closure_size", 0) or 0),
        "required_actions": simulation.get("required_actions", []),
        "reasons": _top_reasons(simulation, ui_candidate, package),
        "route": {
            "smoke_path": simulation.get("smoke_route") or (smoke_spec or {}).get("suggested_route"),
            "framework": ((ui_candidate or {}).get("smoke_plan") or {}).get("route_framework"),
            "template": ((ui_candidate or {}).get("smoke_plan") or {}).get("route_template"),
        },
        "evidence": {
            "source_contract_file": (ui_candidate or {}).get("source_contract_file") or (package or {}).get("source_contract_file"),
            "smoke_spec_path": (smoke_spec or {}).get("spec_path"),
            "copy_file_count": len(copy_files),
            "copy_files_sample": copy_files[:20],
            "copy_files_omitted": max(0, len(copy_files) - 20),
            "unresolved_internal_deps": simulation.get("unresolved_internal_deps", [])[:20],
            "external_deps": simulation.get("external_deps", [])[:20],
            "target_conflicts": simulation.get("target_conflicts", []),
            "confidence_components": simulation.get("confidence_components", {}),
            "harness_plan": (package or {}).get("harness_plan", {}),
        },
    }
    row["stable_key"] = stable_decision_key(row)
    row["confidence"] = confidence_for_decision(simulation, ui_candidate, package, smoke_spec)
    return row


def run_merge_decision_cockpit() -> dict:
    logger.info("Building merge decision cockpit...")
    atlas = load_atlas_data()
    simulation_payload = load_json_file(RAW_DIR / "merge_simulation.json", {})
    ui_payload = load_json_file(RAW_DIR / "ui_runtime_contracts.json", {})
    package_payload = load_json_file(RAW_DIR / "merge_dependency_packages.json", {})
    smoke_payload = load_json_file(RAW_DIR / "ui_smoke_specs.json", {})

    ui_by_key = {
        _key(item.get("source"), item.get("name")): item
        for item in (ui_payload.get("merge_candidates", []) if isinstance(ui_payload, dict) else [])
        if isinstance(item, dict)
    }
    package_by_key = {
        _key(item.get("source"), item.get("candidate")): item
        for item in (package_payload.get("packages", []) if isinstance(package_payload, dict) else [])
        if isinstance(item, dict)
    }
    smoke_by_key = {
        _key(item.get("source"), item.get("candidate")): item
        for item in (smoke_payload.get("specs", []) if isinstance(smoke_payload, dict) else [])
        if isinstance(item, dict)
    }

    rows = []
    suppressions = load_suppressions()
    for simulation in simulation_payload.get("simulations", []) if isinstance(simulation_payload, dict) else []:
        if not isinstance(simulation, dict):
            continue
        key = _key(simulation.get("source"), simulation.get("candidate"))
        row = build_cockpit_row(
            simulation,
            ui_candidate=ui_by_key.get(key),
            package=package_by_key.get(key),
            smoke_spec=smoke_by_key.get(key),
        )
        suppression = find_suppression("merge_decision_cockpit", row, suppressions)
        if suppression:
            row["suppressed"] = True
            row["suppression"] = suppression
        else:
            row["suppressed"] = False
        rows.append(row)

    rows = sorted(rows, key=lambda item: (ACTION_RANK.get(item["action"], 0), item["closure_size"]), reverse=True)
    action_counter = Counter(item["action"] for item in rows)
    effective_action_counter = Counter(item["action"] for item in rows if not item.get("suppressed"))
    suppressed_counter = Counter(item["action"] for item in rows if item.get("suppressed"))
    confidence_counter = Counter((item.get("confidence") or {}).get("tier", "unknown") for item in rows)
    source_counter = Counter(item["source"] for item in rows)
    payload = {
        "meta": {"kind": "merge_decision_cockpit", "version": "v1"},
        "summary": {
            "candidates": len(rows),
            "actions": dict(action_counter),
            "effective_actions": dict(effective_action_counter),
            "suppressed_actions": dict(suppressed_counter),
            "suppressed": sum(1 for item in rows if item.get("suppressed")),
            "confidence_tiers": dict(confidence_counter),
            "by_source": dict(source_counter),
        },
        "decisions": rows,
    }
    save_json_atomic(RAW_DIR / "merge_decision_cockpit.json", payload)
    write_current_atlas_lineage(
        artifact_id="merge_decision_cockpit",
        producer="tools.engines.merge_decision_cockpit",
        artifact_payload=payload,
        atlas=atlas,
        dependency_payloads={
            "merge_simulation": simulation_payload,
            "ui_runtime_contracts": ui_payload,
            "merge_dependency_packages": package_payload,
            "ui_smoke_specs": smoke_payload,
        },
    )

    lines = [
        "# Merge Decision Cockpit",
        "",
        "Single-screen merge readiness cockpit built from UI runtime contracts, dependency packages, smoke specs, and static merge simulation.",
        "",
        f"- candidates: `{len(rows)}`",
        f"- actions: `{dict(action_counter)}`",
        f"- effective_actions: `{dict(effective_action_counter)}`",
        f"- suppressed: `{sum(1 for item in rows if item.get('suppressed'))}`",
        f"- confidence_tiers: `{dict(confidence_counter)}`",
        "",
        "## Decision Board",
        "",
        "| Action | Confidence | Suppressed | Candidate | Source | Target | Route | Files | Reasons |",
        "|---|---|---|---|---|---|---|---:|---|",
    ]
    for item in rows[:100]:
        route = (item.get("route") or {}).get("smoke_path") or "-"
        reasons = ", ".join(item.get("reasons", [])[:5]) or "-"
        confidence = item.get("confidence") or {}
        lines.append(
            f"| `{item['action']}` | `{confidence.get('tier', '-')}` `{confidence.get('score', '-')}` | "
            f"`{bool(item.get('suppressed'))}` | `{item.get('candidate')}` | `{item.get('source')}` | "
            f"`{item.get('target_path') or '-'}` | `{route}` | {item.get('closure_size', 0)} | `{reasons}` |"
        )
    save_text_atomic(REPORTS_DIR / "merge_decision_cockpit.md", "\n".join(lines))
    logger.info("Merge decision cockpit artifacts written.")
    return payload


if __name__ == "__main__":
    run_merge_decision_cockpit()
