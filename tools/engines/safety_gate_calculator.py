import json

from tools.core.config import RAW_DIR, REPORTS_DIR, DOCTRINE, save_json_atomic, save_text_atomic
from tools.core.genome_io import load_genome_data
from tools.core.host_intelligence import get_host_studios
from tools.core.json_io import load_json_file
from tools.core.logger import logger
from tools.core.projects_registry import canonical_project_name, project_display_name, studio_for_module_name


def _risk_rows(payload):
    if isinstance(payload, dict):
        rows = payload.get("rows", [])
        return rows if isinstance(rows, list) else []
    return payload if isinstance(payload, list) else []


def _module_name_from_workspace_rel(workspace_rel: str) -> str:
    parts = [p for p in str(workspace_rel or "").replace("\\", "/").split("/") if p]
    if not parts:
        return ""
    if parts[0] == "src":
        parts = parts[1:]
    if not parts:
        return ""
    if parts[0] == "lifecycle-modules" and len(parts) > 1:
        return parts[1]
    return parts[0]


def _scope_key(project: str, module_name: str) -> str:
    return f"{project}::{module_name}"


def _boundary_signal_index():
    genome = load_genome_data()
    index = {}
    if not isinstance(genome, dict):
        return index

    for occs in genome.values():
        for occ in occs:
            module_name = _module_name_from_workspace_rel(occ.get("workspace_rel") or occ.get("file"))
            if not module_name:
                continue
            project = canonical_project_name(occ.get("project", "WORKSPACE"))
            bucket = index.setdefault(
                _scope_key(project, module_name),
                {
                    "ui_dependencies": 0,
                    "dynamic_imports": 0,
                    "architectural_markers": 0,
                    "member_ui_dependencies": 0,
                    "member_dynamic_imports": 0,
                    "member_architectural_markers": 0,
                    "member_side_effect_markers": 0,
                    "member_side_effect_imports": set(),
                    "provider_markers": 0,
                },
            )
            ui_dependencies = occ.get("ui_dependencies") or []
            dynamic_imports = occ.get("dynamic_imports") or []
            architectural_markers = occ.get("architectural_markers") or []
            member_ui_dependencies = occ.get("member_ui_dependencies") or []
            member_dynamic_imports = occ.get("member_dynamic_imports") or []
            member_architectural_markers = occ.get("member_architectural_markers") or []
            member_side_effect_markers = occ.get("member_side_effect_markers") or []
            member_side_effect_imports = occ.get("member_side_effect_imports") or []
            bucket["ui_dependencies"] += len(ui_dependencies)
            bucket["dynamic_imports"] += len(dynamic_imports)
            bucket["architectural_markers"] += len(architectural_markers)
            bucket["member_ui_dependencies"] += len(member_ui_dependencies)
            bucket["member_dynamic_imports"] += len(member_dynamic_imports)
            bucket["member_architectural_markers"] += len(member_architectural_markers)
            bucket["member_side_effect_markers"] += len(member_side_effect_markers)
            bucket["member_side_effect_imports"].update(str(item) for item in member_side_effect_imports if item)
            if "Provider" in architectural_markers:
                bucket["provider_markers"] += 1
            if "Provider" in member_architectural_markers:
                bucket["provider_markers"] += 1
    for bucket in index.values():
        bucket["member_side_effect_imports"] = sorted(bucket.get("member_side_effect_imports", set()))
    return index


def _apply_boundary_penalty(project: str, module_name: str, readiness_score: float, boundary_index: dict):
    signals = boundary_index.get(_scope_key(project, module_name), {})
    ui_count = int(signals.get("ui_dependencies", 0) or 0)
    dynamic_count = int(signals.get("dynamic_imports", 0) or 0)
    member_ui_count = int(signals.get("member_ui_dependencies", 0) or 0)
    member_dynamic_count = int(signals.get("member_dynamic_imports", 0) or 0)
    member_side_effect_count = int(signals.get("member_side_effect_markers", 0) or 0)
    provider_count = int(signals.get("provider_markers", 0) or 0)

    policy_cfg = DOCTRINE.get("safety_gate_policy", {})
    penalties = policy_cfg.get("penalties", {})

    def get_penalty(metric_val, high_key, med_key, default_high_t, default_high_p, default_med_t, default_med_p):
        high_cfg = penalties.get(high_key, {"threshold": default_high_t, "penalty": default_high_p})
        med_cfg = penalties.get(med_key, {"threshold": default_med_t, "penalty": default_med_p})
        if metric_val >= high_cfg.get("threshold", default_high_t):
            return high_cfg.get("penalty", default_high_p)
        elif metric_val >= med_cfg.get("threshold", default_med_t):
            return med_cfg.get("penalty", default_med_p)
        return 0.0

    penalty = 0.0
    penalty += get_penalty(ui_count, "ui_high", "ui_med", 20, 8.0, 8, 4.0)
    penalty += get_penalty(dynamic_count, "dynamic_high", "dynamic_med", 8, 6.0, 3, 3.0)
    penalty += get_penalty(member_ui_count, "member_ui_high", "member_ui_med", 10, 4.0, 3, 2.0)
    penalty += get_penalty(member_dynamic_count, "member_dynamic_high", "member_dynamic_med", 5, 3.0, 2, 1.5)
    penalty += get_penalty(member_side_effect_count, "side_effect_high", "side_effect_med", 12, 5.0, 4, 2.5)
    penalty += get_penalty(provider_count, "provider_high", "provider_med", 5, 4.0, 2, 2.0)

    next_score = max(0.0, readiness_score - penalty)
    return next_score, penalty, signals


