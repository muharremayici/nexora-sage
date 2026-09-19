from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.core.config import CODE_MAPS_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.external_target_generation import (
    external_target_generation_history,
    resolve_current_external_target_generation,
    resolve_external_target_artifact_dir,
)
from tools.core.external_target_retention import generation_history_limits
from tools.core.json_io import load_json_file


EXTERNAL_TARGETS_DIR = CODE_MAPS_DIR / "output" / "external_targets"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_summary(run_dir: Path) -> dict[str, Any]:
    preflight = load_json_file(run_dir / ".raw" / "external_target_preflight.json", {})
    capability = load_json_file(run_dir / ".raw" / "react_capability_probe.json", {})
    return {
        "preflight": preflight.get("summary", {}) if isinstance(preflight, dict) else {},
        "capability_probe": capability.get("summary", {}) if isinstance(capability, dict) else {},
        "workspace_root": capability.get("workspace_root") if isinstance(capability, dict) else None,
    }


def build_index() -> dict[str, Any]:
    rows = []
    max_manifest_scan, max_history_entries = generation_history_limits()
    if EXTERNAL_TARGETS_DIR.exists():
        for run_dir in sorted(EXTERNAL_TARGETS_DIR.iterdir(), key=lambda p: p.name.lower()):
            if not run_dir.is_dir():
                continue
            current_dir, pointer, current_reason = resolve_current_external_target_generation(run_dir)
            summary_dir, artifact_reason = resolve_external_target_artifact_dir(run_dir)
            generation_history = external_target_generation_history(
                run_dir,
                max_manifest_scan=max_manifest_scan,
                max_history_entries=max_history_entries,
            )
            stat_source = summary_dir if summary_dir.exists() else run_dir
            stat = stat_source.stat()
            rows.append(
                {
                    "slug": run_dir.name,
                    "path": str(run_dir),
                    "current_status": "VALIDATED" if current_dir else "UNVALIDATED_OR_LEGACY",
                    "current_run_id": pointer.get("run_id") if pointer else None,
                    "current_reason": current_reason,
                    "artifact_resolution": artifact_reason,
                    "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                    "summary": _load_summary(summary_dir),
                    "generation_history": generation_history,
                }
            )
    return {
        "meta": {
            "kind": "external_target_index",
            "version": "v1",
            "generated_at": _utc_now(),
            "generator": "tools.generate_external_target_index",
        },
        "summary": {
            "targets": len(rows),
            "with_preflight": sum(1 for row in rows if row["summary"].get("preflight")),
            "with_capability_probe": sum(1 for row in rows if row["summary"].get("capability_probe")),
            "validated_current": sum(1 for row in rows if row["current_status"] == "VALIDATED"),
            "generation_dirs": sum(
                int(row["generation_history"].get("total_generation_dirs") or 0)
                for row in rows
            ),
            "generation_manifests_scanned": sum(
                int(row["generation_history"].get("scanned_generation_dirs") or 0)
                for row in rows
            ),
            "generation_scan_omitted": sum(
                int(row["generation_history"].get("omitted_from_scan") or 0)
                for row in rows
            ),
            "invalid_generation_manifests": sum(
                int(row["generation_history"].get("invalid_manifests") or 0)
                for row in rows
            ),
        },
        "history_policy": {
            "max_manifest_scan_per_target": max_manifest_scan,
            "max_history_entries_per_target": max_history_entries,
            "user_scoped_generations_pruned": False,
        },
        "targets": rows,
    }


def render_report(index: dict[str, Any]) -> str:
    summary = index.get("summary", {})
    lines = [
        "# External Target Runs",
        "",
        f"- targets: `{summary.get('targets')}`",
        f"- with_preflight: `{summary.get('with_preflight')}`",
        f"- with_capability_probe: `{summary.get('with_capability_probe')}`",
        f"- validated_current: `{summary.get('validated_current')}`",
        f"- generation_dirs: `{summary.get('generation_dirs')}`",
        f"- generation_manifests_scanned: `{summary.get('generation_manifests_scanned')}`",
        f"- generation_scan_omitted: `{summary.get('generation_scan_omitted')}`",
        f"- invalid_generation_manifests: `{summary.get('invalid_generation_manifests')}`",
        "",
        "| Target | Current | Current Run | Latest Attempt | Latest Completed | Latest Validated | History | Workspace Root | Preflight | Repo Present | Declared Detected | Modified |",
        "|---|---|---|---|---|---|---:|---|---|---:|---:|---|",
    ]
    for row in index.get("targets", []):
        summary_row = row.get("summary", {})
        preflight = summary_row.get("preflight", {})
        capability = summary_row.get("capability_probe", {})
        history = row.get("generation_history", {})
        roles = history.get("roles", {})
        lines.append(
            f"| `{row.get('slug')}` | `{row.get('current_status')}` | `{roles.get('validated_current')}` | `{roles.get('latest_attempt')}` | `{roles.get('latest_completed')}` | `{roles.get('latest_validated')}` | {len(history.get('history', []))} | `{summary_row.get('workspace_root')}` | `{preflight.get('status')}` | {capability.get('repo_present', 0)} | {capability.get('declared_detected', 0)} | `{row.get('modified_at')}` |"
        )
    return "\n".join(lines) + "\n"


def run() -> dict[str, Any]:
    index = build_index()
    save_json_atomic(EXTERNAL_TARGETS_DIR / "index.json", index)
    save_text_atomic(REPORTS_DIR / "external_target_runs.md", render_report(index))
    return index


def main() -> int:
    index = run()
    print(json.dumps(index.get("summary", {}), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
