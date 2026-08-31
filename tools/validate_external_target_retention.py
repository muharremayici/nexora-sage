from __future__ import annotations

import json
import sys
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.external_target_retention import (
    DEFAULT_KEEP_PER_PREFIX,
    EXTERNAL_TARGETS_DIR,
    GENERATED_FIXTURE_PREFIXES,
    RETENTION_TARGET_ID,
    external_target_retention_inventory,
    prune_generated_external_target_fixtures,
)


RAW_OUTPUT_PATH = RAW_DIR / "external_target_retention_validation.json"
REPORT_OUTPUT_PATH = REPORTS_DIR / "external_target_retention_validation.md"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check(name: str, passed: bool, details: Any = None) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def validate_external_target_retention() -> dict[str, Any]:
    inventory = external_target_retention_inventory()
    dry_run = prune_generated_external_target_fixtures(keep_per_prefix=DEFAULT_KEEP_PER_PREFIX, dry_run=True)
    probe_name = "_sage_user_scoped_retention_probe"
    probe_dir = EXTERNAL_TARGETS_DIR / probe_name
    probe_preserved = False
    probe_cleanup_ok = True
    prune_probe: dict[str, Any] = {}
    try:
        probe_dir.mkdir(parents=True, exist_ok=True)
        (probe_dir / "marker.txt").write_text("user-scoped retention probe\n", encoding="utf-8")
        prune_probe = prune_generated_external_target_fixtures(keep_per_prefix=DEFAULT_KEEP_PER_PREFIX, dry_run=False)
        probe_preserved = probe_dir.exists() and (probe_dir / "marker.txt").exists()
    finally:
        if probe_dir.exists():
            try:
                shutil.rmtree(probe_dir)
            except OSError:
                probe_cleanup_ok = False
    inventory_after_probe = external_target_retention_inventory()
    checks = [
        _check(
            "external_target_outputs_are_scoped",
            inventory_after_probe.get("policy", {}).get("user_scoped_outputs_pruned") is False
            and inventory_after_probe.get("base_dir", "").replace("\\", "/").endswith("output/external_targets"),
            inventory_after_probe,
        ),
        _check(
            "generated_fixture_prefixes_declared",
            len(GENERATED_FIXTURE_PREFIXES) >= 3
            and all(str(prefix).endswith("_") for prefix in GENERATED_FIXTURE_PREFIXES),
            {"retention_target_id": RETENTION_TARGET_ID, "prefixes": list(GENERATED_FIXTURE_PREFIXES)},
        ),
        _check(
            "retention_policy_keeps_recent_generated_fixtures",
            DEFAULT_KEEP_PER_PREFIX >= 1,
            {"default_keep_per_prefix": DEFAULT_KEEP_PER_PREFIX},
        ),
        _check(
            "retention_dry_run_preserves_user_scoped_outputs",
            dry_run.get("dry_run") is True
            and (dry_run.get("before") or {}).get("user_scoped_dirs") == (dry_run.get("after") or {}).get("user_scoped_dirs"),
            {"before": dry_run.get("before"), "after": dry_run.get("after")},
        ),
        _check(
            "retention_prune_preserves_user_scoped_probe",
            probe_preserved and probe_cleanup_ok and not prune_probe.get("failed"),
            {
                "probe_name": probe_name,
                "probe_preserved": probe_preserved,
                "probe_cleanup_ok": probe_cleanup_ok,
                "prune_selected_for_removal": prune_probe.get("selected_for_removal"),
                "prune_failed": prune_probe.get("failed", []),
            },
        ),
    ]
    status = "PASS" if all(check["passed"] for check in checks) else "FAIL"
    payload = {
        "meta": {
            "kind": "external_target_retention_validation",
            "version": "v1",
            "generated_at": _utc_now(),
            "generator": "tools.validate_external_target_retention",
        },
        "summary": {
            "status": status,
            "checks": len(checks),
            "passed": sum(1 for check in checks if check["passed"]),
            "external_target_dirs": inventory_after_probe.get("total_dirs", 0),
            "generated_fixture_dirs": inventory_after_probe.get("generated_fixture_dirs", 0),
            "user_scoped_dirs": inventory_after_probe.get("user_scoped_dirs", 0),
            "dry_run_removal_candidates": dry_run.get("selected_for_removal", 0),
        },
        "inventory": inventory_after_probe,
        "dry_run": {
            "keep_per_prefix": dry_run.get("keep_per_prefix"),
            "selected_for_removal": dry_run.get("selected_for_removal"),
            "failed": dry_run.get("failed", []),
        },
        "checks": checks,
    }
    save_json_atomic(RAW_OUTPUT_PATH, payload)
    save_text_atomic(REPORT_OUTPUT_PATH, render_report(payload))
    return payload


def render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# External Target Retention Validation",
        "",
        "Checks that generated external-target fixture outputs have a bounded retention policy while user-scoped target outputs are preserved.",
        "",
        f"- status: `{summary.get('status')}`",
        f"- passed: `{summary.get('passed')}/{summary.get('checks')}`",
        f"- external_target_dirs: `{summary.get('external_target_dirs')}`",
        f"- generated_fixture_dirs: `{summary.get('generated_fixture_dirs')}`",
        f"- user_scoped_dirs: `{summary.get('user_scoped_dirs')}`",
        f"- dry_run_removal_candidates: `{summary.get('dry_run_removal_candidates')}`",
        "",
        "| Check | Passed |",
        "|---|---|",
    ]
    for check in payload.get("checks", []):
        lines.append(f"| `{check.get('name')}` | `{check.get('passed')}` |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    payload = validate_external_target_retention()
    print(json.dumps(payload.get("summary", {}), ensure_ascii=False))
    return 0 if payload.get("summary", {}).get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
