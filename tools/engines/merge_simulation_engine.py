from __future__ import annotations

from collections import Counter
from pathlib import Path
from pathlib import PurePosixPath

from tools.core.analysis_snapshot_lineage import evaluate_snapshot_bound_inputs, write_current_atlas_lineage
from tools.core.artifact_store import STORE
from tools.core.atlas_io import load_atlas_data
from tools.core.config import CONFIG_DIR, RAW_DIR, REPORTS_DIR, ROOT, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file
from tools.core.logger import logger
from tools.core.projects_registry import resolve_runtime_projects
from tools.core.language_registry import apply_legacy_path_shims


SIMULATION_LIMIT = 120


def _normalize_rel(path: str) -> str:
    parts = []
    for part in PurePosixPath(str(path or "").replace("\\", "/")).parts:
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def _target_rel(path: str) -> str:
    normalized = _normalize_rel(path)
    return apply_legacy_path_shims(normalized)


def _exists(root: Path, rel_path: str) -> bool:
    if not rel_path:
        return False
    try:
        candidate = (root / rel_path).resolve()
        return candidate.exists() and candidate.is_file()
    except OSError:
        return False


def _decision_for(package: dict, signals: dict) -> str:
    if signals["missing_source_files"] > 0:
        return "DO_NOT_IMPORT_YET"
    if signals["target_conflicts"] > 0:
        return "DO_NOT_IMPORT_YET"
    if package.get("closure_truncated"):
        return "DO_NOT_IMPORT_YET"
    if signals["unresolved_internal_deps"] > 25:
        return "DO_NOT_IMPORT_YET"
    if package.get("package_tier") == "manual_package_review":
        return "ASSISTED_IMPORT"
    if signals["unresolved_internal_deps"] > 0 or signals["external_deps"] > 0:
        return "ASSISTED_IMPORT"
    if package.get("recommended_gate") == "browser_smoke_required":
        return "ASSISTED_IMPORT"
    return "SAFE_TO_IMPORT"


def _smoke_index(payload: dict) -> dict[tuple[str, str], dict]:
    runs = payload.get("runs", []) if isinstance(payload, dict) else []
    index: dict[tuple[str, str], dict] = {}
    if not isinstance(runs, list):
        return index
    for run in runs:
        if not isinstance(run, dict):
            continue
        key = (str(run.get("source") or ""), str(run.get("candidate") or ""))
        if key[0] and key[1]:
            index[key] = run
    return index


def _ts_diagnostics_by_project(payload: dict) -> dict[str, int]:
    projects = payload.get("projects", {}) if isinstance(payload, dict) else {}
    totals: dict[str, int] = {}
    if not isinstance(projects, dict):
        return totals
    for project, data in projects.items():
        summary = data.get("summary", {}) if isinstance(data, dict) else {}
        totals[str(project)] = int(summary.get("total", 0) or 0)
    return totals


