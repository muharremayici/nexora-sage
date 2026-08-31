from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
_LOCAL_ROOT = TOOLS_DIR.parent
if str(_LOCAL_ROOT) not in sys.path:
    sys.path.insert(0, str(_LOCAL_ROOT))

from tools.core.config import CODE_MAPS_DIR, OUTPUT_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic
from tools.core.subprocess_telemetry import run_observed_subprocess


@dataclass
class IsolationCheck:
    step: str
    passed: bool
    changed_files: list[str]
    unexpected_files: list[str]


TRACKED_FILES = {
    "atlas": RAW_DIR / "atlas.json",
    "genome": RAW_DIR / "genome.json",
    "dead_code_raw": RAW_DIR / "dead_code.json",
    "dead_code_report": REPORTS_DIR / "dead_code_report.md",
    "quality_gate_raw": RAW_DIR / "quality_gate.json",
    "quality_gate_report": REPORTS_DIR / "quality_gate.md",
    "audit_raw": RAW_DIR / "audit_report.json",
    "audit_report": REPORTS_DIR / "audit_report.txt",
    "health_raw": RAW_DIR / "health_score.json",
    "ai_context_raw": RAW_DIR / "ai_context.json",
}


def _sha(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _snapshot() -> dict[str, str | None]:
    return {name: _sha(path) for name, path in TRACKED_FILES.items()}


def _run_step(step_slug: str) -> None:
    from tools.core.artifact_store import get_adaptive_timeout

    print(f"[step-isolation] START {step_slug}", flush=True)
    result, duration = run_observed_subprocess(
        ["python", ".\\sage.py", "run", "--step", step_slug],
        cwd=CODE_MAPS_DIR,
        label=f"step_isolation_{step_slug}",
        timeout=get_adaptive_timeout(180),
        heartbeat_seconds=10,
        log=lambda message: print(message, flush=True),
    )
    print(
        f"[step-isolation] {step_slug} rc={result.returncode} "
        f"duration_seconds={duration:.3f}",
        flush=True,
    )
    if result.returncode != 0:
        output = "\n".join(part for part in [result.stdout.strip(), result.stderr.strip()] if part)
        raise RuntimeError(output or f"step {step_slug} failed with {result.returncode}")


def _diff(before: dict[str, str | None], after: dict[str, str | None]) -> list[str]:
    changed = []
    for name in TRACKED_FILES:
        if before.get(name) != after.get(name):
            changed.append(name)
    return changed


def run_validation() -> dict:
    checks: list[IsolationCheck] = []

    cases = [
        ("qualitygates", {"quality_gate_raw", "quality_gate_report"}),
        ("deadcode", {"dead_code_raw", "dead_code_report"}),
        # AI context run may refresh dead-code artifact via dependency-aware orchestration.
        ("aicontextgenerator", {"ai_context_raw", "dead_code_raw"}),
    ]

    for step_slug, allowed_changes in cases:
        before = _snapshot()
        _run_step(step_slug)
        after = _snapshot()
        changed = _diff(before, after)
        unexpected = [name for name in changed if name not in allowed_changes]
        checks.append(
            IsolationCheck(
                step=step_slug,
                passed=len(unexpected) == 0,
                changed_files=changed,
                unexpected_files=unexpected,
            )
        )

    payload = {
        "meta": {
            "kind": "step_isolation_validation",
            "version": "v1",
        },
        "summary": {
            "total_checks": len(checks),
            "passed_checks": sum(1 for check in checks if check.passed),
            "failed_checks": sum(1 for check in checks if not check.passed),
        },
        "checks": [check.__dict__ for check in checks],
    }
    return payload


def main() -> int:
    payload = run_validation()
    out_path = RAW_DIR / "step_isolation_validation.json"
    save_json_atomic(out_path, payload)
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["failed_checks"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
