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

from tools.core.config import CONFIG_DIR, DYNAMIC_CONFIG, save_json_atomic

PROJECT_TRUTH_DIR = CONFIG_DIR / "golden" / "projects"
RAW_OUTPUT_PATH = CODE_MAPS_DIR / "output" / ".raw" / "project_truth_sync.json"


def _dead_template(project_key: str) -> dict[str, Any]:
    return {
        "meta": {
            "kind": "dead_code_truth_set",
            "version": "v1",
            "scope": "project",
            "project": project_key,
        },
        "policy": {
            "default_expected": "present",
            "min_enabled_cases": 0,
            "manual_evidence": {
                "enabled": True,
                "max_text_occurrences_for_present_dead": 2,
                "min_text_occurrences_for_absent_guard": 2,
            },
        },
        "cases": [
            {
                "id": f"sample_{project_key.lower()}_dead_case",
                "enabled": False,
                "expected": "present",
                "match": {
                    "project": project_key,
                    "file": "*",
                    "symbol": "SAMPLE_SYMBOL",
                },
                "label": "project_layer_sample",
            }
        ],
    }


def _merge_template(project_key: str) -> dict[str, Any]:
    return {
        "meta": {
            "kind": "merge_candidates_truth_set",
            "version": "v1",
            "scope": "project",
            "project": project_key,
        },
        "policy": {
            "source_artifact": "output/.raw/decision_evidence.json",
            "min_enabled_cases": 0,
        },
        "cases": [
            {
                "id": f"sample_{project_key.lower()}_merge_case",
                "enabled": False,
                "expected": "present",
                "match": {
                    "name": "SampleCandidate",
                    "source": "*",
                    "bucket": "trusted_top_candidates",
                },
                "label": "project_layer_sample",
            }
        ],
    }


def run_sync(create_missing: bool = True, prune_stale: bool = False) -> dict[str, Any]:
    PROJECT_TRUTH_DIR.mkdir(parents=True, exist_ok=True)
    variations = (DYNAMIC_CONFIG.get("variations", {}) or {})
    projects = sorted([str(key).strip() for key in variations.keys() if str(key).strip()])
    active_project_set = set(projects)

    created_files: list[str] = []
    existing_files: list[str] = []
    project_dirs: list[str] = []
    pruned_project_dirs: list[str] = []
    prune_failures: list[dict[str, str]] = []

    for project in projects:
        pdir = PROJECT_TRUTH_DIR / project
        if not pdir.exists():
            pdir.mkdir(parents=True, exist_ok=True)
        project_dirs.append(str(pdir).replace("\\", "/"))

        dead_path = pdir / "dead_code_truth_set.json"
        merge_path = pdir / "merge_candidates_truth_set.json"

        for path, template_factory in (
            (dead_path, _dead_template),
            (merge_path, _merge_template),
        ):
            if path.exists():
                existing_files.append(str(path).replace("\\", "/"))
                continue
            if not create_missing:
                continue
            save_json_atomic(path, template_factory(project))
            created_files.append(str(path).replace("\\", "/"))

    if prune_stale:
        for project_dir in sorted(PROJECT_TRUTH_DIR.iterdir(), key=lambda p: p.name.lower()):
            if not project_dir.is_dir():
                continue
            if project_dir.name in active_project_set:
                continue
            try:
                for nested in sorted(project_dir.rglob("*"), reverse=True):
                    if nested.is_file():
                        nested.unlink(missing_ok=True)
                    elif nested.is_dir():
                        nested.rmdir()
                project_dir.rmdir()
                pruned_project_dirs.append(str(project_dir).replace("\\", "/"))
            except Exception as exc:
                # Leave folder intact on failure and make the skipped cleanup visible.
                prune_failures.append({"project": project_dir.name, "reason": str(exc)})

    payload = {
        "meta": {"kind": "project_truth_sync", "version": "v1"},
        "summary": {
            "projects": len(projects),
            "created_files": len(created_files),
            "existing_files": len(existing_files),
            "pruned_project_dirs": len(pruned_project_dirs),
            "prune_failures": len(prune_failures),
        },
        "projects": projects,
        "created_files": created_files,
        "existing_files": existing_files,
        "pruned_project_dirs": pruned_project_dirs,
        "prune_failures": prune_failures,
    }
    save_json_atomic(RAW_OUTPUT_PATH, payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ensure per-project truth-layer templates exist for all discovered variations."
    )
    parser.add_argument(
        "--no-create",
        action="store_true",
        help="Do not create missing files, only report expected/ existing layers.",
    )
    parser.add_argument(
        "--prune-stale",
        action="store_true",
        help="Delete project truth folders that are not present in active runtime variations.",
    )
    args = parser.parse_args()
    payload = run_sync(create_missing=not args.no_create, prune_stale=args.prune_stale)
    print(json.dumps(payload.get("summary", {}), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