def _confidence_components(package: dict, signals: dict, smoke: dict | None, ts_total: int | None) -> dict:
    harness = package.get("harness_plan") or {}
    score = 100
    components = {
        "static_closure": {
            "score": 100,
            "evidence": {
                "closure_size": signals["closure_size"],
                "missing_source_files": signals["missing_source_files"],
                "target_conflicts": signals["target_conflicts"],
                "unresolved_internal_deps": signals["unresolved_internal_deps"],
                "external_deps": signals["external_deps"],
                "closure_truncated": bool(package.get("closure_truncated")),
            },
        },
        "typescript_diagnostics": {
            "score": max(0, 100 - min(80, ts_total * 4)) if ts_total is not None else None,
            "status": "available" if ts_total is not None else "unavailable",
            "evidence": {"project_diagnostics": ts_total},
        },
        "smoke_readiness": {
            "score": 90 if smoke else 45,
            "evidence": {
                "status": (smoke or {}).get("status") if smoke else "missing",
                "route": (smoke or {}).get("route") if smoke else package.get("smoke_route"),
                "execution_command": (smoke or {}).get("execution_command") if smoke else None,
            },
        },
        "dependency_harness": {
            "score": 95 if harness else 35,
            "evidence": {
                "provider_count": len(harness.get("provider_tree") or []),
                "i18n_required": len(((harness.get("i18n_keys") or {}).get("required") or [])),
                "browser_api_mocks": len(harness.get("browser_api_mocks") or []),
                "has_router_mocks": bool(harness.get("router_mocks")),
            },
        },
    }
    static_penalty = (
        signals["missing_source_files"] * 25
        + signals["target_conflicts"] * 30
        + signals["unresolved_internal_deps"] * 3
        + signals["external_deps"]
        + (20 if package.get("closure_truncated") else 0)
    )
    components["static_closure"]["score"] = max(0, 100 - min(100, static_penalty))
    weighted_components = [
        (components["static_closure"]["score"], 0.45),
        (components["smoke_readiness"]["score"], 0.2),
        (components["dependency_harness"]["score"], 0.2),
    ]
    if ts_total is not None:
        weighted_components.append((components["typescript_diagnostics"]["score"], 0.15))
    weight_total = sum(weight for _, weight in weighted_components)
    score = round(sum(component_score * weight for component_score, weight in weighted_components) / weight_total)
    if signals["missing_source_files"] or signals["target_conflicts"] or package.get("closure_truncated"):
        score = min(score, 39)
    elif signals["unresolved_internal_deps"] > 0 or signals["external_deps"] > 0 or package.get("recommended_gate") == "browser_smoke_required":
        score = min(score, 74)
    if ts_total is None:
        score = min(score, 74)

    if score >= 75:
        recommendation = "import-now"
    elif score >= 45:
        recommendation = "import-with-review"
    else:
        recommendation = "do-not-import-yet"
    return {"score": int(score), "recommendation": recommendation, "components": components}


def simulate_dependency_package(
    package: dict,
    projects: dict[str, Path],
    main_root: Path = ROOT,
    smoke_runs: dict[tuple[str, str], dict] | None = None,
    ts_totals: dict[str, int] | None = None,
) -> dict:
    source = str(package.get("source") or "")
    source_root = projects.get(source) or (ROOT / source)
    files = [_normalize_rel(item) for item in package.get("files", []) if item]
    target_path = _target_rel(str(package.get("target_path") or ""))

    missing_source_files = [path for path in files if not _exists(source_root, path)]
    target_conflicts = [target_path] if target_path and _exists(main_root, target_path) else []
    unresolved = [_normalize_rel(item) for item in package.get("unresolved_internal_deps", []) if item]
    external = [_normalize_rel(item) for item in package.get("external_deps", []) if item]

    signals = {
        "missing_source_files": len(missing_source_files),
        "target_conflicts": len(target_conflicts),
        "unresolved_internal_deps": len(unresolved),
        "external_deps": len(external),
        "closure_size": int(package.get("closure_size", 0) or 0),
    }
    decision = _decision_for(package, signals)
    smoke = (smoke_runs or {}).get((source, str(package.get("candidate") or "")))
    ts_total = None if ts_totals is None else int(ts_totals.get(source, 0) or 0)
    confidence = _confidence_components(package, signals, smoke, ts_total)
    required_actions = ["run_static_gate"]
    if package.get("recommended_gate") == "browser_smoke_required":
        required_actions.append("run_browser_smoke")
    if unresolved:
        required_actions.append("resolve_internal_dependency_paths")
    if external:
        required_actions.append("verify_external_packages")
    if target_conflicts:
        required_actions.append("manual_target_conflict_resolution")
    if missing_source_files:
        required_actions.append("repair_source_closure")
    if confidence["recommendation"] == "do-not-import-yet" and "manual_confidence_review" not in required_actions:
        required_actions.append("manual_confidence_review")
    if ts_total is None:
        required_actions.append("bind_typescript_diagnostics_to_source_snapshot")

    return {
        "candidate": package.get("candidate"),
        "source": source,
        "target_path": target_path,
        "package_tier": package.get("package_tier"),
        "decision": decision,
        "import_recommendation": confidence["recommendation"],
        "confidence_score": confidence["score"],
        "confidence_components": confidence["components"],
        "signals": signals,
        "required_actions": required_actions,
        "missing_source_files": missing_source_files[:80],
        "target_conflicts": target_conflicts,
        "unresolved_internal_deps": unresolved[:80],
        "external_deps": external[:80],
        "smoke_route": package.get("smoke_route"),
        "copy_plan": {
            "source_root": str(source_root),
            "target_root": str(main_root),
            "copy_files": files[:80],
            "target_entry": target_path,
        },
    }


