from __future__ import annotations

import json
import sys
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
ROOT = TOOLS_DIR.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.config import CONFIG_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.evidence_status import evidence_passed
from tools.core.json_io import load_json_file
from tools.core.roadmap_phase_registry import current_product_release, load_roadmap_phase_registry


TAXONOMY_PATH = CONFIG_DIR / "react_fixture_family_taxonomy.json"
DOCTRINE_PATH = CONFIG_DIR / "react_universal_analysis_doctrine.json"
RELEASE_IDENTITY_PATH = CONFIG_DIR / "release_identity.json"


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _artifact_status(name: str) -> dict[str, Any]:
    payload = load_json_file(RAW_DIR / f"{name}.json", {})
    summary = payload.get("summary", {}) if isinstance(payload, dict) else {}
    meta = payload.get("meta", {}) if isinstance(payload, dict) else {}
    status = str(summary.get("status") or "").upper()
    if name == "react_fixture_matrix_validation":
        passed = evidence_passed(payload, default=False)
    elif name == "react_fixture_family_taxonomy_report":
        expected_hash = _sha256_file(TAXONOMY_PATH)
        observed_hash = str(meta.get("config_sha256") or "")
        passed = status == "PASS" and observed_hash == expected_hash
        summary = {
            **summary,
            "config_hash_match": observed_hash == expected_hash,
            "expected_config_sha256": expected_hash,
            "observed_config_sha256": observed_hash,
        }
    else:
        passed = evidence_passed(payload, default=False)
    return {"artifact": name, "exists": bool(payload), "passed": bool(payload) and passed, "summary": summary}


def run_validation() -> dict[str, Any]:
    taxonomy = load_json_file(TAXONOMY_PATH, {})
    release_identity = load_json_file(RELEASE_IDENTITY_PATH, {})
    phase_registry = load_roadmap_phase_registry()
    active_release = current_product_release(phase_registry)
    doctrine = load_json_file(DOCTRINE_PATH, {})
    families = [item for item in taxonomy.get("families", []) if isinstance(item, dict)]
    policy = taxonomy.get("release_policy", {}) if isinstance(taxonomy, dict) else {}
    included_releases = {str(item) for item in policy.get("included_releases", [active_release])}
    release_families = [item for item in families if str(item.get("target_release")) in included_releases]
    missing = [
        {
            "id": item.get("id"),
            "release": item.get("target_release"),
            "support_level": item.get("support_level"),
            "linked_existing_fixtures": item.get("linked_existing_fixtures", []),
            "covered_variants": item.get("covered_variants", []),
        }
        for item in release_families
        if item.get("support_level") != "proven"
        or not item.get("linked_existing_fixtures")
        or not item.get("covered_variants")
    ]
    required_artifacts = [str(item) for item in policy.get("required_validation_artifacts", [])]
    artifact_checks = [_artifact_status(name) for name in required_artifacts]
    artifact_failures = [item for item in artifact_checks if not item["passed"]]
    identity_release_claim = (
        (release_identity.get("release_claim") or {}) if isinstance(release_identity, dict) else {}
    )
    identity_claim = str(identity_release_claim.get("allowed") or "")
    allowed_claim = str(policy.get("allowed_claim") or identity_claim)
    claim_policy_aligned = bool(identity_claim) and allowed_claim == identity_claim
    universal_ready = bool(release_families) and not missing and not artifact_failures and claim_policy_aligned

    rows = [
        {
            "id": item.get("id"),
            "category": item.get("category"),
            "release": item.get("target_release"),
            "support_level": item.get("support_level"),
            "linked_existing_fixtures": item.get("linked_existing_fixtures", []),
            "covered_variants": item.get("covered_variants", []),
            "release_included": item in release_families,
        }
        for item in families
    ]
    payload = {
        "meta": {
            "kind": "react_universal_readiness",
            "version": "1.0.0",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "taxonomy": TAXONOMY_PATH.relative_to(ROOT).as_posix(),
            "evidence_model": "taxonomy_plus_executable_fixture_contracts",
        },
        "summary": {
            "status": "PASS" if universal_ready else "FAIL",
            "total_families": len(families),
            "release_families": len(release_families),
            "proven_release_families": len(release_families) - len(missing),
            "included_releases": sorted(included_releases),
            "certification_level": "react_web_1_0_0_static_governance" if universal_ready else "core_react_web",
            "allowed_claim": allowed_claim,
            "release_identity_claim": identity_claim,
            "universal_ready": universal_ready,
            "fixture_coverage_complete": not missing,
            "current_claim_policy": policy.get("allowed_claim"),
            "claim_policy_aligned": claim_policy_aligned,
            "broad_claim_allowed_by_policy": universal_ready,
            "universality_basis": "taxonomy_plus_executable_fixture_contracts",
            "fixture_role": "executable_regression_and_negative_control",
            "external_fixture_role": "real_world_calibration_evidence",
            "external_fixture_pool_required": False,
            "runtime_boundary": policy.get("runtime_boundary"),
            "missing_release_families": len(missing),
            "failed_validation_artifacts": len(artifact_failures),
        },
        "doctrine": {
            "path": DOCTRINE_PATH.relative_to(ROOT).as_posix(),
            "core_claim": ((doctrine.get("universality_model") or {}) if isinstance(doctrine, dict) else {}).get("core_claim"),
            "runtime_boundary": policy.get("runtime_boundary"),
        },
        "missing_release_families": missing,
        "validation_artifacts": artifact_checks,
        "families": rows,
    }
    save_json_atomic(RAW_DIR / "react_universal_readiness.json", payload)
    save_text_atomic(REPORTS_DIR / "react_universal_readiness.md", render_report(payload))
    return payload


def render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# React Universal Readiness",
        "",
        "The release claim is derived from the 80-family taxonomy and executable fixture contracts.",
        "",
        f"- status: `{summary.get('status')}`",
        f"- certification_level: `{summary.get('certification_level')}`",
        f"- allowed_claim: `{summary.get('allowed_claim')}`",
        f"- release families: `{summary.get('proven_release_families')}/{summary.get('release_families')}`",
        f"- included releases: `{summary.get('included_releases')}`",
        f"- runtime boundary: `{summary.get('runtime_boundary')}`",
        "",
        "## Validation Artifacts",
        "",
        "| Artifact | Exists | Passed |",
        "|---|---|---|",
    ]
    for item in payload.get("validation_artifacts", []):
        lines.append(f"| `{item.get('artifact')}` | `{item.get('exists')}` | `{item.get('passed')}` |")
    lines.extend(["", "## Missing Release Families", ""])
    missing = payload.get("missing_release_families", [])
    if not missing:
        lines.append("None.")
    else:
        for item in missing:
            lines.append(f"- `{item.get('id')}` ({item.get('phase')}): `{item.get('support_level')}`")
    return "\n".join(lines) + "\n"


def main() -> int:
    payload = run_validation()
    print(json.dumps(payload.get("summary", {}), ensure_ascii=False))
    return 0 if payload.get("summary", {}).get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