def calculate_surgical_readiness():
    logger.info("Calculating Surgical Readiness and Safety Gates...")

    risk = load_json_file(RAW_DIR / "module_risk_matrix.json", [])
    host_studios = get_host_studios()
    boundary_index = _boundary_signal_index()

    readiness_report = []
    for item in _risk_rows(risk):
        module_name = item.get("module", "")
        project = canonical_project_name(item.get("project", "WORKSPACE"))
        if not module_name:
            continue

        risk_score = item.get("risk_score", 100)
        readiness_score = max(0, 100 - risk_score)

        policy = "replace_allowed"
        recommendation = "HEX-READY"

        studio = studio_for_module_name(module_name)
        if studio and studio in host_studios:
            data = host_studios[studio]
            locked_count = data.get("host_summary", {}).get("policy_counts", {}).get("host_locked", 0)
            if locked_count > 5:
                policy = "host_locked"
                readiness_score *= 0.7

        readiness_score, boundary_penalty, boundary_signals = _apply_boundary_penalty(
            project,
            module_name,
            readiness_score,
            boundary_index,
        )
        if boundary_penalty > 0 and policy == "replace_allowed":
            policy = "boundary_sensitive"

        if readiness_score < 40:
            recommendation = "BLOCKED (High Risk)"
        elif readiness_score < 60:
            recommendation = "CAUTION (Review Required)"
        elif policy == "host_locked":
            recommendation = "PROTECTED (Boundary)"
        elif policy == "boundary_sensitive":
            recommendation = "CAUTION (AST Boundary)"

        readiness_report.append(
            {
                "project": project,
                "module": module_name,
                "scoped_module": item.get("scoped_module", _scope_key(project, module_name)),
                "score": round(readiness_score, 1),
                "risk": item.get("risk_level", "UNKNOWN"),
                "violations": item.get("audit_violations", 0),
                "policy": policy,
                "recommendation": recommendation,
                "boundary_penalty": round(boundary_penalty, 1),
                "boundary_signals": boundary_signals,
            }
        )

    readiness_report.sort(key=lambda x: x["score"], reverse=True)

    grouped = {}
    for row in readiness_report:
        project_key = row.get("project", "WORKSPACE")
        grouped.setdefault(project_key, []).append(row)

    payload = {
        "meta": {
            "scope_mode": "project_scoped",
            "project_scoped": True,
        },
        "summary": {
            "total_modules": len(readiness_report),
            "blocked": sum(1 for row in readiness_report if str(row.get("recommendation", "")).startswith("BLOCKED")),
            "caution": sum(1 for row in readiness_report if str(row.get("recommendation", "")).startswith(("CAUTION", "PROTECTED"))),
            "hex_ready": sum(1 for row in readiness_report if str(row.get("recommendation", "")) == "HEX-READY"),
        },
        "rows": readiness_report,
        "by_project": {
            project: {
                "display_name": project_display_name(project) if project != "WORKSPACE" else "Workspace",
                "summary": {
                    "total_modules": len(rows),
                    "blocked": sum(1 for row in rows if str(row.get("recommendation", "")).startswith("BLOCKED")),
                    "caution": sum(1 for row in rows if str(row.get("recommendation", "")).startswith(("CAUTION", "PROTECTED"))),
                    "hex_ready": sum(1 for row in rows if str(row.get("recommendation", "")) == "HEX-READY"),
                },
                "rows": rows,
            }
            for project, rows in sorted(grouped.items())
        },
    }

    save_json_atomic(RAW_DIR / "surgical_readiness.json", payload)

    _generate_md(readiness_report)
    logger.info(f"Surgical Readiness Report refined for {len(readiness_report)} modules.")


def _generate_md(report):
    lines = [
        "# Surgical Readiness and Safety Gates",
        "",
        "Note: This architectural readiness layer incorporates Hexagonal Doctrine and Structural Proofs.",
        "",
        "| Project | Module | Score | Risk | Violations | Policy | Recommendation |",
        "|---|---|---:|---|---:|---|---|",
    ]

    for r in report:
        recommendation = str(r.get("recommendation", ""))
        if recommendation.startswith("BLOCKED"):
            badge = "BLOCKED"
        elif recommendation.startswith("CAUTION") or recommendation.startswith("PROTECTED"):
            badge = "CAUTION"
        else:
            badge = "HEX-READY"
        lines.append(
            f"| `{r['project']}` | {badge} `{r['module']}` | **{r['score']}** | {r['risk']} | "
            f"{r['violations']} | {r['policy']} | {r['recommendation']} |"
        )

    save_text_atomic(REPORTS_DIR / "surgical_readiness_report.md", "\n".join(lines))
if __name__ == "__main__":
    calculate_surgical_readiness()
