"""Validate the central risk-scaled SAGE self-development proof selector."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.artifact_validator import ensure_against_schema
from tools.core.config import CONFIG_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.sage_self_development_validation_profiles import (
    assess_independent_evidence,
    select_validation_profile,
    validate_policy,
)

POLICY_PATH = CONFIG_DIR / "sage_self_development_validation_profiles.json"
SCHEMA_PATH = CONFIG_DIR / "schemas" / "sage_self_development_validation_profiles.schema.json"
RAW_PATH = RAW_DIR / "sage_self_development_validation_profile_validation.json"
REPORT_PATH = REPORTS_DIR / "sage_self_development_validation_profile_validation.md"


def _check(check_id: str, ok: bool, details: Any) -> dict[str, Any]:
    return {"id": check_id, "ok": bool(ok), "details": details}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def _acceptance_checks(policy: dict[str, Any]) -> list[dict[str, Any]]:
    checks = []
    for case in policy.get("acceptance_cases", []):
        case_id = str(case.get("id") or "unnamed")
        try:
            result = select_validation_profile(
                case["change"],
                requested_profile=case.get("requested_profile"),
                downgrade_reason=case.get("downgrade_reason"),
                policy=policy,
            )
            expected_downgrade = case.get("expected_downgrade_accepted", "not_asserted")
            ok = result["profile"] == case.get("expected_profile") and (
                expected_downgrade == "not_asserted"
                or result["downgrade"]["accepted"] is expected_downgrade
            )
            details = {
                "expected_profile": case.get("expected_profile"),
                "actual_profile": result["profile"],
                "expected_downgrade_accepted": expected_downgrade,
                "actual_downgrade": result["downgrade"],
            }
        except Exception as exc:
            ok, details = False, str(exc)
        checks.append(_check(f"acceptance_case:{case_id}", ok, details))
    return checks


def _evidence_checks(policy: dict[str, Any]) -> list[dict[str, Any]]:
    base = {
        "command": "python -B tools/validate_example.py",
        "source_identity": "source-a",
        "configuration_identity": "config-a",
        "scope_identity": "scope-a",
        "profile": "bounded",
    }
    duplicate = assess_independent_evidence([base, dict(base)], policy=policy)
    fresh = assess_independent_evidence(
        [base, {**base, "source_identity": "source-b"}],
        policy=policy,
    )
    return [
        _check(
            "exact_evidence_replay_is_not_independent",
            duplicate["status"] == "FAIL"
            and duplicate["independent_records"] == 1
            and len(duplicate["duplicates"]) == 1,
            duplicate,
        ),
        _check(
            "changed_source_identity_invalidates_prior_evidence",
            fresh["status"] == "PASS" and fresh["independent_records"] == 2,
            fresh,
        ),
    ]


def run_validation() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    try:
        policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
        ensure_against_schema(SCHEMA_PATH, "sage_self_development_validation_profiles", policy)
        checks.append(_check("policy_schema_valid", True, "schema ok"))
    except Exception as exc:
        policy = {}
        checks.append(_check("policy_schema_valid", False, str(exc)))

    try:
        validate_policy(policy)
        checks.append(_check("policy_semantics_valid", True, "semantic contract ok"))
    except Exception as exc:
        checks.append(_check("policy_semantics_valid", False, str(exc)))

    if policy:
        checks.extend(_acceptance_checks(policy))
        checks.extend(_evidence_checks(policy))
        required = set(policy["validation"]["required_universal_invariants"])
        gaps = {}
        for profile_id in policy["profiles"]:
            sample = select_validation_profile(
                {
                    "changed_files": [f"tools/{profile_id}.py"],
                    "signals": ["configuration"],
                    "consumer_fan_out": 1,
                    "reversibility": "easy",
                    "release_phase": "development",
                    "behavior_change": True,
                    "scope_kind": "bounded_behavior_change",
                },
                requested_profile=profile_id,
                policy=policy,
            )
            missing = sorted(required - set(sample["universal_invariants"]))
            if missing:
                gaps[profile_id] = missing
        checks.append(_check("universal_invariants_apply_to_every_profile", not gaps, gaps or "complete"))
        proof_runs = {
            key: value["cost_budget"]["full_release_proof_runs"]
            for key, value in policy["profiles"].items()
        }
        checks.append(
            _check(
                "full_release_proof_is_release_profile_only",
                proof_runs == {"micro": 0, "bounded": 0, "critical": 0, "release": 1},
                proof_runs,
            )
        )

    consumers = {
        "development_loop": _read(CONFIG_DIR / "sage_development_loop_contract.json"),
        "agent_router": _read(ROOT / "AGENTS.md"),
        "developer_skill": _read(ROOT / "docs" / "SAGE_DEVELOPER_SKILL.md"),
    }
    policy_token = "sage_self_development_validation_profiles.json"
    selector_token = "select_validation_profile"
    missing_policy = sorted(name for name, text in consumers.items() if policy_token not in text)
    missing_selector = sorted(name for name, text in consumers.items() if selector_token not in text)
    checks.append(
        _check(
            "development_guidance_uses_central_profile_policy",
            not missing_policy and not missing_selector,
            {"missing_policy_reference": missing_policy, "missing_selector_reference": missing_selector},
        )
    )

    failed = [row["id"] for row in checks if not row["ok"]]
    return {
        "meta": {"kind": "sage_self_development_validation_profile_validation", "version": "v1"},
        "status": "PASS" if not failed else "FAIL",
        "summary": {
            "total_checks": len(checks),
            "passed_checks": len(checks) - len(failed),
            "failed_checks": len(failed),
        },
        "checks": checks,
        "failed_checks": failed,
    }


def _render_report(payload: dict[str, Any]) -> str:
    lines = [
        "# SAGE Self-Development Validation Profile Validation",
        "",
        f"- Status: `{payload['status']}`",
        f"- Passed: `{payload['summary']['passed_checks']}`",
        f"- Failed: `{payload['summary']['failed_checks']}`",
        "",
        "| Check | Result | Details |",
        "|---|---|---|",
    ]
    for check in payload["checks"]:
        details = check["details"]
        if not isinstance(details, str):
            details = json.dumps(details, ensure_ascii=False, sort_keys=True)
        details = details.replace("|", "\\|")
        lines.append(f"| `{check['id']}` | {'PASS' if check['ok'] else 'FAIL'} | {details} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    payload = run_validation()
    save_json_atomic(RAW_PATH, payload)
    save_text_atomic(REPORT_PATH, _render_report(payload))
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
