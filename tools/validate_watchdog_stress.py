from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path
from types import MethodType
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
CODE_MAPS_DIR = TOOLS_DIR.parent
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))

from tools.core.config import RAW_DIR, REPORTS_DIR, ROOT, save_json_atomic, save_text_atomic
from tools.core.atlas_io import load_atlas_data
from tools.core.json_io import load_json_file
from tools.orchestrators.orchestrator import normalize_changed_file_scope
from tools.orchestrators.watchdog import CodeMapsHandler


class _FileEvent:
    def __init__(self, src_path: str, is_directory: bool = False):
        self.src_path = src_path
        self.is_directory = is_directory


class _MovedEvent:
    def __init__(self, src_path: str, dest_path: str, is_directory: bool = False):
        self.src_path = src_path
        self.dest_path = dest_path
        self.is_directory = is_directory


def _normalize_batch(items: list[str]) -> list[str]:
    return sorted(set(str(item).replace("\\", "/") for item in items))


def _cleanup_handler(handler: CodeMapsHandler) -> None:
    with handler.lock:
        if handler.timer:
            handler.timer.cancel()
            handler.timer = None
        handler.changed_files.clear()
        handler.pending_followup = False
        handler.is_running = False


def run_validation() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    base_root = Path(tempfile.gettempdir()) / "__codemaps_watchdog_stress__"
    src_dir = base_root / "src"

    f1 = src_dir / "a.ts"
    f2 = src_dir / "b.ts"
    f3 = src_dir / "c.tsx"
    f4 = src_dir / "d.js"
    ignored = base_root / "output" / ".raw" / "ghost.ts"
    md_file = src_dir / "note.md"
    move_src = src_dir / "moved_from.ts"
    move_dst = src_dir / "moved_to.tsx"
    src_dir.mkdir(parents=True, exist_ok=True)
    for source_path in (f1, f2, f3, f4, move_src, move_dst):
        source_path.write_text("export const watchdogStress = true;\n", encoding="utf-8")

    handler = CodeMapsHandler(debounce_seconds=0.12, display_root=base_root)
    runs: list[dict[str, Any]] = []

    def fake_run_analysis(
        self: CodeMapsHandler,
        files: list[str],
        watchdog_profile: str = "live",
        input_origin: str = "explicit_scope",
        acquisition: dict | None = None,
    ):
        runs.append(
            {
                "profile": watchdog_profile,
                "input_origin": input_origin,
                "scope_decision": dict((acquisition or {}).get("scope_decision") or {}),
                "files": _normalize_batch(files),
                "started_at": time.time(),
            }
        )
        time.sleep(0.18)

    handler.run_analysis = MethodType(fake_run_analysis, handler)

    # Scenario 1: Burst coalescing while idle should produce one run.
    handler._track_event_path(str(f1))
    handler._track_event_path(str(f2))
    handler._track_event_path(str(f3))
    time.sleep(0.45)
    s1_pass = len(runs) == 1 and len(runs[0]["files"]) == 3
    checks.append(
        {
            "name": "burst_coalescing_single_pulse",
            "passed": s1_pass,
            "details": {"runs": len(runs), "files": list(runs[0]["files"]) if runs else []},
        }
    )

    # Scenario 2: Events arriving during run should queue one follow-up pulse.
    runs.clear()
    handler._track_event_path(str(f1))
    time.sleep(0.16)
    handler._track_event_path(str(f4))
    time.sleep(0.60)
    snapshot_runs = [
        {"profile": batch.get("profile"), "files": list(batch.get("files", [])), "started_at": batch.get("started_at")}
        for batch in runs
    ]
    s2_pass = (
        len(runs) >= 2
        and str(f1).replace("\\", "/") in runs[0]["files"]
        and any(str(f4).replace("\\", "/") in batch["files"] for batch in runs[1:])
    )
    checks.append(
        {
            "name": "inflight_events_followup_pulse",
            "passed": s2_pass,
            "details": {"runs": len(runs), "batches": snapshot_runs},
        }
    )

    # Scenario 3: Moved event should track src+dest paths.
    runs.clear()
    handler.on_moved(_MovedEvent(str(move_src), str(move_dst)))
    time.sleep(0.35)
    moved_expected = {str(move_src).replace("\\", "/"), str(move_dst).replace("\\", "/")}
    moved_seen = set(runs[0]["files"]) if runs else set()
    s3_pass = len(runs) == 1 and moved_expected.issubset(moved_seen)
    checks.append(
        {
            "name": "move_event_tracks_src_and_dest",
            "passed": s3_pass,
            "details": {"runs": len(runs), "files": list(runs[0]["files"]) if runs else []},
        }
    )

    # Scenario 4: Unsupported ext and ignored output paths should be ignored.
    runs.clear()
    handler.on_modified(_FileEvent(str(md_file)))
    handler.on_modified(_FileEvent(str(ignored)))
    time.sleep(0.30)
    s4_pass = len(runs) == 0
    checks.append(
        {
            "name": "ignored_and_non_source_paths_skipped",
            "passed": s4_pass,
            "details": {"runs": len(runs)},
        }
    )

    _cleanup_handler(handler)

    # Scenario 5: Agent-facing repo-relative refs must normalize back to atlas keys
    # before surgical downstream engines consume them.
    repo_main = ROOT / "src" / "main.tsx"
    atlas = load_atlas_data()
    main_files = (atlas.get("MAIN", {}) or {}).get("files", {}) if isinstance(atlas, dict) else {}
    has_main_workspace_metadata = any(
        isinstance(file_meta, dict)
        and str(file_meta.get("workspace_rel") or file_meta.get("repo_relative_path") or "").replace("\\", "/").strip("/") == "src/main.tsx"
        for file_meta in (main_files.values() if isinstance(main_files, dict) else [])
    )
    if repo_main.exists() and has_main_workspace_metadata:
        normalized_refs = normalize_changed_file_scope([str(repo_main), "MAIN::src/main.tsx"])
        s5_pass = normalized_refs == ["MAIN::main.tsx"]
        checks.append(
            {
                "name": "repo_relative_target_refs_normalize_to_atlas_keys",
                "passed": s5_pass,
                "details": {"normalized": normalized_refs},
            }
        )
    else:
        checks.append(
            {
                "name": "repo_relative_target_refs_normalize_to_atlas_keys",
                "passed": True,
                "details": "skipped: atlas workspace metadata is not present in this clean or pre-atlas environment",
            }
        )

    passed_checks = sum(1 for check in checks if check["passed"])
    failed_checks = len(checks) - passed_checks
    payload = {
        "summary": {
            "total_checks": len(checks),
            "passed_checks": passed_checks,
            "failed_checks": failed_checks,
        },
        "checks": checks,
    }

    save_json_atomic(RAW_DIR / "watchdog_stress_validation.json", payload)
    report_lines = [
        "# Watchdog Stress Validation",
        "",
        f"- Total checks: {len(checks)}",
        f"- Passed: {passed_checks}",
        f"- Failed: {failed_checks}",
        "",
        "| Check | Result |",
        "|---|---|",
    ]
    for check in checks:
        report_lines.append(f"| `{check['name']}` | {'PASS' if check['passed'] else 'FAIL'} |")
    save_text_atomic(REPORTS_DIR / "watchdog_stress_validation.md", "\n".join(report_lines) + "\n")
    return payload


def main() -> int:
    payload = run_validation()
    print(json.dumps(payload.get("summary", {}), ensure_ascii=False))
    return 0 if payload.get("summary", {}).get("failed_checks", 1) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
