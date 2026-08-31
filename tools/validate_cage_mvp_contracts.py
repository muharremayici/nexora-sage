from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.capability_registry import get_capability, load_capability_registry
from tools.core.config import CODE_MAPS_DIR, CONFIG_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file
from tools.core.roadmap_phase_registry import current_product_release, load_roadmap_phase_registry

CONTRACT_PATH = CONFIG_DIR / "cage_mvp_contracts.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check(name: str, passed: bool, details: Any = None) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _load_contract() -> dict[str, Any]:
    payload = load_json_file(CONTRACT_PATH, {})
    return payload if isinstance(payload, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _artifact_path(artifact: str) -> Path:
    return RAW_DIR / f"{artifact}.json"


def _policy_path(policy: str) -> Path:
    return (CODE_MAPS_DIR / policy).resolve()


def validate_cage_mvp_contracts() -> dict[str, Any]:
    contract = _load_contract()
    phase_registry = load_roadmap_phase_registry()
    active_release = current_product_release(phase_registry)
    registry = load_capability_registry()
    cages = [item for item in _as_list(contract.get("cages")) if isinstance(item, dict)]
    release_policy = contract.get("release_policy") if isinstance(contract.get("release_policy"), dict) else {}
    included_releases = {
        str(item) for item in _as_list(release_policy.get("included_releases"))
    } or {active_release}

    missing_policy = []
    missing_validator = []
    capability_mismatches = []
    release_blocking_mismatches = []
    evidence_mismatches = []
    runtime_proof_mismatches = []
    missing_artifacts = []
    checked_cage_ids = []
    skipped_future_cage_ids = []

    for cage in cages:
        cage_id = str(cage.get("id") or "")
        capability_id = str(cage.get("capability_id") or "")
        capability = get_capability(registry, capability_id)
        if not _policy_path(str(cage.get("policy") or "")).exists():
            missing_policy.append(cage_id)
        validator_path = CODE_MAPS_DIR / str(cage.get("validator") or "")
        if not validator_path.exists():
            missing_validator.append(cage_id)
        if not capability:
            capability_mismatches.append({"cage": cage_id, "reason": "capability_not_found", "capability_id": capability_id})
        else:
            capability_release = capability.get("introduced_in") or capability.get("target_release")
            if capability_release != cage.get("target_release"):
                capability_mismatches.append(
                    {
                        "cage": cage_id,
                        "reason": "target_release_mismatch",
                        "contract": cage.get("target_release"),
                        "capability": capability_release,
                    }
                )
            claim = str(capability.get("claim_boundary") or "").lower()
            if capability.get("maturity") == "roadmap" and "not active" not in claim:
                capability_mismatches.append(
                    {
                        "cage": cage_id,
                        "reason": "claim_boundary_does_not_keep_roadmap_out_of_scope",
                        "claim_boundary": capability.get("claim_boundary"),
                    }
                )

        artifact = str(cage.get("artifact") or "")
        if str(cage.get("target_release") or "") not in included_releases:
            skipped_future_cage_ids.append(cage_id)
            continue
        checked_cage_ids.append(cage_id)
        payload = load_json_file(_artifact_path(artifact), {})
        if not isinstance(payload, dict) or not payload:
            missing_artifacts.append(cage_id)
            continue

        findings = [item for item in _as_list(payload.get("findings")) if isinstance(item, dict)]
        minimum_evidence = {str(item) for item in _as_list(cage.get("minimum_evidence"))}
        for finding in findings[:250]:
            evidence_kinds = {str(item) for item in _as_list(finding.get("evidence_kinds"))}
            if minimum_evidence and not minimum_evidence.issubset(evidence_kinds):
                evidence_mismatches.append(
                    {
                        "cage": cage_id,
                        "file": finding.get("file"),
                        "line": finding.get("line"),
                        "expected": sorted(minimum_evidence),
                        "actual": sorted(evidence_kinds),
                    }
                )
            expected_runtime = cage.get("must_have_runtime_proof_status")
            if expected_runtime and finding.get("runtime_proof_status") != expected_runtime:
                runtime_proof_mismatches.append(
                    {
                        "cage": cage_id,
                        "file": finding.get("file"),
                        "line": finding.get("line"),
                        "expected": expected_runtime,
                        "actual": finding.get("runtime_proof_status"),
                    }
                )

        if cage.get("release_blocking") is not False:
            release_blocking_mismatches.append(cage_id)

    checks = [
        _check("cage_contract_file_exists", CONTRACT_PATH.exists(), str(CONTRACT_PATH)),
        _check("cage_contract_declares_cages", len(cages) >= 3, {"cages": len(cages)}),
        _check(
            "current_release_policy_keeps_cages_advisory",
            release_policy.get("current_release_allowed_role") == "advisory_candidate_signals"
            and release_policy.get("release_blocking_cages") == [],
            release_policy,
        ),
        _check("all_cage_policies_exist", not missing_policy, missing_policy),
        _check("all_cage_validators_exist", not missing_validator, missing_validator),
        _check("cages_match_capability_registry_claims", not capability_mismatches, capability_mismatches),
        _check(
            "included_release_cage_artifacts_exist",
            not missing_artifacts,
            {
                "checked_cages": checked_cage_ids,
                "skipped_future_cages": skipped_future_cage_ids,
                "missing_artifacts": missing_artifacts,
                "included_releases": sorted(included_releases),
            },
        ),
        _check(
            "included_release_cage_findings_keep_minimum_evidence",
            not evidence_mismatches,
            {
                "checked_cages": checked_cage_ids,
                "skipped_future_cages": skipped_future_cage_ids,
                "mismatches": evidence_mismatches[:20],
            },
        ),
        _check(
            "included_release_runtime_sensitive_cages_require_runtime_proof",
            not runtime_proof_mismatches,
            {
                "checked_cages": checked_cage_ids,
                "skipped_future_cages": skipped_future_cage_ids,
                "mismatches": runtime_proof_mismatches[:20],
            },
        ),
        _check("no_cage_is_release_blocking_in_1_0_0", not release_blocking_mismatches, release_blocking_mismatches),
    ]
    status = "PASS" if all(check["passed"] for check in checks) else "FAIL"
    payload = {
        "meta": {
            "kind": "cage_mvp_contract_validation",
            "version": "1.0.0",
            "generated_at": _utc_now(),
            "generator": "tools.validate_cage_mvp_contracts",
            "source": "config/cage_mvp_contracts.json",
        },
        "summary": {
            "status": status,
            "cages": len(cages),
            "checks": len(checks),
            "passed": sum(1 for check in checks if check["passed"]),
            "current_release_allowed_role": release_policy.get("current_release_allowed_role"),
            "included_releases": sorted(included_releases),
            "checked_cages": checked_cage_ids,
            "skipped_future_cages": skipped_future_cage_ids,
        },
        "checks": checks,
    }
    save_json_atomic(RAW_DIR / "cage_mvp_contract_validation.json", payload)
    save_text_atomic(REPORTS_DIR / "cage_mvp_contract_validation.md", render_report(payload))
    return payload


def render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# Cage MVP Contract Validation",
        "",
        "Validates the MVP claim boundary for policy-backed AI safety cages.",
        "",
        f"- status: `{summary.get('status')}`",
        f"- cages: `{summary.get('cages')}`",
        f"- current_release_allowed_role: `{summary.get('current_release_allowed_role')}`",
        f"- checked_cages: `{', '.join(summary.get('checked_cages') or []) or '-'}`",
        f"- skipped_future_cages: `{', '.join(summary.get('skipped_future_cages') or []) or '-'}`",
        "",
        "| Check | Passed |",
        "|---|---|",
    ]
    for check in payload.get("checks", []):
        lines.append(f"| `{check.get('name')}` | `{check.get('passed')}` |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    payload = validate_cage_mvp_contracts()
    print(json.dumps(payload.get("summary", {}), ensure_ascii=False))
    return 0 if payload.get("summary", {}).get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
