from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
CODE_MAPS_DIR = TOOLS_DIR.parent
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))

from tools.core.json_io import load_json_file
from tools.core.config import CONFIG_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.evidence_status import evidence_failed_checks


def _failed_checks(path: Path) -> int | None:
    payload = load_json_file(path, {})
    return evidence_failed_checks(payload)


def _exists(path: Path) -> bool:
    return path.exists() and path.is_file()


def _clean_machine_installation_verdict(transcript: str) -> str:
    installation_marker = re.search(
        r"(?mi)^\s*CLEAN_MACHINE_INSTALLATION_VERDICT:\s*(PASS|FAIL|PENDING)\s*$",
        transcript,
    )
    if installation_marker:
        return installation_marker.group(1).upper()
    compatibility_marker = re.search(
        r"(?mi)^\s*CLEAN_MACHINE_VERDICT:\s*(PASS|FAIL|PENDING)\s*$",
        transcript,
    )
    return compatibility_marker.group(1).upper() if compatibility_marker else "MISSING"


def run_status() -> dict[str, Any]:
    lifecycle_failed = _failed_checks(RAW_DIR / "lifecycle_validation.json")
    entry_failed = _failed_checks(RAW_DIR / "entrypoints_failures_validation.json")
    if entry_failed is None:
        entry_failed = _failed_checks(RAW_DIR / "entrypoint_failure_validation.json")
    react_failed = _failed_checks(RAW_DIR / "react_support_validation.json")
    fixture_failed = _failed_checks(RAW_DIR / "react_fixture_matrix_validation.json")
    perf_failed = _failed_checks(RAW_DIR / "performance_budget_validation.json")
    dist_failed = _failed_checks(RAW_DIR / "distribution_hardening_validation.json")
    universal_failed = _failed_checks(RAW_DIR / "universal_proof_validation.json")
    stale_failed = _failed_checks(RAW_DIR / "stale_remediation_validation.json")
    clean_machine_transcript = REPORTS_DIR / "clean_machine_setup_transcript.md"
    clean_machine_text = clean_machine_transcript.read_text(encoding="utf-8", errors="replace") if clean_machine_transcript.exists() else ""
    clean_machine_verdict = _clean_machine_installation_verdict(clean_machine_text)
    clean_machine_pass = clean_machine_verdict == "PASS"
    clean_machine_reason = (
        f"distribution_hardening_failed={dist_failed}; clean_machine_installation_verdict={clean_machine_verdict}; "
        f"transcript={clean_machine_transcript}; required_marker=CLEAN_MACHINE_INSTALLATION_VERDICT: PASS "
        f"(legacy CLEAN_MACHINE_VERDICT accepted)"
    )

    ledger = load_json_file(RAW_DIR / "performance_ledger.json", {})
    ledger_runs = ledger.get("runs", []) if isinstance(ledger, dict) else []
    ledger_count = len(ledger_runs) if isinstance(ledger_runs, list) else 0

    discovery = load_json_file(CONFIG_DIR / "codemaps.discovery.json", {})
    det = (
        (discovery.get("_discovery_metadata") or {}).get("determinism_overview")
        if isinstance(discovery, dict)
        else {}
    )
    has_det = (
        isinstance(det, dict)
        and int(det.get("total_projects", 0) or 0) > 0
        and isinstance(det.get("bundler_primary"), dict)
    )

    phases: list[dict[str, Any]] = []
    phases.append(
        {
            "phase": 0,
            "scope": "Baseline lock",
            "status": "done" if lifecycle_failed == 0 and entry_failed == 0 else "in_progress",
            "reason": f"lifecycle_failed={lifecycle_failed} entry_failed={entry_failed}",
        }
    )
    phases.append(
        {
            "phase": 1,
            "scope": "Contract/docs sync",
            "status": "done" if dist_failed == 0 else "in_progress",
            "reason": f"distribution_hardening_failed={dist_failed}",
        }
    )
    phases.append(
        {
            "phase": 2,
            "scope": "Discovery determinism",
            "status": "done" if has_det and _exists(REPORTS_DIR / "discovery_determinism.md") else "in_progress",
            "reason": f"has_determinism={has_det}",
        }
    )
    phases.append(
        {
            "phase": 3,
            "scope": "AST React completeness",
            "status": "done" if react_failed == 0 and fixture_failed == 0 else "in_progress",
            "reason": f"react_failed={react_failed} fixture_failed={fixture_failed}",
        }
    )
    phases.append(
        {
            "phase": 4,
            "scope": "Stale remediation",
            "status": "done" if stale_failed == 0 else "in_progress",
            "reason": f"stale_remediation_failed={stale_failed}",
        }
    )
    phases.append(
        {
            "phase": 5,
            "scope": "Performance budget",
            "status": "done" if perf_failed == 0 and ledger_count > 0 else "in_progress",
            "reason": f"perf_failed={perf_failed} ledger_runs={ledger_count}",
        }
    )
    phases.append(
        {
            "phase": 6,
            "scope": "Distribution hardening",
            "status": "done" if dist_failed == 0 and clean_machine_pass else ("in_progress" if dist_failed == 0 else "blocked"),
            "reason": clean_machine_reason,
        }
    )
    phases.append(
        {
            "phase": 7,
            "scope": "Universal proof run",
            "status": "done" if universal_failed == 0 else "in_progress",
            "reason": f"universal_proof_failed={universal_failed}",
        }
    )

    summary = {
        "total_phases": len(phases),
        "done": sum(1 for p in phases if p["status"] == "done"),
        "in_progress": sum(1 for p in phases if p["status"] == "in_progress"),
        "blocked": sum(1 for p in phases if p["status"] == "blocked"),
    }

    payload = {
        "meta": {"kind": "release_phase_status", "version": "v1"},
        "summary": summary,
        "phases": phases,
    }
    save_json_atomic(RAW_DIR / "release_phase_status.json", payload)

    lines = [
        "# Release Phase Status",
        "",
        f"- Total phases: `{summary['total_phases']}`",
        f"- Done: `{summary['done']}`",
        f"- In progress: `{summary['in_progress']}`",
        f"- Blocked: `{summary['blocked']}`",
        "",
        "| Phase | Scope | Status | Reason |",
        "|---|---|---|---|",
    ]
    for row in phases:
        lines.append(
            f"| `{row['phase']}` | {row['scope']} | `{row['status']}` | {row['reason']} |"
        )
    save_text_atomic(REPORTS_DIR / "release_phase_status.md", "\n".join(lines) + "\n")
    return payload


def main() -> int:
    payload = run_status()
    print(json.dumps(payload.get("summary", {}), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