def run_merge_simulation_engine() -> dict:
    logger.info("Running static merge simulations from dependency packages...")
    atlas = load_atlas_data()
    atlas_commit = STORE.load_raw("atlas_commit", {})
    projects = resolve_runtime_projects(ROOT)
    package_payload = load_json_file(RAW_DIR / "merge_dependency_packages.json", {})
    smoke_payload = load_json_file(RAW_DIR / "ui_smoke_execution.json", {})
    ts_payload = load_json_file(RAW_DIR / "ts_diagnostics.json", {})
    input_evidence, usable_inputs = evaluate_snapshot_bound_inputs(
        contract_path=CONFIG_DIR / "merge_simulation_input_contract.json",
        raw_dir=RAW_DIR,
        expected_snapshot_id=str(atlas_commit.get("snapshot_id") or ""),
        payloads={
            "merge_dependency_packages": package_payload,
            "ui_smoke_execution": smoke_payload,
            "ts_diagnostics": ts_payload,
        },
    )
    bound_packages = usable_inputs.get("merge_dependency_packages", {})
    packages = bound_packages.get("packages", []) if isinstance(bound_packages, dict) else []
    smoke_runs = _smoke_index(usable_inputs.get("ui_smoke_execution", {}))
    ts_totals = (
        _ts_diagnostics_by_project(usable_inputs["ts_diagnostics"])
        if "ts_diagnostics" in usable_inputs
        else None
    )
    simulations = [
        simulate_dependency_package(package, projects, smoke_runs=smoke_runs, ts_totals=ts_totals)
        for package in packages[:SIMULATION_LIMIT]
        if isinstance(package, dict)
    ]

    decision_counter = Counter(item["decision"] for item in simulations)
    recommendation_counter = Counter(item["import_recommendation"] for item in simulations)
    action_counter = Counter(action for item in simulations for action in item.get("required_actions", []))
    payload = {
        "meta": {"kind": "merge_simulation", "version": "v1", "mode": "static_dry_run"},
        "input_evidence": input_evidence,
        "summary": {
            "simulations": len(simulations),
            "decisions": dict(decision_counter),
            "import_recommendations": dict(recommendation_counter),
            "required_actions": dict(action_counter),
        },
        "simulations": simulations,
    }
    save_json_atomic(RAW_DIR / "merge_simulation.json", payload)
    write_current_atlas_lineage(
        artifact_id="merge_simulation",
        producer="tools.engines.merge_simulation_engine",
        artifact_payload=payload,
        atlas=atlas,
        dependency_payloads={
            artifact_id: usable_inputs.get(artifact_id)
            for artifact_id in ("merge_dependency_packages", "ui_smoke_execution")
            if artifact_id in usable_inputs
        },
    )

    lines = [
        "# Merge Simulation Engine",
        "",
        "Static dry-run over Atlas-backed dependency packages. No target project files are modified.",
        "",
        f"- simulations: `{len(simulations)}`",
        f"- decisions: `{dict(decision_counter)}`",
        f"- import recommendations: `{dict(recommendation_counter)}`",
        f"- input evidence: `{input_evidence['status']}`",
        "",
        "## Top Simulation Decisions",
    ]
    decision_rank = {"DO_NOT_IMPORT_YET": 3, "ASSISTED_IMPORT": 2, "SAFE_TO_IMPORT": 1}
    for item in sorted(simulations, key=lambda row: (decision_rank.get(row["decision"], 0), row["signals"]["closure_size"]), reverse=True)[:80]:
        lines.append(
            f"- `{item['candidate']}` <- {item['source']} | `{item['decision']}` | "
            f"`{item['import_recommendation']}` score `{item['confidence_score']}` | "
            f"files `{item['signals']['closure_size']}` | unresolved `{item['signals']['unresolved_internal_deps']}` | "
            f"missing `{item['signals']['missing_source_files']}` | target `{item.get('target_path') or '-'}`"
        )
    save_text_atomic(REPORTS_DIR / "merge_simulation.md", "\n".join(lines))
    logger.info("Merge simulation artifacts written.")
    return payload


if __name__ == "__main__":
    run_merge_simulation_engine()
