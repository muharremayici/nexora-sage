"""
Module Risk Matrix generates per-module risk cards for each lifecycle module.
Aggregates dead code, coupling, audit violations, and circular deps per module.
"""

import json
from collections import defaultdict

from tools.core.audit_report import get_violations
from tools.core.artifact_validator import ensure_valid_payload
from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file
from tools.core.logger import logger
from tools.core.module_policy import classify_module_path, get_dynamic_modules
from tools.core.projects_registry import canonical_project_name, project_display_name


def _scope_key(project: str, module_name: str) -> str:
    return f"{project}::{module_name}"


def _parse_cycle_node(node: str) -> tuple[str, str]:
    raw = str(node or "")
    if "::" in raw:
        project, path = raw.split("::", 1)
        return canonical_project_name(project), path
    return "WORKSPACE", raw


def generate_module_risk_matrix():
    logger.info("Building module risk matrix...")

    lifecycle_modules = get_dynamic_modules()
    dead_payload = load_json_file(RAW_DIR / "dead_code.json", [])
    dead_items = dead_payload.get("items", []) if isinstance(dead_payload, dict) else dead_payload
    surgical = load_json_file(RAW_DIR / "surgical_discovery.json", [])
    circ_data = load_json_file(RAW_DIR / "circular_deps.json", {})
    audit_violations = get_violations()

    modules = defaultdict(lambda: {
        "project": "WORKSPACE",
        "module": "other",
        "files": set(),
        "dead_files": set(),
        "coupling_scores": [],
        "circular_deps": 0,
        "audit_violations": 0,
    })

    for item in dead_items:
        project = canonical_project_name(item.get("project", "WORKSPACE"))
        file_path = item.get("file", "")
        module_name = classify_module_path(file_path, lifecycle_modules)
        bucket = modules[_scope_key(project, module_name)]
        bucket["project"] = project
        bucket["module"] = module_name
        bucket["dead_files"].add(file_path)

    for item in surgical:
        project = canonical_project_name(item.get("project", "WORKSPACE"))
        file_path = item.get("file", "")
        module_name = classify_module_path(file_path, lifecycle_modules)
        bucket = modules[_scope_key(project, module_name)]
        bucket["project"] = project
        bucket["module"] = module_name
        bucket["files"].add(file_path)
        bucket["coupling_scores"].append(item.get("coupling", 0))

    for cycle in circ_data.get("cycles", []):
        for node in cycle.get("chain", []):
            project, path = _parse_cycle_node(node)
            module_name = classify_module_path(path, lifecycle_modules)
            bucket = modules[_scope_key(project, module_name)]
            bucket["project"] = project
            bucket["module"] = module_name
            bucket["circular_deps"] += 1

    for violation in audit_violations:
        project = canonical_project_name(violation.get("project", "WORKSPACE"))
        file_path = violation.get("file", "")
        module_name = classify_module_path(file_path, lifecycle_modules)
        bucket = modules[_scope_key(project, module_name)]
        bucket["project"] = project
        bucket["module"] = module_name
        bucket["audit_violations"] += 1

    results = []
    for scope_name, data in sorted(modules.items()):
        project = data["project"]
        module_name = data["module"]
        total_file_set = set(data["files"] or set()) | set(data["dead_files"] or set())
        total_files = len(total_file_set)
        dead_code = len(data["dead_files"] or set())
        if total_files == 0 and dead_code == 0:
            continue

        total_files = max(total_files, 1)
        dead_pct = round(dead_code / total_files * 100, 1)
        avg_coupling = round(sum(data["coupling_scores"]) / len(data["coupling_scores"]), 2) if data["coupling_scores"] else 0

        risk = 0
        risk += min(40, dead_pct * 0.8)
        risk += min(30, avg_coupling * 5)
        risk += min(15, data["circular_deps"] * 5)
        risk += min(15, data["audit_violations"] * 3)
        risk_level = "LOW" if risk < 25 else "MEDIUM" if risk < 50 else "HIGH"

        results.append({
            "project": project,
            "module": module_name,
            "scoped_module": scope_name,
            "files": total_files,
            "dead_code": dead_code,
            "dead_pct": dead_pct,
            "avg_coupling": avg_coupling,
            "circular_deps": data["circular_deps"],
            "audit_violations": data["audit_violations"],
            "risk_score": round(risk, 1),
            "risk_level": risk_level,
        })

    results.sort(key=lambda item: item["risk_score"], reverse=True)

    by_project = defaultdict(list)
    for row in results:
        by_project[row.get("project", "WORKSPACE")].append(row)

    payload = {
        "meta": {
            "scope_mode": "project_scoped",
            "project_scoped": True,
        },
        "summary": {
            "total_modules": len(results),
            "high_risk": sum(1 for row in results if row["risk_level"] == "HIGH"),
            "medium_risk": sum(1 for row in results if row["risk_level"] == "MEDIUM"),
            "low_risk": sum(1 for row in results if row["risk_level"] == "LOW"),
        },
        "rows": results,
        "by_project": {
            project: {
                "display_name": project_display_name(project) if project != "WORKSPACE" else "Workspace",
                "summary": {
                    "total_modules": len(rows),
                    "high_risk": sum(1 for row in rows if row["risk_level"] == "HIGH"),
                    "medium_risk": sum(1 for row in rows if row["risk_level"] == "MEDIUM"),
                    "low_risk": sum(1 for row in rows if row["risk_level"] == "LOW"),
                },
                "rows": rows,
            }
            for project, rows in sorted(by_project.items())
        },
    }

    ensure_valid_payload("module_risk_matrix", payload)

    json_path = RAW_DIR / "module_risk_matrix.json"
    save_json_atomic(json_path, payload)

    md_lines = [
        "# Module Risk Matrix",
        "",
        "| Risk | Project | Module | Files | Dead Code | Dead% | Avg Coupling | Cycles | Audit | Score |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in results:
        md_lines.append(
            f"| {row['risk_level']} | `{row['project']}` | **{row['module']}** | {row['files']} | {row['dead_code']} | "
            f"{row['dead_pct']}% | {row['avg_coupling']} | {row['circular_deps']} | "
            f"{row['audit_violations']} | {row['risk_score']} |"
        )

    md_lines.extend(["", "## By Project", ""])
    for project, rows in sorted(by_project.items(), key=lambda item: (item[0] != "MAIN", item[0])):
        project_label = project_display_name(project) if project != "WORKSPACE" else "Workspace"
        md_lines.append(f"### {project_label} [{project}] ({len(rows)})")
        md_lines.append("| Risk | Module | Files | Dead Code | Dead% | Avg Coupling | Cycles | Audit | Score |")
        md_lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|")
        for row in rows[:80]:
            md_lines.append(
                f"| {row['risk_level']} | **{row['module']}** | {row['files']} | {row['dead_code']} | "
                f"{row['dead_pct']}% | {row['avg_coupling']} | {row['circular_deps']} | "
                f"{row['audit_violations']} | {row['risk_score']} |"
            )
        if len(rows) > 80:
            md_lines.append(f"| ... | *and {len(rows) - 80} more* | | | | | | |")
        md_lines.append("")

    md_path = REPORTS_DIR / "module_risk_matrix.md"
    save_text_atomic(md_path, "\n".join(md_lines))

    logger.info(f"Module Risk Matrix: {len(results)} modules analyzed")
    logger.info(f"Report saved to {md_path}")
    return payload
if __name__ == "__main__":
    generate_module_risk_matrix()
