from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
CODE_MAPS_DIR = TOOLS_DIR.parent
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))

from tools.core.config import CONFIG_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file

PROOF_GATE_PATH = CONFIG_DIR / "universal_proof_gate.json"
FIXTURE_VALIDATION_PATH = RAW_DIR / "react_fixture_matrix_validation.json"
READINESS_PATH = RAW_DIR / "react_universal_readiness.json"


def _check(name: str, passed: bool, details: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _summary(path: Path) -> dict[str, Any]:
    payload = load_json_file(path, {})
    summary = payload.get("summary", {}) if isinstance(payload, dict) else {}
    return summary if isinstance(summary, dict) else {}


def _validation_passed(summary: dict[str, Any]) -> bool:
    if summary.get("status") in {"PASS", "READY"}:
        return True
    return summary.get("failed_checks") == 0 and int(summary.get("total_checks", 0) or 0) > 0


def _external_evidence_complete(configured: list[str], passed: list[str], minimum: int) -> bool:
    configured_ids = set(configured)
    passed_ids = set(passed)
    return (
        len(configured) == len(configured_ids)
        and len(configured_ids) >= minimum
        and passed_ids == configured_ids
    )


def run_validation() -> dict[str, Any]:
    proof_cfg = load_json_file(PROOF_GATE_PATH, {})
    fixture_summary = _summary(FIXTURE_VALIDATION_PATH)
    readiness_summary = _summary(READINESS_PATH)
    configured_artifacts = proof_cfg.get("external_validation_artifacts", [])
    external_artifacts = [str(item) for item in configured_artifacts if isinstance(item, str) and item.strip()]
    minimum = int(proof_cfg.get("min_external_validation_artifacts", 1) or 1)

    external_results: list[dict[str, Any]] = []
    for artifact in external_artifacts:
        path = RAW_DIR / f"{artifact}.json"
        summary = _summary(path)
        external_results.append(
            {
                "artifact": artifact,
                "exists": path.exists(),
                "passed": path.exists() and _validation_passed(summary),
                "summary": summary,
            }
        )

    passed_external = [row["artifact"] for row in external_results if row["passed"]]
    release_families = int(readiness_summary.get("release_families", 0) or 0)
    proven_release_families = int(readiness_summary.get("proven_release_families", 0) or 0)
    runtime_boundary = str(readiness_summary.get("runtime_boundary", ""))

    checks = [
        _check(
            "workspace_fixture_matrix_passes",
            _validation_passed(fixture_summary),
            fixture_summary,
        ),
        _check(
            "portable_external_validation_threshold_met",
            len(set(external_artifacts)) >= minimum,
            {"configured": external_artifacts, "unique_configured": sorted(set(external_artifacts)), "minimum": minimum},
        ),
        _check(
            "portable_external_validation_identities_are_unique",
            len(external_artifacts) == len(set(external_artifacts)),
            {"configured": external_artifacts},
        ),
        _check(
            "portable_external_validations_pass",
            _external_evidence_complete(external_artifacts, passed_external, minimum),
            external_results,
        ),
        _check(
            "release_taxonomy_is_fully_proven",
            readiness_summary.get("universal_ready") is True
            and release_families > 0
            and proven_release_families == release_families,
            readiness_summary,
        ),
        _check(
            "runtime_claim_boundary_is_explicit",
            "needs_runtime_proof" in runtime_boundary,
            runtime_boundary,
        ),
    ]

    payload = {
        "meta": {"kind": "universal_proof_validation", "version": "v2"},
        "policy": {
            "min_external_validation_artifacts": minimum,
            "external_validation_artifacts": external_artifacts,
            "proof_basis": "workspace_fixtures_plus_portable_external_smoke_plus_release_taxonomy",
        },
        "external_fixture_summary": {
            "configured_validation_artifacts": external_artifacts,
            "passed_validation_artifacts": passed_external,
            "results": external_results,
        },
        "summary": {
            "total_checks": len(checks),
            "passed_checks": sum(1 for check in checks if check["passed"]),
            "failed_checks": sum(1 for check in checks if not check["passed"]),
        },
        "checks": checks,
    }
    save_json_atomic(RAW_DIR / "universal_proof_validation.json", payload)

    lines = [
        "# Universal Proof Validation",
        "",
        "Portable proof combines local executable fixtures, an isolated external-target smoke, and release taxonomy readiness.",
        "",
        f"- External validation threshold: `{minimum}`",
        f"- External validations passed: `{len(passed_external)}/{len(external_artifacts)}`",
        f"- Release taxonomy: `{proven_release_families}/{release_families}`",
        f"- Failed checks: `{payload['summary']['failed_checks']}`",
        "",
        "| Check | Result |",
        "|---|---|",
    ]
    for check in checks:
        lines.append(f"| `{check['name']}` | {'PASS' if check['passed'] else 'FAIL'} |")
    save_text_atomic(REPORTS_DIR / "universal_proof_validation.md", "\n".join(lines) + "\n")
    return payload


def main() -> int:
    payload = run_validation()
    print(json.dumps(payload.get("summary", {}), ensure_ascii=False))
    return 0 if payload.get("summary", {}).get("failed_checks", 1) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
