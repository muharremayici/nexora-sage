from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
CODE_MAPS_DIR = TOOLS_DIR.parent
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))

from tools.core.config import RAW_DIR, save_json_atomic
from tools.core.json_io import load_json_file


def _resolve_dest(path_text: str) -> Path:
    p = Path(path_text)
    if p.is_absolute():
        return p
    return (CODE_MAPS_DIR / p).resolve()


def _fixture_validation(summary: dict[str, Any]) -> dict[str, Any]:
    failed = 0
    checks: list[dict[str, Any]] = []
    repo_present = int(summary.get("repo_present", 0) or 0)
    detected = int(summary.get("detected", 0) or 0)
    partial = int(summary.get("partial", 0) or 0)
    missing = int(summary.get("missing", 0) or 0)

    checks.append(
        {
            "name": "project_summary_consistency",
            "passed": detected <= repo_present and partial >= 0 and missing >= 0,
            "details": f"repo_present={repo_present} detected={detected} partial={partial} missing={missing}",
        }
    )
    checks.append(
        {
            "name": "detected_equals_present",
            "passed": detected == repo_present,
            "details": f"detected={detected} repo_present={repo_present}",
        }
    )
    checks.append(
        {
            "name": "partial_is_zero",
            "passed": partial == 0,
            "details": f"partial={partial}",
        }
    )
    checks.append(
        {
            "name": "missing_is_zero",
            "passed": missing == 0,
            "details": f"missing={missing}",
        }
    )
    failed = sum(1 for check in checks if not check["passed"])
    return {
        "summary": {
            "total_checks": len(checks),
            "passed_checks": len(checks) - failed,
            "failed_checks": failed,
        },
        "checks": checks,
    }


def run(project: str, dest_root: str) -> dict[str, Any]:
    matrix = load_json_file(RAW_DIR / "react_support_matrix.json", {})
    by_project = matrix.get("by_project", {}) if isinstance(matrix, dict) else {}
    project_payload = by_project.get(project) if isinstance(by_project, dict) else None
    if not isinstance(project_payload, dict):
        return {"ok": False, "reason": "project_not_found", "project": project}

    summary = project_payload.get("summary", {}) if isinstance(project_payload, dict) else {}
    capabilities = project_payload.get("capabilities", []) if isinstance(project_payload, dict) else []
    confidence = project_payload.get("confidence", {}) if isinstance(project_payload, dict) else {}
    role = str(project_payload.get("role") or "")
    source_file_count = int(project_payload.get("source_file_count", 0) or 0)

    fixture_matrix = {
        "workspace_root": f"fixture::{project}",
        "workspace_mode": "single_project_fixture",
        "source_file_count": source_file_count,
        "summary": summary,
        "confidence": confidence,
        "capabilities": capabilities,
        "by_project": {project: project_payload},
        "backlog": [],
        "fixture_meta": {
            "source_workspace_mode": matrix.get("workspace_mode", "unknown"),
            "source_project": project,
            "source_project_role": role,
            "kind": "react_fixture_seed",
            "version": "v1",
        },
    }

    fixture_validation = _fixture_validation(summary if isinstance(summary, dict) else {})

    target_root = _resolve_dest(dest_root)
    target_root.mkdir(parents=True, exist_ok=True)
    save_json_atomic(target_root / "react_support_matrix.json", fixture_matrix)
    save_json_atomic(target_root / "react_support_validation.json", fixture_validation)

    manifest = {
        "kind": "react_fixture_seed_manifest",
        "version": "v1",
        "project": project,
        "target_root": str(target_root),
        "summary": summary,
    }
    save_json_atomic(target_root / "fixture_seed_manifest.json", manifest)

    return {
        "ok": True,
        "project": project,
        "target_root": str(target_root),
        "summary": summary,
        "validation_summary": fixture_validation.get("summary", {}),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate single-project React fixture artifacts from current workspace matrix.")
    parser.add_argument("--project", required=True, help="Project key in output/.raw/react_support_matrix.json by_project")
    parser.add_argument("--dest-root", required=True, help="Destination directory for fixture artifacts")
    args = parser.parse_args()

    payload = run(project=args.project, dest_root=args.dest_root)
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
