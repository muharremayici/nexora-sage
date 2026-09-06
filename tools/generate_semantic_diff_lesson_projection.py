from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.core.config import CODE_MAPS_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.lesson_projection import project_impacted_lessons
from tools.core.artifact_store import get_adaptive_timeout
from tools.core.work_package_receipts import record_work_package_operation_safely


LOOP_PATH = CODE_MAPS_DIR / "config" / "sage_development_loop_contract.json"
LESSON_PATH = CODE_MAPS_DIR / "config" / "audit_lesson_registry.json"


def _run_git(args: list[str], *, timeout_seconds: int) -> list[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=CODE_MAPS_DIR,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed with {result.returncode}")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def changed_files_from_git(*, timeout_seconds: int) -> list[str]:
    tracked = _run_git(["diff", "--name-only", "--relative", "HEAD", "--", "."], timeout_seconds=timeout_seconds)
    untracked = _run_git(["ls-files", "--others", "--exclude-standard", "--", "."], timeout_seconds=timeout_seconds)
    return sorted(set(tracked + untracked))


def _contract(loop: dict) -> dict:
    return (
        loop.get("semantic_diff_review", {})
        .get("lesson_application_levels", {})
        .get("impacted", {})
        .get("projection_contract", {})
    )


def generate_projection(changed_files: list[str], failure_families: list[str]) -> dict:
    loop = json.loads(LOOP_PATH.read_text(encoding="utf-8"))
    registry = json.loads(LESSON_PATH.read_text(encoding="utf-8"))
    lessons = [row for row in registry.get("lessons", []) if isinstance(row, dict)]
    payload = project_impacted_lessons(
        lessons,
        changed_files=changed_files,
        failure_families=failure_families,
        contract=_contract(loop),
    )
    save_json_atomic(RAW_DIR / "semantic_diff_impacted_lesson_projection.json", payload)
    save_text_atomic(REPORTS_DIR / "semantic_diff_impacted_lesson_projection.md", render_report(payload))
    return payload


def render_report(payload: dict) -> str:
    summary = payload["summary"]
    lines = [
        "# Semantic Diff Impacted Lesson Projection",
        "",
        f"- status: `{summary['status']}`",
        f"- changed files: `{summary['changed_files']}`",
        f"- matched lessons: `{summary['matched_lessons']}`",
        f"- shown lessons: `{summary['shown_lessons']}`",
        f"- omitted lessons: `{summary['omitted_lessons']}`",
        f"- unmatched changed files: `{summary['unmatched_changed_files']}`",
        f"- unknown source-layer files: `{summary['unknown_source_layer_files']}`",
        f"- unmatched failure families: `{summary['unmatched_failure_families']}`",
        "",
        "## Lessons",
        "",
    ]
    for row in payload.get("lessons", []):
        lines.append(f"- `{row.get('priority')} {row.get('id')}` via `{', '.join(row.get('matched_strong_signals', []))}`")
    lines.extend(["", "## Unmatched Changed Files", ""])
    for path in payload.get("unmatched_changed_files", []):
        lines.append(f"- `{path}`")
    lines.extend(["", "## Unknown Source-Layer Files", ""])
    for path in payload.get("unknown_source_layer_files", []):
        lines.append(f"- `{path}`")
    lines.extend(["", "## Unmatched Failure Families", ""])
    for family in payload.get("unmatched_failure_families", []):
        lines.append(f"- `{family}`")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    started = time.perf_counter()
    parser = argparse.ArgumentParser(description="Project impacted SAGE lessons for a semantic diff review.")
    parser.add_argument("--changed-file", action="append", default=[])
    parser.add_argument("--failure-family", action="append", default=[])
    args = parser.parse_args()
    try:
        loop = json.loads(LOOP_PATH.read_text(encoding="utf-8"))
        contract = _contract(loop)
        baseline_timeout = int(contract.get("git_discovery_timeout_seconds", 0) or 0)
        if baseline_timeout < 1:
            raise RuntimeError("missing_git_discovery_timeout_seconds")
        changed_files = args.changed_file or changed_files_from_git(
            timeout_seconds=get_adaptive_timeout(baseline_timeout)
        )
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        print(json.dumps({"status": "FAIL", "reason": "git_changed_file_discovery_failed", "error": str(exc)}, indent=2))
        return 1
    payload = generate_projection(changed_files, args.failure_family)
    record_work_package_operation_safely(
        operation_id="semantic_diff_review",
        result_status=payload["summary"]["status"],
        started=started,
        evidence_artifact="output/.raw/semantic_diff_impacted_lesson_projection.json",
    )
    print(json.dumps(payload["summary"], indent=2))
    return 0 if payload["summary"]["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
